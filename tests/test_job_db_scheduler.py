"""Job-DB: Reservierung, Statusmeldung, Migration, Concurrency (PLAN-3 §3.2)."""

from __future__ import annotations

import json
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
        "app_port", "app_prefix", "exec_mode", "image", "defer_time", "error_time",
        "schedule_ref", "env",
    }
    assert r["kind"] == "job" and r["payload"] == "echo hi"


def test_reservation_includes_schedule_image_override(conn):
    # PLAN-24 Befund 1: `image:` aus dem Schedule-MD landet zwar in der DB
    # (job_db._spec_columns), ging bislang aber nie über reservation_view()
    # zum Worker durch — komplett totes Feld, exakt wie `oneshot` vor PLAN-23.
    jid = _insert(conn, "a", 0, time.time())
    conn.execute("UPDATE jobs SET image=? WHERE id=?", ("custom-job-image:1", jid))
    r = job_db.reserve_next(conn)
    assert r["image"] == "custom-job-image:1"


def test_reservation_image_is_none_without_override(conn):
    _insert(conn, "a", 0, time.time())
    r = job_db.reserve_next(conn)
    assert r["image"] is None


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


def test_journal_snapshot_captures_full_config_not_just_live_view(conn):
    # User-Feedback 2026-07-03: "ein Schedule oder Attempts kann sich ändern,
    # deshalb müssen alle Werte ... als Attribut am Lauf hängen" — der Snapshot
    # muss job_full_view() sein (attempts/backoff/model/...), nicht die kleine
    # job_view() (die nur schedule/priority/app_port trägt).
    jid = _seed_full(conn, slug="a", attempts=5, backoff="exponential",
                     model="claude-opus-4-8", status="running", next_fire_at=0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    entry_id = job_db.list_journal(conn, slug="a")[0]["id"]
    entry = job_db.get_journal(conn, entry_id)
    snap = json.loads(entry["snapshot"])
    assert snap["attempts"] == 5
    assert snap["backoff"] == "exponential"
    assert snap["model"] == "claude-opus-4-8"


def test_get_journal_exposes_snapshot_and_archived_at_unlike_list_view(conn):
    # journal_view() (Listenansicht) bleibt bewusst schlank — get_journal()
    # (Einzelabfrage für die Lauf-Detail-Seite) ergänzt snapshot/archived_at.
    jid = _seed_full(conn, slug="a", status="running", next_fire_at=0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    entry_id = job_db.list_journal(conn, slug="a")[0]["id"]
    assert "snapshot" not in job_db.list_journal(conn, slug="a")[0]
    entry = job_db.get_journal(conn, entry_id)
    assert entry["snapshot"] is not None
    assert entry["archived_at"] is not None


def test_report_illegal_transition_rejected(conn):
    jid = _insert(conn, "a", 0, time.time())  # pending
    # pending → complete ist verboten (§5.4)
    assert job_db.report_status(conn, jid, status="complete") == "invalid"
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "pending"


def test_reset_rejected_for_completed_oneshot(conn):
    # PLAN-23 Befund 3: lifecycle.py erlaubt (COMPLETE, RESET) → PENDING
    # generell (richtig für wiederkehrende Jobs) — für einen abgeschlossenen
    # oneshot (`at:`, schedule=None) muss das serverseitig verboten sein, die
    # UI blendet den RESET-Button dafür zwar aus (_VERBS_FOR_STATUS), aber
    # ein direkter API-Call ging bisher trotzdem durch.
    jid = _insert(conn, "once", 0, time.time())
    conn.execute("UPDATE jobs SET status='complete', schedule=NULL WHERE id=?", (jid,))
    conn.commit()
    assert job_db.report_status(conn, jid, status="pending") == "invalid"
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "complete"


def test_reset_still_allowed_for_completed_recurring(conn):
    # Regressionsschutz: der Befund-3-Fix darf RESET für wiederkehrende
    # Schedules (schedule gesetzt) nicht antasten.
    jid = _insert(conn, "recurring", 0, time.time())
    conn.execute("UPDATE jobs SET status='complete', schedule='0 9 * * *' WHERE id=?", (jid,))
    conn.commit()
    assert job_db.report_status(conn, jid, status="pending") == "ok"
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


@pytest.mark.parametrize("source,target", [
    ("running", "killed"), ("failed", "error"),
    ("deferred", "inactive"), ("running", "zombie"),
])
def test_report_status_clears_stale_next_fire_at_for_terminal(conn, source, target):
    # Echte Sackgassen (error/inactive/zombie/killed) dürfen keinen next_fire_at
    # aus dem vorigen Zyklus (Backoff-Timer, alter Rearm-Wert) stehen lassen —
    # sonst zeigt die UI ein "nächster Lauf in Xh", das nie feuert.
    jid = _seed_full(conn, slug="s", status=source, next_fire_at=time.time() + 3600)
    assert job_db.report_status(conn, jid, status=target) == "ok"
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] is None


# ── dispatch_count (PLAN-21 Befund 11 v2, running_since_uptime) ─────────────


def test_dispatch_count_increments_per_successful_reserve(conn):
    assert job_db.dispatch_count() == 0
    _insert(conn, "a", 0, time.time())
    _insert(conn, "b", 0, time.time())
    job_db.reserve_next(conn, now=100.0)
    assert job_db.dispatch_count() == 1
    job_db.reserve_next(conn, now=101.0)
    assert job_db.dispatch_count() == 2


def test_dispatch_count_unchanged_when_nothing_to_reserve(conn):
    job_db.reserve_next(conn, now=100.0)  # keine Jobs vorhanden
    assert job_db.dispatch_count() == 0


# ── count_completed_since (PLAN-26 Befund 3 Nachtrag, job_stats.
# complete_since_uptime) ─────────────────────────────────────────────────────
#
# Der ursprüngliche In-Memory-Zähler (_complete_count/complete_count(), wie
# dispatch_count()) hatte einen Prozessgrenzen-Bug (User-Fund: "warum zählt
# COMPLETE nach einem erfolgreichen Lauf nicht +1?"): report_status(status=
# "complete") für Scheduler-Jobs läuft meist im DETACHED Wrapper-Subprozess
# (SQLite-Direct-Pfad, wrapper/__init__.py::_report_terminal) — ein reiner
# Python-Global im Daemon-Hauptprozess sieht diese Inkremente nie, der
# Wrapper-Prozess erhöht nur seine eigene, kurzlebige Kopie. Fix: eine
# DB-Query gegen die journal-Tabelle (echter, prozessübergreifender Shared
# State) statt eines In-Memory-Zählers.


def test_count_completed_since_counts_scheduled_completions(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, now=100.0)  # → running
    job_db.report_status(conn, jid, status="complete", exit_code=0, now=150.0)
    assert job_db.count_completed_since(conn, 0.0) == 1


def test_count_completed_since_excludes_completions_before_threshold(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, now=100.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0, now=150.0)
    assert job_db.count_completed_since(conn, 200.0) == 0  # Daemon "startete" danach


def test_count_completed_since_counts_each_recurring_completion(conn):
    # Wiederkehrende Schedules laufen mehrfach durch complete — jede echte
    # Ausführung schreibt eine eigene Journal-Zeile.
    jid = _seed_full(conn, slug="a", status="running", next_fire_at=0)
    job_db.report_status(conn, jid, status="complete", exit_code=0, now=100.0)
    conn.execute(
        "UPDATE jobs SET status='pending', locked_at=NULL, next_fire_at=0 WHERE id=?", (jid,))
    job_db.reserve_next(conn, now=200.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0, now=250.0)
    assert job_db.count_completed_since(conn, 0.0) == 2


def test_count_completed_since_excludes_local_runs(conn, seed_journal_row):
    # User-Entscheidung: konsistent zu den anderen 9 Status, die auch nur
    # Scheduler-Jobs sehen — lokale /-/run-Läufe (domain='local') zählen
    # bewusst nicht mit.
    seed_journal_row(
        conn, run_id="x:1", slug="x", kind="job", status="complete", exit_code=0,
        output_ref=None, host="h", worker="w", started_at=100.0, finished_at=150.0)
    assert job_db.count_completed_since(conn, 0.0) == 0


def test_count_completed_since_ignores_non_complete_terminals(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, now=100.0)
    job_db.report_status(conn, jid, status="failed", exit_code=1, now=150.0)
    assert job_db.count_completed_since(conn, 0.0) == 0


def test_count_completed_since_survives_fresh_connection(tmp_path: Path):
    # Kern der Prozessgrenzen-Korrektur: die Zählung hängt an der DB, nicht an
    # In-Memory-State — eine komplett neue Verbindung (simuliert einen neuen
    # Prozess) sieht dieselbe, bereits geschriebene Completion.
    db_path = tmp_path / "jobs.sqlite"
    conn1 = job_db.connect(db_path)
    jid = _insert(conn1, "a", 0, time.time())
    job_db.reserve_next(conn1, now=100.0)
    job_db.report_status(conn1, jid, status="complete", exit_code=0, now=150.0)
    conn1.close()

    conn2 = job_db.connect(db_path)  # "anderer Prozess"
    try:
        assert job_db.count_completed_since(conn2, 0.0) == 1
    finally:
        conn2.close()


# ── journal_landings (PLAN-21 Befund 11 v2, Lauf-Historie-Chart) ────────────


def test_journal_landings_returns_status_and_finished_at(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, now=100.0)
    job_db.report_status(conn, jid, status="complete", now=200.0)
    rows = job_db.journal_landings(conn)
    assert rows == [{"status": "complete", "finished_at": 200.0}]


def test_journal_landings_since_filters(conn):
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, now=100.0)
    job_db.report_status(conn, jid, status="killed", now=200.0)
    assert job_db.journal_landings(conn, since=250.0) == []
    assert job_db.journal_landings(conn, since=150.0) == [
        {"status": "killed", "finished_at": 200.0}]


def test_journal_landings_excludes_non_terminal_status(conn):
    # awaiting ist kein Terminal-Status — journal bekommt dafür nie eine Zeile
    # (_write_journal feuert nur bei target in lifecycle.TERMINAL).
    jid = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, now=100.0)
    job_db.report_status(conn, jid, status="awaiting", now=150.0)
    assert job_db.journal_landings(conn) == []


# ── status_counts (PLAN-21 Befund 11 Stat-Grid) ──────────────────────────────


def test_status_counts_groups_active_jobs_by_status(conn):
    a = _insert(conn, "a", 0, time.time())
    job_db.reserve_next(conn, now=100.0)  # a → running
    job_db.report_status(conn, a, status="killed", now=200.0)  # a → killed
    _insert(conn, "b", 0, time.time())  # bleibt pending, wird nicht reserviert
    assert job_db.status_counts(conn) == {"pending": 1, "killed": 1}


def test_status_counts_excludes_inactive_jobs(conn):
    jid = _insert(conn, "a", 0, time.time())
    conn.execute("UPDATE jobs SET active=0 WHERE id=?", (jid,))
    assert job_db.status_counts(conn) == {}


def test_status_counts_empty_db(conn):
    assert job_db.status_counts(conn) == {}


# ── status_counts_by_kind (Bibi4-Iteration, Job-Status-Matrix) ──────────────


def test_status_counts_by_kind_splits_job_claude_app(conn):
    _insert(conn, "a", 0, time.time())  # payload "echo hi" -> job
    b = _insert(conn, "b", 0, time.time())
    conn.execute("UPDATE jobs SET payload=? WHERE id=?", ("claude: tu was", b))
    c = _insert(conn, "c", 0, time.time())
    conn.execute("UPDATE jobs SET app_port=? WHERE id=?", (9100, c))
    assert job_db.status_counts_by_kind(conn) == {
        "job": {"pending": 1}, "claude": {"pending": 1}, "app": {"pending": 1},
    }


def test_status_counts_by_kind_app_port_wins_over_claude_payload(conn):
    a = _insert(conn, "a", 0, time.time())
    conn.execute("UPDATE jobs SET payload=?, app_port=? WHERE id=?",
                ("claude: tu was", 9100, a))
    assert job_db.status_counts_by_kind(conn) == {
        "job": {}, "claude": {}, "app": {"pending": 1},
    }


def test_status_counts_by_kind_excludes_inactive_jobs(conn):
    jid = _insert(conn, "a", 0, time.time())
    conn.execute("UPDATE jobs SET active=0 WHERE id=?", (jid,))
    assert job_db.status_counts_by_kind(conn) == {"job": {}, "claude": {}, "app": {}}


def test_status_counts_by_kind_empty_db(conn):
    assert job_db.status_counts_by_kind(conn) == {"job": {}, "claude": {}, "app": {}}


# ── next_due_at (PLAN-26 Befund 3, Job-Status-Kachel: "nächster Job") ───────


def test_next_due_at_returns_smallest_next_fire_at(conn):
    _seed_full(conn, slug="a", status="pending", next_fire_at=500.0)
    _seed_full(conn, slug="b", status="pending", next_fire_at=100.0)
    _seed_full(conn, slug="c", status="deferred", next_fire_at=300.0)
    assert job_db.next_due_at(conn) == 100.0


def test_next_due_at_ignores_jobs_without_next_fire_at(conn):
    _seed_full(conn, slug="a", status="running", next_fire_at=None)
    assert job_db.next_due_at(conn) is None


def test_next_due_at_excludes_inactive_jobs(conn):
    jid = _seed_full(conn, slug="a", status="pending", next_fire_at=100.0)
    conn.execute("UPDATE jobs SET active=0 WHERE id=?", (jid,))
    assert job_db.next_due_at(conn) is None


def test_next_due_at_empty_db(conn):
    assert job_db.next_due_at(conn) is None


# ── Concurrency: n parallele /next → disjunkt (§3.2/§3.8) ─────────────────────


def test_sweep_failed_without_next_fire_at_to_error(conn):
    # Bugfix (User-Fund: "ein Failed wechselt sofort nach Ende auf ERROR, falls
    # keine Versuche mehr uebrig sind"): sweep()s frueherer "attempt>=attempts"-
    # Zweig ist weg (_finish() loest Erschoepfung schon synchron auf, eine
    # failed-Zeile schuldet also IMMER einen Dispatch). Der einzige verbliebene
    # Sweep-Fall ist Crash-Recovery: next_fire_at=NULL (der transiente
    # Zwischenschritt in _finish()s Erschoepfungspfad, falls der Wrapper genau
    # dazwischen stirbt) - attempt/attempts sind dafuer irrelevant.
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, "
        "attempts, next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'failed', 1, 3, NULL, ?)",
        (jid, "x", "x.md", "job", "e", time.time()),
    )
    res = job_db.sweep(conn, now=time.time())
    assert res["errored"] == 1
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "error"
    assert any(j["status"] == "error" for j in job_db.list_journal(conn))


def test_sweep_leaves_failed_with_next_fire_at_alone(conn):
    # Egal ob der naechste Dispatch noch der letzte gewaehrte Versuch waere
    # (attempt == attempts) - solange next_fire_at gesetzt ist, schuldet die
    # Zeile reserve_next() noch einen Dispatch, sweep() fasst sie nicht an.
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, "
        "attempts, next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'failed', 2, 2, 0, ?)",
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


def test_failed_retry_via_reserve_next_dispatches_last_granted_attempt(conn):
    # Bugfix (User-Fund: "ein Failed wechselt sofort nach Ende auf ERROR, falls
    # keine Versuche mehr uebrig sind" - beobachtet aber ein Failed, das nie
    # mehr dispatcht wurde): attempt == attempts ist der zuletzt GEWAEHRTE,
    # noch nicht VERBRAUCHTE Versuch - reserve_next() muss ihn weiterhin
    # dispatchen (die frueher hier zusaetzliche "attempt < attempts"-Bedingung
    # war off-by-one und schloss genau diesen Fall faelschlich aus).
    import secrets
    jid = secrets.token_hex(4)
    now = time.time()
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, "
        "attempts, next_fire_at, enqueued_at) VALUES (?,?,?,?,?, 'failed', 2, 2, ?, ?)",
        (jid, "retry2", "retry2.md", "job", "echo hi", now - 1, now),
    )
    res = job_db.reserve_next(conn, worker="w", host="h")
    assert res is not None and res["id"] == jid
    row = conn.execute("SELECT status, attempt FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"
    assert row["attempt"] == 2


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


def test_cron_job_complete_gets_next_fire_at_but_stays_complete(conn):
    # Lazy Rearm (User-Feedback: "archiviert wird erst vor dem nächsten Rerun") —
    # complete bleibt sichtbar/terminal, next_fire_at zeigt nur den nächsten Tick.
    jid = _seed_full(conn, slug="cronjob", schedule="*/5 * * * *",
                     status="running", next_fire_at=0, fire=0, started_at=1.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    row = conn.execute(
        "SELECT status, next_fire_at, fire, attempt, finished_at, exit_code, "
        "locked_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "complete"           # bleibt terminal, kein Sofort-Rearm
    assert row["next_fire_at"] > time.time()     # nächster cron-Tick in der Zukunft
    assert row["fire"] == 0                       # Zähler unverändert bis zum echten Dispatch
    assert row["attempt"] == 0
    assert row["finished_at"] is not None and row["exit_code"] == 0  # Snapshot bleibt erhalten
    assert row["locked_at"] is None               # wieder dispatchbar
    # eine Journal-Zeile für den abgeschlossenen Lauf
    assert len(job_db.list_journal(conn)) == 1


def test_complete_job_redispatched_via_reserve_next_bumps_fire_and_clears_snapshot(conn):
    jid = _seed_full(conn, slug="cronjob2", schedule="*/5 * * * *",
                     status="running", next_fire_at=0, fire=0, started_at=1.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    # next_fire_at manuell fällig machen, statt auf den echten Cron-Tick zu warten.
    conn.execute("UPDATE jobs SET next_fire_at=0 WHERE id=?", (jid,))
    reservation = job_db.reserve_next(conn)
    assert reservation is not None and reservation["slug"] == "cronjob2"
    row = conn.execute(
        "SELECT status, fire, attempt, finished_at, exit_code, output_ref, locked_at "
        "FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"
    assert row["fire"] == 1                       # frischer, eindeutiger run_id-Zähler
    assert row["attempt"] == 0
    assert row["finished_at"] is None and row["exit_code"] is None and row["output_ref"] is None
    assert row["locked_at"] is not None


def test_on_demand_job_stays_complete_with_no_next_fire_at(conn):
    jid = _seed_full(conn, slug="ondemand", schedule="on_demand",
                     status="running", next_fire_at=0, fire=0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    row = conn.execute("SELECT status, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "complete"
    assert row["next_fire_at"] is None


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
    # zweiter Lauf: fire bumpt jetzt erst beim echten Redispatch über reserve_next()
    # (lazy Rearm, kein Sofort-Rearm mehr in report_status).
    conn.execute("UPDATE jobs SET next_fire_at=0 WHERE id=?", (jid,))
    assert job_db.reserve_next(conn)["slug"] == "c"             # fire → 1, status running
    job_db.report_status(conn, jid, status="complete")          # fire 1
    rows = job_db.list_journal(conn)
    assert len(rows) == 2                          # KEIN Dedup über fires hinweg
    assert {r["run_id"] for r in rows} == {
        job_db.run_id_for("c", jid, 0), job_db.run_id_for("c", jid, 1)}


def test_journal_dedup_scoped_by_started_at_not_just_run_id(conn):
    # User-Feedback 2026-07-01 (live reproduziert): `fire` startet bei jedem neu
    # angelegten Job-Datensatz wieder bei 0 — ein heutiger Lauf kann so denselben
    # run_id wie ein längst abgeschlossener Lauf einer alten Job-Inkarnation
    # treffen. Die Dedup-Prüfung darf den echten neuen Eintrag dann nicht für
    # "schon geloggt" halten und stillschweigend verwerfen.
    conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, started_at, finished_at, "
        "archived_at) VALUES ('stale:0','stale','job','error', 100.0, 130.0, 130.0)")
    jid = _seed_full(conn, slug="stale", status="running", fire=0, started_at=9000.0)
    job_db.report_status(conn, jid, status="complete", now=9030.0)
    rows = [r for r in job_db.list_journal(conn) if r["slug"] == "stale"]
    assert len(rows) == 2  # der alte Eintrag UND der echte neue, nicht verschluckt
    assert any(r["status"] == "complete" and r["started_at"] == 9000.0 for r in rows)


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
    # schedule explizit gesetzt (wiederkehrend): PLAN-23 Befund 3 sperrt
    # complete+oneshot (schedule=None) gezielt — dieser Test prüft den
    # generischen, nicht-oneshot-Fall, s. eigener Test für die Sperre.
    jid = _seed_full(conn, slug="x", status=status, next_fire_at=None,
                     schedule="0 9 * * *")
    assert job_db.start_now(conn, jid) == "ok"
    row = conn.execute("SELECT status, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "pending"
    assert row["next_fire_at"] is not None


def test_start_now_rejected_for_completed_oneshot(conn):
    # PLAN-23 Befund 3: start_now() archiviert komplett-Status via
    # report_status() (s. _ARCHIVE_AND_START) — die dortige Sperre greift
    # also auch für START, nicht nur für den direkten RESET-Aufruf. Deckt
    # sich mit der User-Vorgabe "können dann nicht mehr erneut ausgeführt
    # werden" (kein Re-Run über irgendeinen Verb-Pfad).
    jid = _seed_full(conn, slug="once", status="complete", next_fire_at=None,
                     schedule=None)
    assert job_db.start_now(conn, jid) == "invalid"
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "complete"


@pytest.mark.parametrize("status", ["running", "awaiting"])
def test_start_now_stays_invalid_for_non_archivable_status(conn, status):
    # running/awaiting sind keine Terminalzustände und haben keinen eigenen
    # next_fire_at-Fast-Path — START bleibt hier ohne Effekt.
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


def test_reset_to_pending_clears_previous_run_snapshot(conn):
    # User-Feedback 2026-07-03: "PENDING ist ein eigener Eintrag, dessen
    # Attribute und Output zurückgesetzt sind" — der Snapshot des vorigen
    # (terminalen) Laufs darf nicht bis zum nächsten Dispatch stehen bleiben.
    jid = _seed_full(conn, slug="x", schedule="never", status="killed",
                     started_at=1.0, finished_at=2.0, exit_code=1,
                     output_ref="data/job/x/output.jsonl")
    assert job_db.report_status(conn, jid, status="pending") == "ok"
    row = conn.execute(
        "SELECT started_at, finished_at, exit_code, output_ref FROM jobs WHERE id=?",
        (jid,)).fetchone()
    assert row["started_at"] is None
    assert row["finished_at"] is None
    assert row["exit_code"] is None
    assert row["output_ref"] is None


def test_start_now_archive_clears_previous_run_snapshot(conn):
    jid = _seed_full(conn, slug="x", status="error",
                     started_at=1.0, finished_at=2.0, exit_code=1,
                     output_ref="data/job/x/output.jsonl")
    assert job_db.start_now(conn, jid) == "ok"
    row = conn.execute(
        "SELECT started_at, finished_at, exit_code, output_ref FROM jobs WHERE id=?",
        (jid,)).fetchone()
    assert row["started_at"] is None
    assert row["finished_at"] is None
    assert row["exit_code"] is None
    assert row["output_ref"] is None


def test_start_now_deferred_dispatches_immediately_like_pending(conn):
    # Follow-up: deferred braucht KEINE attempts-1-Logik ("sofortiger Start"
    # laut Feedback-Tabelle) — war fälschlich mit failed in einen Topf
    # geworfen und blieb daher bislang mit 409 kaputt.
    jid = _seed_full(conn, slug="x", status="deferred", next_fire_at=time.time() + 9999)
    assert job_db.start_now(conn, jid) == "ok"
    row = conn.execute("SELECT status, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "deferred"
    assert row["next_fire_at"] <= time.time()


def test_start_now_failed_dispatches_immediately_without_attempts_reset(conn):
    # User-Entscheidung (Job Lifecycle §START/failed): kein Attempts-Reset, nur
    # next_fire_at=now überspringt den Backoff-Timer — status bleibt `failed`,
    # bis reserve_next() ihn (rein next_fire_at-basiert, s. Bugfix oben) selbst
    # dispatcht.
    jid = _seed_full(conn, slug="x", status="failed", attempt=1, attempts=3,
                     next_fire_at=time.time() + 9999)
    assert job_db.start_now(conn, jid) == "ok"
    row = conn.execute(
        "SELECT status, attempt, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "failed"
    assert row["attempt"] == 1                    # unverändert, kein Reset
    assert row["next_fire_at"] <= time.time()
    assert job_db.reserve_next(conn)["slug"] == "x"  # jetzt fällig


# ── PLAN-14 Stufe 14.5 — active-Flag ──────────────────────────────────────────


def test_reserve_next_skips_inactive_jobs(conn):
    jid = _seed_full(conn, slug="gone", next_fire_at=0)
    conn.execute("UPDATE jobs SET active=0 WHERE id=?", (jid,))
    assert job_db.reserve_next(conn) is None


# ── PLAN-28 — pinned_host + reserve_next(pinned_only=) ──────────────────────


def test_reserve_next_default_still_reserves_unpinned_job(conn):
    # pinned_host=NULL (heutiges Verhalten, keine Regression) — jeder Worker
    # darf weiterhin ran, egal welcher Host anfragt.
    jid = _seed_full(conn, slug="team", next_fire_at=0)
    res = job_db.reserve_next(conn, host="anyhost")
    assert res is not None and res["id"] == jid


def test_reserve_next_default_reserves_job_pinned_to_own_host(conn):
    jid = _seed_full(conn, slug="mine", next_fire_at=0, pinned_host="mac")
    res = job_db.reserve_next(conn, host="mac")
    assert res is not None and res["id"] == jid


def test_reserve_next_default_skips_job_pinned_to_other_host(conn):
    # Ein für Host A gepinnter Job darf auch im normalen Team-Pfad nie von
    # Host B reserviert werden — die Pin-Garantie gilt unabhängig von
    # pinned_only (PLAN-28: "es zählt die Einschränkung: lokal").
    _seed_full(conn, slug="theirs", next_fire_at=0, pinned_host="sarasate")
    assert job_db.reserve_next(conn, host="mac") is None


def test_reserve_next_pinned_only_skips_unpinned_job(conn):
    # Der neue lokale Mini-Loop (LocalPinnedLoop, PLAN-28) darf nie ungepinnte
    # Team-Queue-Jobs an sich reißen.
    _seed_full(conn, slug="team", next_fire_at=0)
    assert job_db.reserve_next(conn, host="mac", pinned_only=True) is None


def test_reserve_next_pinned_only_reserves_matching_host(conn):
    jid = _seed_full(conn, slug="mine", next_fire_at=0, pinned_host="mac")
    res = job_db.reserve_next(conn, host="mac", pinned_only=True)
    assert res is not None and res["id"] == jid


def test_reserve_next_pinned_only_skips_other_host(conn):
    _seed_full(conn, slug="theirs", next_fire_at=0, pinned_host="sarasate")
    assert job_db.reserve_next(conn, host="mac", pinned_only=True) is None


# ── User-Fund 2026-07-13: list_journal(slug=...) findet gepinnte Läufe nicht ──
# run_pinned() vergibt pro Aufruf einen eindeutigen jobs.slug
# (f"{bucket_slug}-{token}", worker.py) — _write_journal() übernimmt den
# unverändert nach journal.slug. Eine Exact-Match-Suche nach dem stabilen
# Bucket-Slug (z. B. "myjob") fand die Journal-Zeile deshalb nie, obwohl sie
# existiert — Symptom: "Job noch nie gelaufen" + fehlender COMPLETE-Lauf im
# Journal der Job-Detailseite.


def test_list_journal_by_bucket_slug_finds_pinned_run(conn):
    jid = _seed_full(conn, slug="myjob-abc12345", pinned_host="mac",
                     status="running", started_at=1.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    rows = job_db.list_journal(conn, slug="myjob")
    assert len(rows) == 1 and rows[0]["status"] == "complete"


def test_list_journal_by_bucket_slug_ignores_unrelated_similar_slug(conn):
    # "job-runner" ist ein ECHTER, anderer Schedule-Slug (pinned_host=NULL) —
    # darf bei der Suche nach "job" nicht fälschlich mit auftauchen, nur weil
    # er zufällig mit "job-" beginnt.
    jid = _seed_full(conn, slug="job-runner", status="running", started_at=1.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    assert job_db.list_journal(conn, slug="job") == []


def test_list_journal_by_exact_slug_still_works_for_real_schedules(conn):
    jid = _seed_full(conn, slug="realschedule", status="running", started_at=1.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    rows = job_db.list_journal(conn, slug="realschedule")
    assert len(rows) == 1 and rows[0]["status"] == "complete"


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


def test_reconcile_startup_orphans_live_pid_left_running(conn, monkeypatch):
    # Prozess lebt noch (gleiche PID + Startzeit) → kein SIGKILL, Status bleibt
    # running — der Wrapper überlebt Daemon-Neustarts bewusst (start_new_session=
    # True) und meldet seinen Abschluss selbst, s. reconcile_startup_orphans()-Docstring.
    import os
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(job_db, "proc_started_at", lambda pid: "ts-42")
    jid = _seed_full(conn, slug="orphan", status="running", worker="me",
                     next_fire_at=0, pid=1234, pid_started_at="ts-42")
    n = job_db.reconcile_startup_orphans(conn, "me")
    assert n == 0
    assert killed == []
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "running"


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
