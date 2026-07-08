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
    assert 'href="/-/ui/schedule/a"' in html.split("Inactive")[0]
    assert "Inactive" in html and 'href="/-/ui/schedule/b"' in html.split("Inactive")[1].split("Journal")[0]
    assert "Journal" in html and 'href="/-/ui/schedule/c"' in html.split("Journal")[1]


def test_schedule_list_no_inactive_or_journal_heading_when_all_active():
    html = render.schedule_list([_sched("a", active=True)], now=1000.0)
    assert "Inactive" not in html and "Journal —" not in html


def test_schedule_list_default_active_true_when_key_missing():
    # Rückwärtskompatibel: Items ohne "active"-Key (ältere Fixtures/Fake-Clients)
    # landen in "Aktiv", nicht stillschweigend in einer anderen Gruppe.
    item = _sched("a")
    del item["active"]
    html = render.schedule_list([item], now=1000.0)
    assert "Inactive" not in html and "Journal —" not in html
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
    assert "Live Log" in html


def test_schedules_fragment_polls_list_with_filter():
    frag = render.schedules_fragment([_sched("a")], now=1.0, typ="job", status="problem")
    assert 'hx-get="/-/ui/schedules/list?typ=job&status=problem"' in frag


def test_sched_table_column_header_combined():
    assert "last / since" in render.schedule_list([_sched("a")], now=300.0)


# ── Lauf-Historie-Chart (PLAN-21 Befund 11 v2, pure) ─────────────────────────


def _landing(status: str, finished_at: float) -> dict:
    return {"status": status, "finished_at": finished_at}


def test_landings_buckets_counts_events_by_status_and_time():
    now = 100_000.0
    landings = [
        _landing("complete", now - 10 * 60),   # 10min her → letzter Bucket (15min)
        _landing("error", now - 20 * 60),      # 20min her → vorletzter Bucket
    ]
    labels, counts = render._landings_buckets(landings, now=now, bucket_minutes=15)
    assert len(labels) == 96  # Default-Fenster 24h / 15min
    assert counts["complete"][-1] == 1
    assert counts["error"][-2] == 1


def test_landings_buckets_ignores_non_terminal_or_out_of_window():
    now = 100_000.0
    landings = [
        _landing("running", now - 60),        # kein Terminal-Status → ignoriert
        _landing("complete", now - 100 * 3600),  # weit vor dem Fenster → ignoriert
    ]
    _, counts = render._landings_buckets(landings, now=now, bucket_minutes=15)
    assert sum(counts["complete"]) == 0
    assert "running" not in counts


def test_landings_buckets_resolution_window_pairs():
    # Auflösung bestimmt automatisch das Fenster (_RESOLUTION_WINDOWS) —
    # 1min-Auflösung bleibt so bei ~120 statt 1440 Buckets über 24h.
    labels_1h, _ = render._landings_buckets([], now=1_000_000.0, bucket_minutes=60)
    labels_5m, _ = render._landings_buckets([], now=1_000_000.0, bucket_minutes=5)
    labels_1m, _ = render._landings_buckets([], now=1_000_000.0, bucket_minutes=1)
    assert len(labels_1h) == 24    # 60min × 24 = 24h Fenster
    assert len(labels_5m) == 96    # 5min × 96 = 8h Fenster
    assert len(labels_1m) == 120   # 1min × 120 = 2h Fenster


def test_landings_chart_html_has_canvas_and_chartjs_init():
    labels, counts = render._landings_buckets(
        [_landing("complete", 100_000.0 - 60)], now=100_000.0, bucket_minutes=15)
    html = render._landings_chart_html(labels, counts)
    assert '<canvas id="landingsChart"' in html
    assert "new Chart(" in html
    assert "complete" in html and "#5fb37a" in html  # grün


def test_landings_chart_html_empty_shows_placeholder():
    assert "keine Daten" in render._landings_chart_html([], {})


def test_current_state_chips_only_shows_nonzero_statuses():
    # User-Fund 2026-07-08 (2. Runde): kein Stat-Grid mehr — nur Chips für
    # tatsächlich nicht-null Zustände, der Rest wird gar nicht erst gerendert.
    counts = {"pending": 2, "failed": 0, "deferred": 1, "awaiting": 0,
             "running": 3, "error": 1, "inactive": 0, "zombie": 0, "killed": 0}
    html = render._current_state_chips(counts, running_since_uptime=7)
    for shown in ("pending", "deferred", "error", "running"):
        assert shown in html
    for hidden in ("failed", "awaiting", "inactive", "zombie", "killed"):
        assert hidden not in html
    assert "3 running" in html
    assert "7 since start" in html


def test_current_state_chips_colors_match_chart_palette():
    # Kern des User-Funds: dieselbe Farbe wie im Chart macht die Legende
    # redundant — hier konkret geprüft für error/killed.
    counts = {"error": 1, "killed": 2}
    html = render._current_state_chips(counts, running_since_uptime=0)
    assert f'color:{render._LANDING_COLOR["error"]}' in html
    assert f'color:{render._LANDING_COLOR["killed"]}' in html


def test_current_state_chips_running_uses_live_color_only_when_nonzero():
    dimmed = render._current_state_chips({}, running_since_uptime=0)
    assert "<span class=\"ts-chip\">0 running</span>" in dimmed
    lit = render._current_state_chips({"running": 1}, running_since_uptime=0)
    assert f'color:{render._LIVE_COLOR}">1 running' in lit


def test_current_state_chips_empty_state_is_minimal():
    html = render._current_state_chips({}, running_since_uptime=0)
    assert "0 running" in html and "0 since start" in html
    # keine einzige Farb-Chip-Zeile für die neun Namen-Status:
    for s in ("pending", "failed", "deferred", "awaiting",
             "error", "inactive", "zombie", "killed"):
        assert s not in html


def test_timeseries_fragment_is_self_polling_own_target():
    # Eigener, langsamerer Takt als der generische _POLL (User-Fund
    # 2026-07-08 "wackelt" — s. _CHART_POLL-Docstring).
    frag = render.timeseries_fragment([], {"counts": {}, "running_since_uptime": 0}, now=1.0)
    assert 'id="timeseries"' in frag
    assert 'hx-get="/-/ui/schedules/timeseries?res=15"' in frag
    assert "every 20s" in frag


def test_timeseries_fragment_has_resolution_links_not_dropdown():
    # User-Fund 2026-07-08: "statt Drop-down einfach Links, klein, mit dem
    # aktuellen Zeitfenster unterstrichen".
    frag = render.timeseries_fragment([], now=1.0, bucket_minutes=5)
    assert "<select" not in frag
    assert 'class="res-link active"' in frag
    assert render._RESOLUTION_LABEL[5] in frag


def test_schedules_page_includes_timeseries_fragment():
    html = render.schedules_page(
        [_sched("daily")], now=300.0,
        daemon_status={"job_stats": {"counts": {"running": 1}, "running_since_uptime": 2}},
        landings=[_landing("complete", 250.0)])
    assert 'id="timeseries"' in html
    assert "2 since start" in html
    assert "chart.js" in html.lower()


# ── Routen ────────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, schedules: list[dict], *, status: dict | None = None,
                landings: list[dict] | None = None) -> None:
        self._s = schedules
        self._status = status or {}
        self._landings = landings or []

    def schedules(self) -> list[dict]:
        return self._s

    def status(self) -> dict:
        return self._status

    def landings(self, *, since: float | None = None) -> list[dict]:
        return self._landings


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
        assert "MAINT: ON" in r.text


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
        landings=[{"status": "complete", "finished_at": 1.0}])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="timeseries"' in r.text
        assert "5 since start" in r.text


def test_ui_schedules_timeseries_fragment_route(team_repo: Path):
    client = FakeClient(
        [], status={"job_stats": {"counts": {"running": 1}, "running_since_uptime": 1}},
        landings=[{"status": "complete", "finished_at": 1.0}])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/timeseries")
        assert r.status_code == 200
        assert 'id="timeseries"' in r.text
        assert "1 since start" in r.text


def test_ui_schedules_timeseries_fragment_route_honors_resolution_param(team_repo: Path):
    client = FakeClient([], status={"job_stats": {"counts": {}, "running_since_uptime": 0}})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/timeseries", params={"res": 5})
        assert r.status_code == 200
        assert 'class="res-link active"' in r.text
        assert render._RESOLUTION_LABEL[5] in r.text
        assert 'hx-get="/-/ui/schedules/timeseries?res=5"' in r.text


def test_ui_schedules_screen_survives_landings_fetch_failure(team_repo: Path):
    # /-/landings ist scheduler-gated (501 ohne Rolle) — der Screen darf
    # trotzdem laden, nur ohne Chart-Daten (§2.7, wie schedules()/status()).
    class BoomClient(FakeClient):
        def landings(self, *, since=None):
            raise RuntimeError("501")

    client = BoomClient([_sched("daily")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert "daily" in r.text
