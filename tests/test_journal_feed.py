"""Stufe 0 — Journal-Feed-Broadcast: Listener-Hook in job_db (Frontend-Plan §C.0).

Der Feed speist sich aus dem **Journal** (nicht dem Log). Damit `job_db` vom
Daemon-Broadcaster entkoppelt bleibt, ruft `_write_journal`/`write_local_journal`
einen optionalen, vom Daemon injizierten Listener mit der frischen journal_view-Zeile.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

import pytest

from bibi.daemon import job_db


def _db(tmp_path: Path) -> Path:
    return tmp_path / "jobs.sqlite"


def _seed_running(conn, slug: str = "r") -> str:
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, enqueued_at, "
        "started_at) VALUES (?,?,?,?,?, 'running', ?, ?)",
        (jid, slug, f"{slug}.md", "job", "echo", time.time(), time.time()),
    )
    return jid


@pytest.fixture(autouse=True)
def _reset_listener():
    yield
    job_db.set_journal_listener(None)  # globaler Hook: nach jedem Test säubern


def test_listener_fires_on_terminal_journal(tmp_path: Path):
    conn = job_db.connect(_db(tmp_path))
    jid = _seed_running(conn)
    seen: list[dict] = []
    job_db.set_journal_listener(seen.append)
    res = job_db.report_status(conn, jid, status="complete", exit_code=0,
                               output_ref="data/job/r:0/output.jsonl")
    conn.close()
    assert res == "ok"
    assert len(seen) == 1
    row = seen[0]
    assert row["slug"] == "r" and row["status"] == "complete"
    assert row["run_id"] == "r:0"  # slug:fire
    assert row["output_ref"] == "data/job/r:0/output.jsonl"


def test_listener_dedups_no_double_fire(tmp_path: Path):
    # Zweiter identischer Terminal-Report → kein neuer Journal-Insert → kein Notify.
    conn = job_db.connect(_db(tmp_path))
    jid = _seed_running(conn)
    seen: list[dict] = []
    job_db.set_journal_listener(seen.append)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    conn.close()
    assert len(seen) == 1


def test_local_journal_notifies(tmp_path: Path):
    # /run schreibt ebenfalls eine Journal-Zeile (domain=local) → Feed-Push.
    conn = job_db.connect(_db(tmp_path))
    seen: list[dict] = []
    job_db.set_journal_listener(seen.append)
    job_db.write_local_journal(
        conn, run_id="adhoc:ab12", slug="adhoc", kind="job", status="complete",
        exit_code=0, output_ref="data/job/adhoc:ab12/output.jsonl", host="h",
        worker="local", started_at=time.time(), finished_at=time.time(),
    )
    conn.close()
    assert len(seen) == 1 and seen[0]["domain"] == "local"


def test_no_listener_is_noop(tmp_path: Path):
    # Ohne Listener (Default None) darf der Status-Pfad normal durchlaufen.
    conn = job_db.connect(_db(tmp_path))
    jid = _seed_running(conn)
    assert job_db.report_status(conn, jid, status="complete", exit_code=0) == "ok"
    conn.close()
