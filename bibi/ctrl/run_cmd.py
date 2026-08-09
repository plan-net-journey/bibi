"""``bibi-ctrl run`` — lokale On-Demand-Ausführung mit voller Scheduler-
Lifecycle (PLAN-3 §3.3b, DESIGN §6.3; PLAN-28).

Führt einen Job **sofort lokal** aus, gepinnt an diesen Knoten
(``jobs.pinned_host`` — kein anderer Worker kann ihn je reservieren), läuft
aber durch dieselbe Retry/Error/Deferred/Zombie-Maschine wie ein
Scheduler-Job. In-Process: ruft ``worker.run_pinned`` direkt, kein HTTP,
und pollt die ``jobs``-Zeile bis zu einem Terminalzustand.

**PLAN-38 (Entscheidung m.rau, 2026-07-27) — zwei Änderungen gegenüber
PLAN-28:** ``run`` läuft jetzt **in-place gegen den Live-Checkout** (der
frühere frische Worktree aus ``trunk`` entfällt) und ist damit **Client-only**
— auf einem Knoten mit ``scheduler``/``worker``-Rolle wird der Aufruf
abgelehnt statt still umgedeutet (``roles.forbids_local_run()``). Begründung:
wer lokal läuft, will den lokalen Stand laufen lassen und das Ergebnis als
``modified``/``untracked`` im Vault sehen; die Worktree-Isolation versteckte
auf dem eigenen Client nur die eigenen uncommitteten Änderungen vor dem
eigenen Job. Das frühere zweite Verb ``bibi-ctrl test`` hatte genau dieses
In-place-Verhalten und ist damit überflüssig — es bleibt als
Deprecation-Alias auf ``run`` bestehen.

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


def local_run_denied() -> str | None:
    """PLAN-38: Ablehnungstext, wenn dieser Knoten ``run`` nicht darf — sonst ``None``.

    Rollen aus der Config-Datei, genau wie ``daemon_cmd._resolve_roles()`` sie
    liest (``BIBI_ROLE``). Die CLI baut keinen Daemon, hat also kein
    aufgelöstes ``Roles`` — die gemeinsame Regel liegt trotzdem an einer
    Stelle (``roles.forbids_local_run()``), damit CLI und HTTP-Route nie
    auseinanderlaufen."""
    from bibi import config
    from bibi.daemon import roles as R
    blocked = R.forbids_local_run(R.parse_role_env(config.read_env().get("BIBI_ROLE", "")))
    return R.local_run_denied_message(blocked) if blocked else None


def _auto_sync_notice() -> str | None:
    """PLAN-38 Stufe 1: Hinweis, wenn das Ergebnis nicht liegen bleiben wird.

    Bei ``auto_sync: on`` committet der Lauf sein eigenes Ergebnis am Ende
    selbst (Stufe 2, ``wrapper._commit_in_place()``) und der Synchronizer
    pusht es — die Zusage „bleibt als modified/untracked liegen" gilt dann
    nicht. Das eigentliche Ärgernis wäre nicht der Commit, sondern dass er
    lautlos passiert; darum hier ansagen statt verbieten."""
    from bibi import state
    if not state.get_auto_sync():
        return None
    return ("Hinweis: auto_sync ist an — das Ergebnis dieses Laufs wird am Ende "
            "automatisch committet (mit Job-Provenienz) und vom Synchronizer gepusht. "
            "`bibi-ctrl sync off`, wenn du es vorher ansehen willst.")


def run(args: argparse.Namespace) -> int:
    if not args.slug and not args.command:
        print("bibi-ctrl run: <slug> oder --cmd nötig", file=sys.stderr)
        return 2
    denied = local_run_denied()
    if denied:
        print(f"bibi-ctrl run: {denied}", file=sys.stderr)
        return 2
    notice = _auto_sync_notice()
    if notice:
        print(notice, file=sys.stderr)
    try:
        # in_place=True (PLAN-38): lokaler Stand statt frischem trunk-Worktree.
        res = run_pinned(slug=args.slug, cmd=args.command, kind=args.kind, in_place=True)
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
        # Ohne Namen: ``list_pinned()`` nimmt beide Identitaeten dieses Knotens
        # (m.rau/bibi#88) — die stabile ``node_id`` neuer Zeilen und den
        # Hostnamen des Bestands. Mit ``gethostname()`` allein meldete die
        # Liste „(keine lokal gepinnten Jobs)" auf einem Knoten, der gerade
        # welche laufen hat.
        rows = job_db.list_pinned(conn)
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
