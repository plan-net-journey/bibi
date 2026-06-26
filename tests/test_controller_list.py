"""Stufe 4.4 — Volle Schedule-Liste + Archiv (PLAN-4 §4.4, Ebene 2).

Aufklappbar (nicht primär), Quick-Spalten slug/status/last/next; abgelaufene
One-shots (at:) ins **Archiv** (zusammengeklappt, MD bleibt — A15)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


def _sched(slug, *, kind="job", trigger="now", last_status="pending",
           last_run_at=None, next_fire_at=None, oneshot=False) -> dict:
    return {"slug": slug, "kind": kind, "trigger": trigger,
            "last_status": last_status, "last_run_at": last_run_at,
            "next_fire_at": next_fire_at, "oneshot": oneshot}


def test_schedule_list_empty():
    assert "keine Schedules" in render.schedule_list([])


def test_schedule_list_active_rows_and_links():
    items = [_sched("nightly", trigger="0 9 * * *", last_status="complete",
                    last_run_at=100.0, next_fire_at=200.0)]
    html = render.schedule_list(items, now=300.0)
    assert "Alle Schedules" in html
    assert 'href="/-/ui/schedule/nightly"' in html
    assert "complete" in html


def test_schedule_list_archives_expired_oneshots():
    items = [
        _sched("recurring", trigger="0 9 * * *", last_status="pending"),
        _sched("done-oneshot", trigger="2026-06-26T20:00:00", oneshot=True,
               last_status="complete", last_run_at=100.0),
    ]
    html = render.schedule_list(items, now=300.0)
    # aktives recurring oben; abgelaufener One-shot im Archiv-<details>
    assert "recurring" in html
    assert "Archiv" in html
    assert "done-oneshot" in html
    # Archiv ist eingeklappt (details ohne open)
    archive = html.split("Archiv", 1)[1]
    assert "done-oneshot" in archive


def test_schedule_list_pending_oneshot_stays_active():
    # Ein noch nicht gefeuerter One-shot ist NICHT archiviert (steht bevor).
    items = [_sched("future", trigger="2026-06-27T09:00:00", oneshot=True,
                    last_status="pending", next_fire_at=999.0)]
    html = render.schedule_list(items, now=300.0)
    # vor dem Archiv-Abschnitt sichtbar (oder gar kein Archiv)
    head = html.split("Archiv", 1)[0]
    assert "future" in head


def test_schedule_list_next_is_future_worded():
    # „nächster" in der Zukunft → „in …"; Vergangenheit → „—" (kein „vor").
    future = render.schedule_list(
        [_sched("soon", trigger="0 9 * * *", next_fire_at=360.0)], now=300.0)
    assert "in 1 min" in future
    past = render.schedule_list(
        [_sched("done", trigger="0 9 * * *", last_status="complete",
                next_fire_at=100.0)], now=300.0)
    # in der nächster-Spalte kein „vor …" mehr
    assert "vor 3 min" not in past.split("nächster", 1)[1]


def test_schedule_list_escapes_slug():
    html = render.schedule_list([_sched("<x>", oneshot=False)])
    assert "<x>" not in html.replace("/-/ui/schedule/", "")
    assert "&lt;x&gt;" in html


def test_dashboard_page_includes_schedule_list():
    status = {"verdict": {"ok": True, "problems": 0, "overdue": 0,
                          "deviations": [], "overdue_jobs": []}}
    html = render.dashboard_page(status, [_sched("daily", last_status="complete")])
    assert "alles lief" in html
    assert "Alle Schedules" in html
    assert "daily" in html


# ── Route ────────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, status: dict, schedules: list[dict]) -> None:
        self._status, self._schedules = status, schedules

    def status(self) -> dict:
        return self._status

    def schedules(self) -> list[dict]:
        return self._schedules


def test_root_html_lists_schedules(team_repo: Path):
    status = {"verdict": {"ok": True, "problems": 0, "overdue": 0,
                          "deviations": [], "overdue_jobs": []}}
    client = FakeClient(status, [_sched("daily", last_status="complete")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert "Alle Schedules" in r.text
        assert 'href="/-/ui/schedule/daily"' in r.text
