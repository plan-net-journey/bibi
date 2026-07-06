"""Heartbeat für ``--connect`` (DESIGN §2.4/4.2, A12) — unabhängig von der
Worker-Rolle.

User-Feedback 2026-07-05: vorher lebte der Heartbeat-Loop ausschließlich in
``Worker.start()`` (``bibi/daemon/worker.py``), das Worker-Objekt selbst wurde
aber nur gebaut, wenn ``roles.worker`` aktiv war (``daemon_cmd.py``). Ein
reiner Client (Synchronizer + ``--connect``, DESIGN.md/Client Requirements.md:
„weder Scheduler noch Worker") sendete dadurch **nie** einen Heartbeat —
``--connect`` allein war wirkungslos. Dieser eigenständige Mechanismus läuft
für jeden Knoten mit ``--connect``, unabhängig von ``--worker``.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import time
from pathlib import Path

from bibi import repo
from bibi.daemon import activity

log = logging.getLogger("bibi.heartbeat")


class Heartbeat:
    """Periodisches An-/Abmelden beim Scheduler (``POST /-/worker``). Hält
    Erfolg/Fehlschlag + Zeitpunkt des letzten Versuchs für die Status-Anzeige
    (§4.8) — kein Retry/Backoff nötig, der nächste Tick versucht es erneut."""

    def __init__(
        self, *, client, worker_name: str | None = None,
        repo_root: Path | None = None, interval: float = 15.0,
    ) -> None:
        self.client = client
        self.worker_name = worker_name or socket.gethostname()
        self.host = socket.gethostname()
        self.repo_root = repo_root
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._running = False
        self.last_ok: bool | None = None
        self.last_at: float | None = None

    def _git_status(self) -> str:
        """Kurzer Git-Status des Knotens (Branch) für den Heartbeat (A12)."""
        root = self.repo_root or repo.root()
        try:
            r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                               cwd=root, capture_output=True, text=True, check=False)
            return r.stdout.strip() if r.returncode == 0 else "n/a"
        except OSError:
            return "n/a"

    def _beat(self) -> None:
        try:
            self.client.register(self.worker_name, self.host, self._git_status())
            self.last_ok = True
            activity.emit(log, logging.DEBUG, "connect.heartbeat", role="connect",
                          worker=self.worker_name)
        except Exception:
            self.last_ok = False
            activity.emit(log, logging.WARNING, "connect.heartbeat",
                          "Heartbeat fehlgeschlagen (Scheduler erreichbar?)", role="connect")
        self.last_at = time.time()

    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break
            await loop.run_in_executor(None, self._beat)

    async def start(self) -> None:
        self._running = True
        self._beat()  # sofort An-/Abmelden, wie zuvor Worker.start() (synchron)
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
