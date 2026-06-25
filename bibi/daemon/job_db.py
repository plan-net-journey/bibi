"""Job-DB: SQLite-Connection, Migrationen, Mapper, CRUD, Rescan (PLAN-3 §3.1).

Eine Datei ``data/jobs.sqlite`` (gitignored, §3.2). WAL + ``busy_timeout`` für
Nebenläufigkeit (mehrere Worker gegen einen Scheduler, §3.2/§4.2);
``check_same_thread=False``, weil FastAPI sync-Handler über den Threadpool
dispatcht (eine Connection wird je Request sequentiell genutzt, nie nebenläufig).

Schema-Versionierung über ``PRAGMA user_version``: die Basis (v1) liegt in
``schema.sql``; spätere additive Migrationen kommen in :data:`_MIGRATIONS`.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path

from dateutil import parser as _date_parser

from bibi import repo
from bibi.schedule import discovery
from bibi.schedule.parser import ParseResult

SCHEMA_VERSION = 1
_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

#: Additive Migrationen ab v1: ``from_version -> [DDL, …]``. Noch leer (v1 = Basis).
_MIGRATIONS: dict[int, list[str]] = {}


def db_path(path: Path | None = None) -> Path:
    return path or (repo.data() / "jobs.sqlite")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Frische Connection zur Job-DB; stellt Schema + Migrationen sicher (idempotent)."""
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(_SCHEMA_SQL)
        version = SCHEMA_VERSION
    while version in _MIGRATIONS:
        for ddl in _MIGRATIONS[version]:
            conn.executescript(ddl)
        version += 1
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


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
