"""Der Slot — der eine Ausfuehrungsplatz eines Jobs (m.rau/bibi#109).

Ein Job hat genau einen Platz, auf dem gelaufen wird. Er ist reserviert
(``pending``), besetzt (``running``), blockiert (``error``) oder verbraucht
(``done``). Der Begriff ist genauer als "der aktuelle Lauf", weil dieselbe
Zeile vier Dinge mit verschiedenen Zeitrichtungen bedeuten kann.

``done`` ist der zwoelfte Zustand und gehoert bewusst NICHT zu den elf
``models.Status``: ein *Lauf* ist nie ``done`` — er ist ``complete``, ``error``
oder ``killed``. Nur ein *Slot* kann verbraucht sein. Spiegelbildlich dazu ist
``complete`` der einzige Lauf-Zustand, der nie im Slot steht (Archivierungs-
regel A1). Genau diese Spiegelung ist der Grund, warum ``done`` nicht zu den
Lauf-Zustaenden addiert werden durfte.

Reine Funktionen, keine Datenbank, kein HTTP — dieselbe Trennung wie in
``controller/jobs_view.py``: der schwierige Teil (welcher Knopf wann) laesst
sich ohne Fixtures pruefen.
"""

from __future__ import annotations

import pytest

from bibi.daemon import job_db
from bibi.schedule import lifecycle, slot
from bibi.schedule.models import Status


# ── done ist ein Slot-Zustand, kein Lauf-Zustand ────────────────────────────


def test_done_is_not_a_run_status():
    """Der Kern der Trennung: wer ``done`` zu den elf addiert, bekommt einen
    Lauf, der ``done`` sein kann — und den gibt es nicht."""
    assert slot.DONE not in {str(s) for s in Status}


def test_slot_states_are_the_eleven_plus_done():
    assert slot.STATES == frozenset({*(str(s) for s in Status), slot.DONE})


def test_done_has_no_exit_at_all():
    """Der einzige Zustand ohne Ausgang. ``error`` ist auch blockiert, hat aber
    START und RESET — ``done`` hat nichts, weil der Termin verbraucht ist."""
    assert slot.actions(slot.DONE) == frozenset()


def test_blocked_and_finished_are_not_the_same_question():
    """Zwei verschiedene Fragen, die leicht verwechselt werden: „läuft gerade
    nichts?" und „läuft ohne Eingriff nie wieder was?". Ein ``complete``-Slot
    beantwortet die erste mit ja und die zweite mit nein — er hat seinen
    nächsten Termin und läuft von selbst wieder an."""
    assert slot.is_finished("complete") and not slot.is_blocked("complete")
    assert slot.is_finished("error") and slot.is_blocked("error")
    assert slot.is_finished(slot.DONE) and not slot.is_blocked(slot.DONE)
    assert not slot.is_finished("running")


# ── die vier Knopf-Gesichter (Zustandsmodell §4) ────────────────────────────


@pytest.mark.parametrize("state", ["pending", "failed", "deferred"])
def test_waiting_slots_offer_start_and_kill(state):
    assert slot.actions(state) == frozenset({slot.Verb.START, slot.Verb.KILL})


@pytest.mark.parametrize("state", ["starting", "running", "awaiting"])
def test_busy_slots_offer_only_kill(state):
    """Ein laufender Job kann nicht gestartet werden, und RESET wuerde einen
    Lauf verwerfen, der noch etwas tut."""
    assert slot.actions(state) == frozenset({slot.Verb.KILL})


@pytest.mark.parametrize("state", ["error", "inactive", "zombie", "killed"])
def test_blocked_slots_offer_start_and_reset(state):
    """Beide archivieren erst (A2) und unterscheiden sich allein im naechsten
    Zeitpunkt: START setzt ``now``, RESET den regulaeren Termin. KILL ist tot —
    es laeuft nichts mehr, was zu beenden waere."""
    assert slot.actions(state) == frozenset({slot.Verb.START, slot.Verb.RESET})


def test_a_completed_slot_is_a_waiting_slot_with_a_history():
    """``complete`` steht sehr wohl im Slot, und zwar bewusst: der *Lauf* ist
    nach A1 sofort archiviert, die Zeile traegt den Zustand bis zum naechsten
    faelligen Tick weiter (lazy Rearm in ``reserve_next()``, Entscheidung
    m.rau: "archiviert wird erst vor dem naechsten Rerun"). Die
    FE-Spezifikation §4.5 rechnet damit — sie zaehlt "``complete``, sofern ein
    ``next`` gesetzt ist" zu ``waiting``.

    KILL fehlt: es laeuft nichts, was zu beenden waere."""
    assert slot.actions("complete") == frozenset({slot.Verb.START, slot.Verb.RESET})


def test_every_slot_state_has_a_defined_button_face():
    """Vollstaendigkeit: keine Luecke zwischen den Leisten — sonst faellt ein
    Zustand durch und der Screen zeigt gar keine Knoepfe, ohne dass das als
    Fehler auffaellt."""
    for state in slot.STATES:
        assert isinstance(slot.actions(state), frozenset)


def test_an_unknown_state_is_an_error_not_an_empty_toolbar():
    with pytest.raises(ValueError):
        slot.actions("bogus")


# ── die beiden Invarianten der Spiegelung ───────────────────────────────────


def test_the_state_machine_has_no_edge_out_of_done():
    """``done`` ist kein ``models.Status``, also kennt die Zustandsmaschine ihn
    nicht — und genau daran scheitert jeder Versuch, ihn zu verlassen. Der
    Schutz entsteht aus der Modellierung, nicht aus einer Sonderpruefung."""
    assert lifecycle.events_from(slot.DONE) == set()
    for event in lifecycle.Event:
        with pytest.raises(lifecycle.IllegalTransition):
            lifecycle.apply(slot.DONE, event)


def test_done_never_reaches_the_journal(tmp_path):
    """Die zweite Haelfte der Spiegelung: ``complete`` steht nie im Slot,
    ``done`` nie im Journal. Ein Oneshot wird bei ``complete`` archiviert —
    erst *danach* geht sein Slot auf ``done``, und was dort steht, wandert
    nicht noch einmal."""
    conn = job_db.connect(tmp_path / "jobs.sqlite")
    try:
        stored = {r[0] for r in conn.execute("SELECT DISTINCT status FROM journal")}
        assert slot.DONE not in stored
        # Und der Weg dorthin ist versperrt: das Journal kennt nur Lauf-
        # Zustaende, und `done` ist keiner.
        assert slot.DONE not in {str(s) for s in Status}
    finally:
        conn.close()
