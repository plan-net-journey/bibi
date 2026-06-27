"""Periodischer Auto-Rescan (PLAN-5 §5.4-Nachschlag).

bibi hat **keinen** Filesystem-Watcher — neue/geänderte Schedule-MDs werden sonst
nur beim Daemon-Start oder bei einem manuellen ``/-/rescan`` erfasst. Dieser Loop
schließt die Lücke: er ruft periodisch ``job_db.rescan`` (Default 180 s = 3 min,
per ``BIBI_RESCAN_INTERVAL`` übersteuerbar). Nur bei aktiver ``scheduler``-Rolle.

Wie Sweeper/Synchronizer: reiner Tick (``tick_once``) + asyncio-Loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from bibi.daemon import activity, job_db

log = logging.getLogger("bibi.scheduler")

DEFAULT_INTERVAL = 180.0


def resolve_interval() -> float:
    """Rescan-Intervall in Sekunden: ``BIBI_RESCAN_INTERVAL`` env, sonst 180."""
    raw = os.environ.get("BIBI_RESCAN_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_INTERVAL
    try:
        v = float(raw)
        return v if v > 0 else DEFAULT_INTERVAL
    except ValueError:
        return DEFAULT_INTERVAL


class Rescanner:
    def __init__(self, *, db_path: Path | None = None,
                 interval: float | None = None, autorun: bool = True) -> None:
        self.db_path = db_path
        self.interval = interval if interval is not None else resolve_interval()
        self.autorun = autorun
        self._task: asyncio.Task | None = None
        self._running = False

    def tick_once(self) -> dict:
        conn = job_db.connect(self.db_path)
        try:
            res = job_db.rescan(conn)
        finally:
            conn.close()
        changed = any(res.get(k) for k in ("inserted", "updated", "removed"))
        # Bei Änderung INFO (sichtbar), sonst DEBUG (kein 3-Minuten-Firehose).
        activity.emit(log, logging.INFO if changed else logging.DEBUG,
                      "scheduler.rescan", role="scheduler", auto=True,
                      inserted=res.get("inserted"), updated=res.get("updated"),
                      removed=res.get("removed"))
        return res

    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            await asyncio.sleep(self.interval)
            try:
                await loop.run_in_executor(None, self.tick_once)
            except Exception:
                log.exception("Auto-Rescan-Tick fehlgeschlagen")

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
