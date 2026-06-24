"""``bibi-ctrl save`` — committen + (optional) pushen (PLAN-1 §1.2).

Zwei Geltungsbereiche (A10): mit aktivem Case (cwd im Case-Ordner) werden nur
die fallbezogenen Änderungen committet; ohne aktiven Case das *gesamte* Repo.
Push folgt der Sync-Matrix (§4.9): ``--push`` oder ``auto_sync on`` → pushen;
sonst committen + integrieren, aber nicht pushen (der Skill fragt nach).
"""

from __future__ import annotations

import argparse
import sys

from bibi import git_ops, repo, state, sync


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("save", help="aktiven Case oder ganzes Repo committen + (push)")
    p.add_argument("-m", "--message", help="Commit-Message überschreiben")
    p.add_argument("--push", action="store_true",
                   help="pushen unabhängig vom auto_sync-Flag")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    path = state.get_path()  # vault-relativ (aktiver Case) oder None
    if path:
        scope = repo.vault() / path
        default_msg = f"save: {scope.name}"
    else:
        scope = None  # ganzes Repo (A10)
        default_msg = f"save: {repo.root().name}"

    message = args.message or default_msg
    do_push = args.push or sync.auto_push_enabled()

    ok, log, kind = git_ops.commit_and_push(scope, message, do_push)
    for line in log:
        print(line)

    if kind == "conflict":
        state.set_sync_conflict(True)
        print("⚠ Merge-Konflikt — KI-Auflösung nötig (/sync).", file=sys.stderr)
    return 0 if ok else 1
