"""Daemon-Screen (PLAN-17 Stufe 17.0): Status-Kacheln + geteiltes Live-Log.

User-Feedback 2026-07-05: additiv neben dem bestehenden Live-Log-Screen, nicht
ersetzend — reuse desselben Log-Panel-Bausteins (``_log_panel()``), damit
Filter/FOLLOW-Verhalten nicht doppelt gepflegt werden.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── reine Render-Funktionen ──────────────────────────────────────────────────


def test_uptime_label_days_hours():
    assert render._uptime_label(0, now=(2 * 86400 + 6 * 3600)) == "2 T 6 h"


def test_uptime_label_hours_minutes():
    assert render._uptime_label(0, now=(3 * 3600 + 15 * 60)) == "3 h 15 min"


def test_uptime_label_minutes_only():
    assert render._uptime_label(0, now=45 * 60) == "45 min"


def test_uptime_label_none_is_dash():
    assert render._uptime_label(None, now=100.0) == "—"


def test_status_cards_show_roles():
    html = render._status_cards({"roles": ["synchronizer", "connect"]}, now=100.0)
    assert "synchronizer, connect" in html


def test_status_cards_omit_host_connection_without_connect():
    # Kein "connect"-Key im Status ⇒ keine Rolle mit --connect ⇒ keine Kachel,
    # die einen nie stattfindenden Heartbeat suggerieren würde.
    html = render._status_cards({"roles": ["scheduler"]}, now=100.0)
    assert "Host-Verbindung" not in html


def test_status_cards_connected_shows_ok_and_last_heartbeat():
    html = render._status_cards(
        {"roles": ["connect"], "connect": {"ok": True, "last_at": 96.0}}, now=100.0)
    assert "Host-Verbindung" in html and "verbunden" in html
    assert "vor 4s" in html


def test_status_cards_disconnected_shows_bad():
    html = render._status_cards(
        {"roles": ["connect"], "connect": {"ok": False, "last_at": 90.0}}, now=100.0)
    assert "getrennt" in html and 'class="value bad"' in html


def test_status_cards_auto_sync_and_maintenance():
    html = render._status_cards(
        {"roles": [], "auto_sync": True, "maintenance": True}, now=100.0)
    assert "an" in html  # auto_sync
    assert 'class="value bad"' in html  # maintenance an ⇒ bad


def test_daemon_page_has_header_nav_status_and_log():
    html = render.daemon_page(
        {"roles": ["connect"], "connect": {"ok": True, "last_at": 90.0}}, now=100.0)
    assert 'href="/-/"' in html and 'href="/-/ui/logs"' in html
    assert 'id="liveclock"' in html and 'id="follow"' in html
    assert '<div class="statuscards">' in html
    assert 'id="log" class="logbox"' in html  # geteiltes Log-Panel


def test_daemon_page_has_rescan_and_maint():
    html = render.daemon_page({"maintenance": True}, now=100.0)
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html


def test_log_page_unchanged_after_refactor():
    # Regressionsschutz für die _log_panel()-Extraktion: log_page() muss exakt
    # dasselbe HTML liefern wie vorher.
    html = render.log_page(daemon_status={"maintenance": False})
    assert 'id="log" class="logbox"' in html
    assert 'id="lvl"' in html and 'id="q"' in html
    assert render._LOG_JS in html


def test_screen_nav_includes_daemon_tab():
    html = render._screen_nav("Live-Log")
    assert 'href="/-/ui/daemon"' in html and "Daemon" in html


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


def test_daemon_route_renders_status_and_log(app_with):
    app = app_with({"roles": ["connect"], "connect": {"ok": True, "last_at": 90.0},
                    "auto_sync": True, "maintenance": False})
    with TestClient(app) as c:
        r = c.get("/-/ui/daemon")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "Host-Verbindung" in r.text and "verbunden" in r.text
        assert 'id="log" class="logbox"' in r.text
