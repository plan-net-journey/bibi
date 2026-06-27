"""Stufe 4 — Execution-Detail-Screen (lauf-zentriert, Frontend-Plan §C.4).

Ein ``run_id`` (Journal-Zeile) mit Meta + vollem per-Run-Output. Ziel der Feed-/
Journal-/Schedule-Detail-Links (``/-/ui/run/{jid}``). Nutzt die isolierte
per-Run-Datei via ``output_ref``. Backend: GET ``/-/journal/{jid}`` (Metadaten)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app


def _entry(*, jid=1, run_id="Witz:54", slug="Witz", kind="claude", status="complete",
           exit_code=0, started_at=1000.0, finished_at=1012.0, host="mac",
           worker="mac", commit_sha=None, branch=None, reason=None,
           exec_runtime=None) -> dict:
    return {"id": jid, "run_id": run_id, "slug": slug, "kind": kind, "status": status,
            "reason": reason, "started_at": started_at, "finished_at": finished_at,
            "exit_code": exit_code, "exec_runtime": exec_runtime, "host": host,
            "worker": worker, "output_ref": f"data/job/{run_id}/output.jsonl",
            "commit_sha": commit_sha, "branch": branch, "domain": "scheduled"}


# ── pure Renderer ─────────────────────────────────────────────────────────────


def test_execution_detail_meta():
    html = render.execution_detail_page(
        _entry(commit_sha="094df71abcdef", branch="agent/Witz"), events=[], kind="claude")
    assert html.lower().startswith("<!doctype html>")
    assert "Witz:54" in html
    assert 'class="st complete"' in html
    assert "exit 0" in html and "Dauer 12 s" in html
    assert "host mac" in html and "worker mac" in html
    assert "094df71" in html
    assert 'href="/-/ui/schedule/Witz"' in html      # zurück zum Schedule
    assert 'href="/-/ui/feed"' in html               # zurück zum Feed


def test_execution_detail_output_job_preformatted():
    events = [{"t": 1, "s": "out", "line": "hallo"}, {"t": 2, "s": "err", "line": "warn"}]
    html = render.execution_detail_page(_entry(kind="job"), events=events, kind="job")
    assert "hallo" in html and 'class="term"' in html


def test_execution_detail_output_claude_markdown():
    events = [{"t": 1, "s": "out", "line": "# Titel"}]
    html = render.execution_detail_page(_entry(kind="claude"), events=events, kind="claude")
    assert "<h1>Titel</h1>" in html


def test_execution_detail_duration_from_timestamps():
    html = render.execution_detail_page(
        _entry(exec_runtime=None, started_at=100.0, finished_at=109.0), events=[], kind="job")
    assert "Dauer 9 s" in html


def test_execution_detail_escapes_slug():
    html = render.execution_detail_page(_entry(slug="<x>", run_id="r:1"), events=[], kind="job")
    assert "<x>" not in html.replace("/-/ui/schedule/", "")
    assert "&lt;x&gt;" in html


# ── Controller-Route ──────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, entry: dict, output: dict) -> None:
        self._e, self._o = entry, output

    def journal_entry(self, jid: int) -> dict:
        return self._e

    def run_output(self, jid: int) -> dict:
        return self._o


def test_ui_run_detail_route(team_repo: Path):
    client = FakeClient(
        _entry(jid=7, run_id="Witz:54", kind="claude"),
        {"id": 7, "kind": "claude", "events": [{"t": 1, "s": "out", "line": "ein witz"}],
         "output_ref": "data/job/Witz:54/output.jsonl"})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/run/7")
        assert r.status_code == 200
        assert "Witz:54" in r.text and "ein witz" in r.text


# ── Daemon: GET /-/journal/{jid} (Metadaten) ──────────────────────────────────


def _seed_journal_row(run_id: str = "Witz:54") -> int:
    conn = job_db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, archived_at, output_ref, "
            "exit_code) VALUES (?,?,?,?,?,?,?)",
            (run_id, run_id.split(":")[0], "claude", "complete", 1000.0,
             f"data/job/{run_id}/output.jsonl", 0))
        return cur.lastrowid
    finally:
        conn.close()


def test_journal_get_by_id(team_repo: Path):
    jid = _seed_journal_row()
    c = TestClient(create_app(roles.resolve({"scheduler"})))
    r = c.get(f"/-/journal/{jid}")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "Witz:54" and body["status"] == "complete"


def test_journal_get_404(team_repo: Path):
    c = TestClient(create_app(roles.resolve({"scheduler"})))
    assert c.get("/-/journal/99999").status_code == 404
