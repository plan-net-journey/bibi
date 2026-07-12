"""LocalPinnedLoop: rollenunabhängiger Sweep + Dispatch für gepinnte Läufe (PLAN-28)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

import bibi.daemon.worker as W
from bibi.daemon import job_db
from bibi.daemon.pinned import LocalPinnedLoop


@pytest.fixture
def db(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    conn = job_db.connect(p)
    yield p, conn
    conn.close()


def _seed(conn, **cols):
    import secrets
    cols.setdefault("id", secrets.token_hex(4))
    cols.setdefault("schedule_ref", f"{cols.get('slug', 'x')}.md")
    cols.setdefault("kind", "job")
    cols.setdefault("payload", "e")
    cols.setdefault("status", "pending")
    cols.setdefault("enqueued_at", time.time())
    names = ", ".join(cols)
    ph = ", ".join(f":{k}" for k in cols)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({ph})", cols)
    return cols["id"]


def _fake_run_wrapper(tmp_path):
    def fake(**kwargs):
        return 0, None, tmp_path / "data" / "job" / "jid" / "output.jsonl", "detached", 999
    return fake


def test_tick_once_dispatches_due_pinned_job(db, tmp_path, monkeypatch):
    p, conn = db
    jid = _seed(conn, slug="mine", next_fire_at=0, pinned_host="mac")
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(tmp_path))
    loop = LocalPinnedLoop(db_path=p, repo_root=tmp_path, host="mac", autorun=False)
    result = loop.tick_once()
    assert result["dispatched"] == 1
    row = job_db.connect(p).execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"


def test_tick_once_ignores_unpinned_team_queue_job(db, tmp_path, monkeypatch):
    p, conn = db
    _seed(conn, slug="team", next_fire_at=0)  # pinned_host bleibt NULL
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(tmp_path))
    loop = LocalPinnedLoop(db_path=p, repo_root=tmp_path, host="mac", autorun=False)
    result = loop.tick_once()
    assert result["dispatched"] == 0
    row = job_db.connect(p).execute("SELECT status FROM jobs WHERE slug='team'").fetchone()
    assert row["status"] == "pending"  # unangetastet


def test_tick_once_ignores_job_pinned_to_other_host(db, tmp_path, monkeypatch):
    p, conn = db
    _seed(conn, slug="theirs", next_fire_at=0, pinned_host="sarasate")
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(tmp_path))
    loop = LocalPinnedLoop(db_path=p, repo_root=tmp_path, host="mac", autorun=False)
    result = loop.tick_once()
    assert result["dispatched"] == 0
    row = job_db.connect(p).execute("SELECT status FROM jobs WHERE slug='theirs'").fetchone()
    assert row["status"] == "pending"


def test_tick_once_sweeps_pinned_failed_job_to_error(db, tmp_path):
    p, conn = db
    now = time.time()
    jid = _seed(conn, slug="exhausted", status="failed", attempt=3, attempts=3,
               next_fire_at=now - 1, pinned_host="mac")
    loop = LocalPinnedLoop(db_path=p, repo_root=tmp_path, host="mac", autorun=False)
    result = loop.tick_once()
    assert result["errored"] == 1
    row = job_db.connect(p).execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "error"


def test_tick_once_noop_when_nothing_due(db, tmp_path):
    p, _conn = db
    loop = LocalPinnedLoop(db_path=p, repo_root=tmp_path, host="mac", autorun=False)
    result = loop.tick_once()
    assert result["dispatched"] == 0
    assert result["errored"] == 0
    assert result["inactivated"] == 0


def test_loop_does_not_tick_immediately_on_start(db, tmp_path, monkeypatch):
    # Rollenunabhängig heißt: dieser Loop läuft jetzt in praktisch jedem Test
    # mit. Ein Sofort-Tick beim Start (wie der teamweite Sweeper es macht)
    # würde per run_in_executor() in einem eigenen Thread sofort job_db.connect()
    # gegen dieselbe frische jobs.sqlite auslösen, mit der ein Test selbst
    # synchron arbeitet — live gefunden: "database is locked" in der Suite.
    # Erst schlafen, dann ticken, verhindert genau das für alle Tests, die
    # (wie praktisch alle) deutlich unter einer Sekunde laufen.
    p, _conn = db
    calls = []
    monkeypatch.setattr(LocalPinnedLoop, "tick_once", lambda self: calls.append(1))
    loop = LocalPinnedLoop(db_path=p, repo_root=tmp_path, host="mac", interval=10.0)

    async def run():
        await loop.start()
        await asyncio.sleep(0.05)  # deutlich kürzer als das Intervall
        await loop.stop()

    asyncio.run(run())
    assert calls == []
