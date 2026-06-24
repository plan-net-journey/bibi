"""Knoten-Konfiguration: ``~/.config/bibi/env`` (DESIGN §4.10).

Drei host-/team-private Parameter, die das Repo bewusst NICHT enthält:
``BIBI_SCHEDULER_URL``, ``BIBI_ROLE``, ``BIBI_REMOTE``. Geschrieben von
``bibi-ctrl init``, gelesen u. a. von ``bibi-ctrl status`` und (später)
``bibi-ctrl daemon install``.
"""

from __future__ import annotations

import os
from pathlib import Path

# Reihenfolge = Abfrage-/Schreibreihenfolge. Werte sind die Defaults für init.
KEYS: dict[str, str] = {
    "BIBI_SCHEDULER_URL": "http://localhost:8769",
    "BIBI_ROLE": "synchronizer",
    "BIBI_REMOTE": "",
}


def env_path() -> Path:
    """Pfad zu ``env`` — respektiert ``XDG_CONFIG_HOME``, sonst ``~/.config``."""
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
