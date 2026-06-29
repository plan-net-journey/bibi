"""Reine Zustandsmaschine (DESIGN §5.4/§5.5; PLAN-3 §3.0) — erschöpfend."""

from __future__ import annotations

import itertools

import pytest

from bibi.schedule import lifecycle as lc
from bibi.schedule.lifecycle import Event, IllegalTransition
from bibi.schedule.models import Kind, Owner, Reason, Status

# Die kanonische §5.4-Tabelle als Wahrheit für den Test (from, event, to).
EXPECTED_TRANSITIONS = [
    (Status.PENDING, Event.DISPATCH, Status.RUNNING),
    (Status.PENDING, Event.KILL, Status.KILLED),
    (Status.RUNNING, Event.COMPLETE, Status.COMPLETE),
    (Status.RUNNING, Event.FAIL, Status.FAILED),
    (Status.RUNNING, Event.DEFER, Status.DEFERRED),
    (Status.RUNNING, Event.AWAIT_INPUT, Status.AWAITING),
    (Status.RUNNING, Event.KILL, Status.KILLED),
    (Status.RUNNING, Event.SILENCE, Status.ZOMBIE),
    (Status.FAILED, Event.RETRY, Status.RUNNING),
    (Status.FAILED, Event.EXHAUST, Status.ERROR),
    (Status.DEFERRED, Event.RESUME, Status.RUNNING),
    (Status.DEFERRED, Event.EXPIRE, Status.INACTIVE),
    (Status.AWAITING, Event.INPUT, Status.RUNNING),
    (Status.AWAITING, Event.TIMEOUT, Status.ZOMBIE),
    (Status.AWAITING, Event.KILL, Status.KILLED),
    (Status.COMPLETE, Event.RESET, Status.PENDING),
    (Status.ERROR, Event.RESET, Status.PENDING),
    (Status.INACTIVE, Event.RESET, Status.PENDING),
    (Status.ZOMBIE, Event.RESET, Status.PENDING),
    (Status.KILLED, Event.RESET, Status.PENDING),
]


@pytest.mark.parametrize("src,event,dst", EXPECTED_TRANSITIONS)
def test_allowed_transitions(src, event, dst):
    assert lc.apply(src, event) == dst
    assert lc.can(src, event)


def test_no_extra_transitions():
    # Die Maschine erlaubt GENAU die §5.4-Kanten — keine versehentliche dazu.
    allowed = {(s, e) for (s, e, _) in EXPECTED_TRANSITIONS}
    for status, event in itertools.product(Status, Event):
        if (status, event) in allowed:
            assert lc.can(status, event)
        else:
            assert not lc.can(status, event)
            with pytest.raises(IllegalTransition):
                lc.apply(status, event)


def test_forbidden_edges_explicit():
    # Stichproben verbotener Kanten, die leicht durchrutschen.
    for src, ev in [
        (Status.PENDING, Event.COMPLETE),   # darf nicht direkt fertig werden
        (Status.COMPLETE, Event.DISPATCH),  # Terminal nur via reset
        (Status.RUNNING, Event.RESET),      # running ist nicht terminal
        (Status.RUNNING, Event.DISPATCH),   # nur aus pending
        (Status.PENDING, Event.RESET),      # pending ist kein Terminal
        (Status.ERROR, Event.RETRY),        # error nur via reset
    ]:
        assert not lc.can(src, ev)
        with pytest.raises(IllegalTransition):
            lc.apply(src, ev)


def test_terminal_set_matches_design():
    assert lc.TERMINAL == frozenset(
        {Status.COMPLETE, Status.ERROR, Status.INACTIVE, Status.ZOMBIE, Status.KILLED}
    )
    for t in lc.TERMINAL:
        assert lc.is_terminal(t)
        # Terminal → einziges Event ist RESET → pending.
        assert lc.events_from(t) == {Event.RESET}
        assert lc.apply(t, Event.RESET) == Status.PENDING
    assert not lc.is_terminal(Status.RUNNING)
    assert not lc.is_terminal(Status.PENDING)


def test_owner_matches_design_5_4():
    assert lc.owner(Status.PENDING) is Owner.SCHEDULER
    assert lc.owner(Status.RUNNING) is Owner.WORKER
    assert lc.owner(Status.FAILED) is Owner.WORKER
    assert lc.owner(Status.ERROR) is Owner.SCHEDULER
    assert lc.owner(Status.DEFERRED) is Owner.SCHEDULER
    assert lc.owner(Status.INACTIVE) is Owner.SCHEDULER
    assert lc.owner(Status.AWAITING) is Owner.WORKER
    assert lc.owner(Status.COMPLETE) is Owner.SCHEDULER
    assert lc.owner(Status.ZOMBIE) is Owner.WORKER
    assert lc.owner(Status.KILLED) is Owner.WORKER
    # jeder Zustand hat genau einen Owner
    assert set(lc.OWNER) == set(Status)


# ── Typ-gebundene Kanten (PLAN-10 Stufe 10.0: ein Typ, keine Einschränkungen) ─


def test_all_events_valid_for_job():
    # Ein Typ JOB — alle Kanten gelten für ihn.
    assert lc.apply(Status.RUNNING, Event.AWAIT_INPUT, kind=Kind.JOB) == Status.AWAITING
    assert lc.apply(Status.AWAITING, Event.INPUT, kind=Kind.JOB) == Status.RUNNING
    assert lc.apply(Status.AWAITING, Event.TIMEOUT, kind=Kind.JOB) == Status.ZOMBIE
    assert lc.apply(Status.RUNNING, Event.SILENCE, kind=Kind.JOB) == Status.ZOMBIE


def test_all_edges_allow_kind_job():
    # Kein Event ist für Kind.JOB gesperrt.
    for k in Kind:
        assert lc.apply(Status.PENDING, Event.DISPATCH, kind=k) == Status.RUNNING
        assert lc.apply(Status.RUNNING, Event.COMPLETE, kind=k) == Status.COMPLETE


def test_events_from_with_kind_job():
    # Alle Kanten aus RUNNING erreichbar.
    job_events = lc.events_from(Status.RUNNING, kind=Kind.JOB)
    assert Event.SILENCE in job_events
    assert Event.AWAIT_INPUT in job_events


# ── Reason-Zuordnung (§5.5) ─────────────────────────────────────────────────


def test_reason_for_fixed_events():
    assert lc.reason_for(Event.SILENCE) is Reason.SILENCE
    assert lc.reason_for(Event.TIMEOUT) is Reason.ACTIVITY_TIMEOUT
    assert lc.reason_for(Event.EXPIRE) is Reason.DEFERRED_EXPIRED
    assert lc.reason_for(Event.COMPLETE) is None  # Happy-Path hat keine Root Cause


def test_reason_for_kill_picks_cause():
    assert lc.reason_for(Event.KILL) is Reason.BY_USER  # Default
    assert lc.reason_for(Event.KILL, kill_reason=Reason.BY_WALL_TIME) is Reason.BY_WALL_TIME
    assert lc.reason_for(Event.KILL, kill_reason=Reason.NO_PROCESS) is Reason.NO_PROCESS
    with pytest.raises(ValueError):
        lc.reason_for(Event.KILL, kill_reason=Reason.SILENCE)  # keine Kill-Ursache


def test_kill_reasons_set():
    assert lc.KILL_REASONS == frozenset(
        {Reason.NO_PROCESS, Reason.BY_USER, Reason.BY_WALL_TIME}
    )


# ── targets() (Statusmeldungs-Validierung, §4.4) ─────────────────────────────


def test_targets_of_running():
    t = lc.targets(Status.RUNNING, kind=Kind.JOB)
    assert t == {
        Status.COMPLETE, Status.FAILED, Status.DEFERRED,
        Status.KILLED, Status.ZOMBIE, Status.AWAITING,
    }


def test_targets_of_pending_and_terminal():
    assert lc.targets(Status.PENDING) == {Status.RUNNING, Status.KILLED}
    assert lc.targets(Status.COMPLETE) == {Status.PENDING}
