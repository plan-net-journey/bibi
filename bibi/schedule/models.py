"""Datenmodell der Scheduling-Domäne (DESIGN §5.2–§5.5, §7.5; PLAN-3 §3.0).

Reine Typen — keine DB, kein HTTP. Die Enums sind ``StrEnum``, damit ihre Werte
ohne Konvertierung in JSON/OpenAPI und SQLite landen (``Status.PENDING ==
"pending"``).

Unified Job Model (PLAN-10 §3 Stufe 10.0): ein einziger Typ ``JOB``. Frühere
``CLAUDE``- und ``APP``-Typen wurden aufgelöst — ``claude:``-Prefix-Expansion
passiert beim Spawn im Worker, App-Verhalten ist reine Laufzeit-Konvention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    """Job-Zustände (DESIGN §5.4/§5.5). Werte = DB-/JSON-Repräsentation."""

    PENDING = "pending"
    # STARTING (m.rau/bibi#38, Vorschlag von m.rau 2026-07-29): reserviert und
    # im Setup — Worktree anlegen, Container aufräumen, ggf. Image bauen —,
    # aber der Wrapper-Prozess ist noch nicht bekannt. Erst ``report_pid()``
    # schaltet auf RUNNING weiter. Damit gilt die Invariante
    # **RUNNING ⇒ pid gesetzt**, und genau die macht die PID-basierte
    # Waisen-Erkennung ohne Zeitheuristik korrekt: ein RUNNING-Job hat immer
    # einen Prozess zum Prüfen, ein STARTING-Job hat per Konstruktion keinen,
    # und ein beim Daemon-Start vorgefundener STARTING-Job ist eindeutig eine
    # Waise — sein Setup wurde unterbrochen, ein Prozess existiert nicht.
    STARTING = "starting"
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


# claude:-Prefix-Erkennung für Payload-Expansion (PLAN-10 Stufe 10.0; PLAN-12
# Stufe 12.0: konsolidiert aus worker.py::_CLAUDE_RE + render.py::_effective_sched_type).
CLAUDE_PAYLOAD_RE = re.compile(r"^\s*claude\s*:\s*(.+)", re.DOTALL)


def is_claude_payload(payload: str | None) -> bool:
    return bool(payload and CLAUDE_PAYLOAD_RE.match(payload.strip()))


def effective_kind(payload: str | None) -> str:
    """Anzeige-/Dispatch-Typ: ``claude:``-Prefix ⇒ ``"claude"``; sonst ``"job"``
    (PLAN-25 Befund 7 — ``app_port`` beeinflusst die Anzeige nicht mehr, Jobs
    mit Port+Prefix erscheinen wie jeder andere Job). Einzige Quelle für alle
    Aufrufer — DB-``kind`` ist seit PLAN-10 immer ``"job"``."""
    if is_claude_payload(payload):
        return "claude"
    return "job"


def display_kind(payload: str | None, app_port: int | None) -> str:
    """Wie ``effective_kind()``, zusätzlich ``"app"`` für Jobs mit ``app_port``
    (Bibi4-Iteration, User-Fund: "Apps enden nicht" — fachlich eigene Kategorie,
    trotz PLAN-25 Befund 7. Bewusst eine *separate* Funktion statt ``effective_kind()``
    selbst zu ändern: dessen Aufrufer außerhalb der Anzeige — Silence-Timeout-Wahl,
    Schedules-Übersicht/-Filter — sollen ``app_port`` weiterhin ignorieren). Einzige
    Quelle für alle **Anzeige**-Stellen, die job/claude/app unterscheiden: Type-Spalte,
    Job-Status-Card, Archive-Tabelle — sonst driften die Zählweisen wieder auseinander."""
    if app_port:
        return "app"
    return effective_kind(payload)


class Reason(StrEnum):
    """Root Causes für Terminal-/Sonderzustände (DESIGN §5.5)."""

    SILENCE = "silence"                    # zombie: keine Aktivität (Output/Signal)
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

# Default-Silence-Timeout in Sekunden — kind-abhängig (User-Feedback 2026-07-04:
# silence_timeout/hitl_timeout zusammengelegt, "Silence bei Jobs = Aktivität bei
# Apps"). claude:-Payloads (Batch, kein HITL) bekommen den kurzen Default (1h);
# echte Apps (`app_port`/`app_prefix` gesetzt, long-lived, HITL-fähig über
# run_app) den langen (48h) — ein Mensch darf so lange auf eine Antwort warten,
# ohne dass silence_timeout vorzeitig zuschlägt. Einfache Jobs ohne App-Marker
# (PLAN-31 Befund 4, 2026-07-17: `DEFAULT_SILENCE_TIMEOUT_APP` galt vorher
# fälschlich für JEDEN Nicht-claude:-Job, nicht nur für Apps — ein hängender
# einfacher Job blieb dadurch bis zu 48h unbemerkt, statt zeitnah als Zombie
# aufzufallen) bekommen einen deutlich kürzeren Default (2h).
DEFAULT_SILENCE_TIMEOUT = 3600
DEFAULT_SILENCE_TIMEOUT_APP = 48 * 3600
DEFAULT_SILENCE_TIMEOUT_JOB = 2 * 3600

#: Wall-Time (§5.5) — None-Sentinel wie defer_time/error_time (Bibi4-Iteration,
#: User-Fund: "wall_time Default muss doch None sein ... sonst laufen Apps in
#: die Default Wall Time und wir wollen bei Apps den Zombie Status verwenden").
#: Kein globaler DEFAULT_WALL_TIME mehr — Wall Time ist reines Opt-in fürs
#: einzelne Schedule, nie ein ungewollter Default für JOB/CLAUDE/APP gleichermaßen.

#: Defer-Time-Default (§5.5) — nur der letzte Fallback in _finish(), wenn
#: weder ein expliziter bibi.job.Deferred(seconds=N) noch Schedule-
#: Frontmatter (`defer_time:`) etwas anderes vorgeben.
DEFAULT_DEFER_TIME = 360

#: defer_max-Default (§5.5) — ein Job, der 20 Minuten am Stück (seit dem
#: ERSTEN Defer, `deferred_at`) nur deferred statt fertig zu werden, gilt als
#: zu unzuverlässig und wird vom Sweep auf `inactive` gesetzt (User-Fund: "er
#: ist zu faul, deferred ständig"). Vorher: kein Default, ein Job ohne
#: explizites `defer_max:` verfiel nie automatisch.
DEFAULT_DEFER_MAX = 20 * 60


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
    # attempts = Retries ZUSÄTZLICH zum ersten Lauf, nicht Gesamtversuche
    # (Wrapper: "attempt_cur < attempts_max" in _finish()) — Default 0 heißt
    # ein Versuch, kein automatischer Retry (User-Fund 2026-07-21, Bibi4
    # Batch 6: attempts=1 lief tatsächlich zweimal vor "error").
    attempts: int = 0
    backoff: str = "fixed"  # fixed | linear | exponential
    silence_timeout: int = DEFAULT_SILENCE_TIMEOUT
    wall_time: int | None = None
    defer_time: int | None = None
    defer_max: int = DEFAULT_DEFER_MAX
    error_time: int | None = None

    app_port: int | None = None
    app_prefix: str | None = None
    exec_mode: str | None = None  # "host"|"container" — überschreibt Knoten-Config

    # Optionales Override-Image (§7.6).
    image: str | None = None

    # Generischer, UNVALIDIERTER Escape-Hatch für zusätzliche `docker run`-
    # Argumente (§7.6a) — nur in exec_mode: container relevant, sonst No-op.
    # Rohe Strings, ungeprüft an die Docker-CLI durchgereicht: kann bestehende
    # Sicherheits-Annahmen (arbitrary-UID, feste Mounts) unterlaufen, z. B.
    # `--privileged` oder `-v /:/host`. Siehe CONVENTIONS.md-Warnung.
    docker_args: list[str] | None = None


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
