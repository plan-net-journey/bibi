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
           payload="echo hi", app_port=None, active=True, schedule_ref=None) -> dict:
    return {"slug": slug, "kind": kind, "trigger": trigger,
            "next_fire_at": next_fire_at, "last_status": last_status,
            "last_run_at": last_run_at, "row_status": row_status or last_status,
            "oneshot": oneshot, "payload": payload, "app_port": app_port,
            "active": active, "schedule_ref": schedule_ref}


# ── PLAN-14 Stufe 14.6 — Registrierungs-Drei-Gruppen (Aktiv/Inaktiv/Journal) ──


def test_schedule_list_groups_by_active_flag():
    items = [_sched("a", active=True), _sched("b", active=False),
             _sched("c", active=None)]
    html = render.schedule_list(items, now=1000.0)
    assert 'href="/-/ui/schedule/a"' in html.split("Archive")[0]
    assert "Archive" in html and 'href="/-/ui/schedule/b"' in html.split("Archive")[1].split("Journal")[0]
    assert "Journal" in html and 'href="/-/ui/schedule/c"' in html.split("Journal")[1]


def test_schedule_list_no_archive_or_journal_heading_when_all_active():
    html = render.schedule_list([_sched("a", active=True)], now=1000.0)
    assert "Archive" not in html and "Journal —" not in html


def test_schedule_list_default_active_true_when_key_missing():
    # Rückwärtskompatibel: Items ohne "active"-Key (ältere Fixtures/Fake-Clients)
    # landen in "Aktiv", nicht stillschweigend in einer anderen Gruppe.
    item = _sched("a")
    del item["active"]
    html = render.schedule_list([item], now=1000.0)
    assert "Archive" not in html and "Journal —" not in html
    assert 'href="/-/ui/schedule/a"' in html


# ── PLAN-23 Befund 2 — abgeschlossene oneshots wandern ins Archive ──────────


def test_schedule_list_completed_oneshot_with_md_goes_to_archive():
    # PLAN-23 Befund 2: ein `at:`-Einzellauf (oneshot=True), der complete
    # abgeschlossen hat, gehört ins Archive, auch wenn seine MD noch da ist
    # (active=True) — anders als früher (PLAN-14 14.6: "bleibt einfach aktiv").
    items = [_sched("done", active=True, oneshot=True, last_status="complete")]
    html = render.schedule_list(items, now=1000.0)
    assert "Archive" in html
    assert 'href="/-/ui/schedule/done"' in html.split("Archive")[1]
    assert 'href="/-/ui/schedule/done"' not in html.split("Archive")[0]


def test_schedule_list_pending_oneshot_with_md_stays_active():
    # Regressionsschutz: ein NOCH NICHT abgeschlossener oneshot bleibt aktiv —
    # nur last_status=="complete" verschiebt ihn ins Archive.
    items = [_sched("waiting", active=True, oneshot=True, last_status="pending")]
    html = render.schedule_list(items, now=1000.0)
    assert "Archive" not in html
    assert 'href="/-/ui/schedule/waiting"' in html


def test_schedule_list_completed_recurring_stays_active():
    # Regressionsschutz: ein abgeschlossener WIEDERKEHRENDER Schedule
    # (oneshot=False) gehört weiter zur aktiven Rotation (Lazy Rearm), nicht
    # ins Archive — nur echte oneshots werden bei complete archiviert.
    items = [_sched("cron", active=True, oneshot=False, last_status="complete")]
    html = render.schedule_list(items, now=1000.0)
    assert "Archive" not in html
    assert 'href="/-/ui/schedule/cron"' in html


# ── Filter-Cookie-Validierung (pure) ────────────────────────────────────────


def test_cookie_filter_value_accepts_known_type():
    assert render._cookie_filter_value("job", render._SCHED_TYPES) == "job"


def test_cookie_filter_value_accepts_alle_sentinel():
    assert render._cookie_filter_value("alle", render._SCHED_TYPES) == "alle"


def test_cookie_filter_value_rejects_stale_value():
    # z. B. der entfernte "app"-Typ (PLAN-25 Befund 7).
    assert render._cookie_filter_value("app", render._SCHED_TYPES) is None


def test_cookie_filter_value_none_for_missing_cookie():
    assert render._cookie_filter_value(None, render._SCHED_TYPES) is None


def test_cookie_resolution_value_accepts_known_preset():
    assert render._cookie_resolution_value("120") == 120


def test_cookie_resolution_value_rejects_unknown_preset():
    assert render._cookie_resolution_value("999") is None


def test_cookie_resolution_value_rejects_non_numeric():
    assert render._cookie_resolution_value("bogus") is None


def test_cookie_resolution_value_none_for_missing_cookie():
    assert render._cookie_resolution_value(None) is None


# ── Filter (pure) ─────────────────────────────────────────────────────────────


def test_filter_by_typ():
    s = [_sched("a", kind="job"), _sched("b", kind="job")]
    out = render.filter_schedules(s, typ="job", now=1000.0)
    assert [x["slug"] for x in out] == ["a", "b"]


def test_filter_by_typ_job_includes_former_app_port_schedules():
    # PLAN-25 Befund 7, User-Fund: Jobs mit port+prefix sollen als "job"
    # erscheinen, nicht als eigener "app"-Typ — app_port beeinflusst die Kind-
    # Anzeige/den Typ-Filter seit PLAN-25 nicht mehr, nur claude: tut das noch.
    s = [_sched("plain", payload="echo hi"),
         _sched("hitl", payload="python3 hitl_test_app.py", app_port=9100),
         _sched("ai", payload="claude: tu was")]
    assert [x["slug"] for x in render.filter_schedules(s, typ="job", now=1.0)] == ["plain", "hitl"]
    assert [x["slug"] for x in render.filter_schedules(s, typ="claude", now=1.0)] == ["ai"]


def test_sched_types_no_longer_offers_app():
    # PLAN-25 Befund 7: "app" war nie ein eigener kind-Wert in der DB (immer
    # "job"), nur ein abgeleiteter Anzeige-/Filter-Typ — jetzt ganz entfernt,
    # jobs mit app_port erscheinen als "job".
    assert render._SCHED_TYPES == ("job", "claude")


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


def test_schedules_page_includes_feed_status_header():
    # User-Fund: denselben Host/Mode/Git/Job-Status-Kopf wie auf /-/ auch auf
    # /-/ui/schedules zeigen.
    html = render.schedules_page(
        [_sched("daily")], now=300.0,
        daemon_status={"job_stats": {"counts_by_kind": {"job": {"running": 1}},
                                     "complete_since_uptime": 3, "next_due_at": None}},
        git_status={"tree": "clean", "sync": "synced", "branch": "trunk"},
        host_url="http://sarasate.tail9f9173.ts.net:8780")
    assert 'id="feedstatus"' in html
    assert html.count('<div class="card">') == 4  # Host/Mode/Git/Job Status
    assert '<div class="jsg-k">Running</div><div class="jsg-v">1</div><div class="jsg-v">0</div><div class="jsg-v">0</div>' in html


def test_schedules_fragment_active_only_has_single_panel_card():
    # PLAN-25 Befund 6: 2 Rahmen (Chart/Schedules) waren korrekt, solange kein
    # Archive/Journal vorliegt — kein leerer zweiter Rahmen ohne Inhalt.
    frag = render.schedules_fragment([_sched("a", active=True)], now=1000.0)
    assert frag.count('class="panel-card"') == 1


def test_schedules_fragment_never_shows_archive_anymore():
    # Bibi4-Iteration, User-Fund: "Archive wird verschoben auf einen eigenen
    # Screen" — schedules_fragment() zeigt nur noch die aktive Liste, auch
    # wenn Archive/Journal-Einträge vorliegen (die leben jetzt exklusiv auf
    # archive_fragment()/-page(), s. u.). Löst PLAN-25 Befund 6 (3 Rahmen
    # Chart/Schedules/Archive auf einer Seite) ab.
    items = [_sched("a", active=True), _sched("b", active=False),
             _sched("c", active=None)]
    frag = render.schedules_fragment(items, now=1000.0)
    assert frag.count('class="panel-card"') == 1
    assert "Archive" not in frag and "Journal" not in frag


def test_archive_fragment_shows_archive_and_journal_in_one_panel_card():
    items = [_sched("a", active=True), _sched("b", active=False),
             _sched("c", active=None)]
    frag = render.archive_fragment(items, now=1000.0)
    assert frag.count('class="panel-card"') == 1
    assert "Archive" in frag and "Journal" in frag
    assert "Schedules (" not in frag  # aktive Liste steht nicht mehr hier


def test_archive_fragment_empty_shows_placeholder():
    frag = render.archive_fragment([], now=1000.0)
    assert "kein Archiv" in frag


def test_archive_fragment_is_bus_driven():
    frag = render.archive_fragment([_sched("a", active=False)], now=1.0)
    assert 'id="archive"' in frag
    assert 'data-bus="jobs"' in frag
    assert 'data-bus-refetch="/-/ui/archive/list"' in frag
    assert "window.bibiFollow" not in frag


def test_archive_page_has_header_and_archive_fragment():
    html = render.archive_page(
        [_sched("a", active=False)], now=1000.0,
        daemon_status={"roles": ["scheduler"]})
    assert html.lower().startswith("<!doctype html>")
    assert 'href="/-/ui/archive"' not in html  # aktiver Tab, kein Link auf sich selbst
    assert '<span class="tab-active">Archive</span>' in html
    assert 'id="archive"' in html


def test_archive_page_includes_status_cards():
    # Bibi4-Iteration, User-Fund: "Header ist in Feed, Jobs, Archive (!),
    # Live-Log sichtbar" — die Archive-Extraktion hatte die Status-Kacheln
    # (Host/Mode/Git/Job-Status) schlicht nicht mitgenommen.
    html = render.archive_page(
        [_sched("a", active=False)], now=1000.0,
        daemon_status={"roles": ["scheduler"]})
    assert 'id="feedstatus"' in html


def test_schedules_fragment_refetch_url_carries_filter():
    # Der Bus-Refetch muss den aktiven Filter in der URL tragen, damit er
    # den Swap ueberlebt (dieselbe Idee wie frueher beim Self-Poll).
    frag = render.schedules_fragment([_sched("a")], now=1.0, typ="job", status="problem")
    assert 'data-bus-refetch="/-/ui/schedules/list?typ=job&status=problem"' in frag


def test_sched_table_column_header_combined():
    assert "last / since" in render.schedule_list([_sched("a")], now=300.0)


def test_sched_row_shows_app_type_with_port_link():
    # Bibi4-Iteration, User-Fund: "Type (beim Host wird app noch nicht
    # angezeigt, soll es aber, auch mit Port!)" — _sched_row() nutzte bisher
    # _effective_sched_type() (kennt kein "app"), jetzt dieselbe Ableitung wie
    # die Client-Jobs-Tabelle (_jobs_type_cell()). Reversiert nur die Anzeige,
    # nicht die Filter-Semantik (PLAN-25 Befund 7 bleibt für typ=/filter_schedules()).
    html = render._sched_row(_sched("a", app_port=9100), now=100.0,
                             public_host="sarasate.tail9f9173.ts.net")
    assert ('<td class="kind"><a href="http://sarasate.tail9f9173.ts.net:9100/" '
           'target="_blank" rel="noopener">app :9100</a></td>') in html


def test_sched_row_plain_job_type_unaffected():
    html = render._sched_row(_sched("a"), now=100.0)
    assert '<td class="kind">job</td>' in html


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
    labels_2h, _ = render._landings_buckets([], now=1_000_000.0, bucket_minutes=120)
    labels_5m, _ = render._landings_buckets([], now=1_000_000.0, bucket_minutes=5)
    labels_1m, _ = render._landings_buckets([], now=1_000_000.0, bucket_minutes=1)
    assert len(labels_2h) == 24    # 120min × 24 = 48h Fenster
    assert len(labels_5m) == 96    # 5min × 96 = 8h Fenster
    assert len(labels_1m) == 120   # 1min × 120 = 2h Fenster


def test_resolution_windows_cover_all_seven_presets():
    # PLAN-26 Befund 2, Korrektur nach Live-Review: 6h/144h wieder raus,
    # dafür 24h/1m (720h) neu dazu; 8h/168h -> 8h/1w, 3h/72h -> 3h/3d,
    # 2h/48h -> 2h/2d (kompakte Tage/Wochen/Monat-Einheit statt Stundenzahl
    # im Label — die zugrundeliegenden Bucket-/Fenster-Minuten bleiben
    # gleich, nur die Anzeige ändert sich). 15min/5min/1min unverändert.
    assert render._RESOLUTION_WINDOWS == {
        1440: 720, 480: 168, 180: 72, 120: 48, 15: 24, 5: 8, 1: 2,
    }
    assert render._RESOLUTION_LABEL == {
        1440: "24h/1m", 480: "8h/1w", 180: "3h/3d", 120: "2h/2d",
        15: "15min/24h", 5: "5min/8h", 1: "1min/2h",
    }


def test_landings_chart_html_has_canvas_and_chartjs_init():
    labels, counts = render._landings_buckets(
        [_landing("complete", 100_000.0 - 60)], now=100_000.0, bucket_minutes=15)
    html = render._landings_chart_html(labels, counts)
    assert '<canvas id="landingsChart"' in html
    assert "new Chart(" in html
    assert "complete" in html and "#5fb37a" in html  # grün


def test_landings_chart_html_empty_shows_placeholder():
    assert "keine Daten" in render._landings_chart_html([], {})


def test_landings_chart_html_single_day_uses_bare_time():
    # Bibi4-Iteration, User-Fund: Datum nur bei mehr als einem Tag Spannweite —
    # innerhalb eines Tages ist HH:MM schon eindeutig, kein Datum nötig.
    labels, counts = render._landings_buckets(
        [_landing("complete", 100_000.0 - 60)], now=100_000.0, bucket_minutes=15)
    html = render._landings_chart_html(labels, counts)
    assert "labels" in html
    assert "." not in html.split('"labels": [')[1].split("]")[0]  # kein TT.MM.-Punkt


def test_landings_chart_html_multi_day_includes_date():
    # 480min-Bucket/168h-Fenster (Preset "8h/1w") spannt 7 Tage — HH:MM allein
    # wäre über mehrere Tage mehrdeutig (User-Fund, s. Docstring).
    labels, counts = render._landings_buckets(
        [_landing("complete", 100_000.0 - 60)], now=1_000_000.0, bucket_minutes=480)
    html = render._landings_chart_html(labels, counts)
    labels_json = html.split('"labels": [')[1].split("]")[0]
    assert "." in labels_json  # TT.MM HH:MM-Form enthält einen Punkt


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


def test_timeseries_fragment_has_own_bus_target():
    # Eigenes, selteneres Target "chart" (nur neue Journal-Eintraege) statt
    # des generischen "jobs" — dieselbe Absicht wie der fruehere langsamere
    # _CHART_POLL (User-Fund 2026-07-08 "wackelt"), jetzt ereignisgenau.
    frag = render.timeseries_fragment([], {"counts": {}, "running_since_uptime": 0}, now=1.0)
    assert 'id="timeseries"' in frag
    assert 'data-bus="chart"' in frag
    assert 'data-bus-refetch="/-/ui/schedules/timeseries?res=15"' in frag
    assert "every " not in frag


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
    client = FakeClient([], status={"maintenance": True, "roles": ["scheduler"]})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="rescan"' in r.text
        assert 'id="maint" class="toggle warn"' in r.text


def test_ui_archive_screen_route(team_repo: Path):
    # Bibi4-Iteration, User-Fund: eigener Screen fuer Archive/Journal.
    client = FakeClient([_sched("done", active=False), _sched("hist", active=None)])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/archive")
        assert r.status_code == 200
        assert "done" in r.text and "hist" in r.text
        assert "Schedules (" not in r.text  # aktive Liste lebt nur auf /-/ui/schedules


def test_ui_archive_list_fragment_route(team_repo: Path):
    client = FakeClient([_sched("done", active=False)])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/archive/list")
        assert r.status_code == 200
        assert 'id="archive"' in r.text and "done" in r.text


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


def test_ui_schedules_screen_includes_feed_status_header(team_repo: Path):
    # User-Fund: denselben Host/Mode/Git/Job-Status-Kopf wie auf /-/ auch auf
    # /-/ui/schedules zeigen.
    client = FakeClient(
        [], status={"job_stats": {"counts": {"running": 1}, "complete_since_uptime": 3,
                                  "next_due_at": None}})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="feedstatus"' in r.text
        assert r.text.count('<div class="card">') == 4


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


# ── Filter-Persistenz per Cookie (User-Fund: "die ausgewählte Auswahl in
# /-/ui/schedules sollte erhalten bleiben. Entweder Cookies oder Local Store") ─


def test_schedules_screen_sets_filter_cookies_from_query(team_repo: Path):
    client = FakeClient([_sched("a", kind="job"), _sched("b", kind="claude",
                                                        payload="claude: x")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules", params={"typ": "job", "status": "complete"})
        assert r.cookies.get("bibi_sched_typ") == "job"
        assert r.cookies.get("bibi_sched_status") == "complete"


def test_schedules_screen_uses_cookie_when_no_query_param(team_repo: Path):
    client = FakeClient([_sched("jobrun", kind="job"),
                         _sched("clauderun", kind="claude", payload="claude: x")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        c.cookies.set("bibi_sched_typ", "claude")
        r = c.get("/-/ui/schedules")  # kein ?typ= in der URL
        assert r.status_code == 200
        assert "clauderun" in r.text
        assert "jobrun" not in r.text


def test_schedules_screen_query_param_overrides_cookie(team_repo: Path):
    client = FakeClient([_sched("jobrun", kind="job"),
                         _sched("clauderun", kind="claude", payload="claude: x")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        c.cookies.set("bibi_sched_typ", "claude")
        r = c.get("/-/ui/schedules", params={"typ": "job"})
        assert "jobrun" in r.text
        assert "clauderun" not in r.text
        assert r.cookies.get("bibi_sched_typ") == "job"  # Cookie folgt der expliziten Wahl


def test_schedules_screen_ignores_stale_invalid_cookie(team_repo: Path):
    # Z. B. der entfernte "app"-Typ (PLAN-25 Befund 7) — ein altes Cookie darf
    # nicht crashen oder alles unsichtbar filtern.
    client = FakeClient([_sched("a", kind="job")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        c.cookies.set("bibi_sched_typ", "app")
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert '/-/ui/schedule/a"' in r.text


def test_schedules_list_fragment_sets_filter_cookies(team_repo: Path):
    client = FakeClient([_sched("a", kind="job")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/list", params={"typ": "job", "status": "alle"})
        assert r.cookies.get("bibi_sched_typ") == "job"
        assert r.cookies.get("bibi_sched_status") == "alle"


# ── Chart-Auflösungs-Persistenz per Cookie (dieselbe Systematik + User-Fund:
# "warum wird die Auflösung ... nicht gespeichert?") ─────────────────────────


def test_schedules_timeseries_fragment_sets_resolution_cookie(team_repo: Path):
    client = FakeClient([], status={"job_stats": {"counts": {}, "running_since_uptime": 0}})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/timeseries", params={"res": 120})
        assert r.cookies.get("bibi_sched_res") == "120"


def test_schedules_screen_uses_resolution_cookie_when_no_query_param(team_repo: Path):
    client = FakeClient([], status={"job_stats": {"counts": {}, "running_since_uptime": 0}})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        c.cookies.set("bibi_sched_res", "120")
        r = c.get("/-/ui/schedules")  # kein ?res= in der URL
        assert r.status_code == 200
        assert render._RESOLUTION_LABEL[120] in r.text
        assert 'hx-get="/-/ui/schedules/timeseries?res=120"' in r.text


def test_schedules_screen_res_query_param_overrides_cookie(team_repo: Path):
    client = FakeClient([], status={"job_stats": {"counts": {}, "running_since_uptime": 0}})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        c.cookies.set("bibi_sched_res", "120")
        r = c.get("/-/ui/schedules", params={"res": 5})
        assert render._RESOLUTION_LABEL[5] in r.text
        assert r.cookies.get("bibi_sched_res") == "5"


def test_schedules_timeseries_fragment_ignores_stale_invalid_cookie(team_repo: Path):
    client = FakeClient([], status={"job_stats": {"counts": {}, "running_since_uptime": 0}})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        c.cookies.set("bibi_sched_res", "999")
        r = c.get("/-/ui/schedules/timeseries")
        assert r.status_code == 200
        assert render._RESOLUTION_LABEL[render._DEFAULT_RESOLUTION_MINUTES] in r.text


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


# ── Host-Sparkline-Spalte (Batch 9 Punkt 1) ──────────────────────────────────


def _seed_schedule_ref(root: Path, slug: str) -> str:
    d = root / "vault" / "case" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text('---\nschedule: "now"\njob: "echo hi"\n---\n',
                                 encoding="utf-8")
    return f"{slug}/README.md"


def test_ui_schedules_screen_renders_eager_sparkline_cell(team_repo: Path):
    # Analog zu jobs_screen(): der initiale Seitenaufbau rechnet die Serie
    # synchron (schedule_ref -> repo_path -> _job_sparkline_series()), kein
    # separater Lazy-Request pro Zeile.
    ref = _seed_schedule_ref(team_repo, "hitl-test-app")
    client = FakeClient([_sched("hitl-test-app", schedule_ref=ref)])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="spark-hitl-test-app" hx-preserve="true">' in r.text


def test_ui_schedules_list_fragment_omits_sparkline_data(team_repo: Path):
    # Self-Poll-Ziel (schedules_list_fragment(), 2s-Tick) übergibt bewusst
    # keine Sparkline-Daten — htmx behält dank hx-preserve die vom initialen
    # Seitenaufbau gerenderte Zelle (s. render._sparkline_cell()-Docstring),
    # der Poll selbst löst keine erneute git-log-Berechnung aus.
    ref = _seed_schedule_ref(team_repo, "hitl-test-app")
    client = FakeClient([_sched("hitl-test-app", schedule_ref=ref)])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/list")
        assert r.status_code == 200
        assert 'id="spark-hitl-test-app" hx-preserve="true"></span>' in r.text


def test_ui_schedules_screen_sparkline_cell_empty_without_schedule_ref(team_repo: Path):
    # Journal-only-Phantom-Slugs (job_db.list_schedules()) tragen keinen
    # schedule_ref -> keine Sparkline-Berechnung, aber auch kein Crash.
    client = FakeClient([_sched("phantom", schedule_ref=None)])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules")
        assert r.status_code == 200
        assert 'id="spark-phantom" hx-preserve="true"></span>' in r.text
