"""Stufe 4.2 (Controller-Anteil) — Schedule-Detail + Output-Rendering
(PLAN-4 §4.2/§2.5). Render-Funktionen pur; Routen mit gefaktem Client."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── Output-Rendering: event-typ-fähig + job/claude-Dispatch ──────────────────


def test_output_block_job_preformatted_with_stderr():
    ev = [{"s": "out", "line": "zeile eins"}, {"s": "err", "line": "oops"}]
    html = render.output_block(ev, "job")
    assert "term" in html and "<pre" in html
    assert "zeile eins" in html
    assert 'class="err"' in html and "oops" in html


def test_output_block_renders_phase_events_with_own_class():
    # User-Feedback 2026-07-03: Worker-/Wrapper-Startup-Phasen (Worktree,
    # Container, Prozess-Spawn) landen als "phase"-Events im selben Output,
    # optisch von normalem Job-Output abgesetzt.
    ev = [{"s": "phase", "line": "worktree: wird vorbereitet …"},
          {"s": "out", "line": "hallo"}]
    html = render.output_block(ev, "job")
    assert 'class="phase"' in html and "worktree: wird vorbereitet" in html


def test_output_block_escapes_html():
    ev = [{"s": "out", "line": "<script>alert(1)"}]
    html = render.output_block(ev, "job")
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_output_block_strips_ansi():
    ev = [{"s": "out", "line": "\x1b[31mrot\x1b[0m"}]
    html = render.output_block(ev, "job")
    assert "rot" in html
    assert "\x1b[31m" not in html


def test_output_block_claude_uses_same_line_rendering_as_live():
    # User-Feedback 2026-07-01: der archivierte Output sah über einen zweiten,
    # Markdown-basierten Renderer anders aus als während RUNNING — jetzt dieselbe
    # Zeilen-für-Zeile-Formatierung (Uhrzeit-Präfix, kein Markdown-Parsing mehr).
    ev = [{"t": 1, "s": "out", "line": "# Titel"},
          {"t": 2, "s": "thinking", "line": "grübel"}]
    html = render.output_block(ev, "claude")
    assert "<h1>Titel</h1>" not in html
    assert "# Titel" in html
    assert 'class="thinking"' in html and "grübel" in html
    assert 'class="lts"' in html


def test_output_block_empty():
    assert "no output" in render.output_block([], "job")


# ── Schedule-Detail ──────────────────────────────────────────────────────────


def test_schedule_detail_page_renders_runs():
    sched = {"slug": "boom", "kind": "job", "trigger": "now",
             "next_fire_at": None, "last_status": "error"}
    runs = [{"id": 7, "run_id": "boom:0", "status": "error", "reason": "x",
             "exit_code": 1, "started_at": 1.0, "finished_at": 2.0,
             "commit_sha": "abc1234deadbeef", "branch": "agent/boom",
             "output_ref": "data/...", "domain": "scheduled", "kind": "job"}]
    html = render.schedule_detail_page(sched, runs)
    assert html.lower().startswith("<!doctype html>")
    assert "boom" in html
    assert "error" in html
    assert "abc1234" in html and "abc1234deadbeef" not in html.split("title=")[0]
    assert 'href="/-/ui/schedule/boom/attrs">Attribute' in html
    assert "← zurück" not in html  # Bibi4-Iteration, User-Fund: redundant zum Jobs-Tab


def test_schedule_detail_page_no_runs():
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "never", "last_status": None}, [])
    assert "no runs yet" in html


def test_run_rows_have_no_relative_time():
    # #journal pollt nicht mit (Infinite Scroll, §6) — ein einmal gerendertes
    # "vor Xs" würde veralten. Nur der absolute Zeitstempel bleibt stehen.
    now = datetime(2026, 7, 3, 12, 0, 0).timestamp()
    runs = [{"id": 1, "status": "complete", "kind": "job",
            "finished_at": datetime(2026, 7, 3, 11, 59, 57).timestamp()}]
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, runs, slug="x", now=now)
    assert "11:59" in html
    assert "vor 3s" not in html and "vor 0s" not in html


def test_schedule_detail_action_bar_with_job():
    sched = {"slug": "boom", "kind": "job", "trigger": "now", "last_status": "error"}
    job = {"id": "abc123", "slug": "boom", "status": "error"}
    html = render.schedule_detail_page(sched, [], job, slug="boom")
    for verb in ("start", "reset"):  # error: enabled
        assert f'hx-post="/-/ui/schedule/boom/{verb}" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this">' in html
    # kill bleibt sichtbar, aber disabled (ist schon terminal) — alle 3 Buttons
    # rendern immer, Stage 3 der Job-Lifecycle-Matrix.
    assert 'hx-post="/-/ui/schedule/boom/kill" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this" disabled>' in html
    assert 'id="live"' in html and 'id="journal"' in html


def test_schedule_detail_no_action_bar_without_job():
    sched = {"slug": "boom", "kind": "job", "trigger": "now", "last_status": "error"}
    html = render.schedule_detail_page(sched, [], None, slug="boom")
    assert "hx-post=" not in html  # keine Verben ohne Live-Job


# ── PLAN-14 Stufe 14.1 — _VERBS_FOR_STATUS-Matrix-Fixes ──────────────────────


def test_verbs_kill_disabled_for_error():
    assert "kill" not in render._VERBS_FOR_STATUS["error"]


def test_verbs_kill_enabled_for_killed():
    assert "kill" in render._VERBS_FOR_STATUS["killed"]


def test_verbs_reset_disabled_for_failed():
    assert "reset" not in render._VERBS_FOR_STATUS["failed"]


def test_verbs_start_enabled_for_failed():
    # User-Entscheidung (Job Lifecycle §START/failed): sofortiger Start ohne
    # Attempts-Reset, nur der Backoff-Timer wird übersprungen.
    assert "start" in render._VERBS_FOR_STATUS["failed"]


def test_action_bar_has_kill_button_for_killed_job():
    job = {"id": "j1", "slug": "x", "status": "killed"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/kill"' in html


def test_action_bar_reset_disabled_start_and_kill_enabled_for_failed_job():
    job = {"id": "j1", "slug": "x", "status": "failed"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/reset" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this" disabled>' in html
    assert 'hx-post="/-/ui/schedule/x/start" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this">' in html
    assert 'hx-post="/-/ui/schedule/x/kill" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this">' in html


# ── PLAN-14 Stufe 14.2 — START für inactive/zombie/killed (+ error/complete) ──


def test_verbs_start_enabled_for_inactive_zombie_killed():
    for st in ("inactive", "zombie", "killed"):
        assert "start" in render._VERBS_FOR_STATUS[st]


def test_action_bar_has_start_button_for_inactive_job():
    job = {"id": "j1", "slug": "x", "status": "inactive"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/start"' in html


def test_action_bar_pending_kill_enabled():
    # pending+KILL ("aus dem Schedule nehmen") ist jetzt wirksam, nicht mehr
    # nur ein toter Button (Stage 2 der Job-Lifecycle-Matrix).
    job = {"id": "j1", "slug": "x", "status": "pending"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/kill" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this">' in html


def test_action_bar_complete_kill_enabled_reset_disabled():
    # User-Redesign 2026-07-20 (widerruft den complete-Teil von 2026-07-03):
    # KILL auf complete archiviert den Lauf und haelt Lazy Rearm an (s.
    # lifecycle.py) — RESET bleibt fuer complete weiterhin No-op (dafuer gibt
    # es START, das archiviert+sofort faellig macht, s. start_now()).
    job = {"id": "j1", "slug": "x", "status": "complete"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/kill" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this">' in html
    assert 'hx-post="/-/ui/schedule/x/reset" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this" disabled>' in html
    assert 'hx-post="/-/ui/schedule/x/start" hx-target="#live" hx-swap="outerHTML" hx-disabled-elt="this">' in html


# ── PLAN-24 Befund 5 — REBUILD-Aktion nur bei exec_mode: container ──────────


def test_action_bar_shows_rebuild_for_container_job():
    job = {"id": "j1", "slug": "x", "status": "pending"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now", "exec_mode": "container"},
        [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/rebuild" hx-target="#live" hx-swap="outerHTML"' in html
    assert ">REBUILD<" in html


def test_action_bar_hides_rebuild_for_host_job():
    # User-Klärung (PLAN-24): "sichtbar nur bei exec_mode: container", nicht
    # sichtbar-aber-deaktiviert — Host-Mode-Jobs haben kein per-Job-Image.
    job = {"id": "j1", "slug": "x", "status": "pending"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert "rebuild" not in html.lower()


# ── Bibi4 Batch 6 — Aktivitätsanzeige an den Aktions-Buttons ─────────────────
# Pulse/Spinner + hx-disabled-elt, Scope nur START/RESET/KILL/REBUILD (nicht
# Sparkline-/Board-Fragment-Loads) — User-Entscheidung, Beobachtungen.md.


def test_action_bar_buttons_carry_hx_disabled_elt():
    # hx-disabled-elt sperrt den Klick strukturell für die Request-Dauer
    # (verhindert Doppel-Submits), unabhängig vom serverseitigen "disabled".
    job = {"id": "j1", "slug": "x", "status": "killed"}  # start/reset/kill alle enabled
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert html.count('hx-disabled-elt="this"') == 3  # start, reset, kill


def test_action_bar_buttons_carry_spinner_span():
    job = {"id": "j1", "slug": "x", "status": "killed"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert html.count('<span class="btn-spinner" aria-hidden="true"></span>') == 3


def test_action_bar_rebuild_button_carries_disabled_elt_and_spinner():
    job = {"id": "j1", "slug": "x", "status": "pending"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now", "exec_mode": "container"},
        [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/rebuild"' in html
    rebuild_btn = html[html.index('hx-post="/-/ui/schedule/x/rebuild"'):]
    assert 'hx-disabled-elt="this"' in rebuild_btn[:rebuild_btn.index("</button>")]
    assert 'class="btn-spinner"' in rebuild_btn[:rebuild_btn.index("</button>")]


def test_css_defines_btn_spinner_pulse_scoped_to_htmx_request():
    # Kein eigenes hx-indicator nötig: htmx setzt htmx-request automatisch
    # aufs auslösende Element (den Button selbst), der Spinner sitzt darin.
    assert ".htmx-request .btn-spinner { opacity: 1" in render._CSS
    assert "@keyframes bibi-pulse" in render._CSS


def test_run_row_has_delete_button():
    runs = [{"id": 9, "status": "complete", "exit_code": 0, "kind": "job"}]
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, runs, slug="x")
    assert 'hx-delete="/-/ui/schedule/x/run/9"' in html


# ── PLAN-14 Stufe 14.3 — Dauer-Spalte in der Journal-Historie ────────────────


def test_run_rows_show_duration_column():
    runs = [{"id": 1, "status": "complete", "exec_runtime": 61.4, "kind": "job"}]
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, runs, slug="x")
    assert "Dauer" in html
    assert "1m 1s" in html


def test_run_rows_duration_dash_when_missing():
    runs = [{"id": 1, "status": "running", "kind": "job"}]
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, runs, slug="x")
    assert "Dauer" in html
    assert "—" in html


# ── Journal-Historie: Datum bei Nicht-heute-Läufen ────────────────────────────


def test_abs_datetime_omits_date_for_today():
    now = datetime(2026, 7, 3, 12, 0, 0).timestamp()
    ts = datetime(2026, 7, 3, 8, 30, 0).timestamp()
    assert render._abs_datetime(ts, now) == "08:30"


def test_abs_datetime_shows_date_for_other_days():
    now = datetime(2026, 7, 3, 12, 0, 0).timestamp()
    ts = datetime(2026, 6, 28, 20, 56, 0).timestamp()
    assert render._abs_datetime(ts, now) == "28.06. 20:56"


def test_run_rows_show_date_for_runs_from_other_days():
    now = datetime(2026, 7, 3, 12, 0, 0).timestamp()
    runs = [{"id": 1, "status": "complete", "kind": "job",
            "finished_at": datetime(2026, 7, 1, 9, 15, 0).timestamp()}]
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, runs, slug="x", now=now)
    assert "01.07. 09:15" in html


# ── Routen mit gefaktem Client ───────────────────────────────────────────────


class FakeClient:
    def __init__(self, *, schedules=None, journal=None, output=None,
                 jobs=None, status=None) -> None:
        self._schedules = schedules or []
        self._journal = journal or []
        self._output = output or {}
        self._jobs = jobs or []
        self._status = status or {}
        self.actions: list[tuple] = []
        self.deleted: list[int] = []

    def status(self) -> dict:
        return self._status

    def schedules(self) -> list[dict]:
        return self._schedules

    def journal(self, *, slug=None, host=None, limit=None, offset=None) -> list[dict]:
        rows = [j for j in self._journal if slug is None or j.get("slug") == slug]
        if limit is not None:
            offset = offset or 0
            rows = rows[offset:offset + limit]
        return rows

    def jobs(self, *, status=None) -> list[dict]:
        return self._jobs

    def run_output(self, jid: int) -> dict:
        return self._output

    def job_output(self, job_id: str) -> dict:
        return self._output

    def job_action(self, job_id: str, verb: str) -> dict:
        self.actions.append((job_id, verb))
        return {"id": job_id, "status": verb}

    def delete_journal(self, jid: int) -> dict:
        self.deleted.append(jid)
        return {"deleted": jid}


@pytest.fixture
def app_with(team_repo: Path):
    def _make(client):
        return create_app(roles.resolve({"controller"}), controller_client=client)
    return _make


def test_schedule_detail_route(app_with):
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now",
                    "next_fire_at": None, "last_status": "error"}],
        journal=[{"id": 7, "slug": "boom", "status": "error", "reason": None,
                  "exit_code": 1, "started_at": 1.0, "finished_at": 2.0,
                  "commit_sha": "abc1234", "branch": "agent/boom", "kind": "job"}])
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/boom")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "boom" in r.text and "error" in r.text
        assert "Output ↓" not in r.text  # Follow-up: "Output entfällt" für Journal-Zeilen


def test_schedule_detail_route_has_rescan_and_reflects_maintenance(app_with):
    # User-Feedback 2026-07-03: RESCAN + MAINT auch auf der Job-Detail-Seite.
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now"}],
        status={"maintenance": True, "roles": ["scheduler"]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/boom")
        assert r.status_code == 200
        assert 'id="rescan"' in r.text
        assert 'id="maint" class="toggle warn"' in r.text


def test_schedule_detail_meta_shows_app_type_via_display_kind(app_with):
    # Batch 9 Punkt 2(a): live_fragment()s Typ-Zeile leitete "Typ" bisher roh
    # aus schedule["kind"] ab (immer "job"), statt wie die Schedules-Tabelle
    # (_sched_row()) über _jobs_type_cell()/display_kind() zu gehen — ein
    # App-Job (app_port gesetzt) zeigte auf der Detailseite fälschlich "job"
    # statt "app :<port>" + Link.
    client = FakeClient(
        schedules=[{"slug": "hitl-test-app", "kind": "job", "trigger": "now",
                    "app_port": 9101, "next_fire_at": None, "last_status": "pending"}])
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/hitl-test-app")
        assert r.status_code == 200
        assert "app :9101" in r.text


def test_schedule_detail_route_shows_app_link_for_running_app_job(app_with):
    # Batch 9 Punkt 2(b): _detail_data() reichte job["app_port"] nie durch —
    # _live_panel()s "Open app →"-Link (render.py) blieb dadurch für jeden
    # App-Job auf der Host-Detailseite unsichtbar, obwohl der zugehörige
    # Schedule app_port längst kennt (schedule_view()).
    client = FakeClient(
        schedules=[{"slug": "hitl-test-app", "kind": "job", "trigger": "now",
                    "app_port": 9101, "next_fire_at": None, "last_status": "running"}],
        jobs=[{"id": "j1", "slug": "hitl-test-app", "status": "running", "started_at": 1.0}])
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/hitl-test-app")
        assert r.status_code == 200
        assert "Open app →" in r.text and ":9101" in r.text


def test_schedule_detail_route_shows_output_for_terminal_job(app_with):
    # User-Feedback 2026-07-01: der Controller holt jetzt auch für einen
    # bereits terminalen Job den Output — die Route darf ihn nicht mehr
    # unterdrücken, bis ein neuer Lauf ihn ersetzt.
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now",
                    "next_fire_at": None, "last_status": "complete"}],
        jobs=[{"id": "j1", "slug": "boom", "status": "complete", "finished_at": 2.0}],
        output={"id": "j1", "kind": "job", "events": [{"s": "out", "line": "fertig"}]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/boom")
        assert r.status_code == 200
        # terminal ⇒ liveclamp (20-Zeilen-Höhenbegrenzung, Stage 5)
        assert 'class="liveout liveclamp"' in r.text and "fertig" in r.text


def test_schedule_detail_route_shows_output_for_failed_job(app_with):
    # User-Feedback 2026-07-05: "failed" (Retry noch übrig, vor Backoff-Ablauf)
    # fehlte in _TERMINAL_VIEW → die Live-Output-Box blieb leer, obwohl der
    # Output längst da ist — gerade bei "failed" will man ihn VOR dem nächsten
    # Retry sehen.
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now",
                    "next_fire_at": 5.0, "last_status": "failed"}],
        jobs=[{"id": "j1", "slug": "boom", "status": "failed", "finished_at": 2.0}],
        output={"id": "j1", "kind": "job", "events": [{"s": "err", "line": "kaputt"}]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/boom")
        assert r.status_code == 200
        assert 'class="liveout liveclamp"' in r.text and "kaputt" in r.text


def test_run_output_route_renders(app_with):
    client = FakeClient(output={"id": 7, "kind": "job", "events": [
        {"s": "out", "line": "hallo"}, {"s": "err", "line": "fehler"}]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/run/7/output")
        assert r.status_code == 200
        assert "hallo" in r.text
        assert 'class="err"' in r.text and "fehler" in r.text


def test_run_output_route_renders_claude_tool_use_as_plain_line(app_with):
    # Follow-up (User-Feedback 2026-07-01): output_block() rendert claude nicht
    # mehr über die Markdown-Kette — bereits formatierte Tool-Use-Zeilen (wie sie
    # output_format.format_events liefert) erscheinen als eigene Zeile, roh.
    client = FakeClient(output={"id": 7, "kind": "claude", "events": [
        {"t": 1, "s": "out", "line": "Ein **fetter** Satz."},
        {"t": 2, "s": "out", "line": ""},
        {"t": 3, "s": "out", "line": "→ Bash: ls -la"},
        {"t": 4, "s": "out", "line": ""},
    ]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/run/7/output")
        assert r.status_code == 200
        assert "<strong>fetter</strong>" not in r.text
        assert "Ein **fetter** Satz." in r.text
        assert "→ Bash: ls -la" in r.text


def test_action_route_calls_verb_and_rerenders(app_with):
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now",
                    "last_status": "error"}],
        journal=[],
        jobs=[{"id": "abc123", "slug": "boom", "status": "error"}])
    with TestClient(app_with(client)) as c:
        r = c.post("/-/ui/schedule/boom/start")
        assert r.status_code == 200
        assert client.actions == [("abc123", "start")]
        assert 'id="live"' in r.text  # re-rendertes Live-Fragment
        assert 'id="journal" hx-swap-oob="true"' in r.text  # + OOB-Refresh (G-1)

def test_action_route_rejects_unknown_verb(app_with):
    client = FakeClient(jobs=[{"id": "abc123", "slug": "boom"}])
    with TestClient(app_with(client)) as c:
        assert c.post("/-/ui/schedule/boom/destroy").status_code == 404
        assert client.actions == []


def test_action_route_accepts_rebuild_verb(app_with):
    # PLAN-24 Befund 5: rebuild ist kein _VERBS-Eintrag (nicht Teil der immer
    # gerenderten START/RESET/KILL-Leiste), muss aber trotzdem als gültiges
    # Verb akzeptiert werden (render._CONTAINER_VERBS).
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now", "exec_mode": "container"}],
        journal=[],
        jobs=[{"id": "abc123", "slug": "boom", "status": "pending"}])
    with TestClient(app_with(client)) as c:
        r = c.post("/-/ui/schedule/boom/rebuild")
        assert r.status_code == 200
        assert client.actions == [("abc123", "rebuild")]


def test_run_delete_route(app_with):
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now"}],
        jobs=[{"id": "abc123", "slug": "boom"}])
    with TestClient(app_with(client)) as c:
        r = c.request("DELETE", "/-/ui/schedule/boom/run/42")
        assert r.status_code == 200
        assert client.deleted == [42]
        assert 'id="journal"' in r.text
        assert 'id="live"' not in r.text  # nur #journal wird neu gerendert


def test_detail_shows_last_run_in_meta_and_live_state_in_panel():
    # „letzter Lauf" (Journal) in der Meta; der aktuelle/aktive Zustand im Live-Block.
    s = {"slug": "witz", "kind": "claude", "trigger": "*/3 * * * *",
         "last_status": "pending", "next_fire_at": None}
    runs = [{"id": 9, "slug": "witz", "status": "error", "exit_code": 1,
             "started_at": 1.0, "finished_at": 2.0, "kind": "claude"}]
    job = {"id": "j1", "slug": "witz", "status": "pending", "next_fire_at": None}
    html = render.schedule_detail_inner(s, runs, job, slug="witz", now=10.0)
    assert "last run <b>error</b>" in html          # Journal-Historie in Meta
    # PLAN-22 Befund 1: pending zeigt "waiting", nicht "active run" — es läuft
    # ja noch gar nichts (kein started_at, leeres Journal).
    assert 'class="live"' in html and "waiting" in html  # Live-Block
    assert '<h2>Journal</h2>' in html                    # Journal-Liste bleibt unten


def test_detail_live_panel_pending_shows_wartet_not_aktiver_lauf():
    # PLAN-22 Befund 1: "active run" suggeriert einen laufenden Prozess —
    # pending hat weder started_at noch Output, es wartet nur auf den Start.
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "pending", "next_fire_at": None}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'class="st pending">pending' in html
    assert "waiting" in html
    assert "active run" not in html


def test_detail_live_panel_for_running_job():
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'class="live"' in html and 'class="st running">running' in html
    # Job-Lifecycle-Redesign (leichte Variante, 2026-07-27): ein laufender Job
    # ohne echte Journal-Zeilen zeigt jetzt eine reine Anzeige-Platzhalterzeile
    # statt "no runs yet" — verlinkt zurück auf die Live-Region (#live).
    assert "no runs yet" not in html
    assert '<a class="back" href="#live">↑ live</a>' in html


def test_detail_live_panel_deferred_shows_wartet_auf_retry():
    # Bugfix (User-Fund, "von der defer habe ich nie etwas im FE gesehen"):
    # deferred hatte weder einen eigenen Label- noch next_fire_at-Zweig.
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "deferred", "started_at": 1.0,
           "next_fire_at": 20.0}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'class="live"' in html and 'class="st deferred">deferred' in html
    assert "waiting for retry" in html
    assert "active run" not in html
    assert "next run" in html


def test_detail_live_panel_deferred_shows_output_from_before_defer():
    job = {"id": "j", "slug": "a", "status": "deferred", "started_at": 1.0}
    live = {"kind": "job", "events": [{"s": "out", "line": "bis hierhin gelaufen"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    assert 'class="liveout' in html and "bis hierhin gelaufen" in html


def test_detail_live_panel_failed_shows_next_run():
    # Bugfix (User-Fund, "keine Log-Eintraege beim Retry sichtbar"): next_fire_at
    # fehlte bei failed komplett, obwohl der Job zwischen zwei Versuchen genau
    # darauf wartet — dasselbe Feld, das pending/deferred schon zeigen.
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "failed", "finished_at": 2.0,
           "next_fire_at": 15.0}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'class="st failed">failed' in html
    assert "next run" in html


def test_detail_app_link_defaults_to_localhost():
    # PLAN-22 Befund 6: ohne explizit übergebenen public_host bleibt localhost
    # der sichere Default (kein I/O in render.py — "pure" Funktionen, s.
    # Moduldocstring — config.public_host() wird eine Ebene höher aufgelöst).
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0, "app_port": 9100}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'href="http://localhost:9100/"' in html


def test_detail_app_link_uses_passed_public_host():
    # Auf einem Remote-Host (sarasate) muss der Link eine erreichbare Adresse
    # zeigen, nicht 127.0.0.1/localhost (live beobachtet, FeedbackOnJobManagement.md).
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0, "app_port": 9100}
    html = render.schedule_detail_inner(
        s, [], job, slug="a", now=5.0, public_host="sarasate.tail9f9173.ts.net")
    assert 'href="http://sarasate.tail9f9173.ts.net:9100/"' in html
    assert "127.0.0.1" not in html


def test_detail_shows_live_panel_for_last_terminal_run():
    # User-Feedback 2026-07-01: "archiviert wird erst vor dem nächsten Rerun" —
    # der letzte Lauf bleibt oben sichtbar (Status + "beendet vor..."), bis ein
    # neuer Lauf ihn ersetzt. Das Journal bekommt seine Zeile trotzdem sofort.
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "complete", "finished_at": 2.0}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'class="live"' in html and 'class="st complete">complete' in html
    assert "last run" in html and "finished 3s ago" in html


def test_detail_live_region_is_bus_only():
    # PLAN-36 Stufe 36.3: einziger Update-Weg ist der Bus (data-bus/-refetch)
    # — kein Sicherheitsnetz-Poll mehr, kein hx-get/hx-trigger auf der Region
    # (den Still-gestorbener-Strom-Fall deckt der Ping-Watchdog in _EVENTS_JS).
    html = render.schedule_detail_inner({"slug": "a"}, [], None, slug="a", now=1.0)
    assert 'data-bus="live:a"' in html
    assert 'data-bus-refetch="/-/ui/schedule/a/live"' in html
    live_div = html.split('id="live"')[1].split(">")[0]
    assert "hx-get" not in live_div and "hx-trigger" not in live_div


def test_detail_awaiting_needs_no_poll_special_case():
    # Der fruehere unbedingte awaiting-2s-Poll ist zurueckgebaut: jeder
    # awaiting-Uebergang ist eine (status, fire)-Aenderung, der Collector
    # feuert das live:-Event ereignisgenau — dieselbe Latenzklasse (~1s Tick)
    # wie der alte Poll, ohne Sonderpfad.
    job = {"id": "j", "slug": "a", "status": "awaiting", "started_at": 1.0}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert "every 2s" not in html
    assert 'data-bus="live:a"' in html


def test_journal_fragment_carries_bus_address():
    # PLAN-36 Stufe 36.2: ein journal:-Zustands-Event refetcht die Region ueber
    # ihre eigene Refresh-Route. Bis `#100` gab es zwei Basen (Host und die
    # abgeloeste Client-Detailseite); geblieben ist die eine, gegen die sie
    # wirkt.
    html = render.journal_fragment([], "a", now=1.0)
    assert 'data-bus="journal:a"' in html
    assert 'data-bus-refetch="/-/ui/schedule/a/journal"' in html


def test_live_panel_shows_output_expanded():
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    live = {"kind": "job", "events": [{"s": "out", "line": "lebt"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    assert 'class="liveout"' in html and "lebt" in html   # Live-Output inline


def test_live_panel_shows_output_for_last_terminal_run():
    # User-Feedback 2026-07-01: Output des letzten Laufs bleibt oben sichtbar,
    # auch nachdem der Job terminal geworden ist (nicht erst wieder über den
    # Journal-Detail-Link).
    job = {"id": "j", "slug": "a", "status": "complete", "finished_at": 5.0}
    live = {"kind": "job", "events": [{"s": "out", "line": "fertig"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    # terminal ⇒ liveclamp (20-Zeilen-Höhenbegrenzung, Stage 5)
    assert 'class="liveout liveclamp"' in html and "fertig" in html


def test_live_panel_terminal_output_has_height_cap():
    job = {"id": "j", "slug": "a", "status": "error", "finished_at": 5.0}
    live = {"kind": "job", "events": [{"s": "out", "line": "x"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    assert "liveclamp" in html


def test_live_panel_awaiting_output_has_height_cap():
    job = {"id": "j", "slug": "a", "status": "awaiting", "app_url": "http://x/"}
    live = {"kind": "job", "events": [{"s": "out", "line": "x"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    assert "liveclamp" in html


def test_live_panel_running_output_not_double_capped():
    # running hat mit .liveterm bereits einen eigenen Cap (24rem) — kein
    # zweiter verschachtelter Scrollbox-Rahmen nötig.
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    live = {"kind": "job", "events": [{"s": "out", "line": "lebt"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    assert "liveclamp" not in html


def test_run_rows_no_output_toggle():
    # Follow-up (User-Feedback): "Output entfällt" für Journal-Zeilen — kein
    # Inline-Toggle mehr, nur noch Detail/Löschen. Wer den Output sehen will,
    # geht über "→ Detail" auf die Execution-Detail-Seite (die ihn jetzt roh
    # und formatiert anbietet).
    runs = [{"id": 2, "status": "complete", "kind": "claude", "finished_at": 9.0},
            {"id": 1, "status": "complete", "kind": "claude", "finished_at": 5.0}]
    html = render.schedule_detail_inner({"slug": "a"}, runs, None, slug="a", now=10.0)
    assert "Output ↓" not in html
    assert 'href="/-/ui/run/1">→ Detail</a>' in html
    assert 'href="/-/ui/run/2">→ Detail</a>' in html


# ── PLAN-10 §10.5: HITL-Panel (awaiting + app_url direkt) ────────────────────


def test_hitl_panel_shows_app_url_link():
    """awaiting + app_url → direkter Link im Panel, Linktext = die URL selbst
    (regulärer Link statt Button — User-Feedback: Button-Klick schlug fehl,
    reiner Text-Link lässt die Ziel-URL sichtbar/kopierbar und ist eindeutig
    ein normaler <a>-Link)."""
    job = {"id": "j1", "slug": "a", "status": "awaiting",
           "app_url": "http://localhost:9100/input"}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert 'class="hitl"' in html
    assert '<a href="http://localhost:9100/input"' in html
    assert ">http://localhost:9100/input<" in html
    assert "textarea" not in html


def test_hitl_panel_no_app_url_shows_fallback():
    """awaiting ohne app_url → Fallback-Text."""
    job = {"id": "j2", "slug": "a", "status": "awaiting", "app_url": None}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert 'class="hitl"' in html
    assert "app_url unavailable" in html


def test_hitl_panel_region_carries_bus_address_when_awaiting():
    """awaiting → Region hängt am Bus wie jeder andere Zustand (PLAN-36 36.3);
    das HITL-Formular selbst rendert unverändert."""
    job = {"id": "j3", "slug": "a", "status": "awaiting",
           "app_url": "http://localhost:9100/input"}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert 'class="hitl"' in html
    assert 'data-bus="live:a"' in html
    assert "window.bibiFollow" not in html


# ── Journal Infinite Scroll ───────────────────────────────────────────────────


def _run(i: int) -> dict:
    return {"id": i, "status": "complete", "exit_code": 0, "kind": "job",
            "finished_at": float(i)}


def test_journal_sentinel_colspan_matches_columns():
    # 7 Spalten in der Journal-Tabelle: Zeit/Status/Grund/exit/Dauer/Commit/Aktionen.
    runs = [_run(i) for i in range(render._JOURNAL_PAGE_SIZE)]
    html = render.journal_fragment(runs, "x", now=100.0)
    assert 'id="journal-more"' in html
    assert '<td colspan="7"' in html


def test_journal_fragment_omits_sentinel_when_batch_short():
    runs = [_run(i) for i in range(3)]
    html = render.journal_fragment(runs, "x", now=100.0)
    assert 'id="journal-more"' not in html


def test_journal_fragment_oob_swap_attribute():
    html = render.journal_fragment([], "x", now=100.0, oob=True)
    assert 'id="journal" hx-swap-oob="true"' in html


# ── Live-Platzhalterzeile (Job-Lifecycle-Redesign, leichte Variante statt
# PLAN-35, Case 20260621.Bibi4-870bd9db, 2026-07-27) ─────────────────────────


@pytest.mark.parametrize("status", ["running", "awaiting", "deferred"])
def test_journal_fragment_shows_live_placeholder_for_in_progress_job(status):
    job = {"id": "j", "slug": "a", "status": status, "started_at": 1.0}
    html = render.journal_fragment([], "a", now=5.0, live_job=job)
    assert "no runs yet" not in html
    assert f'<td class="st {status}">{status}</td>' in html
    assert '<a class="back" href="#live">↑ live</a>' in html


@pytest.mark.parametrize("status", ["pending", "complete", "error", "inactive",
                                    "zombie", "killed", "failed"])
def test_journal_fragment_omits_live_placeholder_for_non_active_job(status):
    job = {"id": "j", "slug": "a", "status": status, "started_at": 1.0}
    html = render.journal_fragment([], "a", now=5.0, live_job=job)
    assert "no runs yet" in html  # kein Platzhalter, echte Läufe fehlen weiterhin


def test_journal_fragment_omits_live_placeholder_without_job():
    html = render.journal_fragment([], "a", now=5.0, live_job=None)
    assert "no runs yet" in html


def test_journal_fragment_live_placeholder_sits_above_real_runs():
    runs = [_run(1)]
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.journal_fragment(runs, "a", now=5.0, live_job=job)
    placeholder_idx = html.index("↑ live")
    real_row_idx = html.index("→ Detail")
    assert placeholder_idx < real_row_idx


def test_journal_fragment_live_placeholder_defaults_missing_status_to_running():
    # Client-live-Dicts trugen historisch nicht immer ein explizites status-Feld
    # — dieselbe Default-Regel wie _local_job_view() (Block 1).
    job = {"id": "j", "slug": "a", "started_at": 1.0}
    html = render.journal_fragment([], "a", now=5.0, live_job=job)
    assert '<td class="st running">running</td>' in html


def test_journal_runs_route_returns_next_batch_and_sentinel(app_with):
    full_page = [{"id": i, "slug": "boom", "status": "complete", "kind": "job",
                 "finished_at": float(i)} for i in range(render._JOURNAL_PAGE_SIZE)]
    client = FakeClient(journal=full_page)
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/boom/runs", params={"offset": 0})
        assert r.status_code == 200
        assert 'id="journal-more"' in r.text
        assert f'offset={render._JOURNAL_PAGE_SIZE}' in r.text


def test_journal_runs_route_omits_sentinel_when_batch_short(app_with):
    # 51 Läufe insgesamt: die Batch ab offset=50 hat nur noch 1 Eintrag (< 50)
    # → kein weiterer Sentinel, Scroll endet natürlich.
    all_runs = [{"id": i, "slug": "boom", "status": "complete", "kind": "job",
                "finished_at": float(i)} for i in range(51)]
    client = FakeClient(journal=all_runs)
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/boom/runs", params={"offset": 50})
        assert r.status_code == 200
        assert 'id="journal-more"' not in r.text


# ── Automatischer Journal-Refresh bei Terminal-Übergang ohne Klick ────────────
# (User-Feedback 2026-07-03: "wenn ein RUNNING Lauf terminal endet ... wird er
# erst bei manuellem Reload angezeigt")


def test_live_fragment_carries_slug_and_finished_at_fingerprint():
    job = {"id": "j1", "slug": "x", "status": "complete", "finished_at": 123.0}
    html = render.live_fragment({"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'data-slug="x"' in html
    assert 'data-finished-at="123.0"' in html


def test_live_fragment_finished_at_empty_while_running():
    job = {"id": "j1", "slug": "x", "status": "running", "started_at": 1.0}
    html = render.live_fragment({"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'data-finished-at=""' in html


def test_events_js_refetches_state_targets_via_htmx():
    # PLAN-36 Stufe 36.2 (ersetzt den frueheren finished-at-Fingerprint-
    # Autorefresh): ein state-Event sucht sein [data-bus=…]-Element und
    # refetcht dessen eigene Fragment-Route per htmx-Swap.
    js = render._EVENTS_JS
    assert "data-bus" in js and "data-bus-refetch" in js
    assert "htmx.ajax" in js and "outerHTML" in js


def test_schedule_journal_route_returns_fresh_page_one(app_with):
    client = FakeClient(journal=[{"id": 1, "slug": "boom", "status": "error", "kind": "job",
                                  "finished_at": 5.0}])
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/schedule/boom/journal")
        assert r.status_code == 200
        assert 'id="journal"' in r.text and "error" in r.text
