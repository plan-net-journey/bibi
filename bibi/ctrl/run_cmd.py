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
import socket
import sys
import time

from bibi import repo
from bibi.daemon import job_db
from bibi.daemon.worker import Worker, run_pinned
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


def run_list(args: argparse.Namespace) -> int:
    """PLAN-32 Stufe 32.3 (User-Fund): ``/run``-gepinnte Jobs sind für
    ``bibi-ctrl job list`` unsichtbar (das redet mit dem Scheduler, s.
    ``job_cmd.py``) — auf einem reinen Client-Knoten (kein ``--scheduler``)
    gibt es dafür sonst gar keinen Einblick. Direkter DB-Zugriff, kein Daemon
    nötig, genau wie ``run_pinned()`` selbst."""
    conn = job_db.connect()
    try:
        rows = job_db.list_pinned(conn, socket.gethostname())
    finally:
        conn.close()
    if not rows:
        print("(keine lokal gepinnten Jobs)")
        return 0
    width = max(len(r["slug"]) for r in rows)
    for r in rows:
        print(f"{r['slug']:<{width}}  {r['status']:<9}  ({r['kind']})  {r['id']}")
    return 0


def run_kill(args: argparse.Namespace) -> int:
    """Killt einen lokal gepinnten Job direkt (kein HTTP/Scheduler-Rolle
    nötig) — spiegelt ``POST /-/job/{id}/kill`` (``app.py``), aber für Jobs,
    die dieser Route (Scheduler-Rolle-gated) nie erreichbar wären."""
    conn = job_db.connect()
    try:
        row = conn.execute(
            "SELECT pinned_host FROM jobs WHERE id=?", (args.id,)).fetchone()
        if row is None:
            print(f"kein Job mit id {args.id}", file=sys.stderr)
            return 1
        if row["pinned_host"] is None:
            print(f"Job {args.id} ist nicht lokal gepinnt (kein /run-Job)", file=sys.stderr)
            return 1
        worker = Worker(autopoll=False)
        signaled = worker.kill(args.id)
        outcome = job_db.report_status(conn, args.id, status="killed", reason="by_user")
    finally:
        conn.close()
    if outcome == "invalid":
        print(f"Job {args.id} läuft nicht (nicht killbar)", file=sys.stderr)
        return 1
    print(f"{args.id} killed (signaled={signaled})")
    return 0


def run_reset(args: argparse.Namespace) -> int:
    """Räumt einen lokal gepinnten Job vollständig weg: killt ihn best-effort
    (falls noch aktiv), wischt seine ``bibi.job.data_dir()``-Daten und löscht
    die Zeile. Anders als ``bibi-ctrl job restart`` (Terminalzustand →
    pending) gibt es hier kein sinnvolles "pending" — ein gepinnter Job hat
    einen einmaligen, zufallssuffigierten Slug und wird nie neu disponiert,
    reset bedeutet für ihn deshalb vollständiges Löschen (User-Entscheidung
    PLAN-32 Stufe 32.3: reset == löschen für /run-Jobs)."""
    conn = job_db.connect()
    try:
        row = conn.execute(
            "SELECT pinned_host FROM jobs WHERE id=?", (args.id,)).fetchone()
        if row is None:
            print(f"kein Job mit id {args.id}", file=sys.stderr)
            return 1
        if row["pinned_host"] is None:
            print(f"Job {args.id} ist nicht lokal gepinnt (kein /run-Job)", file=sys.stderr)
            return 1
        Worker(autopoll=False).kill(args.id)  # best-effort, Status hier egal
        job_db.wipe_job_data(args.id)
        job_db.delete_pinned_job(conn, args.id)
    finally:
        conn.close()
    print(f"{args.id} zurückgesetzt (Daten gewischt, Zeile gelöscht)")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("run", help="Job sofort lokal ausführen (§6.3, gepinnt, kein Retry)")
    p.add_argument("slug", nargs="?", default=None, help="Slug einer erfassten Schedule-MD")
    # dest != "cmd": die Top-Level-Subparser nutzen bereits dest="cmd".
    p.add_argument("--cmd", dest="command", default=None, help="ad-hoc Shell-Befehl (rein lokal)")
    p.add_argument("--kind", default="job", help="Typ für --cmd (default: job)")
    p.set_defaults(func=run)


def register_pinned(sub: argparse._SubParsersAction) -> None:
    """Eigener Top-Level-Befehl statt Unterbefehle von ``run`` — ``run``
    selbst nimmt schon ein positionales ``slug`` entgegen (``bibi-ctrl run
    <slug>``), das würde mit `argparse`-Subparsern für list/kill/reset
    kollidieren (beides positional an derselben Stelle)."""
    p = sub.add_parser(
        "pinned", help="lokal gepinnte /run-Jobs verwalten (§6.3, PLAN-32 Stufe 32.3)")
    psub = p.add_subparsers(dest="pinned_cmd")
    pl = psub.add_parser("list", help="lokal gepinnte Jobs listen (dieser Host)")
    pl.set_defaults(func=run_list)
    pk = psub.add_parser("kill", help="einen lokal gepinnten Job killen")
    pk.add_argument("id")
    pk.set_defaults(func=run_kill)
    pr = psub.add_parser("reset", help="einen lokal gepinnten Job killen + Daten/Zeile löschen")
    pr.add_argument("id")
    pr.set_defaults(func=run_reset)
    p.set_defaults(func=lambda _a: (p.print_help() or 1))
