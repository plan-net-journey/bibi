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
    _write(tmp_path / "case" / "daily.md", '---\nschedule: "0 9 * * *"\njob: "claude: x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    sched = job_db.list_schedules(conn)
    assert sched[0]["slug"] == "daily"
    assert sched[0]["trigger"] == "0 9 * * *"
    assert sched[0]["kind"] == "job"
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


def test_reset_increments_fire_and_allows_new_journal_entry(conn, tmp_path: Path):
    # Regression: ohne fire++ beim RESET blockiert der Dedup-Check (run_id, status) den
    # Journal-Eintrag des zweiten Laufs, wenn er denselben Terminal-Status hat.
    _write(tmp_path / "case" / "once.md", '---\nschedule: never\njob: "echo x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = conn.execute("SELECT id FROM jobs WHERE slug='once'").fetchone()["id"]

    # Erster Lauf → error
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed")
    job_db.report_status(conn, jid, status="error")
    fire1 = conn.execute("SELECT fire FROM jobs WHERE id=?", (jid,)).fetchone()["fire"]
    j1 = conn.execute("SELECT run_id, status FROM journal WHERE slug='once' ORDER BY id").fetchall()
    assert len(j1) == 1 and j1[0]["status"] == "error"

    # RESET muss fire++ (neue run_id für den nächsten Lauf)
    job_db.report_status(conn, jid, status="pending")
    fire2 = conn.execute("SELECT fire FROM jobs WHERE id=?", (jid,)).fetchone()["fire"]
    assert fire2 == fire1 + 1, "fire muss beim RESET erhöht werden"

    # Zweiter Lauf → gleicher Terminal-Status (error), muss trotzdem in den Journal
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed")
    job_db.report_status(conn, jid, status="error")
    j2 = conn.execute("SELECT run_id, status FROM journal WHERE slug='once' ORDER BY id").fetchall()
    assert len(j2) == 2, "zweiter Lauf muss eigenen Journal-Eintrag bekommen"
    assert j2[0]["run_id"] != j2[1]["run_id"], "run_id muss sich zwischen den Läufen unterscheiden"

    # schedule_view: last_status soll den letzten Lauf zeigen (error, neuere finished_at)
    scheds = job_db.list_schedules(conn)
    s = next(x for x in scheds if x["slug"] == "once")
    assert s["last_status"] == "error"


# ── PLAN-11.2: last_ping_at + demand ─────────────────────────────────────────


def _insert_job(conn, slug: str = "j") -> str:
    """Minimalen Job-Eintrag direkt einfügen; gibt die ID zurück."""
    import secrets
    job_id = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, slug, f"{slug}.md", "job", "echo hi", "running"),
    )
    return job_id


def test_schema_v11_columns(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "last_ping_at" in cols
    assert "demand" in cols


def test_touch_ping_updates_timestamp(conn):
    jid = _insert_job(conn)
    before = time.time()
    result = job_db.touch_ping(conn, jid)
    after = time.time()
    assert result is True
    ts = conn.execute("SELECT last_ping_at FROM jobs WHERE id=?", (jid,)).fetchone()["last_ping_at"]
    assert before <= ts <= after


def test_touch_ping_returns_false_for_unknown(conn):
    assert job_db.touch_ping(conn, "does-not-exist") is False


def test_set_and_get_demand(conn):
    jid = _insert_job(conn)
    demand = {"input_request": "Wie viele?", "input_format": "number"}
    job_db.set_demand(conn, jid, demand)
    result = job_db.get_demand(conn, jid)
    assert result == demand


def test_get_demand_returns_none_if_not_set(conn):
    jid = _insert_job(conn)
    assert job_db.get_demand(conn, jid) is None


def test_set_demand_overwrites(conn):
    jid = _insert_job(conn)
    job_db.set_demand(conn, jid, {"input_request": "alt", "input_format": "text"})
    job_db.set_demand(conn, jid, {"input_request": "neu", "input_format": "number"})
    assert job_db.get_demand(conn, jid)["input_request"] == "neu"


def test_migration_v10_to_v11(tmp_path: Path):
    """Bestehende v10-DB bekommt last_ping_at + demand per Migration."""
    import sqlite3 as _sqlite3
    p = tmp_path / "old.sqlite"

    # v10-DB manuell aufbauen (ohne die neuen Felder)
    c = _sqlite3.connect(p)
    c.row_factory = _sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
            schedule_ref TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', app_url TEXT
        )
    """)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
              "slug TEXT, kind TEXT, status TEXT, archived_at REAL NOT NULL, "
              "snapshot TEXT NOT NULL DEFAULT '{}', domain TEXT NOT NULL DEFAULT 'scheduled')")
    c.execute("PRAGMA user_version = 10")
    c.commit()
    c.close()

    # connect() soll migrieren
    conn2 = job_db.connect(p)
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(jobs)")}
    assert "last_ping_at" in cols
    assert "demand" in cols
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    conn2.close()
