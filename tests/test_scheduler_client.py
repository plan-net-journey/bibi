"""LocalScheduler/RemoteScheduler — Auswahl-/Melde-Pfad (PLAN-3 §3.6; PLAN-28)."""

from __future__ import annotations

import time
from pathlib import Path

from bibi.daemon import job_db
from bibi.daemon.scheduler_client import LocalScheduler


def _seed(conn, slug: str, *, pinned_host: str | None = None) -> str:
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
        "pinned_host, next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'pending', ?, 0, ?)",
        (jid, slug, f"{slug}.md", "job", "echo hi", pinned_host, time.time()),
    )
    return jid


def test_local_scheduler_default_ignores_pinned_only_flag(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    conn = job_db.connect(p)
    _seed(conn, "team")  # pinned_host=NULL
    conn.close()
    sched = LocalScheduler(p)
    res = sched.next(host="mac")
    assert res is not None and res["slug"] == "team"


def test_local_scheduler_pinned_only_skips_unpinned_job(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    conn = job_db.connect(p)
    _seed(conn, "team")  # pinned_host=NULL
    conn.close()
    sched = LocalScheduler(p, pinned_only=True)
    assert sched.next(host="mac") is None


def test_local_scheduler_pinned_only_reserves_matching_host(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    conn = job_db.connect(p)
    _seed(conn, "mine", pinned_host="mac")
    conn.close()
    sched = LocalScheduler(p, pinned_only=True)
    res = sched.next(host="mac")
    assert res is not None and res["slug"] == "mine"


def test_local_scheduler_pinned_only_skips_other_host(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    conn = job_db.connect(p)
    _seed(conn, "theirs", pinned_host="sarasate")
    conn.close()
    sched = LocalScheduler(p, pinned_only=True)
    assert sched.next(host="mac") is None
