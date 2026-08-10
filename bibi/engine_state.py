"""Welche Engine dieser Knoten meint — ``expected``, ``installed``, ``running``
(m.rau/bibi#125).

Drei Größen, drei Namen, **eine** Stelle, an der sie entstehen:

======================  ================================  =====================
``expected``            was ``pyproject.toml`` verlangt   Pin
``installed``           was im venv liegt                 ``direct_url.json``
``running``             was der Prozess geladen hat       Portdatei (Startstand)
======================  ================================  =====================

**Es gibt dieses Modul, weil die Trennung dreimal an einem Aufrufer gemacht
wurde statt an der Wurzel.** m.rau/bibi#81 hat sie am 2026-08-09 vorgeschlagen
und den Nodes-Screen namentlich als zweite Fundstelle genannt; gewählt wurde der
billigere Weg. m.rau/bibi#102 fand daraufhin, dass #81 „nur dem Namen nach
umgesetzt" war — und behauptete seinerseits, beide Stellen erledigt zu haben,
während der Heartbeat unverändert die Platte meldete. Live am 2026-08-10 sagte
derselbe Knoten über sich selbst ``v0.7.17`` und in der Registry des Schedulers
``v0.7.18``.

**Der Unterschied zwischen ``installed`` und ``running`` ist genau so lange
harmlos, wie niemand ihn braucht.** Beide fallen auseinander, sobald zwischen
``uv sync`` und Neustart jemand hinsieht — also in dem Fenster, in dem man
wissen will, ob ein Rollout angekommen ist. Nur der startende Prozess weiß
sicher, welchen Stand er geladen hat, deshalb legt er ihn beim Start in der
Portdatei ab (``portfile.write(engine=…)``); von außen ist ausschließlich das
venv sichtbar.

**Durchweg defensiv (§2.7).** Diese Auskunft hängt im Heartbeat, in der
Statusleiste und im Sitzungsstart — keiner von ihnen darf an ihr scheitern.
Fehlt die Portdatei oder ihr Feld, bleibt es beim venv: unbekannt ist kein
Grund, die Auskunft ganz aufzugeben.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bibi.engine_info import EngineInfo, engine_info


@dataclass(frozen=True, slots=True)
class EngineState:
    """Die drei Größen, dazu die Rohdaten für Aufrufer, die mehr brauchen.

    ``info`` bleibt sichtbar, weil ``deploy.update_status()`` sein Urteil aus
    ``editable``/``local``/``ref`` bildet und nicht aus der fertigen
    Bezeichnung. Wer nur die Bezeichnung braucht, nimmt die Felder.
    """

    expected: str | None = None
    installed: str | None = None
    running: str | None = None
    info: EngineInfo = EngineInfo()

    def tree_status(self) -> str | None:
        """Der Arbeitsbaum des Engine-Checkouts (m.rau/bibi#67).

        **Bewusst eine Methode und kein Feld:** dahinter steht ein
        git-Aufruf, und ``engine_state()`` läuft im 15-Sekunden-Takt des
        Heartbeats. Wer den Baum nicht braucht, soll ihn nicht bezahlen.
        """
        return self.info.tree_status()


def installed_label(info: EngineInfo | None = None) -> str | None:
    """Was im venv liegt — **ohne** die Portdatei zu befragen.

    Die eine Stelle, an der ein Startstand überhaupt entsteht: ``portfile``
    ruft sie beim Schreiben. Ein Rückgriff auf ``running`` wäre hier zirkulär —
    der Wert, der gleich abgelegt wird, kann nicht aus der Ablage kommen.
    """
    try:
        info = engine_info() if info is None else info
        return info.label()
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        return None


def _startstand(root: Path | None = None) -> str | None:
    """Der abgelegte Stand des laufenden Daemons, oder ``None``.

    ``None`` heißt *unbekannt*, nicht *keiner*: ein Daemon, der vor dieser
    Ablage startete, und ein CLI-Aufruf ohne laufenden Daemon sehen von hier
    aus gleich aus. Beide Male entscheidet das venv.
    """
    try:
        from bibi.daemon import portfile
        return (portfile.read(root) or {}).get("engine")
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        return None


def _pin(root: Path | None = None) -> str | None:
    """Der Soll-Stand aus ``pyproject.toml``.

    Bleibt in ``deploy.current_ref()``: dieselbe Zeile trägt auch die
    Herkunfts-URL, und ``set_expected_version()`` schreibt sie zurück. Für
    ``expected`` gab es nie zwei Fassungen — die Doppelung, gegen die dieses
    Modul antritt, betraf ausschließlich die anderen beiden Größen.
    """
    try:
        from bibi.daemon import deploy
        return deploy.current_ref(root)
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        return None


def engine_state(root: Path | None = None,
                 info: EngineInfo | None = None) -> EngineState:
    """Die drei Größen dieses Knotens, in einem Zug erhoben.

    ``info`` ist stellbar, damit ein Test die Ist-Seite setzen kann, ohne die
    echte Installation zu befragen — dieselbe Erwägung wie bei
    ``portfile.write(engine=…)``.

    **Was das kostet:** ein ``importlib.metadata``-Zugriff, zwei kleine Dateien
    (Portdatei, ``pyproject.toml``). Kein git, kein HTTP, keine
    Host-Abhängigkeit — die Lokalität, auf der ``deploy.update_status()`` seit
    m.rau/bibi#43 besteht, bleibt unangetastet.
    """
    try:
        info = engine_info() if info is None else info
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        info = EngineInfo()
    installed = installed_label(info)
    return EngineState(expected=_pin(root), installed=installed,
                       running=_startstand(root) or installed, info=info)
