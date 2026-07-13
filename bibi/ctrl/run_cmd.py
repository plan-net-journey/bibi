"""``bibi-ctrl run`` — lokale On-Demand-Ausführung mit voller Scheduler-
Lifecycle (PLAN-3 §3.3b, DESIGN §6.3; PLAN-28).

Führt einen Job **sofort lokal** aus, gepinnt an diesen Knoten
(``jobs.pinned_host`` — kein anderer Worker kann ihn je reservieren), läuft
aber durch dieselbe Retry/Error/Deferred/Zombie-Maschine wie ein
Scheduler-Job. In-Process: ruft ``worker.run_pinned`` direkt, kein HTTP,
und pollt die ``jobs``-Zeile bis zu einem Terminalzustand.

Kein Retry standardmäßig (``attempts=0`` in ``run_pinned()``, bewusst *nicht*
der Scheduler-Default 1 — ein fälliger Retry bräuchte den gepinnten
``Worker``-Loop aus ``create_app()``, den es hier, ohne laufenden Daemon,
nicht gibt; ein wartender Retry bliebe sonst für immer unbedient hängen).

  bibi-ctrl run <slug>          # eine erfasste Schedule-MD per Slug
  bibi-ctrl run --cmd "echo hi" # ad-hoc, rein lokal
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
    """Blockiert, bis die ``jobs``-Zeile einen Terminalzustand erreicht (§5.4/
    §5.5) — CLI-Aufrufer erwarten wie bisher einen blockierenden Aufruf.
    ``FAILED`` zählt bewusst *nicht* als Terminal (kann noch retryen); mit dem
    CLI-Default ``attempts=0`` kommt das hier aber nie vor (s. Modul-Docstring)."""
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


def run(args: argparse.Namespace) -> int:
    if not args.slug and not args.command:
        print("bibi-ctrl run: <slug> oder --cmd nötig", file=sys.stderr)
        return 2
    try:
        res = run_pinned(slug=args.slug, cmd=args.command, kind=args.kind)
    except LookupError as exc:
        print(f"bibi-ctrl run: {exc}", file=sys.stderr)
        return 1

    row = _wait_until_terminal(res["id"])
    out_path = repo.root() / res["output_ref"]
    for line in output.lines(out_path):
        print(line)
    print(f"[{row['status']}] exit={row['exit_code']} ({res['kind']})", file=sys.stderr)
    return 0 if row["status"] == "complete" else 1


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("run", help="Job sofort lokal ausführen (§6.3, gepinnt, kein Retry)")
    p.add_argument("slug", nargs="?", default=None, help="Slug einer erfassten Schedule-MD")
    # dest != "cmd": die Top-Level-Subparser nutzen bereits dest="cmd".
    p.add_argument("--cmd", dest="command", default=None, help="ad-hoc Shell-Befehl (rein lokal)")
    p.add_argument("--kind", default="job", help="Typ für --cmd (default: job)")
    p.set_defaults(func=run)
