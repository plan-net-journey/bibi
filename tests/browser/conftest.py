"""Fixtures der Browser-Ebene. Das Handwerk liegt in ``browserlib.py``.

Die Trennung ist keine Kosmetik: ein Testmodul darf ``browserlib`` importieren,
eine ``conftest`` nicht importiert werden — pytest lädt sie unter einem eigenen
Namen und aus zwei Verzeichnissen zugleich (``tests/`` hat auch eine). Ein
``from conftest import …`` griffe je nach ``sys.path``-Reihenfolge mal die eine,
mal die andere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .browserlib import _ZAEHLER_JS, Fabrik


@pytest.fixture
def fabrik(tmp_path: Path):
    """Daemon-Fabrik; räumt am Ende jeden gestarteten Prozess ab."""
    f = Fabrik(tmp=tmp_path)
    try:
        yield f
    finally:
        f.raeume_ab()


@pytest.fixture
def seite(page):
    """Die Playwright-Seite mit zählender ``EventSource`` und Anfragen-Protokoll.

    ``add_init_script`` läuft **vor** jedem Skript der Seite — auch vor
    ``_EVENTS_JS``. Der Zähler steht damit, bevor die erste Verbindung
    aufgebaut wird; ein später gesetzter Haken sähe sie nicht. Er überlebt
    dabei jede Navigation: das Skript wird pro Dokument neu eingespielt, die
    Zählung beginnt also mit jeder Seite bei null.
    """
    page.add_init_script(_ZAEHLER_JS)
    return page
