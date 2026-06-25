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
import signal
import subprocess
import sys
from pathlib import Path

from bibi import repo
from bibi.daemon import job_db, worktree
from bibi.schedule.lifecycle import TERMINAL
from bibi.schedule.models import Status

log = logging.getLogger("bibi.worker")


def _output_path(repo_root: Path, job_id: str) -> Path:
    return repo_root / "data" / "job" / job_id / "output.jsonl"


def _build_env(reservation: dict, *, out_path: Path, worktree_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["BIBI_JOB_TYPE"] = reservation["kind"]
    env["BIBI_JOB_ID"] = reservation["id"]
    env["BIBI_OUTPUT_PATH"] = str(out_path)
    env["BIBI_WORKTREE"] = str(worktree_path)
    if reservation["kind"] == "job":
        env["BIBI_JOB_CMD"] = reservation["payload"]
    elif reservation["kind"] == "claude":
        env["BIBI_JOB_PROMPT"] = reservation["payload"]
        if reservation.get("model"):
            env["BIBI_JOB_MODEL"] = reservation["model"]
    return env


def execute_reservation(
    reservation: dict, *, repo_root: Path, work_dir: Path,
    db_path: Path | None = None, worker_name: str | None = None,
    register=None,
) -> dict:
    """Einen reservierten Job ausführen (synchron). Gibt Run-Eckdaten zurück.

    ``register(id, proc|None)`` (optional) trägt den Wrapper-Prozess für ``kill``
    ein/aus. Ist der Job beim Abschluss bereits terminal (z. B. ``killed``),
    überschreibt der Worker den Zustand **nicht**."""
    jid, slug, kind = reservation["id"], reservation["slug"], reservation["kind"]
    wt_path = worktree.prepare(repo_root=repo_root, work_dir=work_dir, slug=slug)
    out_path = _output_path(repo_root, jid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = _build_env(reservation, out_path=out_path, worktree_path=wt_path)

    # eigene Session ⇒ kill kann die ganze Prozessgruppe (Wrapper + Child) treffen.
    proc = subprocess.Popen(
        [sys.executable, "-m", "bibi.wrapper"],
        env=env, cwd=str(repo_root), start_new_session=True,
    )
    if register is not None:
        register(jid, proc)
    code = proc.wait()
    if register is not None:
        register(jid, None)

    commit_sha = worktree.commit(worktree=wt_path, message=f"{slug}: run {jid}", slug=slug)

    reported: str | None = None
    conn = job_db.connect(db_path)
    try:
        cur = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
        if cur is not None and Status(cur["status"]) not in TERMINAL:
            reported = "complete" if code == 0 else "failed"
            rel = out_path.relative_to(repo_root).as_posix()
            job_db.report_status(
                conn, jid, status=reported, exit_code=code,
                output_ref=rel, worker=worker_name,
            )
    finally:
        conn.close()
    return {"id": jid, "exit_code": code, "commit": commit_sha, "status": reported}


class Worker:
    """Async-Loop, der reservierte Jobs ausführt (im Daemon-Lifespan gestartet)."""

    def __init__(
        self, *, repo_root: Path | None = None, work_dir: Path | None = None,
        db_path: Path | None = None, poll_interval: float = 1.0,
        worker_name: str | None = None, autopoll: bool = True,
    ) -> None:
        self.repo_root = repo_root
        self.work_dir = work_dir
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.worker_name = worker_name
        # autopoll=False: nur die Routen (Streams/kill) bedienen, kein Pull-Loop —
        # für Tests und für reine Stream-Knoten ohne lokale Ausführung.
        self.autopoll = autopoll
        self._procs: dict[str, subprocess.Popen] = {}
        self._task: asyncio.Task | None = None
        self._running = False

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
        """Einen Job reservieren + ausführen. ``False`` = nichts zu tun."""
        conn = job_db.connect(self.db_path)
        try:
            res = job_db.reserve_next(conn, worker=self.worker_name)
        finally:
            conn.close()
        if res is None:
            return False
        root, work = self._roots()
        try:
            execute_reservation(
                res, repo_root=root, work_dir=work, db_path=self.db_path,
                worker_name=self.worker_name, register=self._register,
            )
        except Exception:  # ein kaputter Run darf den Loop nicht killen (§2.7)
            log.exception("Job-Ausführung fehlgeschlagen: %s", res.get("id"))
        return True

    def kill(self, job_id: str) -> bool:
        """SIGTERM an die Prozessgruppe des laufenden Wrappers (best-effort)."""
        proc = self._procs.get(job_id)
        if proc is None or proc.poll() is not None:
            return False
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
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

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
