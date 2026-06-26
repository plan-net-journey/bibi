"""Repo-scoped Pfade des Team-Repos (DESIGN §3.2).

Anders als die Knoten-Konfiguration (``bibi.config`` → ``~/.config/bibi/env``,
host-privat) sind dies Eigenschaften des *Team-Repos*: sie reisen mit dem Repo.
Alles wird vom aktuellen Arbeitsverzeichnis aus aufgelöst (git-Toplevel), lazy —
``bibi-ctrl status``/``init`` brauchen kein Repo, ``open`` schon.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

#: Default-Name des Case-Verzeichnisses; per pyproject ``[tool.bibi] case_dir``
#: oder ``BIBI_CASE_DIR`` überschreibbar. bibi3-Kompat: ``case_dir = "project"``.
DEFAULT_CASE_DIR = "case"


@lru_cache(maxsize=None)
def _root_of(cwd: str) -> Path:
    """git-Toplevel von ``cwd``. Beendet mit Code 2, wenn kein git-Repo.

    Nach ``cwd`` gecached (nicht nach Argument-Default), damit ein cwd-Wechsel
    — im Prozess wie in Tests — ein neues Ergebnis liefert.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"bibi: {cwd} liegt in keinem git-Repo.", file=sys.stderr)
        sys.exit(2)
    return Path(proc.stdout.strip())


def root() -> Path:
    """git-Toplevel des aktuellen Arbeitsverzeichnisses."""
    return _root_of(str(Path.cwd().resolve()))


def vault() -> Path:
    return root() / "vault"


def state_path() -> Path:
    return root() / ".claude" / ".state.md"


def data() -> Path:
    """Gitignored Laufzeit-Verzeichnis (DESIGN §3.2) — Job-DB, output.jsonl, Journal."""
    return root() / "data"


def case_dir_name() -> str:
    """``BIBI_CASE_DIR`` > pyproject ``[tool.bibi] case_dir`` > Default ``case``."""
    env = os.environ.get("BIBI_CASE_DIR")
    if env:
        return env.strip()
    pyproject = root() / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            value = data.get("tool", {}).get("bibi", {}).get("case_dir")
            if value:
                return str(value).strip()
        except (tomllib.TOMLDecodeError, OSError):
            pass
    return DEFAULT_CASE_DIR


def case_dir() -> Path:
    """Absoluter Pfad zum Case-Verzeichnis (``vault/<case_dir_name>``)."""
    return vault() / case_dir_name()
