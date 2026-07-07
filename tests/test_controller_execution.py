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
    assert "<td><b>exit_code</b></td><td>0</td>" in html and "Dauer 12 s" in html
    assert "<td><b>host</b></td><td>mac</td>" in html
    assert "<td><b>worker</b></td><td>mac</td>" in html
    assert "094df71" in html
    assert 'href="/-/ui/schedule/Witz"' in html      # zurück zum Schedule
    assert 'href="/-/"' in html                      # zurück zur Home (Schedules)


def test_execution_detail_links_to_raw_journal_stream():
    # Follow-up (User-Feedback): "auch bei archivierten Jobs im Journal eine
    # Möglichkeit, den Original Output zu sehen" — bislang existierte die
    # Route (/-/journal/{jid}/out|err|stream), aber nirgends verlinkt.
    html = render.execution_detail_page(_entry(jid=7), events=[], kind="claude")
    assert 'href="/-/journal/7/stream"' in html


def test_execution_detail_output_job_preformatted():
    events = [{"t": 1, "s": "out", "line": "hallo"}, {"t": 2, "s": "err", "line": "warn"}]
    html = render.execution_detail_page(_entry(kind="job"), events=events, kind="job")
    assert "hallo" in html and 'class="term"' in html


def test_execution_detail_output_claude_uses_same_renderer_as_live():
    # User-Feedback 2026-07-01: archivierter Output muss genauso formatiert sein
    # wie während RUNNING (Uhrzeit-Präfix), nicht über einen separaten Markdown-Renderer.
    events = [{"t": 1, "s": "out", "line": "# Titel"}]
    html = render.execution_detail_page(_entry(kind="claude"), events=events, kind="claude")
    assert "<h1>Titel</h1>" not in html
    assert "# Titel" in html
    assert 'class="lts"' in html


def test_execution_detail_attrs_show_kind_status_and_range_in_one_table():
    # PLAN-21 Befund 9, User-Entscheidung (revidiert 2026-07-01s "breite
    # Summary-Zeile statt Tabelle"): kind/status/Start->Ende jetzt als Zeilen
    # in derselben Attribut-Tabelle, keine separate Kopfzeile mehr.
    html = render.execution_detail_page(_entry(kind="claude", status="complete"),
                                        events=[], kind="claude")
    assert "<td><b>kind</b></td><td>claude</td>" in html
    assert '<td><b>status</b></td><td><span class="st complete">complete</span></td>' in html
    assert "→" in html  # Start -> Ende


def test_execution_detail_attr_table_has_no_duplicate_fields():
    # kind/status/exit_code/host/worker dürfen nur je einmal vorkommen — vorher
    # gab es sie doppelt (separate Summary-Zeile + Attribut-Tabelle), das war
    # der eigentliche Auslöser für PLAN-21 Befund 9.
    html = render.execution_detail_page(
        _entry(kind="claude", status="complete"), events=[], kind="claude")
    attrs_html = html.split("<h2>Output</h2>")[0]
    for key in ("kind", "status", "exit_code", "host", "worker"):
        assert attrs_html.count(f"<td><b>{key}</b></td>") == 1, key


def test_execution_detail_header_has_no_duplicate_bibi_prefix():
    # User-Feedback 2026-07-01: Nav-/Kopfzeile war doppelt ("bibi" im globalen
    # Header + nochmal "bibi ·" vor dem run_id) — jetzt ein bloßes <h1>{run_id}</h1>.
    html = render.execution_detail_page(_entry(run_id="Witz:54"), events=[], kind="claude")
    assert "<h1>bibi ·" not in html
    assert "<h1><span" in html


# ── Konfiguration zu diesem Lauf (journal.snapshot, User-Feedback 2026-07-03) ─


def test_execution_detail_shows_run_config_snapshot():
    # PLAN-21 Befund 9: die eingefrorene Konfiguration hängt jetzt als eigener,
    # per Trennzeile abgesetzter Block an derselben Attribut-Tabelle (statt
    # einer zweiten `<table>` mit eigener "Konfiguration (zu diesem Lauf)"-
    # Überschrift).
    import json
    snap = json.dumps({"schedule_ref": "x.md", "attempts": 5, "backoff": "exponential",
                       "model": "claude-opus-4-8", "schedule": "0 */4 * * *"})
    entry = {**_entry(), "snapshot": snap}
    html = render.execution_detail_page(entry, events=[], kind="claude")
    assert "Konfiguration bei Start" in html
    assert html.count("<table") == 1  # eine Tabelle, kein zweites <table>
    assert "<td><b>attempts</b></td>" in html and "<code>5</code>" in html
    assert "<code>exponential</code>" in html
    assert "<code>claude-opus-4-8</code>" in html


def test_execution_detail_hides_run_config_for_local_domain():
    # /run-Läufe (domain=local) haben keinen Schedule — kein echter Snapshot.
    import json
    snap = json.dumps({"slug": "x", "kind": "job", "status": "complete", "exit_code": 0})
    entry = {**_entry(), "domain": "local", "snapshot": snap}
    html = render.execution_detail_page(entry, events=[], kind="job")
    assert "Konfiguration bei Start" not in html


def test_execution_detail_hides_run_config_when_snapshot_missing():
    # Ältere Journal-Zeilen (vor dem Fix) oder fehlender Snapshot — kein Crash,
    # einfach keine Sektion.
    html = render.execution_detail_page(_entry(), events=[], kind="claude")
    assert "Konfiguration bei Start" not in html


def test_attr_table_no_longer_shows_raw_snapshot_row():
    entry = {**_entry(), "snapshot": '{"schedule_ref": "x.md"}'}
    html = render.execution_detail_page(entry, events=[], kind="claude")
    assert "<td><b>snapshot</b></td>" not in html


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
    def __init__(self, entry: dict, output: dict, *, schedule: dict | None = None) -> None:
        self._e, self._o, self._sched = entry, output, schedule or {}

    def journal_entry(self, jid: int) -> dict:
        return self._e

    def run_output(self, jid: int) -> dict:
        return self._o

    def schedule_config(self, slug: str) -> dict:
        return self._sched


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


def test_ui_run_detail_route_shows_schedule_ref(team_repo: Path):
    # User-Feedback 2026-07-01: schedule_ref fehlte auf der Execution-Detail-Seite
    # (steht nicht im Journal, nur am aktuellen Job) — Live-Lookup per Slug.
    client = FakeClient(
        _entry(jid=7, run_id="Witz:54", slug="Witz", kind="claude"),
        {"id": 7, "kind": "claude", "events": []},
        schedule={"schedule_ref": "20260628.Witz-abc1.md"})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/run/7")
        assert r.status_code == 200
        assert "20260628.Witz-abc1.md" in r.text


def test_ui_run_detail_route_tolerates_missing_schedule_config(team_repo: Path):
    # Schedule inzwischen gelöscht/umbenannt — darf die Seite nicht kaputt machen.
    class _NoScheduleConfigClient(FakeClient):
        def schedule_config(self, slug: str) -> dict:
            raise RuntimeError("boom")

    client = _NoScheduleConfigClient(
        _entry(jid=7, run_id="Witz:54", slug="Witz", kind="claude"),
        {"id": 7, "kind": "claude", "events": []})
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/run/7")
        assert r.status_code == 200
        assert "Witz:54" in r.text


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
