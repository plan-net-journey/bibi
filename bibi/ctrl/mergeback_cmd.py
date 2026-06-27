"""``bibi-ctrl mergeback`` — liegengebliebene ``agent/*``-Branches nach trunk holen.

Recovery-Werkzeug (PLAN-6 Slice C): nach Slice B merged jeder erfolgreiche Lauf
sofort; bleibt dennoch ein Branch mit unmergten Commits liegen (alter Daemon,
früherer Konflikt), führt ``--apply`` ihn nachträglich zusammen. Ohne Flag nur Liste.
"""

from __future__ import annotations

import argparse

from bibi import repo
from bibi.daemon import mergeback


def run(args: argparse.Namespace) -> int:
    root = repo.root()
    pending = mergeback.unmerged_agent_branches(repo_root=root)
    if not pending:
        print("mergeback: keine unmergten agent/*-Branches ✓")
        return 0
    if not args.apply:
        print(f"{len(pending)} unmergte(r) Branch(es) — mit --apply zusammenführen:")
        for b in pending:
            print(f"  {b}")
        return 1
    results = mergeback.remerge_all(repo_root=root)
    for branch, status in results.items():
        print(f"  {branch}: {status}")
    bad = [b for b, s in results.items() if s not in ("merged", "up_to_date")]
    print(f"\n{len(results)} verarbeitet, {len(bad)} ungelöst (Konflikt/Fehler).")
    return 1 if bad else 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("mergeback",
                       help="unmergte agent/*-Branches listen/zusammenführen (PLAN-6)")
    p.add_argument("--apply", action="store_true", help="zusammenführen statt nur listen")
    p.set_defaults(func=run)
