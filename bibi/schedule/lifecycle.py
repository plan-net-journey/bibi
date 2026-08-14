"""Reine Zustandsmaschine des Job-Lifecycles (DESIGN §5.4/§5.5; PLAN-3 §3.0).

Kein DB-, kein HTTP-, kein Zeit-Zustand — nur ``(status, event) → status'``. Das
Herzstück wird isoliert bewiesen, bevor es an DB/Prozesse gekoppelt wird (wie der
``PushDebouncer`` der Phase 2). Die Übergänge und Owner-Regeln stammen 1:1 aus der
§5.4-Tabelle, die Root Causes aus §5.5.

PLAN-10 Stufe 10.0: ein einziger Typ ``JOB`` — keine typ-gebundenen Kanten mehr.
Alle Events gelten für alle Jobs. ``apply(..., kind=...)`` wird der Rückwärts-
kompatibilität wegen beibehalten, hat aber keine einschränkende Wirkung mehr.
"""

from __future__ import annotations

from enum import StrEnum

from bibi.schedule.models import Kind, Owner, Reason, Status


class Event(StrEnum):
    """Lifecycle-Auslöser (die Kanten des §5.5-Graphen)."""

    DISPATCH = "dispatch"        # pending → starting  (auch das User-Verb `start`)
    SPAWNED = "spawned"          # starting → running  (PID bekannt, #38)
    COMPLETE = "complete"        # running → complete
    FAIL = "fail"                # running/starting → failed
    DEFER = "defer"              # running → deferred
    AWAIT_INPUT = "await_input"  # running → awaiting   (nur app)
    KILL = "kill"                # running/starting/pending → killed
    SILENCE = "silence"          # running → zombie     (nur job/claude)
    RETRY = "retry"              # failed  → starting
    EXHAUST = "exhaust"          # failed  → error
    # ``RESUME`` startet den Job **von vorn** — der Name verspricht mehr, als
    # bibi hält (#177, Einwand m.rau 2026-08-13: *„Resume gefällt mir nicht,
    # weil es versprachlicht, dass an der Stelle fortgesetzt wird, wo aufgehört
    # wurde. Das stimmt aber nicht. Der Job startet von vorne."*).
    #
    # **Die Nicht-Zusage, ausgeschrieben, weil sie bisher nirgends stand:**
    # *„Checkpoint/Restart-Fähigkeit oder Idempotenz muss vom Job
    # operationalisiert werden."* Wer sich auf das Wort verlässt, baut einen
    # Job, der doppelt tut, was er einmal tun sollte.
    #
    # Der Name bleibt trotzdem (*„Never change a running system"*, m.rau
    # 2026-08-13) — der vorgeschlagene Umbau auf ``REDISPATCH`` entfällt. Der
    # Unterschied zu ``RETRY`` gehört daneben und ist der einzige zwischen
    # beiden: **``RETRY`` verbraucht einen Versuch, ``RESUME`` nicht.**
    RESUME = "resume"            # deferred → starting (Neustart, kein Fortsetzen)
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
    # STARTING gehört dem Worker: er hat reserviert und ist dabei, den Wrapper
    # zu spawnen — nur er kann den Zustand verlassen (SPAWNED bei Erfolg, FAIL
    # bei einem Setup-Fehler).
    Status.STARTING: Owner.WORKER,
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
    # PLAN-… / m.rau/bibi#38: der Dispatch landet nicht mehr direkt auf RUNNING,
    # sondern auf STARTING. Erst wenn der Wrapper läuft und seine PID bekannt
    # ist, schaltet SPAWNED weiter — daher die Invariante RUNNING ⇒ pid gesetzt.
    (Status.PENDING, Event.DISPATCH): Status.STARTING,
    (Status.STARTING, Event.SPAWNED): Status.RUNNING,
    # Setup-Fehler vor dem Spawn (Worktree, Container, Image): derselbe FAIL-
    # Weg wie ein fehlgeschlagener Lauf, damit Retry/Backoff unverändert
    # greifen — der Job ist nicht anders gescheitert, nur früher.
    (Status.STARTING, Event.FAIL): Status.FAILED,
    (Status.STARTING, Event.KILL): Status.KILLED,
    # STARTING muss JEDE Wrapper-Meldung annehmen, die auch RUNNING annimmt —
    # der Wrapper läuft ab dem ``Popen`` und meldet eigenständig, während
    # ``report_pid()`` erst danach an die Reihe kommt. Ein kurzer Job ist
    # regelmäßig fertig, bevor seine PID notiert ist; ohne diese Kanten würde
    # sein Abschluss als "invalid" verworfen und die Zeile bliebe für immer auf
    # STARTING stehen. Derselbe Race, den ``report_pid()`` von der anderen Seite
    # behandelt — nur ist er hier kein Randfall, sondern der Normalfall für alles
    # unter ein paar hundert Millisekunden.
    (Status.STARTING, Event.COMPLETE): Status.COMPLETE,
    (Status.STARTING, Event.DEFER): Status.DEFERRED,
    (Status.STARTING, Event.AWAIT_INPUT): Status.AWAITING,
    (Status.STARTING, Event.SILENCE): Status.ZOMBIE,
    (Status.RUNNING, Event.COMPLETE): Status.COMPLETE,
    (Status.RUNNING, Event.FAIL): Status.FAILED,
    (Status.RUNNING, Event.DEFER): Status.DEFERRED,
    (Status.RUNNING, Event.AWAIT_INPUT): Status.AWAITING,
    (Status.RUNNING, Event.KILL): Status.KILLED,
    (Status.RUNNING, Event.SILENCE): Status.ZOMBIE,
    # Retry und Resume gehen ebenfalls über STARTING — sie sind Dispatches wie
    # jeder andere, und auch bei ihnen ist die PID erst nach dem Spawn bekannt.
    (Status.FAILED, Event.RETRY): Status.STARTING,
    (Status.FAILED, Event.EXHAUST): Status.ERROR,
    (Status.DEFERRED, Event.RESUME): Status.STARTING,
    (Status.DEFERRED, Event.EXPIRE): Status.INACTIVE,
    # #210: derselbe Ausgang für einen Slot, den der Dispatcher per Konstruktion
    # nie holt (`attempts=0` aus der Zeit vor #168). **Die Nuance gehört
    # benannt:** bei DEFERRED heißt EXPIRE „die Frist ist abgelaufen", hier
    # „diese Zeile wird nie dran sein". Verschiedene Anlässe, dieselbe Aussage —
    # INACTIVE bedeutet an beiden Stellen *daraus wird nichts mehr*, und dafür
    # einen zweiten Event einzuführen hieße, dieselbe Kante zweimal zu führen.
    #
    # Ohne diese Kante schrieb `report_status()` nicht und meldete `"invalid"` —
    # der Sweeper zählte den Erfolg trotzdem, weil er den Rückgabewert verwarf.
    (Status.PENDING, Event.EXPIRE): Status.INACTIVE,
    (Status.AWAITING, Event.INPUT): Status.RUNNING,
    (Status.AWAITING, Event.TIMEOUT): Status.ZOMBIE,
    (Status.AWAITING, Event.KILL): Status.KILLED,
    # Terminal → pending (reset)
    (Status.COMPLETE, Event.RESET): Status.PENDING,
    (Status.ERROR, Event.RESET): Status.PENDING,
    (Status.INACTIVE, Event.RESET): Status.PENDING,
    (Status.ZOMBIE, Event.RESET): Status.PENDING,
    (Status.KILLED, Event.RESET): Status.PENDING,
    # KILL greift überall dort, wo gerade noch ein Lauf aktiv oder unmittelbar
    # bevorstehend ist (pending wartet auf Trigger, failed auf Retry, deferred
    # auf Resume) — reine Lauf-Ebene, KEINE Job/Schedule-Semantik mehr (User-
    # Feedback 2026-07-03: "vermischt Lauf und Job/Schedule-Behandlung").
    (Status.PENDING, Event.KILL): Status.KILLED,
    (Status.FAILED, Event.KILL): Status.KILLED,
    (Status.DEFERRED, Event.KILL): Status.KILLED,
    # User-Redesign 2026-07-20 (widerruft den Teil von 2026-07-03, der COMPLETE
    # bewusst ausschloss): dank Lazy Rearm trägt ein wiederkehrender complete-
    # Job weiter einen next_fire_at und dispatcht sich beim nächsten fälligen
    # Tick von selbst neu — KILL war dort bis hierhin ein reiner No-op, ein Job
    # ließ sich also gar nicht "anhalten", ohne die MD zu editieren. Jetzt: wie
    # ein RESET archiviert dieser Übergang den abgeschlossenen Lauf (s.
    # report_status()s eigener Archiv-Zweig für genau diese Kante), landet aber
    # sofort auf KILLED statt PENDING — next_fire_at wird dabei genullt (kommt
    # aus dem KILLED/ERROR/…-Zweig dort), Lazy Rearm kann diesen Zustand also
    # nicht mehr überholen. Bleibt weiterhin reine Lauf-Ebene, keine MD-
    # Änderung: ein RESET holt den Job jederzeit zurück in den Schedule.
    (Status.COMPLETE, Event.KILL): Status.KILLED,
}

# ── Typ-gebundene Kanten ─────────────────────────────────────────────────────
# PLAN-10 Stufe 10.0: ein Typ (JOB) → keine Einschränkungen.
EVENT_KINDS: dict[Event, frozenset[Kind]] = {}

# ── Reason-Zuordnung (§5.5) ─────────────────────────────────────────────────

#: Events mit fest verknüpfter Root Cause (§5.5).
EVENT_REASON: dict[Event, Reason] = {
    Event.SILENCE: Reason.SILENCE,
    # TIMEOUT (awaiting → zombie) bleibt ein eigenes Event in der Übergangs-
    # tabelle (anderer Quellzustand als SILENCE), meldet aber dieselbe Root
    # Cause — User-Feedback 2026-07-04: "Silence bei Jobs = Aktivität bei Apps".
    Event.TIMEOUT: Reason.SILENCE,
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
