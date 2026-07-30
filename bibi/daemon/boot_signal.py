"""Boot-Signale: was beim nächsten Start VOR dem Server passieren soll
(m.rau/bibi#39, Design von m.rau 2026-07-30).

Der Kern ist eine Einsicht über Prozesse: **ein Prozess kann sein eigenes venv
nicht unter sich austauschen** — er läuft daraus. Ein Deploy braucht deshalb
zwei Phasen, und die erste darf nicht der finale Prozess sein:

1. Der Endpunkt schreibt ein Signal (überlebt den Prozess, weil Datei) und
   beendet sich.
2. Der Supervisor startet neu (``Restart=always``/``RestartSec=3``, launchd
   ``KeepAlive``). ``uv run`` synct dabei das venv gegen die Lock — noch die
   alte. Diese Phase liest das Signal, tut die vorbereitende Arbeit, entfernt
   das Signal und beendet sich wieder, **ohne den Server zu starten**.
3. Der Supervisor startet erneut. Jetzt synct ``uv run`` gegen die *neue* Lock,
   und der Server läuft mit dem neuen Stand.

Die Arbeit in Phase 2 ist bewusst winzig, weil ``uv run`` den schweren Teil
ohnehin erledigt:

- ``deployment`` = ``git pull``. Kein ``uv``-Aufruf nötig: sobald die neue Lock
  im Checkout liegt, zieht der nächste Start die Version von selbst.
- ``reset`` = venv wegwerfen. ``uv run`` baut es beim nächsten Start neu.

``reset`` impliziert ``deployment`` — ein frisch gebautes venv entsteht ohnehin
gegen die aktuelle Lock, also wird vorher gepullt.

**Warum Phase 2 den Server nicht startet:** Sie ist die einzige Stelle, an der
sich der Prozess ohne laufende Dienste beenden kann. Würde erst der Server
hochfahren, müssten Synchronizer, Worker und Heartbeat gleich wieder gestoppt
werden — mit allen Halbzuständen, die ein Shutdown mitten im Anlauf mitbringt.

**Fehler löschen das Signal trotzdem.** Ein fehlgeschlagener Pull darf keine
Neustart-Schleife erzeugen; der Knoten läuft dann auf dem alten Stand weiter,
was die richtige Landung ist. Damit das nicht wie ein erfolgreicher Deploy
aussieht, wird der Fehlschlag über ``activity`` gemeldet — der einzige Ort, an
dem er sonst unsichtbar bliebe.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

from bibi import repo
from bibi.daemon import activity

log = logging.getLogger("bibi.boot")

#: Erkannte Signale. ``restart`` braucht keins — ein Neustart ohne Vorarbeit
#: ist einfach ein Prozessende, das der Supervisor auffängt.
KINDS = ("deployment", "reset")


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


def _pull(root: Path) -> tuple[bool, str | None]:
    """Origin integrieren — derselbe Pfad, den der Synchronizer nutzt.

    ``guard_live_paths=False``: der Live-Edit-Guard schützt **unbeaufsichtigte**
    Schreibvorgänge davor, einem tippenden Menschen den Boden wegzuziehen. Ein
    angefordertes Deployment ist das Gegenteil davon — jemand hat es ausgelöst,
    und ein stiller Skip wäre hier genau der Fehler: der Knoten käme ohne den
    neuen Stand zurück und niemand wüsste warum.
    """
    from bibi import git_ops
    branch = git_ops.current_branch(root) or "trunk"
    return git_ops.integrate(branch, guard_live_paths=False)


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

    do_reset = "reset" in kinds
    # reset impliziert deployment: ein neu gebautes venv entsteht gegen die
    # aktuelle Lock, also vorher pullen.
    do_pull = do_reset or "deployment" in kinds

    if do_pull:
        try:
            ok, kind = _pull(root)
        except Exception as exc:  # noqa: BLE001 — nie den Neustart verlieren
            ok, kind = False, f"exception: {exc}"
        if ok:
            activity.emit(log, logging.INFO, "boot.pull",
                          "Deployment: origin integriert", role="daemon")
        else:
            # Der Knoten läuft auf dem alten Stand weiter — richtig so, aber es
            # darf nicht wie ein geglückter Deploy aussehen.
            activity.emit(log, logging.WARNING, "boot.pull",
                          "Deployment: Pull fehlgeschlagen — alter Stand bleibt",
                          role="daemon", reason=str(kind))

    if do_reset:
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
