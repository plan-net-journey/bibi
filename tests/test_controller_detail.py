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


# ── Routen mit gefaktem Client ───────────────────────────────────────────────


class FakeClient:
    def __init__(self, *, schedules=None, journal=None, output=None) -> None:
        self._schedules = schedules or []
        self._journal = journal or []
        self._output = output or {}

    def status(self) -> dict:
        return {}

    def schedules(self) -> list[dict]:
        return self._schedules

    def journal(self, *, slug=None, host=None) -> list[dict]:
        return [j for j in self._journal if slug is None or j.get("slug") == slug]

    def run_output(self, jid: int) -> dict:
        return self._output


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
        assert 'hx-get="/-/ui/run/7/output"' in r.text


def test_run_output_route_renders(app_with):
    client = FakeClient(output={"id": 7, "kind": "job", "events": [
        {"s": "out", "line": "hallo"}, {"s": "err", "line": "fehler"}]})
    with TestClient(app_with(client)) as c:
        r = c.get("/-/ui/run/7/output")
        assert r.status_code == 200
        assert "hallo" in r.text
        assert 'class="err"' in r.text and "fehler" in r.text
