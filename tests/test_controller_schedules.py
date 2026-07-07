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
           next_fire_at=None, last_run_at=100.0, trigger="now", oneshot=False,
           payload="echo hi", app_port=None, active=True) -> dict:
    return {"slug": slug, "kind": kind, "trigger": trigger,
            "next_fire_at": next_fire_at, "last_status": last_status,
            "last_run_at": last_run_at, "row_status": row_status or last_status,
            "oneshot": oneshot, "payload": payload, "app_port": app_port,
            "active": active}


# ── PLAN-14 Stufe 14.6 — Registrierungs-Drei-Gruppen (Aktiv/Inaktiv/Journal) ──


def test_schedule_list_groups_by_active_flag():
    items = [_sched("a", active=True), _sched("b", active=False),
             _sched("c", active=None)]
    html = render.schedule_list(items, now=1000.0)
    assert 'href="/-/ui/schedule/a"' in html.split("Inaktiv")[0]
    assert "Inaktiv" in html and 'href="/-/ui/schedule/b"' in html.split("Inaktiv")[1].split("Journal")[0]
    assert "Journal" in html and 'href="/-/ui/schedule/c"' in html.split("Journal")[1]


def test_schedule_list_no_inactive_or_journal_heading_when_all_active():
    html = render.schedule_list([_sched("a", active=True)], now=1000.0)
    assert "Inaktiv" not in html and "Journal —" not in html


def test_schedule_list_default_active_true_when_key_missing():
    # Rückwärtskompatibel: Items ohne "active"-Key (ältere Fixtures/Fake-Clients)
    # landen in "Aktiv", nicht stillschweigend in einer anderen Gruppe.
    item = _sched("a")
    del item["active"]
    html = render.schedule_list([item], now=1000.0)
    assert "Inaktiv" not in html and "Journal —" not in html
    assert 'href="/-/ui/schedule/a"' in html


# ── Filter (pure) ─────────────────────────────────────────────────────────────


def test_filter_by_typ():
    s = [_sched("a", kind="job"), _sched("b", kind="job")]
    out = render.filter_schedules(s, typ="job", now=1000.0)
    assert [x["slug"] for x in out] == ["a", "b"]


def test_filter_by_typ_app_uses_app_port_not_dead_kind_column():
    # kind ist seit PLAN-10 (Unified Job Model) immer "job" — Typ "app"/"claude"
    # muss aus payload/app_port abgeleitet werden, sonst verschwindet jede
    # App/Claude-Schedule aus der gefilterten Liste (kind matcht nie).
    s = [_sched("plain", payload="echo hi"),
         _sched("hitl", payload="python3 hitl_test_app.py", app_port=9100),
         _sched("ai", payload="claude: tu was")]
    assert [x["slug"] for x in render.filter_schedules(s, typ="app", now=1.0)] == ["hitl"]
    assert [x["slug"] for x in render.filter_schedules(s, typ="claude", now=1.0)] == ["ai"]
    assert [x["slug"] for x in render.filter_schedules(s, typ="job", now=1.0)] == ["plain"]


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


# ── Lauf-Historie-Chart (PLAN-21 Befund 11, pure) ─────────────────────────────


def _trans(job_id: str, to_status: str, ts: float, *, slug: str = "a",
          from_status: str | None = "pending") -> dict:
    return {"job_id": job_id, "slug": slug, "from_status": from_status,
            "to_status": to_status, "ts": ts}


def test_timeseries_buckets_counts_current_status_per_hour():
    now = 100_000.0
    transitions = [
        _trans("j1", "running", now - 23 * 3600 - 1),  # kurz vor dem Fenster
    ]
    buckets = render._timeseries_buckets(transitions, now=now, hours=24)
    assert len(buckets) == 24
    # j1 lief schon vor dem ersten Bucket-Ende → zählt in JEDEM Bucket mit.
    assert all(b["running"] == 1 for b in buckets)
    assert all(b["waiting"] == 0 and b["halt"] == 0 for b in buckets)


def test_timeseries_buckets_status_change_moves_job_between_groups():
    now = 100_000.0
    transitions = [
        _trans("j1", "running", now - 10 * 3600),
        _trans("j1", "awaiting", now - 5 * 3600),  # waiting ab hier
    ]
    buckets = render._timeseries_buckets(transitions, now=now, hours=24)
    # 9h vor "jetzt" (Bucket-Index 24-9=15, 0-basiert): noch running.
    assert buckets[14]["running"] == 1
    # 4h vor "jetzt": schon awaiting (waiting-Gruppe).
    assert buckets[19]["waiting"] == 1
    assert buckets[19]["running"] == 0


def test_timeseries_buckets_complete_hides_job():
    now = 100_000.0
    transitions = [
        _trans("j1", "running", now - 10 * 3600),
        _trans("j1", "complete", now - 5 * 3600),
    ]
    buckets = render._timeseries_buckets(transitions, now=now, hours=24)
    assert buckets[19]["running"] == 0 and buckets[19]["waiting"] == 0 and buckets[19]["halt"] == 0


def test_timeseries_buckets_future_job_not_yet_counted():
    now = 100_000.0
    buckets = render._timeseries_buckets(
        [_trans("j1", "running", now)], now=now, hours=24)
    assert buckets[0]["running"] == 0  # existiert erst am rechten Rand
    assert buckets[-1]["running"] == 1


def test_timeseries_html_has_24_cols_and_axis_labels():
    buckets = [{"waiting": 0, "running": 0, "halt": 0}] * 24
    html = render._timeseries_html(buckets)
    assert html.count('class="chart-col"') == 24
    assert "vor 24h" in html and "jetzt" in html


def test_timeseries_html_empty_buckets_shows_placeholder():
    assert "keine Daten" in render._timeseries_html([])


def test_timeseries_html_scales_segments_to_bucket_maximum():
    buckets = [{"waiting": 0, "running": 2, "halt": 0},
               {"waiting": 0, "running": 4, "halt": 0}]
    html = render._timeseries_html(buckets)
    assert "height:50.0%" in html  # 2/4
    assert "height:100.0%" in html  # 4/4


def test_job_stats_grid_shows_all_nine_states_and_uptime_counter():
    counts = {"pending": 2, "failed": 0, "deferred": 1, "awaiting": 0,
             "running": 3, "error": 1, "inactive": 0, "zombie": 0, "killed": 0}
    html = render._job_stats_grid(counts, running_since_uptime=7)
    for status in ("pending", "failed", "deferred", "awaiting",
                  "error", "inactive", "zombie", "killed"):
        assert status in html
    assert "jsg-big\">3<" in html  # running gross in der Mitte
    assert "7 seit Start" in html


def test_job_stats_grid_defaults_missing_status_to_zero():
    html = render._job_stats_grid({}, running_since_uptime=0)
    assert "jsg-big\">0<" in html


def test_timeseries_fragment_is_self_polling_own_target():
    frag = render.timeseries_fragment([], {"counts": {}, "running_since_uptime": 0}, now=1.0)
    assert 'id="timeseries"' in frag
    assert 'hx-get="/-/ui/schedules/timeseries"' in frag
    assert "every 2s" in frag


def test_schedules_page_includes_timeseries_fragment():
    html = render.schedules_page(
        [_sched("daily")], now=300.0,
        daemon_status={"job_stats": {"counts": {"running": 1}, "running_since_uptime": 2}},
        transitions=[_trans("j1", "running", 250.0)])
    assert 'id="timeseries"' in html
    assert "2 seit Start" in html


# ── Routen ────────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, schedules: list[dict], *, status: dict | None = None,
                transitions: list[dict] | None = None) -> None:
        self._s = schedules
        self._status = status or {}
        self._transitions = transitions or []

    def schedules(self) -> list[dict]:
        return self._s

    def status(self) -> dict:
        return self._status

    def transitions(self, *, since: float | None = None) -> list[dict]:
        return self._transitions


def test_ui_schedules_screen_route(team_repo: Path):
    client = FakeClient([_sched("daily", last_status="complete")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'name="status"' in r.text and "daily" in r.text


def test_ui_schedules_screen_route_has_rescan_and_reflects_maintenance(team_repo: Path):
    # User-Feedback 2026-07-03: RESCAN + MAINT auch auf dem Schedules-Screen.
    client = FakeClient([], status={"maintenance": True})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="rescan"' in r.text
        assert "MAINT: AN" in r.text


def test_ui_schedules_list_filters_problem(team_repo: Path):
    client = FakeClient([_sched("fine", last_status="complete"),
                         _sched("broken", last_status="failed")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/list", params={"status": "problem"})
        assert r.status_code == 200
        assert "broken" in r.text
        assert "fine" not in r.text.replace("/-/ui/schedule/", "")


def test_ui_schedules_screen_includes_timeseries(team_repo: Path):
    client = FakeClient(
        [], status={"job_stats": {"counts": {"running": 2}, "running_since_uptime": 5}},
        transitions=[{"job_id": "j1", "slug": "a", "from_status": "pending",
                     "to_status": "running", "ts": 1.0}])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="timeseries"' in r.text
        assert "5 seit Start" in r.text


def test_ui_schedules_timeseries_fragment_route(team_repo: Path):
    client = FakeClient(
        [], status={"job_stats": {"counts": {"running": 1}, "running_since_uptime": 1}},
        transitions=[{"job_id": "j1", "slug": "a", "from_status": "pending",
                     "to_status": "running", "ts": 1.0}])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/timeseries")
        assert r.status_code == 200
        assert 'id="timeseries"' in r.text
        assert "1 seit Start" in r.text


def test_ui_schedules_screen_survives_transitions_fetch_failure(team_repo: Path):
    # /-/transitions ist scheduler-gated (501 ohne Rolle) — der Screen darf
    # trotzdem laden, nur ohne Chart-Daten (§2.7, wie schedules()/status()).
    class BoomClient(FakeClient):
        def transitions(self, *, since=None):
            raise RuntimeError("501")

    client = BoomClient([_sched("daily")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert "daily" in r.text
