"""``/-/feed/stream`` — SSE-Feed aus dem Journal (Frontend-Plan §C.0).

Backfill der letzten ``n`` terminalen Läufe (**älteste zuerst** → der Client hängt
neue unten an, Konsolen-Tail) + Live-Push bei jedem Journal-Write. Quelle ist das
Journal, nicht der activity-Log. Scheduler-gated (hält die Job-DB)."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app


def _seed_journal(*run_ids: str) -> None:
    conn = job_db.connect()
    try:
        for i, rid in enumerate(run_ids):
            slug = rid.split(":")[0]
            # finished_at == archived_at (wie in der Realität — report_status()
            # setzt beide im selben Terminal-Übergang auf denselben `now`-Wert,
            # PLAN-14 Stufe 14.3 sortiert list_journal() jetzt nach finished_at).
            t = time.time() + i
            conn.execute(
                "INSERT INTO journal (run_id, slug, kind, status, finished_at, "
                "archived_at, output_ref) VALUES (?,?,?,?,?,?,?)",
                (rid, slug, "job", "complete", t, t,
                 f"data/job/{rid}/output.jsonl"),
            )
    finally:
        conn.close()


def _client() -> TestClient:
    return TestClient(create_app(roles.resolve({"scheduler"})))


def test_feed_backfill_snapshot(team_repo: Path):
    _seed_journal("Witz:54", "Backup:7")
    r = _client().get("/-/feed/stream", params={"follow": "false", "n": 10})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "Witz:54" in r.text and "Backup:7" in r.text


def test_feed_backfill_oldest_first(team_repo: Path):
    # neueste unten ⇒ Backfill ältester zuerst (archived_at steigend A<B<C).
    _seed_journal("A:1", "B:2", "C:3")
    r = _client().get("/-/feed/stream", params={"follow": "false"})
    assert r.text.index("A:1") < r.text.index("B:2") < r.text.index("C:3")


def test_feed_backfill_respects_n(team_repo: Path):
    # n schneidet die NEUESTEN n ab (hier C), nicht die ältesten.
    _seed_journal("A:1", "B:2", "C:3")
    r = _client().get("/-/feed/stream", params={"follow": "false", "n": 1})
    assert r.text.count("data: ") == 1 and "C:3" in r.text and "A:1" not in r.text


def test_feed_empty_when_no_journal(team_repo: Path):
    r = _client().get("/-/feed/stream", params={"follow": "false"})
    assert r.status_code == 200 and r.text.strip() == ""


def test_feed_absent_without_scheduler(team_repo: Path):
    c = TestClient(create_app(roles.resolve(set())))  # keine scheduler-Rolle
    assert c.get("/-/feed/stream", params={"follow": "false"}).status_code == 404
