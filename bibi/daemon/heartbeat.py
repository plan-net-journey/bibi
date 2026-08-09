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
import os
import socket
import time
from pathlib import Path

from bibi import config, git_ops, repo
from bibi.daemon import activity
from bibi.engine_info import engine_info
from bibi.git_status import working_tree_status

log = logging.getLogger("bibi.heartbeat")


class Heartbeat:
    """Periodisches An-/Abmelden beim Scheduler (``POST /-/worker``). Hält
    Erfolg/Fehlschlag + Zeitpunkt des letzten Versuchs für die Status-Anzeige
    (§4.8) — kein Retry/Backoff nötig, der nächste Tick versucht es erneut."""

    def __init__(
        self, *, client, worker_name: str | None = None,
        repo_root: Path | None = None, interval: float = 15.0,
        role: str | None = None, session: bool = False,
    ) -> None:
        self.client = client
        self.worker_name = worker_name or socket.gethostname()
        self.host = socket.gethostname()
        # Bibi4-Iteration (Connected-Clients-Screen, User-Fund: derselbe
        # Client tauchte je nach Netzwerk unter anderem Namen auf) — einmal
        # pro Prozesslebensdauer gelesen/generiert, bleibt über Netzwerk-/
        # Hostname-Wechsel stabil, anders als worker_name/host oben.
        self.node_id = config.node_id()
        # Zweite Bibi4-Iteration, User-Fund ("Client Übersicht braucht die
        # Rollen je Client") — vom Aufrufer übergeben (daemon_cmd.py kennt
        # dort schon den aufgelösten Roles.active_names(), genauer als hier
        # erneut BIBI_ROLE zu parsen), nicht pro Beat neu ermittelt.
        self.role = role
        # m.rau/bibi#44: gehört dieser Daemon einer Sitzung (kein Supervisor)
        # oder einer Unit? Vom startenden Prozess übergeben, wie ``role`` —
        # **von außen ist das nicht feststellbar**, und genau deshalb muss es
        # mitreisen: der Nodes-Screen des Hosts entscheidet daran, was der
        # Restart-Knopf für diesen Knoten verspricht.
        self.session = session
        self.repo_root = repo_root
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._running = False
        self.last_ok: bool | None = None
        self.last_at: float | None = None

    def _tree_status(self) -> tuple[str, str | None]:
        """Git-Status des **Team-Repos** (Branch + Tree + Sync) plus Commit für
        den Heartbeat (A12).

        PLAN-18 Stufe 18.0: A12 verspricht, derselbe Heartbeat trage Tree+Sync
        mit hoch zum Scheduler — bisher lieferte diese Methode nur den
        Branch-Namen. Geteilte ``working_tree_status()``-Basis (auch von der
        CLI-Statusline genutzt) behebt das, ohne das Schema zu ändern
        (``git_status`` bleibt ein einzelner String).

        Der Commit kommt als **zweiter Rückgabewert** dazu (m.rau/bibi#19), nicht
        im String: zwei Knoten können beide „synced" melden und trotzdem auf
        verschiedenen Commits stehen, wenn einer vor fünf Minuten gesynct hat —
        „synced" allein beantwortet also nicht, ob zwei Knoten denselben Stand
        fahren. Beide Werte stammen aus **einem** ``git status``-Aufruf; der
        Heartbeat tickt alle 15 s, ein zweiter Aufruf pro Tick wäre reine
        Verschwendung.
        """
        root = self.repo_root or repo.root()
        s = working_tree_status(root)
        if s is None:
            return "n/a", None
        label = f"{s.branch or '(detached)'} · {s.tree} · {s.sync}"
        return label, (s.oid[:7] if s.oid else None)

    def _port(self) -> int | None:
        """Batch 9 Punkt 3 (Name+Host-Link im Nodes-Screen): ``BIBI_DAEMON_PORT``
        ist der tatsächliche Bind-Port dieses Prozesses, von ``daemon_cmd.py``
        VOR ``heartbeat.start()`` im Environment verankert (derselbe Wert, den
        auch der Wrapper für seinen Merge-back-Trigger liest) — kein neuer
        Mechanismus, nur ein weiterer Leser desselben schon etablierten Werts."""
        raw = os.environ.get("BIBI_DAEMON_PORT")
        return int(raw) if raw and raw.isdigit() else None

    def _apply_config_bundle(self, resp: dict) -> None:
        """PLAN-32 Stufe 32.2: Host hängt ``config_bundle`` nur an, wenn sich
        seine Version von der zuletzt hier angewandten unterscheidet (und der
        Knoten ``approved`` ist, s. ``app.py::worker_heartbeat()``) — die
        meisten Heartbeats tragen also nur die paar Bytes ``config_version``,
        kein Bundle. Komplettes Ersetzen (nicht mergen): das Bundle ist schon
        die vollständige aktuelle Sicht des Hosts."""
        bundle = resp.get("config_bundle")
        version = resp.get("config_version")
        if bundle is not None and version:
            config.write_distributed_env(bundle, version=version)

    def _forget_bootstrap_token(self) -> None:
        """Den Startschlüssel nach dem ersten Erfolg aus der env streichen
        (m.rau/bibi#141).

        **Ein Startschlüssel, der liegen bleibt, ist ein Dauergeheimnis** —
        also genau das, was mit ``BIBI_CONNECT_SECRET`` abgeschafft wurde. Er
        ist ohnehin verbraucht: der Scheduler hat seine Zeile beim Einlösen
        gelöscht, ein zweiter Versuch bekäme ``401``. Ihn stehenzulassen hieße
        also, ein wertloses Geheimnis dauerhaft auf der Platte zu halten und es
        bei jedem Heartbeat mitzuschicken.

        Bewusst an ``_beat()``s Erfolgspfad und nicht an den Start: ein
        Scheduler, der beim ersten Versuch nicht erreichbar ist, darf den
        Schlüssel nicht kosten.
        """
        werte = config.read_env()
        if not werte.get("BIBI_BOOTSTRAP_TOKEN"):
            return
        config.write_env({**werte, "BIBI_BOOTSTRAP_TOKEN": ""})
        os.environ.pop("BIBI_BOOTSTRAP_TOKEN", None)

    def _sync_state(self) -> dict:
        """Ob dieser Knoten seine Arbeit loswird (m.rau/bibi#74).

        **Der teuerste Meldeweg ist der, den es nicht gibt.**
        ``sync_conflict`` wurde ausschließlich lokal gelesen: von der
        Oberfläche desselben Knotens, von ``bibi-ctrl status``, von der
        Statusleiste. ``sarasate-client`` hing damit vom 2026-08-05 bis zum
        2026-08-07 fest und meldete es 102-mal alle drei Minuten an niemanden —
        in eine Weboberfläche auf Port 8781, die im Normalbetrieb niemand
        öffnet. Aufgefallen ist es, weil ein Rollout zufällig danach fragte.

        ``auto_sync`` reist mit, weil es dieselbe Frage beantwortet: ein Knoten
        mit abgeschaltetem Sync ist nicht kaputt, aber seine Arbeit bleibt
        genauso liegen.

        Scheitert das Lesen, bleiben beide ``None`` — *unbekannt*, nicht *in
        Ordnung*. Ein Heartbeat darf daran nicht scheitern; die Angabe ist eine
        Beigabe, nicht sein Zweck.
        """
        try:
            from bibi import state
            return {"sync_conflict": state.get_sync_conflict(),
                    "auto_sync": state.get_auto_sync()}
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return {"sync_conflict": None, "auto_sync": None}

    def _beat(self) -> None:
        try:
            git_status, git_commit = self._tree_status()
            resp = self.client.register(
                self.worker_name, self.host, git_status,
                node_id=self.node_id,
                git_user=git_ops.git_user_name(self.repo_root or repo.root()),
                role=self.role, port=self._port(),
                client_config_version=config.distributed_config_version(),
                # m.rau/bibi#19: welche Engine fährt dieser Knoten. Ohne die
                # Angabe konnte ein Deploy sein eigenes Ergebnis nicht prüfen —
                # der letzte Nachweis lief über ein Verhaltensmerkmal des neuen
                # Codes in einer Logzeile, also über Indizien.
                engine=engine_info().label(),
                # m.rau/bibi#67: der Arbeitsbaum des Engine-Checkouts, damit
                # die Engine-Zelle dieselbe dreiteilige Auskunft geben kann
                # wie die Repo-Zelle. None bei einem VCS-Pin — dort gibt es
                # keinen Arbeitsbaum, und der Chip entfaellt.
                engine_tree=engine_info().tree_status(),
                # m.rau/bibi#44: ob ein Neustart-Knopf für diesen Knoten
                # überhaupt einen Neustart bedeutet.
                session=self.session,
                # m.rau/bibi#74: ob dieser Knoten seine Arbeit ueberhaupt
                # loswird. Pro Beat frisch gelesen — das Flag kippt zwischen
                # zwei Beats, und ein beim Start eingefrorener Wert waere
                # schlimmer als keiner.
                **self._sync_state(),
                git_commit=git_commit,
                # m.rau/bibi#141: nur beim allerersten Mal gesetzt — danach hat
                # ``_forget_bootstrap_token()`` ihn aus der env gestrichen.
                bootstrap_token=(config.read_env().get("BIBI_BOOTSTRAP_TOKEN") or None))
            if resp:
                self._apply_config_bundle(resp)
            self._forget_bootstrap_token()
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

    def _deregister(self) -> bool:
        """Dem Host sagen, dass dieser Knoten geht (m.rau/bibi#47).

        Blockierendes HTTP, deshalb aus dem Executor gerufen — und defensiv wie
        ``_beat()``: ein nicht erreichbarer Host darf das Beenden nicht
        aufhalten. Kommt die Abmeldung nicht an, greift die 60-Sekunden-Stale-
        Erkennung wie bisher.
        """
        deregister = getattr(self.client, "deregister", None)
        if deregister is None:  # älterer/anderer Client-Typ
            return False
        try:
            ok = bool(deregister(self.node_id))
        except Exception:  # noqa: BLE001
            ok = False
        activity.emit(log, logging.INFO if ok else logging.DEBUG,
                      "connect.disconnect",
                      "Beim Host abgemeldet" if ok else
                      "Abmeldung beim Host nicht bestätigt — Stale-Frist greift",
                      role="connect", worker=self.worker_name)
        return ok

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Erst die Schleife anhalten, dann abmelden — umgekehrt könnte ein Tick
        # dazwischenfunken und den Knoten sofort wieder anmelden.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._deregister)
