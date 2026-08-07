"""``bibi-ctrl daemon …`` — Daemon-Steuerung (DESIGN §4.2/§4.10, PLAN-2 §2.1/2.5).

- ``run``       — Daemon im Vordergrund starten (uvicorn), Rollen aus
                  ``BIBI_ROLE`` + CLI-Flags; baut die App aus den Rollen.
- ``install``   — Autostart-Unit/Plist schreiben (systemd/launchd).
- ``uninstall`` — Unit/Plist entfernen.
- ``status``    — laufenden Daemon über ``/-/health`` abfragen.
- ``stop``      — laufenden Daemon beenden (SIGTERM, m.rau/bibi#58).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import urllib.request

from bibi import config, repo, state
from bibi.daemon import activity, boot_signal
from bibi.daemon import roles as R


def resolve_from_args(args: argparse.Namespace) -> tuple[R.Roles, list[str]]:
    """Rollen aus ``BIBI_ROLE`` + CLI-Flags auflösen und validieren.

    Gibt (Roles, Fehler) zurück. Fehler = harte Invarianten (§4.2) plus die
    noch nicht startbaren Rollen/Modifikatoren (ab Stufe 3.0 nur ``connect``).
    """
    # ``or config.KEYS[...]`` statt ``.get(key, default)``: ``read_env()``
    # wendet keine Defaults an, und ein leerer Wert in der Datei soll dasselbe
    # bedeuten wie eine fehlende Zeile. Ohne diesen Rückfall galt der
    # dokumentierte Default ``BIBI_ROLE=synchronizer`` nur für Knoten, die
    # durch ``init`` gegangen sind — ein frischer ``daemon run`` löste zu
    # *keiner* Rolle auf und startete einen Daemon, der nichts tut. Aufgefallen
    # beim Schärfen von m.rau/bibi#163, dessen Analyse die Invariante
    # ausdrücklich dem Default zuschreibt; sie tat es nur nicht überall.
    active = R.parse_role_env(
        config.read_env().get("BIBI_ROLE") or config.KEYS["BIBI_ROLE"])
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
    """Explizite Knoten-Identität für Worker/Heartbeat: ``BIBI_NODE_NAME`` env >
    Config-Datei (``BIBI_NODE_NAME``, mit ``BIBI_WORKER_NAME`` als Fallback für
    noch nicht migrierte Knoten, PLAN-34) > ``None`` (⇒ Aufrufer fällt auf
    ``socket.gethostname()`` zurück). Getrennt von ``run()`` gehalten, damit
    ohne echten uvicorn-Start testbar (wie ``_apply_auto_sync_default``).
    Funktionsname bleibt bewusst ``_resolve_worker_name`` — sie liefert weiter
    den internen ``worker_name``-Parameter für ``Worker``/``Heartbeat``, nur
    die Config-Quelle wurde umbenannt (PLAN-34 Entscheidung 1: nur die nach
    außen sichtbare Ebene, nicht die Worker-Rollen-interne)."""
    env = config.read_env()
    name = (os.environ.get("BIBI_NODE_NAME", "").strip()
            or env.get("BIBI_NODE_NAME", "").strip()
            or os.environ.get("BIBI_WORKER_NAME", "").strip()
            or env.get("BIBI_WORKER_NAME", "").strip())
    return name or None


#: ``--port auto`` / ``BIBI_DAEMON_PORT=auto`` — der Daemon sucht sich seinen
#: Port selbst (m.rau/bibi#45).
AUTO_PORT = "auto"


def _is_auto_port(value: str | int | None) -> bool:
    """Soll der Port automatisch gesucht werden?

    Das Flag gewinnt über die Umgebung; ohne Flag zählt ``BIBI_DAEMON_PORT=auto``,
    damit eine Autostart-Unit dieselbe Wahl treffen kann, ohne dass ihr Aufrufer
    eine Zahl kennt. Getrennt von ``run()`` gehalten, damit ohne echten
    uvicorn-Start testbar (wie ``_resolve_shutdown_timeout``).
    """
    raw = "" if value is None else str(value).strip().lower()
    if raw:
        return raw == AUTO_PORT
    return os.environ.get("BIBI_DAEMON_PORT", "").strip().lower() == AUTO_PORT


def _explicit_port(value: str | int | None) -> int:
    """``--port <n>`` als Zahl. ``0`` bei fehlendem/ungültigem Wert — der
    Aufrufer fällt dann auf ``config.daemon_port()`` zurück, unverändert zum
    Verhalten vor der Port-Automatik."""
    if value is None:
        return 0
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


SHUTDOWN_TIMEOUT_DEFAULT_S = 10


def _resolve_shutdown_timeout() -> int:
    """Frist in Sekunden, die uvicorn beim SIGTERM auf offene Verbindungen
    wartet: ``BIBI_SHUTDOWN_TIMEOUT_S`` env > Config-Datei > Default 10.

    Der Wert existiert wegen des Event-Bus (PLAN-36): ``/-/events`` ist ein
    SSE-Strom, der von sich aus nie schließt. Uvicorns Default (keine Frist)
    heißt „unbegrenzt warten" — damit hielt **jeder offene Browser-Tab** den
    Daemon am Neustart fest. Unter systemd fiel das nur als Verzögerung auf
    (``TimeoutStopSec``-Default 90 s, dann SIGKILL: gemessen 1m30s gegen 0,17s
    ohne Tab), unter launchd war es ein stiller Ausfall — der Prozess nahm
    keine Verbindungen mehr an, lebte aber weiter, also sah ``KeepAlive``
    nichts zum Respawnen (Case-Befund 2026-07-28g).

    Bewusst **kein** :data:`config.KEYS`-Eintrag: niemand soll beim ``init``-
    Interview eine Shutdown-Frist eintippen müssen. In der env-Datei wirkt der
    Wert trotzdem, weil ``read_env()`` ungefiltert parst — der Notausstieg für
    einen Knoten mit ungewöhnlich langen In-flight-Requests bleibt also da.

    ``0`` ist gültig (sofort abbrechen). Ungültiges fällt auf den Default
    zurück, nie auf ``None``: unbegrenztes Warten ist genau der Fehler.
    Getrennt von ``run()`` gehalten, damit ohne echten uvicorn-Start testbar
    (wie ``_apply_auto_sync_default`` und ``_resolve_worker_name``).
    """
    raw = (os.environ.get("BIBI_SHUTDOWN_TIMEOUT_S", "").strip()
           or config.read_env().get("BIBI_SHUTDOWN_TIMEOUT_S", "").strip())
    if raw:
        try:
            secs = int(raw)
        except ValueError:
            return SHUTDOWN_TIMEOUT_DEFAULT_S
        if secs >= 0:
            return secs
    return SHUTDOWN_TIMEOUT_DEFAULT_S


#: ``daemon run`` bricht ab, weil auf diesem Checkout schon einer läuft
#: (m.rau/bibi#155). Eigener Code, damit ein Aufrufer ihn von einem Rollen-/
#: Eingabefehler (2) unterscheiden kann: hier ist nichts falsch eingegeben,
#: hier ist nur nichts zu tun.
EXIT_ALREADY_RUNNING = 3

#: Der Lock, der „genau einer" durchsetzt — unter ``data/``, also je Checkout
#: getrennt wie Job-DB und Portdatei. Bewusst **nicht** ``session-start.lock``:
#: den hält ``session.main()`` bereits, während es ``daemon run`` als Subprozess
#: startet; derselbe Lock ließe den regulären Weg über ``bibi`` scheitern.
RUN_LOCK_FILENAME = "daemon-run.lock"


def _acquire_run_lock():
    """Das exklusive Recht, auf diesem Checkout ein Daemon zu **sein** — oder
    ``None``, wenn es schon jemand hat.

    **Der Schutz gehört hierher, weil hier jeder Startweg vorbeikommt.** Was es
    vorher gab, deckte je einen ab: ``session._acquire_start_lock()`` nur den
    über ``bibi``, ``portfile.clear()`` nur das Aufräumen. ``run()`` selbst
    prüfte gar nichts — es band einen Port und schrieb die Portdatei.

    Vor der Port-Automatik (m.rau/bibi#45) erledigte das ein ``EADDRINUSE``:
    der zweite Daemon wollte denselben festen Port und starb. Kein entworfener
    Schutz, aber ein wirksamer, und sein Wegfall wurde nie ersetzt — seither
    findet der zweite geräuschlos einen freien Port. Am 2026-08-05 waren es
    fünf Startpaare an einem Nachmittag, aus jeweils *einem* Sitzungsstart.

    **Warum ein Lock und nicht die Portdatei.** Die Portdatei ist die naheliegende
    Quelle und als alleinige Prüfung zu spät: ``run()`` schreibt sie erst kurz
    vor ``server.run()``, nach Rollenauflösung, Synchronizer, Worker, Heartbeat
    und den uvicorn-Importen. Die beobachteten Startpaare lagen 1 bis 16 Sekunden
    auseinander — mitten in diesem Fenster. Der Lock dagegen wird vor allem
    anderen genommen und bis zum Prozessende gehalten.

    ``LOCK_NB``: der zweite Daemon soll **abbrechen**, nicht warten. Warten hieße,
    ihn am Leben zu lassen, bis der erste endet — und dann liefe er los, obwohl
    der Grund seines Starts längst vorbei ist. Der blockierende Lock in
    ``session.py`` will das Gegenteil und ist deshalb dort richtig: wer über
    ``bibi`` kommt, wartet kurz und hängt sich danach an den laufenden an.

    ``flock`` statt einer selbstgebauten Lock-Datei, aus demselben Grund wie
    dort: der Kernel gibt ihn beim Prozessende von selbst frei, ein ``kill -9``
    hinterlässt also keine verwaiste Sperre.
    """
    import fcntl
    root = repo.root_or_none()
    if root is None:
        return None, False   # kein Repo: nichts zu schützen (s. portfile.port_file)
    path = root / "data" / RUN_LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None, True
    return fh, False


def _release_run_lock(fh) -> None:
    """Den Lock loslassen. Der Kernel täte es beim Prozessende ohnehin — hier
    steht es trotzdem, weil ``run()`` auch **zurückkehrt**, ohne dass der Prozess
    endet: der Boot-Signal-Zweig, und jeder Test, der ``run()`` direkt aufruft.
    Auf CPythons Refcount zu bauen hieße, den Schutz an ein Implementierungs-
    detail zu hängen."""
    if fh is None:
        return
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _refuse_second_daemon() -> str:
    """Die Meldung zum Abbruch — mit PID und Port, wenn sie zu haben sind.

    Der Lock weiß nur *dass* jemand da ist. Wer, steht in der Portdatei — und die
    fehlt genau dann, wenn der andere noch startet. Beide Fälle bekommen einen
    Satz, statt den zweiten mit einer Lücke im ersten abzufertigen.
    """
    from bibi.daemon import portfile
    entry = portfile.read()
    wer = (f" (PID {entry['pid']}, Port {entry['port']})" if entry
           else " (er startet gerade — noch ohne Portdatei)")
    return (f"Auf diesem Checkout läuft bereits ein Daemon{wer} — "
            f"es startet keiner daneben.\n"
            f"Den laufenden benutzen (`bibi-ctrl status`), oder ihn beenden.")


def run(args: argparse.Namespace) -> int:
    r, errs = resolve_from_args(args)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 2

    # Vor allem anderen: es darf nur einen geben (m.rau/bibi#155). Vor dem Bind
    # und vor jedem Thread — ein Abbruch danach hätte dem laufenden Daemon schon
    # in den Zustand getreten.
    run_lock, taken = _acquire_run_lock()
    if not taken:
        # Zweite Linie: ein Daemon, der **keinen** Lock hält. Das ist kein
        # Sonderfall, sondern der Normalfall beim Ausrollen dieser Änderung
        # selbst — der laufende Prozess stammt aus der Version davor. Ihn nur
        # am Lock zu erkennen hieße, den ersten Neustart nach dem Upgrade
        # ungeschützt zu lassen, also genau den, bei dem es zählt.
        from bibi.daemon import portfile
        entry = portfile.read()
        taken = entry is not None and entry.get("pid") != os.getpid()
        if taken:
            _release_run_lock(run_lock)
    if taken:
        print(_refuse_second_daemon(), file=sys.stderr)
        return EXIT_ALREADY_RUNNING

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
                              role=",".join(r.active_names()),
                              # m.rau/bibi#44: dieselbe Quelle wie für
                              # create_app()/portfile.write() unten — der
                              # startende Prozess ist der einzige, der es weiß.
                              session=bool(getattr(args, "session", False)))

    import uvicorn

    from bibi.daemon import portfile
    from bibi.daemon.app import create_app
    # Controller ruft die /-/-API über HTTP am **tatsächlichen** Bind-Port auf
    # (nicht config.daemon_port() — sonst zeigt --port ins Leere/auf einen Fremd-Daemon).
    #
    # ``--port auto`` (m.rau/bibi#45): der Daemon sucht sich selbst einen freien
    # Port. Der Socket bleibt dabei offen und wird an uvicorn durchgereicht —
    # sonst gäbe es zwischen „Nummer gelesen" und „uvicorn bindet" ein Fenster,
    # in dem ein anderer Prozess denselben Port belegen kann. Genau der Fall,
    # für den die Automatik da ist (zwei Instanzen auf einer Maschine), ist auch
    # der Fall, in dem sich zwei Starts überschneiden können.
    sock = None
    if _is_auto_port(getattr(args, "port", None)):
        sock, port = portfile.bind_free(args.host)
    else:
        port = _explicit_port(getattr(args, "port", None)) or config.daemon_port()
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
                     sync_lock=sync_lock,
                     # m.rau/bibi#46: --session sagt „dieser Daemon gehört
                     # Sitzungen und endet mit der letzten". Ein Daemon aus
                     # einer Autostart-Unit trägt das Flag nie und wird deshalb
                     # von keiner Sitzung gestoppt, egal wie der Zähler steht.
                     session_scoped=bool(getattr(args, "session", False)))
    # Aktivitätslog verdrahten (§5.1): JSONL unter gitignored data/ + Klartext auf
    # stdout → der Vordergrund-Startschirm *ist* der Live-Tail.
    names = r.active_names() or ["idle"]
    level = activity.resolve_level(getattr(args, "log_level", None),
                                   os.environ.get("BIBI_LOG_LEVEL"))
    log_path = activity.setup_logging(role_names=names, level=level,
                                      log_dir=repo.root() / "data" / "daemon-log")
    grace = _resolve_shutdown_timeout()
    activity.emit(logging.getLogger("bibi.daemon"), logging.INFO, "daemon.start",
                  role="daemon", roles=",".join(names), port=port,
                  loglevel=logging.getLevelName(level), log=str(log_path),
                  shutdown_grace_s=grace)
    # Boot-Signale (m.rau/bibi#39): Phase 2 des Doppel-Neustarts. Hier — VOR
    # uvicorn.run() — ist das ein gewöhnlicher Programmablauf: pullen bzw. venv
    # wegwerfen, dann zurückkehren. Kein Server, kein Signal-Handling, keine
    # Dienste, die gleich wieder gestoppt werden müssten. Der Supervisor
    # (Restart=always/KeepAlive) startet erneut, und erst dieser dritte Prozess
    # läuft mit dem neuen Stand, weil `uv run` das venv beim Hochfahren gegen
    # die inzwischen gepullte Lock synct.
    if boot_signal.apply_and_clear():
        if sock is not None:
            sock.close()
        _release_run_lock(run_lock)
        return 0
    # Den tatsächlichen Port ablegen (m.rau/bibi#45), damit ihn andere Prozesse
    # dieses Checkouts finden: ``bibi-ctrl status`` im zweiten Terminal, die
    # Statusline, der Browser. Erst hier, nach dem Boot-Signal-Zweig — der kehrt
    # zurück, ohne je einen Server zu starten, ein Eintrag wäre dort gelogen.
    portfile.write(port, host=args.host, roles=",".join(names),
                   session=bool(getattr(args, "session", False)))
    # timeout_graceful_shutdown: ohne die Frist wartet uvicorn beim SIGTERM
    # unbegrenzt auf offene Verbindungen — und der SSE-Strom /-/events schließt
    # nie von selbst (s. _resolve_shutdown_timeout()).
    #
    # ``Config``+``Server`` statt ``uvicorn.run()``: das ist dessen Innenleben
    # (``uvicorn.run`` baut genau diese beiden, plus Reload/Worker-Zweige, die
    # hier nie greifen) — nur nimmt ``Server.run()`` einen vorgebundenen Socket
    # entgegen, und den braucht die Port-Automatik oben.
    server = _stream_closing_server(
        uvicorn.Config(app, host=args.host, port=port,
                       timeout_graceful_shutdown=grace),
        getattr(app.state, "bus", None))
    try:
        server.run(sockets=[sock] if sock is not None else None)
    finally:
        # Netz für die Wege, die den Server nie erreichen (Bind schlägt fehl,
        # Konfigurationsfehler). Der Normalfall — SIGTERM — kommt hier NICHT an:
        # uvicorn feuert das eingefangene Signal am Ende von ``capture_signals()``
        # erneut, nachdem es ``SIG_DFL`` wiederhergestellt hat, und der Prozess
        # ist damit sofort weg. Deshalb räumt der ``lifespan``-Finally die
        # Portdatei (s. dort); diese Zeile ist der Gürtel dazu, nicht der
        # Hosenträger. Gegen SIGKILL hilft ohnehin nur die PID-Prüfung beim Lesen.
        portfile.clear()
        _release_run_lock(run_lock)
    return 0


def _stream_closing_server(config, bus):
    """``uvicorn.Server``, der beim Signal zuerst die SSE-Ströme schließt (#176).

    **Die Reihenfolge ist der ganze Punkt.** uvicorns Frist für offene
    Verbindungen beginnt mit ``handle_exit``; wer die Ströme erst danach
    schließt, hat sie schon hineinlaufen lassen. Am Ende der Frist bricht
    uvicorn die Task ab und protokolliert den Abbruch als *„Exception in ASGI
    application"* — rund fünfzig Zeilen Stacktrace für einen geplanten Vorgang.

    Was hier **nicht** passiert: die Frist abschaffen. Sie bleibt unverändert
    und deckt weiterhin alles, was sich nicht von selbst schließt; ``super()``
    läuft unangetastet weiter. Entlastet wird sie nur um den einen Strom, von
    dem wir wissen, dass er nie von selbst endet.

    Eine Fabrik statt einer Modulklasse, weil ``uvicorn`` in diesem Modul
    bewusst spät importiert wird — ein ``bibi-ctrl status`` soll den ASGI-Stack
    nicht laden müssen.
    """
    import uvicorn

    class _StreamClosingServer(uvicorn.Server):
        def handle_exit(self, sig, frame):  # noqa: D102 — Vertrag von uvicorn
            if bus is not None:
                bus.begin_shutdown()
            super().handle_exit(sig, frame)

    return _StreamClosingServer(config)


def install_cmd(args: argparse.Namespace) -> int:
    from bibi.daemon import install
    print(install.install(role=args.role, connect=args.connect,
                          port=getattr(args, "port", None) or None))
    return 0


def uninstall_cmd(_: argparse.Namespace) -> int:
    from bibi.daemon import install
    print(install.uninstall())
    return 0


def status(args: argparse.Namespace) -> int:
    # m.rau/bibi#58: ohne ``--port`` und ohne laufenden Daemon fiel
    # ``config.daemon_port()`` auf den Default 8769 zurück, und die
    # Fehlermeldung nannte ihn — als sei dort geprüft worden. Auf einem Client
    # mit ``--port auto`` ist 8769 nie der Port. Fehlt die Portdatei, lautet die
    # Auskunft „kein Daemon", nicht „nicht erreichbar unter einer Adresse, die
    # nie eine war".
    if args.port:
        port = args.port
    else:
        from bibi.daemon import portfile
        entry = portfile.read()
        if entry is None:
            print("kein laufender Daemon in diesem Repo (keine Portdatei unter "
                  "data/). Eine bestimmte Adresse lässt sich mit --port abfragen.",
                  file=sys.stderr)
            return 1
        port = entry["port"]
    url = f"http://127.0.0.1:{port}/-/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 (localhost)
            print(json.dumps(json.loads(resp.read()), ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"daemon nicht erreichbar auf {url}: {e}", file=sys.stderr)
        return 1


def stop(args: argparse.Namespace) -> int:
    """Den Daemon dieses Repos beenden (m.rau/bibi#58).

    ``daemon`` kannte ``run``, ``install``, ``uninstall``, ``status`` und
    ``logs``. Wer die Vordergrund-Variante gestartet hatte — die der
    ``bibi-setup``-Skill für Container und für Knoten ohne Init-System vorsieht —
    hatte danach einen Prozess, den er nur über ``kill <pid>`` wieder loswurde;
    ``uninstall`` half nicht, weil es keine Unit gab.

    Über SIGTERM, aus demselben Grund wie beim Sitzungsende und beim
    Restart-Endpunkt: nur so greifen uvicorns ``timeout_graceful_shutdown`` und
    der Job-Drain im ``lifespan``-Finally. Ein ``kill -9`` nähme beide Zusagen.
    """
    from bibi.daemon import portfile
    entry = portfile.read()
    if entry is None:
        print("kein laufender Daemon in diesem Repo.", file=sys.stderr)
        return 1
    pid = entry.get("pid")
    if not pid:
        print("Portdatei ohne PID — von Hand nachsehen (data/daemon-port.json).",
              file=sys.stderr)
        return 1
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        print(f"PID {pid} läuft nicht mehr — die Portdatei war veraltet.",
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f"konnte PID {pid} nicht beenden: {e}", file=sys.stderr)
        return 1
    print(f"SIGTERM an PID {pid} (Port {entry['port']}) — der Daemon fährt "
          "herunter und lässt laufende Jobs auslaufen.")
    return 0


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
    pr.add_argument("--port", default=None,
                    help="Portnummer, 'auto' = freien Port suchen und ablegen "
                         "(m.rau/bibi#45); ohne Angabe BIBI_DAEMON_PORT/Default")
    pr.add_argument("--synchronizer", action="store_true")
    pr.add_argument("--scheduler", action="store_true")
    pr.add_argument("--worker", action="store_true")
    pr.add_argument("--controller", action="store_true")
    pr.add_argument("--connect", action="store_true")
    pr.add_argument("--pull", action="store_true")
    pr.add_argument("--push", action="store_true")
    pr.add_argument("--session", action="store_true",
                    help="dieser Daemon gehört Sitzungen und fährt herunter, "
                         "wenn die letzte endet (m.rau/bibi#46)")
    pr.add_argument("--log-level", default=None,
                    help="debug|info|warning|error (sonst BIBI_LOG_LEVEL, Default info)")
    pr.set_defaults(func=run)

    pi = dsub.add_parser("install", help="Autostart-Unit/Plist schreiben")
    pi.add_argument("--port", type=int, default=0,
                    help="fester Lauschport der Unit (m.rau/bibi#15); ohne "
                         "Angabe aus BIBI_DAEMON_PORT/BIBI_SCHEDULER_URL/Default. "
                         "Kein 'auto': eine Unit braucht eine Nummer, die auch "
                         "morgen noch gilt")
    pi.add_argument("--role", default=None, help="BIBI_ROLE für die Unit (sonst aus env)")
    pi.add_argument("--connect", action="store_true",
                    help="Heartbeat/--connect für die Unit aktivieren (kein BIBI_ROLE-Mitglied)")
    pi.set_defaults(func=install_cmd)

    dsub.add_parser("uninstall", help="Autostart entfernen").set_defaults(func=uninstall_cmd)

    # m.rau/bibi#58: das Gegenstück zu ``run``. ``uninstall`` entfernt die Unit
    # und half einem von Hand gestarteten Daemon deshalb nicht — den wurde man
    # bis dahin nur per ``kill`` los.
    dsub.add_parser(
        "stop", help="laufenden Daemon dieses Repos beenden (SIGTERM)"
    ).set_defaults(func=stop)

    ps = dsub.add_parser("status", help="laufenden Daemon abfragen (/-/health)")
    ps.add_argument("--port", type=int, default=0)
    ps.set_defaults(func=status)

    pl = dsub.add_parser("logs", help="Aktivitätslog anzeigen (§5.1); -f folgt live")
    pl.add_argument("-f", "--follow", action="store_true", help="wie tail -f")
    pl.add_argument("-n", "--lines", type=int, default=40, help="letzte N Zeilen (0 = alle)")
    pl.set_defaults(func=logs)

    p.set_defaults(func=lambda _a: (p.print_help() or 1))
