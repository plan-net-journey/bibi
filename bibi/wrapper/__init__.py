"""Generischer Worker-Wrapper (DESIGN §7.5; PLAN-3 §1.2/§3.3).

Ein env-konfigurierter Entrypoint, der den Job-Prozess als **Child** spawnt,
stdout/stderr via Pipe liest und nach ``data/job/{id}/output.jsonl`` appendet.
Der Typ wird aus einer **Registry** (datengetriebenes ``type → TypeHandler``-
Mapping, keine if/else-Kette) bestimmt — neue Typen (``app``, ``openai-sdk`` …)
docken ohne Umbau an.

Aufruf als eigener Prozess: ``python -m bibi.wrapper``. Env (vom Worker gesetzt):

- ``BIBI_JOB_TYPE``   — Registry-Schlüssel (``job``/``claude``/``app``).
- ``BIBI_JOB_ID``     — stabile Job-Hash-ID.
- ``BIBI_OUTPUT_PATH``— absoluter Pfad der ``output.jsonl``.
- ``BIBI_WORKTREE``   — Arbeitsverzeichnis des Childs.
- ``BIBI_SCHEDULER_URL`` — für Terminal-Status-Meldung (optional).
- ``BIBI_REPO_ROOT``, ``BIBI_JOB_SLUG``, ``BIBI_RUN_ID`` — für Worktree-Commit.
- ``BIBI_WALL_TIME``, ``BIBI_SILENCE_TIMEOUT`` — Timeout-Überwachung.
- ``BIBI_ATTEMPT``, ``BIBI_ATTEMPTS`` — Retry-Zähler für failed/error.
- typ-spezifisch: ``BIBI_JOB_CMD`` (job), ``BIBI_JOB_PROMPT``/``BIBI_JOB_MODEL`` (claude).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bibi.schedule.models import DEFAULT_CLAUDE_MODEL
from bibi.wrapper import exec_backend, output


@dataclass(frozen=True)
class TypeHandler:
    """Wie ein Typ zu einem Child-Prozess wird (§7.5)."""

    build_command: Callable[[dict[str, str]], list[str]]  # env → argv des Childs
    long_lived: bool = False     # app: kein Silence-Zombie, Wrapper bleibt
    supports_hitl: bool = False  # nur app


def _claude_argv(env: dict[str, str]) -> list[str]:
    container = (env.get("BIBI_EXEC_MODE") or "").strip().lower() == "container"
    # Host: BIBI_CLAUDE_BIN überschreibt das Binary (Tests/Stubs, abs. Pfad bei
    # eingeschränktem PATH). Container: claude liegt im Image auf dem PATH — der
    # Host-Pfad wäre dort sinnlos (Cannot find module), also immer ``claude``.
    binary = "claude" if container else (env.get("BIBI_CLAUDE_BIN") or "claude")
    argv = [binary, "-p", env.get("BIBI_JOB_PROMPT", "")]
    argv += ["--model", env.get("BIBI_JOB_MODEL") or DEFAULT_CLAUDE_MODEL]
    # Container ohne ~/.claude-Settings: claude würde bei Tool-Nutzung (Datei
    # schreiben) nachfragen und headless hängen. ``acceptEdits`` erlaubt Datei-Edits
    # ohne Prompt und funktioniert als root (``--dangerously-skip-permissions`` ist
    # als root verboten). Deckt vault-schreibende Jobs. Host-Modus unverändert
    # (Nutzer-Settings gelten). Volle Autonomie (bash etc.) bräuchte einen non-root
    # Container — späterer Ausbau (PLAN-8 D9).
    if (env.get("BIBI_EXEC_MODE") or "").strip().lower() == "container":
        argv += ["--permission-mode", "acceptEdits"]
    session = env.get("BIBI_JOB_SESSION")
    if session:  # Dialog fortsetzen (§5.3)
        argv += ["--resume", session]
    return argv


def _app_argv(env: dict[str, str]) -> list[str]:
    return ["bash", "-c", env.get("BIBI_APP_ENTRYPOINT", "")]


#: Das Registry-Mapping. Frontmatter-Key == Typ == Schlüssel (§1.2).
REGISTRY: dict[str, TypeHandler] = {
    "job": TypeHandler(build_command=lambda env: ["bash", "-c", env.get("BIBI_JOB_CMD", "")]),
    "claude": TypeHandler(build_command=_claude_argv),
    "app": TypeHandler(build_command=_app_argv, long_lived=True, supports_hitl=True),
}


# ── Monitoring-Threads ────────────────────────────────────────────────────────

def _last_activity(out_path: Path, started: float) -> float:
    """Zeitpunkt der letzten Output-Aktivität (mtime oder Start)."""
    try:
        return max(out_path.stat().st_mtime, started)
    except OSError:
        return started


def _terminate_proc(proc: subprocess.Popen) -> None:
    """Prozessgruppe graceful terminieren."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    time.sleep(2.0)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def _wall_monitor(proc: subprocess.Popen, wall_time: int, started: float,
                  outcome: list[str]) -> None:
    """Thread: wall_time überwachen und Proc bei Überschreitung killen."""
    while proc.poll() is None:
        if time.time() - started > wall_time:
            outcome[0] = "wall_time"  # VOR terminate setzen → kein Race mit proc.wait()
            _terminate_proc(proc)
            return
        time.sleep(1.0)


def _silence_monitor(proc: subprocess.Popen, silence_timeout: int,
                     out_path: Path, started: float, outcome: list[str]) -> None:
    """Thread: Silence-Timeout überwachen (kein stdout/stderr → Zombie)."""
    while proc.poll() is None:
        if time.time() - _last_activity(out_path, started) > silence_timeout:
            outcome[0] = "silence"  # VOR terminate setzen → kein Race mit proc.wait()
            _terminate_proc(proc)
            return
        time.sleep(1.0)


def _hitl_monitor(proc: subprocess.Popen, state, *, poll: float = 1.0) -> None:
    """Hintergrund-Thread: HITL-Zombie-Timeout überwachen (PLAN-9 §6, Slice 9.3)."""
    while proc.poll() is None:
        if (state.hitl_timeout is not None
                and state.status == "awaiting"
                and state.idle_seconds > state.hitl_timeout):
            state.report("zombie", reason="activity_timeout")
            _terminate_proc(proc)
            return
        time.sleep(poll)


# ── Post-completion: Commit + Report ─────────────────────────────────────────

def _commit_worktree(env: dict[str, str]) -> tuple[str | None, str | None]:
    """Worktree committen und (commit_sha, branch) zurückgeben."""
    repo_root_str = env.get("BIBI_REPO_ROOT")
    if not repo_root_str:
        return None, None
    try:
        from bibi.daemon import worktree as _wt
        repo_root = Path(repo_root_str)
        wt_path = Path(env["BIBI_WORKTREE"])
        slug = env.get("BIBI_JOB_SLUG", "")
        run_id = env.get("BIBI_RUN_ID", env.get("BIBI_JOB_ID", ""))
        commit_sha = _wt.commit(worktree=wt_path, message=f"{slug}: run {run_id}", slug=slug)
        branch = _wt.branch_name(slug) if commit_sha else None
        if env.get("BIBI_EPHEMERAL") == "1":
            _wt.remove(repo_root=repo_root, worktree=wt_path)
        return commit_sha or None, branch
    except Exception:
        return None, None


def _report_terminal(env: dict[str, str], *, status: str, reason: str | None = None,
                     exit_code: int | None = None, output_ref: str | None = None,
                     commit_sha: str | None = None, branch: str | None = None,
                     attempt: int | None = None, next_fire_at: float | None = None) -> None:
    """Terminal-Status an Scheduler melden (best-effort, PLAN-9 §8 E2).

    Bevorzugt direkten SQLite-Zugriff (BIBI_SCHEDULER_DB_PATH), sonst HTTP."""
    job_id = env.get("BIBI_JOB_ID")
    if not job_id:
        return
    worker = env.get("BIBI_WORKER_NAME")
    host_name = env.get("BIBI_HOST")

    db_path_str = env.get("BIBI_SCHEDULER_DB_PATH")
    if db_path_str:
        try:
            from bibi.daemon import job_db as _jdb
            conn = _jdb.connect(Path(db_path_str))
            try:
                _jdb.report_status(
                    conn, job_id, status=status, reason=reason, exit_code=exit_code,
                    output_ref=output_ref, commit_sha=commit_sha, branch=branch,
                    worker=worker, host=host_name,
                    attempt=attempt, next_fire_at=next_fire_at,
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
        return

    url_base = env.get("BIBI_SCHEDULER_URL")
    if not url_base:
        return
    url = f"{url_base.rstrip('/')}/-/scheduler/status/{job_id}"
    body: dict = {"status": status}
    if reason is not None:
        body["reason"] = reason
    if exit_code is not None:
        body["exit_code"] = exit_code
    if output_ref is not None:
        body["output_ref"] = output_ref
    if commit_sha is not None:
        body["commit_sha"] = commit_sha
    if branch is not None:
        body["branch"] = branch
    if worker:
        body["worker"] = worker
    if host_name:
        body["host"] = host_name
    if attempt is not None:
        body["attempt"] = attempt
    if next_fire_at is not None:
        body["next_fire_at"] = next_fire_at
    payload = json.dumps(body).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0):  # noqa: S310
            pass
    except (urllib.error.URLError, OSError):
        pass


def _finish(env: dict[str, str], exit_code: int, outcome: str) -> None:
    """Worktree committen + Terminal-Status melden."""
    commit_sha, branch = _commit_worktree(env)

    attempt_cur = int(env.get("BIBI_ATTEMPT", "0"))
    attempts_max = int(env.get("BIBI_ATTEMPTS", "1"))
    report_attempt: int | None = None
    next_fire_at: float | None = None

    if outcome == "wall_time":
        status, reason = "killed", "by_wall_time"
    elif outcome == "silence" or outcome == "zombie_reported":
        status, reason = "zombie", "silence"
    elif exit_code == 0:
        status, reason = "complete", None
    elif attempt_cur < attempts_max:
        from bibi.schedule import backoff as _backoff
        next_attempt = attempt_cur + 1
        base = float(os.environ.get("BIBI_RETRY_BASE") or _backoff.DEFAULT_BASE)
        btype = env.get("BIBI_BACKOFF") or "fixed"
        next_fire_at = time.time() + _backoff.delay(btype, next_attempt, base=base)
        report_attempt = next_attempt
        status, reason = "failed", "nonzero_exit"
    else:
        status, reason = "error", "nonzero_exit"

    out_path_str = env.get("BIBI_OUTPUT_PATH", "")
    repo_root_str = env.get("BIBI_REPO_ROOT", "")
    output_ref: str | None = None
    if out_path_str and repo_root_str:
        try:
            output_ref = Path(out_path_str).relative_to(repo_root_str).as_posix()
        except ValueError:
            output_ref = out_path_str

    _report_terminal(env, status=status, reason=reason, exit_code=exit_code,
                     output_ref=output_ref, commit_sha=commit_sha, branch=branch,
                     attempt=report_attempt, next_fire_at=next_fire_at)


# ── Ausführungs-Hauptfunktionen ───────────────────────────────────────────────

def run_app(env: dict[str, str]) -> int:
    """App-Typ: Wrapper-HTTP-Server starten + App-Child nebenläufig ausführen."""
    kind = env["BIBI_JOB_TYPE"]
    handler = REGISTRY.get(kind)
    if handler is None:
        raise KeyError(f"unbekannter Job-Typ: {kind!r} (Registry: {sorted(REGISTRY)})")
    child_argv = handler.build_command(env)
    out_path = Path(env["BIBI_OUTPUT_PATH"])
    job_id = env.get("BIBI_JOB_ID", "unknown")
    wrapper_port = int(env.get("BIBI_WRAPPER_PORT") or "8080")

    from bibi.wrapper.server import WrapperState, start_server
    scheduler_url = env.get("BIBI_SCHEDULER_URL") or None
    scheduler_db_path = env.get("BIBI_SCHEDULER_DB_PATH") or None
    app_port_str = env.get("BIBI_APP_PORT")
    app_port = int(app_port_str) if app_port_str else None
    hitl_str = env.get("BIBI_HITL_TIMEOUT")
    hitl_timeout = int(hitl_str) if hitl_str else None
    wrapper_url = env.get("BIBI_WRAPPER_EXTERNAL_URL") or f"http://127.0.0.1:{wrapper_port}"
    state = WrapperState(job_id=job_id, scheduler_url=scheduler_url,
                         scheduler_db_path=scheduler_db_path,
                         app_port=app_port, hitl_timeout=hitl_timeout,
                         wrapper_url=wrapper_url)
    server = start_server(state, port=wrapper_port)

    spec = exec_backend.build_exec(child_argv, env)
    proc = subprocess.Popen(
        spec.argv, cwd=spec.cwd, env=spec.env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, start_new_session=True,
    )
    lock = threading.Lock()

    def pump(pipe, tag: str) -> None:
        assert pipe is not None
        for line in pipe:
            with lock:
                output.append(out_path, tag, line.rstrip("\n"))
            state.touch()

    outcome: list[str] = [""]
    started = time.time()
    wall_str = env.get("BIBI_WALL_TIME")

    monitors = [threading.Thread(target=_hitl_monitor, args=(proc, state),
                                 kwargs={"poll": 0.5}, daemon=True, name="hitl-monitor")]
    if wall_str:
        monitors.append(threading.Thread(
            target=_wall_monitor, args=(proc, int(wall_str), started, outcome),
            daemon=True, name="wall-monitor"))

    pump_threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out")),
        threading.Thread(target=pump, args=(proc.stderr, "err")),
    ]
    for t in [*pump_threads, *monitors]:
        t.start()
    proc.wait()
    for t in pump_threads:
        t.join()

    server.should_exit = True

    # HITL-Monitor setzt outcome nicht — nur wall_time tut es für app.
    # Zombie wird vom hitl_monitor via state.report("zombie") gemeldet und proc terminiert;
    # proc.returncode ist dann negativ — als "zombie" klassifizieren.
    if not outcome[0] and state.status == "zombie":
        outcome[0] = "zombie_reported"

    _finish(env, proc.returncode or 0, outcome[0])
    return proc.returncode or 0


def run_job(env: dict[str, str]) -> int:
    """job/claude-Typ: Child spawnen, Output pumpen, monitoren, committen, melden."""
    kind = env["BIBI_JOB_TYPE"]
    handler = REGISTRY.get(kind)
    if handler is None:
        raise KeyError(f"unbekannter Job-Typ: {kind!r} (Registry: {sorted(REGISTRY)})")
    child_argv = handler.build_command(env)
    out_path = Path(env["BIBI_OUTPUT_PATH"])
    spec = exec_backend.build_exec(child_argv, env)

    proc = subprocess.Popen(
        spec.argv, cwd=spec.cwd, env=spec.env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, start_new_session=True,
    )
    lock = threading.Lock()

    def pump(pipe, tag: str) -> None:
        assert pipe is not None
        for line in pipe:
            with lock:
                output.append(out_path, tag, line.rstrip("\n"))

    outcome: list[str] = [""]
    started = time.time()
    wall_str = env.get("BIBI_WALL_TIME")
    silence_str = env.get("BIBI_SILENCE_TIMEOUT")

    monitors = []
    if wall_str:
        monitors.append(threading.Thread(
            target=_wall_monitor, args=(proc, int(wall_str), started, outcome),
            daemon=True, name="wall-monitor"))
    if silence_str:
        monitors.append(threading.Thread(
            target=_silence_monitor,
            args=(proc, int(silence_str), out_path, started, outcome),
            daemon=True, name="silence-monitor"))

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out")),
        threading.Thread(target=pump, args=(proc.stderr, "err")),
        *monitors,
    ]
    for t in threads:
        t.start()
    proc.wait()
    for t in threads[:2]:  # pump threads joinen (monitors sind daemon)
        t.join()

    _finish(env, proc.returncode or 0, outcome[0])
    return proc.returncode or 0


def main(argv: list[str] | None = None) -> int:
    env = dict(os.environ)
    kind = env.get("BIBI_JOB_TYPE", "")
    handler = REGISTRY.get(kind)
    if handler is None:
        raise KeyError(f"unbekannter Job-Typ: {kind!r} (Registry: {sorted(REGISTRY)})")
    if handler.long_lived:
        return run_app(env)
    return run_job(env)


if __name__ == "__main__":
    sys.exit(main())
