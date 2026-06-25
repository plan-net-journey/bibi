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


def test_maintenance_toggle(sync_app):
    client, _ = sync_app
    assert client.post("/-/maintenance").json() == {"maintenance": True}
    assert state.get_maintenance() is True
    assert client.delete("/-/maintenance").json() == {"maintenance": False}
    assert state.get_maintenance() is False


def test_rescan_is_stub(sync_app):
    client, _ = sync_app
    body = client.post("/-/rescan").json()
    assert body["rescanned"] is False


def test_synchronizer_toggle(sync_app):
    client, fake = sync_app
    client.post("/-/synchronizer/push")
    assert fake.push is True
    client.delete("/-/synchronizer/pull")
    assert fake.pull is False


def test_schedule_lists_at_mds(sync_app, team_repo: Path):
    (team_repo / "vault" / "case" / "job1.md").write_text(
        "---\nat: 2026-07-01 09:00\n---\nrun me\n", encoding="utf-8"
    )
    client, _ = sync_app
    items = client.get("/-/schedule").json()["schedules"]
    assert any(s["name"] == "job1" and "2026-07-01" in s["trigger"] for s in items)


def test_no_synchronizer_role(team_repo):
    app = create_app(roles.resolve(set()))  # idle daemon
    with TestClient(app) as client:
        assert client.get("/-/health").json()["roles"] == []
        assert "synchronizer" not in client.get("/-/status").json()
        # Toggle-Endpunkte existieren nicht ohne Synchronizer-Rolle.
        assert client.post("/-/synchronizer/push").status_code == 404
