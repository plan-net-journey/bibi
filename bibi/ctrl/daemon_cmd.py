"""``bibi-ctrl daemon …`` — Daemon-Steuerung (DESIGN §4.2/§4.10, PLAN-2 §2.1/2.5).

- ``run``       — Daemon im Vordergrund starten (uvicorn), Rollen aus
                  ``BIBI_ROLE`` + CLI-Flags; baut die App aus den Rollen.
- ``install``   — Autostart-Unit/Plist schreiben (systemd/launchd).
- ``uninstall`` — Unit/Plist entfernen.
- ``status``    — laufenden Daemon über ``/-/health`` abfragen.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from bibi import config, state
from bibi.daemon import roles as R


def resolve_from_args(args: argparse.Namespace) -> tuple[R.Roles, list[str]]:
    """Rollen aus ``BIBI_ROLE`` + CLI-Flags auflösen und validieren.

    Gibt (Roles, Fehler) zurück. Fehler = harte Invarianten (§4.2) plus die
    noch nicht startbaren Rollen/Modifikatoren (ab Stufe 3.0 nur ``connect``).
    """
    active = R.parse_role_env(config.read_env().get("BIBI_ROLE", ""))
    for name in ("synchronizer", "scheduler", "worker"):
        if getattr(args, name, False):
            active.add(name)
    r = R.resolve(active, connect=args.connect, pull=args.pull, push=args.push)
    errs = R.validate(r)
    unsup = R.unsupported(r)
    if unsup:
        errs.append(
            f"Noch nicht implementiert: {', '.join(unsup)} — siehe PLAN-3 §3.6 "
            f"(--connect / Worker-Verbund)."
        )
    return r, errs


def run(args: argparse.Namespace) -> int:
    r, errs = resolve_from_args(args)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 2

    synchronizer = None
    if r.synchronizer:
        from bibi.daemon.synchronizer import Synchronizer
        if r.push:
            state.set_auto_sync(True)   # --push = stehende Push-Zustimmung an (§4.9)
        # Push-Fähigkeit immer an; der tatsächliche Push ist an auto_sync gegated.
        synchronizer = Synchronizer(push=True, pull=True, consent=state.get_auto_sync)

    worker = None
    if r.worker:
        import os

        from bibi.daemon.worker import Worker
        # --connect ⇒ Remote-Pull beim Scheduler (BIBI_SCHEDULER_URL: env > Config-Datei).
        url = None
        if r.connect:
            url = os.environ.get("BIBI_SCHEDULER_URL") or config.read_env().get("BIBI_SCHEDULER_URL")
        worker = Worker(
            connect=r.connect, scheduler_url=url,
            secret=os.environ.get("BIBI_CONNECT_SECRET"),
        )

    import uvicorn

    from bibi.daemon.app import create_app
    app = create_app(r, synchronizer=synchronizer, worker=worker)
    port = args.port or config.daemon_port()
    print(f"bibi daemon: rollen={r.active_names() or ['idle']} port={port}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=port)
    return 0


def install_cmd(args: argparse.Namespace) -> int:
    from bibi.daemon import install
    print(install.install(role=args.role))
    return 0


def uninstall_cmd(_: argparse.Namespace) -> int:
    from bibi.daemon import install
    print(install.uninstall())
    return 0


def status(args: argparse.Namespace) -> int:
    port = args.port or config.daemon_port()
    url = f"http://127.0.0.1:{port}/-/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 (localhost)
            print(json.dumps(json.loads(resp.read()), ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"daemon nicht erreichbar auf {url}: {e}", file=sys.stderr)
        return 1


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("daemon", help="Daemon: run/install/uninstall/status (§4.2/§4.10)")
    dsub = p.add_subparsers(dest="daemon_cmd")

    pr = dsub.add_parser("run", help="Daemon im Vordergrund starten")
    pr.add_argument("--host", default="127.0.0.1")
    pr.add_argument("--port", type=int, default=0, help="0 = aus BIBI_DAEMON_PORT/Default")
    pr.add_argument("--synchronizer", action="store_true")
    pr.add_argument("--scheduler", action="store_true")
    pr.add_argument("--worker", action="store_true")
    pr.add_argument("--connect", action="store_true")
    pr.add_argument("--pull", action="store_true")
    pr.add_argument("--push", action="store_true")
    pr.set_defaults(func=run)

    pi = dsub.add_parser("install", help="Autostart-Unit/Plist schreiben")
    pi.add_argument("--role", default=None, help="BIBI_ROLE für die Unit (sonst aus env)")
    pi.set_defaults(func=install_cmd)

    dsub.add_parser("uninstall", help="Autostart entfernen").set_defaults(func=uninstall_cmd)

    ps = dsub.add_parser("status", help="laufenden Daemon abfragen (/-/health)")
    ps.add_argument("--port", type=int, default=0)
    ps.set_defaults(func=status)

    p.set_defaults(func=lambda _a: (p.print_help() or 1))
