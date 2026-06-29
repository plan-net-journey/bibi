"""Datenmodell der Scheduling-Domäne (DESIGN §5.2–§5.5, §7.5; PLAN-3 §3.0).

Reine Typen — keine DB, kein HTTP. Die Enums sind ``StrEnum``, damit ihre Werte
ohne Konvertierung in JSON/OpenAPI und SQLite landen (``Status.PENDING ==
"pending"``).

Unified Job Model (PLAN-10 §3 Stufe 10.0): ein einziger Typ ``JOB``. Frühere
``CLAUDE``- und ``APP``-Typen wurden aufgelöst — ``claude:``-Prefix-Expansion
passiert beim Spawn im Worker, App-Verhalten ist reine Laufzeit-Konvention.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    """Job-Zustände (DESIGN §5.4/§5.5). Werte = DB-/JSON-Repräsentation."""

    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    ERROR = "error"
    DEFERRED = "deferred"
    INACTIVE = "inactive"
    AWAITING = "awaiting"
    COMPLETE = "complete"
    ZOMBIE = "zombie"
    KILLED = "killed"


class Kind(StrEnum):
    """Ausführungstypen. Nur noch ``JOB`` (PLAN-10 Stufe 10.0)."""

    JOB = "job"


class Reason(StrEnum):
    """Root Causes für Terminal-/Sonderzustände (DESIGN §5.5)."""

    SILENCE = "silence"                    # zombie: kein stdout/stderr
    ACTIVITY_TIMEOUT = "activity_timeout"  # zombie: job in awaiting, keine Activity
    DEFERRED_EXPIRED = "deferred_expired"  # inactive: Deferred-Periode abgelaufen
    NO_PROCESS = "no_process"              # killed: Prozess weg ohne Exit-Code
    BY_USER = "by_user"                    # killed: manuelles kill
    BY_WALL_TIME = "by_wall_time"          # killed: Laufzeit-Limit überschritten


class Owner(StrEnum):
    """Wer einen Zustand „besitzt" (DESIGN §5.4): er führt den Übergang heraus."""

    SCHEDULER = "scheduler"
    WORKER = "worker"


# Default-Modell für claude:-Prefix-Jobs (überschreibbar via `model:`).
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"

# Default-Silence-Timeout in Sekunden (DESIGN §5.5: 1 h).
DEFAULT_SILENCE_TIMEOUT = 3600
# Default-HITL-Activity-Timeout in Sekunden (DESIGN §5.5: 48 h).
DEFAULT_HITL_TIMEOUT = 48 * 3600


@dataclass(frozen=True)
class ScheduleSpec:
    """Eine aus Frontmatter geparste Schedule-Definition (Ziel-Struktur des Parsers,
    der erst in Stufe 3.1 gebaut wird — hier steht nur das Modell).

    Genau ein Trigger (``schedule`` *oder* ``at``, DESIGN §5.2) und genau ein
    Typ-Payload (passend zu ``kind``) sind gesetzt.
    """

    slug: str
    kind: Kind
    payload: str  # Shell-Cmd; mit `claude: <prompt>` Prefix → claude-Expansion beim Spawn

    # Trigger (§5.2) — croniter-Ausdruck | now | startup | never  bzw. ISO-8601.
    schedule: str | None = None
    at: str | None = None

    # Scheduler-Auswahl (§4.4).
    priority: int = 0

    # claude:-Prefix-Felder (nur bei claude:-Payload ausgewertet).
    model: str = DEFAULT_CLAUDE_MODEL
    soul: str | None = None
    session: str | None = None

    # Lifecycle-Stellschrauben (§5.5) — vom Worker ausgewertet (Stufe 3.5).
    attempts: int = 1
    backoff: str = "fixed"  # fixed | linear | exponential
    silence_timeout: int = DEFAULT_SILENCE_TIMEOUT
    wall_time: int | None = None
    defer_time: int | None = None
    defer_max: int | None = None
    hitl_timeout: int = DEFAULT_HITL_TIMEOUT

    app_port: int | None = None
    app_prefix: str | None = None
    exec_mode: str | None = None  # "host"|"container" — überschreibt Knoten-Config

    # Optionales Override-Image (§7.6).
    image: str | None = None


@dataclass(frozen=True)
class JobRow:
    """Eine Job-Instanz in der Scheduler-DB (DESIGN §5.4/§5.5, §4.4).

    Die Hash-``id`` vergibt der Scheduler (§4.4); ab ``pending`` steuert der Worker
    den Lifecycle. ``output_ref`` zeigt auf die ``output.jsonl`` beim Worker (§1.4) —
    der Scheduler ist output-frei (§4.4), trägt nie den Output selbst.
    """

    id: str
    slug: str
    kind: Kind
    status: Status
    reason: Reason | None = None
    priority: int = 0

    enqueued_at: float | None = None
    locked_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None

    attempt: int = 0
    host: str | None = None
    worker: str | None = None
    output_ref: str | None = None


@dataclass(frozen=True)
class JournalEntry:
    """Eine Zeile im domänengetrennten Journal (DESIGN §1.4, PLAN-3 §3.1).

    ``run_id`` (``slug:trial``) bleibt über Retries konstant; ``host``/``worker``
    sind first-class (föderierte A13-Sicht). ``output_ref`` referenziert die
    ``output.jsonl`` statt sie zu enthalten (anders als bibi3' ``stdout_blob``).
    """

    run_id: str
    slug: str
    kind: Kind
    status: Status
    reason: Reason | None = None
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    exec_runtime: float | None = None
    host: str | None = None
    worker: str | None = None
    output_ref: str | None = None
