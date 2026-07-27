"""Stufe 4.4 — Volle Schedule-Liste (PLAN-4 §4.4, Ebene 2).

Quick-Spalten slug/status/last/next. Die frühere Archiv-Klapp-Logik für
abgelaufene One-shots ist mit PLAN-14 Stufe 14.6 vollständig durch das
Registrierungs-Drei-Gruppen-Modell ersetzt (Aktiv/Archive/Journal, siehe
test_controller_schedules.py). PLAN-23 Befund 2 verfeinert das nochmal: ein
abgeschlossener oneshot (`at:`) mit noch vorhandener MD landet jetzt NICHT
mehr in „Aktiv", sondern im Archive (nicht mehr erneut startbar, s. Befund
3) — anders als PLAN-14 14.6 das ursprünglich vorsah."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


def _sched(slug, *, kind="job", trigger="now", last_status="pending",
           last_run_at=None, last_run_id=None, next_fire_at=None, oneshot=False,
           payload="echo hi", app_port=None, active=True) -> dict:
    return {"slug": slug, "kind": kind, "trigger": trigger,
            "last_status": last_status, "last_run_at": last_run_at,
            "last_run_id": last_run_id, "next_fire_at": next_fire_at,
            "oneshot": oneshot, "payload": payload, "app_port": app_port,
            "active": active}


def test_schedule_list_empty():
    assert "no schedules" in render.schedule_list([])


def test_schedule_list_active_rows_and_links():
    items = [_sched("nightly", trigger="0 9 * * *", last_status="complete",
                    last_run_at=100.0, next_fire_at=200.0)]
    html = render.schedule_list(items, now=300.0)
    assert "Schedules (" in html
    assert 'href="/-/ui/schedule/nightly"' in html
    assert "complete" in html


def test_schedule_list_completed_oneshot_with_md_moves_to_archive():
    # PLAN-23 Befund 2 (ersetzt die PLAN-14-14.6-Annahme "bleibt einfach
    # aktiv"): ein complete abgeschlossener oneshot gehört jetzt ins Archive,
    # auch wenn seine MD noch da ist — nicht mehr erneut startbar (Befund 3).
    # Ein NICHT abgeschlossener recurring-Schedule bleibt unverändert aktiv.
    items = [
        _sched("recurring", trigger="0 9 * * *", last_status="pending"),
        _sched("done-oneshot", trigger="2026-06-26T20:00:00", oneshot=True,
               last_status="complete", last_run_at=100.0, active=True),
    ]
    html = render.schedule_list(items, now=300.0)
    assert "recurring" in html.split("Archive")[0]
    assert "Archive" in html and "done-oneshot" in html.split("Archive")[1]


def test_schedule_list_next_is_future_worded():
    # „nächster" in der Zukunft → „in …"; None → „—"; fällig/überfällig
    # (gesetzt, aber ≤ now) → „asap" (PLAN-23 Befund 4 — vorher identisch zu
    # None als „—" gerendert, das machte den PLAN-23-Befund-1-Bug in der UI
    # unsichtbar).
    future = render.schedule_list(
        [_sched("soon", trigger="0 9 * * *", next_fire_at=360.0)], now=300.0)
    assert "in 1 min" in future
    past = render.schedule_list(
        [_sched("done", trigger="0 9 * * *", last_status="complete",
                next_fire_at=100.0)], now=300.0)
    # in der next-Spalte kein „X min ago" mehr, sondern „asap"
    assert "3 min ago" not in past.split(">next<", 1)[1]
    assert "asap" in past.split(">next<", 1)[1]
    none_html = render.schedule_list(
        [_sched("idle", trigger="never", next_fire_at=None)], now=300.0)
    assert "asap" not in none_html.split(">next<", 1)[1]


def test_schedule_list_escapes_slug():
    html = render.schedule_list([_sched("<x>", oneshot=False)])
    assert "<x>" not in html.replace("/-/ui/schedule/", "")
    assert "&lt;x&gt;" in html


# ── PLAN-23 Befund 4 — _until() direkt (drei Zustände) ───────────────────────


def test_until_none_is_dash():
    assert render._until(None, 300.0) == "—"


def test_until_future_is_in_x():
    assert render._until(360.0, 300.0) == "in 1 min"


def test_until_past_is_asap():
    assert render._until(100.0, 300.0) == "asap"


def test_until_exactly_now_is_asap():
    # ts == now zählt als fällig, nicht als Zukunft (kein "in 0s").
    assert render._until(300.0, 300.0) == "asap"


# ── Time-Toggle (Bibi4-Iteration, User-Fund: "Time: abs./rel./both") ────────


def test_time_abs_full_includes_year():
    import datetime
    ts = datetime.datetime(2026, 7, 18, 23, 18).timestamp()
    assert render._time_abs_full(ts) == "2026-07-18 23:18"


def test_time_abs_full_none_is_dash():
    assert render._time_abs_full(None) == "—"


def test_time_toggle_cell_renders_all_three_variants():
    html = render._time_toggle_cell(100.0, 300.0, rel_fn=render._ago)
    assert '<span class="tt-abs">' in html
    assert '<span class="tt-relonly">3 min ago</span>' in html
    assert '<span class="tt-relboth"> (3 min ago)</span>' in html


def test_time_toggle_cell_none_is_bare_dash():
    # Kein Toggle-Markup für fehlende Zeitstempel — bleibt schlicht "—", wie
    # zuvor bei _abs_time(None)/_ago(None).
    assert render._time_toggle_cell(None, 300.0) == "—"


def test_time_toggle_cell_uses_until_for_next_column():
    # "next" ist zukunftsgerichtet — rel_fn=_until traegt den "asap"-Sonderfall.
    html = render._time_toggle_cell(100.0, 300.0, rel_fn=render._until)
    assert '<span class="tt-relonly">asap</span>' in html


# ── Route ────────────────────────────────────────────────────────────────────


# ── Stufe 4.x — KIND-Spalte, letzter-Lauf-Status, Self-Poll, Handles ─────────


def test_sched_row_has_kind_and_last_status():
    # kind ist seit PLAN-10 (Unified Job Model) immer "job" — die "Art"-Spalte
    # leitet den Typ aus payload/app_port ab (render._effective_sched_type).
    items = [_sched("nightly", payload="claude: tu was", last_status="complete",
                    last_run_at=100.0, next_fire_at=200.0)]
    html = render.schedule_list(items, now=300.0)
    assert '<th>Type</th>' in html
    assert '>claude<' in html
    assert 'class="st complete">complete<' in html


def test_sched_row_status_and_ago_link_to_run_detail():
    # User-Feedback 2026-07-01: Status/letzter-seit -> Lauf-Details (journal-id),
    # Schedule/nächster -> Job-Details (Schedule selbst).
    items = [_sched("nightly", trigger="0 9 * * *", last_status="complete",
                    last_run_at=100.0, last_run_id=42, next_fire_at=360.0)]
    html = render.schedule_list(items, now=300.0)
    assert 'href="/-/ui/run/42">complete<' in html
    # Time-Toggle (Bibi4-Iteration) rendert alle drei Varianten vor, "3 min ago"
    # steht deshalb jetzt in einem verschachtelten Span statt direkt im <a>.
    assert 'href="/-/ui/run/42"><span class="tt-abs">' in html
    assert '<span class="tt-relonly">3 min ago</span>' in html
    assert 'href="/-/ui/schedule/nightly">nightly<' in html
    assert 'href="/-/ui/schedule/nightly"><span class="tt-abs">' in html  # "next" verlinkt


def test_sched_row_status_and_ago_plain_without_run_id():
    # Ohne abgeschlossenen Lauf (last_run_id=None) gibt es nichts zum Verlinken.
    items = [_sched("fresh", last_status="pending")]
    html = render.schedule_list(items, now=300.0)
    assert '/-/ui/run/' not in html
    assert 'class="st pending">pending<' in html


def test_schedules_fragment_is_bus_driven():
    # PLAN-36 Stufe 36.3: Liste haengt am kollektiven Bus-Target "jobs".
    frag = render.schedules_fragment([_sched("a")], now=1.0)
    assert 'id="schedules"' in frag
    assert 'data-bus="jobs"' in frag
    assert 'data-bus-refetch="/-/ui/schedules/list"' in frag  # Fragment-Route
    assert "window.bibiFollow" not in frag


class FakeClient:
    def __init__(self, status: dict, schedules: list[dict]) -> None:
        self._status, self._schedules = status, schedules

    def status(self) -> dict:
        return self._status

    def schedules(self) -> list[dict]:
        return self._schedules


def test_ui_schedules_fragment_route(team_repo: Path):
    client = FakeClient({}, [_sched("daily", last_status="complete")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="schedules"' in r.text and "daily" in r.text
