"""Die Suite darf nicht davon abhängen, wer sie startet (m.rau/bibi#75).

**Der Befund, der diese Datei nötig machte:** derselbe Commit lieferte am
2026-07-31 drei verschiedene Ergebnisse — 1 roter Test in einer nackten Shell,
10 in einer Shell mit gesourcter Knoten-Config, 11 als echter bibi-Job. Später
am selben Tag ergab dieselbe Revision auf zwei Maschinen 12 bzw. 13 rote
Tests, von denen nur 9 auf beiden rot waren.

Die Ursache ist keine Testlogik, sondern die Umgebung: ``worker.py`` reicht mit
``env = os.environ.copy()`` die komplette Daemon-Umgebung in jeden Job, und
``config.env_path()`` fällt ohne ``XDG_CONFIG_HOME`` auf die echte
``~/.config/bibi/env`` des ausführenden Nutzers zurück. Ein Test, der beides
nicht wegräumt, misst den Rechner statt den Code.

Ein CI, dessen Ergebnis davon abhängt, wer es startet, kann kein Merge-Veto
begründen — und wer einen roten Lauf von Hand nachstellt, bekommt eine andere
Antwort als die Maschine. Deshalb steht die Prüfung hier als eigener Test und
nicht nur als Fixture: eine Fixture, die still nicht mehr greift, fällt
niemandem auf.
"""

from __future__ import annotations

import os

from pathlib import Path

from bibi import config


def test_no_bibi_variable_leaks_into_the_suite():
    leaked = sorted(k for k in os.environ if k.startswith("BIBI_"))
    assert leaked == [], (
        "Diese BIBI_*-Variablen stammen aus der Startumgebung und verfälschen "
        "die Suite: " + ", ".join(leaked))


def test_config_path_does_not_point_at_the_real_user_config():
    """``doctor``/``hygiene`` lesen die Knoten-Config. Ohne Umlenkung ist das
    die echte Datei des Nutzers, samt seiner Credentials — auf sarasate genau
    der Grund, warum sechs ``test_hygiene``-Tests rot waren und auf dem Mac
    nicht."""
    real = Path.home() / ".config" / "bibi"
    assert not str(config.env_path()).startswith(str(real)), (
        f"env_path() zeigt auf die echte Nutzer-Konfiguration: {config.env_path()}")
