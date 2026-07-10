"""Knoten-Konfiguration: ``~/.config/bibi/env`` (DESIGN §4.10).

Drei host-/team-private Parameter, die das Repo bewusst NICHT enthält:
``BIBI_SCHEDULER_URL``, ``BIBI_ROLE``, ``BIBI_REMOTE``. Geschrieben von
``bibi-ctrl init``, gelesen u. a. von ``bibi-ctrl status`` und (später)
``bibi-ctrl daemon install``.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

# Reihenfolge = Abfrage-/Schreibreihenfolge. Werte sind die Defaults für init.
KEYS: dict[str, str] = {
    "BIBI_SCHEDULER_URL": "http://localhost:8769",
    "BIBI_ROLE": "synchronizer",
    "BIBI_REMOTE": "",
    # Pfad/Name des claude-Binaries (claude-Jobs). Default "claude" = via PATH;
    # absoluter Pfad nötig, wenn claude nicht auf dem (Service-)PATH liegt.
    "BIBI_CLAUDE_BIN": "claude",
    # Knoten-Identität für Worker/Heartbeat (Team-Registry, §4.2/A12) — Default
    # leer = socket.gethostname(). Explizit nötig, sobald mehrere Instanzen
    # (Host + Client) unter demselben Hostnamen laufen, sonst überschreiben sich
    # ihre Registry-Einträge gegenseitig (gleicher Dict-Key).
    "BIBI_WORKER_NAME": "",
    # Von außen erreichbarer Hostname für App-Adressen (PLAN-22 Befund 6) —
    # Default leer = Ableitung über public_host() (BIBI_SCHEDULER_URL-Hostname,
    # sonst localhost). Nötig für jeden Knoten, der App-Typ-Jobs (app_port)
    # dispatcht und dessen Adresse einem Remote-Browser gemeldet werden soll.
    "BIBI_PUBLIC_HOST": "",
}

DAEMON_PORT_DEFAULT = 8769


def daemon_port() -> int:
    """Lauschport des Daemons: ``BIBI_DAEMON_PORT`` env > Port aus
    ``BIBI_SCHEDULER_URL`` (env oder ``~/.config/bibi/env``) > Default 8769.

    Ohne den ``BIBI_SCHEDULER_URL``-Fallback liefen ``bibi-ctrl job``/
    ``daemon status`` ohne ``--port``-Flag an per ``init`` konfigurierten
    Instanzen (z. B. Port 8780) vorbei — silent gegen einen Fremdprozess
    am Default-Port statt gegen den eigentlich gemeinten Daemon.
    """
    raw = os.environ.get("BIBI_DAEMON_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass

    scheduler_url = (os.environ.get("BIBI_SCHEDULER_URL", "").strip()
                      or read_env().get("BIBI_SCHEDULER_URL", "").strip())
    if scheduler_url:
        port = urlparse(scheduler_url).port
        if port:
            return port

    return DAEMON_PORT_DEFAULT


def public_host() -> str:
    """Von außen erreichbarer Hostname dieses Knotens für App-Adressen (§
    PLAN-22 Befund 6 — löst die zuvor an drei Stellen hartkodierte
    ``127.0.0.1``-Adresse ab, die auf einem Remote-Host wie sarasate tot war).

    Stufen: ``BIBI_PUBLIC_HOST`` (env > ``~/.config/bibi/env``) > Hostname aus
    ``BIBI_SCHEDULER_URL`` > ``localhost``.

    Stufe 2 ist nur eine Heuristik für Client-Rolle-Knoten (die überhaupt eine
    ``BIBI_SCHEDULER_URL`` gesetzt haben) — kein Beweis, dass die eigene
    Adresse im selben Netz liegt. **Host-Rolle-Knoten wie ein Scheduler selbst
    haben kein ``BIBI_SCHEDULER_URL``, das auf sie selbst zeigt** — für sie
    ist Stufe 1 nicht optional, sondern der einzige Weg zu einer für Remote-
    Zugriff korrekten Adresse.
    """
    explicit = (os.environ.get("BIBI_PUBLIC_HOST", "").strip()
                or read_env().get("BIBI_PUBLIC_HOST", "").strip())
    if explicit:
        return explicit

    scheduler_url = (os.environ.get("BIBI_SCHEDULER_URL", "").strip()
                      or read_env().get("BIBI_SCHEDULER_URL", "").strip())
    if scheduler_url:
        hostname = urlparse(scheduler_url).hostname
        if hostname:
            return hostname

    return "localhost"


def env_path() -> Path:
    """Pfad zu ``env`` — ``BIBI_CONFIG_PATH`` (explizite Datei) > ``XDG_CONFIG_HOME``
    > ``~/.config``.

    ``BIBI_CONFIG_PATH`` erlaubt mehrere Daemon-Instanzen unter demselben
    Linux-User (z. B. Host + Client auf demselben Knoten) mit getrennten
    ``BIBI_ROLE``-Dateien, ohne über ``XDG_CONFIG_HOME``-Indirektion zu gehen —
    ein Pfad, direkt in der jeweiligen systemd-Unit sichtbar.
    """
    explicit = os.environ.get("BIBI_CONFIG_PATH", "").strip()
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "bibi" / "env"


def read_env(path: Path | None = None) -> dict[str, str]:
    """``env`` parsen (``KEY=VALUE`` je Zeile). Fehlt die Datei: leeres Dict.

    Robust gegen Kommentare (``#``) und Leerzeilen; Werte werden getrimmt.
    """
    p = path or env_path()
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def write_env(values: dict[str, str], path: Path | None = None) -> Path:
    """``env`` atomar schreiben (nur bekannte KEYS, in Reihenfolge). Mode 0600."""
    p = path or env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# bibi-Knoten-Konfiguration — von `bibi-ctrl init` erzeugt (DESIGN §4.10).",
             "# Host-/team-privat; nie ins Repo committen.", ""]
    for key in KEYS:
        lines.append(f"{key}={values.get(key, '')}")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)
    return p
