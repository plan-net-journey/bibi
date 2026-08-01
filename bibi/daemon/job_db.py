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
import shutil
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

from dateutil import parser as _date_parser

from bibi import repo
from bibi.schedule import discovery, dispatcher, lifecycle
from bibi.schedule.models import Kind, Status, display_kind
from bibi.schedule.parser import ParseResult

SCHEMA_VERSION = 19
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


def _mig_jobs_fire(conn: sqlite3.Connection) -> None:  # v4 → v5
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "fire"):
        conn.execute("ALTER TABLE jobs ADD COLUMN fire INTEGER NOT NULL DEFAULT 0")


def _mig_journal_commit(conn: sqlite3.Connection) -> None:  # v5 → v6
    # Commit-SHA + Branch je Lauf (PLAN-4 §2.3, Stufe 4.0): der Worker committet
    # das Worktree, der SHA gehört in die Lauf-Historie (F7-Commit-Link).
    if not _has_column(conn, "journal", "commit_sha"):
        conn.execute("ALTER TABLE journal ADD COLUMN commit_sha TEXT")
    if not _has_column(conn, "journal", "branch"):
        conn.execute("ALTER TABLE journal ADD COLUMN branch TEXT")


def _mig_jobs_exec_mode(conn: sqlite3.Connection) -> None:  # v6 → v7
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "exec_mode"):
        conn.execute("ALTER TABLE jobs ADD COLUMN exec_mode TEXT")


def _mig_kind_normalize(conn: sqlite3.Connection) -> None:  # v7 → v8
    # PLAN-10 Stufe 10.0: `claude`/`app` → `job` (ein einziger Typ).
    if _has_table(conn, "jobs") and _has_column(conn, "jobs", "kind"):
        conn.execute("UPDATE jobs SET kind = 'job' WHERE kind IN ('claude', 'app')")
    if _has_table(conn, "journal") and _has_column(conn, "journal", "kind"):
        conn.execute("UPDATE journal SET kind = 'job' WHERE kind IN ('claude', 'app')")


def _mig_jobs_pid(conn: sqlite3.Connection) -> None:  # v8 → v9
    # PLAN-10 Stufe 10.2: PID + Startzeit für Orphan-Erkennung beim Worker-Neustart.
    if _has_table(conn, "jobs"):
        if not _has_column(conn, "jobs", "pid"):
            conn.execute("ALTER TABLE jobs ADD COLUMN pid INTEGER")
        if not _has_column(conn, "jobs", "pid_started_at"):
            conn.execute("ALTER TABLE jobs ADD COLUMN pid_started_at TEXT")


def _mig_jobs_app_url(conn: sqlite3.Connection) -> None:  # v9 → v10
    # PLAN-10 Stufe 10.4: HITL-Eingabe-Endpunkt der App (kein Proxy mehr).
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "app_url"):
        conn.execute("ALTER TABLE jobs ADD COLUMN app_url TEXT")


def _mig_jobs_ping_demand(conn: sqlite3.Connection) -> None:  # v10 → v11
    # PLAN-11.2: Ping-Timestamp (Zombie-Timeout §2.5) + HITL-Demand JSON.
    if _has_table(conn, "jobs"):
        if not _has_column(conn, "jobs", "last_ping_at"):
            conn.execute("ALTER TABLE jobs ADD COLUMN last_ping_at REAL")
        if not _has_column(conn, "jobs", "demand"):
            conn.execute("ALTER TABLE jobs ADD COLUMN demand TEXT")


def _mig_journal_payload(conn: sqlite3.Connection) -> None:  # v11 → v12
    # PLAN-12 Stufe 12.1: journal.payload für den Ausgabefilter (effective_kind
    # braucht den Payload auch rückwirkend für archivierte Läufe).
    if not _has_column(conn, "journal", "payload"):
        conn.execute("ALTER TABLE journal ADD COLUMN payload TEXT")


def _mig_jobs_active(conn: sqlite3.Connection) -> None:  # v12 → v13
    # PLAN-14 Stufe 14.5: Registrierungs-Flag — ist die MD noch im Vault
    # entdeckt? Bestehende Zeilen gelten als aktiv (waren beim letzten Rescan
    # entdeckt), Default 1 deckt das ab.
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "active"):
        conn.execute("ALTER TABLE jobs ADD COLUMN active INTEGER NOT NULL DEFAULT 1")


def _mig_transitions(conn: sqlite3.Connection) -> None:  # v13 → v14
    # User-Feedback 2026-07-07 (Lauf-Historie-Chart): journal hat nur eine
    # Zeile pro Lauf mit Endstatus, Zwischenzustände (z. B. eine mehrstündige
    # awaiting-Phase) gehen verloren. transitions loggt jeden Statuswechsel.
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS transitions ("
        "    id          INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    job_id      TEXT NOT NULL,"
        "    slug        TEXT NOT NULL,"
        "    from_status TEXT,"
        "    to_status   TEXT NOT NULL,"
        "    ts          REAL NOT NULL"
        ");"
        "CREATE INDEX IF NOT EXISTS transitions_ts_idx ON transitions (ts);"
    )


def _mig_jobs_pinned_host(conn: sqlite3.Connection) -> None:  # v14 → v15
    # PLAN-28: gepinnte /run-Läufe mit voller Scheduler-Lifecycle (Retry/Error/
    # Deferred/Zombie) — NULL = jeder Worker (heutiges Verhalten unverändert),
    # gesetzt = nur dieser Host darf reservieren (reserve_next()s neuer
    # pinned_only-Filter).
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "pinned_host"):
        conn.execute("ALTER TABLE jobs ADD COLUMN pinned_host TEXT")


def _mig_journal_pinned_host(conn: sqlite3.Connection) -> None:  # v15 → v16
    # PLAN-28: spiegelt jobs.pinned_host zum Schreibzeitpunkt — /-/run/journal
    # kann so "meine eigene /run-Historie" (domain='local' ODER pinned_host
    # gesetzt) von echten Team-Queue-Läufen unterscheiden, unabhängig von
    # domain (die für gepinnte Läufe jetzt 'scheduled' ist, echte jobs-Zeile).
    if not _has_column(conn, "journal", "pinned_host"):
        conn.execute("ALTER TABLE journal ADD COLUMN pinned_host TEXT")


def _mig_jobs_error_time(conn: sqlite3.Connection) -> None:  # v16 → v17
    # Pendant zu defer_time für den Fehlerfall: per-Schedule-Default für die
    # Retry-Backoff-Basis (statt nur des globalen BIBI_RETRY_BASE), s. job.py
    # Failed(seconds=…).
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "error_time"):
        conn.execute("ALTER TABLE jobs ADD COLUMN error_time INTEGER")


def _mig_approved_nodes(conn: sqlite3.Connection) -> None:  # v17 → v18
    # PLAN-32 Stufe 32.1 (Open-Trust-Connect-Gate): Freischaltung ist eine
    # Host-Entscheidung, kein Client-Selbstbericht — gehört bewusst NICHT ins
    # In-Memory-WorkerRegistry-Dict (das ist nur für selbstheilende
    # Heartbeat-Felder gedacht), sonst würde jeder Host-Neustart alle
    # Freischaltungen löschen.
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS approved_nodes ("
        "    node_id    TEXT PRIMARY KEY,"
        "    status     TEXT NOT NULL DEFAULT 'pending',"  # pending | approved | blocked
        "    updated_at REAL NOT NULL"
        ");"
    )


def _mig_jobs_docker_args(conn: sqlite3.Connection) -> None:  # v18 → v19
    # Generischer, unvalidierter `docker run`-Escape-Hatch (§7.6a) — JSON-Liste
    # roher CLI-Argumente, nur in exec_mode: container relevant.
    if _has_table(conn, "jobs") and not _has_column(conn, "jobs", "docker_args"):
        conn.execute("ALTER TABLE jobs ADD COLUMN docker_args TEXT")


#: Additive Migrationen für *bestehende* DBs: ``from_version -> [callable, …]``.
#: ``schema.sql`` ist das volle aktuelle Schema (frische DB); diese Schritte heben
#: ältere DBs Stück für Stück an, **idempotent** (PLAN-3 §3.1).
_MIGRATIONS: dict[int, list] = {
    1: [_mig_meta],
    2: [_mig_journal_domain],
    3: [_mig_jobs_deferred_at],
    4: [_mig_jobs_fire],
    5: [_mig_journal_commit],
    6: [_mig_jobs_exec_mode],
    7: [_mig_kind_normalize],
    8: [_mig_jobs_pid],
    9: [_mig_jobs_app_url],
    10: [_mig_jobs_ping_demand],
    11: [_mig_journal_payload],
    12: [_mig_jobs_active],
    13: [_mig_transitions],
    14: [_mig_jobs_pinned_host],
    15: [_mig_journal_pinned_host],
    16: [_mig_jobs_error_time],
    17: [_mig_approved_nodes],
    18: [_mig_jobs_docker_args],
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
    if version >= SCHEMA_VERSION:
        return  # bereits aktuell — kein Schreibzugriff nötig (PLAN-31 Baustein A)
    while version < SCHEMA_VERSION:  # bestehende DB: schrittweise migrieren
        for migrate in _MIGRATIONS.get(version, []):
            migrate(conn)
        version += 1
    conn.execute(f"PRAGMA user_version = {version}")


def call_with_lock_retry(fn, *, delays: tuple[float, ...] = (0.2, 0.5)):
    """Ruft ``fn()`` auf; bei ``sqlite3.OperationalError`` mit "locked" in der
    Meldung bis zu ``len(delays)`` weitere Versuche mit den angegebenen
    Backoff-Pausen (Sekunden). Jede andere Exception wird sofort durchgereicht.

    PLAN-31 Baustein B/C: sowohl der Setup-Report (``worker.py``s
    ``report_pid()``) als auch der Completion-Report (``wrapper/__init__.py``s
    ``_report_terminal()``) sollen einen kurzen, durch Baustein A stark
    reduzierten Rest-Lock überleben, statt den Job sofort als Fehler zu
    markieren bzw. den Statusübergang stillschweigend zu verlieren."""
    attempts = (0.0, *delays)
    for i, delay in enumerate(attempts):
        if delay:
            time.sleep(delay)
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or i == len(attempts) - 1:
                raise


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
    if sched is None or sched in ("startup", "autostart", "never", "on_demand"):
        return None
    if sched == "now":
        return now
    return _next_cron(sched, now)


_SPECIAL = ("now", "startup", "never", "on_demand", "autostart")


def is_recurring(schedule: str | None) -> bool:
    """True für wiederkehrende (croniter-)Schedules — nicht ``now``/``startup``/
    ``never`` und kein ``at:`` (one-shot)."""
    return schedule is not None and schedule not in _SPECIAL


def _next_cron(expr: str, now: float) -> float | None:
    """Nächste Feuerzeit für einen Cron-Ausdruck, in lokaler Wanduhrzeit.

    User-Fund 2026-07-06 ("nächster Lauf in 1h" stimmte nicht): ``croniter``
    interpretiert einen rohen Epoch-Float intern als UTC-Wanduhrzeit — auf
    einem Nicht-UTC-Knoten verschiebt das jede Berechnung um den UTC-Offset
    (verifiziert: 2h Differenz auf einem CEST-Knoten). Fix: ``now`` erst in
    eine lokale (naive, aber Wanduhrzeit-korrekte) ``datetime`` wandeln, dann
    croniter darauf rechnen lassen und wieder zu Epoch zurückwandeln."""
    try:
        import datetime

        import croniter
        local_now = datetime.datetime.fromtimestamp(now)
        next_dt = croniter.croniter(expr, local_now).get_next(datetime.datetime)
        return next_dt.timestamp()
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
        "model": s.model if s.model else None,
        "soul": s.soul,
        "session": s.session,
        "app_port": s.app_port,
        "app_prefix": s.app_prefix,
        "exec_mode": s.exec_mode,
        "image": s.image,
        "docker_args": json.dumps(s.docker_args) if s.docker_args else None,
        "attempts": s.attempts,
        "backoff": s.backoff,
        "silence_timeout": s.silence_timeout,
        "wall_time": s.wall_time,
        "defer_time": s.defer_time,
        "defer_max": s.defer_max,
        "error_time": s.error_time,
    }


# ── CRUD ─────────────────────────────────────────────────────────────────────


#: Status, deren next_fire_at ein laufender Retry-/Resume-Timer von Worker/Sweep
#: ist (§5.5) — kein aus dem Schedule ableitbares Datum. Ein Rescan-Overwrite auf
#: den nächsten Cron-Tick verhindert sonst sowohl den Backoff-Retry als auch das
#: spätere Eskalieren zu ``error`` (User-Feedback 2026-07-01: ein `failed`-Job mit
#: 2h-Cron blieb dadurch bis zu 1h "hängen" statt nach 30s Backoff zu retryn/
#: zu eskalieren). ``complete`` seit dem lazy Rearm (§5.2) ebenso betroffen: sein
#: next_fire_at ist der Timer bis zum nächsten Dispatch, kein Rescan-Datum.
_PRESERVE_NEXT_FIRE_AT = {"failed", "deferred", "complete"}

#: Echte Sackgassen (§5.5/report_status): beim Übergang dorthin setzt
#: report_status() next_fire_at bewusst auf NULL — "feuern nie automatisch,
#: erst nach explizitem START/RESET". Anders als bei _PRESERVE_NEXT_FIRE_AT
#: ist dieses NULL hier kein Unfall, der geheilt werden soll — ein Rescan
#: darf es nicht stillschweigend durch einen frischen Cron-Tick ersetzen,
#: sonst feuert ein `error`-Job doch wieder automatisch (User-Feedback
#: 2026-07-05, real beobachtet bei `news-aggregator`: ein Rescan gab ihm nach
#: dem Erschöpfen der attempts trotzdem einen neuen next_fire_at). START/RESET
#: setzen next_fire_at explizit selbst (start_now()/report_status() mit
#: übergebenem Wert) — die bleiben davon unberührt.
_FROZEN_UNTIL_USER_ACTION = {"error", "killed", "inactive", "zombie"}


def upsert_schedule(conn: sqlite3.Connection, pr: ParseResult, now: float) -> str:
    """Schedule einfügen (status pending, neue ID) oder Spec-Spalten aktualisieren.

    Schlüssel ist der Slug. Bei Update bleiben ``id`` und Live-Status erhalten —
    nur die aus der MD abgeleiteten Felder werden neu geschrieben. Ausnahmen bei
    ``next_fire_at``:

    - ``_FROZEN_UNTIL_USER_ACTION``: bleibt **immer** unangetastet (auch wenn
      ``NULL``) — das ist dort der gewollte Dauerzustand, kein Heilungsfall.
    - ``_PRESERVE_NEXT_FIRE_AT``: bleibt unangetastet, wenn der Job dort bereits
      einen echten Timer trägt (s.o.). Ist er dort stattdessen ``NULL`` (kein
      Timer gesetzt — z. B. weil ein manueller Start auf einen Zwischenstand
      traf, bevor das Schedule live geschaltet war, real beobachtet 2026-07-05
      bei `gmail-transfer`), gibt es sonst **keinen** Weg mehr zurück:
      ``reserve_next()`` verlangt ``next_fire_at IS NOT NULL`` und der Job
      bliebe für immer eingefroren. In diesem Fall lässt dieser Upsert den
      frisch berechneten Wert stehen — der nächste (periodische) Rescan heilt
      den Job so von selbst.
    """
    cols = _spec_columns(pr, now)
    cols["active"] = 1  # jeder erfolgreiche Upsert kommt von einer entdeckten MD
    # (PLAN-14 Stufe 14.5) — reaktiviert einen zuvor deaktivierten Slug automatisch.
    existing = conn.execute(
        "SELECT id, status, next_fire_at FROM jobs WHERE slug=?", (cols["slug"],)).fetchone()
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
    if existing["status"] in _FROZEN_UNTIL_USER_ACTION:
        cols.pop("next_fire_at", None)
    elif existing["status"] == "complete" and cols.get("schedule") is None:
        # PLAN-23 Befund 1: ein abgeschlossener oneshot (`at:`, schedule=None)
        # darf beim Rescan nicht "geheilt" werden — compute_next_fire() hat für
        # at: keine Vergangenheits-Prüfung, ein NULL hier ist gewollter
        # Dauerzustand (wie bei _FROZEN_UNTIL_USER_ACTION), kein Unfall.
        # Wiederkehrende complete-Jobs (schedule gesetzt) durchlaufen weiter
        # den Zweig darunter — deren Heilung bleibt unangetastet.
        cols.pop("next_fire_at", None)
    elif existing["status"] in _PRESERVE_NEXT_FIRE_AT and existing["next_fire_at"] is not None:
        cols.pop("next_fire_at", None)
    cols["updated_at"] = now
    assignments = ", ".join(f"{k}=:{k}" for k in cols)
    conn.execute(f"UPDATE jobs SET {assignments} WHERE slug=:slug", cols)
    return existing["id"]


def deactivate_slugs(conn: sqlite3.Connection, slugs: set[str]) -> int:
    """MDs, die beim Rescan nicht mehr gefunden wurden, als inaktiv markieren
    statt zu löschen (PLAN-14 Stufe 14.5) — die Journal-Historie bleibt über
    die Zeile per Slug erreichbar (Schedules-Übersicht „Inaktiv"-Gruppe)."""
    n = 0
    for slug in slugs:
        cur = conn.execute(
            "UPDATE jobs SET active=0 WHERE slug=? AND active=1", (slug,))
        n += cur.rowcount
    return n


def active_worktree_slugs(conn: sqlite3.Connection) -> set[str]:
    """Slugs, deren Job-Worktree (``data/worktrees/<slug>/``) noch gebraucht
    wird — nicht jede je gesehene Zeile (Bug "Kein Worktree Cleanup", Case
    20260621.Bibi4-870bd9db, 2026-07-22).

    ``active=0`` heißt: die Schedule-MD ist bei einem Rescan aus dem Vault
    verschwunden (``deactivate_slugs()`` oben), die Zeile bleibt nur wegen
    der Journal-Historie stehen (PLAN-14 §14.5) — nie wieder ein Fire. Ein
    terminaler Job ohne ``next_fire_at`` (einmaliger Job, fertig) feuert
    ebenso nie wieder, auch wenn seine MD noch im Vault liegt — anders als
    ein wiederkehrender Job, dessen ``next_fire_at`` schon den nächsten
    Zyklus trägt (s. ``reserve_next()``s ``status='complete' AND
    next_fire_at IS NOT NULL``-Zweig weiter unten). Gemeinsame Quelle für
    den Orphan-Worktree-Check (``doctor``) UND den periodischen
    Worktree-Sweep (``Synchronizer``) — beide müssen exakt dasselbe
    "noch in Gebrauch" verstehen, sonst räumt der Sweep etwas weg, das
    doctor gerade erst als unbedenklich eingestuft hat, oder umgekehrt."""
    rows = conn.execute("SELECT slug, status, active, next_fire_at FROM jobs").fetchall()
    known: set[str] = set()
    for r in rows:
        if not r["active"]:
            continue
        if lifecycle.is_terminal(Status(r["status"])) and r["next_fire_at"] is None:
            continue
        known.add(r["slug"])
    return known


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Aktuelle Zustands-Zählung aller aktiven Jobs (PLAN-21 Befund 11 Stat-Grid)."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM jobs WHERE active=1 GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def status_counts_by_kind(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Wie ``status_counts()``, zusätzlich nach ``models.display_kind()``
    aufgeschlüsselt — Grundlage der Job-Status-Matrix (Bibi4-Iteration,
    User-Fund: "Apps enden nicht"). Klassifikation in Python statt SQL, damit
    ``display_kind()`` die einzige Quelle für job/claude/app bleibt. Immer
    alle drei Kind-Schlüssel vorhanden (auch mit leerem Dict), damit Aufrufer
    gefahrlos ``.get(kind, {})`` ohne weiteren Default-Fall nutzen können."""
    rows = conn.execute(
        "SELECT status, payload, app_port FROM jobs WHERE active=1"
    ).fetchall()
    out: dict[str, dict[str, int]] = {"job": {}, "claude": {}, "app": {}}
    for r in rows:
        bucket = out[display_kind(r["payload"], r["app_port"])]
        bucket[r["status"]] = bucket.get(r["status"], 0) + 1
    return out


def next_due_at(conn: sqlite3.Connection) -> float | None:
    """Kleinster ``next_fire_at`` über alle aktiven Jobs (PLAN-26 Befund 3,
    Job-Status-Kachel: "nächster Job in …"). ``None``, wenn kein aktiver Job
    einen Trigger gesetzt hat."""
    row = conn.execute(
        "SELECT MIN(next_fire_at) AS m FROM jobs WHERE active=1 AND next_fire_at IS NOT NULL"
    ).fetchone()
    return row["m"] if row else None


def list_jobs(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    # active=1 (PLAN-14 Stufe 14.5): dies speist die Root-Bänder (Live-Betrieb) —
    # ein deaktivierter Schedule (MD entfernt) gehört dort nicht mehr hin, nur
    # noch in die Schedules-Übersicht „Inaktiv"-Gruppe (list_schedules()).
    if status:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status=? AND active=1 "
            "ORDER BY priority DESC, enqueued_at ASC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE active=1 ORDER BY priority DESC, enqueued_at ASC"
        ).fetchall()
    # Letzter abgeschlossener Lauf je Slug (für Bands-Anzeige).
    last_run_at: dict[str, float] = {}
    for r in conn.execute(
        "SELECT slug, MAX(finished_at) AS last_at FROM journal"
        " WHERE finished_at IS NOT NULL GROUP BY slug"
    ).fetchall():
        if r["last_at"] is not None:
            last_run_at[r["slug"]] = r["last_at"]
    return [job_view(r, last_run_at=last_run_at.get(r["slug"])) for r in rows]


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return job_view(row) if row else None


def get_job_exec_mode(conn: sqlite3.Connection, job_id: str) -> tuple[str, str | None] | None:
    """``(slug, exec_mode)`` — die REBUILD-Aktion (PLAN-24 Befund 5) muss
    wissen, ob ein Job überhaupt im Container-Modus läuft, bevor sie dessen
    per-Job-Image verwirft."""
    row = conn.execute("SELECT slug, exec_mode FROM jobs WHERE id=?", (job_id,)).fetchone()
    return (row["slug"], row["exec_mode"]) if row else None


def list_schedules(conn: sqlite3.Connection) -> list[dict]:
    # Letzten disponierten Lauf je Slug aus dem Journal (für STATUS = „letzter Lauf",
    # nicht der nach Cron-Re-Arm harmlose Zeilen-Status `pending`).
    last: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT j.id, j.slug, j.status, j.finished_at FROM journal j JOIN ("
        "  SELECT slug, MAX(id) AS mx FROM journal WHERE domain='scheduled' GROUP BY slug"
        ") m ON j.id = m.mx"
    ).fetchall():
        last[r["slug"]] = {"id": r["id"], "status": r["status"], "finished_at": r["finished_at"]}
    rows = conn.execute("SELECT * FROM jobs ORDER BY slug").fetchall()
    out = [schedule_view(r, last_run=last.get(r["slug"])) for r in rows]

    # Journal-only-Phantome (PLAN-14 Stufe 14.6): Slugs mit disponierter
    # Journal-Historie, aber ohne (mehr) zugehörige jobs-Zeile — vor Stufe 14.5
    # löschte remove_slugs() die Zeile hart, alte DBs kennen diesen Zustand noch.
    # domain='scheduled' schließt /run-lokale Läufe aus (waren nie Schedules).
    # active=None markiert die dritte Gruppe (Schedules-Übersicht „Journal").
    known = {r["slug"] for r in rows}
    for r in conn.execute(
        "SELECT j.id, j.slug, j.status, j.finished_at, j.kind, j.payload FROM journal j JOIN ("
        "  SELECT slug, MAX(id) AS mx FROM journal WHERE domain='scheduled' GROUP BY slug"
        ") m ON j.id = m.mx"
    ).fetchall():
        if r["slug"] in known:
            continue
        out.append({
            "slug": r["slug"], "kind": r["kind"], "trigger": "",
            "next_fire_at": None, "last_status": r["status"],
            "last_run_at": r["finished_at"], "last_run_id": r["id"],
            "row_status": r["status"],
            "oneshot": True, "payload": r["payload"], "app_port": None,
            "active": None,
        })
    return out


# ── Row → View (JobView/ScheduleView-Form, §3.0-Schemata) ────────────────────


def job_view(row: sqlite3.Row, *, last_run_at: float | None = None) -> dict:
    return {
        "id": row["id"], "slug": row["slug"], "kind": row["kind"],
        "status": row["status"], "reason": row["reason"], "priority": row["priority"],
        "enqueued_at": row["enqueued_at"], "started_at": row["started_at"],
        "finished_at": row["finished_at"], "exit_code": row["exit_code"],
        "attempt": row["attempt"], "host": row["host"], "worker": row["worker"],
        "output_ref": row["output_ref"], "next_fire_at": row["next_fire_at"],
        "last_run_at": last_run_at, "schedule": row["schedule"],
        "app_port": row["app_port"], "app_url": row["app_url"],
        "active": bool(row["active"]),
        # nur intern genutzt (Ausgabefilter, PLAN-12 Stufe 12.4/12.5) — response_model=JobView
        # deklariert dieses Feld nicht, FastAPI/Pydantic filtert es beim Serialisieren heraus.
        "payload": row["payload"],
    }


def job_full_view(row: sqlite3.Row) -> dict:
    """Alle DB-Felder eines Jobs — für die Attribute-Ansicht (§10.x)."""
    return {
        **job_view(row),
        "model": row["model"],
        "soul": row["soul"],
        "session": row["session"],
        "attempts": row["attempts"],
        "backoff": row["backoff"],
        "silence_timeout": row["silence_timeout"],
        "wall_time": row["wall_time"],
        "defer_time": row["defer_time"],
        "defer_max": row["defer_max"],
        "error_time": row["error_time"],
        "app_prefix": row["app_prefix"],
        "exec_mode": row["exec_mode"],
        "image": row["image"],
        "docker_args": row["docker_args"],
        "at_iso": row["at_iso"],
        "fire": row["fire"],
        "deferred_at": row["deferred_at"],
        "pid": row["pid"],
        "schedule_ref": row["schedule_ref"],
    }


def get_job_by_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE slug=?", (slug,)).fetchone()
    return job_full_view(row) if row else None


#: Nicht-terminale Zeilen-Status — die STATUS-Spalte zeigt sie direkt statt des
#: letzten Journal-Ergebnisses (User-Feedback 2026-07-01: ein `failed`-Retry
#: zeigte 40+s lang noch `error` vom vorherigen Zyklus, weil nur `running` als
#: "live" galt — dieselbe Lücke hätte `awaiting`/`deferred` genauso getroffen).
#: `pending` war hier ursprünglich bewusst ausgenommen ("nicht bloß ein
#: routinemäßiger Cron-Re-Arm") — das griff aber nicht: ein wiederkehrender
#: Job bleibt zwischen Zyklen `complete` und wird von `reserve_next()` direkt
#: dorthin dispatcht (lazy Rearm), durchläuft `pending` also gar nie routine-
#: mäßig. `pending` mit `started_at` ungleich `None` kommt praktisch nicht vor
#: (RESET räumt `started_at` explizit, s. `report_status()`) — PLAN-22 Befund 2:
#: nach RESET zeigte die Übersicht sonst weiterhin den alten Terminal-Status
#: (z. B. "killed") statt "pending", weil genau dieser Ausschluss griff.
_LIVE_ROW_STATUSES = {"starting", "running", "failed", "awaiting", "deferred", "pending"}


def schedule_view(row: sqlite3.Row, last_run: dict | None = None) -> dict:
    trigger = row["schedule"] if row["schedule"] is not None else row["at_iso"]
    row_status = row["status"]
    # STATUS-Spalte: gerade aktiv (_LIVE_ROW_STATUSES) → Zeilen-Status direkt;
    # sonst Ergebnis des letzten Laufs (Journal); sonst der Zeilen-Status (nie
    # gelaufen → pending). Bei running/failed/awaiting/deferred ist finished_at
    # entweder noch NULL oder veraltet (der laufende/wartende Zyklus ist ja noch
    # nicht fertig) — last_run_at zeigt hier stattdessen started_at, damit die
    # "seit"-Spalte die Laufzeit anzeigt statt "—" (User-Feedback).
    if row_status in _LIVE_ROW_STATUSES:
        last_status, last_run_at = row_status, row["started_at"]
    elif last_run is not None:
        # Wenn die Jobs-Zeile einen neueren Terminal-Zustand hat (finished_at aktueller),
        # gewinnt sie — der Journal-MAX-Eintrag kann durch den Dedup-Skip veraltet sein.
        row_ft = row["finished_at"]
        last_ft = last_run["finished_at"]
        if (lifecycle.is_terminal(Status(row_status))
                and row_ft is not None
                and (last_ft is None or row_ft > last_ft)):
            last_status, last_run_at = row_status, row_ft
        else:
            last_status, last_run_at = last_run["status"], last_ft
    else:
        last_status, last_run_at = row_status, row["finished_at"]
    # Journal-ID des letzten abgeschlossenen Laufs — Ziel für den "Lauf Details"-
    # Link (User-Feedback 2026-07-01); unabhängig vom aktuellen Live-Status, da
    # ein laufender Job noch keine eigene Journal-Zeile hat.
    last_run_id = last_run["id"] if last_run is not None else None
    return {
        "slug": row["slug"], "kind": row["kind"], "trigger": trigger or "",
        "next_fire_at": row["next_fire_at"], "last_status": last_status,
        "last_run_at": last_run_at, "last_run_id": last_run_id, "row_status": row_status,
        # One-shot (at:) hat kein wiederkehrendes schedule — Basis fürs Archiv (§4.4).
        "oneshot": row["schedule"] is None,
        # kind ist seit PLAN-10 (Unified Job Model) immer "job" — payload/app_port
        # sind die einzige Quelle, um claude-/app-artige Schedules zu unterscheiden
        # (FE-Typ-Filter, render.py _effective_sched_type).
        "payload": row["payload"], "app_port": row["app_port"],
        # Registrierungs-Zustand (PLAN-14 Stufe 14.6): True = MD aktuell entdeckt.
        "active": bool(row["active"]),
        # PLAN-24 Befund 5: der Controller braucht das, um die REBUILD-Aktion
        # nur bei Container-Jobs anzuzeigen (render._action_bar()).
        "exec_mode": row["exec_mode"],
        # Batch 9 Punkt 1 (Host-Sparkline-Spalte): case-dir-relativer Pfad der
        # Schedule-MD, Grundlage für den repo-root-relativen "repo_path", den
        # controller._sched_sparkline_series() für _job_sparkline_series()
        # baut — dieselbe Ableitung wie schon lange bei job_full_view()
        # (Zeile oben), hier nur zusätzlich in der schlankeren Listen-Sicht.
        "schedule_ref": row["schedule_ref"],
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
    # "removed" heißt seit PLAN-14 Stufe 14.5 "deaktiviert" (Zeile bleibt,
    # active=0) — Feldname aus Kompatibilität zu bibi/ctrl/job_cmd.py,
    # bibi/daemon/rescanner.py unverändert belassen (nur Logging, kein Branch).
    removed = deactivate_slugs(conn, existing - discovered)
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
        "soul": row["soul"], "session": row["session"],
        # fire (Pro-Lauf-Zähler, §5.2): der Worker bildet daraus run_id=slug:fire
        # → per-Run-Output-Pfad (kein Akkumulieren über Läufe).
        "fire": row["fire"],
        # Ausführungs-/Retry-Parameter, damit ein Remote-Worker (ohne lokale DB)
        # überwachen + Backoff rechnen kann (§3.6).
        "attempt": row["attempt"], "attempts": row["attempts"],
        "backoff": row["backoff"], "wall_time": row["wall_time"],
        "silence_timeout": row["silence_timeout"],
        "app_port": row["app_port"], "app_prefix": row["app_prefix"],
        "exec_mode": row["exec_mode"],
        # PLAN-24 Befund 1: image war zwar in der DB (_spec_columns), ging aber
        # nie an den Worker durch — dasselbe Bug-Muster wie app_port/exec_mode
        # vor PLAN-22 und oneshot vor PLAN-23.
        "image": row["image"],
        "docker_args": json.loads(row["docker_args"]) if row["docker_args"] else None,
        "defer_time": row["defer_time"],
        "error_time": row["error_time"],
        # Vault-relativer Pfad der Schedule-MD (unter case_dir) — der Worker
        # leitet daraus das Job-cwd ab (Verzeichnis der MD, nicht Worktree-Root).
        "schedule_ref": row["schedule_ref"],
        "env": {},
    }


def reserve_next(
    conn: sqlite3.Connection, *, worker: str | None = None,
    host: str | None = None, now: float | None = None, pinned_only: bool = False,
) -> dict | None:
    """Nächstbesten Job atomar reservieren (§4.4/§3.2). ``None`` = nichts zu tun.

    Auswahl (Fairness-Offset), Lock (Compare-and-Swap) und Offset-Vorrücken
    laufen in **einer** ``BEGIN IMMEDIATE``-Transaktion — SQLite serialisiert
    Writer, also können zwei gleichzeitige ``/next`` denselben Job nicht bekommen
    und der read-modify-write des Cursors bleibt konsistent (Invariante: genau 1
    Scheduler).

    ``pinned_host`` (PLAN-28) schränkt ein, welcher Host reservieren darf:
    ``NULL`` = wie bisher jeder Worker; gesetzt = nur der eine Host. Das gilt
    **immer**, unabhängig von ``pinned_only`` — ein für Host A gepinnter Job
    darf nie von Host B reserviert werden, auch nicht über den normalen
    Team-Pfad. ``pinned_only=True`` (vom neuen, rollenunabhängigen
    ``LocalPinnedLoop`` genutzt) geht noch einen Schritt weiter: **nur**
    gepinnte Zeilen dieses Hosts sind eligibel, ungepinnte (Team-Queue-)Zeilen
    bleiben komplett unangetastet — der lokale Loop darf nie in die geteilte
    Warteschlange greifen.
    """
    now = time.time() if now is None else now
    host = host or socket.gethostname()
    conn.execute("BEGIN IMMEDIATE")
    try:
        offset = _get_offset(conn)
        pin_clause = ("pinned_host = :host" if pinned_only
                     else "(pinned_host IS NULL OR pinned_host = :host)")
        # Eligibel nur, was **fällig** ist (§5.2): pending feuert erst, wenn
        # next_fire_at gesetzt UND erreicht ist (`now` ⇒ sofort, `at:`/cron ⇒ zur
        # Zeit, `never` ⇒ next_fire_at NULL ⇒ nie). Dazu retriable failed (Backoff
        # fällig), fällige deferred (resume) und fällige complete (lazy Rearm —
        # der Job bleibt bis hierhin sichtbar `complete`), §5.4.
        #
        # Bugfix (User-Fund: "ein Failed wechselt sofort nach Ende auf ERROR,
        # falls keine Versuche mehr übrig sind" — beobachtet aber stattdessen
        # ein Failed, das für immer liegen blieb und erst durch den Sweeper
        # extern zu error gezwungen wurde): das frühere ``attempt < attempts``
        # hier war off-by-one UND redundant. _finish() (wrapper/__init__.py)
        # entscheidet bereits synchron beim Abschluss jedes Versuchs "attempt_cur
        # < attempts_max ⇒ Retry gewähren, sonst SOFORT (in derselben
        # Wrapper-Instanz) direkt nach 'error', 'failed' wird dabei komplett
        # übersprungen" — ein Job landet also PER KONSTRUKTION nur dann als
        # 'failed' in der DB, wenn der zuletzt gewährte Retry noch aussteht,
        # nie wenn er erschöpft ist. Die doppelte Prüfung hier verglich zudem
        # den BEREITS INKREMENTIERTEN ``attempt`` (den Wert NACH der Gewährung)
        # mit ``<`` statt ``<=`` gegen ``attempts`` — genau der zuletzt
        # gewährte, noch nicht verbrauchte Versuch (``attempt == attempts``)
        # wurde dadurch nie dispatcht: die Zeile blieb ewig 'failed' liegen,
        # bis der (separate, verzögerte) Sweeper sie zu 'error' zwang, statt
        # dass der Wrapper selbst sofort und synchron entscheidet. 'failed'
        # jetzt wie 'deferred' rein zeitbasiert eligibel — job_db.sweep()s
        # eigene "attempt >= attempts"-Erschöpfung bleibt als Sicherheitsnetz
        # für Anomalien (z. B. ein abgestürzter Wrapper vor dem Report), greift
        # im Normalbetrieb aber nicht mehr, weil 'failed' das per Konstruktion
        # nie mehr erreicht.
        rows = conn.execute(
            "SELECT id, slug, status, priority, enqueued_at, rowid AS seq FROM jobs "
            f"WHERE active=1 AND locked_at IS NULL AND {pin_clause} AND ("
            "  (status='pending' AND next_fire_at IS NOT NULL AND next_fire_at <= :now)"
            "  OR (status='failed' AND next_fire_at IS NOT NULL AND next_fire_at <= :now)"
            "  OR (status='deferred' AND next_fire_at IS NOT NULL AND next_fire_at <= :now)"
            "  OR (status='complete' AND next_fire_at IS NOT NULL AND next_fire_at <= :now)"
            ")",
            {"now": now, "host": host},
        ).fetchall()
        chosen, new_offset = dispatcher.select([dict(r) for r in rows], offset)
        if chosen is None:
            conn.execute("COMMIT")
            return None
        # Quelle 'complete' braucht zusätzlich das Aufräumen des alten Laufs (früher
        # der Eager-Rearm in report_status()): frischer fire-Zähler (eindeutige
        # run_id), Attempt-Reset, terminaler Snapshot weg. Für pending/failed/deferred
        # sind diese Felder ohnehin schon leer/unverändert — die CASEs sind dort No-ops.
        # status='starting' (m.rau/bibi#38): die Reservierung landet nicht mehr
        # direkt auf 'running'. Der Wrapper ist an dieser Stelle noch nicht
        # gespawnt — es gibt also keine PID, und ein 'running' ohne PID war
        # genau die Lücke, die eine PID-basierte Waisen-Prüfung unmöglich
        # machte. pid/pid_started_at werden mitgenullt, damit ein Wiederanlauf
        # (complete → starting via Lazy Rearm, failed → starting via Retry)
        # nicht die PID des VORIGEN Laufs erbt und der Check sie für den neuen
        # hält.
        cur = conn.execute(
            "UPDATE jobs SET status='starting', locked_at=:now, started_at=:now, "
            "worker=:w, host=:h, pid=NULL, pid_started_at=NULL, "
            "fire        = CASE WHEN status='complete' THEN fire+1 ELSE fire END, "
            "attempt     = CASE WHEN status='complete' THEN 0 ELSE attempt END, "
            "finished_at = CASE WHEN status='complete' THEN NULL ELSE finished_at END, "
            "exit_code   = CASE WHEN status='complete' THEN NULL ELSE exit_code END, "
            "output_ref  = CASE WHEN status='complete' THEN NULL ELSE output_ref END, "
            "reason      = CASE WHEN status='complete' THEN NULL ELSE reason END, "
            "deferred_at = CASE WHEN status='complete' THEN NULL ELSE deferred_at END "
            "WHERE id=:id AND status IN ('pending','failed','deferred','complete') "
            "AND locked_at IS NULL",
            {"now": now, "w": worker, "h": host, "id": chosen["id"]},
        )
        if cur.rowcount != 1:  # unter BEGIN IMMEDIATE eigentlich unerreichbar
            conn.execute("ROLLBACK")
            return None
        _set_offset(conn, new_offset)
        global _dispatch_count
        _dispatch_count += 1
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
    next_fire_at: float | None = None, commit_sha: str | None = None,
    branch: str | None = None, app_url: str | None = None,
    now: float | None = None,
) -> str:
    """Worker meldet einen Zustandswechsel (§4.4, output-frei). Rückgabe:
    ``ok`` | ``invalid`` (verbotener Übergang, §5.4) | ``not_found``."""
    now = time.time() if now is None else now
    row = conn.execute("SELECT status, kind, schedule, slug FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return "not_found"
    current = Status(row["status"])
    target = Status(status)
    if target is current and target in lifecycle.TERMINAL:
        # Idempotenter Wiederholungs-Report (z. B. doppelter Kill-Klick, PLAN-14
        # 14.1b): echtes No-Op statt finished_at/reason erneut zu setzen —
        # _write_journal dedupliziert zwar über (run_id, status), die jobs-Zeile
        # driftete aber trotzdem bei jedem Wiederholungs-Report nach vorn.
        return "ok"
    if target != current and target not in lifecycle.targets(current, kind=Kind(row["kind"])):
        return "invalid"
    # PLAN-23 Befund 3: lifecycle.py erlaubt (COMPLETE, RESET) → PENDING generell
    # (richtig für wiederkehrende Schedules) — ein abgeschlossener oneshot
    # (`at:`, schedule=None) ist mit complete aber erledigt; eine neue
    # Ausführung braucht ein neues MD (neuer Slug), keinen Reset des alten.
    if target is Status.PENDING and current is Status.COMPLETE and row["schedule"] is None:
        return "invalid"

    fields: dict = {"status": target.value, "reason": reason, "updated_at": now}
    if target in lifecycle.TERMINAL:
        fields["finished_at"] = now
    # failed/deferred/pending sind wieder dispatchbar ⇒ Lock lösen (reserve braucht NULL).
    # complete ebenso: reserve_next() darf einen fällig gewordenen complete-Job selbst
    # dispatchen (lazy Rearm, §5.2 — siehe COMPLETE-Zweig unten), das Lock vom letzten
    # Lauf muss dafür schon jetzt frei sein.
    if target in (Status.FAILED, Status.DEFERRED, Status.PENDING, Status.COMPLETE):
        fields["locked_at"] = None
    if target is Status.DEFERRED:
        # deferred_at = Zeitpunkt des ersten Defers (für defer_max-Sweep); nur beim ersten Mal setzen.
        da_row = conn.execute("SELECT deferred_at FROM jobs WHERE id=?", (job_id,)).fetchone()
        if da_row and da_row["deferred_at"] is None:
            fields["deferred_at"] = now
    if target is Status.PENDING:  # reset = archivieren + Trigger neu auswerten (§5.6)
        fields["attempt"] = 0
        fields["reason"] = None
        fields["deferred_at"] = None
        # PENDING ist ein eigener, sauberer Eintrag (User-Feedback 2026-07-03) —
        # der Lauf-Snapshot des vorigen (terminalen) Zyklus darf nicht bis zum
        # nächsten Dispatch stehen bleiben. reserve_next() überschreibt started_at
        # ohnehin bei jedem Dispatch, aber bis dahin soll die Zeile nicht die
        # Werte eines bereits abgeschlossenen Laufs zeigen.
        fields["started_at"] = None
        fields["finished_at"] = None
        fields["exit_code"] = None
        fields["output_ref"] = None
        # Reset respektiert den Trigger, statt blind sofort zu feuern (User-
        # Feedback: RESET reihte bei `schedule: never` fälschlich einen neuen
        # Lauf ein — "steht ja auf never"). Wiederkehrende Schedules bekommen
        # den nächsten regulären Cron-Tick, alles andere (never/on_demand/
        # startup/at:) bleibt unfällig (next_fire_at=None) bis zu einem
        # expliziten START — der erzwingt "sofort" via next_fire_at=now
        # (siehe start_now()).
        fields["next_fire_at"] = (
            _next_cron(row["schedule"], now) if is_recurring(row["schedule"]) else None
        )
    if target is Status.KILLED and current is Status.COMPLETE:
        # User-Redesign 2026-07-20 (lifecycle.py: (COMPLETE, KILL) → KILLED):
        # derselbe Archiv-Schritt wie beim PENDING-Zweig oben — der
        # abgeschlossene Lauf darf keine stale Startzeit/Exit-Code/Attempts
        # mehr zeigen, sobald die Zeile auf einen frischen (sofort toten)
        # Zyklus wechselt. finished_at NICHT hier anfassen — der generische
        # TERMINAL-Zweig oben hat es schon korrekt auf "jetzt" gesetzt (der
        # Zeitpunkt, zu dem DIESER Zyklus endete, nicht der alte). next_fire_at
        # ebenfalls nicht hier setzen — kommt gleich aus dem KILLED/ERROR/…-
        # Zweig unten (None statt Cron-Neuberechnung: anders als PENDING soll
        # dieser Zyklus nicht von selbst wieder feuern).
        fields["attempt"] = 0
        fields["deferred_at"] = None
        fields["started_at"] = None
        fields["exit_code"] = None
        fields["output_ref"] = None
    if target in (Status.KILLED, Status.ERROR, Status.INACTIVE, Status.ZOMBIE):
        # Echte Sackgassen: ein evtl. noch gesetzter next_fire_at (Backoff-Timer aus
        # dem vorigen failed/deferred, oder Rest von vor einem KILL) ist jetzt eine
        # Karteileiche — sie feuern nie automatisch, erst nach explizitem START/RESET.
        fields["next_fire_at"] = None
    if target is Status.COMPLETE:
        # Lazy Rearm (User-Feedback: "archiviert wird erst vor dem nächsten Rerun") —
        # der Job bleibt sichtbar `complete` (Status/Output/Zeiten unangetastet) bis
        # reserve_next() ihn beim tatsächlich fälligen nächsten Tick selbst dispatcht
        # (siehe dortiger complete-Zweig). Kein sofortiges Zurückspringen auf pending.
        fields["next_fire_at"] = (
            _next_cron(row["schedule"], now) if is_recurring(row["schedule"]) else None
        )
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
    if target is Status.AWAITING:
        if app_url is not None:
            fields["app_url"] = app_url
    else:
        fields["app_url"] = None  # HITL beendet oder Terminal → Endpunkt löschen

    assignments = ", ".join(f"{k}=:{k}" for k in fields)
    # RESET (und, seit 2026-07-20, KILL auf COMPLETE) → neuer Lauf-Zähler, damit
    # run_id = slug:fire in `_write_journal` eindeutig bleibt. Ohne fire++ würde
    # der Dedup-Check (run_id, status) spätere Journal-Einträge stillschweigend
    # unterdrücken, wenn derselbe Status aus einem früheren Zyklus schon im
    # Journal steht — für COMPLETE→KILLED konkret: der alte Lauf hat schon
    # seinen eigenen "complete"-Journal-Eintrag (COMPLETE ist selbst terminal),
    # der neue, sofort tote Zyklus braucht einen eigenen run_id für seinen
    # "killed"-Eintrag.
    if target is Status.PENDING or (target is Status.KILLED and current is Status.COMPLETE):
        assignments += ", fire=fire+1"
    fields["id"] = job_id

    # Terminal-Übergang → eine Journal-Zeile (disponierte Domäne, §1.4). complete
    # rearmt NICHT mehr sofort hier — das übernimmt reserve_next() lazy, sobald der
    # nächste next_fire_at-Tick tatsächlich fällig ist (siehe Kommentar oben).
    #
    # **Status und Journal-Zeile sind EIN Vorgang** (m.rau/bibi#95). Ohne Klammer
    # committen sie einzeln (``connect()``: „übrige Schreibpfade committen je
    # Statement") — und dazwischen liegt ein Fenster, in dem jeder Leser einen
    # terminalen Job ohne Journal-Zeile sieht. Das trifft nicht nur die Suite:
    # FE, CLI und jeder andere Knoten lesen dieselbe DB. „Fertig" ist logisch
    # ein Vorgang und darf nicht als zwei sichtbar werden.
    #
    # Nur der terminale Fall wird geklammert: sonst ist das UPDATE ein einzelnes
    # Statement und für sich atomar. Eine fremde Transaktion wird nicht
    # angetastet — dann committet der Aufrufer.
    if target not in lifecycle.TERMINAL:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id=:id", fields)
        return "ok"

    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id=:id", fields)
        _write_journal(conn, job_id, now, commit_sha=commit_sha, branch=branch)
    except BaseException:
        if own_tx:
            conn.execute("ROLLBACK")
        raise
    if own_tx:
        conn.execute("COMMIT")
    return "ok"


def sweep(conn: sqlite3.Connection, now: float | None = None) -> dict:
    """Zeitgesteuerte Scheduler-Übergänge (§5.4/§5.5; PLAN-3 §3.5).

    - **failed ohne next_fire_at** (Crash-Recovery, s. u.) → ``error``.
    - **deferred + abgelaufen** (``defer_max`` überschritten) → ``inactive``
      (``deferred_expired``).

    Worker-seitige Übergänge (wall_time/silence/no_process während der Ausführung)
    macht der Worker selbst — der Sweep deckt nur die rein zeit-/zählerbasierten
    Scheduler-Entscheidungen ab.

    Bugfix (User-Fund: "ein Failed wechselt sofort nach Ende auf ERROR, falls
    keine Versuche mehr übrig sind — nicht erst beim nächsten [Sweep-]Versuch"):
    der frühere ``attempt >= attempts``-Zweig hier setzte genau die falsche
    Prämisse voraus, die auch ``reserve_next()``s inzwischen entfernte
    ``attempt < attempts``-Bedingung hatte — ``_finish()``
    (``wrapper/__init__.py``) löst Erschöpfung längst SYNCHRON auf: erschöpft
    ein Versuch (``attempt_cur >= attempts_max``), meldet der Wrapper im selben
    Aufruf direkt ``error``, ``failed`` wird dabei komplett übersprungen. Eine
    Zeile mit ``status='failed'`` schuldet also per Konstruktion IMMER noch
    einen Dispatch — ``attempt >= attempts`` hier hätte, jetzt wo der Sweeper
    rollenunabhängig auf jedem Knoten läuft, gegen genau diesen noch
    ausstehenden letzten ``reserve_next()``-Dispatch geracet und ihn manchmal
    vorzeitig zu ``error`` gezwungen, bevor der Wrapper selbst entscheiden
    konnte. Echter Crash-Recovery-Fall bleibt trotzdem abgedeckt: die
    Erschöpfungs-Meldung in ``_finish()`` schreibt ``failed`` mit
    ``next_fire_at=None`` als reinen Zwischenschritt, bevor sie synchron
    ``error`` nachschiebt — stirbt der Wrapper-Prozess genau dazwischen, bleibt
    eine Zeile ohne jedes ``next_fire_at`` zurück, die ``reserve_next()`` nie
    wieder findet (``next_fire_at IS NOT NULL`` dort). Nur DAS ist der noch
    gültige Sweep-Fall."""
    now = time.time() if now is None else now
    errored = inactivated = 0
    for r in conn.execute(
        "SELECT id FROM jobs WHERE status='failed' AND next_fire_at IS NULL"
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


def fire_startup(conn: sqlite3.Connection, now: float | None = None) -> int:
    """``schedule: startup``-Jobs zum Feuern bei Daemon-Start re-enqueuen (§5.2).

    Setzt sie auf ``pending`` + ``next_fire_at=now`` (frischer Zähler) — bei jedem
    Daemon-Start einmal. Gibt die Anzahl angestoßener Jobs zurück."""
    now = time.time() if now is None else now
    cur = conn.execute(
        "UPDATE jobs SET status='pending', next_fire_at=:now, attempt=0, reason=NULL, "
        "fire=fire+1, locked_at=NULL, started_at=NULL, finished_at=NULL, exit_code=NULL, "
        # `autostart` loest der Parser seit m.rau/bibi#50 zu `startup` auf. Hier
        # trotzdem beide: in der DB koennen Zeilen stehen, die vor dieser
        # Aenderung geschrieben wurden, und ein Job, der beim Daemon-Start nicht
        # feuert, meldet das von sich aus nicht -- genau der stille Fehler, um
        # den es in dem Issue geht.
        "output_ref=NULL, deferred_at=NULL, updated_at=:now "
        "WHERE schedule IN ('startup','autostart')",
        {"now": now},
    )
    return cur.rowcount


def wipe_job_data(job_id: str) -> None:
    """RESET-Cleanup für job-eigene, per ``bibi.job.data_dir()`` abgelegte
    Daten (External job data & secrets, ``vault/CONVENTIONS.md``) — Bibi4
    Batch 6, User-Entscheidung "RESET wischt, START bewahrt". Generisches
    Glob über ``~/.local/share/bibi/*/<job_id>/``, kein Wissen über den
    Job-Inhalt oder das jeweilige Subsystem nötig (dasselbe Prinzip wie der
    generische Container-Mount, ``DESIGN.md`` §7.4, nur fürs Aufräumen statt
    fürs Mounten). Nur aus ``job_reset()`` gerufen, NICHT aus ``start_now()``
    — ein Job, der nie unter der Konvention geschrieben hat, hat schlicht
    nichts zu löschen (best-effort, keine Fehler bei fehlendem Root)."""
    root = Path.home() / ".local" / "share" / "bibi"
    if not root.is_dir():
        return
    for d in root.glob(f"*/{job_id}"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def list_pinned(conn: sqlite3.Connection, host: str) -> list[dict]:
    """``bibi-ctrl run list`` (PLAN-32 Stufe 32.3, User-Fund): ``/run``-gepinnte
    Jobs sind der Scheduler-HTTP-API (``/-/job*``, Scheduler-Rolle-gated)
    unerreichbar — ein reiner Client-Knoten (z. B. dieser Mac, kein
    ``--scheduler``) hat also gar keinen API-Weg, sie zu sehen/verwalten.
    Direkter, rollen-unabhängiger DB-Zugriff auf genau diesen Host schließt
    die Lücke, ohne den Scheduler-HTTP-Pfad anzufassen."""
    rows = conn.execute(
        "SELECT id, slug, status, kind, payload FROM jobs "
        "WHERE pinned_host=? ORDER BY enqueued_at DESC", (host,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_pinned_job(conn: sqlite3.Connection, job_id: str) -> str:
    """Entfernt eine ``/run``-gepinnte Job-Zeile vollständig (§ oben) — anders
    als ``reset``/``start`` gibt es dafür keinen sinnvollen ``pending``-
    Zustand: eine gepinnte Zeile hat einen einmaligen, zufallssuffigierten
    Slug (``run_pinned()``s ``unique_slug``) und wird nie erneut disponiert.
    Nur für ``pinned_host IS NOT NULL``-Zeilen — eine vom Scheduler verwaltete
    Zeile hier zu löschen wäre ein Datenverlust, den nur der reguläre
    Reconcile-Pfad (inactive/rescan) verantworten darf. ``ok`` | ``not_found``
    | ``not_pinned``."""
    row = conn.execute("SELECT pinned_host FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return "not_found"
    if row["pinned_host"] is None:
        return "not_pinned"
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return "ok"


#: Terminalzustände, die ``start`` archiviert (= report_status→pending) statt
#: nur fällig zu machen (PLAN-14 Stufe 14.2). failed/deferred bewusst nicht
#: dabei — die bleiben in ihrem Status und werden nur sofort fällig gemacht
#: (kein Attempts-Reset, User-Entscheidung Job Lifecycle §START/failed).
_ARCHIVE_AND_START = (Status.ERROR, Status.INACTIVE, Status.ZOMBIE, Status.KILLED, Status.COMPLETE)


def start_now(conn: sqlite3.Connection, job_id: str, now: float | None = None) -> str:
    """User-Verb ``start`` (§5.6): einen ``pending``-, ``deferred``- oder
    ``failed``-Job **sofort** fällig machen (``next_fire_at=now``), ohne auf den
    Trigger/Backoff zu warten — ``failed`` bewusst ohne Attempts-Reset, nur der
    Timer wird übersprungen. Bei archivierbaren Terminalzuständen
    (``_ARCHIVE_AND_START``) archiviert es wie ``reset`` den alten Lauf, erzwingt
    aber zusätzlich die sofortige Fälligkeit (``next_fire_at=now``) — ``reset``
    allein respektiert den Trigger und lässt z. B. ``never``-Jobs bewusst
    unfällig (User-Feedback, PLAN-14 14.2). ``ok`` | ``invalid`` (running/awaiting)
    | ``not_found``."""
    now = time.time() if now is None else now
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return "not_found"
    status = Status(row["status"])
    if status in (Status.PENDING, Status.DEFERRED, Status.FAILED):
        # failed/deferred brauchen keine attempts-1-Logik — "sofortiger Start"
        # reicht, der Job ist schon dispatchbar sobald next_fire_at fällig ist.
        conn.execute("UPDATE jobs SET next_fire_at=?, updated_at=? WHERE id=?", (now, now, job_id))
        return "ok"
    if status in _ARCHIVE_AND_START:
        # anders als reset: START erzwingt sofortige Fälligkeit, unabhängig vom Trigger.
        return report_status(conn, job_id, status="pending", next_fire_at=now, now=now)
    return "invalid"


def proc_started_at(pid: int) -> str | None:
    """Prozess-Startzeit als opaker String für PID-Recycling-Erkennung.

    Linux: liest ``/proc/<pid>/stat`` (Feld 22, clock ticks seit Boot).
    macOS/Fallback: ``ps -o lstart=``. Gibt ``None`` zurück wenn der Prozess
    nicht existiert oder die Plattform den Wert nicht liefert."""
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            txt = proc_stat.read_text()
            rest = txt[txt.rfind(")") + 2:]
            fields = rest.split()
            return fields[19] if len(fields) > 19 else None
        except OSError:
            pass
    try:
        r = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        )
        s = r.stdout.strip()
        return s if s else None
    except (OSError, subprocess.SubprocessError):
        return None


def get_pid(conn: sqlite3.Connection, job_id: str) -> tuple[int, str | None] | None:
    """PID + Startzeit eines Jobs lesen (Gegenstück zu ``report_pid()``, §10.2).

    ``None`` bei unbekannter ID oder wenn kein PID hinterlegt ist (z. B. Job nie
    per ``report_pid()`` getrackt) — der Aufrufer entscheidet, ob das ein Fehler
    oder erwartet ist (``Worker.kill()``-Fallback nach einem Daemon-Neustart)."""
    row = conn.execute(
        "SELECT pid, pid_started_at FROM jobs WHERE id=?", (job_id,),
    ).fetchone()
    if row is None or row["pid"] is None:
        return None
    return row["pid"], row["pid_started_at"]


def report_pid(
    conn: sqlite3.Connection, job_id: str, pid: int, pid_started_at: str | None,
) -> bool:
    """PID + Startzeit nach dem Wrapper-Spawn schreiben und ``starting`` →
    ``running`` schalten (§10.2, m.rau/bibi#38). ``True``, wenn der Übergang
    stattfand.

    **Das ``AND status='starting'`` ist der Kern und kein Schmuck.** Diese
    Funktion läuft, nachdem ``_run_wrapper()`` zurückgekehrt ist — bei
    ``detach=True`` also unmittelbar nach dem ``Popen``, während der Wrapper
    schon arbeitet. Ein sehr kurzer Job kann in diesem Moment längst fertig sein
    und ``complete`` gemeldet haben. Ein unbedingtes ``SET status='running'``
    würde diesen Terminalzustand überschreiben: der Job fiele auf ``running``
    zurück, mit einer PID, deren Prozess nicht mehr existiert — und die
    periodische Waisen-Prüfung räumte ihn beim nächsten Durchlauf als
    ``killed/no_process`` ab. Ein erfolgreich beendeter Job landete als getötet,
    und je schneller er ist, desto wahrscheinlicher.

    Trifft das Update null Zeilen, hat der Wrapper das Rennen gewonnen; sein
    gemeldeter Zustand bleibt stehen und die PID interessiert niemanden mehr.
    """
    cur = conn.execute(
        "UPDATE jobs SET pid=:pid, pid_started_at=:ps, status='running' "
        "WHERE id=:id AND status='starting'",
        {"pid": pid, "ps": pid_started_at, "id": job_id},
    )
    return cur.rowcount > 0


def reconcile_no_process(
    conn: sqlite3.Connection, stale_workers: set[str], now: float | None = None
) -> int:
    """``running``-Jobs verwaister (heartbeat-veralteter) Worker → ``killed``
    (``no_process``), §5.5. ``stale_workers`` = bekannte, aber abgelaufene Worker
    (lokale Worker ohne Heartbeat sind NICHT dabei ⇒ keine Fehlalarme)."""
    now = time.time() if now is None else now
    n = 0
    for w in stale_workers:
        for r in conn.execute(
            "SELECT id FROM jobs WHERE status='running' AND worker=?", (w,)
        ).fetchall():
            report_status(conn, r["id"], status="killed", reason="no_process", now=now)
            n += 1
    return n


def reconcile_orphans(
    conn: sqlite3.Connection, worker_name: str, now: float | None = None,
    *, include_starting: bool = True,
) -> int:
    """RUNNING/AWAITING-Jobs dieses Workers per PID prüfen (§10.2, #38).

    Hieß bis 2026-07-30 ``reconcile_startup_orphans`` und lief nur beim
    Daemon-Start. Sie läuft jetzt zusätzlich periodisch (``Sweeper``), was erst
    durch ``starting`` gefahrlos wurde: **RUNNING ⇒ pid gesetzt**, die Prüfung
    ist also jederzeit eindeutig und braucht keine Karenzzeit, die einen
    Setup-Vorgang von einem toten Prozess unterscheiden müsste.

    ``include_starting`` trennt die beiden Aufrufer:

    - **Beim Start** (``True``): ein vorgefundener ``starting``-Job ist
      zweifelsfrei eine Waise. Sein Setup wurde vom Prozessende unterbrochen,
      ein Wrapper existiert nicht und wird nie einen melden.
    - **Periodisch** (``False``): ``starting`` heißt gerade *im Setup* —
      Worktree anlegen, Container aufräumen, Image bauen. Das darf Minuten
      dauern und ist genau der Fall, den ein laufender Sweep nicht anfassen
      darf.

    - ``pid`` + ``pid_started_at`` stimmen mit dem laufenden Prozess überein →
      Prozess lebt echt noch (z. B. ``start_new_session=True``-Wrapper, der
      einen Daemon-Neustart überlebt hat) → Zeile unangetastet lassen, kein
      SIGKILL. Der Wrapper supervised sich selbst und meldet seinen Abschluss
      eigenständig (lokale DB oder HTTP) — die nächste Reconciliation greift
      erst wieder, wenn er wirklich weg ist.
    - ``pid`` tot oder PID recycled (Startzeit weicht ab) → ``killed/no_process``.
    - ``pid`` nicht in DB (Altlast ohne Tracking) → ``killed/no_process`` wie bisher.
    - Recurring Cron-Jobs werden bei echtem ``killed`` sofort auf ``pending``
      zurückgesetzt — bei einem noch lebenden Prozess **nicht** (sonst würde
      der Scheduler denselben Job parallel ein zweites Mal dispatchen).
    """
    now = time.time() if now is None else now
    n = 0
    states = ["running", "awaiting"] + (["starting"] if include_starting else [])
    rows = conn.execute(
        "SELECT id, schedule, status, pid, pid_started_at FROM jobs "
        f"WHERE status IN ({','.join('?' * len(states))}) AND worker=?",
        (*states, worker_name),
    ).fetchall()
    for r in rows:
        pid = r["pid"]
        if r["status"] == "starting":
            # Kein Zweifelsfall: 'starting' bedeutet, dass der Wrapper noch nie
            # gespawnt wurde. Wird dieser Zustand beim Daemon-Start vorgefunden,
            # ist das Setup vom Prozessende unterbrochen worden — es gibt keinen
            # Prozess, den man prüfen könnte, und es wird nie einen geben.
            report_status(conn, r["id"], status="killed", reason="no_process", now=now)
            _rearm_if_recurring(conn, r, now)
            n += 1
            continue
        still_alive = pid is not None and proc_started_at(pid) == r["pid_started_at"]
        if still_alive:
            continue
        report_status(conn, r["id"], status="killed", reason="no_process", now=now)
        _rearm_if_recurring(conn, r, now)
        n += 1
    return n


def _rearm_if_recurring(conn: sqlite3.Connection, row, now: float) -> None:
    """Wiederkehrende Jobs nach echtem ``killed`` sofort auf ``pending``.

    Bei einem noch lebenden Prozess ausdrücklich **nicht** — der Scheduler würde
    denselben Job sonst parallel ein zweites Mal dispatchen. Herausgezogen, weil
    der ``starting``-Zweig (#38) dieselbe Behandlung braucht.
    """
    if not is_recurring(row["schedule"]):
        return
    conn.execute(
        "UPDATE jobs SET status='pending', next_fire_at=:now, attempt=0, "
        "reason=NULL, locked_at=NULL, started_at=NULL, finished_at=NULL, "
        "exit_code=NULL, output_ref=NULL, pid=NULL, pid_started_at=NULL, "
        "updated_at=:now WHERE id=:id",
        {"now": now, "id": row["id"]},
    )


# ── Dispatch-Zähler (PLAN-21 Befund 11 v2, User-Redesign 2026-07-08) ────────
#
# Die frühere transitions-Tabelle (Lifecycle-Zeitreihe, User-Feedback
# 2026-07-07) ist zurückgebaut — das neue Chart zählt nur noch Landungen in
# Terminal-Zuständen (aus journal, s. journal_landings()), das brauchte keine
# eigene Zwischenzustands-Historie mehr. Schema (Migration v14, Tabelle
# ``transitions``) bleibt unangetastet (kein Rückwärts-Migrationsaufwand für
# eine leere/ungenutzte Tabelle) — nur die Schreib-/Lese-Funktionen und ihre
# Aufrufer fallen weg. Einzig verbliebener Konsument war
# job_stats.running_since_uptime; der zieht jetzt aus diesem simplen
# In-Memory-Zähler statt aus einer Transitions-Abfrage — sogar präziser
# (keine 48h-Kappung mehr).

_dispatch_count = 0


def dispatch_count() -> int:
    """Anzahl erfolgreicher ``reserve_next()``-Dispatches seit Prozessstart
    (In-Memory, kein DB-State) — Basis für ``job_stats.running_since_uptime``.

    ``reserve_next()`` läuft immer im Scheduler-Prozess selbst (der die Queue
    besitzt) — ein In-Memory-Zähler ist hier sicher, anders als bei
    ``count_completed_since()`` unten (die Completion-Meldung läuft meist in
    einem *anderen* Prozess, s. dortiger Docstring)."""
    return _dispatch_count


def count_completed_since(conn: sqlite3.Connection, since: float) -> int:
    """Anzahl Scheduler-Jobs, die seit ``since`` (Prozessstart) mit
    ``status='complete'`` im Journal gelandet sind (PLAN-26 Befund 3,
    Basis für ``job_stats.complete_since_uptime``).

    DB-Query statt In-Memory-Zähler (User-Fund: "warum zählt COMPLETE nach
    einem erfolgreichen Lauf nicht +1?") — Root Cause: ``report_status()``
    für Scheduler-Jobs wird meist aus dem **detachten Wrapper-Subprozess**
    aufgerufen (SQLite-Direct-Pfad, ``wrapper/__init__.py::_report_terminal``),
    nicht aus dem Daemon-Hauptprozess. Ein In-Memory-Zähler dort sähe diese
    Inkremente nie — der Wrapper-Prozess erhöht nur seine eigene, mit ihm
    endende Kopie. Die ``journal``-Tabelle ist dagegen echter, prozess-
    übergreifender State (dieselbe SQLite-Datei).

    ``domain='scheduled'`` schließt lokale ``/-/run``-Läufe aus (User-
    Entscheidung: konsistent zu den anderen 9 Status, die ebenfalls nur die
    ``jobs``-Tabelle/Scheduler-Domäne sehen)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE domain='scheduled' "
        "AND status='complete' AND finished_at >= ?", (since,)
    ).fetchone()
    return row["n"] if row else 0


# ── Journal (disponierte Domäne, §1.4) ───────────────────────────────────────


def run_id_for(slug: str, job_id: str, fire: int) -> str:
    """Kanonische Lauf-ID: ``slug:fire`` (lesbar, wie bisher) + Job-ID-Suffix.

    ``fire`` allein ist NICHT über die gesamte Slug-Historie eindeutig — er
    startet bei jeder Neuanlage einer Job-Zeile (neue ``job_id``, z. B. nach
    einem DB-Reset/Maschinenwechsel) wieder bei 0. Ein heutiger Lauf konnte so
    denselben ``run_id`` wie ein Jahre alter Lauf einer früheren Job-
    Inkarnation treffen (User-Feedback 2026-07-01) — mit Folgen sowohl für den
    Journal-Dedup (``_write_journal``) als auch für den Output-Pfad auf Platte
    (``worker.py:_output_path`` ist ``data/job/<run_id>/output.jsonl``, eine
    Kollision hätte dort Läufe unterschiedlicher Job-Inkarnationen vermischt).
    Der Job-ID-Suffix macht ihn durch Konstruktion eindeutig. **Muss** überall
    identisch verwendet werden, wo ``run_id`` vor UND nach dem Report gebildet
    wird (``worker.py:execute_reservation``/``Worker.output_path`` vs. hier),
    sonst laufen Output-Pfad und Journal-Metadaten auseinander."""
    return f"{slug}:{fire}:{job_id}"


def _write_journal(
    conn: sqlite3.Connection, job_id: str, archived_at: float,
    *, commit_sha: str | None = None, branch: str | None = None,
) -> None:
    """Eine append-only Journal-Zeile aus dem aktuellen Job-Zustand schreiben.

    Watermark-Dedup: pro (run_id, status, started_at) genau eine Zeile — ein
    erneuter Terminal-Report (z. B. idempotenter Retry) dupliziert nicht.
    ``started_at`` ist Teil des Schlüssels (User-Feedback 2026-07-01): ``fire``
    startet bei jedem neu angelegten Job-Datensatz wieder bei 0 (z. B. nach
    Neuanlage/Migration) — ein heutiger Lauf kann so zufällig denselben
    ``run_id`` wie ein Jahre alter, abgeschlossener Lauf treffen. Ohne
    ``started_at`` im Schlüssel hielt die Dedup-Prüfung das für "schon
    geloggt" und verwarf den echten neuen Eintrag still, sodass die Liste auf
    dem uralten (Fehl-)Status hängen blieb. Ein *echter* Wiederholungs-Report
    derselben Ausführung hat dagegen dasselbe ``started_at`` — bleibt also
    weiter dedupliziert. ``commit_sha``/``branch`` kommen vom Worker-Report
    (v6, §2.3); Sweeper-/Reconcile-Terminals ohne Worktree-Commit lassen sie
    ``NULL``."""
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return
    # run_id (inkl. job_id-Suffix, s. run_id_for()) ist über die gesamte
    # Slug-Historie eindeutig ⇒ Watermark-Dedup kollidiert nicht über
    # wiederkehrende Läufe *und* nicht über Job-Neuanlagen hinweg.
    run_id = run_id_for(row["slug"], row["id"], row["fire"])
    dup = conn.execute(
        "SELECT 1 FROM journal WHERE run_id=? AND status=? AND started_at IS ?",
        (run_id, row["status"], row["started_at"]),
    ).fetchone()
    if dup:
        return
    exec_runtime = None
    if row["started_at"] is not None and row["finished_at"] is not None:
        exec_runtime = row["finished_at"] - row["started_at"]
    cur = conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, reason, started_at, "
        "finished_at, exit_code, exec_runtime, host, worker, output_ref, commit_sha, "
        "branch, payload, pinned_host, snapshot, archived_at) VALUES (:run_id,:slug,:kind,"
        ":status,:reason,:started_at,:finished_at,:exit_code,:exec_runtime,:host,:worker,"
        ":output_ref,:commit_sha,:branch,:payload,:pinned_host,:snapshot,:archived_at)",
        {
            "run_id": run_id, "slug": row["slug"], "kind": row["kind"],
            "status": row["status"], "reason": row["reason"],
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            "exit_code": row["exit_code"], "exec_runtime": exec_runtime,
            "host": row["host"], "worker": row["worker"], "output_ref": row["output_ref"],
            "commit_sha": commit_sha, "branch": branch, "payload": row["payload"],
            "pinned_host": row["pinned_host"],
            # job_full_view() statt job_view() (User-Feedback 2026-07-03: "ein
            # Schedule oder Attempts kann sich ändern" — der Snapshot muss ALLE
            # Konfig-Felder einfrieren, nicht nur die kleine Live-Sicht, sonst
            # verliert man z. B. attempts/backoff/model rückwirkend).
            "snapshot": json.dumps(job_full_view(row), ensure_ascii=False),
            "archived_at": archived_at,
        },
    )


def journal_view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "run_id": row["run_id"], "slug": row["slug"],
        "kind": row["kind"], "status": row["status"], "reason": row["reason"],
        "started_at": row["started_at"], "finished_at": row["finished_at"],
        "exit_code": row["exit_code"], "exec_runtime": row["exec_runtime"],
        "host": row["host"], "worker": row["worker"], "output_ref": row["output_ref"],
        "commit_sha": row["commit_sha"], "branch": row["branch"],
        "domain": row["domain"],
        # nur intern genutzt (Ausgabefilter, PLAN-12 Stufe 12.4/12.5;
        # pinned_host seit PLAN-28 für die /-/run/journal*-Routen, die
        # "meine eigene /run-Historie" unabhängig von domain erkennen müssen —
        # gepinnte Läufe haben domain='scheduled', aber pinned_host gesetzt).
        "payload": row["payload"], "pinned_host": row["pinned_host"],
    }


def get_journal(conn: sqlite3.Connection, journal_id: int) -> dict | None:
    """Eine Journal-Zeile per ID (für Output-Replay & Detail-Sicht, §4.2). Anders
    als ``journal_view()`` (Listenansicht, bewusst schlank — 50 Zeilen pro Scroll-
    Batch sollen keinen Snapshot-JSON-Ballast tragen) liefert diese Einzelabfrage
    zusätzlich ``snapshot``/``archived_at`` — die Lauf-Detail-Seite zeigt daraus
    die zum Laufzeitpunkt eingefrorene Konfiguration (User-Feedback 2026-07-03)."""
    row = conn.execute("SELECT * FROM journal WHERE id=?", (journal_id,)).fetchone()
    if row is None:
        return None
    return {**journal_view(row), "snapshot": row["snapshot"], "archived_at": row["archived_at"]}


def delete_journal(conn: sqlite3.Connection, journal_id: int) -> bool:
    """Einen Lauf-Record löschen (PLAN-4 §4.0 / A15: **nur** DB-Records, kein
    MD-CRUD). Rückgabe: True, wenn eine Zeile entfernt wurde."""
    cur = conn.execute("DELETE FROM journal WHERE id=?", (journal_id,))
    return cur.rowcount > 0


def list_journal(
    conn: sqlite3.Connection, slug: str | None = None,
    host: str | None = None, domain: str | None = None,
    limit: int | None = None, offset: int | None = None, mine_only: bool = False,
) -> list[dict]:
    """``mine_only`` (PLAN-28): "meine eigene /run-Historie" unabhängig von
    ``domain`` — deckt sowohl historische ``domain='local'``-Zeilen ab (vor
    PLAN-28 Refactor D geschrieben, damals von der inzwischen entfernten
    ``write_local_journal()``; auf Bestandsknoten können solche Zeilen noch
    existieren) als auch gepinnte HTTP-``/run``-Läufe (``domain='scheduled'``
    — echte ``jobs``-Zeile, volle Lifecycle — aber ``pinned_host`` gesetzt).
    Echte Team-Queue-Läufe (``pinned_host IS NULL``) bleiben ausgeschlossen,
    auch wenn sie zufällig auf demselben Host liefen.

    ``slug`` (User-Fund 2026-07-13): ``run_pinned()`` vergibt pro Aufruf einen
    eindeutigen ``jobs.slug`` (``f"{bucket_slug}-{token}"``, ``token`` immer
    ``secrets.token_hex(4)`` = 8 Hex-Zeichen) — ``_write_journal()`` übernimmt
    den unverändert nach ``journal.slug``. Eine reine Exact-Match-Suche nach
    dem stabilen Bucket-Slug fand solche Zeilen deshalb nie. Fix: zusätzlich
    zum Exact-Match ein ``LIKE``-Präfix, aber **nur** für Zeilen mit
    ``pinned_host`` gesetzt — das verhindert Fehltreffer wie ``"job"`` vs.
    einem echten, andersartigen Schedule-Slug ``"job-runner"`` (dessen
    ``pinned_host`` immer ``NULL`` ist, s. ``worker.py``s ``_pinned_live_row()``
    für dieselbe Konvention)."""
    sql = "SELECT * FROM journal"
    clauses, params = [], []
    if slug:
        clauses.append("(slug=? OR (pinned_host IS NOT NULL AND slug LIKE ?))")
        params.extend([slug, f"{slug}-________"])
    if host:
        clauses.append("host=?"); params.append(host)
    if domain:
        clauses.append("domain=?"); params.append(domain)
    if mine_only:
        clauses.append("(domain='local' OR pinned_host IS NOT NULL)")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY finished_at DESC"  # PLAN-14 Stufe 14.3 (war archived_at)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset or 0]
    return [journal_view(r) for r in conn.execute(sql, params).fetchall()]


def journal_landings(conn: sqlite3.Connection, *, since: float | None = None) -> list[dict]:
    """Dünn projizierte Landungen (``status``+``finished_at``) für das
    Lauf-Historie-Chart (PLAN-21 Befund 11 v2) — ``journal`` ist per
    Konstruktion schon terminal-only (``_write_journal`` feuert nur bei
    ``target in lifecycle.TERMINAL``), darum reicht ein simpler Zeitfilter
    ohne die volle ``journal_view()``-Projektion (Snapshot/Payload etc.)."""
    q = "SELECT status, finished_at FROM journal WHERE finished_at IS NOT NULL"
    params: dict = {}
    if since is not None:
        q += " AND finished_at >= :since"
        params["since"] = since
    q += " ORDER BY finished_at ASC"
    return [dict(r) for r in conn.execute(q, params).fetchall()]


#: Live-Zustände, die als **Abweichung** zählen (PLAN-4 §3/§4.1): „lief nicht".
#: ``failed`` (Retry-wartend), ``error`` (aufgegeben), ``zombie``/``killed``.
PROBLEM_STATES = ("failed", "error", "zombie", "killed")


def verdict(conn: sqlite3.Connection, now: float | None = None) -> dict:
    """Server-seitiges „läuft alles?" (PLAN-4 §2.3): Abweichungen (aktueller
    Job-Zustand in :data:`PROBLEM_STATES`) + **überfällige** Jobs (``pending``,
    deren ``next_fire_at`` in der Vergangenheit liegt — Trigger verpasst). DB-nah
    und wiederverwendbar (Controller, ``bibi-ctrl status``, Föderation)."""
    now = time.time() if now is None else now
    placeholders = ",".join("?" * len(PROBLEM_STATES))
    deviations = [
        job_view(r) for r in conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
            PROBLEM_STATES,
        ).fetchall()
    ]
    # Lauf-Historie einbeziehen: ein wiederkehrender Job re-armt nach Fehlschlag zu
    # `pending` — der Zeilen-Status ist dann harmlos, aber der LETZTE LAUF im Journal
    # ist gescheitert. Sonst meldete das Verdikt fälschlich „alles lief". Dedup gegen
    # die Zeilen-Abweichungen; laufende/entfernte Schedules ausgenommen.
    current_slugs = {d["slug"] for d in deviations}
    for r in conn.execute(
        "SELECT j.* FROM journal j JOIN ("
        "  SELECT slug, MAX(id) AS mx FROM journal WHERE domain='scheduled' GROUP BY slug"
        ") m ON j.id = m.mx "
        f"WHERE j.status IN ({placeholders}) ORDER BY j.finished_at DESC",
        PROBLEM_STATES,
    ).fetchall():
        if r["slug"] in current_slugs:
            continue
        jr = conn.execute("SELECT status FROM jobs WHERE slug=?", (r["slug"],)).fetchone()
        if jr is None or jr["status"] in ("starting", "running"):
            # Schedule entfernt oder gerade aktiv → kein „letzter-Lauf"-Problem.
            # 'starting' gehört dazu (#38): ein Job im Setup läuft bereits, sein
            # Ausgang steht noch aus. Ihn wegen eines alten Fehllaufs zu melden
            # wäre dieselbe Fehlmeldung, die dieser Zweig für 'running' verhindert.
            continue
        d = journal_view(r)
        d["last_run"] = True  # Zeile re-armt, aber letzter Lauf gescheitert
        deviations.append(d)
    overdue = [
        job_view(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE status='pending' AND next_fire_at IS NOT NULL "
            "AND next_fire_at < ? ORDER BY next_fire_at ASC", (now,),
        ).fetchall()
    ]
    return {
        "ok": not deviations and not overdue,
        "problems": len(deviations),
        "overdue": len(overdue),
        "deviations": deviations,
        "overdue_jobs": overdue,
    }


# ── PLAN-11.2: Ping + Demand ──────────────────────────────────────────────────


def touch_ping(conn: sqlite3.Connection, job_id: str) -> bool:
    """Setzt last_ping_at = now. Gibt False zurück wenn Job nicht existiert."""
    cur = conn.execute(
        "UPDATE jobs SET last_ping_at=? WHERE id=?", (time.time(), job_id)
    )
    return cur.rowcount > 0


def set_demand(conn: sqlite3.Connection, job_id: str, demand: dict) -> None:
    """Schreibt aktuellen HITL-Demand (überschreibt)."""
    conn.execute(
        "UPDATE jobs SET demand=? WHERE id=?", (json.dumps(demand, ensure_ascii=False), job_id)
    )


def get_demand(conn: sqlite3.Connection, job_id: str) -> dict | None:
    """Liest aktuellen HITL-Demand; None wenn nicht gesetzt."""
    row = conn.execute("SELECT demand FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row["demand"] is None:
        return None
    return json.loads(row["demand"])


def set_app_port(conn: sqlite3.Connection, job_id: str, port: int) -> None:
    """Setzt app_port (PLAN-11.3: app_register-Signal vom Job)."""
    conn.execute("UPDATE jobs SET app_port=? WHERE id=?", (port, job_id))


# ── Open-Trust-Connect-Gate (PLAN-32 Stufe 32.1) ─────────────────────────────

_KNOWN_NODE_STATUSES = ("pending", "approved", "blocked")


def node_approval_status(conn: sqlite3.Connection, node_id: str, *,
                         now: float | None = None) -> str:
    """Liest den Freischalt-Status eines Knotens; legt ihn bei erstem Aufruf
    (unbekannter ``node_id``) mit Status ``"pending"`` an — genau EIN
    atomarer Read-or-Create, kein separates "existiert schon?"-Vorab-SELECT.
    Bewusst in `job_db` statt im In-Memory-`WorkerRegistry` (dortiger
    Docstring, PLAN-32): eine Freischaltung ist eine Host-Entscheidung, kein
    Client-Selbstbericht, muss also einen Host-Neustart überleben."""
    row = conn.execute(
        "SELECT status FROM approved_nodes WHERE node_id=?", (node_id,)
    ).fetchone()
    if row is not None:
        return row["status"]
    now = time.time() if now is None else now
    conn.execute(
        "INSERT OR IGNORE INTO approved_nodes (node_id, status, updated_at) "
        "VALUES (?, 'pending', ?)", (node_id, now)
    )
    return "pending"


def set_node_approval(conn: sqlite3.Connection, node_id: str, status: str, *,
                      now: float | None = None) -> None:
    """Setzt den Freischalt-Status explizit (Host-Operator-Aktion über den
    Nodes-Screen: Freischalten/Blockieren). ``status`` eines von
    ``_KNOWN_NODE_STATUSES`` — kein Enum, um dieselbe leichte Validierung wie
    an anderen Stellen dieser Datei (z. B. ``report_status()``) zu spiegeln."""
    if status not in _KNOWN_NODE_STATUSES:
        raise ValueError(f"unbekannter Node-Status: {status!r}")
    now = time.time() if now is None else now
    conn.execute(
        "INSERT INTO approved_nodes (node_id, status, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(node_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
        (node_id, status, now)
    )


def reconcile_blocked_nodes(
    conn: sqlite3.Connection, workers: list[dict], now: float | None = None,
) -> int:
    """Laufende Jobs **gebannter** Knoten beenden (m.rau/bibi#23).

    Die Ban-Semantik war bisher nur halb gebaut: ein ``blocked`` gemeldeter
    Knoten wird beim Heartbeat mit 401 abgewiesen und bekommt kein
    Config-Bundle — seine bereits laufenden Jobs blieben aber unangetastet in
    der DB stehen und zählten weiter als aktiv. Ein Bann, der laufende Arbeit
    weiterlaufen lässt, ist keiner.

    ``workers`` ist die Registry-Sicht (``worker`` + ``node_id`` je Zeile); die
    Zuordnung Bann → Job läuft über den Worker-Namen, weil die jobs-Tabelle
    keine ``node_id`` führt.

    Landung ist ``killed``/``no_process``, wie bei jeder anderen Waise: der
    Prozess ist für diesen Scheduler nicht mehr erreichbar, ob er auf dem
    fremden Knoten noch atmet, kann er weder wissen noch beeinflussen. Der
    lokale Worker ist nie betroffen — er heartbeatet sich nicht selbst und
    steht folglich nie in dieser Liste.
    """
    now = time.time() if now is None else now
    blocked = {r["node_id"]: r["status"] for r in
               conn.execute("SELECT node_id, status FROM approved_nodes "
                            "WHERE status='blocked'")}
    if not blocked:
        return 0
    names = {w.get("worker") for w in workers
             if w.get("node_id") in blocked and w.get("worker")}
    n = 0
    for name in names:
        for r in conn.execute(
            "SELECT id FROM jobs WHERE status IN ('starting','running','awaiting') "
            "AND worker=?", (name,),
        ).fetchall():
            report_status(conn, r["id"], status="killed", reason="no_process", now=now)
            n += 1
    return n


def list_node_approvals(conn: sqlite3.Connection) -> dict[str, str]:
    """Alle bekannten Freischalt-Status, für den Nodes-Screen (eine Abfrage
    statt einer je Zeile)."""
    return {r["node_id"]: r["status"]
            for r in conn.execute("SELECT node_id, status FROM approved_nodes")}
