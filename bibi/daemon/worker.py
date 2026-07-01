"""Worker: führt zugeteilte Jobs aus (DESIGN §4.5/§7.5; PLAN-3 §3.3).

Ablauf je Run: Worktree ``agent/<slug>`` vorbereiten → Wrapper als **eigenen
Prozess** spawnen (``python -m bibi.wrapper``, env-konfiguriert, §7.5) → auf Exit
warten → Worktree committen (Bibi) → Lifecycle melden. Der Wrapper schreibt
``data/job/{id}/output.jsonl``; der Worker-Daemon serviert daraus die
Stream-Endpunkte (host-process-Phase §7.7 — kein per-Job-HTTP wie im Docker-Bild).

Single-Node: der Worker reserviert **lokal** über ``job_db.reserve_next`` (genau
1 Scheduler im selben Prozess). Der HTTP-Pull (``/-/scheduler/next``) ist der
*Remote*-Pfad und kommt mit ``--connect`` (Stufe 3.6).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

from bibi import config, repo, state
from bibi.daemon import activity, job_db, worktree
from bibi.wrapper import exec_backend
from bibi.schedule import backoff, discovery
from bibi.schedule.models import CLAUDE_PAYLOAD_RE as _CLAUDE_RE
from bibi.schedule.models import Status

log = logging.getLogger("bibi.worker")


def _output_path(repo_root: Path, job_id: str) -> Path:
    return repo_root / "data" / "job" / job_id / "output.jsonl"


def _last_activity(out_path: Path, default: float) -> float:
    """Zeitpunkt der jüngsten Output-Zeile (mtime), **nie vor Lauf-Start** (``default``).

    Seit per-Run-Output (``data/job/<slug:fire>/``) startet jeder Lauf mit frischer
    Datei — eine veraltete mtime aus einem Vorlauf kann nicht mehr durchschlagen.
    Der ``default``-Floor bleibt als Robustheit: existiert die Datei noch nicht
    (langsam startender Container), liefert ``stat`` einen Fehler ⇒ Lauf-Start, und
    der ``max`` deckt einen evtl. wiederverwendeten run_id-Pfad mit ab."""
    try:
        return max(out_path.stat().st_mtime, default)
    except OSError:
        return default


def _monitored_wait(
    proc: subprocess.Popen, *, out_path: Path, started: float,
    wall_time: int | None, silence_timeout: int | None, poll: float = 0.1,
    job_id: str | None = None,
) -> tuple[int, str]:
    """Auf den Child warten und dabei wall_time/silence überwachen (§5.5).

    Gibt ``(exit_code, outcome)`` mit ``outcome`` ∈ {``normal``, ``wall_time``,
    ``silence``}. Bei wall_time/silence wird der Lauf terminiert (container-aware)."""
    while proc.poll() is None:
        now = time.time()
        if wall_time and now - started > wall_time:
            _terminate(proc, job_id=job_id)
            return proc.wait(), "wall_time"
        if silence_timeout and now - _last_activity(out_path, started) > silence_timeout:
            _terminate(proc, job_id=job_id)
            return proc.wait(), "silence"
        time.sleep(poll)
    return proc.returncode, "normal"


# ── Container-Exec-Konfig + Terminierung (PLAN-8 Slice B) ────────────────────

def _exec_config() -> dict[str, str]:
    """Container-Exec-Env aus Prozess-Env > Knoten-Config (an den Wrapper gereicht).
    Leer/`host` ⇒ Host-Modus. Inkl. ANTHROPIC_API_KEY für claude-im-Container (D5).
    Alle Einträge aus ~/.config/bibi/env mit Prefix ``BIBI_JOB_ENV_`` werden
    ohne Prefix weitergereicht — damit können beliebige Credentials ohne
    Engine-Änderung in Jobs verfügbar gemacht werden."""
    cfg = config.read_env()
    out: dict[str, str] = {}
    for key in ("BIBI_EXEC_MODE", "BIBI_JOB_IMAGE", "BIBI_DOCKER_BIN",
                "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        val = os.environ.get(key) or cfg.get(key)
        if val:
            out[key] = val
    # Dynamische Job-Env-Vars: BIBI_JOB_ENV_FOO → FOO im Container
    prefix = "BIBI_JOB_ENV_"
    for raw_key, val in {**cfg, **os.environ}.items():
        if raw_key.startswith(prefix) and val:
            out[raw_key[len(prefix):]] = val
    return out


def _is_container() -> bool:
    return (_exec_config().get("BIBI_EXEC_MODE") or "host").strip().lower() == "container"


def _docker_env() -> dict[str, str]:
    # docker-bin-Dir in den PATH (Cred-Helper docker-credential-*).
    bin_ = exec_backend.resolve_docker_bin({**os.environ, **config.read_env()})
    env = os.environ.copy()
    env["PATH"] = str(Path(bin_).parent) + os.pathsep + env.get("PATH", "")
    return env


def _docker(args: list[str]) -> None:
    """Best-effort docker-Subkommando (stop/kill) — Fehler dürfen nie hochpropagieren."""
    bin_ = exec_backend.resolve_docker_bin({**os.environ, **config.read_env()})
    try:
        subprocess.run([bin_, *args], capture_output=True, env=_docker_env(),
                       timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _traefik_dynamic_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "traefik" / "dynamic"


def _register_app_route(job_id: str, port: int) -> None:
    """Traefik-Route für die App eines Jobs registrieren (PLAN-11.4, §7.5/§7.7).

    File-Provider statt Docker-Labels: der App-Port ist erst zur Laufzeit bekannt
    (``app_register``-Signal, ``bibi.job``), Docker-Labels lassen sich an einem
    laufenden Container nicht mehr nachträglich setzen. Host-Modus: Ziel ist der
    lokale Loopback (Worker und App teilen den Host). Container-Modus: Ziel ist
    der Container selbst (``bibi-<id>``), da Traefik im selben Docker-Netz läuft
    (``bibi-net``, PLAN-9 §2)."""
    target = f"bibi-{job_id}:{port}" if _is_container() else f"127.0.0.1:{port}"
    name = f"job-{job_id}-app"
    cfg = {
        "http": {
            "routers": {name: {"rule": f"PathPrefix(`/-/job/{job_id}/app`)", "service": name}},
            "services": {name: {"loadBalancer": {"servers": [{"url": f"http://{target}"}]}}},
        },
    }
    path = _traefik_dynamic_dir(repo.root()) / f"{job_id}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _deregister_app_route(job_id: str) -> None:
    """Traefik-Route beim Job-Ende entfernen (Gegenstück zu ``_register_app_route``)."""
    (_traefik_dynamic_dir(repo.root()) / f"{job_id}.yml").unlink(missing_ok=True)


def _terminate(proc: subprocess.Popen, *, job_id: str | None = None) -> None:
    """Lauf beenden. Container (D7): ``docker stop bibi-<id>`` gibt dem Job graceful
    SIGTERM + Frist (eskaliert selbst auf SIGKILL); zusätzlich die Host-Wrapper-Gruppe
    terminieren. Host: SIGTERM → der Wrapper propagiert an den Child (dessen SIGTERM-
    Handler killt die Child-Prozessgruppe). Backstop nach 5 s: SIGKILL an Wrapper."""
    if job_id is not None and _is_container():
        _docker(["stop", exec_backend.container_name(job_id)])
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    # Backstop: wenn Wrapper nach 5 s noch lebt → SIGKILL (Daemon-Thread, kein Blockieren).
    def _escalate() -> None:
        import time as _t
        _t.sleep(5.0)
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    threading.Thread(target=_escalate, daemon=True, name="kill-escalate").start()


def _run_wrapper(
    *, job_id: str, slug: str, kind: str, payload: str, model: str | None = None,
    soul: str | None = None, session: str | None = None,
    wall_time: int | None = None, silence_timeout: int | None = None,
    app_port: int | None = None, app_prefix: str | None = None,
    exec_mode: str | None = None,
    hitl_timeout: int | None = None,
    defer_time: int | None = None,
    repo_root: Path, work_dir: Path, register=None, ephemeral: bool = False,
    run_id: str | None = None,
    # Detach-Modus: Wrapper-Prozess meldet selbst Terminal-Status + Commit (§9).
    detach: bool = False,
    worker_name: str | None = None, host: str | None = None,
    attempt: int = 0, attempts: int = 1,
    backoff_type: str | None = None,
    scheduler_db_path: str | None = None,  # Direkter DB-Zugriff (kein HTTP)
    scheduler_url: str | None = None,      # HTTP-Reporting (App-Typ / Remote)
) -> tuple[int, str | None, Path, str, int | None]:
    """Worktree → Wrapper-Subprozess → Commit/Report.

    ``detach=True`` (disponierte Jobs): Wrapper läuft eigenständig — kein Wait,
    kein Commit, kein Report im Worker. Wrapper übernimmt alles.
    ``detach=False`` (``run_local``, ephemeral): bisheriger blockierender Pfad.
    ``run_id`` bestimmt den ``output.jsonl``-Pfad (pro Run eindeutig).
    Der Container-Name bleibt an ``job_id`` (Docker-Namensregel, §3.3b).
    5. Rückgabewert: Wrapper-PID (detach) oder ``None`` (nicht-detach)."""
    out_run_id = run_id or job_id
    wt_path = worktree.prepare(repo_root=repo_root, work_dir=work_dir, slug=slug)
    activity.emit(log, logging.DEBUG, "worktree.prepare", role="worker",
                  slug=slug, run_id=out_run_id, path=str(wt_path))
    out_path = _output_path(repo_root, out_run_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["BIBI_JOB_ID"] = job_id
    env["BIBI_OUTPUT_PATH"] = str(out_path)
    env["BIBI_WORKTREE"] = str(wt_path)

    # PLAN-10 Stufe 10.0: claude:-Prefix-Expansion beim Spawn.
    # kind aus DB ist immer "job"; effective_type steuert den Wrapper-Dispatch.
    _claude_m = _CLAUDE_RE.match(payload.strip()) if payload else None
    if _claude_m:
        effective_type = "claude"
        env["BIBI_JOB_TYPE"] = "claude"
        env["BIBI_JOB_PROMPT"] = _claude_m.group(1).strip()
        env["BIBI_CLAUDE_BIN"] = (os.environ.get("BIBI_CLAUDE_BIN")
                                  or config.read_env().get("BIBI_CLAUDE_BIN") or "claude")
        if model:
            env["BIBI_JOB_MODEL"] = model
        if soul:
            env["BIBI_JOB_SOUL"] = soul
        if session:
            env["BIBI_JOB_SESSION"] = session
    else:
        effective_type = "job"
        env["BIBI_JOB_TYPE"] = "job"
        env["BIBI_JOB_CMD"] = payload
        if app_port:
            env["BIBI_APP_PORT"] = str(app_port)
        if app_prefix:
            env["BIBI_APP_PREFIX"] = app_prefix
        if hitl_timeout is not None:
            env["BIBI_HITL_TIMEOUT"] = str(hitl_timeout)
        if defer_time is not None:
            env["BIBI_DEFER_TIME"] = str(defer_time)

    # Detach-Modus: Commit + Report im Wrapper-Prozess.
    if detach:
        env["BIBI_REPO_ROOT"] = str(repo_root)
        env["BIBI_JOB_SLUG"] = slug
        env["BIBI_RUN_ID"] = out_run_id
        if ephemeral:
            env["BIBI_EPHEMERAL"] = "1"
        if wall_time is not None:
            env["BIBI_WALL_TIME"] = str(wall_time)
        if silence_timeout is not None:
            env["BIBI_SILENCE_TIMEOUT"] = str(silence_timeout)
        if worker_name:
            env["BIBI_WORKER_NAME"] = worker_name
        if host:
            env["BIBI_HOST"] = host
        env["BIBI_ATTEMPT"] = str(attempt)
        env["BIBI_ATTEMPTS"] = str(attempts)
        if backoff_type:
            env["BIBI_BACKOFF"] = backoff_type
        # Reporting-Ziel: explizit gesetzt > lokale DB > HTTP-Daemon.
        if scheduler_db_path:
            env["BIBI_SCHEDULER_DB_PATH"] = scheduler_db_path
        elif scheduler_url:
            env["BIBI_SCHEDULER_URL"] = scheduler_url
        elif not env.get("BIBI_SCHEDULER_URL"):
            env["BIBI_SCHEDULER_URL"] = "http://127.0.0.1:8769"

    env.update(_exec_config())
    if exec_mode:
        env["BIBI_EXEC_MODE"] = exec_mode.strip().lower()
    if _is_container():
        _docker(["rm", "-f", exec_backend.container_name(job_id)])

    started = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-m", "bibi.wrapper"],
        env=env, cwd=str(repo_root), start_new_session=True,
    )
    if register is not None:
        register(job_id, proc)

    if detach:
        # Wrapper-Prozess läuft eigenständig — sofort zurückkehren.
        return 0, None, out_path, "detached", proc.pid

    # Blockierender Pfad (run_local / ephemeral): warten, committen, zurückgeben.
    code, outcome = _monitored_wait(
        proc, out_path=out_path, started=started,
        wall_time=wall_time, silence_timeout=silence_timeout, job_id=job_id,
    )
    if register is not None:
        register(job_id, None)

    commit_sha = worktree.commit(worktree=wt_path, message=f"{slug}: run {out_run_id}", slug=slug)
    activity.emit(log, logging.DEBUG, "worktree.commit", role="worker",
                  slug=slug, run_id=out_run_id, commit=(commit_sha or None))
    if ephemeral:
        worktree.remove(repo_root=repo_root, worktree=wt_path)
        activity.emit(log, logging.DEBUG, "worktree.remove", role="worker",
                      slug=slug, run_id=out_run_id)
    return code, commit_sha, out_path, outcome, None


def _retry_fields(reservation: dict) -> dict:
    """``failed``-Statusfelder mit Backoff/attempt++ (Retry; Dauerfehler exhaust→error, §5.5)."""
    attempt = (reservation.get("attempt") or 0) + 1
    base = float(os.environ.get("BIBI_RETRY_BASE") or backoff.DEFAULT_BASE)
    nf = time.time() + backoff.delay(reservation.get("backoff") or "fixed", attempt, base=base)
    return {"status": "failed", "attempt": attempt, "next_fire_at": nf}


def _report_level(status: str) -> int:
    """Log-Level für ein terminales Outcome: Fehlschläge fallen nicht im INFO-Strom unter."""
    if status == "error":
        return logging.ERROR
    if status in ("failed", "killed", "zombie"):
        return logging.WARNING
    return logging.INFO


def execute_reservation(
    reservation: dict, *, repo_root: Path, work_dir: Path, client,
    worker_name: str | None = None, host: str | None = None, register=None,
) -> dict:
    """Einen **disponierten** (reservierten) Job ausführen + via ``client`` melden,
    inkl. Lifecycle-Kanten (§5.5): wall_time→killed, silence→zombie, exit≠0→failed
    (mit Backoff, attempt++). Alle Ausführungs-/Retry-Parameter kommen aus der
    **Reservierung** (so braucht ein Remote-Worker keine lokale DB, §3.6).

    Der Status wird über ``client.report`` gesetzt; ist der Job bereits terminal
    (z. B. ``killed`` per ``/-/job/{id}/kill``), lehnt der Scheduler den Übergang ab
    (``invalid``) und der Worker überschreibt nichts."""
    jid = reservation["id"]
    run_id = job_db.run_id_for(reservation["slug"], jid, reservation.get("fire", 0))
    host = host or socket.gethostname()
    attempt = reservation.get("attempt") or 0
    attempts = reservation.get("attempts") or 1
    # Lokaler DB-Pfad (LocalScheduler): Wrapper kann direkt schreiben statt HTTP.
    # client.db_path kann None sein (kein expliziter Pfad) → Default auflösen.
    # App-Jobs nutzen immer HTTP, damit wrapper_url beim Relay registriert wird.
    from bibi.daemon.scheduler_client import LocalScheduler as _LocalScheduler
    if isinstance(client, _LocalScheduler):
        _resolved_db: str = str(job_db.db_path(client.db_path))
    else:
        _resolved_db = str(client.db_path) if getattr(client, "db_path", None) else ""
    try:
        kind = reservation["kind"]
        silence_timeout = reservation.get("silence_timeout")
        # PLAN-10 Stufe 10.0: SQLite-Direct wenn verfügbar; sonst HTTP.
        _daemon_port = int(os.environ.get("BIBI_DAEMON_PORT", "8769"))
        _sched_db_path: str | None = _resolved_db or None
        _sched_url: str | None = None if _sched_db_path else f"http://127.0.0.1:{_daemon_port}"
        _, _, out_path, outcome, proc_pid = _run_wrapper(
            job_id=jid, slug=reservation["slug"], kind=kind,
            payload=reservation["payload"], model=reservation.get("model"),
            soul=reservation.get("soul"), session=reservation.get("session"),
            wall_time=reservation.get("wall_time"),
            silence_timeout=silence_timeout,
            app_port=reservation.get("app_port"),
            app_prefix=reservation.get("app_prefix"),
            exec_mode=reservation.get("exec_mode"),
            hitl_timeout=reservation.get("hitl_timeout"),
            defer_time=reservation.get("defer_time"),
            repo_root=repo_root, work_dir=work_dir, register=register, ephemeral=False,
            run_id=run_id, detach=True,
            worker_name=worker_name, host=host,
            attempt=attempt, attempts=attempts,
            backoff_type=reservation.get("backoff"),
            scheduler_db_path=_sched_db_path,
            scheduler_url=_sched_url,
        )
        if proc_pid is not None and _sched_db_path:
            _pid_conn = job_db.connect(Path(_sched_db_path))
            try:
                job_db.report_pid(
                    _pid_conn, jid, proc_pid, job_db.proc_started_at(proc_pid),
                )
            finally:
                _pid_conn.close()
    except Exception as exc:
        # Setup-Fehler vor Wrapper-Start: Job nicht in `running` hängen lassen.
        activity.emit(log, logging.ERROR, "worker.setup_error",
                      "Setup vor Wrapper-Start fehlgeschlagen", role="worker",
                      slug=reservation.get("slug"), run_id=jid, error=str(exc))
        log.exception("Setup fehlgeschlagen: %s", jid)
        fields = {**_retry_fields(reservation), "exit_code": -1, "output_ref": None,
                  "worker": worker_name, "host": host, "commit_sha": None, "branch": None}
        res = client.report(jid, **fields)
        return {"id": jid, "exit_code": -1, "commit": None,
                "status": fields["status"] if res == "ok" else None,
                "outcome": "setup_error"}

    # Detach: Wrapper läuft selbstständig weiter. Worker kehrt sofort zurück.
    activity.emit(log, logging.INFO, "worker.spawned", role="worker",
                  slug=reservation.get("slug"), run_id=jid, outcome=outcome)
    return {"id": jid, "exit_code": None, "commit": None,
            "status": "running", "outcome": "detached"}


def _resolve_spec(repo_root: Path, slug: str):
    """Eine erfasste Schedule-MD per Slug finden (für ``/run <slug>``)."""
    res = discovery.discover(repo_root / "vault")
    return res.found.get(slug)


def run_local(
    *, slug: str | None = None, cmd: str | None = None, kind: str = "job",
    model: str | None = None, repo_root: Path | None = None,
    work_dir: Path | None = None, db_path: Path | None = None,
    worker_name: str = "local", register=None,
) -> dict:
    """**Lokale** On-Demand-Ausführung (§3.3b). Umgeht den Scheduler vollständig:
    **kein** ``jobs``-Eintrag, **kein** ``/-/scheduler/status`` — nur die lokale
    Journal-Zeile (``domain='local'``) + ``output.jsonl`` bleiben am Knoten.

    Entweder ``slug`` (erfasste MD) **oder** ``cmd`` (ad-hoc, rein lokal)."""
    repo_root = repo_root or repo.root()
    work_dir = work_dir or (repo_root / "data" / "worktrees")
    eff_soul = eff_session = None
    if cmd is not None:
        eff_slug, payload, eff_kind, eff_model = slug or "adhoc", cmd, kind, model
    else:
        if not slug:
            raise ValueError("run_local braucht entweder slug oder cmd")
        pr = _resolve_spec(repo_root, slug)
        if pr is None or pr.spec is None:
            raise LookupError(f"kein Schedule mit Slug {slug!r}")
        s = pr.spec
        eff_slug, payload, eff_kind, eff_model = s.slug, s.payload, s.kind.value, s.model
        eff_soul, eff_session = s.soul, s.session

    jid = secrets.token_hex(4)
    started = time.time()
    code, commit_sha, out_path, outcome, _ = _run_wrapper(
        job_id=jid, slug=eff_slug, kind=eff_kind, payload=payload, model=eff_model,
        soul=eff_soul, session=eff_session,
        repo_root=repo_root, work_dir=work_dir, register=register, ephemeral=True,
    )
    finished = time.time()
    status = "complete" if code == 0 else "failed"
    rel = out_path.relative_to(repo_root).as_posix()

    conn = job_db.connect(db_path)
    try:
        job_db.write_local_journal(
            conn, run_id=f"{eff_slug}:{jid}", slug=eff_slug, kind=eff_kind,
            status=status, exit_code=code, output_ref=rel,
            host=socket.gethostname(), worker=worker_name,
            started_at=started, finished_at=finished, payload=payload,
        )
    finally:
        conn.close()
    return {"id": jid, "slug": eff_slug, "kind": eff_kind, "status": status,
            "exit_code": code, "output_ref": rel, "commit": commit_sha}


class Worker:
    """Async-Loop, der reservierte Jobs ausführt (im Daemon-Lifespan gestartet)."""

    def __init__(
        self, *, repo_root: Path | None = None, work_dir: Path | None = None,
        db_path: Path | None = None, poll_interval: float = 1.0,
        worker_name: str | None = None, autopoll: bool = True,
        client=None, connect: bool = False, scheduler_url: str | None = None,
        secret: str | None = None, heartbeat_interval: float = 15.0,
        max_concurrent: int = 4,
    ) -> None:
        self.repo_root = repo_root
        self.work_dir = work_dir
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.worker_name = worker_name or socket.gethostname()
        self.host = socket.gethostname()
        # autopoll=False: nur die Routen (Streams/kill) bedienen, kein Pull-Loop —
        # für Tests und für reine Stream-Knoten ohne lokale Ausführung.
        self.autopoll = autopoll
        self.connect = connect
        self.heartbeat_interval = heartbeat_interval
        self.max_concurrent = max_concurrent
        # Scheduler-Client: lokal (Single-Node) oder remote (--connect, §3.6).
        if client is not None:
            self.client = client
        elif connect:
            from bibi.daemon.scheduler_client import RemoteScheduler
            self.client = RemoteScheduler(scheduler_url or "http://127.0.0.1:8769", secret=secret)
        else:
            from bibi.daemon.scheduler_client import LocalScheduler
            self.client = LocalScheduler(db_path)
        self._procs: dict[str, subprocess.Popen] = {}
        self._app_routes: dict[str, int] = {}  # job_id → zuletzt registrierter app_port
        self._task: asyncio.Task | None = None
        self._hb_task: asyncio.Task | None = None
        self._running = False
        self._maint_active = False  # Wartungs-Übergang nur einmal loggen (kein Tick-Spam)

    def _roots(self) -> tuple[Path, Path]:
        root = self.repo_root or repo.root()
        work = self.work_dir or (root / "data" / "worktrees")
        return root, work

    def output_path(self, job_id: str) -> Path:
        """``output.jsonl``-Pfad des **aktuellen** Laufs eines Jobs (Live-Routen).

        Löst die stabile ``job_id`` über die DB auf den laufenden run_id
        (``run_id_for()``) auf — so zeigt ``/-/job/{id}/…`` immer nur den
        jüngsten Lauf (Historie früherer Läufe läuft über ``output_ref`` im
        Journal). Ist die ID unbekannt (z. B. ephemerer ``/run``), bleibt sie
        selbst der Pfad."""
        root, _ = self._roots()
        conn = job_db.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT slug, fire FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        run_id = job_db.run_id_for(row["slug"], job_id, row["fire"]) if row else job_id
        return _output_path(root, run_id)

    def _register(self, job_id: str, proc: subprocess.Popen | None) -> None:
        if proc is None:
            self._procs.pop(job_id, None)
        else:
            self._procs[job_id] = proc

    def _poll_app_routes(self) -> None:
        """``app_port``-Änderungen aktiver Jobs in Traefik-Routen übersetzen
        (PLAN-11.4, §7.5/§7.7) — Gegenstück zum stdout-``app_register``-Signal
        (``bibi.job``, von ``_handle_signal`` in ``job_db.app_port`` geschrieben).
        „Aktiv" = hat einen laufenden Prozess (``running``/``awaiting``), nicht
        bloß „nicht terminal" (``pending``/``failed``/``deferred`` haben keinen).
        Jobs ohne laufenden Prozess deregistrieren ihre Route wieder."""
        conn = job_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, app_port, status FROM jobs WHERE app_port IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        # Nicht terminal ≠ hat einen laufenden Prozess: pending (noch nicht
        # gestartet) und failed/deferred (Prozess schon beendet, wartet auf
        # Retry) sind ebenfalls nicht-terminal, haben aber keinen App-Server.
        live = {row["id"]: row["app_port"] for row in rows
                if Status(row["status"]) in (Status.RUNNING, Status.AWAITING)}
        for jid, port in live.items():
            if self._app_routes.get(jid) != port:
                _register_app_route(jid, port)
                self._app_routes[jid] = port
        for jid in [j for j in self._app_routes if j not in live]:
            _deregister_app_route(jid)
            self._app_routes.pop(jid, None)

    def tick_once(self) -> bool:
        """Einen Job über den Client reservieren + ausführen. ``False`` = nichts zu tun.

        Wartungsmodus (§ daemon-weit): pausiert das Reservieren neuer Jobs. Der
        Übergang wird **einmal** geloggt (nicht je Tick → kein Spam)."""
        self._poll_app_routes()
        if state.get_maintenance():
            if not self._maint_active:
                self._maint_active = True
                activity.emit(log, logging.INFO, "worker.maintenance",
                              "Wartungsmodus aktiv — keine neuen Jobs reserviert",
                              role="worker")
            return False
        if self._maint_active:
            self._maint_active = False
            activity.emit(log, logging.INFO, "worker.resumed",
                          "Wartungsmodus beendet — Dispatch wieder aktiv", role="worker")
        # Beendete Wrapper-Procs aus _procs entfernen (Slot-Freigabe).
        for jid in [jid for jid, p in list(self._procs.items()) if p.poll() is not None]:
            self._procs.pop(jid, None)
        if len(self._procs) >= self.max_concurrent:
            return False  # Slot voll — nächster Tick versucht es erneut
        res = self.client.next(worker=self.worker_name, host=self.host)
        if res is None:
            return False
        root, work = self._roots()
        activity.emit(log, logging.INFO, "worker.pickup", role="worker",
                      slug=res.get("slug"), run_id=res.get("id"), kind=res.get("kind"))
        try:
            execute_reservation(
                res, repo_root=root, work_dir=work, client=self.client,
                worker_name=self.worker_name, host=self.host, register=self._register,
            )  # Detach: kehrt sofort zurück; Wrapper läuft eigenständig.
        except Exception:
            activity.emit(log, logging.ERROR, "worker.error",
                          "Job-Setup fehlgeschlagen", role="worker",
                          slug=res.get("slug"), run_id=res.get("id"))
            log.exception("Job-Setup fehlgeschlagen: %s", res.get("id"))
        return True

    def _git_status(self) -> str:
        """Kurzer Git-Status des Knotens (Branch) für den Heartbeat (A12)."""
        root, _ = self._roots()
        try:
            r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                               cwd=root, capture_output=True, text=True, check=False)
            return r.stdout.strip() if r.returncode == 0 else "n/a"
        except OSError:
            return "n/a"

    async def _heartbeat_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                await loop.run_in_executor(
                    None, lambda: self.client.register(self.worker_name, self.host, self._git_status()))
                activity.emit(log, logging.DEBUG, "worker.heartbeat", role="worker",
                              worker=self.worker_name)
            except Exception:
                activity.emit(log, logging.WARNING, "worker.heartbeat",
                              "Heartbeat fehlgeschlagen (Scheduler erreichbar?)", role="worker")
            await asyncio.sleep(self.heartbeat_interval)

    def kill(self, job_id: str) -> bool:
        """Lauf beenden — container-aware (PLAN-8 D7): ``docker stop`` (graceful) +
        Host-Wrapper-Gruppe; im Container-Modus auch dann ``docker kill`` als Backstop,
        wenn der Wrapper schon weg ist (Container könnte verwaist weiterlaufen)."""
        proc = self._procs.get(job_id)
        if proc is None or proc.poll() is not None:
            if _is_container():  # Wrapper weg, Container evtl. noch da → einsammeln
                _docker(["kill", exec_backend.container_name(job_id)])
                return True
            return False
        _terminate(proc, job_id=job_id)
        activity.emit(log, logging.INFO, "worker.kill", "Lauf beendet (graceful)",
                      role="worker", run_id=job_id)
        return True

    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                did = await loop.run_in_executor(None, self.tick_once)
            except Exception:
                log.exception("Worker-Tick fehlgeschlagen")
                did = False
            if not did:
                await asyncio.sleep(self.poll_interval)

    async def start(self) -> None:
        if not self.autopoll:
            return  # nur Routen bedienen, kein Pull-Loop
        self._running = True
        self._task = asyncio.create_task(self._loop())
        if self.connect:  # beim entfernten Scheduler an-/abmelden (Heartbeat, A12)
            self.client.register(self.worker_name, self.host, self._git_status())
            self._hb_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        self._running = False
        for task in (self._task, self._hb_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = self._hb_task = None
