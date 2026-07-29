"""``bibi-ctrl job`` — Scheduler-Sicht (PLAN-3 §3.1, DESIGN §6.3).

Dünner HTTP-Client gegen den lokalen Daemon (``/-/job``, ``/-/rescan``). Kein
Model-Reasoning, kein DB-Zugriff — die Wahrheit hält der Scheduler. Drei
Unterbefehle: ``list`` / ``show <id>`` / ``rescan``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from bibi import config


def _base(args: argparse.Namespace) -> str:
    # PLAN-13 Stufe 13.0: explizites --port bleibt ein reiner Lokalitäts-
    # Override (z. B. eine zweite lokale Test-Instanz) — ohne --port die
    # volle BIBI_SCHEDULER_URL (Host + Port) statt blind 127.0.0.1, sonst
    # laufen Client-Knoten mit korrekt konfiguriertem, aber entferntem
    # Scheduler ins Leere (gegen ihren eigenen, falschen lokalen Daemon).
    port = getattr(args, "port", 0)
    if port:
        return f"http://127.0.0.1:{port}"
    return config.scheduler_base_url()


def _req(url: str, method: str = "GET") -> tuple[int, object]:
    # X-Bibi-Node-Id: seit dem Job-Control-Approval-Bug-Fix (2026-07-25) prüft
    # der Host jede /-/job*-Route gegen approved_nodes — ohne diesen Header
    # liefe ein bereits approvter Knoten (Mac, sarasate-client) hier ins Leere.
    # Seit Befund 4 (Live-Test PLAN-37, 2026-07-27) gilt das auch über Loopback,
    # sobald die node_id nicht die des angesprochenen Daemons selbst ist: auf
    # sarasate teilen sich mehrere Knoten eine Maschine, "127.0.0.1" heißt dort
    # gerade nicht "derselbe Knoten".
    headers = {"X-Bibi-Node-Id": config.node_id()}
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (localhost)
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or "null")
        except Exception:
            return e.code, None
    except OSError as e:
        print(f"daemon nicht erreichbar auf {url}: {e}", file=sys.stderr)
        return 0, None


def _fail(code: int, body: object, what: str) -> int:
    """Fehlschlag sichtbar machen statt stumm 1 zurückzugeben (Befund 5,
    Live-Test PLAN-37, 2026-07-27).

    Live beobachtet: `bibi-ctrl job list` auf einem nicht freigeschalteten
    Knoten gab **gar nichts** aus und beendete sich mit 1 — von "keine Jobs
    vorhanden" für den Menschen nicht zu unterscheiden. Gerade beim
    Approval-Modell ist die Begründung aber der ganze Punkt: der Nutzer muss
    erfahren, dass sein Knoten noch freizuschalten ist. FastAPIs
    ``HTTPException`` liefert sie in ``detail`` gleich mit.
    """
    detail = body.get("detail") if isinstance(body, dict) else None
    if code == 403:
        print(f"{what}: vom Scheduler abgewiesen — {detail or 'nicht freigeschaltet'}. "
              "Dieser Knoten muss am Host freigeschaltet werden (Nodes-Screen).",
              file=sys.stderr)
    elif code == 0:
        pass  # _req() hat die Ursache schon gemeldet
    else:
        print(f"{what}: HTTP {code}{f' — {detail}' if detail else ''}", file=sys.stderr)
    return 1


def _list(args: argparse.Namespace) -> int:
    url = f"{_base(args)}/-/job" + (f"?status={args.status}" if args.status else "")
    code, body = _req(url)
    if code == 501:
        print("Scheduler-Rolle nicht aktiv (Daemon ohne --scheduler).", file=sys.stderr)
        return 1
    if code != 200 or not isinstance(body, list):
        return _fail(code, body, "job list")
    if not body:
        print("(keine Jobs)")
        return 0
    width = max(len(j["slug"]) for j in body)
    for j in body:
        reason = f"  [{j['reason']}]" if j.get("reason") else ""
        print(f"{j['slug']:<{width}}  {j['status']:<9}  ({j['kind']})  {j['id']}{reason}")
    return 0


def _show(args: argparse.Namespace) -> int:
    code, body = _req(f"{_base(args)}/-/job/{args.id}")
    if code == 404:
        print(f"kein Job mit id {args.id}", file=sys.stderr)
        return 1
    if code != 200 or not isinstance(body, dict):
        return _fail(code, body, "job show")
    print(json.dumps(body, indent=2, ensure_ascii=False))
    return 0


def _start(args: argparse.Namespace) -> int:
    code, _ = _req(f"{_base(args)}/-/job/{args.id}/start", method="POST")
    if code == 404:
        print(f"kein Job mit id {args.id}", file=sys.stderr); return 1
    if code == 409:
        print(f"Job {args.id} ist nicht pending (nicht startbar)", file=sys.stderr); return 1
    if code != 200:
        return _fail(code, None, "job start")
    print(f"{args.id} → jetzt fällig")
    return 0


def _kill(args: argparse.Namespace) -> int:
    code, body = _req(f"{_base(args)}/-/job/{args.id}/kill", method="POST")
    if code == 404:
        print(f"kein Job mit id {args.id}", file=sys.stderr); return 1
    if code == 409:
        print(f"Job {args.id} läuft nicht (nicht killbar)", file=sys.stderr); return 1
    if code != 200:
        return _fail(code, body, "job kill")
    print(f"{args.id} killed")
    return 0


def _restart(args: argparse.Namespace) -> int:
    # restart = reset (Terminalzustand → pending, neu eingeplant, §5.6)
    code, body = _req(f"{_base(args)}/-/job/{args.id}/reset", method="POST")
    if code == 404:
        print(f"kein Job mit id {args.id}", file=sys.stderr); return 1
    if code == 409:
        print(f"Job {args.id} ist nicht in einem Terminalzustand", file=sys.stderr); return 1
    if code != 200:
        return _fail(code, body, "job restart")
    print(f"{args.id} → pending")
    return 0


def rescan(args: argparse.Namespace) -> int:
    code, body = _req(f"{_base(args)}/-/rescan", method="POST")
    if code != 200 or not isinstance(body, dict):
        print("rescan fehlgeschlagen (Scheduler aktiv?)", file=sys.stderr)
        return 1
    print(f"inserted={body['inserted']} updated={body['updated']} removed={body['removed']}")
    for e in body.get("errors", []):
        print(f"  error: {e['schedule_ref']}: {e['error']}", file=sys.stderr)
    for c in body.get("collisions", []):
        print(f"  collision: slug '{c['slug']}' in {', '.join(c['schedule_refs'])}", file=sys.stderr)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("job", help="Scheduler-Jobs listen/inspizieren/rescannen (§6.3)")
    p.add_argument("--port", type=int, default=0, help="0 = aus BIBI_DAEMON_PORT/Default")
    jsub = p.add_subparsers(dest="job_cmd")

    pl = jsub.add_parser("list", help="Jobs listen")
    pl.add_argument("--status", default=None, help="nach Status filtern")
    pl.set_defaults(func=_list)

    ps = jsub.add_parser("show", help="einen Job zeigen")
    ps.add_argument("id")
    ps.set_defaults(func=_show)

    pst = jsub.add_parser("start", help="pending-Job sofort fällig machen (§5.6)")
    pst.add_argument("id")
    pst.set_defaults(func=_start)

    pk = jsub.add_parser("kill", help="laufenden Job beenden (by_user)")
    pk.add_argument("id")
    pk.set_defaults(func=_kill)

    pr = jsub.add_parser("restart", help="Terminal-Job neu einplanen (reset)")
    pr.add_argument("id")
    pr.set_defaults(func=_restart)

    jsub.add_parser("rescan", help="Vault neu scannen").set_defaults(func=rescan)
    p.set_defaults(func=lambda _a: (p.print_help() or 1))


def register_rescan(sub: argparse._SubParsersAction) -> None:
    """Top-Level ``bibi-ctrl rescan`` — selbe Wirkung wie ``job rescan``, auffindbarer."""
    p = sub.add_parser("rescan", help="Vault neu scannen + erfassen (mit Ausgabe)")
    p.add_argument("--port", type=int, default=0, help="0 = aus BIBI_DAEMON_PORT/Default")
    p.set_defaults(func=rescan)
