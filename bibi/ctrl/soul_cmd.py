"""``bibi-ctrl soul`` — aktive Persona für die Session merken (PLAN-13 Stufe 13.1).

Souls sind team-eigener Content, nicht Engine-Code: verfügbare Personas werden
dynamisch aus ``.claude/souls/*.SOUL.md`` im aktuellen Team-Repo gelesen, nicht
hartcodiert — jedes Team pflegt sein eigenes Souls-Set. Die Wahl wird
repo-global in ``.state.md`` persistiert (``state.py``, analog ``maintenance``),
case-insensitiv gematcht (Dateiname trägt die kanonische Schreibweise).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bibi import repo, state

_SOUL_FILE_RE = re.compile(r"^\d+\.(?P<name>[^.]+)\.SOUL\.md$")


def souls_dir() -> Path:
    return repo.root() / ".claude" / "souls"


def available_souls() -> dict[str, str]:
    """``{lowercase name: kanonischer Name}`` aller ``.claude/souls/*.SOUL.md``,
    sortiert nach Dateiname. Leeres Dict, wenn kein Souls-Verzeichnis existiert."""
    d = souls_dir()
    if not d.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(d.glob("*.SOUL.md")):
        m = _SOUL_FILE_RE.match(p.name)
        if m:
            out[m.group("name").lower()] = m.group("name")
    return out


def run(args: argparse.Namespace) -> int:
    souls = available_souls()

    if not args.name:
        current = state.get_soul()
        if current:
            print(current)
        else:
            print("keine Soul aktiv", file=sys.stderr)
        return 0

    key = args.name.strip().lower()
    if key not in souls:
        print(f"unbekannte Soul: {args.name}", file=sys.stderr)
        if souls:
            print("verfügbar: " + ", ".join(sorted(souls.values())), file=sys.stderr)
        else:
            print(f"kein Souls-Verzeichnis unter {souls_dir()}", file=sys.stderr)
        return 1

    canonical = souls[key]
    state.set_soul(canonical)
    print(canonical)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "soul",
        help="aktive Persona setzen/anzeigen (.claude/souls/*.SOUL.md)",
    )
    p.add_argument(
        "name", nargs="?", default=None,
        help="Persona-Name (case-insensitiv); ohne Argument: aktuelle Soul anzeigen",
    )
    p.set_defaults(func=run)
