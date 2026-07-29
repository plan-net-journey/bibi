"""Scheduler-Sweeper: zeitgesteuerte Lifecycle-Übergänge (PLAN-3 §3.5).

Periodischer Loop (wie Synchronizer/Worker), der ``job_db.sweep`` tickt:
``failed``-Jobs ohne ``next_fire_at`` (Crash-Recovery, s. ``job_db.sweep()``-
Docstring) → ``error``, abgelaufene ``deferred``-Jobs → ``inactive``.
Rollenunabhängig gestartet (Bugfix — gepinnte ``/-/run``-Läufe sind bewusst
rollenunabhängig, ihr Aufräumer war es vorher nicht: ein erschöpfter gepinnter
Job auf einem reinen Client blieb sonst für immer in ``failed`` hängen).
Worker-seitige Kanten (wall_time/silence) macht der Worker selbst.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from bibi.daemon import activity, job_db

log = logging.getLogger("bibi.sweeper")


class Sweeper:
    def __init__(self, *, db_path: Path | None = None, interval: float = 2.0,
                 autorun: bool = True, registry=None,
                 local_worker_name: str | None = None) -> None:
        self.db_path = db_path
        self.interval = interval
        self.autorun = autorun
        self.registry = registry  # WorkerRegistry für no_process-Reconcile (§3.6)
        # Name des co-located Workers (falls aktiv) — nie als "stale" reconcilen,
        # egal was in der Registry steht. Der lokale Worker registriert sich nie
        # selbst dort (kein --connect nötig, um lokal zu dispatchen); lebt aber
        # ein fremder --connect-Knoten zufällig unter demselben Namen (z. B.
        # Hostname-Kollision bei co-located Host+Client), würde dessen veralteter
        # Registry-Eintrag sonst fälschlich die eigenen laufenden Jobs killen —
        # live gefunden 2026-07-11 (sarasate Host+Client-Deploy).
        self.local_worker_name = local_worker_name
        self._task: asyncio.Task | None = None
        self._running = False

    def tick_once(self) -> dict:
        conn = job_db.connect(self.db_path)
        try:
            out = job_db.sweep(conn)
            if self.registry is not None:  # verwaiste running-Jobs toter Worker
                stale = self.registry.stale_workers() - {self.local_worker_name}
                out["no_process"] = job_db.reconcile_no_process(conn, stale)
            if any(out.values()):  # nur wenn wirklich etwas terminalisiert wurde
                activity.emit(log, logging.INFO, "sweeper.reap", role="scheduler", **out)
            return out
        finally:
            conn.close()

    async def _loop(self) -> None:
        # Bugfix (User-Fund via Testfehlschlag: der jetzt rollenunabhängig
        # immer gestartete Sweeper tickte sofort beim Start — in Tests, die
        # gleich nach TestClient(app) synchron eine Zeile seeden und direkt
        # abfragen, konnte der allererste Tick dazwischenfunken, bevor der Test
        # überhaupt fertig geschrieben hatte. Exakt dasselbe Muster wie
        # Worker._loop() (PLAN-28: "ein zweiter, rollenunabhängig immer
        # gestarteter Worker lief sonst in praktisch jedem Test sofort ...
        # gegen dieselbe frische jobs.sqlite — 'database is locked'") — erst
        # schlafen, dann ticken, beim allerersten Durchlauf.
        await asyncio.sleep(self.interval)
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
