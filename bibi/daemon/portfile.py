"""Der **tatsächliche** Bind-Port eines laufenden Daemons (m.rau/bibi#45).

``config.daemon_port()`` löst ausschließlich aus *Konfigurations*-Quellen auf —
``BIBI_DAEMON_PORT``, der Port aus ``BIBI_SCHEDULER_URL``, sonst der Default
8769. Ein zur Laufzeit gewählter Port steht in keiner davon, und genau den
brauchen andere Prozesse: ``bibi-ctrl status`` im zweiten Terminal, die
Statusline, der Browser, der Heartbeat, der ihn an den Host meldet.

Deshalb legt der Daemon ihn ab, sobald er ihn kennt, und räumt ihn beim Beenden
weg. Der Ablageort ist ``data/`` (gitignored, repo-scoped) — dieselbe Ebene wie
Job-DB und Aktivitätslog, und damit automatisch je Checkout getrennt.

**Warum die Datei einen PID trägt.** Ein ``kill -9`` oder ein Stromausfall
lässt sie stehen; eine Portnummer ohne Lebendigkeitsprüfung wäre danach eine
Falle, die auf einen Port zeigt, an dem niemand mehr lauscht. Die Prüfung ist
dieselbe Invariante, die #38 für Jobs eingeführt hat, nur billiger: ein
``os.kill(pid, 0)`` statt eines ``ps``-Aufrufs. Auf einen Vergleich der
Prozess-Startzeit (PID-Recycling, s. ``job_db.proc_started_at()``) wird hier
bewusst verzichtet — ``daemon_port()`` läuft u. a. in der Statusline und damit
oft, ein Subprozess je Aufruf wäre der falsche Preis. Der Schaden im
Recycling-Fall ist eine fehlgehende HTTP-Verbindung, kein Datenverlust.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from bibi import repo

#: Dateiname unter ``data/``. JSON statt roher Zahl, damit PID und Rollen
#: danebenpassen, ohne dass ein Leser ein Format raten muss.
FILENAME = "daemon-port.json"


def port_file(root: Path | None = None) -> Path | None:
    """Pfad der Ablage, oder ``None`` außerhalb eines git-Repos.

    ``None`` statt Abbruch: ``daemon_port()`` darf laut ``config``-Modul-
    Docstring ausdrücklich auch ohne Repo laufen (``bibi-ctrl status``/``init``).
    """
    root = root or repo.root_or_none()
    return None if root is None else root / "data" / FILENAME


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existiert, gehört nur einem anderen User
    except OSError:
        return False
    return True


def read(root: Path | None = None) -> dict | None:
    """Eintrag des **laufenden** Daemons, oder ``None``.

    ``None`` heißt in jedem Fall dasselbe für den Aufrufer — „hier läuft nichts,
    das du finden könntest": keine Datei, kaputtes JSON, kein Port darin, oder
    ein Eintrag, dessen Prozess nicht mehr lebt. Ein verwaister Eintrag wird
    dabei nicht gelöscht; Aufräumen ist Sache dessen, der ihn geschrieben hat
    (``clear()``), und ein Leser soll nicht schreiben dürfen.
    """
    p = port_file(root)
    if p is None or not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    pid = data.get("pid")
    if not isinstance(port, int) or not isinstance(pid, int):
        return None
    if not _alive(pid):
        return None
    # Vor #59 geschriebene Einträge tragen kein ``session``. Sie als Unit zu
    # lesen wäre bequem und im Sitzungsfall falsch — am 2026-07-31 live an
    # einem laufenden Sitzungs-Daemon beobachtet, der sich dadurch als Unit
    # ausgab. ``None`` heißt hier ausdrücklich *unbekannt*, nicht *keine
    # Sitzung*: eine Auskunft, die falsch sein kann, ist schlechter als keine.
    # Verschwindet von selbst, sobald der Daemon einmal neu startet.
    data.setdefault("session", None)
    return data


def read_port(root: Path | None = None) -> int | None:
    """Nur die Portnummer des laufenden Daemons — die Form, die
    ``config.daemon_port()`` braucht."""
    entry = read(root)
    return entry["port"] if entry else None


def write(port: int, *, host: str | None = None, roles: str | None = None,
          session: bool = False, root: Path | None = None) -> Path | None:
    """Den tatsächlichen Bind-Port ablegen. ``None``, wenn **nicht geschrieben**
    wurde: kein Repo, oder ein lebender Fremdeintrag steht im Weg.

    Atomar über eine Temp-Datei, damit ein gleichzeitiger Leser nie einen halb
    geschriebenen Eintrag sieht — dieselbe Vorsicht wie bei ``config.write_env()``.

    ``session`` hält fest, ob dieser Daemon einer Sitzung gehört (#46) oder
    einem Supervisor. Es ist ein abgelegter Wert und keine Heuristik, weil nur
    der startende Prozess es sicher weiß — von außen sind die beiden Fälle
    nicht unterscheidbar, und der Unterschied entscheidet, ob ein Neustart den
    Daemon zurückbringt oder eine Sitzung ohne Dashboard hinterlässt (#59).

    **Ein fremder, lebender Eintrag wird nicht überschrieben** (m.rau/bibi#119).
    ``clear()`` prüft die PID seit jeher — und war dadurch wirkungslos: hatte
    ein zweiter Daemon erst überschrieben, stand dort die *eigene* PID, und sein
    Ende räumte den Eintrag des noch laufenden ersten weg. Der Schutz gehört
    deshalb auf beide Seiten. Ein **toter** Eintrag darf weiterhin überschrieben
    werden, sonst schlüge ein ``kill -9`` in eine Sperre um, die nur von Hand zu
    lösen ist.

    Dass es überhaupt zwei Daemons gibt, verhindert ``daemon_cmd.run()``
    (m.rau/bibi#155). Diese Prüfung ist die zweite Verteidigungslinie: sie greift
    auch dort, wo nicht der Startpfad schreibt — Testläufe, Fremdstarts.
    """
    p = port_file(root)
    if p is None:
        return None
    live = read(root)
    if live is not None and live.get("pid") != os.getpid():
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"port": int(port), "pid": os.getpid(), "host": host,
               "roles": roles, "session": bool(session), "started_at": time.time()}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def clear(root: Path | None = None) -> None:
    """Eintrag entfernen — aber nur den **eigenen**.

    Die PID-Prüfung ist kein Zierrat: schreibt ein zweiter Daemon denselben
    Pfad (zwei Instanzen auf einem Checkout, was der Sitzungs-Zähler aus #46
    gerade verhindern soll), würde ein bedingungsloses ``unlink`` beim Beenden
    des ersten den Eintrag des noch laufenden zweiten wegräumen.
    """
    p = port_file(root)
    if p is None or not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("pid") not in (None, os.getpid()):
            return
        p.unlink()
    except (OSError, ValueError):
        pass


def bind_free(host: str = "127.0.0.1") -> tuple[socket.socket, int]:
    """Einen freien Port belegen und **den Socket behalten**.

    Der naheliegende Weg — Port 0 binden, Nummer lesen, Socket schließen, den
    Port an uvicorn weitergeben — hat ein Zeitfenster, in dem ein anderer
    Prozess genau diesen Port wegschnappt. Das Fenster ist klein, aber es
    verschwindet ganz, wenn der Socket offen bleibt und uvicorn ihn übernimmt
    (``Server.run(sockets=[…])``). Der Aufwand dafür sind zwei Zeilen mehr, und
    der Preis ist, dass ``uvicorn.run()`` durch ``Config``+``Server`` ersetzt
    wird — was ohnehin nur dessen Innenleben ist.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    return sock, sock.getsockname()[1]
