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


def root_or_none() -> Path | None:
    """git-Toplevel — ``None`` statt Prozessabbruch, wenn hier kein Repo ist.

    :func:`root` beendet mit Code 2, und das ist für ``open``/``save`` richtig:
    ohne Repo gibt es dort nichts zu tun. Für einen Leser, der ein Repo nur
    *versucht*, ist es falsch — ``config.daemon_port()`` läuft laut Modul-
    Docstring ausdrücklich auch außerhalb (``bibi-ctrl status``/``init``
    brauchen kein Repo), und seit m.rau/bibi#45 sieht es dabei unter ``data/``
    nach, welcher Daemon gerade läuft.

    **Warum hier kein ``git rev-parse`` und kein Cache.** Beides hätte
    :func:`_root_of` geliefert, aber diese Funktion wird über
    ``config.daemon_port()`` auch aus der Statusline gerufen, also bei jedem
    Prompt — ein Subprozess dafür wäre zu teuer, und ein zweiter ``lru_cache``
    daneben müsste in gut zwanzig Test-Fixtures zusätzlich invalidiert werden,
    die heute nur ``_root_of.cache_clear()`` rufen (eine vergessene Stelle wäre
    ein stiller, schwer zu findender Testfehler). Das Hochlaufen nach ``.git``
    ist billig genug, um ohne Cache auszukommen, und liefert dasselbe Ergebnis:
    ``.exists()`` deckt auch die *Datei* ``.git`` ab, die ein git-Worktree dort
    ablegt — dessen Toplevel ist das Worktree-Verzeichnis, genau wie bei
    ``git rev-parse --show-toplevel``.
    """
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


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


def credential_checks() -> list[dict[str, str]]:
    """Geheimnisse, die an zwei Orten liegen und nicht auseinanderlaufen dürfen.

    Aus pyproject ``[[tool.bibi.credential_checks]]``::

        [[tool.bibi.credential_checks]]
        env = "GITEA_TOKEN"                    # ohne BIBI_JOB_ENV_-Präfix
        keychain_service = "sarasate-gitea"
        keychain_account = "gitea-bibi-issues"

    Bewusst im Team-Repo konfiguriert, nicht in der Engine fest verdrahtet:
    *welches* Credential doppelt gehalten wird und unter welchem
    Keychain-Namen, ist eine Eigenschaft der Instanz. Die Engine liefert nur
    den Mechanismus. Die Angaben sind keine Geheimnisse (Dienst- und
    Kontoname), gehören also in ein committetes pyproject — die Werte selbst
    liest ``doctor`` zur Laufzeit und gibt sie nie aus.
    """
    pyproject = root() / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return []
    raw = data.get("tool", {}).get("bibi", {}).get("credential_checks") or []
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        env = str(entry.get("env", "")).strip()
        service = str(entry.get("keychain_service", "")).strip()
        account = str(entry.get("keychain_account", "")).strip()
        if env and service and account:
            out.append({"env": env, "keychain_service": service,
                        "keychain_account": account})
    return out
