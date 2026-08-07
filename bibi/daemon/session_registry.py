"""Wer arbeitet gerade mit diesem Repo? (m.rau/bibi#46)

Mehrere interaktive Sitzungen teilen sich **einen** Daemon, und er soll genau
dann herunterfahren, wenn die **letzte** von ihnen endet — nicht wenn die erste
geht. Der Einwand, aus dem das entstand (m.rau, 2026-07-31): *„Was ist, wenn ich
ein zweites `bcc` starte und das erste beende? Dann fehlt dem zweiten der
Daemon."* Die zuerst vorgeschlagene Regel „der Befehl räumt weg, was er selbst
gestartet hat" beschreibt nur den Ein-Sitzungs-Fall.

**Warum nicht einfach ein Daemon je Sitzung.** Naheliegend, scheidet aber aus:
der ``sync_lock``, der Pull, Push und Merge-back gegeneinander absichert, ist
ein ``threading.Lock()`` (``bibi/ctrl/daemon_cmd.py``) und damit prozess-lokal.
Zwei Daemons auf demselben Repo würden gleichzeitig ins selbe
Arbeitsverzeichnis pullen und pushen. Die SQLite hielte das aus (WAL,
``busy_timeout``), die git-Ebene nicht.

**Kein neues Konzept, sondern ein vorhandenes.** Seit #38 läuft im ``Sweeper``
eine periodische PID-Prüfung; diese Registry funktioniert genauso. Eine
abgestürzte Sitzung dekrementiert nie — ihre PID lebt aber auch nicht mehr, also
zählt sie nicht mit. Und ein verwaister Eintrag nach einem harten Neustart ist
harmlos, weil die Prüfung an der PID hängt und nicht an der Datei. Es ist exakt
die Invariante, die #38 für Jobs eingeführt hat, angewandt auf Sitzungen.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from bibi import repo
from bibi.daemon.portfile import _alive

#: Unterverzeichnis unter ``data/`` (gitignored). Eine Datei je Sitzung statt
#: einer gemeinsamen Liste: zwei Sitzungen, die gleichzeitig starten, schreiben
#: dann nie in dieselbe Datei, und es braucht kein Lock über Prozessgrenzen.
DIRNAME = "sessions"


def sessions_dir(root: Path | None = None) -> Path | None:
    """Ablageort, oder ``None`` außerhalb eines git-Repos."""
    root = root or repo.root_or_none()
    return None if root is None else root / "data" / DIRNAME


def _entry_path(pid: int, root: Path | None = None) -> Path | None:
    d = sessions_dir(root)
    return None if d is None else d / f"{pid}.json"


def revision(root: Path | None = None) -> float:
    """Billiger Änderungsanzeiger für das Registry-Verzeichnis (m.rau/bibi#50).

    Ein einzelner ``stat``: die mtime des Verzeichnisses springt bei jedem
    :func:`register` und :func:`unregister`, weil beide einen Eintrag anlegen
    oder löschen. ``0.0``, wenn es (noch) keins gibt.

    **Wofür das da ist.** Der Sweeper drosselt seine Sitzungszählung auf
    ``pid_check_interval`` — zu Recht, denn :func:`live_pids` prüft jede PID
    einzeln und liefe sonst bei jedem Tick. Die Drosselung kostete aber genau
    das, was sie sparen sollte: nach dem letzten ``exit`` lebte der Daemon bis
    zu 45 Sekunden weiter, und ein sofort nachgestartetes ``bibi`` fand seine
    Portdatei und hängte sich an. Der alte Code blieb im Speicher, und die
    Upgrade-Aufforderung erschien beim nächsten Start unverändert — wer ihrem
    Rat *„exit, dann bibi"* folgte, kam zuverlässig dort an, wo er losging.

    Das Sitzungsende ist ein **bekannter** Zeitpunkt, kein zu entdeckender.
    Diese Funktion macht ihn sichtbar, ohne die teure Zählung vorzuziehen: der
    Sweeper hebt seine Drosselung auf, wenn sich hier etwas gerührt hat, und
    behält sie sonst.
    """
    d = sessions_dir(root)
    if d is None:
        return 0.0
    try:
        return d.stat().st_mtime
    except OSError:
        return 0.0


def register(pid: int | None = None, *, label: str | None = None,
             root: Path | None = None) -> Path | None:
    """Diese Sitzung anmelden. ``None``, wenn kein Repo da ist.

    Der Dateiname **ist** die PID — damit kann eine zweite Sitzung nie den
    Eintrag der ersten überschreiben, und das Aufräumen braucht keinen Index.
    """
    pid = os.getpid() if pid is None else pid
    p = _entry_path(pid, root)
    if p is None:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": pid, "label": label, "started_at": time.time()}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def unregister(pid: int | None = None, root: Path | None = None) -> bool:
    """Diese Sitzung abmelden. ``True``, wenn es einen Eintrag gab.

    Der reguläre Weg. Bleibt er aus (Absturz, ``kill -9``, Stromausfall), fängt
    :func:`live_pids` das auf — deshalb ist ein verpasstes ``unregister`` kein
    Fehlerfall, der behandelt werden müsste.
    """
    pid = os.getpid() if pid is None else pid
    p = _entry_path(pid, root)
    if p is None or not p.exists():
        return False
    try:
        p.unlink()
    except OSError:
        return False
    return True


def live_pids(root: Path | None = None, *, prune: bool = True) -> list[int]:
    """PIDs der Sitzungen, deren Prozess **noch lebt**.

    ``prune``: tote Einträge werden dabei gelöscht. Das ist kein Nebenzweck,
    sondern der Grund, warum ein naiver Zähler nicht reicht — wer abstürzt,
    dekrementiert nichts, und ohne Aufräumen bliebe der Ordner voll von
    Karteileichen, die niemandem mehr gehören.
    """
    d = sessions_dir(root)
    if d is None or not d.is_dir():
        return []
    alive: list[int] = []
    for entry in sorted(d.glob("*.json")):
        try:
            pid = int(entry.stem)
        except ValueError:
            continue
        if _alive(pid):
            alive.append(pid)
        elif prune:
            try:
                entry.unlink()
            except OSError:
                pass
    return alive


def count(root: Path | None = None) -> int:
    """Wie viele Sitzungen leben gerade in diesem Repo."""
    return len(live_pids(root))
