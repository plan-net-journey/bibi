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
import time
from pathlib import Path

from bibi import config, repo, state
from bibi.daemon import activity, job_db, worktree
from bibi.schedule import backoff, discovery
from bibi.schedule.lifecycle import TERMINAL
from bibi.schedule.models import Status

log = logging.getLogger("bibi.worker")


def _output_path(repo_root: Path, job_id: str) -> Path:
    return repo_root / "data" / "job" / job_id / "output.jsonl"


def _last_activity(out_path: Path, default: float) -> float:
    """Zeitpunkt der jüngsten Output-Zeile (mtime), sonst ``default``."""
    try:
        return out_path.stat().st_mtime
    except OSError:
        return default


def _monitored_wait(
    proc: subprocess.Popen, *, out_path: Path, started: float,
    wall_time: int | None, silence_timeout: int | None, poll: float = 0.1,
) -> tuple[int, str]:
    """Auf den Child warten und dabei wall_time/silence überwachen (§5.5).

    Gibt ``(exit_code, outcome)`` mit ``outcome`` ∈ {``normal``, ``wall_time``,
    ``silence``}. Bei wall_time/silence wird die Prozessgruppe terminiert."""
    while proc.poll() is None:
        now = time.time()
        if wall_time and now - started > wall_time:
            _terminate(proc)
            return proc.wait(), "wall_time"
        if silence_timeout and now - _last_activity(out_path, started) > silence_timeout:
            _terminate(proc)
            return proc.wait(), "silence"
        time.sleep(poll)
    return proc.returncode, "normal"


def _terminate(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def _run_wrapper(
    *, job_id: str, slug: str, kind: str, payload: str, model: str | None = None,
    soul: str | None = None, session: str | None = None,
    wall_time: int | None = None, silence_timeout: int | None = None,
    repo_root: Path, work_dir: Path, register=None, ephemeral: bool = False,
) -> tuple[int, str, Path, str]:
    """Der gemeinsame Ausführungs-Kern beider Pfade: Worktree → Wrapper-Subprozess
    (überwacht) → Commit. Gibt ``(exit_code, commit_sha, output_path, outcome)``.

    ``ephemeral=True`` (für ``/run``) entfernt den Worktree nach dem Lauf wieder.
    Der Wrapper ist die einzige Ausführungs-Einheit; nur der Aufrufweg
    unterscheidet disponiert (execute_reservation) von lokal (run_local), §3.3b."""
    wt_path = worktree.prepare(repo_root=repo_root, work_dir=work_dir, slug=slug)
    activity.emit(log, logging.DEBUG, "worktree.prepare", role="worker",
                  slug=slug, run_id=job_id, path=str(wt_path))
    out_path = _output_path(repo_root, job_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["BIBI_JOB_TYPE"] = kind
    env["BIBI_JOB_ID"] = job_id
    env["BIBI_OUTPUT_PATH"] = str(out_path)
    env["BIBI_WORKTREE"] = str(wt_path)
    if kind == "job":
        env["BIBI_JOB_CMD"] = payload
    elif kind == "claude":
        env["BIBI_JOB_PROMPT"] = payload
        # claude-Binary konfigurierbar: Prozess-Env > Knoten-Config > Default "claude".
        # Absoluter Pfad nötig, wenn claude nicht auf dem (Service-)PATH liegt.
        env["BIBI_CLAUDE_BIN"] = (os.environ.get("BIBI_CLAUDE_BIN")
                                  or config.read_env().get("BIBI_CLAUDE_BIN") or "claude")
        if model:
            env["BIBI_JOB_MODEL"] = model
        if soul:
            env["BIBI_JOB_SOUL"] = soul
        if session:
            env["BIBI_JOB_SESSION"] = session

    started = time.time()
    # eigene Session ⇒ kill kann die ganze Prozessgruppe (Wrapper + Child) treffen.
    proc = subprocess.Popen(
        [sys.executable, "-m", "bibi.wrapper"],
        env=env, cwd=str(repo_root), start_new_session=True,
    )
    if register is not None:
        register(job_id, proc)
    code, outcome = _monitored_wait(
        proc, out_path=out_path, started=started,
        wall_time=wall_time, silence_timeout=silence_timeout,
    )
    if register is not None:
        register(job_id, None)

    commit_sha = worktree.commit(worktree=wt_path, message=f"{slug}: run {job_id}", slug=slug)
    activity.emit(log, logging.DEBUG, "worktree.commit", role="worker",
                  slug=slug, run_id=job_id, commit=(commit_sha or None))
    if ephemeral:
        worktree.remove(repo_root=repo_root, worktree=wt_path)
        activity.emit(log, logging.DEBUG, "worktree.remove", role="worker",
                      slug=slug, run_id=job_id)
    return code, commit_sha, out_path, outcome


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
    host = host or socket.gethostname()
    try:
        code, commit_sha, out_path, outcome = _run_wrapper(
            job_id=jid, slug=reservation["slug"], kind=reservation["kind"],
            payload=reservation["payload"], model=reservation.get("model"),
            soul=reservation.get("soul"), session=reservation.get("session"),
            wall_time=reservation.get("wall_time"),
            silence_timeout=reservation.get("silence_timeout"),
            repo_root=repo_root, work_dir=work_dir, register=register, ephemeral=False,
        )
    except Exception as exc:
        # Setup/Run-Fehler VOR jeder Statusmeldung (Worktree/Wrapper/Commit, Fund B):
        # den Job NICHT in `running` hängen lassen. Als Fehlschlag melden
        # (Backoff/attempt++; Dauerfehler exhaust→error) → sofort als Abweichung sichtbar.
        activity.emit(log, logging.ERROR, "worker.setup_error",
                      "Setup/Run vor Statusmeldung fehlgeschlagen", role="worker",
                      slug=reservation.get("slug"), run_id=jid, error=str(exc))
        log.exception("Setup/Run fehlgeschlagen: %s", jid)
        fields = {**_retry_fields(reservation), "exit_code": -1, "output_ref": None,
                  "worker": worker_name, "host": host, "commit_sha": None, "branch": None}
        res = client.report(jid, **fields)
        return {"id": jid, "exit_code": -1, "commit": None,
                "status": fields["status"] if res == "ok" else None,
                "outcome": "setup_error"}

    rel = out_path.relative_to(repo_root).as_posix()
    # Commit-SHA + Branch des Worktrees journalen (v6, §2.3): "" → None.
    branch = worktree.branch_name(reservation["slug"]) if commit_sha else None
    common = {"exit_code": code, "output_ref": rel, "worker": worker_name, "host": host,
              "commit_sha": commit_sha or None, "branch": branch}
    if outcome == "wall_time":
        fields = {"status": "killed", "reason": "by_wall_time", **common}
    elif outcome == "silence":
        fields = {"status": "zombie", "reason": "silence", **common}
    elif code == 0:
        fields = {"status": "complete", **common}
    else:
        fields = {**_retry_fields(reservation), **common}

    res = client.report(jid, **fields)
    activity.emit(log, _report_level(fields["status"]), "worker.report", role="worker",
                  slug=reservation.get("slug"), run_id=jid, status=fields["status"],
                  reason=fields.get("reason"), exit_code=code, outcome=outcome,
                  applied=(res == "ok"))
    return {"id": jid, "exit_code": code, "commit": commit_sha,
            "status": fields["status"] if res == "ok" else None, "outcome": outcome}


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
    code, commit_sha, out_path, outcome = _run_wrapper(
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
            started_at=started, finished_at=finished,
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
        self._task: asyncio.Task | None = None
        self._hb_task: asyncio.Task | None = None
        self._running = False
        self._maint_active = False  # Wartungs-Übergang nur einmal loggen (kein Tick-Spam)

    def _roots(self) -> tuple[Path, Path]:
        root = self.repo_root or repo.root()
        work = self.work_dir or (root / "data" / "worktrees")
        return root, work

    def output_path(self, job_id: str) -> Path:
        root, _ = self._roots()
        return _output_path(root, job_id)

    def _register(self, job_id: str, proc: subprocess.Popen | None) -> None:
        if proc is None:
            self._procs.pop(job_id, None)
        else:
            self._procs[job_id] = proc

    def tick_once(self) -> bool:
        """Einen Job über den Client reservieren + ausführen. ``False`` = nichts zu tun.

        Wartungsmodus (§ daemon-weit): pausiert das Reservieren neuer Jobs. Der
        Übergang wird **einmal** geloggt (nicht je Tick → kein Spam)."""
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
            )  # terminales Outcome loggt execute_reservation selbst (worker.report)
        except Exception:  # ein kaputter Run darf den Loop nicht killen (§2.7)
            activity.emit(log, logging.ERROR, "worker.error",
                          "Job-Ausführung fehlgeschlagen", role="worker",
                          slug=res.get("slug"), run_id=res.get("id"))
            log.exception("Job-Ausführung fehlgeschlagen: %s", res.get("id"))
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
        """SIGTERM an die Prozessgruppe des laufenden Wrappers (best-effort)."""
        proc = self._procs.get(job_id)
        if proc is None or proc.poll() is not None:
            return False
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            activity.emit(log, logging.INFO, "worker.kill", "SIGTERM an Wrapper-Prozessgruppe",
                          role="worker", run_id=job_id)
            return True
        except (ProcessLookupError, OSError):
            return False

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
