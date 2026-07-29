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


def _seed_local_run(root: Path, *, slug: str = "x", out_rel: str = "data/job/x/output.jsonl",
                    lines: list[tuple[str, str]] | None = None) -> None:
    p = root / out_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if lines is None:
        output.append(p, "out", "hallo welt", t=1.0)
    else:
        for stream, line in lines:
            output.append(p, stream, line)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, started_at, finished_at, "
            "exit_code, host, worker, output_ref, archived_at, domain) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'local')",
            (f"{slug}:1", slug, "job", "complete", 1.0, 2.0, 0, "h", "w", out_rel, 2.0),
        )
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
    assert c.get("/-/run/journal/99999/out").status_code == 404
    assert c.get("/-/run/journal/99999/err").status_code == 404
    assert c.get("/-/run/journal/99999/stream").status_code == 404


def test_run_journal_detail_404_for_scheduled_domain(client_only):
    # Kein Leck disponierter Läufe über diese eigentlich rollenfreie Route —
    # nur domain="local" wird ausgeliefert.
    c, root = client_only
    jid = _seed_scheduled_run(root)
    assert c.get(f"/-/run/journal/{jid}").status_code == 404
    assert c.get(f"/-/run/journal/{jid}/output").status_code == 404
    assert c.get(f"/-/run/journal/{jid}/out").status_code == 404
    assert c.get(f"/-/run/journal/{jid}/err").status_code == 404
    assert c.get(f"/-/run/journal/{jid}/stream").status_code == 404


# ── /-/run/journal/{jid}/out|err|stream (User-Feedback 2026-07-13) ──────────
# PLAN-28: execution_detail_page() unterdrückt für eigene/gepinnte Läufe die
# rohen out/err/stream-Links, weil es dafür bisher keine rollenunabhängige
# Route gab — Analogon zu /-/journal/{jid}/out|err|stream (§4.2/PLAN-14 Stufe
# 14.0), nur über _is_own_run() statt scheduler-gated.


def test_run_journal_out_raw_replays_out_events_only(client_only):
    c, root = client_only
    _seed_local_run(root, slug="x", lines=[("out", "hallo"), ("err", "warnung")])
    jid = c.get("/-/run/journal").json()[0]["id"]
    r = c.get(f"/-/run/journal/{jid}/out")
    assert r.status_code == 200
    assert "hallo" in r.text
    assert "warnung" not in r.text


def test_run_journal_err_raw_replays_err_events_only(client_only):
    c, root = client_only
    _seed_local_run(root, slug="x", lines=[("out", "hallo"), ("err", "warnung")])
    jid = c.get("/-/run/journal").json()[0]["id"]
    r = c.get(f"/-/run/journal/{jid}/err")
    assert r.status_code == 200
    assert "warnung" in r.text
    assert "hallo" not in r.text


def test_run_journal_stream_raw_combines_both_sources(client_only):
    c, root = client_only
    _seed_local_run(root, slug="x", lines=[("out", "hallo"), ("err", "warnung")])
    jid = c.get("/-/run/journal").json()[0]["id"]
    r = c.get(f"/-/run/journal/{jid}/stream")
    assert r.status_code == 200
    assert "hallo" in r.text
    assert "warnung" in r.text


def test_run_journal_detail_works_alongside_scheduler_role(team_repo: Path):
    # Auf einem kombinierten Knoten (z. B. sarasate) muss die Route trotzdem
    # funktionieren — sie ist ein Zusatzangebot, kein Ersatz.
    app = create_app(roles.resolve({"scheduler", "synchronizer", "controller"}))
    with TestClient(app) as c:
        _seed_local_run(team_repo, slug="mein-testjob")
        jid = c.get("/-/run/journal").json()[0]["id"]
        assert c.get(f"/-/run/journal/{jid}").status_code == 200


# ── GET /-/run/journal?slug=... (PLAN-21 Befund 10-Nachtrag) ────────────────


def test_run_journal_list_filters_by_slug(client_only):
    c, root = client_only
    _seed_local_run(root, slug="a", out_rel="data/job/a/output.jsonl")
    _seed_local_run(root, slug="b", out_rel="data/job/b/output.jsonl")
    rows = c.get("/-/run/journal", params={"slug": "a"}).json()
    assert len(rows) == 1 and rows[0]["slug"] == "a"


# ── DELETE /-/run/journal/{jid} (PLAN-21 Befund 10-Nachtrag) ────────────────


def test_run_journal_delete_removes_local_entry(client_only):
    c, root = client_only
    _seed_local_run(root, slug="mein-testjob")
    jid = c.get("/-/run/journal").json()[0]["id"]
    r = c.delete(f"/-/run/journal/{jid}")
    assert r.status_code == 200 and r.json() == {"deleted": jid}
    assert c.get(f"/-/run/journal/{jid}").status_code == 404


def test_run_journal_delete_404_for_unknown_id(client_only):
    c, _ = client_only
    assert c.delete("/-/run/journal/99999").status_code == 404


def test_run_journal_delete_404_for_scheduled_domain(client_only):
    # Kein Leck disponierter Läufe über diese rollenfreie Route — auch beim
    # Löschen nicht: ein Client darf über sie keinen Scheduler-Journal-
    # Eintrag entsorgen können.
    c, root = client_only
    jid = _seed_scheduled_run(root)
    assert c.delete(f"/-/run/journal/{jid}").status_code == 404
    # Und tatsächlich nicht gelöscht — via scheduler-seitiger Route noch da.
    conn = job_db.connect()
    try:
        assert job_db.get_journal(conn, jid) is not None
    finally:
        conn.close()
