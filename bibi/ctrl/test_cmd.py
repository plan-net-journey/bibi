"""``bibi-ctrl test`` — lokale Ausführung **in-place gegen den Live-Checkout**
(User-Fund 2026-07-14: Sibling zu ``bibi-ctrl run``, für schnelle lokale
Iteration).

Anders als ``run`` (frischer Worktree von ``trunk``, committet + entfernt
danach) läuft ``test`` direkt gegen den aktuellen Working Tree — **dirty
erlaubt, kein Commit vorher nötig, committet auch danach nie**. Ergebnisse
bleiben als uncommittete Änderungen im Repo liegen, zur Durchsicht.

Funktioniert für **beide** ``exec_mode``: bei ``container`` wird der
Live-Checkout selbst gemountet statt eines frischen Worktrees — der Container
sieht damit auch alles Uncommittete/Gitignorte, das gerade im Repo liegt
(nicht nur, was in ``trunk`` committet ist), und teilt sich denselben
``.git/index`` mit deinem interaktiven Git-Client (kein isolierter
Worktree-Index). Das ist eine bewusste Eigenschaft, kein Bug — ``test`` ist
für einen einzelnen, bewusst handelnden Menschen auf seinem eigenen Knoten
gedacht, nicht für den Scheduler (der ``in_place`` nie setzt) oder einen
geteilten Produktions-Knoten.

  bibi-ctrl test <slug>          # eine erfasste Schedule-MD, in-place
  bibi-ctrl test --cmd "echo hi" # ad-hoc, in-place
"""

from __future__ import annotations

import argparse
import sys
import time

from bibi import repo
from bibi.daemon import job_db
from bibi.daemon.worker import run_pinned
from bibi.schedule.lifecycle import TERMINAL
from bibi.schedule.models import Status
from bibi.wrapper import output


def _wait_until_terminal(job_id: str, *, poll: float = 0.1) -> dict:
    """Wie ``run_cmd._wait_until_terminal()`` — blockiert bis Terminalzustand."""
    conn = job_db.connect()
    try:
        while True:
            row = conn.execute(
                "SELECT status, exit_code FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is not None and Status(row["status"]) in TERMINAL:
                return dict(row)
            time.sleep(poll)
    finally:
        conn.close()


def test(args: argparse.Namespace) -> int:
    if not args.slug and not args.command:
        print("bibi-ctrl test: <slug> oder --cmd nötig", file=sys.stderr)
        return 2
    try:
        res = run_pinned(slug=args.slug, cmd=args.command, kind=args.kind, in_place=True)
    except LookupError as exc:
        print(f"bibi-ctrl test: {exc}", file=sys.stderr)
        return 1

    row = _wait_until_terminal(res["id"])
    out_path = repo.root() / res["output_ref"]
    for line in output.lines(out_path):
        print(line)
    print(f"[{row['status']}] exit={row['exit_code']} ({res['kind']}, in-place)", file=sys.stderr)
    return 0 if row["status"] == "complete" else 1


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "test",
        help="Job in-place gegen den Live-Checkout ausführen (dirty erlaubt, committet nie)")
    p.add_argument("slug", nargs="?", default=None, help="Slug einer erfassten Schedule-MD")
    p.add_argument("--cmd", dest="command", default=None, help="ad-hoc Shell-Befehl (in-place)")
    p.add_argument("--kind", default="job", help="Typ für --cmd (default: job)")
    p.set_defaults(func=test)
