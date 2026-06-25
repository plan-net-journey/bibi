"""``bibi-ctrl status`` — Repo-State + Knoten-Konfiguration anzeigen.

Zwei Blöcke:
- **Repo-State** (cwd-abgeleitet + ``.state.md``): path, auto_sync,
  sync_conflict, protocol (letzteres nur wenn ein Case aktiv ist).
- **Knoten-Config** (``~/.config/bibi/env``): Rolle, Remote, Scheduler-URL.
"""

from __future__ import annotations

import argparse

from .. import __version__, case_store, config, state


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("status", help="Repo-State und Knoten-Konfiguration anzeigen")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    # --- Repo-State ---
    s = state.read()
    case_path = state.get_path()
    print(f"path: {case_path or '(none)'}")
    print(f"auto_sync: {s.get('auto_sync', 'off')}")
    if s.get("sync_conflict"):
        print("sync_conflict: true")
    if case_path:
        folder = case_store.active_case()
        if folder:
            proto = case_store.read_frontmatter(folder).get("protocol", "")
            print(f"protocol: {proto or 'off'}")

    # --- Knoten-Config ---
    print(f"bibi {__version__}")
    env_path = config.env_path()
    env = config.read_env(env_path)
    if not env:
        print(f"Keine Konfiguration ({env_path}). 'bibi-ctrl init' ausführen.")
        return 0
    print(f"  Scheduler-URL: {env.get('BIBI_SCHEDULER_URL', '—')}")
    print(f"  Rollen:        {env.get('BIBI_ROLE', '—')}")
    print(f"  Git-Remote:    {env.get('BIBI_REMOTE', '—') or '—'}")
    return 0
