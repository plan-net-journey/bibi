"""Feed als Home + Ops-Bedienelemente auf dem Feed-Screen.

RESCAN, MAINT-Toggle (spiegelt status.maintenance) und FOLLOW (pausiert Live)
liegen auf der Home (Feed); das Dashboard bleibt URL-erreichbar (/-/ui/dashboard)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── pure Renderer ─────────────────────────────────────────────────────────────


def test_feed_page_has_ops_handles():
    html = render.feed_page([], jobs=[], now=1.0)
    assert 'id="rescan"' in html and "RESCAN" in html
    assert 'id="maint"' in html and 'id="follow"' in html
    # plain-JS gegen die JSON-API
    assert "/-/rescan" in html and "/-/maintenance" in html
    assert "bibiToggleFollow" in html  # FOLLOW-Toggle (window.bibiFollow)


def test_feed_maint_reflects_status():
    on = render.feed_page([], jobs=[], status={"maintenance": True}, now=1.0)
    assert "MAINT: AN" in on and "handle warn" in on
    off = render.feed_page([], jobs=[], status={"maintenance": False}, now=1.0)
    assert "MAINT: aus" in off


def test_feed_follow_gates_live():
    html = render.feed_page([], jobs=[], now=1.0)
    assert "window.bibiFollow === false" in html  # Live-Append + Band-Poll gegated


# ── Routen ────────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, *, journal=None, jobs=None, status=None,
                 schedules=None) -> None:
        self._journal = journal or []
        self._jobs = jobs or []
        self._status = status or {}
        self._schedules = schedules or []

    def journal(self, *, slug=None, host=None):
        return self._journal

    def jobs(self, *, status=None):
        return self._jobs

    def status(self):
        return self._status

    def schedules(self):
        return self._schedules


def test_root_is_feed_with_handles(team_repo: Path):
    client = FakeClient(journal=[], jobs=[], status={"maintenance": False})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert 'id="feed"' in r.text and "RESCAN" in r.text


def test_dashboard_still_reachable(team_repo: Path):
    status = {"verdict": {"ok": True, "problems": 0, "overdue": 0,
                          "deviations": [], "overdue_jobs": []}}
    client = FakeClient(status=status, schedules=[])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/dashboard")
        assert r.status_code == 200 and "alles lief" in r.text
