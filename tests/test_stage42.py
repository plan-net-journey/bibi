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


def _seed_run(root: Path, *, slug: str, kind: str, out_rel: str) -> None:
    p = root / out_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    output.append(p, "out", "hallo welt", t=1.0)
    output.append(p, "err", "ein fehler", t=1.5)
    conn = job_db.connect()
    try:
        job_db.write_local_journal(
            conn, run_id=f"{slug}:1", slug=slug, kind=kind, status="complete",
            exit_code=0, output_ref=out_rel, host="h", worker="w",
            started_at=1.0, finished_at=2.0)
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


def test_journal_output_empty_when_no_ref(sched):
    client, root = sched
    conn = job_db.connect()
    try:
        job_db.write_local_journal(
            conn, run_id="y:1", slug="y", kind="job", status="complete",
            exit_code=0, output_ref=None, host="h", worker="w",
            started_at=1.0, finished_at=2.0)
    finally:
        conn.close()
    jid = client.get("/-/journal").json()[0]["id"]
    r = client.get(f"/-/journal/{jid}/output")
    assert r.status_code == 200
    assert r.json()["events"] == []
