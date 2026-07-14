"""Generischer Worker-Wrapper (DESIGN §7.5; PLAN-3 §1.2/§3.3).

Ein env-konfigurierter Entrypoint, der den Job-Prozess als **Child** spawnt,
stdout/stderr via Pipe liest und nach ``data/job/{id}/output.jsonl`` appendet.
PLAN-10 Stufe 10.0: nur noch ``job`` und ``claude`` im REGISTRY.
PLAN-11.3: Signale kommen via stdout ``BIBI:{...}`` statt HTTP ``/-/signal/*``.

Aufruf als eigener Prozess: ``python -m bibi.wrapper``. Env (vom Worker gesetzt):

- ``BIBI_JOB_TYPE``   — Registry-Schlüssel (``job`` oder ``claude``).
- ``BIBI_JOB_ID``     — stabile Job-Hash-ID.
- ``BIBI_OUTPUT_PATH``— absoluter Pfad der ``output.jsonl``.
- ``BIBI_WORKTREE``   — Arbeitsverzeichnis des Childs.
- ``BIBI_SCHEDULER_URL`` — für Terminal-Status-Meldung (optional, Fallback).
- ``BIBI_SCHEDULER_DB_PATH`` — direkter SQLite-Zugriff für Signal-Handling.
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


# ── PLAN-11.3: stdout-Signalprotokoll ────────────────────────────────────────


def _parse_bibi_line(line: str) -> dict | None:
    """Parst eine BIBI:{...}-Zeile; gibt None zurück wenn kein gültiges Signal."""
    if not line.startswith("BIBI:"):
        return None
    try:
        return json.loads(line[5:])
    except (json.JSONDecodeError, ValueError):
        return None


def _handle_signal(conn, job_id: str, sig: dict) -> None:
    """Verarbeitet ein geparsten BIBI-Signal direkt in job_db.

    ``deferred`` wird nicht hier behandelt — der Pump-Thread setzt ``outcome``
    direkt, da kein DB-Schreiben nötig (nur ``_finish`` ändert den Status).
    """
    from bibi import config as _config
    from bibi.daemon import job_db as _jdb
    name = sig.get("name")
    if name == "running":
        _jdb.report_status(conn, job_id, status="running")
    elif name == "awaiting":
        port = sig.get("port")
        if port is None:
            row = conn.execute("SELECT app_port FROM jobs WHERE id=?", (job_id,)).fetchone()
            port = row["app_port"] if row else None
        app_url = f"http://{_config.public_host()}:{port}/" if port else None
        _jdb.report_status(conn, job_id, status="awaiting", app_url=app_url)
        _jdb.set_demand(conn, job_id, sig)
    elif name == "app_register":
        _jdb.set_app_port(conn, job_id, sig["port"])
    elif name == "activity":
        pass  # reiner Herzschlag — last_activity_ts aktualisiert schon der Pump-Loop


def _record_signal(sig: dict, *, job_id: str, out_path: Path, db_path_str: str | None,
                    current_status: list[str], lock: threading.Lock) -> None:
    """Ein nicht-``deferred``/nicht-``activity``-Signal aus dem Pump-Loop
    verarbeiten (aufgerufen von ``run_app()``). Meldet an die Scheduler-DB
    (``db_path_str`` gesetzt, dispatchte Jobs) oder — ephemeral/lokal per
    ``/run``, ``db_path_str`` ist dann ``None`` — als eigenen ``signal``-
    Event-Tag ins ``output.jsonl`` (Ausbau User-Fund 2026-07-10: dieser Zweig
    fehlte bisher komplett, das Signal ging spurlos verloren; die
    Job-Detailseite lokaler App-Jobs zeigte deshalb nie ``awaiting``, s.
    ``worker.local_run_signal_state()``, das diese Zeilen wieder ausliest)."""
    name = sig.get("name")
    if name in ("running", "awaiting"):
        with lock:
            current_status[0] = name
    if db_path_str:
        try:
            from bibi.daemon import job_db as _jdb
            conn = _jdb.connect(Path(db_path_str))
            try:
                _handle_signal(conn, job_id, sig)
            finally:
                conn.close()
        except Exception:
            pass
    else:
        with lock:
            output.append(out_path, "signal", json.dumps(sig, separators=(",", ":")))


@dataclass(frozen=True)
class TypeHandler:
    """Wie ein Typ zu einem Child-Prozess wird (§7.5)."""

    build_command: Callable[[dict[str, str]], list[str]]  # env → argv des Childs
    long_lived: bool = False     # True → run_app (HITL-fähig, Wrapper-HTTP-Server)
    supports_hitl: bool = False  # True → App kann AWAIT_INPUT-Signal senden


def _resolve_soul_prompt(env: dict[str, str]) -> str | None:
    """``soul: <name>`` → Inhalt von ``.claude/souls/*.<name>.SOUL.md`` im
    Job-Worktree (Nummer-Präfix wird ignoriert, Name-Teil case-insensitive).
    Best-effort: kein Verzeichnis/keine Datei ⇒ None, kein Fehler."""
    soul = env.get("BIBI_JOB_SOUL")
    worktree = env.get("BIBI_WORKTREE")
    if not soul or not worktree:
        return None
    souls_dir = Path(worktree) / ".claude" / "souls"
    if not souls_dir.is_dir():
        return None
    target = f".{soul.lower()}.soul.md"
    for p in sorted(souls_dir.iterdir()):
        if p.is_file() and p.name.lower().endswith(target):
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def _claude_argv(env: dict[str, str]) -> list[str]:
    container = (env.get("BIBI_EXEC_MODE") or "").strip().lower() == "container"
    # Host: BIBI_CLAUDE_BIN überschreibt das Binary (Tests/Stubs, abs. Pfad bei
    # eingeschränktem PATH). Container: claude liegt im Image auf dem PATH — der
    # Host-Pfad wäre dort sinnlos (Cannot find module), also immer ``claude``.
    binary = "claude" if container else (env.get("BIBI_CLAUDE_BIN") or "claude")
    argv = [binary, "-p", env.get("BIBI_JOB_PROMPT", "")]
    argv += ["--model", env.get("BIBI_JOB_MODEL") or DEFAULT_CLAUDE_MODEL]
    # Unconditional (Registry-Default, PLAN-12 Stufe 12.2) — `claude -p` puffert
    # sonst den kompletten Output bis Turn-Ende (live gemessen). `--verbose` ist bei
    # `--print --output-format stream-json` PFLICHT (die CLI bricht sonst sofort mit
    # "requires --verbose" ab, live in test_container_claude.py aufgedeckt).
    # `--include-partial-messages` (Follow-up PLAN-14, vormals bewusst aus) liefert
    # zusätzlich Token-Level-Deltas (stream_event/content_block_delta) für die
    # Live-Box; der Ausgabefilter (output_format.py) verarbeitet sie zustandslos
    # pro Aufruf über die volle Roh-Historie, kein Reassemble über Aufrufe hinweg
    # nötig. Die komplette assistant-Nachricht kommt weiterhin zusätzlich — der
    # Formatter unterdrückt die dann redundante Text-Wiederholung selbst.
    argv += ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    soul_prompt = _resolve_soul_prompt(env)
    if soul_prompt:
        argv += ["--append-system-prompt", soul_prompt]
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


#: Das Registry-Mapping (PLAN-10 Stufe 10.0: ``job`` und ``claude``).
#: ``job`` → run_app (HITL-fähig). ``claude`` → run_job (Batch, kein HITL).
REGISTRY: dict[str, TypeHandler] = {
    "job": TypeHandler(
        build_command=lambda env: ["bash", "-c", env.get("BIBI_JOB_CMD", "")],
        long_lived=True, supports_hitl=True,
    ),
    "claude": TypeHandler(build_command=_claude_argv),
}


# ── Monitoring-Threads ────────────────────────────────────────────────────────

def _terminate_proc(proc: subprocess.Popen, env: dict[str, str] | None = None) -> None:
    """Prozessgruppe graceful terminieren; bei ``exec_mode: container`` zuvor
    den Docker-Container selbst stoppen (``exec_backend.stop_container()``) —
    der überwachte ``proc`` ist dort nur der attached ``docker run``-CLI-
    Prozess, nicht der vom Docker-Daemon separat verwaltete Container; SIGKILL
    auf die lokale Prozessgruppe allein ließ ihn zuvor verwaist weiterlaufen
    (ZOMBIE-Befund). ``env=None`` (z. B. in älteren Aufrufer-Kontexten ohne
    Zugriff darauf) überspringt den Docker-Schritt unverändert wie zuvor."""
    if env is not None:
        exec_backend.stop_container(env)
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
                  outcome: list[str], out_path: Path, lock: threading.Lock,
                  env: dict[str, str] | None = None) -> None:
    """Thread: wall_time überwachen und Proc bei Überschreitung killen."""
    while proc.poll() is None:
        if time.time() - started > wall_time:
            outcome[0] = "wall_time"  # VOR terminate setzen → kein Race mit proc.wait()
            with lock:
                output.append(out_path, "phase",
                              f"wall_time: Zeitlimit ({wall_time}s) überschritten — "
                              "Prozess wird beendet")
            _terminate_proc(proc, env)
            return
        time.sleep(1.0)


def _silence_monitor(proc: subprocess.Popen, silence_timeout: int,
                     last_activity_ts: list[float], outcome: list[str],
                     out_path: Path, lock: threading.Lock,
                     env: dict[str, str] | None = None) -> None:
    """Thread: Aktivitäts-Timeout überwachen (keine Zeile/Signal → Zombie).

    ``last_activity_ts`` wird vom Pump-Loop bei JEDER Zeile aktualisiert — Output
    wie BIBI-Signal (z. B. der App-eigene ``activity``-Herzschlag pro Request).
    Eine Quelle für Batch-Jobs (kein stdout mehr) wie HITL-Apps (kein Ping trotz
    ``awaiting``) gleichermaßen, ohne Status-Unterscheidung (User-Feedback
    2026-07-04: „Silence bei Jobs = Aktivität bei Apps")."""
    while proc.poll() is None:
        if time.time() - last_activity_ts[0] > silence_timeout:
            outcome[0] = "silence"  # VOR terminate setzen → kein Race mit proc.wait()
            with lock:
                output.append(out_path, "phase",
                              f"silence: keine Aktivität seit {silence_timeout}s — "
                              "Prozess wird als Zombie beendet")
            _terminate_proc(proc, env)
            return
        time.sleep(1.0)


def _deferred_watcher(proc: subprocess.Popen, current_status: list[str],
                      outcome: list[str], lock: threading.Lock,
                      env: dict[str, str] | None = None) -> None:
    """Thread: wartet auf ein vom Kindprozess gesendetes ``deferred``-Signal und
    terminiert dann — kein Timeout, sondern ein expliziter Job-Wunsch, deshalb
    ein eigener Watcher statt Teil des Silence-Monitors."""
    while proc.poll() is None:
        with lock:
            cs = current_status[0]
            oc = outcome[0]
        if cs == "deferred" and not oc:
            with lock:
                outcome[0] = "deferred"
            _terminate_proc(proc, env)
            return
        time.sleep(0.5)


# ── Post-completion: Commit + Report ─────────────────────────────────────────

def _commit_worktree(env: dict[str, str]) -> tuple[str | None, str | None]:
    """Worktree committen und (commit_sha, branch) zurückgeben.

    ``BIBI_IN_PLACE=1`` (``bibi-ctrl test``, User-Fund 2026-07-14) überspringt
    den Commit bewusst — bewusst NICHT an ``BIBI_REPO_ROOT``s Präsenz gekoppelt
    wie in einer früheren Version: ``_finish()`` unten liest dieselbe Variable
    für ``output_ref`` (ein ganz anderer Zweck), ein gemeinsames Gate hätte
    also auch den Output-Verweis für in-place-Läufe stumm auf ``None`` gelassen
    (leeres Journal-Transkript, dauerhaft) statt nur den Commit zu unterdrücken."""
    if env.get("BIBI_IN_PLACE") == "1":
        return None, None
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

    out_path_str = env.get("BIBI_OUTPUT_PATH", "")
    repo_root_str = env.get("BIBI_REPO_ROOT", "")
    output_ref: str | None = None
    if out_path_str and repo_root_str:
        try:
            output_ref = Path(out_path_str).relative_to(repo_root_str).as_posix()
        except ValueError:
            output_ref = out_path_str

    if outcome == "wall_time":
        status, reason = "killed", "by_wall_time"
    elif outcome == "killed":
        # SIGTERM an den Wrapper (User-KILL, s. _on_sigterm() in run_app()/
        # run_job()) — reason="by_user" spiegelt job_kill()s (app.py)
        # Konvention für denselben Fall.
        status, reason = "killed", "by_user"
    elif outcome == "silence":
        status, reason = "zombie", "silence"
    elif outcome == "deferred":
        defer_secs = int(env.get("BIBI_DEFER_TIME") or "60")
        next_fire_at = time.time() + defer_secs
        status, reason = "deferred", None
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
        # Erschöpft (attempt_cur >= attempts_max): "error" ist von "running" aus
        # KEIN gültiger Übergang (lifecycle.py: nur failed --exhaust--> error,
        # keine (running, *) -> error-Kante) — ein direkter Report würde von
        # report_status() als "invalid" verworfen, OHNE Exception, OHNE
        # Journal-Eintrag — der Job bliebe für immer sichtbar "running", obwohl
        # der Prozess längst beendet ist (Fund PLAN-28: erstmals beobachtet mit
        # attempts=0, betrifft aber jeden Job, der seine Retries je ausschöpft
        # — auch bei attempts>0 ist die letzte, exhaustierende Meldung immer
        # ein direkter running→error-Versuch gewesen).
        #
        # Fix: erst der gültige Zwischenschritt running→failed (kein
        # next_fire_at, kein weiterer Retry geplant — "failed" ist nicht
        # TERMINAL, erzeugt also noch keinen Journal-Eintrag), dann sofort
        # synchron failed→error (jetzt gültig, EXHAUST-Kante) — beides hier im
        # selben Wrapper-Aufruf, kein Sweep/Daemon nötig.
        _report_terminal(env, status="failed", reason="nonzero_exit", exit_code=exit_code,
                         output_ref=output_ref, commit_sha=commit_sha, branch=branch,
                         attempt=attempt_cur, next_fire_at=None)
        _report_terminal(env, status="error", reason="nonzero_exit", exit_code=exit_code,
                         output_ref=output_ref, commit_sha=commit_sha, branch=branch)
        return

    _report_terminal(env, status=status, reason=reason, exit_code=exit_code,
                     output_ref=output_ref, commit_sha=commit_sha, branch=branch,
                     attempt=report_attempt, next_fire_at=next_fire_at)


# ── Ausführungs-Hauptfunktionen ───────────────────────────────────────────────

def run_app(env: dict[str, str]) -> int:
    """App-Typ: Child spawnen, stdout-Signale parsen, Aktivität überwachen.

    PLAN-11.3: kein HTTP-Server mehr — Signale kommen via stdout ``BIBI:{...}``.
    """
    kind = env["BIBI_JOB_TYPE"]
    handler = REGISTRY.get(kind)
    if handler is None:
        raise KeyError(f"unbekannter Job-Typ: {kind!r} (Registry: {sorted(REGISTRY)})")
    child_argv = handler.build_command(env)
    out_path = Path(env["BIBI_OUTPUT_PATH"])
    job_id = env.get("BIBI_JOB_ID", "unknown")
    db_path_str = env.get("BIBI_SCHEDULER_DB_PATH") or None

    spec = exec_backend.build_exec(child_argv, env)
    output.append(out_path, "phase", "prozess: wird gestartet …")
    proc = subprocess.Popen(
        spec.argv, cwd=spec.cwd, env=spec.env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, start_new_session=True,
    )

    lock = threading.Lock()
    outcome: list[str] = [""]
    current_status: list[str] = ["running"]  # shared mit deferred_watcher
    last_activity_ts: list[float] = [time.time()]

    if threading.current_thread() is threading.main_thread():
        def _on_sigterm(signum, frame):
            # User-Fund 2026-07-13: ``raise SystemExit(1)`` hier sprang an
            # main()s ``except Exception`` vorbei (SystemExit erbt von
            # BaseException, nicht Exception) — _finish() lief nach einem
            # Kill deshalb NIE, der Job blieb für immer "running" (auf dem
            # Host durch job_kill()s direkten DB-Write verdeckt, beim
            # gepinnten /run sichtbar geworden). Fix: outcome wie bei
            # wall_time/silence VOR _terminate_proc() setzen (kein Race mit
            # proc.wait()) und normal zurückkehren — proc.wait() retryt die
            # unterbrochene waitpid() automatisch (PEP 475), der übliche
            # Nachlauf (Threads joinen, _finish()) läuft danach ganz normal
            # weiter, jetzt mit outcome="killed".
            with lock:
                if not outcome[0]:
                    outcome[0] = "killed"
            _terminate_proc(proc, env)
        signal.signal(signal.SIGTERM, _on_sigterm)

    def pump(pipe, tag: str) -> None:
        assert pipe is not None
        for line in pipe:
            stripped = line.rstrip("\n")
            sig = _parse_bibi_line(stripped)
            if sig:
                name = sig.get("name")
                if name == "deferred":
                    if "seconds" in sig:
                        env["BIBI_DEFER_TIME"] = str(sig["seconds"])
                    with lock:
                        current_status[0] = "deferred"
                        if not outcome[0]:
                            outcome[0] = "deferred"
                elif name == "activity":
                    pass  # reiner Herzschlag (wie im DB-Pfad _handle_signal) — keine Zeile
                else:
                    _record_signal(sig, job_id=job_id, out_path=out_path,
                                   db_path_str=db_path_str,
                                   current_status=current_status, lock=lock)
            else:
                with lock:
                    output.append(out_path, tag, stripped)
            last_activity_ts[0] = time.time()

    started = time.time()
    wall_str = env.get("BIBI_WALL_TIME")
    silence_str = env.get("BIBI_SILENCE_TIMEOUT")

    monitors = [threading.Thread(
        target=_deferred_watcher, args=(proc, current_status, outcome, lock, env),
        daemon=True, name="deferred-watcher")]
    if silence_str:
        monitors.append(threading.Thread(
            target=_silence_monitor,
            args=(proc, int(silence_str), last_activity_ts, outcome, out_path, lock, env),
            daemon=True, name="silence-monitor"))
    if wall_str:
        monitors.append(threading.Thread(
            target=_wall_monitor, args=(proc, int(wall_str), started, outcome, out_path, lock, env),
            daemon=True, name="wall-monitor"))

    pump_threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out")),
        threading.Thread(target=pump, args=(proc.stderr, "err")),
    ]
    for t in [*pump_threads, *monitors]:
        t.start()
    proc.wait()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    for t in pump_threads:
        t.join()

    with lock:
        cs = current_status[0]
        oc = outcome[0]
    if not oc and cs == "deferred":
        outcome[0] = "deferred"

    # PLAN-24 Befund 5: mit aktiver Job-Image-Persistenz existiert der
    # Container jetzt noch (kein --rm, s. exec_backend.build_exec) — genau
    # hier committen, bevor finalize_container() ihn selbst aufräumt.
    exec_backend.finalize_container(env)
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

    # User-Feedback 2026-07-03: Startup-Phasen gehören ins Job-Output, auch
    # wenn sie vom Wrapper stammen — dieselbe Datei, die der Worker schon mit
    # seinen eigenen Phasen (worktree/container) vorbefüllt hat.
    output.append(out_path, "phase", "prozess: wird gestartet …")
    proc = subprocess.Popen(
        spec.argv, cwd=spec.cwd, env=spec.env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, start_new_session=True,
    )
    lock = threading.Lock()
    last_activity_ts: list[float] = [time.time()]

    def pump(pipe, tag: str) -> None:
        assert pipe is not None
        for line in pipe:
            with lock:
                output.append(out_path, tag, line.rstrip("\n"))
            last_activity_ts[0] = time.time()

    outcome: list[str] = [""]

    if threading.current_thread() is threading.main_thread():
        def _on_sigterm(signum, frame):
            # User-Fund 2026-07-13, s. run_app() für den vollen Kontext:
            # ``raise SystemExit(1)`` sprang an main()s ``except Exception``
            # vorbei — outcome VOR _terminate_proc() setzen (wie wall_time/
            # silence) statt eine BaseException durchzureichen, proc.wait()
            # retryt die unterbrochene waitpid() automatisch (PEP 475).
            with lock:
                if not outcome[0]:
                    outcome[0] = "killed"
            _terminate_proc(proc, env)
        signal.signal(signal.SIGTERM, _on_sigterm)

    started = time.time()
    wall_str = env.get("BIBI_WALL_TIME")
    silence_str = env.get("BIBI_SILENCE_TIMEOUT")

    monitors = []
    if wall_str:
        monitors.append(threading.Thread(
            target=_wall_monitor, args=(proc, int(wall_str), started, outcome, out_path, lock, env),
            daemon=True, name="wall-monitor"))
    if silence_str:
        monitors.append(threading.Thread(
            target=_silence_monitor,
            args=(proc, int(silence_str), last_activity_ts, outcome, out_path, lock, env),
            daemon=True, name="silence-monitor"))

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out")),
        threading.Thread(target=pump, args=(proc.stderr, "err")),
        *monitors,
    ]
    for t in threads:
        t.start()
    proc.wait()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    for t in threads[:2]:  # pump threads joinen (monitors sind daemon)
        t.join()

    # PLAN-24 Befund 5: mit aktiver Job-Image-Persistenz existiert der
    # Container jetzt noch (kein --rm, s. exec_backend.build_exec) — genau
    # hier committen, bevor finalize_container() ihn selbst aufräumt.
    exec_backend.finalize_container(env)
    _finish(env, proc.returncode or 0, outcome[0])
    return proc.returncode or 0


def main(argv: list[str] | None = None) -> int:
    env = dict(os.environ)
    try:
        kind = env.get("BIBI_JOB_TYPE", "")
        handler = REGISTRY.get(kind)
        if handler is None:
            raise KeyError(f"unbekannter Job-Typ: {kind!r} (Registry: {sorted(REGISTRY)})")
        if handler.long_lived:
            return run_app(env)
        return run_job(env)
    except Exception as exc:
        # Sicherheitsnetz (User-Feedback 2026-07-03): stürzt der Wrapper VOR
        # _finish() ab (z. B. beim Exec-Setup), lief bislang kein Report mehr —
        # der Job blieb für immer `running`, ohne dass je jemand es sah. Der
        # Fehler geht jetzt ins Job-Output, und _finish() meldet ihn wie einen
        # normalen Fehlschlag (Retry/Backoff greifen dadurch ganz normal).
        out_path_str = env.get("BIBI_OUTPUT_PATH")
        if out_path_str:
            try:
                output.append(Path(out_path_str), "phase", f"wrapper-fehler: {exc}")
            except Exception:  # noqa: BLE001 — defensiv, darf den Report nicht verhindern
                pass
        _finish(env, 1, "crash")
        return 1


if __name__ == "__main__":
    sys.exit(main())
