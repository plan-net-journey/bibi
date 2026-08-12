"""Daemon-Screen (PLAN-17 Stufe 17.0): Status-Kacheln + geteiltes Live-Log.

User-Feedback 2026-07-05: additiv neben dem bestehenden Live-Log-Screen, nicht
ersetzend — reuse desselben Log-Panel-Bausteins (``_log_panel()``), damit
Filter/FOLLOW-Verhalten nicht doppelt gepflegt werden.

PLAN-18 Stufe 18.4 (2026-07-06): die eigene Seite/der Nav-Tab sind wieder
zurückgebaut (Inhalt lebt jetzt im Feed-Header) — ``daemon_page()``/
``_status_cards()`` bleiben aber als Render-Bausteine bestehen und werden
hier weiterhin pur getestet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── reine Render-Funktionen ──────────────────────────────────────────────────


def test_log_page_unchanged_after_refactor():
    # Regressionsschutz für die _log_panel()-Extraktion: log_page() muss exakt
    # dasselbe HTML liefern wie vorher.
    html = render.log_page(daemon_status={"maintenance": False})
    assert 'id="log" class="logbox"' in html
    assert 'id="lvl"' in html and 'id="q"' in html
    assert render._LOG_JS in html


def test_screen_nav_no_longer_includes_daemon_tab():
    # PLAN-18 Stufe 18.4: Daemon-Tab entfernt, Inhalt lebt im Feed-Header.
    html = render._screen_nav("Log")
    assert 'href="/-/ui/daemon"' not in html and "Daemon" not in html


# ── Route (gefakter Client) ──────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, status: dict) -> None:
        self._status = status

    def status(self) -> dict:
        return self._status

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(status: dict):
        client = _FakeClient(status)
        return create_app(roles.resolve({"controller"}), controller_client=client)
    return _make


def test_daemon_route_retired(app_with):
    # PLAN-18 Stufe 18.4: /-/ui/daemon ist zurückgebaut — Status-Kacheln +
    # Git-Segment leben jetzt im Feed-Header (/-/), s. test_controller_feed.py.
    app = app_with({"roles": ["connect"], "connect": {"ok": True, "last_at": 90.0},
                    "auto_sync": True, "maintenance": False})
    with TestClient(app) as c:
        assert c.get("/-/ui/daemon").status_code == 404


def test_htmx_served_locally(app_with):
    # PLAN-36 Stufe 36.0 (Befund 3, FE-Live-Update-Briefing): htmx kommt vom
    # eigenen Daemon statt von unpkg.com — Tailnet-only darf nie vom Internet
    # abhängen. Versionierter Pfad => aggressives, immutables Caching erlaubt.
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        r = c.get("/-/static/htmx-1.9.12.min.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert "immutable" in r.headers["cache-control"]
        assert "htmx" in r.text[:2000]
        # und die Seiten referenzieren den lokalen Pfad, nicht mehr das CDN
        home = c.get("/-/", headers={"Accept": "text/html"})
        assert "unpkg.com" not in home.text
        assert "/-/static/htmx-1.9.12.min.js" in home.text


