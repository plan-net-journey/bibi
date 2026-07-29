"""Live-Log-Panel (PLAN-5 §5.4 Slice C) — render + Route."""

from __future__ import annotations

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


class _Dummy:
    def status(self): return {}
    def schedules(self): return []
    def jobs(self): return []
    def journal(self, **_): return []


def test_log_page_has_eventsource_and_filters():
    html = render.log_page()
    assert "<!DOCTYPE html>" in html
    assert "new EventSource('/-/log/stream" in html      # Live-Quelle
    assert 'id="log"' in html                            # Container
    assert 'id="lvl"' in html and 'id="q"' in html       # Level- + Text-Filter
    assert 'href="/-/"' in html                           # Nav (Stufe 6, Home = Schedules)


def test_logs_route_serves_panel():
    app = create_app(roles.resolve({"controller"}), controller_client=_Dummy())
    c = TestClient(app)
    r = c.get("/-/ui/logs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "EventSource('/-/log/stream" in r.text and 'id="log"' in r.text
