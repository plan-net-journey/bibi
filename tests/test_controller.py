"""Controller-Web-App auf ``/-/`` (PLAN-4 §2.1/§4.0, Stufe 4.0 — Skelett).

Die App-Wurzel *ist* der Steuer-Namensraum ``/-/`` mit Content-Negotiation:
Browser (``Accept: text/html``) → HTML-App; Nicht-Browser → knapper
JSON-Service-Deskriptor (System-Info + App-Link), §1.1 auch an der Wurzel gewahrt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app


@pytest.fixture
def ctrl(team_repo: Path):
    app = create_app(roles.resolve({"controller"}))
    with TestClient(app) as client:
        yield client


def test_controller_role_resolves():
    r = roles.resolve({"controller"})
    assert r.controller is True
    assert "controller" in r.active_names()
    assert r.controller is True and "controller" in roles.KNOWN_ROLES


def test_root_serves_html_for_browser(ctrl):
    r = ctrl.get("/-/", headers={"Accept": "text/html,application/xhtml+xml,*/*"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text.lower()
    assert "<!doctype html>" in body
    assert "htmx" in body  # htmx geladen (PLAN-4 §2.1)


def test_root_serves_json_descriptor_for_client(ctrl):
    # Explizit JSON → knapper Service-Deskriptor, kein Markup (§1.1).
    r = ctrl.get("/-/", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["service"] == "bibi"
    assert "app" in body          # App-Link
    assert "contract" in body     # /-/openapi.json-Vertragsversion
    assert "controller" in body["roles"]


def test_root_default_curl_is_json(ctrl):
    # Default-curl (Accept: */*, kein text/html) → Deskriptor, nicht HTML.
    r = ctrl.get("/-/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["service"] == "bibi"


def test_root_absent_without_controller_role(team_repo):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as client:
        assert client.get("/-/", headers={"Accept": "text/html"}).status_code == 404
