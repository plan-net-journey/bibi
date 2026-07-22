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
import logging
import os
import sys
import urllib.request

from bibi import config, repo, state
from bibi.daemon import activity
from bibi.daemon import roles as R


def resolve_from_args(args: argparse.Namespace) -> tuple[R.Roles, list[str]]:
    """Rollen aus ``BIBI_ROLE`` + CLI-Flags auflösen und validieren.

    Gibt (Roles, Fehler) zurück. Fehler = harte Invarianten (§4.2) plus die
    noch nicht startbaren Rollen/Modifikatoren (ab Stufe 3.0 nur ``connect``).
    """
    active = R.parse_role_env(config.read_env().get("BIBI_ROLE", ""))
    for name in ("synchronizer", "scheduler", "worker", "controller"):
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


def _apply_auto_sync_default(r: R.Roles) -> None:
    """Setzt ``auto_sync`` beim Daemon-Start, wo ein „off"-Default riskant wäre —
    getrennt von ``run()`` gehalten, damit die Entscheidung ohne echten
    uvicorn-Start testbar bleibt."""
    if r.push:
        state.set_auto_sync(True)   # --push = stehende Push-Zustimmung an (§4.9)
    elif r.scheduler and state.auto_sync_was_never_set():
        # Der Scheduler ist der EINE zentrale Knoten (DESIGN §4.2: "scheduler
        # exactly 1") — andere Knoten/Klone verlassen sich auf sein Origin als
        # Wahrheit. Überall sonst defaultet auto_sync auf "off"; hier wäre das
        # riskant (User-Fund 2026-07-07: Merge-Sweep-Commits blieben auf
        # sarasate unbegrenzt liegen, u. a. weil niemand je "sync on" gesetzt
        # hatte). Sicherer Default statt Hardcode — bewusstes "sync off" bleibt
        # weiterhin möglich, wird hier nur nicht mehr stillschweigend geerbt.
        state.set_auto_sync(True)


def _resolve_worker_name() -> str | None:
    """Explizite Knoten-Identität für Worker/Heartbeat: ``BIBI_WORKER_NAME``
    env > Config-Datei > ``None`` (⇒ Aufrufer fällt auf ``socket.gethostname()``
    zurück). Getrennt von ``run()`` gehalten, damit ohne echten uvicorn-Start
    testbar (wie ``_apply_auto_sync_default``). Nötig, sobald mehrere Instanzen
    (Host + Client) unter demselben Hostnamen laufen — sonst kollidieren ihre
    Team-Registry-Einträge auf demselben Dict-Key."""
    name = (os.environ.get("BIBI_WORKER_NAME", "").strip()
            or config.read_env().get("BIBI_WORKER_NAME", "").strip())
    return name or None


def run(args: argparse.Namespace) -> int:
    r, errs = resolve_from_args(args)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 2

    # Gemeinsamer sync_lock (PLAN-6 §3 D2): koordiniert Synchronizer-Pull/Push mit
    # dem Merge-back nach trunk im Scheduler — sie dürfen sich nicht überschneiden.
    import threading
    sync_lock = threading.Lock()

    synchronizer = None
    if r.synchronizer:
        from bibi.daemon.synchronizer import Synchronizer
        _apply_auto_sync_default(r)
        # Push-Fähigkeit immer an; der tatsächliche Push ist an auto_sync gegated.
        synchronizer = Synchronizer(push=True, pull=True, consent=state.get_auto_sync,
                                    lock=sync_lock, repo_root=repo.root())

    # --connect ⇒ Remote-Pull beim Scheduler (BIBI_SCHEDULER_URL: env > Config-Datei).
    connect_url = None
    if r.connect:
        connect_url = os.environ.get("BIBI_SCHEDULER_URL") or config.read_env().get("BIBI_SCHEDULER_URL")

    worker_name = _resolve_worker_name()

    worker = None
    if r.worker:
        from bibi.daemon.worker import Worker
        worker = Worker(
            connect=r.connect, scheduler_url=connect_url, worker_name=worker_name,
            secret=os.environ.get("BIBI_CONNECT_SECRET"),
        )

    # Heartbeat (A12) ist von der Worker-Rolle entkoppelt (User-Feedback
    # 2026-07-05): ein reiner Client (Synchronizer + --connect, kein Worker)
    # meldet sich sonst nie beim Scheduler — --connect wäre sonst wirkungslos.
    heartbeat = None
    if r.connect:
        from bibi.daemon.heartbeat import Heartbeat
        from bibi.daemon.scheduler_client import RemoteScheduler
        hb_client = RemoteScheduler(
            connect_url or "http://127.0.0.1:8769",
            secret=os.environ.get("BIBI_CONNECT_SECRET"),
        )
        heartbeat = Heartbeat(client=hb_client, repo_root=repo.root(), worker_name=worker_name,
                              role=",".join(r.active_names()))

    import uvicorn

    from bibi.daemon.app import create_app
    # Controller ruft die /-/-API über HTTP am **tatsächlichen** Bind-Port auf
    # (nicht config.daemon_port() — sonst zeigt --port ins Leere/auf einen Fremd-Daemon).
    port = args.port or config.daemon_port()
    # PLAN-30 Ebene 1 v2 (Fund Review-Runde 2, 2026-07-15): den tatsächlichen
    # Bind-Port hier im Prozess-Environment verankern, BEVOR irgendein Worker/
    # Wrapper-Subprozess gespawnt wird — der Wrapper braucht ihn für seinen
    # Merge-back-Trigger (worker.py::execute_reservation()) und liest bewusst
    # nur BIBI_DAEMON_PORT (nicht config.daemon_port()s Fallback-Kette, die für
    # genau diesen Zweck laut Kommentar oben nicht zuverlässig ist — z. B. bei
    # einem --connect-Client, dessen BIBI_SCHEDULER_URL auf einen ANDEREN Knoten
    # zeigt). Ohne dies bliebe BIBI_DAEMON_PORT leer, sobald --port ohne die Env-
    # Variable selbst gesetzt wurde (z. B. das aktuelle launchd-Plist auf macOS,
    # das --port in ProgramArguments einbettet, aber BIBI_DAEMON_PORT nicht in
    # EnvironmentVariables spiegelt) — der Wrapper-Trigger würde dann lautlos
    # gegen den falschen (Default-)Port laufen.
    os.environ["BIBI_DAEMON_PORT"] = str(port)
    app = create_app(r, synchronizer=synchronizer, worker=worker, heartbeat=heartbeat,
                     controller_base_url=f"http://{args.host}:{port}",
                     sync_lock=sync_lock)
    # Aktivitätslog verdrahten (§5.1): JSONL unter gitignored data/ + Klartext auf
    # stdout → der Vordergrund-Startschirm *ist* der Live-Tail.
    names = r.active_names() or ["idle"]
    level = activity.resolve_level(getattr(args, "log_level", None),
                                   os.environ.get("BIBI_LOG_LEVEL"))
    log_path = activity.setup_logging(role_names=names, level=level,
                                      log_dir=repo.root() / "data" / "daemon-log")
    activity.emit(logging.getLogger("bibi.daemon"), logging.INFO, "daemon.start",
                  role="daemon", roles=",".join(names), port=port,
                  loglevel=logging.getLevelName(level), log=str(log_path))
    uvicorn.run(app, host=args.host, port=port)
    return 0


def install_cmd(args: argparse.Namespace) -> int:
    from bibi.daemon import install
    print(install.install(role=args.role, connect=args.connect))
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


def logs(args: argparse.Namespace) -> int:
    """Aktivitätslog (§5.1) als Klartext anzeigen; ``-f`` folgt wie ``tail -f``."""
    path = repo.root() / "data" / "daemon-log" / activity.LOG_FILENAME
    if not path.exists():
        print(f"kein Aktivitätslog: {path} (läuft der Daemon schon?)", file=sys.stderr)
        return 1
    for ln in activity.tail_lines(path, args.lines):
        print(activity.render_jsonl_line(ln))
    if args.follow:
        import time
        with path.open("r", encoding="utf-8") as f:
            f.seek(0, 2)
            try:
                while True:
                    ln = f.readline()
                    if ln:
                        print(activity.render_jsonl_line(ln), flush=True)
                    else:
                        time.sleep(0.3)
            except KeyboardInterrupt:
                pass
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("daemon", help="Daemon: run/install/uninstall/status/logs (§4.2/§4.10)")
    dsub = p.add_subparsers(dest="daemon_cmd")

    pr = dsub.add_parser("run", help="Daemon im Vordergrund starten")
    pr.add_argument("--host", default="127.0.0.1")
    pr.add_argument("--port", type=int, default=0, help="0 = aus BIBI_DAEMON_PORT/Default")
    pr.add_argument("--synchronizer", action="store_true")
    pr.add_argument("--scheduler", action="store_true")
    pr.add_argument("--worker", action="store_true")
    pr.add_argument("--controller", action="store_true")
    pr.add_argument("--connect", action="store_true")
    pr.add_argument("--pull", action="store_true")
    pr.add_argument("--push", action="store_true")
    pr.add_argument("--log-level", default=None,
                    help="debug|info|warning|error (sonst BIBI_LOG_LEVEL, Default info)")
    pr.set_defaults(func=run)

    pi = dsub.add_parser("install", help="Autostart-Unit/Plist schreiben")
    pi.add_argument("--role", default=None, help="BIBI_ROLE für die Unit (sonst aus env)")
    pi.add_argument("--connect", action="store_true",
                    help="Heartbeat/--connect für die Unit aktivieren (kein BIBI_ROLE-Mitglied)")
    pi.set_defaults(func=install_cmd)

    dsub.add_parser("uninstall", help="Autostart entfernen").set_defaults(func=uninstall_cmd)

    ps = dsub.add_parser("status", help="laufenden Daemon abfragen (/-/health)")
    ps.add_argument("--port", type=int, default=0)
    ps.set_defaults(func=status)

    pl = dsub.add_parser("logs", help="Aktivitätslog anzeigen (§5.1); -f folgt live")
    pl.add_argument("-f", "--follow", action="store_true", help="wie tail -f")
    pl.add_argument("-n", "--lines", type=int, default=40, help="letzte N Zeilen (0 = alle)")
    pl.set_defaults(func=logs)

    p.set_defaults(func=lambda _a: (p.print_help() or 1))
