"""Job-DB: SQLite-Connection, Migrationen, Mapper, CRUD, Rescan (PLAN-3 §3.1).

Eine Datei ``data/jobs.sqlite`` (gitignored, §3.2). WAL + ``busy_timeout`` für
Nebenläufigkeit (mehrere Worker gegen einen Scheduler, §3.2/§4.2);
``check_same_thread=False``, weil FastAPI sync-Handler über den Threadpool
dispatcht (eine Connection wird je Request sequentiell genutzt, nie nebenläufig).

Schema-Versionierung über ``PRAGMA user_version``: die Basis (v1) liegt in
``schema.sql``; spätere additive Migrationen kommen in :data:`_MIGRATIONS`.
"""

from __future__ import annotations

import json
import secrets
import socket
import sqlite3
import time
from pathlib import Path

from dateutil import parser as _date_parser

from bibi import repo
from bibi.schedule import discovery, dispatcher, lifecycle
from bibi.schedule.models import Kind, Status
from bibi.schedule.parser import ParseResult

SCHEMA_VERSION = 4
_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r["name"] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _mig_meta(conn: sqlite3.Connection) -> None:  # v1 → v2
    conn.executescript("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")


def _mig_journal_domain(conn: sqlite3.Connection) -> None:  # v2 → v3
    if not _has_column(conn, "journal", "domain"):
        conn.execute("ALTER TABLE journal ADD COLUMN domain TEXT NOT NULL DEFAULT 'scheduled'")


def _mig_jobs_deferred_at(conn: sqlite3.Connection) -> None:  # v3 → v4
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "deferred_at"):
        conn.execute("ALTER TABLE jobs ADD COLUMN deferred_at REAL")


#: Additive Migrationen für *bestehende* DBs: ``from_version -> [callable, …]``.
#: ``schema.sql`` ist das volle aktuelle Schema (frische DB); diese Schritte heben
#: ältere DBs Stück für Stück an, **idempotent** (PLAN-3 §3.1).
_MIGRATIONS: dict[int, list] = {
    1: [_mig_meta],
    2: [_mig_journal_domain],
    3: [_mig_jobs_deferred_at],
}


def db_path(path: Path | None = None) -> Path:
    return path or (repo.data() / "jobs.sqlite")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Frische Connection zur Job-DB; stellt Schema + Migrationen sicher (idempotent).

    ``isolation_level=None`` (Autocommit): erlaubt explizite ``BEGIN IMMEDIATE``-
    Transaktionen für die atomare Reservierung (§3.2); übrige Schreibpfade
    committen je Statement.
    """
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(_SCHEMA_SQL)  # frische DB: volles aktuelles Schema
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    while version < SCHEMA_VERSION:  # bestehende DB: schrittweise migrieren
        for migrate in _MIGRATIONS.get(version, []):
            migrate(conn)
        version += 1
    conn.execute(f"PRAGMA user_version = {version}")


# ── next_fire_at-Berechnung (§5.2) ──────────────────────────────────────────


def compute_next_fire(spec, now: float | None = None) -> float | None:
    """Nächste Feuerzeit als Epoch (oder None für startup/never/unbestimmt)."""
    now = time.time() if now is None else now
    if spec.at is not None:
        try:
            return _date_parser.isoparse(spec.at).timestamp()
        except ValueError:
            return None
    sched = spec.schedule
    if sched is None or sched in ("startup", "never"):
        return None
    if sched == "now":
        return now
    try:
        import croniter
        return croniter.croniter(sched, now).get_next(float)
    except Exception:
        return None


# ── Spec → Spalten ──────────────────────────────────────────────────────────


def _spec_columns(pr: ParseResult, now: float) -> dict:
    """Die aus einer ParseResult ableitbaren Schedule-/Spec-Spalten (ohne Live-Status)."""
    s = pr.spec
    assert s is not None
    return {
        "slug": s.slug,
        "schedule_ref": pr.schedule_ref,
        "slug_explicit": 1 if pr.slug_explicit else 0,
        "kind": s.kind.value,
        "payload": s.payload,
        "schedule": s.schedule,
        "at_iso": s.at,
        "next_fire_at": compute_next_fire(s, now),
        "priority": s.priority,
        "model": s.model if s.kind.value == "claude" else None,
        "soul": s.soul,
        "session": s.session,
        "app_port": s.app_port,
        "app_prefix": s.app_prefix,
        "image": s.image,
        "attempts": s.attempts,
        "backoff": s.backoff,
        "silence_timeout": s.silence_timeout,
        "wall_time": s.wall_time,
        "defer_time": s.defer_time,
        "defer_max": s.defer_max,
        "hitl_timeout": s.hitl_timeout,
    }


# ── CRUD ─────────────────────────────────────────────────────────────────────


def upsert_schedule(conn: sqlite3.Connection, pr: ParseResult, now: float) -> str:
    """Schedule einfügen (status pending, neue ID) oder Spec-Spalten aktualisieren.

    Schlüssel ist der Slug. Bei Update bleiben ``id`` und Live-Status erhalten —
    nur die aus der MD abgeleiteten Felder werden neu geschrieben.
    """
    cols = _spec_columns(pr, now)
    existing = conn.execute("SELECT id FROM jobs WHERE slug=?", (cols["slug"],)).fetchone()
    if existing is None:
        job_id = secrets.token_hex(4)
        fields = {
            "id": job_id, "status": "pending", "attempt": 0,
            "enqueued_at": now, "created_at": now, "updated_at": now, **cols,
        }
        names = ", ".join(fields)
        placeholders = ", ".join(f":{k}" for k in fields)
        conn.execute(f"INSERT INTO jobs ({names}) VALUES ({placeholders})", fields)
        return job_id
    cols["updated_at"] = now
    assignments = ", ".join(f"{k}=:{k}" for k in cols)
    conn.execute(f"UPDATE jobs SET {assignments} WHERE slug=:slug", cols)
    return existing["id"]


def remove_slugs(conn: sqlite3.Connection, slugs: set[str]) -> int:
    n = 0
    for slug in slugs:
        cur = conn.execute("DELETE FROM jobs WHERE slug=?", (slug,))
        n += cur.rowcount
    return n


def list_jobs(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY priority DESC, enqueued_at ASC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY priority DESC, enqueued_at ASC"
        ).fetchall()
    return [job_view(r) for r in rows]


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return job_view(row) if row else None


def list_schedules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM jobs ORDER BY slug").fetchall()
    return [schedule_view(r) for r in rows]


# ── Row → View (JobView/ScheduleView-Form, §3.0-Schemata) ────────────────────


def job_view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "slug": row["slug"], "kind": row["kind"],
        "status": row["status"], "reason": row["reason"], "priority": row["priority"],
        "enqueued_at": row["enqueued_at"], "started_at": row["started_at"],
        "finished_at": row["finished_at"], "exit_code": row["exit_code"],
        "attempt": row["attempt"], "host": row["host"], "worker": row["worker"],
        "output_ref": row["output_ref"],
    }


def schedule_view(row: sqlite3.Row) -> dict:
    trigger = row["schedule"] if row["schedule"] is not None else row["at_iso"]
    return {
        "slug": row["slug"], "kind": row["kind"], "trigger": trigger or "",
        "next_fire_at": row["next_fire_at"], "last_status": row["status"],
        "last_run_at": row["finished_at"],
    }


# ── Rescan: Vault → DB abgleichen ────────────────────────────────────────────


def rescan(conn: sqlite3.Connection, vault_root: Path | None = None) -> dict:
    """Vault begehen, DB abgleichen: einfügen/aktualisieren/entfernen.

    Liefert eine Zählübersicht plus die gemeldeten Fehler/Kollisionen (§3.1).
    """
    vault_root = vault_root or repo.case_dir()
    now = time.time()
    result = discovery.discover(vault_root)

    existing = {r["slug"] for r in conn.execute("SELECT slug FROM jobs").fetchall()}
    discovered = set(result.found)

    inserted = updated = 0
    for slug, pr in result.found.items():
        if slug in existing:
            upsert_schedule(conn, pr, now)
            updated += 1
        else:
            upsert_schedule(conn, pr, now)
            inserted += 1
    removed = remove_slugs(conn, existing - discovered)
    conn.commit()

    return {
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
        "errors": [{"schedule_ref": e.schedule_ref, "error": e.error} for e in result.errors],
        "collisions": [
            {"slug": c.slug, "schedule_refs": list(c.schedule_refs)} for c in result.collisions
        ],
    }


# ── Fairness-Cursor (meta) ───────────────────────────────────────────────────


def _get_offset(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM meta WHERE key='dispatcher_offset'").fetchone()
    return float(row["value"]) if row else 0.0


def _set_offset(conn: sqlite3.Connection, value: float) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('dispatcher_offset', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(float(value)),),
    )


# ── Reservierung + Statusmeldung (§4.4, §3.2) ────────────────────────────────


def reservation_view(row: sqlite3.Row) -> dict:
    """Antwort auf ``/-/scheduler/next`` (§4.4): der reservierte Job + Env-Stub.
    Den vollständigen Env-Bau übernimmt die Typ-Registry des Wrappers (Stufe 3.3)."""
    return {
        "id": row["id"], "slug": row["slug"], "kind": row["kind"],
        "payload": row["payload"], "model": row["model"],
        "soul": row["soul"], "session": row["session"], "env": {},
    }


def reserve_next(
    conn: sqlite3.Connection, *, worker: str | None = None,
    host: str | None = None, now: float | None = None,
) -> dict | None:
    """Nächstbesten Job atomar reservieren (§4.4/§3.2). ``None`` = nichts zu tun.

    Auswahl (Fairness-Offset), Lock (Compare-and-Swap) und Offset-Vorrücken
    laufen in **einer** ``BEGIN IMMEDIATE``-Transaktion — SQLite serialisiert
    Writer, also können zwei gleichzeitige ``/next`` denselben Job nicht bekommen
    und der read-modify-write des Cursors bleibt konsistent (Invariante: genau 1
    Scheduler).
    """
    now = time.time() if now is None else now
    host = host or socket.gethostname()
    conn.execute("BEGIN IMMEDIATE")
    try:
        offset = _get_offset(conn)
        # Eligibel: pending, retriable failed (Backoff fällig, Versuche übrig) und
        # fällige deferred (resume) — §5.4 failed→running / deferred→running.
        rows = conn.execute(
            "SELECT id, priority, enqueued_at, rowid AS seq FROM jobs "
            "WHERE locked_at IS NULL AND ("
            "  status='pending'"
            "  OR (status='failed' AND attempt < attempts "
            "      AND (next_fire_at IS NULL OR next_fire_at <= :now))"
            "  OR (status='deferred' AND (next_fire_at IS NULL OR next_fire_at <= :now))"
            ")",
            {"now": now},
        ).fetchall()
        chosen, new_offset = dispatcher.select([dict(r) for r in rows], offset)
        if chosen is None:
            conn.execute("COMMIT")
            return None
        cur = conn.execute(
            "UPDATE jobs SET status='running', locked_at=:now, started_at=:now, "
            "worker=:w, host=:h "
            "WHERE id=:id AND status IN ('pending','failed','deferred') "
            "AND locked_at IS NULL",
            {"now": now, "w": worker, "h": host, "id": chosen["id"]},
        )
        if cur.rowcount != 1:  # unter BEGIN IMMEDIATE eigentlich unerreichbar
            conn.execute("ROLLBACK")
            return None
        _set_offset(conn, new_offset)
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (chosen["id"],)).fetchone()
        conn.execute("COMMIT")
        return reservation_view(row)
    except Exception:
        conn.execute("ROLLBACK")
        raise


def report_status(
    conn: sqlite3.Connection, job_id: str, *, status: str,
    reason: str | None = None, exit_code: int | None = None,
    host: str | None = None, worker: str | None = None,
    output_ref: str | None = None, attempt: int | None = None,
    next_fire_at: float | None = None, now: float | None = None,
) -> str:
    """Worker meldet einen Zustandswechsel (§4.4, output-frei). Rückgabe:
    ``ok`` | ``invalid`` (verbotener Übergang, §5.4) | ``not_found``."""
    now = time.time() if now is None else now
    row = conn.execute("SELECT status, kind FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return "not_found"
    current = Status(row["status"])
    target = Status(status)
    if target != current and target not in lifecycle.targets(current, kind=Kind(row["kind"])):
        return "invalid"

    fields: dict = {"status": target.value, "reason": reason, "updated_at": now}
    if target in lifecycle.TERMINAL:
        fields["finished_at"] = now
    # failed/deferred/pending sind wieder dispatchbar ⇒ Lock lösen (reserve braucht NULL).
    if target in (Status.FAILED, Status.DEFERRED, Status.PENDING):
        fields["locked_at"] = None
    if target is Status.PENDING:  # reset = frische Neueinplanung (§5.6)
        fields["attempt"] = 0
        fields["next_fire_at"] = None
        fields["reason"] = None
        fields["deferred_at"] = None
    if attempt is not None:
        fields["attempt"] = attempt
    if next_fire_at is not None:
        fields["next_fire_at"] = next_fire_at
    if exit_code is not None:
        fields["exit_code"] = exit_code
    if host is not None:
        fields["host"] = host
    if worker is not None:
        fields["worker"] = worker
    if output_ref is not None:  # nur Referenz — der Scheduler bleibt output-frei (§4.4)
        fields["output_ref"] = output_ref

    assignments = ", ".join(f"{k}=:{k}" for k in fields)
    fields["id"] = job_id
    conn.execute(f"UPDATE jobs SET {assignments} WHERE id=:id", fields)

    # Terminal-Übergang → eine Journal-Zeile (disponierte Domäne, §1.4).
    if target in lifecycle.TERMINAL:
        _write_journal(conn, job_id, now)
    return "ok"


def sweep(conn: sqlite3.Connection, now: float | None = None) -> dict:
    """Zeitgesteuerte Scheduler-Übergänge (§5.4/§5.5; PLAN-3 §3.5).

    - **failed + erschöpft** (``attempt >= attempts``, Backoff fällig) → ``error``.
    - **deferred + abgelaufen** (``defer_max`` überschritten) → ``inactive``
      (``deferred_expired``).

    Worker-seitige Übergänge (wall_time/silence/no_process während der Ausführung)
    macht der Worker selbst — der Sweep deckt nur die rein zeit-/zählerbasierten
    Scheduler-Entscheidungen ab."""
    now = time.time() if now is None else now
    errored = inactivated = 0
    for r in conn.execute(
        "SELECT id FROM jobs WHERE status='failed' AND attempt >= attempts "
        "AND (next_fire_at IS NULL OR next_fire_at <= ?)", (now,)
    ).fetchall():
        report_status(conn, r["id"], status="error", now=now)
        errored += 1
    for r in conn.execute(
        "SELECT id FROM jobs WHERE status='deferred' AND deferred_at IS NOT NULL "
        "AND defer_max IS NOT NULL AND (? - deferred_at) >= defer_max", (now,)
    ).fetchall():
        report_status(conn, r["id"], status="inactive", reason="deferred_expired", now=now)
        inactivated += 1
    return {"errored": errored, "inactivated": inactivated}


# ── Journal (disponierte Domäne, §1.4) ───────────────────────────────────────


def _write_journal(conn: sqlite3.Connection, job_id: str, archived_at: float) -> None:
    """Eine append-only Journal-Zeile aus dem aktuellen Job-Zustand schreiben.

    Watermark-Dedup: pro (run_id, status) genau eine Zeile — ein erneuter
    Terminal-Report (z. B. idempotenter Retry) dupliziert nicht."""
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return
    run_id = f"{row['slug']}:{row['attempt']}"
    dup = conn.execute(
        "SELECT 1 FROM journal WHERE run_id=? AND status=?", (run_id, row["status"])
    ).fetchone()
    if dup:
        return
    exec_runtime = None
    if row["started_at"] is not None and row["finished_at"] is not None:
        exec_runtime = row["finished_at"] - row["started_at"]
    conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, reason, started_at, "
        "finished_at, exit_code, exec_runtime, host, worker, output_ref, snapshot, "
        "archived_at) VALUES (:run_id,:slug,:kind,:status,:reason,:started_at,"
        ":finished_at,:exit_code,:exec_runtime,:host,:worker,:output_ref,:snapshot,"
        ":archived_at)",
        {
            "run_id": run_id, "slug": row["slug"], "kind": row["kind"],
            "status": row["status"], "reason": row["reason"],
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            "exit_code": row["exit_code"], "exec_runtime": exec_runtime,
            "host": row["host"], "worker": row["worker"], "output_ref": row["output_ref"],
            "snapshot": json.dumps(job_view(row), ensure_ascii=False),
            "archived_at": archived_at,
        },
    )


def journal_view(row: sqlite3.Row) -> dict:
    return {
        "run_id": row["run_id"], "slug": row["slug"], "kind": row["kind"],
        "status": row["status"], "reason": row["reason"],
        "started_at": row["started_at"], "finished_at": row["finished_at"],
        "exit_code": row["exit_code"], "exec_runtime": row["exec_runtime"],
        "host": row["host"], "worker": row["worker"], "output_ref": row["output_ref"],
        "domain": row["domain"],
    }


def list_journal(
    conn: sqlite3.Connection, slug: str | None = None,
    host: str | None = None, domain: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM journal"
    clauses, params = [], []
    if slug:
        clauses.append("slug=?"); params.append(slug)
    if host:
        clauses.append("host=?"); params.append(host)
    if domain:
        clauses.append("domain=?"); params.append(domain)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY archived_at DESC"
    return [journal_view(r) for r in conn.execute(sql, params).fetchall()]


def write_local_journal(
    conn: sqlite3.Connection, *, run_id: str, slug: str, kind: str, status: str,
    exit_code: int | None, output_ref: str | None, host: str | None,
    worker: str | None, started_at: float, finished_at: float,
    reason: str | None = None,
) -> None:
    """Journal-Zeile der **lokalen** Domäne (§1.4) — von ``/run``. Bewusst **ohne**
    ``jobs``-Eintrag: die zentrale Queue sieht den Lauf nie. ``domain='local'``."""
    conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, reason, started_at, "
        "finished_at, exit_code, exec_runtime, host, worker, output_ref, snapshot, "
        "archived_at, domain) VALUES (:run_id,:slug,:kind,:status,:reason,:started_at,"
        ":finished_at,:exit_code,:exec_runtime,:host,:worker,:output_ref,:snapshot,"
        ":archived_at,'local')",
        {
            "run_id": run_id, "slug": slug, "kind": kind, "status": status,
            "reason": reason, "started_at": started_at, "finished_at": finished_at,
            "exit_code": exit_code, "exec_runtime": finished_at - started_at,
            "host": host, "worker": worker, "output_ref": output_ref,
            "snapshot": json.dumps({"slug": slug, "kind": kind, "status": status,
                                    "exit_code": exit_code}, ensure_ascii=False),
            "archived_at": finished_at,
        },
    )
