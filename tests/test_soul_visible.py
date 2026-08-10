"""Die aktive Persona ist ablesbar (`#75` Teil A, `#45`).

**Der Zustand wurde geführt und war nirgends zu sehen.** `soul:` steht seit
jeher in der repo-globalen `.state.md`, `state.get_soul()` existiert seit
`state.py:309` — aber weder `bibi-ctrl status` (das `/state` aufruft) noch die
Statusleiste zeigten ihn. Sichtbar wurde er nur durch einen expliziten
`bibi-ctrl soul`, also **genau durch das Kommando, das man nicht tippt, wenn
man die Antwort schon zu kennen glaubt.**

Alle anderen Zustände dieser Art — Case, `proto:`, `sync:` — stehen längst in
der Leiste. Die Soul war die Ausnahme.

**Die Skill-Doku hat die Lücke als Eigenschaft beschrieben:** *„a plain
persistence field, not shown by `/state`"*. Ein Satz, der stimmte und dadurch
verhinderte, dass jemand die Lücke für eine hielt.

**Was hier NICHT geprüft wird, weil es nicht gebaut ist:** ob die Persona auch
*wirkt*. Eine Sitzung ohne expliziten `/soul` läuft weiterhin ohne sie — das
ist `#75` Teil B, drei Varianten, keine entschieden. Anzeige und Wirkung sind
zwei Fragen, und dieser Test beantwortet nur die erste.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bibi import state
from bibi.ctrl import status_cmd, statusline_cmd


def test_status_shows_the_active_soul(team_repo: Path, capsys):
    """`/state` ruft dieses Kommando — hier fehlte die Zeile schlicht."""
    state.set_soul("Rook")
    status_cmd.run(argparse.Namespace())
    assert "soul: Rook" in capsys.readouterr().out, (
        "der aktive Zustand wird gefuehrt und nicht gezeigt — genau die Frage, "
        "die nach einer Kompaktierung jemand stellt (#75 A)")


def test_status_says_none_rather_than_staying_silent(team_repo: Path, capsys):
    """**Abwesenheit ist hier selbst eine Auskunft.**

    Anders als in der Statusleiste: `status` ist die vollstaendige Lage eines
    Knotens. „Keine Persona aktiv" ist genau das, was jemand wissen will, der
    nachsieht, weil er sich nicht mehr sicher ist.
    """
    status_cmd.run(argparse.Namespace())
    assert "soul: (none)" in capsys.readouterr().out


def test_the_status_line_carries_the_soul(team_repo: Path):
    """`#45`: dieselbe Achse, andere Stelle — passiv ablesbar statt erfragt."""
    state.set_soul("Rook")
    zeile = statusline_cmd.render({"session_id": "s1"})
    assert "soul:Rook" in zeile, (
        "die Statusleiste traegt Case, proto und sync — die Soul war die "
        "Ausnahme, obwohl sie das Verhalten am staerksten aendert (#45)")


def test_the_status_line_stays_quiet_without_a_soul(team_repo: Path):
    """**Kein Segment fuer eine Abwesenheit.**

    Die Leiste traegt schon Branch, Modell, ctx%, Case, proto und sync; ein
    siebtes Segment, das „nichts" sagt, verdraengt eines, das etwas sagt.
    Deshalb hier stumm — und in `status` (oben) nicht.
    """
    assert "soul" not in statusline_cmd.render({"session_id": "s1"})


def test_both_skill_texts_say_what_the_code_does():
    """Die Doku hat die Luecke als Eigenschaft beschrieben — **in der Quelle**.

    `skills/` im Engine-Repo ist der Ursprung fuer `/library use`; eine
    Reparatur nur in der Instanz schriebe ein spaeterer Sync zurueck.
    """
    wurzel = Path(statusline_cmd.__file__).resolve().parent.parent.parent / "skills"
    soul = (wurzel / "bibi-soul" / "SKILL.md").read_text(encoding="utf-8")
    zustand = (wurzel / "bibi-state" / "SKILL.md").read_text(encoding="utf-8")
    assert "not shown by" not in soul, (
        "der Skilltext behauptet weiterhin, /state zeige die Soul nicht")
    assert "soul" in zustand.lower(), (
        "die Feldliste von /state kennt die Soul nicht")
