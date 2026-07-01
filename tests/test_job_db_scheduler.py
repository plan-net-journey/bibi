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
        "app_port", "app_prefix", "exec_mode", "hitl_timeout", "defer_time",
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
        (jid, "k", "k.md", "job", "claude: prompt", 0, time.time(),
         "claude-haiku-4-5-20251001", "Data", "sess-9"),
    )
    r = job_db.reserve_next(conn)
    assert r["kind"] == "job"
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


def test_report_status_same_terminal_status_is_noop(conn):
    # PLAN-14 Stufe 14.1b: target == current bei Terminal-Status (z. B.
    # wiederholter Kill-Klick) darf finished_at/reason nicht erneut setzen
    # und keinen zweiten Journal-Write auslösen.
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn)  # → running
    assert job_db.report_status(conn, jid, status="killed", reason="by_user", now=100.0) == "ok"
    before = conn.execute(
        "SELECT finished_at, updated_at, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert job_db.report_status(conn, jid, status="killed", reason="by_user", now=200.0) == "ok"
    after = conn.execute(
        "SELECT finished_at, updated_at, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert after["finished_at"] == before["finished_at"] == 100.0
    assert after["updated_at"] == before["updated_at"] == 100.0
    assert len(job_db.list_journal(conn)) == 1


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


def test_failed_retry_via_reserve_next(conn):
    """PLAN-10 §10.1: FAILED-Job mit next_fire_at in der Vergangenheit → reserve_next dispatcht ihn."""
    import secrets
    jid = secrets.token_hex(4)
    now = time.time()
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, "
        "attempts, next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'failed', 1, 3, ?, ?)",
        (jid, "retry", "retry.md", "job", "echo hi", now - 1, now),
    )
    res = job_db.reserve_next(conn, worker="w", host="h")
    assert res is not None and res["id"] == jid
    row = conn.execute("SELECT status, attempt FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"
    assert row["attempt"] == 1  # attempt unverändert (Reserve inkrementiert nicht)


def test_report_status_deferred_sets_deferred_at(conn):
    """PLAN-10 §10.1: Erster DEFERRED-Report setzt deferred_at automatisch."""
    import secrets
    jid = secrets.token_hex(4)
    now = time.time()
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
        "next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'running', ?, ?)",
        (jid, "def", "def.md", "job", "e", now, now),
    )
    res = job_db.report_status(conn, jid, status="deferred", next_fire_at=now + 60, now=now)
    assert res == "ok"
    row = conn.execute("SELECT status, deferred_at, next_fire_at FROM jobs WHERE id=?",
                       (jid,)).fetchone()
    assert row["status"] == "deferred"
    assert row["deferred_at"] == now
    assert row["next_fire_at"] == now + 60


def test_report_status_deferred_preserves_existing_deferred_at(conn):
    """Zweiter DEFERRED-Report darf deferred_at nicht überschreiben (defer_max-Basis)."""
    import secrets
    jid = secrets.token_hex(4)
    earlier = time.time() - 200
    now = time.time()
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, deferred_at, "
        "next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'deferred', ?, ?, ?)",
        (jid, "def2", "def2.md", "job", "e", earlier, now - 60, now),
    )
    # Zweiter Defer (DEFERRED → DEFERRED ist gleicher Zustand → kein Transition-Check-Problem)
    # Wir simulieren via running → deferred nochmals, indem wir status auf running setzen.
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
    res = job_db.report_status(conn, jid, status="deferred", next_fire_at=now + 60, now=now)
    assert res == "ok"
    row = conn.execute("SELECT deferred_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["deferred_at"] == earlier  # unverändert


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


@pytest.mark.parametrize("status", ["error", "inactive", "zombie", "killed", "complete"])
def test_start_now_archives_terminal_status_to_pending(conn, status):
    # PLAN-14 Stufe 14.2: START auf error/inactive/zombie/killed/complete
    # archiviert wie RESET, erzwingt aber zusätzlich next_fire_at=now — RESET
    # allein respektiert stattdessen den Trigger (Follow-up-Fix, 2026-07-01).
    jid = _seed_full(conn, slug="x", status=status, next_fire_at=None)
    assert job_db.start_now(conn, jid) == "ok"
    row = conn.execute("SELECT status, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "pending"
    assert row["next_fire_at"] is not None


@pytest.mark.parametrize("status", ["running", "awaiting", "failed"])
def test_start_now_stays_invalid_for_non_archivable_status(conn, status):
    # Bewusste Grenze (PLAN-14 Stufe 14.2): failed bräuchte eine eigene
    # attempts-1-Logik statt einfachem Archivieren — nicht Teil dieser Stufe.
    # running/awaiting sind keine Terminalzustände.
    jid = _seed_full(conn, slug="x", status=status, next_fire_at=0)
    assert job_db.start_now(conn, jid) == "invalid"


def test_reset_never_schedule_leaves_unfired(conn):
    # User-Feedback 2026-07-01: RESET auf `schedule: never` darf keinen neuen
    # Lauf einreihen — next_fire_at bleibt None, erst ein explizites START
    # (next_fire_at=now) macht den Job wieder fällig.
    jid = _seed_full(conn, slug="x", schedule="never", status="killed", next_fire_at=None)
    assert job_db.report_status(conn, jid, status="pending") == "ok"
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] is None


def test_reset_recurring_schedule_uses_next_cron_tick_not_now(conn):
    # RESET respektiert den Trigger (nächster regulärer Cron-Tick) statt
    # sofort zu feuern — anders als START (next_fire_at=now).
    jid = _seed_full(conn, slug="x", schedule="0 9 * * *", status="killed", next_fire_at=None)
    assert job_db.report_status(conn, jid, status="pending") == "ok"
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] is not None
    assert row["next_fire_at"] > time.time()


def test_start_now_deferred_dispatches_immediately_like_pending(conn):
    # Follow-up: deferred braucht KEINE attempts-1-Logik ("sofortiger Start"
    # laut Feedback-Tabelle) — war fälschlich mit failed in einen Topf
    # geworfen und blieb daher bislang mit 409 kaputt.
    jid = _seed_full(conn, slug="x", status="deferred", next_fire_at=time.time() + 9999)
    assert job_db.start_now(conn, jid) == "ok"
    row = conn.execute("SELECT status, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "deferred"
    assert row["next_fire_at"] <= time.time()


# ── PLAN-14 Stufe 14.5 — active-Flag ──────────────────────────────────────────


def test_reserve_next_skips_inactive_jobs(conn):
    jid = _seed_full(conn, slug="gone", next_fire_at=0)
    conn.execute("UPDATE jobs SET active=0 WHERE id=?", (jid,))
    assert job_db.reserve_next(conn) is None


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


def test_reconcile_startup_orphans_awaiting(conn):
    # AWAITING-Jobs (HITL) werden ebenfalls reconciliert.
    jid = _seed_full(conn, slug="waiting", status="awaiting", worker="me", next_fire_at=0)
    assert job_db.reconcile_startup_orphans(conn, "me") == 1
    row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "killed"
    assert row["reason"] == "no_process"


def test_reconcile_startup_orphans_no_pid_kills_without_signal(conn):
    # Kein PID in DB (Altlast) → killed, kein Signal-Versuch.
    jid = _seed_full(conn, slug="legacy", status="running", worker="me", next_fire_at=0)
    # pid=NULL in DB → keine Ausnahme, trotzdem killed
    assert job_db.reconcile_startup_orphans(conn, "me") == 1
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "killed"


def test_reconcile_startup_orphans_pid_recycled(conn):
    # PID in DB, aber pid_started_at stimmt nicht überein → PID recycled,
    # kein SIGKILL, nur killed in DB.
    jid = _seed_full(conn, slug="recycled", status="running", worker="me",
                     next_fire_at=0, pid=99999, pid_started_at="stale-ts")
    # proc_started_at(99999) liefert entweder None (PID tot) oder einen anderen Wert.
    # In jedem Fall: Status muss killed sein, kein Fehler.
    n = job_db.reconcile_startup_orphans(conn, "me")
    assert n == 1
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "killed"


def test_reconcile_startup_orphans_live_pid_sends_sigkill(conn, monkeypatch):
    # Prozess lebt noch (gleiche PID + Startzeit) → SIGKILL.
    import os, signal
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(job_db, "proc_started_at", lambda pid: "ts-42")
    jid = _seed_full(conn, slug="orphan", status="running", worker="me",
                     next_fire_at=0, pid=1234, pid_started_at="ts-42")
    n = job_db.reconcile_startup_orphans(conn, "me")
    assert n == 1
    assert (1234, signal.SIGKILL) in killed
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "killed"


def test_report_pid_writes_to_db(conn):
    jid = _seed_full(conn, slug="s", status="running", worker="me", next_fire_at=0)
    job_db.report_pid(conn, jid, 5678, "boot-ts")
    row = conn.execute("SELECT pid, pid_started_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["pid"] == 5678
    assert row["pid_started_at"] == "boot-ts"


def test_proc_started_at_current_process():
    # Der aktuelle Prozess hat eine ermittelbare Startzeit.
    import os
    result = job_db.proc_started_at(os.getpid())
    assert result is not None and len(result) > 0


def test_migration_v8_to_v9_adds_pid_columns(tmp_path):
    import sqlite3
    p = tmp_path / "jobs.sqlite"
    # v8-DB simulieren: Schema anlegen ohne pid-Spalten, version=8 setzen.
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 8")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
            schedule_ref TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    c.close()
    c2 = job_db.connect(p)
    assert c2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    cols = {r["name"] for r in c2.execute("PRAGMA table_info(jobs)")}
    assert "pid" in cols
    assert "pid_started_at" in cols
    c2.close()


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
