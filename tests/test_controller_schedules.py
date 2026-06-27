"""Stufe 3 — Schedules-Screen mit Filter (Frontend-Plan §C.3).

Eine Liste statt „Abweichungen + Schedules": Filter (Typ job/claude/app + Status
inkl. Gruppe „Problem") ersetzen den Split. Dedizierter Screen ``/-/ui/schedules``
(Seite) + filter-fähiges Fragment ``/-/ui/schedules/list`` (auch Self-Poll-Ziel)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


def _sched(slug: str, *, kind="job", last_status="complete", row_status=None,
           next_fire_at=None, last_run_at=100.0, trigger="now", oneshot=False) -> dict:
    return {"slug": slug, "kind": kind, "trigger": trigger,
            "next_fire_at": next_fire_at, "last_status": last_status,
            "last_run_at": last_run_at, "row_status": row_status or last_status,
            "oneshot": oneshot}


# ── Filter (pure) ─────────────────────────────────────────────────────────────


def test_filter_by_typ():
    s = [_sched("a", kind="job"), _sched("b", kind="claude")]
    out = render.filter_schedules(s, typ="claude", now=1000.0)
    assert [x["slug"] for x in out] == ["b"]


def test_filter_by_status():
    s = [_sched("a", last_status="complete"), _sched("b", last_status="failed")]
    out = render.filter_schedules(s, status="failed", now=1000.0)
    assert [x["slug"] for x in out] == ["b"]


def test_filter_problem_group_includes_failed_and_overdue():
    s = [_sched("fine", last_status="complete"),
         _sched("broken", last_status="killed"),
         _sched("late", last_status="pending", row_status="pending", next_fire_at=500.0)]
    out = render.filter_schedules(s, status="problem", now=1000.0)
    assert {x["slug"] for x in out} == {"broken", "late"}  # killed + überfällig


def test_filter_alle_passthrough():
    s = [_sched("a"), _sched("b")]
    assert len(render.filter_schedules(s, typ="alle", status="alle", now=1.0)) == 2
    assert len(render.filter_schedules(s, now=1.0)) == 2


# ── Screen + Fragment (pure) ──────────────────────────────────────────────────


def test_schedules_page_has_filter_and_nav():
    html = render.schedules_page([_sched("daily")], now=300.0)
    assert html.lower().startswith("<!doctype html>")
    assert 'name="typ"' in html and 'name="status"' in html
    assert "/-/ui/schedules/list" in html      # Filter-Ziel + Self-Poll
    assert 'id="schedules"' in html and "daily" in html
    assert "Live-Log" in html


def test_schedules_fragment_polls_list_with_filter():
    frag = render.schedules_fragment([_sched("a")], now=1.0, typ="job", status="problem")
    assert 'hx-get="/-/ui/schedules/list?typ=job&status=problem"' in frag


def test_sched_table_column_header_combined():
    assert "letzter / seit" in render.schedule_list([_sched("a")], now=300.0)


# ── Routen ────────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, schedules: list[dict]) -> None:
        self._s = schedules

    def schedules(self) -> list[dict]:
        return self._s

    def status(self) -> dict:
        return {}


def test_ui_schedules_screen_route(team_repo: Path):
    client = FakeClient([_sched("daily", last_status="complete")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'name="status"' in r.text and "daily" in r.text


def test_ui_schedules_list_filters_problem(team_repo: Path):
    client = FakeClient([_sched("fine", last_status="complete"),
                         _sched("broken", last_status="failed")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/list", params={"status": "problem"})
        assert r.status_code == 200
        assert "broken" in r.text
        assert "fine" not in r.text.replace("/-/ui/schedule/", "")
