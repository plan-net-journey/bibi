"""``bibi-ctrl status`` — Knoten-Konfiguration anzeigen (PLAN-0 0.5).

Liest ``~/.config/bibi/env`` und gibt Rolle, Remote und Scheduler-URL aus.
Ohne Konfiguration ein Hinweis auf ``bibi-ctrl init``.
"""

from __future__ import annotations

import argparse

from .. import __version__, config


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("status", help="Knoten-Konfiguration anzeigen")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    path = config.env_path()
    env = config.read_env(path)
    print(f"bibi {__version__}")
    if not env:
        print(f"Keine Konfiguration ({path}). 'bibi-ctrl init' ausführen.")
        return 0
    print(f"Konfiguration: {path}")
    print(f"  Scheduler-URL: {env.get('BIBI_SCHEDULER_URL', '—')}")
    print(f"  Rollen:        {env.get('BIBI_ROLE', '—')}")
    print(f"  Git-Remote:    {env.get('BIBI_REMOTE', '—') or '—'}")
    return 0
