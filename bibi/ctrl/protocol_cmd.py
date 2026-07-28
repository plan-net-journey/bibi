"""``bibi-ctrl protocol on|off|debug`` + ``bibi-ctrl on-stop`` (PLAN-1 §1.4).

``protocol`` ist ein **reiner Frontmatter-Toggle** — es fasst ``settings.json``
nie an. Der Stop-Hook steht statisch (committed) in der Team-Repo-Settings und
ist selbst-gated: ``on-stop`` tut nichts, wenn der aktive Case kein
``protocol:``-Feld trägt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bibi import case_store, frontmatter, protocol, state


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("protocol", help="Turn-Logging im aktiven Case umschalten")
    p.add_argument("action", choices=["on", "off", "debug"])
    p.set_defaults(func=run_protocol)

    ph = sub.add_parser("on-stop", help="intern: Claude-Code-Stop-Hook-Handler")
    ph.set_defaults(func=run_on_stop)


def run_protocol(args: argparse.Namespace) -> int:
    folder = case_store.active_case()
    if folder is None:
        print("kein aktiver Case. Zuerst `bibi-ctrl open <topic>`.", file=sys.stderr)
        return 2

    readme = folder / "README.md"
    if args.action == "off":
        frontmatter.patch(readme, protocol=None)
        print("protocol → off")
        return 0

    value = f"./{protocol.PROTOCOL_FILENAME}"
    if args.action == "debug":
        value += "+debug"
    frontmatter.patch(readme, protocol=value)
    print(f"protocol → {args.action} → {value}")
    return 0


def run_on_stop(_: argparse.Namespace) -> int:
    """Vom Claude-Code-Stop-Hook gerufen. Endet IMMER mit 0 (blockiert Claude nie)."""
    try:
        hook_data = json.load(sys.stdin)
    except Exception:
        return 0

    # Hooks laufen als eigener Prozess im Projektverzeichnis, nie im geparkten
    # Case-cwd — ohne die session_id aus dem Payload fände `active_case()` hier
    # grundsätzlich nichts und das Turn-Logging liefe still ins Leere.
    state.adopt_session(hook_data.get("session_id"))

    folder = case_store.active_case()
    if folder is None:
        return 0

    try:
        protocol_field = case_store.read_frontmatter(folder).get("protocol")
    except Exception:
        return 0
    if not protocol_field:
        return 0

    transcript = hook_data.get("transcript_path")
    if not transcript:
        return 0

    try:
        protocol.append_turn(folder, str(protocol_field), Path(transcript))
    except Exception:
        pass
    return 0
