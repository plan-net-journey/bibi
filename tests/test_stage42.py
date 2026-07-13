"""Stufe 4.2 (Engine-Anteil) — Output-Replay je Lauf: GET /-/journal/{id}/output
(PLAN-4 §4.2/§2.5). Die output.jsonl wird als **getypte Events** ausgeliefert,
Replay-Quelle für die Detail-Sicht des Controllers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app
from bibi.wrapper import output


@pytest.fixture
def sched(team_repo: Path):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as client:
        yield client, team_repo


def _seed_run(root: Path, *, slug: str, kind: str, out_rel: str,
              payload: str | None = None, lines: list[tuple[str, str]] | None = None) -> None:
    p = root / out_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if lines is None:
        output.append(p, "out", "hallo welt", t=1.0)
        output.append(p, "err", "ein fehler", t=1.5)
    else:
        for stream, line in lines:
            output.append(p, stream, line)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, started_at, finished_at, "
            "exit_code, host, worker, output_ref, payload, archived_at, domain) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'local')",
            (f"{slug}:1", slug, kind, "complete", 1.0, 2.0, 0, "h", "w", out_rel, payload, 2.0),
        )
    finally:
        conn.close()


def test_journal_output_replays_typed_events(sched):
    client, root = sched
    _seed_run(root, slug="x", kind="job", out_rel="data/job/abcd/output.jsonl")
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/output")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "job"
    ev = body["events"]
    assert any(e["s"] == "out" and "hallo welt" in e["line"] for e in ev)
    assert any(e["s"] == "err" and "ein fehler" in e["line"] for e in ev)


def test_journal_output_404(sched):
    client, _ = sched
    assert client.get("/-/journal/99999/output").status_code == 404


def test_journal_output_formats_claude_stream_json(sched):
    # PLAN-12 Stufe 12.5: claude:-Payload → effektiver kind="claude", der
    # Ausgabefilter formatiert die rohen stream-json-Zeilen zu Klartext.
    import json as _json
    client, root = sched
    raw = _json.dumps({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": "Hallo!"}]}})
    _seed_run(root, slug="c", kind="job", out_rel="data/job/c1/output.jsonl",
             payload="claude: tu was", lines=[("out", raw)])
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/output")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "claude"
    text = [e["line"] for e in body["events"]]
    assert "Hallo!" in text
    assert not any(ln.startswith("{") for ln in text)


def test_journal_output_empty_when_no_ref(sched):
    client, root = sched
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, started_at, finished_at, "
            "exit_code, host, worker, archived_at, domain) VALUES (?,?,?,?,?,?,?,?,?,?,'local')",
            ("y:1", "y", "job", "complete", 1.0, 2.0, 0, "h", "w", 2.0),
        )
    finally:
        conn.close()
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/output")
    assert r.status_code == 200
    assert r.json()["events"] == []


# ── PLAN-14 Stufe 14.0 — rohe out/err/stream-Routen für archivierte Läufe ────
# Analog zu /-/job/{id}/out|err|stream (worker-gated, nur laufender Job), aber
# über journal.output_ref aufgelöst — deckt die Lücke: bislang gab es für
# archivierte Läufe nur den formatierten /output-Endpunkt, keinen rohen Zugriff.


def test_journal_out_raw_replays_out_events_only(sched):
    client, root = sched
    _seed_run(root, slug="x", kind="job", out_rel="data/job/abcd/output.jsonl",
              lines=[("out", "hallo"), ("err", "warnung")])
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/out")
    assert r.status_code == 200
    assert "hallo" in r.text
    assert "warnung" not in r.text


def test_journal_err_raw_replays_err_events_only(sched):
    client, root = sched
    _seed_run(root, slug="x", kind="job", out_rel="data/job/abcd/output.jsonl",
              lines=[("out", "hallo"), ("err", "warnung")])
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/err")
    assert r.status_code == 200
    assert "warnung" in r.text
    assert "hallo" not in r.text


def test_journal_stream_raw_combines_both_sources(sched):
    client, root = sched
    _seed_run(root, slug="x", kind="job", out_rel="data/job/abcd/output.jsonl",
              lines=[("out", "hallo"), ("err", "warnung")])
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/stream")
    assert r.status_code == 200
    assert "hallo" in r.text
    assert "warnung" in r.text


def test_journal_out_404_for_unknown_id(sched):
    client, _ = sched
    assert client.get("/-/journal/99999/out").status_code == 404
    assert client.get("/-/journal/99999/err").status_code == 404
    assert client.get("/-/journal/99999/stream").status_code == 404


def test_journal_out_empty_when_no_ref(sched):
    client, root = sched
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, started_at, finished_at, "
            "exit_code, host, worker, archived_at, domain) VALUES (?,?,?,?,?,?,?,?,?,?,'local')",
            ("z:1", "z", "job", "complete", 1.0, 2.0, 0, "h", "w", 2.0),
        )
    finally:
        conn.close()
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/out")
    assert r.status_code == 200
    assert r.text.strip() == ""
