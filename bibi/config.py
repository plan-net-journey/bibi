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
    # Menschlich gewählter Anzeigename für den Connected-Clients/Nodes-Screen
    # (Team-Registry, §4.2/A12) — Default leer = socket.gethostname(). Gilt für
    # JEDEN --connect-Knoten (Client oder Worker), nicht nur die Worker-Rolle,
    # trotz des historischen Namens (PLAN-34: BIBI_WORKER_NAME war irreführend,
    # BIBI_NODE_NAME passt zu BIBI_NODE_ID unten). Registry-Kollisionsschutz ist
    # NICHT mehr der Grund, ihn zu setzen — das übernimmt seit dem node_id-Fix
    # (Bibi4-Iteration) node_id als Registry-Schlüssel; Grund heute: ein
    # sprechendes Label statt eines rohen/opaken Hostnamens (z. B. im Docker-
    # Container). Für die Worker-Rolle bleibt derselbe Wert zusätzlich die
    # Job-Claim-Identität (``jobs.worker``-Spalte, ``worker.py``) — dort weiter
    # unter dem internen Namen ``worker_name`` geführt, s. PLAN-34 Entscheidung 1.
    "BIBI_NODE_NAME": "",
    # Von außen erreichbarer Hostname für App-Adressen (PLAN-22 Befund 6) —
    # Default leer = Ableitung über public_host() (BIBI_SCHEDULER_URL-Hostname,
    # sonst localhost). Nötig für jeden Knoten, der App-Typ-Jobs (app_port)
    # dispatcht und dessen Adresse einem Remote-Browser gemeldet werden soll.
    "BIBI_PUBLIC_HOST": "",
    # BIBI_STATUS_POLL_INTERVAL / BIBI_JOB_STATUS_POLL_INTERVAL: entfernt in
    # PLAN-36 Stufe 36.3 — das FE pollt nicht mehr, alle Regionen hängen am
    # Event-Bus (/-/events); der Collector-Takt ist ein Engine-Internum
    # (daemon/bus.py), kein Konfigurationswert. Pre-1.0, kein Backcompat.
    # Stabile, generierte Knoten-Identität für den Connected-Clients-Screen
    # (Bibi4-Iteration, User-Fund: derselbe physische Client tauchte je nach
    # Netzwerk mit unterschiedlichem BIBI_NODE_NAME/Hostname auf, alte
    # Registry-Einträge blieben stale liegen) — unabhängig von IP/Hostname,
    # einmalig generiert (node_id() unten), danach nie mehr geändert. Anders
    # als jeder andere Wert hier NIE interaktiv abgefragt (init_cmd.py
    # special-cased das) — ein Mensch soll nie eine UUID eintippen müssen.
    "BIBI_NODE_ID": "",
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


def scheduler_base_url() -> str:
    """Basis-URL des Schedulers — anders als :func:`daemon_port` (nur der Port)
    liefert diese Funktion Host **und** Port.

    ``BIBI_DAEMON_PORT`` (env, lokal-explizit) > ``BIBI_SCHEDULER_URL`` (env
    oder ``~/.config/bibi/env``, volle URL inkl. Host) > ``http://localhost:8769``.

    PLAN-13 Stufe 13.0 (2026-07-17): ``bibi-ctrl job``/``at`` sprachen bisher
    immer ``127.0.0.1:{daemon_port()}`` an — auch auf einem reinen Client-
    Knoten, dessen ``BIBI_SCHEDULER_URL`` korrekt auf einen entfernten Host
    zeigt. Läuft dort zufällig ein eigener lokaler Daemon auf demselben Port
    (z. B. Client-Rolle auf Port 8780), landet der Befehl nicht bei
    "Connection refused", sondern beim eigenen, falschen (Nicht-Scheduler-)
    Daemon — Root Cause einer Session, die lange raten musste, wo der
    Scheduler tatsächlich läuft, obwohl die Antwort in der eigenen Config
    stand. ``BIBI_DAEMON_PORT`` bleibt Vorrang, weil es explizit "sprich mit
    MEINEM eigenen Daemon" bedeutet (von ``bibi-ctrl daemon`` selbst gesetzt,
    s. ``daemon_cmd.py``) — ein reiner Lokalitäts-Override, kein Federations-
    Ziel."""
    raw = os.environ.get("BIBI_DAEMON_PORT", "").strip()
    if raw:
        try:
            return f"http://127.0.0.1:{int(raw)}"
        except ValueError:
            pass

    scheduler_url = (os.environ.get("BIBI_SCHEDULER_URL", "").strip()
                      or read_env().get("BIBI_SCHEDULER_URL", "").strip())
    if scheduler_url:
        return scheduler_url.rstrip("/")

    return f"http://localhost:{DAEMON_PORT_DEFAULT}"


def public_host() -> str:
    """Von außen erreichbarer Hostname dieses Knotens für App-Adressen (§
    PLAN-22 Befund 6 — löst die zuvor an drei Stellen hartkodierte
    ``127.0.0.1``-Adresse ab, die auf einem Remote-Host wie sarasate tot war).

    Stufen: ``BIBI_PUBLIC_HOST`` (env > ``~/.config/bibi/env``) > ``localhost``.

    Früher gab es eine Zwischenstufe, die ohne explizites ``BIBI_PUBLIC_HOST``
    den Hostnamen aus ``BIBI_SCHEDULER_URL`` borgte — entfernt (Bibi4-
    Iteration, User-Fund: ein Client zeigte den Hostnamen seines Schedulers
    statt seines eigenen). Sie half laut ihrer eigenen ursprünglichen Doku nie
    dem Host-Rolle-Fall (der braucht Stufe 1 ohnehin zwingend) und war für
    einen echten Remote-Client schlicht falsch — sie borgte die Adresse eines
    FREMDEN Knotens. Ohne explizites ``BIBI_PUBLIC_HOST`` bleibt es jetzt beim
    reinen ``localhost``-Default, kein Rätselraten mehr.
    """
    explicit = (os.environ.get("BIBI_PUBLIC_HOST", "").strip()
                or read_env().get("BIBI_PUBLIC_HOST", "").strip())
    if explicit:
        return explicit

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


def node_id() -> str:
    """Stabile, generierte Knoten-Identität (``BIBI_NODE_ID``, s. ``KEYS``-
    Kommentar) — self-healing: fehlt der Wert (Bestandsknoten von vor dieser
    Änderung, oder ein ``bibi-ctrl init`` ohne diesen Schlüssel), wird er beim
    ersten Zugriff generiert und in dieselbe ``env``-Datei zurückgeschrieben,
    kein manuelles Neu-``init`` auf bereits konfigurierten Knoten nötig."""
    import uuid
    existing = read_env()
    val = existing.get("BIBI_NODE_ID", "").strip()
    if val:
        return val
    new_id = uuid.uuid4().hex
    existing["BIBI_NODE_ID"] = new_id
    write_env(existing)
    return new_id


# ── PLAN-32 Stufe 32.2/32.3: Credential-Distribution (Host → Client) ────────
#
# Allowlist ist eine Namenskonvention, kein zweites Verzeichnis (Entscheidung
# 3): jeder ``BIBI_JOB_ENV_*``-Wert im eigenen ``env`` ist automatisch
# verteilbar — dieselbe Menge, die ``worker.py::_exec_config()`` für
# Job-Injection bereits liest. Auf dem Client landen empfangene Werte in
# einer ZWEITEN, dem eigentlichen ``env`` vorgelagerten Datei (Entscheidung
# 4) — Herkunft bleibt sichtbar, ein lokal in ``env`` gesetzter gleichnamiger
# Wert gewinnt immer (dortiges Merge in ``worker.py::_exec_config()``).

_JOB_ENV_PREFIX = "BIBI_JOB_ENV_"
#: Interner Marker in der Distributed-Datei, kein Job-Credential — beginnt
#: bewusst nicht mit _JOB_ENV_PREFIX, damit der Präfix-Scan ihn nie injiziert.
_DISTRIBUTED_VERSION_KEY = "__bibi_config_version__"


def distributable_config(env: dict[str, str] | None = None) -> dict[str, str]:
    """Host-Seite: alle ``BIBI_JOB_ENV_*``-Werte aus ``env`` (Default:
    ``read_env()`` gemergt mit ``os.environ``, Prozess-Env gewinnt bei
    Kollision — dieselbe Präzedenz wie ``worker.py::_exec_config()``s
    Job-Injection) — die komplette Distribution-Allowlist, eine reine
    Namens-Prüfung, keine zweite Liste. Beide Quellen zu berücksichtigen ist
    hier bewusst: verteilbar soll exakt sein, was der Präfix-Scan für die
    eigene Job-Injection ohnehin schon nutzt, nicht nur der Datei-Anteil davon."""
    env = {**read_env(), **os.environ} if env is None else env
    return {k: v for k, v in env.items() if k.startswith(_JOB_ENV_PREFIX) and v}


def config_version(bundle: dict[str, str]) -> str:
    """Kurzer, stabiler Hash über die verteilbare Config (Entscheidung 2:
    Hash statt Timestamp — ändert sich genau dann, wenn sich ein Wert
    tatsächlich ändert, immun gegen Uhrzeit-Drift/„berührt-aber-unverändert")."""
    import hashlib
    canonical = "\n".join(f"{k}={bundle[k]}" for k in sorted(bundle))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def distributed_env_path() -> Path:
    """Client-Seite: die zweite, ``env`` vorgelagerte Datei (Entscheidung 4)
    — neben der Haupt-``env`` desselben Knotens (erbt so automatisch
    ``BIBI_CONFIG_PATH``s Mehrfach-Instanz-Trennung, s. ``env_path()``)."""
    return env_path().parent / "distributed-env"


def read_distributed_env(path: Path | None = None) -> dict[str, str]:
    """Client-Seite: zuletzt vom Host empfangenes Bundle + Versionsmarker
    lesen. Fehlt die Datei (noch nie ein Bundle empfangen): leeres Dict —
    dieselbe Robustheit wie ``read_env()``."""
    p = path or distributed_env_path()
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


def distributed_config_version(path: Path | None = None) -> str | None:
    """Client-Seite: die zuletzt angewandte Version, fürs nächste
    Heartbeat-``client_config_version``-Feld. ``None`` = noch nie empfangen."""
    return read_distributed_env(path).get(_DISTRIBUTED_VERSION_KEY)


def write_distributed_env(bundle: dict[str, str], *, version: str,
                          path: Path | None = None) -> Path:
    """Client-Seite: neues Bundle atomar schreiben (analog ``write_env()``,
    Mode 0600) — komplett ersetzt, nicht gemergt (das Bundle selbst ist schon
    die vollständige, aktuelle Sicht vom Host)."""
    p = path or distributed_env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# bibi — vom Host verteilte Job-Credentials (PLAN-32 Stufe 32.2).",
             "# Automatisch geschrieben bei jedem Heartbeat mit neuer Version —",
             "# manuelle Änderungen gehen beim nächsten Fetch verloren. Ein lokal",
             "# in ~/.config/bibi/env gesetzter gleichnamiger Wert gewinnt immer.", ""]
    for key in sorted(bundle):
        lines.append(f"{key}={bundle[key]}")
    lines.append(f"{_DISTRIBUTED_VERSION_KEY}={version}")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)
    return p
