"""Stufe 4.2 (Controller-Anteil) — Schedule-Detail + Output-Rendering
(PLAN-4 §4.2/§2.5). Render-Funktionen pur; Routen mit gefaktem Client."""

from __future__ import annotations

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


def test_output_block_claude_markdown():
    ev = [{"s": "out", "line": "# Titel"},
          {"s": "out", "line": ""},
          {"s": "out", "line": "Ein **fetter** Satz mit `code`."},
          {"s": "out", "line": "- erstens"},
          {"s": "out", "line": "- zweitens"}]
    html = render.output_block(ev, "claude")
    assert "<h1>Titel</h1>" in html
    assert "<strong>fetter</strong>" in html
    assert "<code>code</code>" in html
    assert "<li>erstens</li>" in html and "<li>zweitens</li>" in html


def test_output_block_claude_fenced_code():
    ev = [{"s": "out", "line": "```"},
          {"s": "out", "line": "x = 1"},
          {"s": "out", "line": "```"}]
    html = render.output_block(ev, "claude")
    assert "<pre><code>x = 1</code></pre>" in html


def test_output_block_empty():
    assert "kein Output" in render.output_block([], "job")


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
    assert 'hx-get="/-/ui/run/7/output"' in html
    assert 'href="/-/"' in html  # zurück-Link


def test_schedule_detail_page_no_runs():
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "never", "last_status": None}, [])
    assert "noch keine Läufe" in html


def test_schedule_detail_action_bar_with_job():
    sched = {"slug": "boom", "kind": "job", "trigger": "now", "last_status": "error"}
    job = {"id": "abc123", "slug": "boom", "status": "error"}
    html = render.schedule_detail_page(sched, [], job, slug="boom")
    for verb in ("start", "reset"):  # PLAN-14 14.1: kill entfernt für error (Bug #1)
        assert f'hx-post="/-/ui/schedule/boom/{verb}"' in html
    assert 'hx-post="/-/ui/schedule/boom/kill"' not in html
    assert "#detail" in html


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


def test_action_bar_has_kill_button_for_killed_job():
    job = {"id": "j1", "slug": "x", "status": "killed"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/kill"' in html


def test_action_bar_no_reset_button_for_failed_job():
    job = {"id": "j1", "slug": "x", "status": "failed"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/reset"' not in html
    assert 'hx-post="/-/ui/schedule/x/start"' in html
    assert 'hx-post="/-/ui/schedule/x/kill"' in html


# ── PLAN-14 Stufe 14.2 — START für inactive/zombie/killed (+ error/complete) ──


def test_verbs_start_enabled_for_inactive_zombie_killed():
    for st in ("inactive", "zombie", "killed"):
        assert "start" in render._VERBS_FOR_STATUS[st]


def test_action_bar_has_start_button_for_inactive_job():
    job = {"id": "j1", "slug": "x", "status": "inactive"}
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, [], job, slug="x")
    assert 'hx-post="/-/ui/schedule/x/start"' in html


def test_run_row_has_delete_button():
    runs = [{"id": 9, "status": "complete", "exit_code": 0, "kind": "job"}]
    html = render.schedule_detail_page(
        {"slug": "x", "kind": "job", "trigger": "now"}, runs, slug="x")
    assert 'hx-delete="/-/ui/schedule/x/run/9"' in html


# ── Routen mit gefaktem Client ───────────────────────────────────────────────


class FakeClient:
    def __init__(self, *, schedules=None, journal=None, output=None,
                 jobs=None) -> None:
        self._schedules = schedules or []
        self._journal = journal or []
        self._output = output or {}
        self._jobs = jobs or []
        self.actions: list[tuple] = []
        self.deleted: list[int] = []

    def status(self) -> dict:
        return {}

    def schedules(self) -> list[dict]:
        return self._schedules

    def journal(self, *, slug=None, host=None) -> list[dict]:
        return [j for j in self._journal if slug is None or j.get("slug") == slug]

    def jobs(self, *, status=None) -> list[dict]:
        return self._jobs

    def run_output(self, jid: int) -> dict:
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
        assert "Output ↓" in r.text  # jeder Lauf, auch der jüngste: Toggle-Button


def test_run_output_route_renders(app_with):
    client = FakeClient(output={"id": 7, "kind": "job", "events": [
        {"s": "out", "line": "hallo"}, {"s": "err", "line": "fehler"}]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/run/7/output")
        assert r.status_code == 200
        assert "hallo" in r.text
        assert 'class="err"' in r.text and "fehler" in r.text


def test_run_output_route_renders_claude_tool_use_as_own_paragraph(app_with):
    # PLAN-12 Stufe 12.6 (Verifikation): bereits formatierte Events (Text +
    # gepolsterte Tool-Use-Zeile, wie sie output_format.format_events liefert)
    # rendern über die bestehende Markdown-Kette ohne Änderung an output_block —
    # die Leerzeilen um die Summary lassen _markdown() sie als eigenen <p> setzen.
    client = FakeClient(output={"id": 7, "kind": "claude", "events": [
        {"s": "out", "line": "Ein **fetter** Satz."},
        {"s": "out", "line": ""},
        {"s": "out", "line": "→ Bash: ls -la"},
        {"s": "out", "line": ""},
    ]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/run/7/output")
        assert r.status_code == 200
        assert "<p>Ein <strong>fetter</strong> Satz.</p>" in r.text
        assert "<p>→ Bash: ls -la</p>" in r.text


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
        assert 'id="detail"' in r.text  # re-rendertes Fragment

def test_action_route_rejects_unknown_verb(app_with):
    client = FakeClient(jobs=[{"id": "abc123", "slug": "boom"}])
    with TestClient(app_with(client)) as c:
        assert c.post("/-/ui/schedule/boom/destroy").status_code == 404
        assert client.actions == []


def test_run_delete_route(app_with):
    client = FakeClient(
        schedules=[{"slug": "boom", "kind": "job", "trigger": "now"}],
        jobs=[{"id": "abc123", "slug": "boom"}])
    with TestClient(app_with(client)) as c:
        r = c.request("DELETE", "/-/ui/schedule/boom/run/42")
        assert r.status_code == 200
        assert client.deleted == [42]
        assert 'id="detail"' in r.text


def test_detail_shows_last_run_in_meta_and_live_state_in_panel():
    # „letzter Lauf" (Journal) in der Meta; der aktuelle/aktive Zustand im Live-Block.
    s = {"slug": "witz", "kind": "claude", "trigger": "*/3 * * * *",
         "last_status": "pending", "next_fire_at": None}
    runs = [{"id": 9, "slug": "witz", "status": "error", "exit_code": 1,
             "started_at": 1.0, "finished_at": 2.0, "kind": "claude"}]
    job = {"id": "j1", "slug": "witz", "status": "pending", "next_fire_at": None}
    html = render.schedule_detail_inner(s, runs, job, slug="witz", now=10.0)
    assert "letzter Lauf <b>error</b>" in html          # Journal-Historie in Meta
    assert 'class="live"' in html and "aktiver Lauf" in html  # Live-Block
    assert '<h2>Journal</h2>' in html                    # Journal-Liste bleibt unten


def test_detail_live_panel_for_running_job():
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'class="live"' in html and 'class="st running">running' in html
    assert "noch keine Läufe" in html                    # Journal noch leer


def test_detail_no_live_panel_when_terminal():
    s = {"slug": "a", "kind": "job", "trigger": "now"}
    job = {"id": "j", "slug": "a", "status": "complete", "finished_at": 2.0}
    html = render.schedule_detail_inner(s, [], job, slug="a", now=5.0)
    assert 'class="live"' not in html                    # terminal → kein Live-Block


def test_detail_self_polls_under_follow():
    html = render.schedule_detail_inner({"slug": "a"}, [], None, slug="a", now=1.0)
    assert 'hx-get="/-/ui/schedule/a/detail"' in html
    assert "every 2s [window.bibiFollow]" in html


def test_live_panel_shows_output_expanded():
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    live = {"kind": "job", "events": [{"s": "out", "line": "lebt"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    assert 'class="liveout"' in html and "lebt" in html   # Live-Output inline


def test_run_rows_all_get_toggle_no_auto_inline():
    # User-Feedback: kein Auto-Expand mehr für den jüngsten Lauf — der Output
    # landete optisch nach der kompletten (oft langen) Tabelle, ganz ohne
    # Bezug zur Zeile ("was soll das hier am Ende?"). Jede Zeile bekommt jetzt
    # einheitlich ihren eigenen Toggle, nichts wird automatisch aufgeklappt.
    runs = [{"id": 2, "status": "complete", "kind": "claude", "finished_at": 9.0},
            {"id": 1, "status": "complete", "kind": "claude", "finished_at": 5.0}]
    html = render.schedule_detail_inner({"slug": "a"}, runs, None, slug="a", now=10.0)
    assert 'hx-get="/-/ui/run/1/output"' in html
    assert 'hx-get="/-/ui/run/2/output"' in html


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
    assert "app_url nicht verfügbar" in html


def test_hitl_panel_polls_ungated_when_awaiting():
    """awaiting → #detail-Poll ohne bibiFollow-Gate."""
    job = {"id": "j3", "slug": "a", "status": "awaiting",
           "app_url": "http://localhost:9100/input"}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert "every 2s" in html
    assert "[window.bibiFollow]" not in html.split("every 2s")[0].split("hx-trigger")[-1]
