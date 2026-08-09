"""Der Upgrade-Hinweis vergleicht gegen den **laufenden** Stand (m.rau/bibi#81).

``deploy.update_status()`` nannte ein Feld ``running``, das die *installierte*
Version enthielt: verglichen wurde ``pyproject.toml`` gegen ``direct_url.json``
im venv — also gegen das, was auf der Platte liegt, nicht gegen das, was der
Prozess geladen hat.

Solange beides zusammenfaellt, ist das Ergebnis richtig. Auseinander laufen die
zwei genau dann, wenn jemand zwischen Installation und Neustart steht — **und
das ist per Definition der Sitzungs-Knoten**, fuer den ``upgrade_notice`` ueber-
haupt nur existiert. Live am 2026-08-08: nach einem ``uv sync`` ohne Neustart
erlosch ``UPDATE — exit!``, waehrend der alte Prozess weiterlief und weiterhin
15 Verbindungen pro Minute aufbaute — das Muster des in `v0.7.7` behobenen
Fehlers.

**Die Namensgebung ist der Kern des Fehlers, nicht sein Nebeneffekt.** Ein Feld,
das ``running`` heisst und ``installed`` enthaelt, ist genau so lange harmlos,
wie niemand den Unterschied braucht.

Die Lokalitaet bleibt gewahrt: die laufende Version steht in der Portdatei, die
``pending()`` ohnehin liest, um die Sitzungs-Herkunft zu bestimmen. Kein HTTP,
keine Host-Abhaengigkeit — nur ein abgelegter Wert mehr, geschrieben von dem
einen Prozess, der ihn sicher kennt.
"""

from __future__ import annotations

import pytest

from bibi import upgrade_notice
from bibi.daemon import deploy, portfile


class _Info:
    """Ein installierter Stand, wie ``engine_info()`` ihn liefert."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        self.editable = False
        self.local = False

    def label(self) -> str:
        return self.ref


@pytest.fixture
def gepinnt(team_repo):
    """Das Repo pinnt ``v0.7.8``."""
    (team_repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\n'
        'dependencies = ["bibi[daemon] @ git+http://x/bibi.git@v0.7.8"]\n',
        encoding="utf-8")
    return team_repo


def test_the_fields_say_what_they_contain(gepinnt):
    """``installed`` heisst jetzt so — vorher hiess dasselbe ``running``."""
    st = deploy.update_status(gepinnt, _Info("v0.7.7"))
    assert st["expected"] == "v0.7.8"
    assert st["installed"] == "v0.7.7"


def test_an_updated_venv_with_an_old_process_still_asks(gepinnt, monkeypatch):
    """**Der Rot-Schritt von #81.**

    venv aktuell, Prozess alt — genau die Lage nach einem ``uv sync`` ohne
    Neustart. Vorher verschwand die Aufforderung hier, und der Deploy-Weg des
    Sitzungs-Knotens endete still: ``upgrade_notice`` ist fuer ihn der einzige,
    ``release.sh`` startet ihn ausdruecklich nicht.
    """
    monkeypatch.setattr(portfile, "read",
                        lambda root=None: {"port": 1, "pid": 2, "session": True,
                                           "engine": "v0.7.7"})
    st = upgrade_notice.pending(gepinnt, _Info("v0.7.8"))
    assert st is not None, (
        "venv aktuell, Prozess alt — die Aufforderung faellt aus, und mit ihr "
        "der einzige Deploy-Weg dieses Knotens (#81)")
    assert st["running"] == "v0.7.7"
    assert st["expected"] == "v0.7.8"


def test_a_node_where_both_are_current_stays_quiet(gepinnt, monkeypatch):
    """Die Gegenprobe. Ohne sie waere der Test oben auch dann gruen, wenn die
    Aufforderung **immer** erschiene — und eine, die nie weggeht, wird nach dem
    zweiten Mal ueberlesen."""
    monkeypatch.setattr(portfile, "read",
                        lambda root=None: {"port": 1, "pid": 2, "session": True,
                                           "engine": "v0.7.8"})
    assert upgrade_notice.pending(gepinnt, _Info("v0.7.8")) is None


def test_an_old_venv_still_asks_as_before(gepinnt, monkeypatch):
    """Der Bestandsfall bleibt: venv alt, Prozess alt. Er war nie kaputt und
    darf es durch die Trennung nicht werden."""
    monkeypatch.setattr(portfile, "read",
                        lambda root=None: {"port": 1, "pid": 2, "session": True,
                                           "engine": "v0.7.7"})
    st = upgrade_notice.pending(gepinnt, _Info("v0.7.7"))
    assert st is not None and st["running"] == "v0.7.7"


def test_without_a_recorded_engine_the_venv_decides(gepinnt, monkeypatch):
    """Ein Daemon, der die Angabe nicht abgelegt hat, faellt auf das venv
    zurueck — dasselbe Verhalten wie vor dieser Aenderung. Unbekannt ist kein
    Grund, den Hinweis ganz aufzugeben."""
    monkeypatch.setattr(portfile, "read",
                        lambda root=None: {"port": 1, "pid": 2, "session": True})
    assert upgrade_notice.pending(gepinnt, _Info("v0.7.7")) is not None
    assert upgrade_notice.pending(gepinnt, _Info("v0.7.8")) is None


def test_the_portfile_records_the_running_engine(team_repo):
    """Und der Daemon muss die Angabe auch **ablegen**.

    Ohne sie bliebe die ganze Kette gruen, waehrend die Portdatei nichts
    traegt — gebaut waere dann ein Vergleich ohne Vergleichswert."""
    portfile.write(1234, session=True, root=team_repo)
    eintrag = portfile.read(team_repo)
    assert eintrag is not None
    assert eintrag.get("engine"), (
        "die Portdatei haelt nicht fest, welchen Stand dieser Prozess geladen "
        "hat — dann kann ihn niemand mit dem Soll vergleichen (#81)")
