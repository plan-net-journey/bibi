"""Connected-Clients-Screen (Host, Bibi4-Iteration) — Backend (WorkerRegistry,
/-/worker) existierte schon lange, hier nur die erste Darstellung dafür."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── _clients_table()/clients_fragment()/clients_page() (rein) ──────────────


def test_clients_table_empty_state():
    html = render._clients_table([], now=100)
    assert "keine verbundenen Clients" in html


def test_clients_table_renders_worker_row():
    workers = [{
        "worker": "air2024", "host": "mac", "git_user": "m.rau",
        "git_status": "trunk · clean · synced", "stale": False,
        "connected_at": 0, "last_heartbeat": 90,
    }]
    html = render._clients_table(workers, now=100)
    assert "air2024" in html and "mac" in html and "m.rau" in html
    assert "trunk · clean · synced" in html
    assert "connected" in html and "disconnected" not in html


def test_clients_table_shows_disconnected_chip_when_stale():
    workers = [{"worker": "gone", "host": "h", "stale": True,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=1000)
    assert "disconnected" in html


def test_clients_table_handles_missing_git_user_gracefully():
    # Älterer Client (vor dem node_id/git_user-Ausbau) heartbeatet ohne
    # git_user — darf die Zeile nicht crashen lassen, nur "—" zeigen.
    workers = [{"worker": "old", "host": "h", "stale": False,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "old" in html


def test_clients_fragment_has_self_poll_attrs():
    html = render.clients_fragment([], now=0)
    assert 'id="clientsboard"' in html
    assert 'hx-get="/-/ui/clients/board"' in html


def test_clients_page_includes_header_and_table():
    html = render.clients_page([{"worker": "w1", "host": "h1", "stale": False,
                                "connected_at": 0, "last_heartbeat": 0}], now=0)
    assert "<header>" in html
    assert "w1" in html
    assert "bibi · Clients" in html


# ── Controller-Route /-/ui/clients (+/board) ────────────────────────────────
# Ein fake ControllerClient statt der echten /-/worker-Registry-Anmeldung —
# _status() macht sonst einen echten HTTP-Selbstaufruf gegen daemon_port(),
# den TestClient (ASGI-Transport, kein echter Socket) nicht bedienen kann.
# Die Registry selbst (node_id-Rekeying etc.) ist schon in test_connect.py
# unit-getestet — hier geht es nur um die Rendering-Verdrahtung der Route.


class _FakeClient:
    def __init__(self, *, workers: list[dict] | None = None) -> None:
        self._workers = workers or []

    def status(self) -> dict:
        return {"roles": ["scheduler", "controller"], "workers": self._workers}

    def schedules(self) -> list[dict]:
        return []

    def landings(self, *, since: float | None = None) -> list[dict]:
        return []


def test_clients_screen_route_renders_registered_worker(team_repo: Path):
    client = _FakeClient(workers=[{
        "worker": "air2024", "host": "mac", "git_user": "m.rau",
        "git_status": "trunk", "stale": False,
        "connected_at": 0, "last_heartbeat": 0,
    }])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/clients")
    assert r.status_code == 200
    assert "air2024" in r.text and "m.rau" in r.text


def test_clients_board_fragment_route(team_repo: Path):
    client = _FakeClient(workers=[{"worker": "w1", "host": "h1", "stale": False,
                                   "connected_at": 0, "last_heartbeat": 0}])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/clients/board")
    assert r.status_code == 200
    assert "w1" in r.text
    assert 'id="clientsboard"' in r.text


def test_clients_screen_route_empty_without_any_registered_worker(team_repo: Path):
    app = create_app(roles.resolve({"controller"}), controller_client=_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/clients")
    assert r.status_code == 200
    assert "keine verbundenen Clients" in r.text
