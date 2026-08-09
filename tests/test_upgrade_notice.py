"""Upgrade-Aufforderung für Sitzungs-Knoten (m.rau/bibi#94).

Ein Knoten mit Supervisor bekommt seinen Neustart per Knopf. Ein Sitzungs-Knoten
hat keinen — für ihn endet der Deploy-Weg beim Menschen, und wenn ihm niemand
sagt, dass ein Upgrade wartet, bleibt er beliebig lange auf dem alten Stand.

Getestet wird beides getrennt: **ob** eine Aufforderung fällig ist
(``pending()``) und **wie** sie aussieht (``banner()``/``segment()``). Das
Urteil ist die Engine-Seite und der Grund für den Rot-Schritt; die Darstellung
hängt daran, nicht umgekehrt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import upgrade_notice
from bibi.daemon import portfile
from bibi.engine_info import EngineInfo


def _pin(root: Path, ref: str) -> None:
    """``pyproject.toml`` des Team-Repos auf ``ref`` pinnen — die Soll-Seite.

    Dieselbe Zeile, die ``deploy.current_ref()`` liest; sie ist im Team-Repo die
    einzige Stelle, die den erwarteten Stand trägt.
    """
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "team"\n'
        'dependencies = ["bibi[daemon] @ git+http://example/bibi.git@'
        f'{ref}"]\n',
        encoding="utf-8")


def _running(ref: str) -> EngineInfo:
    """Die Ist-Seite: eine per VCS-URL installierte Engine auf ``ref``."""
    return EngineInfo(version=ref.lstrip("v"), ref=ref, commit="a" * 40,
                      url="git+http://example/bibi.git")


# ── Wann eine Aufforderung fällig ist ───────────────────────────────────────


def test_pending_on_session_node_behind_pin(team_repo: Path):
    """Der Fall, um den es geht: Sitzungs-Daemon, gepinnt neuer als laufend.

    ``engine=`` in der Portdatei ist seit m.rau/bibi#81 die **laufende** Seite
    des Vergleichs, ``info`` bleibt die installierte. Solange beide zusammen-
    fallen — wie hier — ändert das am Ergebnis nichts; gestellt werden müssen
    jetzt aber beide, sonst mischt sich die echte Installation des Testrechners
    ein. Der Fall, in dem sie auseinanderlaufen, steht in
    ``test_upgrade_notice_running.py``."""
    portfile.write(9001, session=True, engine="v0.5.3")
    _pin(team_repo, "v0.6.0")
    st = upgrade_notice.pending(info=_running("v0.5.3"))
    assert st is not None
    assert st["expected"] == "v0.6.0"
    assert st["running"] == "v0.5.3"


def test_silent_when_current(team_repo: Path):
    """Eine Sitzung auf aktuellem Stand zeigt nichts — sonst nervt es bei
    jedem Start, und ein Hinweis, der immer da ist, wird nicht gelesen."""
    portfile.write(9001, session=True, engine="v0.6.0")
    _pin(team_repo, "v0.6.0")
    assert upgrade_notice.pending(info=_running("v0.6.0")) is None


def test_silent_on_supervised_node(team_repo: Path):
    """Ein Knoten **mit** Supervisor bleibt unberührt: dort ist der
    Restart-Knopf der richtige Weg, und eine Aufforderung an den Menschen wäre
    die falsche Auskunft."""
    portfile.write(9001, session=False)
    _pin(team_repo, "v0.6.0")
    assert upgrade_notice.pending(info=_running("v0.5.3")) is None


def test_silent_when_no_daemon_runs(team_repo: Path):
    """Ohne laufenden Daemon ist die Herkunft unbekannt. Dann lieber schweigen
    als eine Sitzung behaupten — ein falscher Aufruf schickt jemanden los,
    etwas neu zu starten, das gar nicht läuft."""
    _pin(team_repo, "v0.6.0")
    assert upgrade_notice.pending(info=_running("v0.5.3")) is None


def test_silent_on_daemon_from_before_the_session_flag(team_repo: Path):
    """``session: None`` heißt „vor #59 gestartet", nicht „Sitzung". Derselbe
    Grund wie oben: unbekannt ist kein Ja."""
    portfile.write(9001, session=True)
    p = portfile.port_file()
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    del data["session"]
    p.write_text(json.dumps(data), encoding="utf-8")
    assert upgrade_notice.pending(info=_running("v0.5.3")) is None


def test_silent_for_editable_install(team_repo: Path):
    """Ein Arbeits-Checkout ist kein Rückstand, sondern eine Absicht — und ein
    Neustart holt dort keinen gepinnten Stand."""
    portfile.write(9001, session=True)
    _pin(team_repo, "v0.6.0")
    info = EngineInfo(version="0.5.3", editable=True, url="file:///tmp/bibi")
    assert upgrade_notice.pending(info=info) is None


def test_silent_on_branch_pin(team_repo: Path):
    """Zeigt das Pinning auf einen Branch, weiß hier lokal niemand, ob der
    weitergewandert ist. ``deploy.update_status()`` sagt dann ``branch``, und
    daraus darf keine Aufforderung werden."""
    portfile.write(9001, session=True)
    _pin(team_repo, "dev")
    assert upgrade_notice.pending(info=_running("v0.5.3")) is None


def test_pending_survives_a_broken_pyproject(team_repo: Path):
    """Nie eine Exception: die Aufforderung hängt in der Statusleiste und im
    Sitzungsstart, und beide dürfen an ihr nicht scheitern."""
    portfile.write(9001, session=True)
    (team_repo / "pyproject.toml").write_text("kein toml [[[", encoding="utf-8")
    assert upgrade_notice.pending(info=_running("v0.5.3")) is None


# ── Wie sie aussieht ────────────────────────────────────────────────────────


def test_banner_names_the_way_not_only_the_state():
    """Die Aufforderung nennt den Weg, nicht nur den Zustand — ein Hinweis,
    der einen Zustand meldet und offenlässt, was zu tun ist, verschiebt die
    Arbeit nur."""
    text = upgrade_notice.banner({"expected": "v0.6.0", "running": "v0.5.3"})
    assert "v0.6.0" in text and "v0.5.3" in text
    assert "bibi" in text
    # Der Weg zurück steht drin, nicht nur die Feststellung.
    assert "exit" in text.lower()


def test_banner_is_multiline_and_set_off():
    """Motd-Form: mehrzeilig und abgesetzt. Eine einzelne Zeile zwischen den
    übrigen Startmeldungen wäre genau das Einreihen, das #94 ausschließt."""
    text = upgrade_notice.banner({"expected": "v0.6.0", "running": "v0.5.3"})
    assert len(text.splitlines()) >= 3


def test_segment_leads_the_statusline():
    """In der Leiste steht die Aufforderung **vorn** und invers — „hart über
    alles" heißt Vorrang vor jedem anderen Segment, nicht Einreihen."""
    seg = upgrade_notice.segment({"expected": "v0.6.0", "running": "v0.5.3"})
    assert "v0.6.0" in seg
    assert "\033[7m" in seg  # invers
    assert seg.endswith("\033[0m")


def test_segment_stays_short():
    """Die Leiste ist schmal und trägt schon Branch, Modell, ctx% und Case.
    Eine Aufforderung, die sie sprengt, verdrängt genau die Information, neben
    der sie stehen soll."""
    seg = upgrade_notice.segment({"expected": "v0.6.0", "running": "v0.5.3"})
    import re
    plain = re.sub(r"\033\[[0-9;]*m", "", seg)
    assert len(plain) <= 28, plain
