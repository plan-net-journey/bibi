"""Reine Zustandsmaschine des Job-Lifecycles (DESIGN §5.4/§5.5; PLAN-3 §3.0).

Kein DB-, kein HTTP-, kein Zeit-Zustand — nur ``(status, event) → status'``. Das
Herzstück wird isoliert bewiesen, bevor es an DB/Prozesse gekoppelt wird (wie der
``PushDebouncer`` der Phase 2). Die Übergänge und Owner-Regeln stammen 1:1 aus der
§5.4-Tabelle, die Root Causes aus §5.5.

Die Maschine ist **typ-agnostisch im Graphen**, kennt aber die typ-gebundenen
Kanten (DESIGN §5.4): ``awaiting`` und seine Kanten gelten nur für ``app``, der
Silence-``zombie`` nur für ``job``/``claude``. ``apply(..., kind=...)`` setzt das
durch; ohne ``kind`` bleibt die Prüfung aus (reine Graph-Sicht).
"""

from __future__ import annotations

from enum import StrEnum

from bibi.schedule.models import Kind, Owner, Reason, Status


class Event(StrEnum):
    """Lifecycle-Auslöser (die Kanten des §5.5-Graphen)."""

    DISPATCH = "dispatch"        # pending → running   (auch das User-Verb `start`)
    COMPLETE = "complete"        # running → complete
    FAIL = "fail"                # running → failed
    DEFER = "defer"              # running → deferred
    AWAIT_INPUT = "await_input"  # running → awaiting   (nur app)
    KILL = "kill"                # running/pending → killed
    SILENCE = "silence"          # running → zombie     (nur job/claude)
    RETRY = "retry"              # failed  → running
    EXHAUST = "exhaust"          # failed  → error
    RESUME = "resume"            # deferred → running
    EXPIRE = "expire"            # deferred → inactive
    INPUT = "input"              # awaiting → running   (nur app)
    TIMEOUT = "timeout"          # awaiting → zombie    (nur app)
    RESET = "reset"              # {terminal} → pending (das User-Verb `reset`)


class IllegalTransition(Exception):
    """Ein nicht erlaubter ``(status, event)``-Übergang (ggf. typ-bedingt)."""


# ── Zustands-Klassen ────────────────────────────────────────────────────────

#: Terminalzustände — ihr einziger Ausgang ist ``RESET`` zurück nach ``pending``.
TERMINAL: frozenset[Status] = frozenset(
    {Status.COMPLETE, Status.ERROR, Status.INACTIVE, Status.ZOMBIE, Status.KILLED}
)

#: Wer den Zustand besitzt = ist für den Übergang heraus verantwortlich (§5.4).
OWNER: dict[Status, Owner] = {
    Status.PENDING: Owner.SCHEDULER,
    Status.RUNNING: Owner.WORKER,
    Status.FAILED: Owner.WORKER,
    Status.ERROR: Owner.SCHEDULER,
    Status.DEFERRED: Owner.SCHEDULER,
    Status.INACTIVE: Owner.SCHEDULER,
    Status.AWAITING: Owner.WORKER,
    Status.COMPLETE: Owner.SCHEDULER,
    Status.ZOMBIE: Owner.WORKER,
    Status.KILLED: Owner.WORKER,
}

# ── Übergangstabelle (§5.4/§5.5) ────────────────────────────────────────────

_TRANSITIONS: dict[tuple[Status, Event], Status] = {
    (Status.PENDING, Event.DISPATCH): Status.RUNNING,
    (Status.PENDING, Event.KILL): Status.KILLED,      # Stornierung vor Ausführung
    (Status.RUNNING, Event.COMPLETE): Status.COMPLETE,
    (Status.RUNNING, Event.FAIL): Status.FAILED,
    (Status.RUNNING, Event.DEFER): Status.DEFERRED,
    (Status.RUNNING, Event.AWAIT_INPUT): Status.AWAITING,
    (Status.RUNNING, Event.KILL): Status.KILLED,
    (Status.RUNNING, Event.SILENCE): Status.ZOMBIE,
    (Status.FAILED, Event.RETRY): Status.RUNNING,
    (Status.FAILED, Event.EXHAUST): Status.ERROR,
    (Status.DEFERRED, Event.RESUME): Status.RUNNING,
    (Status.DEFERRED, Event.EXPIRE): Status.INACTIVE,
    (Status.AWAITING, Event.INPUT): Status.RUNNING,
    (Status.AWAITING, Event.TIMEOUT): Status.ZOMBIE,
    # Terminal → pending (reset)
    (Status.COMPLETE, Event.RESET): Status.PENDING,
    (Status.ERROR, Event.RESET): Status.PENDING,
    (Status.INACTIVE, Event.RESET): Status.PENDING,
    (Status.ZOMBIE, Event.RESET): Status.PENDING,
    (Status.KILLED, Event.RESET): Status.PENDING,
}

# ── Typ-gebundene Kanten (§5.4) ─────────────────────────────────────────────

#: Events, die nur für bestimmte Typen gültig sind. Nicht gelistete Events gelten
#: für alle Typen.
EVENT_KINDS: dict[Event, frozenset[Kind]] = {
    Event.AWAIT_INPUT: frozenset({Kind.APP}),
    Event.INPUT: frozenset({Kind.APP}),
    Event.TIMEOUT: frozenset({Kind.APP}),
    Event.SILENCE: frozenset({Kind.JOB, Kind.CLAUDE}),
}

# ── Reason-Zuordnung (§5.5) ─────────────────────────────────────────────────

#: Events mit fest verknüpfter Root Cause (§5.5).
EVENT_REASON: dict[Event, Reason] = {
    Event.SILENCE: Reason.SILENCE,
    Event.TIMEOUT: Reason.ACTIVITY_TIMEOUT,
    Event.EXPIRE: Reason.DEFERRED_EXPIRED,
}

#: ``KILL`` trägt eine der drei Kill-Ursachen (§5.5) — vom Aufrufer bestimmt.
KILL_REASONS: frozenset[Reason] = frozenset(
    {Reason.NO_PROCESS, Reason.BY_USER, Reason.BY_WALL_TIME}
)


# ── API ─────────────────────────────────────────────────────────────────────


def is_terminal(status: Status) -> bool:
    """True, wenn ``status`` nur per ``RESET`` verlassen werden kann."""
    return status in TERMINAL


def owner(status: Status) -> Owner:
    """Der Eigentümer von ``status`` (§5.4)."""
    return OWNER[status]


def _kind_allows(event: Event, kind: Kind | None) -> bool:
    allowed = EVENT_KINDS.get(event)
    if allowed is None or kind is None:
        return True
    return kind in allowed


def can(status: Status, event: Event, *, kind: Kind | None = None) -> bool:
    """True, wenn ``(status, event)`` ein erlaubter Übergang ist.

    Mit ``kind`` werden zusätzlich die typ-gebundenen Kanten (§5.4) geprüft.
    """
    if (status, event) not in _TRANSITIONS:
        return False
    return _kind_allows(event, kind)


def apply(status: Status, event: Event, *, kind: Kind | None = None) -> Status:
    """Den Übergang anwenden; ``IllegalTransition`` bei verbotener/typ-fremder Kante."""
    if (status, event) not in _TRANSITIONS:
        raise IllegalTransition(f"{status} kennt kein Event {event}")
    if not _kind_allows(event, kind):
        allowed = ", ".join(sorted(EVENT_KINDS[event]))
        raise IllegalTransition(f"{event} gilt nur für Typ {{{allowed}}}, nicht {kind}")
    return _TRANSITIONS[(status, event)]


def events_from(status: Status, *, kind: Kind | None = None) -> set[Event]:
    """Alle aus ``status`` erlaubten Events (optional typ-gefiltert)."""
    return {
        ev for (st, ev) in _TRANSITIONS if st == status and _kind_allows(ev, kind)
    }


def targets(status: Status, *, kind: Kind | None = None) -> set[Status]:
    """Alle aus ``status`` erreichbaren Folgezustände (optional typ-gefiltert).

    Nützlich, um eine Status*meldung* zu prüfen, ohne den Event-Namen zu kennen
    (der Worker meldet den Zielzustand, §4.4): ``dst in targets(current)``."""
    return {_TRANSITIONS[(status, ev)] for ev in events_from(status, kind=kind)}


def reason_for(event: Event, *, kill_reason: Reason | None = None) -> Reason | None:
    """Die Root Cause eines Events (§5.5), falls eine zugeordnet ist.

    ``KILL`` hat keine feste Ursache — ``kill_reason`` (eine aus :data:`KILL_REASONS`)
    bestimmt sie; Default ``by_user``.
    """
    if event is Event.KILL:
        r = kill_reason or Reason.BY_USER
        if r not in KILL_REASONS:
            raise ValueError(f"{r} ist keine Kill-Ursache (§5.5)")
        return r
    return EVENT_REASON.get(event)
