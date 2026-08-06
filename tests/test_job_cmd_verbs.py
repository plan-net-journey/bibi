"""m.rau/bibi#164 — die CLI spricht die Verben des Modells.

Die kleinste Änderung dieses Releases mit der längsten Belegkette. ``bibi-ctrl
job restart`` gab es, das Modell kennt das Verb nicht: ``slot.Verb`` führt
``START``/``RESET``/``KILL``, die Route heißt ``/-/job/{id}/reset``. Der Schaden
war keine Fehlfunktion — der Befehl tat, was ``reset`` tut —, sondern eine
falsche Aussage: aus *„ich kenne keinen Weg, einen Job sofort fällig zu machen"*
wurde *„es gibt keinen"*, und das stand als Verfahrenslücke am Board, bis m.rau
widersprach. **Ein Ticket, das eine vorhandene Fähigkeit als fehlend meldet,
kostet mehr als eine ungestellte Frage — es wird geglaubt, und jemand baut
daran.**

``restart`` verschwindet **ersatzlos**, ohne Alias: ein Alias hielte genau die
Verwechslung am Leben, um die es geht. Pre-1.0 ist das erlaubt (siehe
``.claude/CLAUDE.md`` und den v0.7.1-Plan); nach ``v1.0.0`` wäre dieselbe
Änderung ein Major.

Braucht kein Git-Repo und keinen Daemon, daher nicht ``slow``.
"""

from __future__ import annotations

import argparse

import pytest

from bibi.ctrl import job_cmd
from bibi.schedule.slot import Verb


def _job_subcommands() -> set[str]:
    """Die Subkommando-Namen, die ``bibi-ctrl job`` tatsächlich registriert."""
    parser = argparse.ArgumentParser(prog="bibi-ctrl")
    sub = parser.add_subparsers(dest="cmd")
    job_cmd.register(sub)
    job_parser = sub.choices["job"]
    for action in job_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("job hat keine Subkommandos")


def test_jedes_modell_verb_hat_ein_subkommando() -> None:
    """``START``/``RESET``/``KILL`` — die Verben aus ``slot.Verb``, nicht aus
    der Erinnerung. Die Quelle ist das Enum selbst, damit der Test mitwandert,
    wenn das Modell wächst."""
    fehlend = {str(v) for v in Verb} - _job_subcommands()
    assert not fehlend, f"Verben ohne CLI-Subkommando: {sorted(fehlend)}"


def test_restart_ist_ersatzlos_verschwunden() -> None:
    """Kein Alias — der hielte die Verwechslung am Leben."""
    assert "restart" not in _job_subcommands()


def test_die_cli_erfindet_keine_verben() -> None:
    """Die Gegenrichtung: jedes handelnde Subkommando muss im Modell stehen.

    ``list``/``show``/``rescan`` sind Abfragen und wirken auf keinen Slot —
    sie stehen deshalb zu Recht nicht in ``slot.Verb``. Alles andere schon.
    Ohne diese Hälfte könnte die CLI ein zweites ``restart`` bekommen und die
    Tests oben blieben grün."""
    abfragen = {"list", "show", "rescan"}
    handelnd = _job_subcommands() - abfragen
    assert handelnd <= {str(v) for v in Verb}, \
        f"CLI-Verben ohne Entsprechung im Modell: {sorted(handelnd - {str(v) for v in Verb})}"
