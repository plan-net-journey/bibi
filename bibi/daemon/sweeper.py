"""Scheduler-Sweeper: zeitgesteuerte Lifecycle-Übergänge (PLAN-3 §3.5).

Periodischer Loop (wie Synchronizer/Worker), der ``job_db.sweep`` tickt:
erschöpfte ``failed``-Jobs → ``error``, abgelaufene ``deferred``-Jobs →
``inactive``. Nur bei aktiver ``scheduler``-Rolle (der Scheduler besitzt diese
Zustände, §5.4). Worker-seitige Kanten (wall_time/silence) macht der Worker selbst.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from bibi.daemon import activity, job_db

log = logging.getLogger("bibi.sweeper")


class Sweeper:
    def __init__(self, *, db_path: Path | None = None, interval: float = 2.0,
                 autorun: bool = True, registry=None) -> None:
        self.db_path = db_path
        self.interval = interval
        self.autorun = autorun
        self.registry = registry  # WorkerRegistry für no_process-Reconcile (§3.6)
        self._task: asyncio.Task | None = None
        self._running = False

    def tick_once(self) -> dict:
        conn = job_db.connect(self.db_path)
        try:
            out = job_db.sweep(conn)
            if self.registry is not None:  # verwaiste running-Jobs toter Worker
                stale = self.registry.stale_workers()
                out["no_process"] = job_db.reconcile_no_process(conn, stale)
            if any(out.values()):  # nur wenn wirklich etwas terminalisiert wurde
                activity.emit(log, logging.INFO, "sweeper.reap", role="scheduler", **out)
            return out
        finally:
            conn.close()

    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                await loop.run_in_executor(None, self.tick_once)
            except Exception:
                log.exception("Sweeper-Tick fehlgeschlagen")
            await asyncio.sleep(self.interval)

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
