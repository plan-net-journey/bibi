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
import os
import signal
import time
from pathlib import Path

from bibi.daemon import activity, job_db

log = logging.getLogger("bibi.sweeper")


def _shutdown_self() -> None:
    """Sich selbst beenden, wenn die letzte Sitzung gegangen ist (#46).

    Über SIGTERM, nicht ``os._exit()`` — genau wie beim Restart-Endpunkt: nur so
    greifen uvicorns ``timeout_graceful_shutdown`` und das ``lifespan``-Finally
    mit dem Job-Drain (#49). Ein harter Abbruch hebelte beide Zusagen aus.
    """
    os.kill(os.getpid(), signal.SIGTERM)



def _session_revision() -> float:
    """Lazy-Wrapper um ``session_registry.revision()`` (m.rau/bibi#50).

    Der Import liegt bewusst in der Funktion: ``session_registry`` zieht
    ``bibi.repo`` und damit git nach, und der Sweeper wird auch dort gebaut,
    wo es um Sitzungen gar nicht geht.
    """
    from bibi.daemon import session_registry
    return session_registry.revision()


class Sweeper:
    def __init__(self, *, db_path: Path | None = None, interval: float = 2.0,
                 autorun: bool = True, registry=None,
                 local_worker_name: str | None = None,
                 pid_check_interval: float = 45.0,
                 session_scoped: bool = False,
                 on_last_session_gone=None) -> None:
        self.db_path = db_path
        self.interval = interval
        # Eigenes, gröberes Intervall für die PID-Prüfung (m.rau/bibi#38, so von
        # m.rau vorgesehen): jeder Durchlauf kostet einen ``proc_started_at()``-
        # Syscall je laufendem Job. Im 2-Sekunden-Takt des Sweepers wäre das
        # Verschwendung — die Landung eines toten Jobs ist nicht dringend,
        # wichtig ist nur, dass sie überhaupt passiert, solange der Daemon
        # durchläuft. Vorher geschah das ausschließlich beim Daemon-Start, was
        # bei ``Restart=always`` praktisch „nie" bedeutete.
        self.pid_check_interval = pid_check_interval
        # Startzeit statt 0.0: der erste PID-Check kommt nach einem vollen
        # Intervall, nicht sofort. Der Startzeitpunkt ist bereits abgedeckt —
        # `app.py::_scheduler_startup()` ruft `reconcile_orphans()` mit
        # `include_starting=True`, und nur dort ist ein vorgefundenes
        # `starting` zweifelsfrei eine Waise. Ein sofortiger Sweeper-Check wäre
        # eine Dopplung, und mit 0.0 wäre die Bedingung gegen einen
        # Unix-Timestamp ohnehin immer erfüllt: das Intervall hätte gar nicht
        # gegriffen.
        self._last_pid_check = time.time()
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
        # Sitzungs-Registry (m.rau/bibi#46) — im selben groben Takt wie die
        # PID-Prüfung oben, aus demselben Grund: es ist nicht dringend, nur
        # verlässlich. Nur ein von einer Sitzung gestarteter Daemon fährt
        # dadurch herunter; einer aus einer Autostart-Unit NIE, egal wie der
        # Zähler steht.
        self.session_scoped = session_scoped
        self.on_last_session_gone = on_last_session_gone or _shutdown_self
        # Startzeit — die erste Zählung kommt nach einem vollen Intervall, und
        # das IST die Karenz: ein Daemon, der eine Handbreit vor „seiner"
        # Sitzung hochkommt, wird nicht sofort abgeräumt.
        #
        # Der erste Entwurf hatte stattdessen eine Sperre „scharf erst, wenn je
        # eine Sitzung gesehen wurde". Der Rauchtest von #48 hat sie widerlegt:
        # wer eine Sitzung öffnet und gleich wieder schließt, ist beim ersten
        # Blick nach 45 s längst weg — der Daemon hatte dann nie eine Sitzung
        # gesehen, wurde nie scharf und stand für immer. Genau der Alltagsfall
        # „kurz reinschauen". Eine Karenz ab Start deckt beide Richtungen ab,
        # ohne sich zu merken, was sie mal gesehen hat.
        #
        # Folge, bewusst in Kauf genommen: ein ``daemon run --session`` ganz
        # ohne Sitzung beendet sich nach einem Intervall von selbst. Das Flag
        # sagt „ich gehöre Sitzungen" — sind keine da, ist das die richtige
        # Antwort und keine Überraschung.
        self._last_session_check = time.time()
        # Stand des Registry-Verzeichnisses bei der letzten Zählung
        # (m.rau/bibi#50). Beim Bau der **aktuelle** Wert, nicht ``None``: sonst
        # gälte der erste Tick als Änderung und nähme dem Daemon die Gnadenfrist
        # eine Zeile höher. Verschluckt wird dadurch nichts — meldet sich
        # zwischen Bau und erstem Tick eine Sitzung an oder ab, springt die
        # mtime, und genau das ist das Signal.
        self._last_session_revision = _session_revision()
        self._task: asyncio.Task | None = None
        self._running = False

    def tick_once(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        conn = job_db.connect(self.db_path)
        try:
            out = job_db.sweep(conn)
            if self.registry is not None:  # verwaiste running-Jobs toter Worker
                stale = self.registry.stale_workers() - {self.local_worker_name}
                out["no_process"] = job_db.reconcile_no_process(conn, stale)
            # PID-Prüfung des EIGENEN Workers (#38). reconcile_no_process() oben
            # nimmt ihn ausdrücklich aus (Schutz gegen Hostname-Kollisionen bei
            # co-located Host+Client) — für den lokalen Worker gab es dadurch
            # bisher überhaupt keine laufende Waisen-Erkennung.
            # include_starting=False: 'starting' heißt hier *gerade im Setup*
            # und darf nicht angefasst werden; nur der Daemon-Start weiß, dass
            # ein vorgefundenes 'starting' zwangsläufig eine Waise ist.
            if (self.local_worker_name
                    and now - self._last_pid_check >= self.pid_check_interval):
                self._last_pid_check = now
                out["no_pid"] = job_db.reconcile_orphans(
                    conn, self.local_worker_name, include_starting=False)
                # Ban-Reconcile (m.rau/bibi#23) im selben Takt: die Ban-Semantik
                # war nur halb gebaut — ein blockierter Knoten wird beim
                # Heartbeat mit 401 abgewiesen und bekommt kein Config-Bundle,
                # aber seine bereits laufenden Jobs blieben unangetastet in der
                # DB stehen. Ein Bann, der laufende Arbeit weiterlaufen lässt,
                # ist keiner. Derselbe periodische Rahmen, ein weiterer Aufruf
                # darin — genau die Frage, die #38 offengelassen hatte.
                if self.registry is not None:
                    out["banned"] = job_db.reconcile_blocked_nodes(
                        conn, self.registry.list(now=now))
            if any(out.values()):  # nur wenn wirklich etwas terminalisiert wurde
                activity.emit(log, logging.INFO, "sweeper.reap", role="scheduler", **out)
            # Sitzungs-Zählung (m.rau/bibi#46) NACH dem Reap-Log: sie ist keine
            # Terminalisierung und gehört nicht in dessen Zeile.
            self._check_sessions(now)
            return out
        finally:
            conn.close()

    def _check_sessions(self, now: float) -> None:
        """Fährt der Daemon herunter, weil die letzte Sitzung gegangen ist?

        Getrennt von ``tick_once()``s Job-Aufräumen gehalten: das eine ist
        Datenpflege, das andere beendet den Prozess — die beiden sollen sich
        beim Lesen nicht vermischen.

        **Die Drosselung gilt der Zählung, nicht dem Ereignis** (m.rau/bibi#50).
        ``live_pids()`` prüft jede PID einzeln; sie bei jedem Tick laufen zu
        lassen wäre verschwendet, solange sich nichts geändert hat. Nur war
        „nichts geändert" bisher eine Annahme statt einer Feststellung — und
        genau in den bis zu 45 Sekunden danach lebte ein Daemon weiter, dessen
        letzte Sitzung schon gegangen war. Ein sofort nachgestartetes ``bibi``
        fand seine Portdatei und hängte sich an, samt altem Code.

        ``session_registry.revision()`` ist ein einzelner ``stat`` und beantwortet
        die Frage direkt. Ändert sie sich, wird gezählt; sonst bleibt die
        Drosselung, für die es gute Gründe gibt.
        """
        if not self.session_scoped:
            return
        from bibi.daemon import session_registry
        rev = _session_revision()
        if (rev == self._last_session_revision
                and now - self._last_session_check < self.pid_check_interval):
            return
        self._last_session_check = now
        self._last_session_revision = rev
        if session_registry.count():
            return
        activity.emit(log, logging.INFO, "daemon.session_end",
                      "Letzte Sitzung beendet — Daemon fährt herunter",
                      role="daemon")
        self.on_last_session_gone()

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
