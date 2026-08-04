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













def test_the_type_cell_shows_app_with_a_port_link():
    """Bibi4-Iteration, User-Fund: "Type (beim Host wird app noch nicht
    angezeigt, soll es aber, auch mit Port!)".

    Geprueft wird jetzt `_jobs_type_cell()` selbst statt `_sched_row()`, das
    sie nur durchreichte: die Zeilenfunktion des bibi4-Schedules-Screens ist
    mit m.rau/bibi#130 entfallen, die Zelle lebt weiter und hat zwei Aufrufer.
    Ein Test, der eine lebende Funktion durch eine tote hindurch prueft,
    verschwindet sonst mit der toten — samt seiner Aussage.
    """
    zelle = render._jobs_type_cell(_sched("a", app_port=9100),
                                   "sarasate.tail9f9173.ts.net")
    assert ('<a href="http://sarasate.tail9f9173.ts.net:9100/" '
           'target="_blank" rel="noopener">app :9100</a>') == zelle


def test_the_type_cell_leaves_a_plain_job_alone():
    assert render._jobs_type_cell(_sched("a"), "localhost") == "job"


# ── Lauf-Historie-Chart (PLAN-21 Befund 11 v2, pure) ─────────────────────────


def _landing(status: str, finished_at: float) -> dict:
    return {"status": status, "finished_at": finished_at}










def _seed_schedule_ref(root: Path, slug: str) -> str:
    d = root / "vault" / "case" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text('---\nschedule: "now"\njob: "echo hi"\n---\n',
                                 encoding="utf-8")
    return f"{slug}/README.md"



