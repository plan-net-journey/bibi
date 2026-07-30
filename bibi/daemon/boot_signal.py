"""Boot-Signale: was beim nächsten Start VOR dem Server passieren soll
(m.rau/bibi#39).

Nur noch **ein** Signal, und das aus einem harten Grund: ``reset`` wirft das
venv weg, und **ein Prozess kann sein eigenes venv nicht unter sich
austauschen** — er läuft daraus. Das muss also in einer Phase passieren, die
nicht der finale Prozess ist:

1. Der Endpunkt schreibt das Signal (überlebt den Prozess, weil Datei) und
   beendet sich.
2. Der Supervisor startet neu (``Restart=always``/``RestartSec=3``, launchd
   ``KeepAlive``). Diese Phase liest das Signal, wirft das venv weg, entfernt
   das Signal und beendet sich wieder, **ohne den Server zu starten**.
3. Der Supervisor startet erneut. ``uv run`` baut das venv gegen die Lock neu,
   der Server läuft.

**Der Pull gehört ausdrücklich NICHT hierher** (Einwand von m.rau, 2026-07-30).
Er lief in einem früheren Entwurf ebenfalls als Boot-Signal und kostete damit
einen Neustart mehr als nötig: liegt die neue Lock schon vor dem *ersten*
Neustart im Checkout, synct ``uv run`` sofort dagegen und ein Durchlauf genügt.
Der Pull passiert deshalb synchron im Request (s. ``app.py::daemon_restart``) —
mit zwei weiteren Vorteilen: er läuft dort unter dem ``sync_lock`` (keine
Kollision mit dem Synchronizer), und ein Fehlschlag kann sofort als HTTP-Fehler
gemeldet werden statt nur im Log zu landen.

**Warum Phase 2 den Server nicht startet:** Sie ist die einzige Stelle, an der
sich der Prozess ohne laufende Dienste beenden kann. Würde erst der Server
hochfahren, müssten Synchronizer, Worker und Heartbeat gleich wieder gestoppt
werden — mit allen Halbzuständen, die ein Shutdown mitten im Anlauf mitbringt.

**Fehler löschen das Signal trotzdem.** Es darf keine Neustart-Schleife
entstehen; die wäre der teuerste Fehlerfall, weil sie den Knoten dauerhaft aus
dem Betrieb nimmt. Sichtbar wird ein Fehlschlag über ``activity``.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

from bibi import repo
from bibi.daemon import activity

log = logging.getLogger("bibi.boot")

#: Erkannte Signale. ``restart`` braucht keins (ein Neustart ohne Vorarbeit ist
#: einfach ein Prozessende, das der Supervisor auffängt) und ``deployment``
#: auch nicht mehr — der Pull läuft im Request, s. Modul-Docstring.
KINDS = ("reset",)


def _dir(root: Path | None = None) -> Path:
    return (root or repo.root()) / "data" / "boot"


def path(kind: str, root: Path | None = None) -> Path:
    return _dir(root) / kind


def request(kind: str, root: Path | None = None) -> None:
    """Signal für den nächsten Start hinterlegen. Idempotent."""
    if kind not in KINDS:
        raise ValueError(f"unbekanntes Boot-Signal: {kind}")
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path(kind, root).touch()


def pending(root: Path | None = None) -> list[str]:
    d = _dir(root)
    return [k for k in KINDS if (d / k).exists()]


def clear(kind: str, root: Path | None = None) -> None:
    path(kind, root).unlink(missing_ok=True)


def apply_and_clear(root: Path | None = None) -> bool:
    """Anliegende Signale abarbeiten. ``True`` ⇒ dieser Prozess soll sich
    beenden, damit der Supervisor ihn mit dem neuen Stand neu startet.

    Reihenfolge und Löschzeitpunkt sind beide Absicht: **zuerst** löschen, dann
    arbeiten. Ein Absturz mitten in der Arbeit hinterlässt so kein Signal, das
    beim nächsten Start dieselbe Arbeit erneut anstößt — eine Neustart-Schleife
    wäre der teuerste denkbare Fehlerfall, weil sie den Knoten dauerhaft aus
    dem Betrieb nimmt.
    """
    root = root or repo.root()
    kinds = pending(root)
    if not kinds:
        return False

    for k in kinds:
        clear(k, root)

    if "reset" in kinds:
        venv = root / ".venv"
        try:
            if venv.exists():
                shutil.rmtree(venv, ignore_errors=True)
            activity.emit(log, logging.INFO, "boot.reset",
                          "Reset: venv entfernt, wird beim Start neu gebaut",
                          role="daemon")
        except Exception as exc:  # noqa: BLE001
            activity.emit(log, logging.WARNING, "boot.reset",
                          "Reset: venv konnte nicht entfernt werden",
                          role="daemon", reason=str(exc))

    activity.emit(log, logging.INFO, "boot.restart",
                  "Boot-Signal abgearbeitet — Prozess endet für den Neustart",
                  role="daemon", kinds=",".join(kinds))
    # stdout, weil der Vordergrund-Startschirm der Live-Tail ist: wer `daemon
    # run` von Hand aufruft, soll sehen, warum der Prozess sofort zurückkehrt.
    print(f"boot: {', '.join(kinds)} abgearbeitet — Neustart durch den Supervisor",
          file=sys.stderr)
    return True
