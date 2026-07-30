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
        role: str | None = None,
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
                git_commit=git_commit)
            if resp:
                self._apply_config_bundle(resp)
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
