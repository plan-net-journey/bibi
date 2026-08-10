"""Eine Quelle für ``expected`` / ``installed`` / ``running`` (`#125`).

**Dasselbe Ticket zum dritten Mal.** `#81` hat die Trennung am 2026-08-09
vorgeschlagen und den Nodes-Screen namentlich als zweite Fundstelle genannt;
gewählt wurde der billigere Weg. `#102` fand daraufhin, dass `#81` „nur dem
Namen nach umgesetzt" war — und sein Abschlusskommentar behauptet, **beide**
Stellen erledigt zu haben. Der Satz ist falsch: der Fix fasste ``deploy.py``
und ``node_info.py`` an, ``heartbeat.py`` nie.

Live am 2026-08-10 (m.rau)::

    Mac über sich selbst    (/-/status)   engine = v0.7.17   ← Prozess, richtig
    Registry beim Scheduler (/-/worker)   engine = v0.7.18   ← Platte, falsch

**Der Verhaltensnachweis konnte es nicht merken.** Er zeigte ``Mac v0.7.11 ·
behind``, und dort waren Platte und Prozess beide ``v0.7.11`` — zwei
Hypothesen, dieselbe Anzeige. Eine Prüfung, deren zwei Erklärungen dasselbe
Ergebnis haben, unterscheidet nichts. Deshalb stellt der erste Test die beiden
Stände **auseinander**, statt eine Übereinstimmung zu bestätigen.

Der zweite Test prüft nicht die Fundstelle, sondern die **Zusage**: kein Modul
außerhalb der Quelle bildet die Engine-Angabe selbst. Er hätte alle drei Fälle
gefangen — derselbe Schnitt wie bei der Link-Klasse (`#117`–`#119`) und den
Dauer-Ankern (`#123`).
"""

from __future__ import annotations

import ast
import pathlib
from pathlib import Path

import pytest

from bibi.daemon import portfile
from bibi.daemon.heartbeat import Heartbeat


class _FakeClient:
    """Nimmt jeden Heartbeat entgegen und merkt sich seine Felder."""

    def __init__(self) -> None:
        self.last_kwargs: dict = {}

    def register(self, worker: str, host: str, git_status: str | None = None,
                 **kw) -> dict | None:
        self.last_kwargs = kw
        return None


@pytest.fixture
def gestartet_auf_altem_stand(team_repo: Path) -> Path:
    """Ein Daemon, dessen Prozess älter ist als sein venv.

    Das ist die Lage jedes Knotens zwischen ``uv sync`` und Neustart — und
    damit per Definition die des Sitzungs-Knotens, für den die Unterscheidung
    überhaupt gebraucht wird.
    """
    portfile.write(12345, root=team_repo, session=True, engine="v0.7.17")
    return team_repo


def test_the_heartbeat_reports_the_running_engine_not_the_one_on_disk(
        gestartet_auf_altem_stand: Path):
    """`#125`: der Heartbeat meldete die Platte, nicht den Prozess.

    Der Testlauf selbst liefert die zweite Hälfte des Falles frei Haus: das
    venv trägt hier einen anderen Stand als die gestellte Portdatei, also
    fallen die beiden Größen auseinander, ohne dass irgendetwas gefälscht
    werden müsste.
    """
    client = _FakeClient()
    Heartbeat(client=client, repo_root=gestartet_auf_altem_stand)._beat()
    assert client.last_kwargs["engine"] == "v0.7.17", (
        "der Heartbeat meldet den Stand auf der Platte statt den geladenen — "
        "die Zeile des Knotens im Nodes-Screen behauptet ein Rollout, das erst "
        "beim Neustart wirksam wird (#125)")


def test_the_self_report_and_the_heartbeat_say_the_same_thing(
        gestartet_auf_altem_stand: Path):
    """`/-/status` und der Nodes-Screen dürfen sich nicht widersprechen.

    Sie sind die beiden Wege, auf denen dieselbe Auskunft nach außen kommt —
    einer lokal, einer über die Registry. Genau ihr Auseinanderlaufen war der
    Live-Befund.
    """
    from bibi.daemon import deploy, node_info
    from bibi.daemon import roles as roles_mod

    client = _FakeClient()
    Heartbeat(client=client, repo_root=gestartet_auf_altem_stand)._beat()
    eigen = node_info.self_entry(roles_mod.resolve({"controller"}))
    status = deploy.update_status(gestartet_auf_altem_stand)
    assert client.last_kwargs["engine"] == eigen["engine"] == status["running"]


#: Die eine Stelle, an der die Engine-Angabe entstehen darf.
_QUELLE = "bibi/engine_state.py"

#: Was ``engine_info()`` außerhalb der Quelle rufen darf.
#:
#: **Was hier steht, ist Schuld, keine Erlaubnis.** Die Liste ist leer; wer
#: etwas einträgt, begründet es im Commit.
_ERLAUBT: frozenset[str] = frozenset()

_PAKET = pathlib.Path(__file__).resolve().parent.parent / "bibi"


def _aufrufe_von(pfad: pathlib.Path, name: str) -> list[int]:
    """Zeilen, in denen ``name(...)`` gerufen wird — Aufrufe, keine Erwähnungen.

    Der Unterschied ist der Grund, warum hier ein AST steht und kein ``grep``:
    ``deploy.py`` und ``node_info.py`` nennen ``engine_info()`` in ihren
    Kommentaren, und ein Textfund hätte sie als Verstoß gemeldet, nachdem sie
    längst repariert sind. Dieselbe Verwechslung von *erwähnt* mit *benutzt*
    hat schon den ersten Entwurf des Erreichbarkeits-Wächters (`#100`) eine
    tote Route für lebendig halten lassen.
    """
    try:
        baum = ast.parse(pfad.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    treffer = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        f = knoten.func
        gerufen = (f.id if isinstance(f, ast.Name)
                   else f.attr if isinstance(f, ast.Attribute) else None)
        if gerufen == name:
            treffer.append(knoten.lineno)
    return treffer


def test_no_module_outside_the_source_builds_the_engine_label():
    """Die Zusage, nicht die Fundstelle (`#125`).

    Ein Wächter, der eine Fundstelle prüft, ist eine Buchhaltung; einer, der
    die Zusage prüft, ist ein Test. Dieser hier hätte `#81`, `#102` und `#125`
    gefangen — jedes Mal war der Fehler ein Aufrufer, der die Größe selbst
    bildete, statt sie zu lesen.
    """
    verstösse = []
    for pfad in sorted(_PAKET.rglob("*.py")):
        rel = pfad.relative_to(_PAKET.parent).as_posix()
        if rel == _QUELLE or rel in _ERLAUBT:
            continue
        for zeile in _aufrufe_von(pfad, "engine_info"):
            verstösse.append(f"{rel}:{zeile}")
    assert not verstösse, (
        "diese Stellen bilden die Engine-Angabe selbst, statt sie aus "
        f"{_QUELLE} zu lesen — genau so ist #81 dreimal danebengegangen: "
        + ", ".join(verstösse))
