"""``bibi-ctrl run`` — lokale On-Demand-Ausführung (PLAN-3 §3.3b, DESIGN §6.3).

Führt einen Job **sofort lokal** aus — **ohne** stehenden Worker-Daemon und
**ohne** den zentralen Scheduler zu berühren (kein ``jobs``-Eintrag). Journal +
``output.jsonl`` bleiben im lokalen ``data/`` (§1.4). In-Process: ruft
``worker.run_local`` direkt, kein HTTP.

  bibi-ctrl run <slug>          # eine erfasste Schedule-MD per Slug
  bibi-ctrl run --cmd "echo hi" # ad-hoc, rein lokal
"""

from __future__ import annotations

import argparse
import sys

from bibi import repo
from bibi.daemon.worker import run_local
from bibi.wrapper import output


def run(args: argparse.Namespace) -> int:
    if not args.slug and not args.command:
        print("bibi-ctrl run: <slug> oder --cmd nötig", file=sys.stderr)
        return 2
    try:
        res = run_local(slug=args.slug, cmd=args.command, kind=args.kind)
    except LookupError as exc:
        print(f"bibi-ctrl run: {exc}", file=sys.stderr)
        return 1

    out_path = repo.data() / "job" / res["id"] / "output.jsonl"
    for line in output.lines(out_path):
        print(line)
    print(f"[{res['status']}] exit={res['exit_code']} ({res['kind']})", file=sys.stderr)
    return 0 if res["status"] == "complete" else 1


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("run", help="Job sofort lokal ausführen (§6.3, kein Scheduler)")
    p.add_argument("slug", nargs="?", default=None, help="Slug einer erfassten Schedule-MD")
    # dest != "cmd": die Top-Level-Subparser nutzen bereits dest="cmd".
    p.add_argument("--cmd", dest="command", default=None, help="ad-hoc Shell-Befehl (rein lokal)")
    p.add_argument("--kind", default="job", help="Typ für --cmd (default: job)")
    p.set_defaults(func=run)
