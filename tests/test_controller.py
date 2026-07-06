"""Controller-Web-App auf ``/-/`` (PLAN-4 §2.1/§4.0/§4.1).

Die App-Wurzel *ist* der Steuer-Namensraum ``/-/`` mit Content-Negotiation:
Browser → HTML-App (server-seitig gerendert via :class:`ControllerClient`);
Nicht-Browser → JSON-Service-Deskriptor. Der Client wird in den Tests gefakt,
damit kein echter HTTP-Selbstaufruf nötig ist.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app


class FakeClient:
    def __init__(self, status: dict) -> None:
        self._status = status

    def status(self) -> dict:
        return self._status


_GREEN = {"roles": ["scheduler", "controller"], "verdict": {
    "ok": True, "problems": 0, "overdue": 0, "deviations": [], "overdue_jobs": []}}


@pytest.fixture
def ctrl(team_repo: Path):
    app = create_app(roles.resolve({"controller"}), controller_client=FakeClient(_GREEN))
    with TestClient(app) as client:
        yield client


def test_controller_role_resolves():
    r = roles.resolve({"controller"})
    assert r.controller is True
    assert "controller" in r.active_names()
    assert "controller" in roles.KNOWN_ROLES


def test_root_serves_html_for_browser(ctrl):
    # Home = Feed (PLAN-18 Stufe 18.3, löst 2026-07-04 "Home = Schedules" ab).
    r = ctrl.get("/-/", headers={"Accept": "text/html,application/xhtml+xml,*/*"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "<!doctype html>" in body.lower()
    assert 'id="feedboard"' in body         # Feed-Screen
    assert "RESCAN" in body and "MAINT" in body  # Ops-Handles auf der Home


def test_root_serves_json_descriptor_for_client(ctrl):
    r = ctrl.get("/-/", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["service"] == "bibi"
    assert "app" in body
    assert "contract" in body
    assert "controller" in body["roles"]


def test_root_default_curl_is_json(ctrl):
    r = ctrl.get("/-/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["service"] == "bibi"


def test_root_degrades_when_client_unreachable(team_repo):
    class Boom:
        def status(self):
            raise ConnectionError("daemon weg")

    app = create_app(roles.resolve({"controller"}), controller_client=Boom())
    with TestClient(app) as client:
        r = client.get("/-/", headers={"Accept": "text/html"})
        assert r.status_code == 200  # defensiv: kein 500
        assert 'id="feedboard"' in r.text  # leerer Feed statt Absturz (Boom kennt kein feed())


def test_root_absent_without_controller_role(team_repo):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as client:
        assert client.get("/-/", headers={"Accept": "text/html"}).status_code == 404
