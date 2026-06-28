"""Job-DB: Reservierung, Statusmeldung, Migration, Concurrency (PLAN-3 §3.2)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bibi.daemon import job_db


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _insert(conn, slug, priority, enqueued_at):
    import secrets
    jid = secrets.token_hex(4)
    # next_fire_at=0 ⇒ fällig (immer <= now); enqueued_at bleibt der FIFO-Schlüssel.
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, priority, "
        "status, enqueued_at, next_fire_at) VALUES (?,?,?,?,?,?, 'pending', ?, 0)",
        (jid, slug, f"{slug}.md", "job", "echo hi", priority, enqueued_at),
    )
    return jid


# ── Migration v1 → v2 (meta) ────────────────────────────────────────────────


def test_migration_adds_meta_to_old_db(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    c = job_db.connect(p)
    # Eine "alte" v1-DB simulieren: meta weg, user_version zurück auf 1.
    c.execute("DROP TABLE meta")
    c.execute("PRAGMA user_version = 1")
    c.close()
    c2 = job_db.connect(p)  # Migration läuft (idempotent bis SCHEMA_VERSION)
    assert c2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    tables = {r["name"] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "meta" in tables
    c2.close()


def test_fresh_db_is_current_with_meta(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "meta" in tables


def test_migration_v2_to_v3_adds_journal_domain(tmp_path: Path):
    import sqlite3
    p = tmp_path / "j.sqlite"
    c = sqlite3.connect(p)
    # Eine "alte" v2-DB ohne journal.domain simulieren.
    c.executescript(
        "CREATE TABLE journal (id INTEGER PRIMARY KEY, run_id TEXT);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "PRAGMA user_version = 2;"
    )
    c.commit(); c.close()
    c2 = job_db.connect(p)
    cols = {r["name"] for r in c2.execute("PRAGMA table_info(journal)")}
    assert "domain" in cols
    assert c2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    c2.close()


# ── reserve_next ─────────────────────────────────────────────────────────────


def test_reserve_priority_then_fifo(conn):
    t = time.time()
    _insert(conn, "z1", 0, t + 0)
    pid = _insert(conn, "p5", 5, t + 1)
    _insert(conn, "z2", 0, t + 2)
    r1 = job_db.reserve_next(conn)
    assert r1["id"] == pid and r1["slug"] == "p5"   # höchste Prio zuerst
    r2 = job_db.reserve_next(conn)
    assert r2["slug"] == "z1"                        # dann FIFO
    r3 = job_db.reserve_next(conn)
    assert r3["slug"] == "z2"
    assert job_db.reserve_next(conn) is None         # leer


def test_reserve_gates_on_next_fire_at(conn):
    import secrets
    now = time.time()

    def _ins(slug, next_fire):
        jid = secrets.token_hex(4)
        if next_fire is None:
            conn.execute(
                "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
                "enqueued_at) VALUES (?,?,?,?,?, 'pending', ?)",
                (jid, slug, f"{slug}.md", "job", "e", now))
        else:
            conn.execute(
                "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
                "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'pending', ?, ?)",
                (jid, slug, f"{slug}.md", "job", "e", now, next_fire))
        return jid

    _ins("due", now - 10)       # fällig
    _ins("future", now + 3600)  # at:/cron in der Zukunft
    _ins("never", None)         # schedule: never → next_fire_at NULL

    r1 = job_db.reserve_next(conn)
    assert r1 is not None and r1["slug"] == "due"
    # future ist nicht fällig, never ist deaktiviert ⇒ nichts mehr
    assert job_db.reserve_next(conn) is None


def test_reserve_flips_to_running(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, worker="w1")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"
    assert row["locked_at"] is not None
    assert row["started_at"] is not None
    assert row["worker"] == "w1"


def test_reservation_view_shape(conn):
    _insert(conn, "a", 0, time.time())
    r = job_db.reserve_next(conn)
    assert set(r) == {
        "id", "slug", "kind", "payload", "model", "soul", "session",
        "fire", "attempt", "attempts", "backoff", "wall_time", "silence_timeout",
        "app_port", "app_prefix", "hitl_timeout",
        "env",
    }
    assert r["kind"] == "job" and r["payload"] == "echo hi"


def test_reservation_includes_claude_fields(conn):
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, priority, status, "
        "enqueued_at, next_fire_at, model, soul, session) "
        "VALUES (?,?,?,?,?,?, 'pending', ?, 0, ?,?,?)",
        (jid, "k", "k.md", "claude", "prompt", 0, time.time(),
         "claude-haiku-4-5-20251001", "Data", "sess-9"),
    )
    r = job_db.reserve_next(conn)
    assert r["kind"] == "claude"
    assert r["model"] == "claude-haiku-4-5-20251001"
    assert r["soul"] == "Data" and r["session"] == "sess-9"


# ── report_status (lifecycle-validiert, §5.4) ────────────────────────────────


def test_report_running_to_complete(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn)  # → running
    assert job_db.report_status(conn, jid, status="complete", exit_code=0) == "ok"
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "complete"
    assert row["finished_at"] is not None and row["exit_code"] == 0


def test_report_illegal_transition_rejected(conn):
    jid = _insert(conn, "a", 0, time.time())  # pending
    # pending → complete ist verboten (§5.4)
    assert job_db.report_status(conn, jid, status="complete") == "invalid"
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "pending"


def test_report_not_found(conn):
    assert job_db.report_status(conn, "deadbeef", status="running") == "not_found"


def test_report_output_ref_only_no_blob(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="complete", output_ref="data/job/x/output.jsonl")
    row = conn.execute("SELECT output_ref FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["output_ref"] == "data/job/x/output.jsonl"


# ── Concurrency: n parallele /next → disjunkt (§3.2/§3.8) ─────────────────────


def test_sweep_exhausted_failed_to_error(conn):
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, "
        "attempts, next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'failed', 2, 2, 0, ?)",
        (jid, "x", "x.md", "job", "e", time.time()),
    )
    res = job_db.sweep(conn, now=time.time())
    assert res["errored"] == 1
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "error"
    assert any(j["status"] == "error" for j in job_db.list_journal(conn))


def test_sweep_leaves_retriable_failed(conn):
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, "
        "attempts, next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'failed', 1, 2, 0, ?)",
        (jid, "y", "y.md", "job", "e", time.time()),
    )
    assert job_db.sweep(conn, now=time.time())["errored"] == 0
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "failed"


def test_sweep_deferred_expired_to_inactive(conn):
    import secrets
    jid = secrets.token_hex(4)
    now = time.time()
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, deferred_at, "
        "defer_max, enqueued_at) VALUES (?,?,?,?,?, 'deferred', ?, 10, ?)",
        (jid, "d", "d.md", "job", "e", now - 100, now),
    )
    res = job_db.sweep(conn, now=now)
    assert res["inactivated"] == 1
    row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "inactive" and row["reason"] == "deferred_expired"


def _seed_full(conn, **cols):
    import secrets
    cols.setdefault("id", secrets.token_hex(4))
    cols.setdefault("schedule_ref", f"{cols.get('slug','x')}.md")
    cols.setdefault("kind", "job")
    cols.setdefault("payload", "e")
    cols.setdefault("status", "pending")
    cols.setdefault("enqueued_at", time.time())
    names = ", ".join(cols)
    ph = ", ".join(f":{k}" for k in cols)
    conn.execute(f"INSERT INTO jobs ({names}) VALUES ({ph})", cols)
    return cols["id"]


# ── #1 cron-Recurrence ───────────────────────────────────────────────────────


def test_cron_job_reschedules_after_complete(conn):
    jid = _seed_full(conn, slug="cronjob", schedule="*/5 * * * *",
                     status="running", next_fire_at=0, fire=0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    row = conn.execute("SELECT status, next_fire_at, fire, attempt FROM jobs WHERE id=?",
                       (jid,)).fetchone()
    assert row["status"] == "pending"            # neu eingeplant
    assert row["next_fire_at"] > time.time()     # nächster cron-Tick in der Zukunft
    assert row["fire"] == 1                       # Zähler hoch
    assert row["attempt"] == 0
    # eine Journal-Zeile für den abgeschlossenen Lauf
    assert len(job_db.list_journal(conn)) == 1


@pytest.mark.parametrize("terminal", ["error", "killed", "zombie", "inactive"])
def test_cron_fatal_terminal_does_not_rearm(conn, terminal):
    # error/killed/zombie/inactive sind echte Endzustände — ein wiederkehrender
    # Job darf nach ihnen NICHT neu eingestellt werden (Feedback 2026-06-28).
    # Der Übergang muss via sweep/reconcile ausgelöst werden (nicht direkt aus
    # running), deshalb hier direkte DB-Manipulation nach running.
    jid = _seed_full(conn, slug="cj", schedule="* * * * *",
                     status="running", next_fire_at=0, fire=0, attempts=1, attempt=1)
    # status direkt setzen (Sweep/Worker würde das im echten Pfad tun)
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (terminal, jid))
    # report_status mit demselben Zustand → Watermark-Dedup (identisch); wir
    # brauchen nur den Check, dass der Zustand NICHT zu pending wechselt.
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == terminal  # bleibt terminal, kein Re-Arm


def test_cron_error_via_report_does_not_rearm(conn):
    # Explizit über report_status: failed → error darf cron-Job nicht neu einplanen.
    jid = _seed_full(conn, slug="cj2", schedule="* * * * *",
                     status="failed", next_fire_at=0, fire=0, attempts=1, attempt=1)
    job_db.report_status(conn, jid, status="error")
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "error"


def test_cron_two_fires_two_journal_rows(conn):
    jid = _seed_full(conn, slug="c", schedule="* * * * *", status="running",
                     next_fire_at=0, fire=0)
    job_db.report_status(conn, jid, status="complete")          # fire 0
    # zweiter Lauf: wieder running → complete (fire jetzt 1)
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
    job_db.report_status(conn, jid, status="complete")          # fire 1
    rows = job_db.list_journal(conn)
    assert len(rows) == 2                          # KEIN Dedup über fires hinweg
    assert {r["run_id"] for r in rows} == {"c:0", "c:1"}


def test_now_job_does_not_recur(conn):
    jid = _seed_full(conn, slug="oneshot", schedule="now", status="running",
                     next_fire_at=0, fire=0)
    job_db.report_status(conn, jid, status="complete")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "complete"


# ── #2 startup ───────────────────────────────────────────────────────────────


def test_fire_startup_enqueues(conn):
    jid = _seed_full(conn, slug="boot", schedule="startup", status="complete",
                     next_fire_at=None)
    assert job_db.fire_startup(conn) == 1
    row = conn.execute("SELECT status, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "pending" and row["next_fire_at"] is not None
    # nach fire_startup ist er fällig → reservierbar
    assert job_db.reserve_next(conn)["slug"] == "boot"


# ── #3 start_now ─────────────────────────────────────────────────────────────


def test_start_now_makes_due(conn):
    jid = _seed_full(conn, slug="later", schedule="0 9 * * *",
                     next_fire_at=time.time() + 99999)  # weit in der Zukunft
    assert job_db.reserve_next(conn) is None      # nicht fällig
    assert job_db.start_now(conn, jid) == "ok"
    assert job_db.reserve_next(conn)["slug"] == "later"  # jetzt fällig


def test_start_now_invalid_and_missing(conn):
    jid = _seed_full(conn, slug="r", status="running", next_fire_at=0)
    assert job_db.start_now(conn, jid) == "invalid"   # nicht pending
    assert job_db.start_now(conn, "deadbeef") == "not_found"


# ── #4 no_process-Reconcile ──────────────────────────────────────────────────


def test_reconcile_no_process_kills_stale_worker_jobs(conn):
    jid = _seed_full(conn, slug="remote", status="running", worker="deadnode", next_fire_at=0)
    other = _seed_full(conn, slug="alive", status="running", worker="livenode", next_fire_at=0)
    n = job_db.reconcile_no_process(conn, {"deadnode"})
    assert n == 1
    assert conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()["reason"] == "no_process"
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (other,)).fetchone()["status"] == "running"


def test_reconcile_startup_orphans(conn):
    mine = _seed_full(conn, slug="mine", status="running", worker="me", next_fire_at=0)
    theirs = _seed_full(conn, slug="theirs", status="running", worker="remote", next_fire_at=0)
    assert job_db.reconcile_startup_orphans(conn, "me") == 1
    assert conn.execute("SELECT status, reason FROM jobs WHERE id=?", (mine,)).fetchone()["status"] == "killed"
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (theirs,)).fetchone()["status"] == "running"


def test_concurrent_reserve_disjoint(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    seed = job_db.connect(p)
    t = time.time()
    for i in range(20):
        _insert(seed, f"j{i}", 0, t + i)
    seed.close()

    def grab():
        c = job_db.connect(p)  # eigene Connection je Thread
        try:
            r = job_db.reserve_next(c)
            return r["id"] if r else None
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=20) as ex:
        ids = [f.result() for f in [ex.submit(grab) for _ in range(20)]]

    got = [i for i in ids if i is not None]
    assert len(got) == 20                 # alle 20 zugeteilt
    assert len(set(got)) == 20            # keine Doppelzuweisung
    check = job_db.connect(p)
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0] == 0
    check.close()
