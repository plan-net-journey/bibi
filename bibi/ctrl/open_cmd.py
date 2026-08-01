"""``bibi-ctrl open`` — Case öffnen oder reaktivieren (PLAN-1 §1.1, ex bibi3 `project`).

Substring-Match im Case-Verzeichnis; genau ein Treffer → reaktivieren
(``status: open``, auch aus ``paused``; ``closed`` nur mit ``--force``).
Mehrere Treffer → auflisten. Kein Treffer → anlegen. Gibt in allen Fällen eine
``cd:``-Zeile aus, in die die Skill die Shell parkt.
"""

from __future__ import annotations

import argparse
import sys

from bibi import case_store, repo, state


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("open", help="Case öffnen/reaktivieren")
    p.add_argument("topic", help="neues Topic oder Substring eines bestehenden")
    p.add_argument("--force", action="store_true", help="closed-Cases reaktivieren")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    topic: str = args.topic
    case_name = repo.case_dir_name()
    matches = case_store.find_matches(topic)

    if len(matches) > 1:
        print(f"mehrdeutig: {len(matches)} Treffer für {topic!r}:", file=sys.stderr)
        for m in matches:
            print(f"  {m.folder_name}", file=sys.stderr)
        print("bitte spezifischer.", file=sys.stderr)
        return 2

    if len(matches) == 1:
        m = matches[0]
        current_status = case_store.get_status(m.folder)
        if current_status == "closed" and not args.force:
            print(f"Case {m.folder_name} ist closed. --force zum Reaktivieren nutzen.",
                  file=sys.stderr)
            return 2
        case_store.set_status(m.folder, "open")
        rel = f"{case_name}/{m.folder_name}"
        state.set_path(rel)  # parkt die Session auf den Case
        print(f"reaktiviert: {rel} (status: open)")
        print(f"cd: {m.folder.resolve()}")
        return 0

    folder = case_store.create_case(topic)
    rel = f"{case_name}/{folder.name}"
    state.set_path(rel)  # parkt die Session auf den Case
    print(f"erstellt: {rel}")
    print(f"cd: {folder.resolve()}")
    return 0
