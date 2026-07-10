"""``bibi-ctrl init`` — interaktiver Bootstrap (DESIGN §4.10).

Fragt die drei Knoten-Parameter ab und schreibt ``~/.config/bibi/env``.
Idempotent: existiert die Datei, werden ihre Werte als Defaults vorgeschlagen
und vor dem Überschreiben wird bestätigt. Reines Python, keine externen Deps.
"""

from __future__ import annotations

import argparse

from .. import config


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="bibi auf dieser Maschine einrichten (~/.config/bibi/env)")
    p.add_argument("-f", "--force", action="store_true",
                   help="bestehende env ohne Rückfrage überschreiben")
    p.set_defaults(func=run)


def _prompt(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{label}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def run(args: argparse.Namespace) -> int:
    path = config.env_path()
    existing = config.read_env(path)

    if existing and not args.force:
        print(f"Bestehende Konfiguration: {path}")
        for key in config.KEYS:
            print(f"  {key}={existing.get(key, '')}")
        if _prompt("Überschreiben? (j/N)", "N").lower() not in ("j", "ja", "y", "yes"):
            print("Abgebrochen — nichts geändert.")
            return 0

    values: dict[str, str] = {}
    labels = {
        "BIBI_SCHEDULER_URL": "Scheduler-URL",
        "BIBI_ROLE": "Rollen (kommagetrennt)",
        "BIBI_REMOTE": "Git-Remote",
        "BIBI_CLAUDE_BIN": "claude-Binary (Pfad/Name)",
        "BIBI_WORKER_NAME": "Knoten-Name (leer = Hostname)",
    }
    for key, fallback in config.KEYS.items():
        default = existing.get(key) or fallback
        values[key] = _prompt(labels.get(key, key), default)

    written = config.write_env(values, path)
    print(f"→ geschrieben: {written}")
    return 0
