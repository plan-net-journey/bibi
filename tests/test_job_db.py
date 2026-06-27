"""Job-DB: Schema/Migrationen, CRUD, Rescan (DESIGN §5.4/§1.4; PLAN-3 §3.1)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bibi.daemon import job_db


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_schema_version_set(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION


def test_wal_mode(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_tables_exist(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "journal"} <= names


def test_connect_idempotent(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    job_db.connect(p).close()
    c2 = job_db.connect(p)  # zweiter Connect darf nicht crashen
    assert c2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION


# ── compute_next_fire (§5.2) ─────────────────────────────────────────────────


def test_next_fire_now_is_now():
    from bibi.schedule.models import Kind, ScheduleSpec
    s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="echo", schedule="now")
    assert job_db.compute_next_fire(s, now=1000.0) == 1000.0


def test_next_fire_never_and_startup_are_none():
    from bibi.schedule.models import Kind, ScheduleSpec
    for sched in ("never", "startup"):
        s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="e", schedule=sched)
        assert job_db.compute_next_fire(s, now=1000.0) is None


def test_next_fire_cron_advances():
    from bibi.schedule.models import Kind, ScheduleSpec
    s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="e", schedule="0 9 * * *")
    nf = job_db.compute_next_fire(s, now=1000.0)
    assert nf is not None and nf > 1000.0


def test_next_fire_at_parsed():
    from bibi.schedule.models import Kind, ScheduleSpec
    s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="e", at="2099-01-01T00:00:00")
    nf = job_db.compute_next_fire(s, now=1000.0)
    assert nf is not None and nf > 1000.0


# ── rescan ────────────────────────────────────────────────────────────────


def test_rescan_inserts_then_lists(conn, tmp_path: Path):
    _write(tmp_path / "case" / "hello" / "README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert res["inserted"] == 1 and res["updated"] == 0 and res["removed"] == 0
    jobs = job_db.list_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["slug"] == "hello"
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["kind"] == "job"
    assert len(jobs[0]["id"]) == 8  # Hash-ID (§4.4)


def test_rescan_update_preserves_id_and_status(conn, tmp_path: Path):
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: now\njob: "echo a"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = job_db.list_jobs(conn)[0]["id"]
    # Status simuliert weiter (running) — rescan darf ihn nicht zurücksetzen
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,)); conn.commit()
    _write(md, '---\nschedule: now\njob: "echo b"\npriority: 5\n---\n')
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert res["updated"] == 1 and res["inserted"] == 0
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["payload"] == "echo b"    # Spec aktualisiert
    assert row["priority"] == 5
    assert row["status"] == "running"    # Live-Status erhalten
    assert job_db.list_jobs(conn)[0]["id"] == jid  # gleiche ID


def test_rescan_removes_vanished(conn, tmp_path: Path):
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: now\njob: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    md.unlink()
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert res["removed"] == 1
    assert job_db.list_jobs(conn) == []


def test_rescan_reports_errors_and_collisions(conn, tmp_path: Path):
    _write(tmp_path / "case" / "bad.md", '---\nschedule: "broken"\njob: "x"\n---\n')
    _write(tmp_path / "case" / "a.md", '---\nslug: dup\nschedule: now\njob: "x"\n---\n')
    _write(tmp_path / "case" / "b.md", '---\nslug: dup\nschedule: now\njob: "y"\n---\n')
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert any("cron" in e["error"].lower() for e in res["errors"])
    assert res["collisions"][0]["slug"] == "dup"
    assert job_db.list_jobs(conn) == []  # weder Fehler noch Kollisionen eingefügt


def test_list_jobs_status_filter_and_get(conn, tmp_path: Path):
    _write(tmp_path / "case" / "a" / "README.md", '---\nschedule: now\njob: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = job_db.list_jobs(conn, status="pending")[0]["id"]
    assert job_db.list_jobs(conn, status="running") == []
    assert job_db.get_job(conn, jid)["slug"] == "a"
    assert job_db.get_job(conn, "nope") is None


def test_schedule_view_trigger_and_status(conn, tmp_path: Path):
    _write(tmp_path / "case" / "daily.md", '---\nschedule: "0 9 * * *"\nclaude: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    sched = job_db.list_schedules(conn)
    assert sched[0]["slug"] == "daily"
    assert sched[0]["trigger"] == "0 9 * * *"
    assert sched[0]["kind"] == "claude"
    assert sched[0]["last_status"] == "pending"   # nie gelaufen → Zeilen-Status


def test_schedule_list_status_is_last_run_not_rearmed_pending(conn, tmp_path: Path):
    # Ein wiederkehrender Job re-armt nach `complete` sofort zu `pending`. Die Liste
    # soll dennoch den **letzten Lauf** (complete) zeigen, nicht den Zeilen-Status.
    _write(tmp_path / "case" / "rec.md", '---\nschedule: "0 9 * * *"\njob: "echo x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    conn.execute("UPDATE jobs SET next_fire_at=1.0 WHERE slug='rec'")  # fällig machen
    res = job_db.reserve_next(conn, worker="w", host="h")
    job_db.report_status(conn, res["id"], status="complete", exit_code=0,
                         branch="agent/rec", commit_sha="a" * 40)
    sched = next(s for s in job_db.list_schedules(conn) if s["slug"] == "rec")
    assert sched["row_status"] == "pending"      # re-armt
    assert sched["last_status"] == "complete"    # aber letzter Lauf: complete
    assert sched["last_run_at"] is not None
