"""LocalPinnedLoop: rollenunabhängiger Sweep + Dispatch für gepinnte Läufe (PLAN-28).

Läuft auf **jedem** Knoten, unabhängig von ``roles.scheduler``/``roles.worker`` —
anders als der team-weite Sweeper/Worker, die nur bei aktiver Rolle existieren
(``app.py``: ``if roles.scheduler: ...``). Grund: jeder Knoten hat seine eigene
lokale ``jobs.sqlite`` (§3.2), es gibt keine zentrale, netzwerk-geteilte Job-DB —
ein gepinnter ``/run``-Lauf auf einem reinen Client-Knoten wird also niemals von
sarasates Sweeper/Scheduler gesehen. Ohne diesen Loop bliebe Retry (Backoff-
Redispatch) und Deferred-Re-Arm für solche Läufe tot.

Zuständig ausschließlich für ``jobs.pinned_host == dieser Host`` — Team-Queue-
Zeilen (``pinned_host IS NULL``) bleiben komplett unangetastet
(``reserve_next(pinned_only=True)``, s. dort). ``sweep()`` selbst bleibt
unscoped (sweept die ganze lokale Tabelle) — das ist auf einem reinen Client
harmlos (dort gibt es nur gepinnte Zeilen), auf einem ``scheduler``-Knoten
redundant-aber-idempotent zum dortigen Sweeper.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path

from bibi.daemon import job_db

log = logging.getLogger("bibi.pinned")


class LocalPinnedLoop:
    def __init__(
        self, *, db_path: Path | None = None, repo_root: Path | None = None,
        work_dir: Path | None = None, interval: float = 2.0,
        host: str | None = None, worker_name: str | None = None,
        autorun: bool = True,
    ) -> None:
        self.db_path = db_path
        self.repo_root = repo_root
        self.work_dir = work_dir
        self.interval = interval
        self.host = host or socket.gethostname()
        self.worker_name = worker_name or self.host
        self.autorun = autorun
        self._task: asyncio.Task | None = None
        self._running = False

    def _roots(self) -> tuple[Path, Path]:
        from bibi import repo
        root = self.repo_root or repo.root()
        work = self.work_dir or (root / "data" / "worktrees")
        return root, work

    def tick_once(self) -> dict:
        conn = job_db.connect(self.db_path)
        try:
            swept = job_db.sweep(conn)
            reservation = job_db.reserve_next(conn, host=self.host, pinned_only=True)
        finally:
            conn.close()
        dispatched = 0
        if reservation is not None:
            from bibi.daemon.scheduler_client import LocalScheduler
            from bibi.daemon.worker import execute_reservation
            root, work = self._roots()
            activity_slug = reservation.get("slug")
            try:
                execute_reservation(
                    reservation, repo_root=root, work_dir=work,
                    client=LocalScheduler(self.db_path),
                    worker_name=self.worker_name, host=self.host,
                )
                dispatched = 1
            except Exception:
                log.exception("LocalPinnedLoop-Dispatch fehlgeschlagen: %s", activity_slug)
        return {**swept, "dispatched": dispatched}

    async def _loop(self) -> None:
        # Erst schlafen, DANN ticken (anders als der teamweite Sweeper, der
        # sofort tickt) — dieser Loop läuft jetzt rollenunabhängig in JEDEM
        # create_app()-Aufruf mit, also auch in praktisch jedem Test. Ein
        # Sofort-Tick würde per run_in_executor() in einem eigenen Thread
        # sofort job_db.connect() (PRAGMA/Migrationen) gegen dieselbe
        # jobs.sqlite auslösen, mit der der Test selbst synchron arbeitet —
        # live gefunden: "database is locked" in einem parallelen Testlauf,
        # weil beide Connections dieselbe frische Datei gleichzeitig anlegten.
        loop = asyncio.get_event_loop()
        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break
            try:
                await loop.run_in_executor(None, self.tick_once)
            except Exception:
                log.exception("LocalPinnedLoop-Tick fehlgeschlagen")

    async def start(self) -> None:
        if not self.autorun:
            return
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
