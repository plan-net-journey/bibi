"""Registry verbundener Worker beim Scheduler (DESIGN §4.5/A12; PLAN-3 §3.6).

In-Memory (ein Daemon-Prozess), thread-safe — verbundene Worker melden sich per
Heartbeat (``POST /-/worker``) an; ``GET /-/worker`` + ``/-/status`` lesen sie.
Geht der Daemon neu auf, bauen die Heartbeats die Registry binnen Sekunden wieder
auf (keine Persistenz nötig).
"""

from __future__ import annotations

import threading
import time

#: Ohne Heartbeat seit dieser Spanne (s) gilt ein Worker als veraltet.
STALE_AFTER = 60.0


class WorkerRegistry:
    def __init__(self) -> None:
        self._w: dict[str, dict] = {}
        self._lock = threading.Lock()

    def heartbeat(
        self, worker: str, host: str, git_status: str | None = None, *,
        node_id: str | None = None, git_user: str | None = None,
        role: str | None = None, port: int | None = None, now: float | None = None,
        engine: str | None = None, engine_installed: str | None = None,
        engine_tree: str | None = None,
        git_commit: str | None = None, session: bool | None = None,
        sync_conflict: bool | None = None, auto_sync: bool | None = None,
        merge_stuck: list[str] | None = None,
    ) -> dict:
        """``node_id`` (Bibi4-Iteration, User-Fund: derselbe physische Client
        tauchte je nach Netzwerk mit unterschiedlichem ``worker``-Namen auf,
        alte Einträge blieben stale liegen) ist jetzt der Registry-Schlüssel
        statt ``worker`` — ein Client mit wechselndem Anzeigenamen, aber
        stabiler ``node_id``, aktualisiert dieselbe Zeile statt eine neue
        anzulegen. Fällt auf ``worker`` zurück, wenn kein ``node_id``
        mitgeschickt wird (älterer Client vor dieser Änderung) — schlechter
        als eine stabile ID, aber nicht schlechter als das bisherige
        Verhalten. ``role`` (zweite Bibi4-Iteration, User-Fund: "Client
        Übersicht braucht die Rollen je Client") ist der rohe
        ``BIBI_ROLE``-String des sendenden Knotens, unverändert gespeichert.
        ``port`` (Batch 9 Punkt 3) ist der tatsächliche Bind-Port des
        sendenden Knotens, für den Name+Host-Link im Nodes-Screen.

        ``engine``/``git_commit`` (m.rau/bibi#19): der installierte Engine-Stand
        und der Commit des Team-Repos. Ein Knoten konnte bisher nicht sagen, was
        er fährt — ein Deploy war damit nicht überprüfbar, sondern nur über
        Verhaltensmerkmale des neuen Codes zu erschließen. Beide werden
        unverändert durchgereicht; ein Client, der sie nicht sendet, hinterlässt
        sie leer statt einen alten Wert zu konservieren (sonst zeigte der Screen
        nach einem Downgrade des Clients dauerhaft den letzten bekannten Stand).

        ``session`` (m.rau/bibi#44): ob der sendende Daemon einer Sitzung gehört
        (kein Supervisor) oder einer Unit. ``None`` heißt *unbekannt* — ein
        Client, der es nicht sendet, ist älter als diese Änderung; der
        Nodes-Screen verhält sich für ihn wie bisher, statt eine Herkunft zu
        behaupten, die er nicht kennt.

        ``sync_conflict``/``auto_sync`` (m.rau/bibi#74): **ob dieser Knoten
        seine Arbeit überhaupt loswird.** Beide Werte wurden bis zum 2026-08-09
        ausschließlich lokal gelesen — von der Oberfläche desselben Knotens,
        von ``bibi-ctrl status``, von der Statusleiste. Ein blockierter
        Synchronizer konnte seinen Zustand damit niemandem mitteilen:
        ``sarasate-client`` hing 43 Stunden fest und meldete es 102-mal an
        eine Weboberfläche, die im Normalbetrieb niemand öffnet. Aufgefallen
        ist es erst, weil ein Rollout zufällig danach fragte — und ein Rollout
        ist kein Überwachungswerkzeug.

        ``None`` heißt auch hier *unbekannt*, nicht *in Ordnung*: ein Knoten,
        der nichts sendet, darf nicht als gesund gelten.

        ``merge_stuck`` (m.rau/bibi#111): eskalierte ``agent/*``-Branches aus
        der Merge-Quarantäne dieses Knotens (``data/merge_quarantine.json``) —
        die zweite Konflikt-Sorte neben ``sync_conflict``: nicht "dieser Knoten
        kommt mit origin nicht klar", sondern "die Arbeit eines Jobs kommt
        nicht nach trunk". Reiste bisher nirgendwohin, sichtbar nur lokal in
        ``bibi-ctrl status``/der Statusline desselben Knotens."""
        now = time.time() if now is None else now
        key = node_id or worker
        with self._lock:
            entry = self._w.get(key) or {"connected_at": now}
            entry.update(worker=worker, host=host, git_status=git_status,
                         node_id=node_id, git_user=git_user, role=role, port=port,
                         engine=engine, engine_installed=engine_installed,
                         engine_tree=engine_tree,
                         git_commit=git_commit, session=session,
                         sync_conflict=sync_conflict, auto_sync=auto_sync,
                         merge_stuck=merge_stuck,
                         last_heartbeat=now)
            self._w[key] = entry
            return dict(entry)

    def remove(self, key: str) -> bool:
        """Einen Eintrag abmelden (m.rau/bibi#47). ``True``, wenn es ihn gab.

        Der Schlüssel ist derselbe wie bei :meth:`heartbeat` — die ``node_id``,
        sonst der Anzeigename. Ein flüchtiger Knoten kommt und geht mehrmals
        täglich; ohne diesen Weg bleibt er nach jedem Gehen 60 Sekunden lang als
        „frisch" gemeldet und danach dauerhaft als veraltete Zeile stehen, bis
        der Host-Daemon selbst neu startet.

        Die Stale-Erkennung daneben bleibt bestehen und wird **nicht** ersetzt:
        sie ist das Netz für Absturz, Netzverlust und ``kill -9``. Diese Methode
        macht nur den Normalfall sauber.
        """
        with self._lock:
            return self._w.pop(key, None) is not None

    def list(self, *, stale_after: float = STALE_AFTER, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with self._lock:
            return [
                {**e, "stale": (now - e["last_heartbeat"]) > stale_after}
                for e in self._w.values()
            ]

    def fresh_count(self, *, stale_after: float = STALE_AFTER, now: float | None = None) -> int:
        return sum(1 for w in self.list(stale_after=stale_after, now=now) if not w["stale"])

    def stale_workers(self, *, stale_after: float = STALE_AFTER, now: float | None = None) -> set[str]:
        """Namen **bekannter, aber abgelaufener** Worker (für no_process-Reconcile).
        Unbekannte (nie angemeldete, z. B. lokale) Worker sind absichtlich NICHT
        enthalten — sonst würde ein lebender lokaler Worker fälschlich verwaisen."""
        return {w["worker"] for w in self.list(stale_after=stale_after, now=now) if w["stale"]}
