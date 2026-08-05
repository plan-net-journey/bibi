"""FastAPI-Skelett: /-/-Endpunkte (PLAN-2 §2.2, DESIGN §4.2/§4.8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi import state
from bibi.daemon import roles
from bibi.daemon.app import create_app


class FakeSync:
    """Minimaler Synchronizer-Stand-in für den App-Test."""

    def __init__(self) -> None:
        self.started = False
        self.pull = True
        self.push = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def status(self) -> dict:
        return {"pull": self.pull, "push": self.push}

    def set_pull(self, v: bool) -> None:
        self.pull = v

    def set_push(self, v: bool) -> None:
        self.push = v


@pytest.fixture
def sync_app(team_repo):
    fake = FakeSync()
    app = create_app(roles.resolve({"synchronizer"}), synchronizer=fake)
    with TestClient(app) as client:
        yield client, fake


def test_health(sync_app):
    client, _ = sync_app
    r = client.get("/-/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "synchronizer" in body["roles"]


def test_lifespan_starts_and_status_includes_synchronizer(sync_app):
    client, fake = sync_app
    assert fake.started is True  # lifespan lief beim TestClient-Enter
    body = client.get("/-/status").json()
    assert body["synchronizer"] == {"pull": True, "push": False}
    assert body["auto_sync"] is False
    assert body["maintenance"] is False


def test_status_describes_the_node_itself(sync_app):
    """``/-/status`` sagt, **wer** antwortet — nicht nur, wie es ihm geht.

    Ein Knoten meldet sich per Heartbeat beim Scheduler; der Scheduler meldet
    sich nirgends. Seine Zeile im Nodes-Screen entstand deshalb immer lokal —
    und war von einem anderen Knoten aus nicht zu bekommen. Genau das war der
    Grund, warum der Screen auf einem Client den Knoten nicht zeigen konnte,
    dem die Flotte gehört. Dieselben Felder, die ein Heartbeat trägt.
    """
    client, _ = sync_app
    node = client.get("/-/status").json()["node"]
    assert node["node_id"]
    assert node["worker"] == "testnode.invalid"
    assert "synchronizer" in (node["role"] or "")
    assert "engine" in node and "git_status" in node


def test_maintenance_toggle(sync_app):
    client, _ = sync_app
    assert client.post("/-/maintenance").json() == {"maintenance": True}
    assert state.get_maintenance() is True
    assert client.delete("/-/maintenance").json() == {"maintenance": False}
    assert state.get_maintenance() is False


def test_synchronizer_toggle(sync_app):
    client, fake = sync_app
    client.post("/-/synchronizer/push")
    assert fake.push is True
    client.delete("/-/synchronizer/pull")
    assert fake.pull is False


def test_no_synchronizer_role(team_repo):
    app = create_app(roles.resolve(set()))  # idle daemon
    with TestClient(app) as client:
        assert client.get("/-/health").json()["roles"] == []
        assert "synchronizer" not in client.get("/-/status").json()
        # Toggle-Endpunkte existieren nicht ohne Synchronizer-Rolle.
        assert client.post("/-/synchronizer/push").status_code == 404


class FakePinnedWorker:
    """Minimaler Worker-Stand-in für den gepinnten Zweit-Worker — PLAN-28."""

    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False


def test_pinned_worker_starts_even_without_any_role(team_repo):
    # PLAN-28: der gepinnte Zweit-Worker ist rollenunabhängig — jeder Knoten
    # hat seine eigene lokale jobs.sqlite, ein reiner Client ohne
    # scheduler/worker-Rolle braucht trotzdem Retry-Redispatch/Deferred-
    # Re-Arm für seine eigenen gepinnten /run-Läufe.
    fake = FakePinnedWorker()
    app = create_app(roles.resolve(set()), pinned_worker=fake)  # idle daemon, keine Rollen
    with TestClient(app):
        assert fake.started is True
