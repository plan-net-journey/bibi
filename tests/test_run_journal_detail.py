"""GET /-/run/journal/{jid} + /-/run/journal/{jid}/output (PLAN-21 Befund 10)
— rollenunabhängiges Gegenstück zu /-/journal/{jid} (scheduler-gated, §1.1
gefrorener Vertrag), nur domain="local". Grundlage für die lokale Lauf-
Detail-Seite eines reinen Clients (kein --scheduler nötig). Schnell (kein
echter Subprozess-Lauf, direkter job_db-Seed wie test_stage42.py) — anders
als test_run_local.py, das echte /run-Läufe durchspielt und deshalb
@pytest.mark.slow ist."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app
from bibi.wrapper import output


@pytest.fixture
def client_only(team_repo: Path):
    # Bewusst OHNE scheduler-Rolle — genau der Fall, für den diese Route
    # gebaut wurde (ein reiner Client kann seine eigene Lauf-Historie im
    # Detail sehen, ohne je die scheduler-Rolle zu tragen).
    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        yield c, team_repo


def _seed_local_run(root: Path, *, slug: str = "x", out_rel: str = "data/job/x/output.jsonl") -> None:
    p = root / out_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    output.append(p, "out", "hallo welt", t=1.0)
    conn = job_db.connect()
    try:
        job_db.write_local_journal(
            conn, run_id=f"{slug}:1", slug=slug, kind="job", status="complete",
            exit_code=0, output_ref=out_rel, host="h", worker="w",
            started_at=1.0, finished_at=2.0)
    finally:
        conn.close()


def _seed_scheduled_run(root: Path, *, slug: str = "y") -> int:
    conn = job_db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, started_at, finished_at, "
            "archived_at, domain) VALUES (?,?,?,?,?,?,?,'scheduled')",
            (f"{slug}:1", slug, "job", "complete", 1.0, 2.0, 2.0),
        )
        return cur.lastrowid
    finally:
        conn.close()


def test_run_journal_detail_works_without_scheduler_role(client_only):
    c, root = client_only
    _seed_local_run(root, slug="mein-testjob")
    jid = c.get("/-/run/journal").json()[0]["id"]
    r = c.get(f"/-/run/journal/{jid}")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "mein-testjob" and body["domain"] == "local"


def test_run_journal_detail_output_works_without_scheduler_role(client_only):
    c, root = client_only
    _seed_local_run(root)
    jid = c.get("/-/run/journal").json()[0]["id"]
    r = c.get(f"/-/run/journal/{jid}/output")
    assert r.status_code == 200
    body = r.json()
    assert any(e["s"] == "out" and "hallo welt" in e["line"] for e in body["events"])


def test_run_journal_detail_404_for_unknown_id(client_only):
    c, _ = client_only
    assert c.get("/-/run/journal/99999").status_code == 404
    assert c.get("/-/run/journal/99999/output").status_code == 404


def test_run_journal_detail_404_for_scheduled_domain(client_only):
    # Kein Leck disponierter Läufe über diese eigentlich rollenfreie Route —
    # nur domain="local" wird ausgeliefert.
    c, root = client_only
    jid = _seed_scheduled_run(root)
    assert c.get(f"/-/run/journal/{jid}").status_code == 404
    assert c.get(f"/-/run/journal/{jid}/output").status_code == 404


def test_run_journal_detail_works_alongside_scheduler_role(team_repo: Path):
    # Auf einem kombinierten Knoten (z. B. sarasate) muss die Route trotzdem
    # funktionieren — sie ist ein Zusatzangebot, kein Ersatz.
    app = create_app(roles.resolve({"scheduler", "synchronizer", "controller"}))
    with TestClient(app) as c:
        _seed_local_run(team_repo, slug="mein-testjob")
        jid = c.get("/-/run/journal").json()[0]["id"]
        assert c.get(f"/-/run/journal/{jid}").status_code == 200
