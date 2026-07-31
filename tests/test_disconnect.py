"""Abmelden beim Host (m.rau/bibi#47).

Ein flüchtiger Knoten kommt und geht mehrmals täglich. Ohne Abmeldung bleibt er
nach jedem Gehen 60 Sekunden lang als „frisch" gemeldet und danach dauerhaft als
veraltete Zeile stehen, bis der Host-Daemon selbst neu startet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi import config
from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app
from bibi.daemon.heartbeat import Heartbeat
from bibi.daemon.worker_registry import WorkerRegistry


# ── Die Registry ────────────────────────────────────────────────────────────


def test_remove_drops_the_entry():
    r = WorkerRegistry()
    r.heartbeat("w1", "h1", node_id="n1")
    assert r.remove("n1") is True
    assert r.list() == []


def test_remove_reports_an_unknown_key():
    assert WorkerRegistry().remove("gibt-es-nicht") is False


def test_remove_uses_the_same_key_as_heartbeat():
    # Ohne node_id ist der Anzeigename der Schlüssel — beide Wege müssen
    # dieselbe Zeile treffen, sonst meldet sich ein Altlast-Client nie ab.
    r = WorkerRegistry()
    r.heartbeat("w1", "h1")
    assert r.remove("w1") is True
    assert r.list() == []


def test_remove_leaves_other_nodes_alone():
    r = WorkerRegistry()
    r.heartbeat("w1", "h1", node_id="n1")
    r.heartbeat("w2", "h2", node_id="n2")
    r.remove("n1")
    assert [w["node_id"] for w in r.list()] == ["n2"]


def test_stale_detection_stays_in_place():
    # Der Endpunkt ERSETZT die Stale-Regel nicht — sie bleibt das Netz für
    # Absturz, Netzverlust und kill -9.
    r = WorkerRegistry()
    r.heartbeat("w1", "h1", node_id="n1", now=0.0)
    assert r.stale_workers(now=1000.0) == {"w1"}


# ── Die Route ───────────────────────────────────────────────────────────────


@pytest.fixture()
def host(team_repo: Path):
    """Ein Host mit Scheduler-Rolle und einem angemeldeten Knoten."""
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as c:
        c.post("/-/worker", json={"worker": "mac", "host": "Mac.local",
                                  "node_id": "n-mac"})
        yield c


def test_disconnect_removes_the_node(host):
    assert len(host.get("/-/worker").json()) == 1
    r = host.post("/-/worker/n-mac/disconnect")
    assert r.status_code == 200
    assert r.json() == {"node_id": "n-mac", "removed": True}
    assert host.get("/-/worker").json() == []


def test_disconnect_of_an_unknown_node_is_not_an_error(host):
    # Idempotent: wer zweimal geht, bekommt keinen Fehler. Ein Shutdown-Pfad
    # soll nicht an so etwas hängenbleiben.
    r = host.post("/-/worker/gibt-es-nicht/disconnect")
    assert r.status_code == 200
    assert r.json()["removed"] is False


def test_a_node_may_deregister_itself(host):
    # Freigeschaltet sein muss er trotzdem: ein Loopback-Aufruf mit FREMDER
    # node_id (fremd aus Sicht dieses Daemons) durchläuft seit Befund 4 die
    # reguläre Approval-Prüfung — „127.0.0.1" heißt auf einer Maschine mit
    # mehreren Knoten gerade nicht „derselbe Knoten".
    conn = job_db.connect()
    try:
        job_db.set_node_approval(conn, "n-mac", "approved")
    finally:
        conn.close()
    r = host.post("/-/worker/n-mac/disconnect",
                  headers={"X-Bibi-Node-Id": "n-mac"})
    assert r.status_code == 200
    assert r.json()["removed"] is True


def test_a_node_may_not_deregister_another(host):
    # „approved" heißt „darf mitarbeiten", nicht „darf über fremde Einträge
    # verfügen". Ohne diese Schranke könnte jeder approvte Knoten den
    # Nodes-Screen leerräumen.
    conn = job_db.connect()
    try:
        job_db.set_node_approval(conn, "n-fremd", "approved")
    finally:
        conn.close()
    r = host.post("/-/worker/n-mac/disconnect",
                  headers={"X-Bibi-Node-Id": "n-fremd"})
    assert r.status_code == 403
    assert host.get("/-/worker").json() != []   # unangetastet


def test_disconnect_needs_approval_from_a_remote_node(team_repo: Path):
    # Die Approval-Prüfung davor: ein nicht freigeschalteter Knoten kommt gar
    # nicht erst an die Registry. Über einen echten Remote-Peer geprüft —
    # „testclient" gilt als lokal (s. _LOCAL_CLIENT_HOSTS).
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app, client=("10.0.0.9", 5000)) as c:
        c.post("/-/worker", json={"worker": "neu", "host": "h", "node_id": "n-neu"})
        r = c.post("/-/worker/n-neu/disconnect",
                   headers={"X-Bibi-Node-Id": "n-neu"})
    assert r.status_code == 403
    assert "not approved" in r.json()["detail"]


def test_an_approved_remote_node_may_deregister_itself(team_repo: Path):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app, client=("10.0.0.9", 5000)) as c:
        c.post("/-/worker", json={"worker": "neu", "host": "h", "node_id": "n-neu"})
        conn = job_db.connect()
        try:
            job_db.set_node_approval(conn, "n-neu", "approved")
        finally:
            conn.close()
        r = c.post("/-/worker/n-neu/disconnect",
                   headers={"X-Bibi-Node-Id": "n-neu"})
        assert r.status_code == 200
        assert r.json()["removed"] is True
        assert c.get("/-/worker").json() == []


# ── Der Client meldet sich beim Beenden ab ──────────────────────────────────


class _FakeClient:
    def __init__(self, ok: bool = True) -> None:
        self.registered: list = []
        self.deregistered: list = []
        self._ok = ok

    def register(self, worker, host, git_status=None, **kw):
        self.registered.append(worker)
        return {}

    def deregister(self, node_id: str) -> bool:
        self.deregistered.append(node_id)
        return self._ok


def test_heartbeat_stop_deregisters(team_repo: Path):
    # Heartbeat.stop() beendete bisher nur die eigene Schleife und sagte dem
    # Host nichts — das war die Lücke.
    c = _FakeClient()
    hb = Heartbeat(client=c, worker_name="w1", repo_root=team_repo, interval=60)

    async def _go():
        await hb.start()
        await hb.stop()

    asyncio.run(_go())
    assert c.deregistered == [config.node_id()]


def test_heartbeat_stop_survives_an_unreachable_host(team_repo: Path):
    class _Broken(_FakeClient):
        def deregister(self, node_id: str) -> bool:
            raise OSError("Netz weg")

    hb = Heartbeat(client=_Broken(), worker_name="w1", repo_root=team_repo,
                   interval=60)

    async def _go():
        await hb.start()
        await hb.stop()   # darf nicht werfen

    asyncio.run(_go())


def test_heartbeat_stop_tolerates_a_client_without_deregister(team_repo: Path):
    class _Old:
        def register(self, *a, **kw):
            return {}

    hb = Heartbeat(client=_Old(), worker_name="w1", repo_root=team_repo, interval=60)

    async def _go():
        await hb.start()
        await hb.stop()

    asyncio.run(_go())


def test_deregister_sends_the_node_id_header():
    # Ohne den Header antwortet die Route mit 403 — der Header IST der Nachweis,
    # dass ein Knoten sich selbst abmeldet.
    from bibi.daemon.scheduler_client import RemoteScheduler
    seen: dict = {}

    rs = RemoteScheduler("http://host:8780")

    def _fake_post(path, payload, *, extra_headers=None):
        seen["path"] = path
        seen["headers"] = extra_headers
        return 200, {}

    rs._post = _fake_post
    assert rs.deregister("n-mac") is True
    assert seen["path"] == "/-/worker/n-mac/disconnect"
    assert seen["headers"] == {"X-Bibi-Node-Id": "n-mac"}


def test_deregister_never_raises():
    from bibi.daemon.scheduler_client import RemoteScheduler
    rs = RemoteScheduler("http://host-gibt-es-nicht.invalid:8780", timeout=0.5)
    assert rs.deregister("n-mac") is False


def test_local_scheduler_deregister_is_a_noop():
    from bibi.daemon.scheduler_client import LocalScheduler
    assert LocalScheduler().deregister("n1") is False
