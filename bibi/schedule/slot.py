"""Der Slot — der eine Ausführungsplatz eines Jobs (Zustandsmodell §1).

Ein Job hat genau eine Zeile in der Scheduler-DB, und die heißt Slot. Der
Begriff ist genauer als „der aktuelle Lauf", weil dieselbe Zeile vier
verschiedene Dinge mit verschiedenen Zeitrichtungen bedeuten kann — eine
Reservierung für die Zukunft, einen laufenden Prozess, eine Unterbrechung,
oder einen Rückstand aus der Vergangenheit, der nicht abgeräumt wurde. Was
alle vier teilen, ist kein Zeitpunkt, sondern ein Platz: er ist reserviert,
besetzt, blockiert oder verbraucht.

Der Slot erklärt die Archivierungsregel besser als jede Statusliste: ein
blockierter Slot ist nicht freigegeben, deshalb feuert nichts mehr; ``RESET``
heißt „Slot räumen und neu reservieren"; ``complete`` räumt sich selbst, weil
sonst der nächste Lauf keinen Platz fände.

**``done`` und ``complete`` sind Spiegelbilder.** ``complete`` ist der einzige
Lauf-Zustand, der nie im Slot steht; ``done`` der einzige Slot-Zustand, der nie
im Journal steht. ``complete`` heißt „der Lauf ist fertig, der Slot wird neu",
``done`` heißt „der Slot ist fertig, ein Lauf kommt nicht mehr". Deshalb gehört
``done`` nicht zu den elf ``models.Status``: ein *Lauf* ist nie ``done``.

Dieses Modul ist rein — keine Datenbank, kein HTTP, keine Zeit. Es beantwortet
genau eine Frage: welche Verben stehen auf einem Slot zur Verfügung.
"""

from __future__ import annotations

from enum import StrEnum

from bibi.schedule.models import Status

#: Der zwölfte Zustand, auf der Slot- statt der Lauf-Ebene: der Termin ist
#: verbraucht, ein Lauf kommt nicht mehr. Erreicht ihn nur ein Oneshot (``at:``)
#: nach erfolgreichem Durchlauf — der einzige Trigger, der sich verbraucht.
DONE = "done"

#: Alle Werte, die in der ``status``-Spalte eines Slots stehen können.
STATES: frozenset[str] = frozenset({*(str(s) for s in Status), DONE})

#: Alle Werte, die dort tatsächlich *angetroffen* werden. ``complete`` fehlt:
#: nach der Archivierungsregel A1 wird ein fertiger Lauf unverzüglich ins
#: Journal geschrieben und die Zeile im selben Zug neu initialisiert — der
#: Zustand existiert nur für die Dauer eines Schreibvorgangs.
STATES_IN_A_SLOT: frozenset[str] = STATES - {str(Status.COMPLETE)}

#: Kein Ausgang mehr, ohne dass ein Mensch eingreift. ``done`` ist darunter der
#: einzige ganz ohne Ausgang (s. ``actions()``).
TERMINAL: frozenset[str] = frozenset({
    str(Status.ERROR), str(Status.INACTIVE), str(Status.ZOMBIE),
    str(Status.KILLED), DONE,
})


class Verb(StrEnum):
    """Die Verben, die auf einen Slot wirken (Zustandsmodell §4).

    ``DELETE`` fehlt hier bewusst: es wirkt auf eine Journal-Zeile, nicht auf
    den Slot — die Scheduler-DB-Zeile verschwindet nie, sie wird überschrieben.
    """

    START = "start"
    RESET = "reset"
    KILL = "kill"


#: Die vier Knopf-Gesichter. Genau vier, deshalb muss die Zustandstabelle in
#: der Oberfläche nicht abgedruckt werden.
_ACTIONS: dict[str, frozenset[Verb]] = {
    # Wartend: START zieht den Termin auf jetzt vor (bei ``failed``/``deferred``
    # überspringt er die verbleibende Frist), KILL bricht ab.
    str(Status.PENDING): frozenset({Verb.START, Verb.KILL}),
    str(Status.FAILED): frozenset({Verb.START, Verb.KILL}),
    str(Status.DEFERRED): frozenset({Verb.START, Verb.KILL}),
    # Besetzt: START wäre ein zweiter Lauf auf demselben Platz, RESET würde
    # einen Lauf verwerfen, der noch etwas tut. ``awaiting`` ist auch hier
    # unzulässig — dort wartet ein Mensch, nicht die Uhr.
    str(Status.STARTING): frozenset({Verb.KILL}),
    str(Status.RUNNING): frozenset({Verb.KILL}),
    str(Status.AWAITING): frozenset({Verb.KILL}),
    # Blockiert: beide archivieren erst (A2) und unterscheiden sich allein im
    # nächsten Zeitpunkt — START setzt ``now``, RESET den regulären Termin.
    # KILL ist tot: es läuft nichts mehr, was zu beenden wäre.
    str(Status.ERROR): frozenset({Verb.START, Verb.RESET}),
    str(Status.INACTIVE): frozenset({Verb.START, Verb.RESET}),
    str(Status.ZOMBIE): frozenset({Verb.START, Verb.RESET}),
    str(Status.KILLED): frozenset({Verb.START, Verb.RESET}),
    # Verbraucht: keine. Das Fehlen der Leiste ist selbst die Aussage — ein
    # ``done``-Slot zeigt keine toten Knöpfe, weil es nichts mehr zu tun gibt.
    # Wer den Oneshot erneut laufen lassen will, legt eine neue ``at``-Datei an
    # oder macht ihn zu einem ``adhoc``-Job.
    DONE: frozenset(),
}


def is_terminal(state: str) -> bool:
    """Ob der Slot ohne Eingriff nicht mehr weiterläuft."""
    return state in TERMINAL


def actions(state: str) -> frozenset[Verb]:
    """Die auf diesem Slot verfügbaren Verben.

    Wirft ``ValueError`` bei ``complete`` und bei Unbekanntem statt eine leere
    Menge zu liefern: eine leere Leiste ist eine *Aussage* (``done`` — hier ist
    nichts mehr zu tun), und die darf nicht versehentlich aus einem Tippfehler
    entstehen. ``complete`` ist eigens genannt, weil es zwar ein gültiger
    Lauf-Zustand ist, in einem Slot aber nach A1 nie angetroffen wird.
    """
    if state == str(Status.COMPLETE):
        raise ValueError(
            "complete steht nie in einem Slot (Archivierungsregel A1): ein "
            "fertiger Lauf wird unverzüglich archiviert und die Zeile im "
            "selben Zug neu initialisiert")
    try:
        return _ACTIONS[state]
    except KeyError:
        raise ValueError(f"kein Slot-Zustand: {state!r}") from None
