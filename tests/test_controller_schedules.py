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













def _seed_schedule_ref(root: Path, slug: str) -> str:
    d = root / "vault" / "case" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text('---\nschedule: "now"\njob: "echo hi"\n---\n',
                                 encoding="utf-8")
    return f"{slug}/README.md"



