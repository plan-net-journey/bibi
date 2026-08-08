"""FastAPI-Skelett des Daemons (DESIGN §4.2/§4.8, PLAN-2 §2.2).

Alles, was der Daemon serviert, liegt unter dem reservierten Präfix ``/-/`` —
so kollidiert es nie mit App-Inhalts-Routen. **Singular**, HTTP nutzt ``status``
(getrennt vom ``/state``-Skill), Verben als Aktions-Subpfad.

``create_app(roles, synchronizer=None)`` ist eine Factory — testbar ohne realen
Synchronizer und ohne globale Rollen-Erkennung. Der Daemon-Entrypoint
(``daemon_cmd``) baut die App aus den aufgelösten Rollen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from bibi import config, repo, state
from bibi.daemon import (activity, boot_signal, job_db, mergeback, node_info, openapi,
                         output_format)
from bibi.daemon import worker as worker_mod  # Modul-Alias (bibi.daemon.app.worker ist eine Worker-Instanz)
from bibi.schedule import models
from bibi.daemon.openapi import (
    JobReservation, JobView, JournalEntryView, KillRequest, NextRequest, RestartRequest,
    RunRequest, StatusReport, WorkerHeartbeat, WorkerView,
)
from bibi.daemon import roles as roles_mod  # Modul-Alias (der Parameter heißt `roles`)
from bibi.daemon.roles import Roles
from bibi.daemon.worker import Worker, run_pinned
from bibi.daemon.worker_registry import WorkerRegistry
from bibi.schedule.lifecycle import TERMINAL
from bibi.schedule.models import Status
from bibi.wrapper import output

log = logging.getLogger("bibi.daemon")

#: Ping-Intervall des /-/events-Stroms (PLAN-36 Stufe 36.1) — Kommentarzeilen
#: gegen Verbindungsabrisse bei Sendepausen (s. Route-Kommentar dort).
EVENTS_PING_S = 15.0

#: Frist des Drains beim **regulären** Herunterfahren (m.rau/bibi#49).
#:
#: Deutlich kürzer als die 120 s des Restart-Endpunkts, und der Grund ist keine
#: Geschmacksfrage: dort läuft der Drain **im Request**, bevor sich der Prozess
#: selbst SIGTERM schickt — die Uhr des Supervisors tickt da noch gar nicht.
#: Hier ist sie längst angelaufen. launchd killt nach ``ExitTimeOut`` (Default
#: **20 s**), systemd nach ``TimeoutStopSec`` (Default 90 s), und davor liegt
#: noch uvicorns eigene Frist für offene Verbindungen (Default hier 10 s, s.
#: ``daemon_cmd._resolve_shutdown_timeout()``). Ein Drain, der über das Budget
#: hinausläuft, endet im SIGKILL — also genau in dem unkontrollierten Abbruch,
#: den er verhindern soll.
#:
#: 10 s deckt den häufigen Fall (Worktree-Setup, Sekunden) und passt auch auf
#: einem Knoten, dessen Unit noch aus der Zeit vor dieser Änderung stammt.
#: ``install.py`` setzt die Fristen für neue Units jetzt ausdrücklich auf 90 s —
#: wer mehr braucht, hebt danach ``BIBI_DRAIN_TIMEOUT_S``.
DRAIN_TIMEOUT_DEFAULT_S = 10.0


def _resolve_drain_timeout() -> float:
    """``BIBI_DRAIN_TIMEOUT_S`` env > ``~/.config/bibi/env`` > Default.

    Bewusst **kein** :data:`config.KEYS`-Eintrag — niemand soll beim ``init``-
    Interview eine Drain-Frist eintippen müssen; in der env-Datei wirkt der Wert
    trotzdem, weil ``read_env()`` ungefiltert parst. Dieselbe Bauart wie
    ``_resolve_shutdown_timeout()``, aus demselben Grund.

    ``0`` ist gültig (nicht warten). Ungültiges fällt auf den Default zurück.
    """
    raw = (os.environ.get("BIBI_DRAIN_TIMEOUT_S", "").strip()
           or config.read_env().get("BIBI_DRAIN_TIMEOUT_S", "").strip())
    if raw:
        try:
            secs = float(raw)
        except ValueError:
            return DRAIN_TIMEOUT_DEFAULT_S
        if secs >= 0:
            return secs
    return DRAIN_TIMEOUT_DEFAULT_S


async def _drain_for_shutdown(w, *, timeout: float, label: str) -> dict:
    """``worker.drain()`` mit einem Ausweg für den Menschen (m.rau/bibi#49).

    Warten ist der Default, Abbruch die ausdrückliche Entscheidung — so hat
    m.rau die Aufteilung vorgegeben. Nur: ein zweites ``CTRL+C`` reicht dafür
    nicht von selbst. uvicorns Signal-Handler setzt beim zweiten Signal
    lediglich ``force_exit``; das bricht keine Coroutine ab, die gerade im
    ``lifespan``-Finally läuft — der Nutzer drückt also und nichts passiert.

    Deshalb übernimmt dieser Block SIGINT **für die Dauer des Drains** und gibt
    ihn danach zurück. Das Fenster ist eng und der Handler tut genau eines: ein
    Event setzen, gegen das der Drain rennt.

    Läuft der Prozess nicht im Haupt-Thread (``TestClient`` fährt den Lifespan
    in einem eigenen), ist ``signal.signal`` nicht erlaubt — dann eben ohne
    Abbruchweg, statt am Aufräumen zu scheitern.

    **Wirft nie.** Der Aufrufer ist ein ``finally``-Block, in dem danach noch
    Heartbeat, Rescanner, Sweeper und Synchronizer gestoppt werden. Eine
    Exception hier würde all das überspringen — der Drain soll das Aufräumen
    verbessern, nicht es kippen. Dieselbe Haltung wie beim generischen
    Exception-Handler der App: ein Fehler in einem Teil darf den Daemon nicht
    mitnehmen.
    """
    if w is None or not hasattr(w, "drain"):
        return {"drained": True, "starting": 0}
    loop = asyncio.get_running_loop()
    interrupted = asyncio.Event()
    previous = None
    try:
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT,
                      lambda *_: loop.call_soon_threadsafe(interrupted.set))
    except (ValueError, OSError):
        previous = None  # kein Haupt-Thread — kein Übernehmen, kein Zurückgeben

    drain = asyncio.create_task(w.drain(timeout=timeout))
    waiter = asyncio.create_task(interrupted.wait())
    try:
        done, _pending = await asyncio.wait(
            {drain, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if drain in done:
            return drain.result()
        drain.cancel()
        try:
            await drain
        except asyncio.CancelledError:
            pass
        activity.emit(log, logging.WARNING, "worker.drain",
                      "Drain abgebrochen (CTRL+C) — Jobs im Setup überstehen das "
                      "Ende womöglich nicht", role="worker", which=label)
        return {"drained": False, "starting": w.starting_count(), "interrupted": True}
    except Exception as exc:  # noqa: BLE001 — s. „Wirft nie" im Docstring
        activity.emit(log, logging.WARNING, "worker.drain",
                      f"Drain fehlgeschlagen, Aufräumen läuft weiter: {exc}",
                      role="worker", which=label)
        return {"drained": False, "starting": 0, "error": str(exc)}
    finally:
        waiter.cancel()
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def _merge_back(branch: str, *, sync_lock=None, synchronizer=None) -> None:
    """``agent/<slug>`` nach trunk mergen (PLAN-6) und — bei Zustimmung — pushen.

    Defensiv: jeder Fehler bleibt hier (der Lauf ist bereits terminal ``complete``,
    der Commit über den Branch erreichbar). Konflikt → Ebene-2-Quarantäne, ab
    3 Fehlschlägen sichtbar über Ebene 3 (``/state``, Statuszeile, Git-Kachel)."""
    slug = branch.removeprefix("agent/")
    try:
        res = mergeback.merge_back(repo_root=repo.root(), slug=slug, lock=sync_lock)
    except Exception as exc:  # nie den Status-Report killen
        activity.emit(log, logging.ERROR, "worker.merge_error",
                      "Merge-back fehlgeschlagen", role="scheduler", slug=slug, error=str(exc))
        return
    if res.status == "merged":
        activity.emit(log, logging.INFO, "worker.merge", role="scheduler",
                      slug=slug, trunk=res.trunk_sha)
        if synchronizer is not None:  # D5: Merge-Commit aktiv pushen (debouncer-blind)
            synchronizer.push_now()
    elif res.status == "conflict":
        # PLAN-30 Ebene 3, Fund aus der ursprünglichen Analyse: das globale
        # sync_conflict-Flag hier zu setzen war der eigentliche Bug, nicht nur
        # eine Ungenauigkeit — es wird von JEDEM erfolgreichen Pull/Push im
        # selben Tick zurückgesetzt (synchronizer.py::_resolve_conflict()),
        # unabhängig davon, ob DIESER Branch noch hängt (der Ur-Befund: bei
        # stündlichen erfolgreichen Syncs kam eine Job-Branch-Konflikt-Meldung
        # beim User praktisch nie an). Requirement 2 hat jetzt seine eigene,
        # korrekte, per-Branch-Sichtbarkeit (merge_quarantine.py, ab 3
        # Fehlschlägen eskaliert) — das globale Flag bleibt exklusiv für
        # Requirement 3 (echte Pull-Konflikte auf trunk selbst).
        activity.emit(log, logging.WARNING, "worker.merge_conflict",
                      "Merge-back-Konflikt — trunk unverändert, Branch intakt (/sync)",
                      role="scheduler", slug=slug, detail=res.detail)
    elif res.status == "error":
        activity.emit(log, logging.ERROR, "worker.merge_error",
                      "Merge-back-Fehler", role="scheduler", slug=slug, detail=res.detail)
    elif res.status == "blocked":
        # Modus A (PLAN-30 Ebene 2): Dirty-Tree-Verweigerung, kein echter
        # Konflikt — löst sich von selbst, sobald committet wird. Kein
        # sync_conflict, keine Eskalation, nur DEBUG-Sichtbarkeit.
        activity.emit(log, logging.DEBUG, "worker.merge_blocked",
                      "Merge-back blockiert (dirty Datei, kein Konflikt)",
                      role="scheduler", slug=slug, detail=res.detail)
    elif res.status == "quarantined":
        # Trunk unverändert seit letztem Fehlschlag ODER hart eskaliert
        # (merge_quarantine.ESCALATE_AFTER) — der ursprüngliche Fehlschlag
        # wurde bereits einmal als worker.merge_conflict/error geloggt, hier
        # nur DEBUG, sonst reproduziert das genau das "WARNING, die niemand
        # liest"-Problem, das Ebene 2 eigentlich beheben soll.
        activity.emit(log, logging.DEBUG, "worker.merge_quarantined",
                      "Merge-back übersprungen (Quarantäne)",
                      role="scheduler", slug=slug, detail=res.detail)
    elif res.status == "live_edit":
        # PLAN-30 Ebene 4: der Merge hätte eine gerade bearbeitete Datei
        # angefasst — bewusst übersprungen, kein Fehlschlag, keine
        # Eskalation. Nächster Versuch (Sofort-Trigger des nächsten Laufs
        # oder der Sweep) holt es automatisch nach, sobald die Datei ruht.
        activity.emit(log, logging.DEBUG, "worker.merge_live_edit",
                      "Merge-back übersprungen (Datei live bearbeitet)",
                      role="scheduler", slug=slug, detail=res.detail)
    elif res.status == "repo_busy":
        # Review-Runde 4, Fund 1: ein anderer Merge/Rebase ist bereits offen
        # (z. B. ein von /sync interaktiv offen gelassener Job-Branch-
        # Konflikt) — bewusst übersprungen, kein Fehlschlag DIESES Branches,
        # keine Eskalation. Nächster Trigger holt es nach, sobald der Mensch
        # den offenen Vorgang via `/sync continue`/`abort` abgeschlossen hat.
        activity.emit(log, logging.DEBUG, "worker.merge_repo_busy",
                      "Merge-back übersprungen (anderer Merge/Rebase offen)",
                      role="scheduler", slug=slug, detail=res.detail)


def _auth_dependency():
    """Optionaler Shared-Secret-Schutz für Verbund-Endpunkte (§1.3): ist
    BIBI_CONNECT_SECRET gesetzt, müssen Remote-Worker den Header mitschicken;
    ohne Secret gilt die Loopback-/Trust-Netz-Annahme (Single-Node/Tailscale).
    Eigene Factory (statt Closure-Duplikat), gemeinsam genutzt von
    ``_add_status_route()`` und ``_add_scheduler_routes()``."""
    _secret = os.environ.get("BIBI_CONNECT_SECRET")

    def _auth(x_bibi_secret: str | None = Header(default=None)):
        if _secret and x_bibi_secret != _secret:
            raise HTTPException(status_code=401, detail="bad or missing shared secret")
    return _auth


_LOCAL_CLIENT_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
#: "testclient" ist Starlettes ASGI-Test-Transport-Default (TestClient(app) ohne
#: expliziten ``client=``-Override, s. starlette.testclient) — kein echter
#: Netzwerk-Peer kann diesen Hostnamen liefern, ohne diesen Eintrag müsste jeder
#: bestehende Job-Route-Test einen approvten Knoten simulieren, obwohl er
#: technisch dieselbe In-Prozess-Anfrage wie ein lokaler Aufruf ist.


def _require_approved_or_local(request: Request,
                               x_bibi_node_id: str | None = Header(default=None)) -> None:
    """Sperrt Job-Control-Routen (``/-/job*``-Aktionen, ``/-/run``)
    für nicht freigeschaltete Knoten — Live-Fund 2026-07-25 (Job-Control-
    Approval-Bug): das Open-Trust-Freischalt-Modell (PLAN-32, ``approved_nodes``)
    war bisher nur an Heartbeat/Config-Distribution verdrahtet, nicht hier — ein
    ``pending``- oder sogar ``blocked``-Knoten konnte trotzdem uneingeschränkt
    Jobs auflisten/starten/killen.

    Lokale Aufrufe (Loopback — der Knoten spricht mit seinem eigenen Daemon,
    genau der Weg, den ``bibi-ctrl job``/``run``/``test`` ohne einen entfernten
    ``BIBI_SCHEDULER_URL``-Override nehmen) sind immer erlaubt; für den eigenen
    Daemon gibt es kein Node-Konzept. Ein entfernter Aufruf muss seine
    ``node_id`` über den ``X-Bibi-Node-Id``-Header mitschicken UND ``approved``
    sein — bewusst fail-closed bei fehlendem Header oder ``pending``/
    ``blocked``-Status, anders als die Rückwärtskompatibilität am Heartbeat
    (``/-/worker``): dort ist ein fehlender node_id ein Altlast-Client von vor
    PLAN-32, hier ist ein fehlender Header exakt der reproduzierte Bug.

    Bewusst NICHT an die rein lesenden Job-Status/Output-Routen gehängt
    (``/-/job/{id}/status|log|output|out|err|stream``, ``/-/run/live*``,
    ``/-/run/journal*`` GET) — die trägt render.py per ``EventSource`` direkt
    aus dem Browser gegen den jeweiligen Knoten (Nodes-Screen verlinkt jeden
    Knoten mit seiner eigenen Dashboard-URL, PLAN-32-unabhängig), ein Gate hier
    würde die bestehende Cross-Node-Live-Output-Ansicht brechen, für die es noch
    keinen anderen Auth-Mechanismus gibt. Eigener, bewusst offener Folgepunkt,
    kein Teil dieses Fixes.

    **Nachtrag Befund 4 (Live-Test PLAN-37, 2026-07-27):** die Loopback-Freigabe
    setzte *Loopback* mit *derselbe Knoten* gleich — das gilt nur, solange auf
    einer Maschine genau ein Knoten läuft. Auf sarasate teilen sich Host (8780),
    Client (8781) und der Testknoten (8782) eine Maschine; ein frisch
    onboardeter, in ``approved_nodes`` als ``pending`` geführter Knoten konnte
    darüber die volle Job-Kontrolle des Hosts ausüben (live reproduziert: über
    ``127.0.0.1`` 200, über die Tailscale-Adresse 403). Die CLI schickt ihre
    Identität längst mit (``job_cmd.py``: ``X-Bibi-Node-Id``) — der Host hat nur
    nie hingesehen, weil die Adressprüfung davor kurzschloss. Darum jetzt: ein
    Loopback-Aufruf, der eine **fremde** node_id trägt, durchläuft die reguläre
    Approval-Prüfung; frei bleibt nur echtes Selbstgespräch (kein Header, oder
    die eigene node_id dieses Daemons).

    Bewusst **nicht** gelöst: ein lokaler Aufruf ohne Header bleibt frei. Das ist
    der Weg, den der eigene Controller/das FE dieses Knotens nimmt
    (``DaemonClient`` schickt keine node_id), und ein lokaler Nutzer könnte den
    Header ohnehin weglassen — gegen einen bewusst handelnden lokalen Angreifer
    schützt diese Ebene nicht (er könnte auch ``bibi-ctrl`` direkt aufrufen oder
    die SQLite lesen). Der Fix schließt den realen Fall: ein ehrlicher, noch
    nicht freigeschalteter Knoten auf derselben Maschine."""
    host = request.client.host if request.client else None
    if host in _LOCAL_CLIENT_HOSTS:
        if not x_bibi_node_id or x_bibi_node_id == config.node_id():
            return
        # fremde node_id über Loopback ⇒ regulär prüfen (s. Nachtrag oben)
    if not x_bibi_node_id:
        raise HTTPException(status_code=403,
                            detail="node approval required (missing X-Bibi-Node-Id header)")
    conn = job_db.connect()
    try:
        status = job_db.node_approval_status(conn, x_bibi_node_id)
    finally:
        conn.close()
    if status != "approved":
        raise HTTPException(status_code=403, detail=f"node not approved (status: {status})")


def _require_approved_if_identified(
        x_bibi_node_id: str | None = Header(default=None)) -> None:
    """Gate fuer ``/-/events`` (#77): wer sich ausweist, wird geprueft.

    Die Route ist bewusst ungegatet gebaut, und der Grund haelt: eine
    ``EventSource`` kann keine Header setzen, ein Gate haette das FE
    ausgesperrt. Solange der Strom ein Nebenweg war, hing daran nichts.

    **Mit #77 wird er der Hauptkanal zwischen den Knoten** — und dort ist der
    Verbraucher kein Browser, sondern ein Daemon. Der kann sich ausweisen, und
    genau deshalb ist der Schutz hier billig: ein Aufruf **mit**
    ``X-Bibi-Node-Id`` muss ``approved`` sein, sonst 403. Wiederverwendung der
    vorhandenen Freischaltung (PLAN-32 „Open Trust"), keine neue Auth-Schicht.
    Wer keine Arbeit und kein Config-Bundle bekommt, soll auch keinen
    Ereignisstrom bekommen.

    **Die Grenze steht ausdruecklich hier und ist keine Nachlaessigkeit:** ein
    Aufruf ohne Header kommt weiter durch. Das ist der Browser, und es ist
    zugleich der Weg, den ein unehrlicher Daemon nehmen koennte, indem er den
    Header weglaesst. Diese Ebene schuetzt gegen den *ehrlichen* Knoten, der
    noch nicht freigeschaltet ist — dieselbe Zusage wie
    ``_require_approved_or_local()`` sie fuer Job-Control gibt, und dieselbe
    offene Flanke. Wer die Oberflaeche als Ganzes schuetzen will, braucht #19;
    dass dieser Posten den vorhandenen Schutz mitnimmt, ist genau der Grund,
    warum #19 dabei bleiben kann, was es ist.
    """
    if not x_bibi_node_id:
        return
    conn = job_db.connect()
    try:
        status = job_db.node_approval_status(conn, x_bibi_node_id)
        conn.commit()
    finally:
        conn.close()
    if status != "approved":
        raise HTTPException(status_code=403, detail=f"node not approved (status: {status})")


def _pull_for_deploy(sync_lock=None) -> tuple[bool, str | None]:
    """Origin integrieren, synchron und unter dem ``sync_lock`` (m.rau/bibi#39).

    Warum im Request und nicht als Boot-Signal (Einwand von m.rau, 2026-07-30):
    liegt die neue Lock **vor** dem ersten Neustart im Checkout, synct ``uv run``
    sofort dagegen — ein Durchlauf genügt statt zweier. Dazu zwei Vorteile, die
    den Signal-Weg auch inhaltlich schlechter machen: hier gilt der
    ``sync_lock`` (der Synchronizer pullt/pusht sonst gleichzeitig ins selbe
    Repo), und ein Fehlschlag lässt sich dem Aufrufer direkt melden statt ihn
    nur ins Log zu schreiben.

    ``guard_live_paths=False``: der Live-Edit-Guard schützt **unbeaufsichtigte**
    Schreibvorgänge davor, einem tippenden Menschen den Boden wegzuziehen. Ein
    angefordertes Deployment ist das Gegenteil — ein stiller Skip wäre hier
    genau der Fehler, weil der Knoten ohne den neuen Stand zurückkäme und
    niemand wüsste warum.
    """
    from bibi import git_ops
    # Kein cwd-Argument: `git_ops._git()` arbeitet durchgängig im Prozess-cwd,
    # und der Daemon läuft im Repo-Root (`WorkingDirectory={root}` in der Unit,
    # analog im launchd-Plist). Ein `current_branch(root)` wäre nicht nur
    # überflüssig, sondern ein TypeError — genau der Fehler, der beim ersten
    # scharfen Einsatz auftrat (2026-07-30).
    branch = git_ops.current_branch() or "trunk"
    if sync_lock is None:
        return git_ops.integrate(branch, guard_live_paths=False)
    with sync_lock:
        return git_ops.integrate(branch, guard_live_paths=False)


def _add_daemon_routes(app: FastAPI, *, sync_lock=None, worker=None,
                       pinned_worker=None, session_scoped: bool = False) -> None:
    """``/-/restart`` — bewusst **rollenunabhängig** (m.rau/bibi#39).

    Jeder Knoten muss neu startbar sein, nicht nur der Scheduler: der Deploy
    trifft alle drei. Beim ersten Entwurf lag die Route in
    ``_add_scheduler_routes()`` und war damit auf einem reinen Client (Mac:
    ``synchronizer,controller,connect``) gar nicht vorhanden — genau dort, wo
    sie am häufigsten gebraucht wird.

    ``session_scoped`` entscheidet, **was die Route versprechen darf**
    (m.rau/bibi#44): sie beendet den Prozess und verlässt sich auf einen
    Supervisor — den ein Sitzungs-Daemon nicht hat. Der Wert kommt vom
    startenden Prozess (``bibi``/``--session``) und ist keine Heuristik: von
    außen sind die beiden Fälle nicht unterscheidbar.
    """

    @app.post("/-/restart", tags=["daemon"])
    async def daemon_restart(req: RestartRequest):
        """Diesen Daemon neu starten — optional mit Deployment oder Reset.

        Es gibt keinen „Neustart-Befehl": die Units tragen ``Restart=always``
        mit ``RestartSec=3`` (bzw. launchd ``KeepAlive``) und starten über
        ``uv run bibi-ctrl daemon run``. **Ein sauberes Prozessende genügt
        also** — der Supervisor bringt den Daemon nach drei Sekunden mit einem
        gegen die Lock gesyncten venv zurück.

        **Für einen Sitzungs-Daemon gilt der zweite Satz nicht** (m.rau/bibi#44).
        Er läuft unter ``bibi`` im Terminal eines Menschen, ohne Supervisor; das
        Prozessende ist dort das Ende, nicht der Anfang eines Neustarts. Die
        Route beendet ihn trotzdem — so entschieden am 2026-08-01 — aber sie
        sagt es: ``supervised: false`` und eine Notiz, die den Weg zurück nennt
        statt einen Neustart zu versprechen, der nicht kommt. Bis dahin meldete
        sie „Supervisor startet neu" und war damit eine Erfolgsmeldung für
        etwas, das nicht stattfindet — dieselbe Fehlerform wie in #88 und #90,
        nur mit Schaden statt bloßem Schweigen.

        ``deployment`` pullt **hier, synchron**: damit liegt die neue Lock schon
        vor dem Neustart und ein Durchlauf genügt. Schlägt der Pull fehl, wird
        **nicht** neu gestartet — ein Neustart auf den alten Stand wäre nur
        Ausfallzeit ohne Nutzen, und der Aufrufer erfährt den Grund (409) statt
        ihn im Log suchen zu müssen.

        ``reset`` hinterlegt zusätzlich ein Boot-Signal, weil ein Prozess sein
        eigenes venv nicht unter sich austauschen kann; das ist der einzige
        Fall, der noch zwei Neustarts braucht (s. ``boot_signal``).

        Der Prozess endet **verzögert**, damit diese Antwort den Aufrufer noch
        erreicht — und über SIGTERM an sich selbst, nicht per ``os._exit()``:
        nur so greifen ``timeout_graceful_shutdown`` und die Aufräumarbeit im
        ``lifespan``-Finally. Ein harter Abbruch würde genau die Garantien
        aushebeln, um die es beim Job-Drain (#38) geht.
        """
        pulled = False
        if req.deployment or req.reset:
            # Blockierendes git in den Executor: dieser Event-Loop trägt auch
            # den SSE-Strom /-/events und die Heartbeats der anderen Knoten.
            loop = asyncio.get_running_loop()
            ok, kind = await loop.run_in_executor(
                None, lambda: _pull_for_deploy(sync_lock))
            if not ok:
                activity.emit(log, logging.WARNING, "daemon.restart_aborted",
                              "Neustart abgebrochen — Pull fehlgeschlagen",
                              role="daemon", reason=str(kind))
                raise HTTPException(
                    status_code=409,
                    detail=f"pull failed ({kind}) — kein Neustart, der Knoten "
                           "bleibt auf dem laufenden Stand")
            pulled = True
            activity.emit(log, logging.INFO, "daemon.deploy_pull",
                          "Deployment: origin integriert", role="daemon")

        kinds: list[str] = []
        if req.reset:
            boot_signal.request("reset")
            kinds.append("reset")

        # Job-Drain (m.rau/bibi#38) vor dem Beenden: keine neuen Reservierungen
        # mehr, und die laufende Setup-Phase auswarten. Danach ist jeder
        # verbliebene Job detacht und überlebt den Neustart — ohne das wäre ein
        # Restart-Knopf ein Würfelwurf auf das Setup-Fenster, das bei einem
        # Container-Job mit Image-Build minutenlang offen steht. Gewartet wird
        # NICHT auf Job-Ende: Agent-Läufe dauern 30 Minuten und mehr.
        drain: dict = {"drained": True, "starting": 0}
        if worker is not None:
            drain = await worker.drain()
        # ``pinned_worker`` dazu (m.rau/bibi#49): er läuft rollenunabhängig auf
        # jedem Knoten und führt die gepinnten ``/-/run``-Läufe aus — auf einem
        # reinen Client ist er der einzige Worker überhaupt. Ihn hier auszulassen
        # war eine Asymmetrie in #38, kein Vorsatz; ein Deploy-Neustart hat auf
        # dem Mac sonst dieselbe Setup-Lücke, die der Knopf gerade schließen soll.
        # Kostet nichts, wenn nichts im Setup steht: ``drain()`` kehrt dann sofort
        # zurück.
        if pinned_worker is not None:
            pinned = await pinned_worker.drain()
            drain = {"drained": drain["drained"] and pinned["drained"],
                     "starting": drain["starting"] + pinned["starting"]}

        activity.emit(log, logging.INFO, "daemon.restart_requested",
                      "Neustart angefordert", role="daemon",
                      kinds=",".join(kinds) or "restart",
                      pulled=str(pulled).lower(),
                      drained=str(drain["drained"]).lower())

        async def _later() -> None:
            await asyncio.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

        asyncio.create_task(_later())
        if session_scoped:
            # Kein Versprechen, das niemand einlöst: für diesen Knoten ist der
            # Neustart eine Aufforderung an den Menschen, kein Vorgang an der
            # Maschine (#44/#94).
            note = ("Der Daemon endet — er läuft in einer Sitzung, kein "
                    "Supervisor bringt ihn zurück. Neu starten mit: bibi")
            if kinds:
                note += " (reset wirkt beim nächsten Start)"
        else:
            note = ("Supervisor startet neu" if not kinds else
                    "Supervisor startet neu; reset braucht einen zweiten Start, "
                    "bevor der Server wieder läuft")
        if not drain["drained"]:
            note += (f" — Achtung: {drain['starting']} Job(s) noch im Setup, "
                     "sie überstehen den Neustart womöglich nicht")
        return {"restarting": True, "pulled": pulled, "signals": kinds,
                "drained": drain["drained"], "supervised": not session_scoped,
                "note": note}


def _add_status_route(app: FastAPI, *, sync_lock=None, synchronizer=None) -> None:
    """``/-/scheduler/status/{id}`` — bewusst **rollenunabhängig** registriert,
    herausgelöst aus ``_add_scheduler_routes()`` (PLAN-30 Ebene 1 v2, Fund
    2026-07-15): jeder Knoten hat laut PLAN-28 ohnehin seine eigene lokale
    Job-DB und sein eigenes trunk-Repo — ein gepinnter Lauf auf einem reinen
    Client (keine ``scheduler``-Rolle) braucht diese Route trotzdem für seinen
    Merge-back-Trigger (``bibi/wrapper/__init__.py::_report_terminal()``), sonst
    existiert sie dort schlicht nicht und der Trigger verpufft als 404."""
    _auth = _auth_dependency()

    @app.post("/-/scheduler/status/{id}", tags=["scheduler"], dependencies=[Depends(_auth)])
    def scheduler_status(id: str, report: StatusReport):  # noqa: A002
        # job_db.connect() ohne Pfad-Argument (anders als andere Routen hier, die
        # worker.db_path/pinned_worker.db_path durchreichen) — bewusst: diese
        # Route ist rollenunabhängig, kennt also keinen bestimmten Worker, dessen
        # db_path sie nehmen könnte. Heute unkritisch, weil jeder Worker/
        # LocalScheduler in diesem Team implizit denselben Default nutzt (Review-
        # Runde 2, Fund 4) — bricht lautlos, falls je ein abweichender db_path
        # eingeführt wird (kein Crash, nur "not_found" auf jeden Report).
        conn = job_db.connect()
        try:
            outcome = job_db.report_status(
                conn, id, status=str(report.status), reason=report.reason,
                exit_code=report.exit_code, host=report.host,
                worker=report.worker, output_ref=report.output_ref,
                attempt=report.attempt, next_fire_at=report.next_fire_at,
                commit_sha=report.commit_sha, branch=report.branch,
                app_url=report.app_url,
            )
        finally:
            conn.close()
        if outcome == "not_found":
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        if outcome == "invalid":
            return JSONResponse(status_code=409, content={"error": "illegal transition", "id": id})
        # Erfolgreicher Lauf mit Ergebnis-Branch → Merge-back nach trunk (PLAN-6).
        # Nur ``complete`` + vorhandener Branch (echo/No-op liefert keinen Commit).
        if str(report.status) == "complete" and report.branch:
            _merge_back(report.branch, sync_lock=sync_lock, synchronizer=synchronizer)
        return {"id": id, "status": str(report.status)}


def _add_journal_route(app: FastAPI) -> None:
    """``GET /-/journal`` — bewusst **rollenunabhängig** registriert,
    herausgelöst aus ``_add_scheduler_routes()`` (m.rau/bibi#103).

    Das Journal ist keine disponierte Domäne: jeder Knoten führt sein eigenes,
    vollständiges — Scheduler und Client sind darin gleichwertig und
    unabhängig, zusammengeführt wird erst in der Anzeige. Scheduler-gated
    antwortete die Route auf einem reinen Client mit 501, und damit hätte das
    Job-Detail keine ``LOCAL``-Gruppe.

    ``/-/run/journal`` bleibt vorerst daneben bestehen; es ist dieselbe Abfrage
    mit ``mine_only=True`` und damit auf ``?domain=local`` abbildbar.
    """

    # ``responses`` statt ``response_model``: das Schema gehört in den Vertrag,
    # aber die Antwort darf nicht darauf zusammengeschnitten werden —
    # ``journal_view()`` liefert zusätzlich ``payload``/``pinned_host``, und
    # ``render.py::_is_own_run()`` liest genau die aus dieser Liste.
    @app.get("/-/journal", tags=["journal"],
             responses={200: {"model": list[JournalEntryView]}})
    def journal(slug: str | None = None, host: str | None = None, domain: str | None = None,
                limit: int | None = None, offset: int | None = None):
        conn = job_db.connect()
        try:
            return job_db.list_journal(conn, slug=slug, host=host, domain=domain,
                                       limit=limit, offset=offset)
        finally:
            conn.close()


def _journal_output_path(entry: dict) -> Path:
    """Output-Datei einer Journal-Zeile: ``output_ref``, sonst der
    deterministische ``data/job/<run_id>/output.jsonl``-Pfad.

    Fallback-Fix (User-Fund 2026-07-27, "kein Output" auf /-/ui/run/…
    nach KILL): daemon-seitige Terminal-Reports (job_kill()s by_user,
    Sweeper-Zombie) schreiben die Journal-Zeile, BEVOR der Wrapper seinen
    output_ref melden kann — dessen Nachzügler-Report verwirft
    report_status() als idempotenten Wiederholungs-Report, output_ref
    blieb NULL, obwohl die Datei liegt. Der Pfad ist aber aus der run_id
    ableitbar (dieselbe Konvention wie worker.output_path()/Collector);
    das heilt auch alle ALT-Zeilen ohne Migration. run_live_kill()/-reset()
    (gepinnte Läufe) hatten denselben Fix schreibseitig schon seit
    2026-07-13 — job_kill() (Host) zog erst jetzt nach, s. dort."""
    ref = entry.get("output_ref")
    if ref:
        return repo.root() / ref
    return repo.root() / "data" / "job" / str(entry.get("run_id") or "") / "output.jsonl"


def _add_scheduler_routes(app: FastAPI, registry: WorkerRegistry,
                          *, sync_lock=None, synchronizer=None) -> None:
    """Echte DB-gestützte Scheduler-Routen (PLAN-3 §3.1) — nur bei aktiver
    ``scheduler``-Rolle (sie hält die Job-DB, §4.4). Ersetzen die 3.0-Stubs für
    ``/-/job``/``/-/job/{id}`` (zuerst registriert ⇒ gewinnen) und den
    Phase-2-Stub von ``/-/rescan``/``/-/schedule``. Reine JSON-API (§1.1).

    ``sync_lock``/``synchronizer``: nur noch für die übrigen Routen hier relevant
    (``/-/scheduler/status/{id}`` selbst lebt jetzt in ``_add_status_route()``,
    rollenunabhängig — PLAN-30 Ebene 1 v2)."""
    _auth = _auth_dependency()

    @app.post("/-/rescan", tags=["scheduler"])
    def rescan():
        conn = job_db.connect()
        try:
            res = job_db.rescan(conn)
            activity.emit(log, logging.INFO, "scheduler.rescan", role="scheduler",
                          inserted=res.get("inserted"), updated=res.get("updated"),
                          removed=res.get("removed"))
            return res
        finally:
            conn.close()

    @app.get("/-/schedule", tags=["scheduler"])
    def schedule():
        conn = job_db.connect()
        try:
            return {"schedules": job_db.list_schedules(conn)}
        finally:
            conn.close()

    @app.get("/-/schedule/{slug}", tags=["scheduler"])
    def schedule_by_slug(slug: str):
        conn = job_db.connect()
        try:
            data = job_db.get_job_by_slug(conn, slug)
        finally:
            conn.close()
        if data is None:
            return JSONResponse(status_code=404, content={"error": "not found", "slug": slug})
        return data

    @app.get("/-/job", response_model=list[JobView], tags=["job"],
            dependencies=[Depends(_require_approved_or_local)])
    def job_list(status: str | None = None):
        conn = job_db.connect()
        try:
            return job_db.list_jobs(conn, status=status)
        finally:
            conn.close()

    @app.get("/-/job/{id}", response_model=JobView, tags=["job"],
            dependencies=[Depends(_require_approved_or_local)])
    def job_get(id: str):  # noqa: A002
        conn = job_db.connect()
        try:
            job = job_db.get_job(conn, id)
        finally:
            conn.close()
        if job is None:
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        return job

    # ── Scheduler-Auswahl: Reservierung (PLAN-3 §3.2; Statusmeldung s. oben,
    # ``_add_status_route()``, rollenunabhängig) ─────────────────────────────
    @app.post("/-/scheduler/next", response_model=JobReservation, tags=["scheduler"],
              dependencies=[Depends(_auth)])
    def scheduler_next(req: NextRequest | None = None):
        if state.get_maintenance():
            return Response(status_code=204)  # Wartungsmodus: nichts ausgeben
        conn = job_db.connect()
        try:
            res = job_db.reserve_next(conn, worker=req.worker if req else None)
        finally:
            conn.close()
        if res is None:
            return Response(status_code=204)  # nichts zu tun (leerer Body)
        activity.emit(log, logging.INFO, "scheduler.dispatch", role="scheduler",
                      slug=res.get("slug"), run_id=res.get("id"), kind=res.get("kind"),
                      worker=req.worker if req else None)
        return res

    # ── Journal ──────────────────────────────────────────────────────────────
    # Die Liste selbst lebt in ``_add_journal_route()``, rollenunabhängig
    # (m.rau/bibi#103) — hier stehen nur noch die Detail- und Output-Wege.
    @app.get("/-/journal/{jid}", tags=["journal"])
    def journal_get(jid: int):
        # Eine Journal-Zeile (Metadaten) — Quelle des Execution-Detail (§C.4).
        conn = job_db.connect()
        try:
            entry = job_db.get_journal(conn, jid)
        finally:
            conn.close()
        if entry is None:
            return JSONResponse(status_code=404,
                                content={"error": "journal entry not found", "id": jid})
        return entry

    def _journal_events(jid: int) -> tuple[dict | None, list[dict]]:
        conn = job_db.connect()
        try:
            entry = job_db.get_journal(conn, jid)
        finally:
            conn.close()
        if entry is None:
            return None, []
        # read_events() toleriert eine fehlende Datei (→ []) — der Fallback-
        # Pfad darf also auch ins Leere zeigen (Lauf ohne jeden Output).
        return entry, output.read_events(_journal_output_path(entry))

    @app.get("/-/journal/{jid}/output", tags=["journal"])
    def journal_output(jid: int):
        # Replay-Quelle (§4.2): die output.jsonl des Laufs als getypte Events.
        entry, raw = _journal_events(jid)
        if entry is None:
            return JSONResponse(status_code=404,
                                content={"error": "journal entry not found", "id": jid})
        kind = models.effective_kind(entry.get("payload"))
        return {"id": jid, "kind": kind, "events": output_format.format_events(raw, kind),
                "output_ref": entry.get("output_ref")}

    def _journal_sse(jid: int, stream: str | None) -> StreamingResponse | JSONResponse:
        # Roher Zugriff (PLAN-14 Stufe 14.0) — Analogon zu /-/job/{id}/out|err|
        # stream, aber für archivierte Läufe über journal.output_ref aufgelöst.
        # Kein Live-Poll nötig: ein archivierter Lauf ändert sich nie mehr, ein
        # einmaliger Replay der vollständig geladenen Events reicht.
        entry, events = _journal_events(jid)
        if entry is None:
            return JSONResponse(status_code=404,
                                content={"error": "journal entry not found", "id": jid})

        def gen():
            for e in events:
                if stream is None or e.get("s") == stream:
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/-/journal/{jid}/out", tags=["journal"])
    def journal_out(jid: int):
        return _journal_sse(jid, "out")

    @app.get("/-/journal/{jid}/err", tags=["journal"])
    def journal_err(jid: int):
        return _journal_sse(jid, "err")

    @app.get("/-/journal/{jid}/stream", tags=["journal"])
    def journal_stream(jid: int):
        return _journal_sse(jid, None)

    @app.delete("/-/journal/{jid}", tags=["journal"])
    def journal_delete(jid: int):
        # A15: nur Lauf-Records aus der DB löschen, kein MD-CRUD (PLAN-4 §4.0).
        conn = job_db.connect()
        try:
            ok = job_db.delete_journal(conn, jid)  # autocommit (isolation_level=None)
        finally:
            conn.close()
        if not ok:
            return JSONResponse(status_code=404,
                                content={"error": "journal entry not found", "id": jid})
        return {"deleted": jid}

    # ── Worker-Verbund: Anmeldung/Heartbeat + Liste (PLAN-3 §3.6, A12) ────────
    # PLAN-32 Stufe 32.1 ("Open Trust"): der frühere BIBI_CONNECT_SECRET-Gate
    # (``_auth``, oben) entfällt hier zugunsten eines Host-seitig gepflegten
    # Freischalt-Status je ``node_id`` (``job_db.approved_nodes`` — bewusst
    # NICHT im In-Memory-``WorkerRegistry``, s. dortiger Docstring). Ein
    # unbekannter Knoten wird beim ersten Heartbeat als "pending" sichtbar
    # (harmlos, keine Arbeit/kein Config-Bundle); ein explizit "blocked"er
    # Knoten wird vollständig abgelehnt — kein Sonderfall, der ihn nur
    # teilweise durchlässt (s. PLAN-32, Client-Ban-Entscheidung). Clients ohne
    # ``node_id`` (älter als diese Änderung) können nicht individuell gebannt
    # werden und gelten als "approved" — Rückwärtskompatibilität, kein
    # Sicherheitsverlust (galt vorher ohnehin fail-open, s. PLAN-32).
    @app.post("/-/worker", tags=["worker"])
    def worker_heartbeat(hb: WorkerHeartbeat):
        status = "approved"  # kein node_id (älterer Client) -> Rückwärtskompatibilität
        bootstrapped = False
        if hb.node_id:
            conn = job_db.connect()
            try:
                status = job_db.node_approval_status(conn, hb.node_id)
                # m.rau/bibi#141: der Startschlüssel des ersten Clients. Erst
                # **nach** der Blocked-Prüfung unten wäre zu spät — aber davor
                # steht die Statusabfrage, und ein blockierter Knoten darf sich
                # auch mit einem gültigen Token nicht freikaufen. Deshalb hier,
                # und nur für einen, der nicht ohnehin schon approved ist.
                if hb.bootstrap_token and status == "pending":
                    bootstrapped = job_db.redeem_bootstrap_token(
                        conn, hb.bootstrap_token, hb.node_id)
                    if bootstrapped:
                        status = "approved"
            finally:
                conn.close()
            if status == "blocked":
                raise HTTPException(status_code=401, detail="node blocked by host operator")
            # Ein vorgezeigter Startschlüssel, der nicht gilt, ist ein Fehler und
            # kein Achselzucken: falsch, abgelaufen oder schon verbraucht sehen
            # von hier aus gleich aus, und in allen drei Fällen soll der Client
            # es erfahren statt zu glauben, es habe geklappt.
            if hb.bootstrap_token and not bootstrapped and status != "approved":
                raise HTTPException(status_code=401,
                                    detail="bootstrap token invalid, expired or already used")
            if bootstrapped:
                # Die eingelöste Zeile ist aus der DB verschwunden — bliebe der
                # Vorgang auch hier unsichtbar, wäre hinterher nicht mehr
                # feststellbar, dass dieser Knoten sich selbst freischaltete und
                # kein Mensch ihn freigab (Nodes.md §3.3, Klasse E).
                activity.emit(log, logging.INFO, "connect.bootstrapped",
                              "Knoten per Startschlüssel freigeschaltet",
                              role="scheduler", node_id=hb.node_id,
                              worker=hb.worker, host=hb.host)
        result = registry.heartbeat(hb.worker, hb.host, hb.git_status,
                                    node_id=hb.node_id, git_user=hb.git_user, role=hb.role,
                                    port=hb.port, engine=hb.engine,
                                    engine_tree=hb.engine_tree,
                                    session=hb.session,
                                    git_commit=hb.git_commit)
        # PLAN-32 Stufe 32.2: Config-Bundle-Distribution huckepack auf
        # demselben Heartbeat-Roundtrip. config_version reist bei JEDEM
        # Heartbeat mit (paar Bytes, kein Secret) — config_bundle nur, wenn
        # sich die Version geändert hat UND der Knoten approved ist (ein
        # pending/blocked-Knoten bekommt nie ein Bundle).
        bundle = config.distributable_config()
        host_version = config.config_version(bundle)
        result["config_version"] = host_version
        if status == "approved" and hb.client_config_version != host_version:
            result["config_bundle"] = bundle
        return result

    @app.post("/-/worker/{node_id}/approve", tags=["worker"],
              dependencies=[Depends(_require_approved_or_local)])
    def worker_approve(node_id: str):
        """Einen Knoten freischalten (m.rau/bibi#141).

        **Die Dependency ist der ganze Fix, und ihr Fehlen war die Lücke:**
        ``approve`` und ``block`` waren die einzigen schreibenden Routen dieser
        Datei ohne sie — wer den Scheduler erreichte, schaltete sich selbst
        frei und bekam beim nächsten Heartbeat ``config.distributable_config()``,
        also jeden ``BIBI_JOB_ENV_*``-Wert des Hosts. Genau das, was die
        Freigabe verhindern soll. Neun Zeilen tiefer stand dieselbe Zeile bei
        ``worker_disconnect`` längst, mit derselben Begründung.

        **Anders als dort ohne Selbst-Bedingung, und zwar umgekehrt:**
        ``disconnect`` lässt einen Knoten nur *sich selbst* abmelden; hier darf
        er *sich selbst gerade nicht* freigeben. Beides fällt aus derselben
        Dependency: ein ``pending``-Knoten kommt nicht durch, und wer schon
        ``approved`` ist, hat nichts mehr freizuschalten. Ein approvter Knoten
        gibt fremde frei, der Host-Operator lokal ohne Header alle.

        Dass ein frischer Scheduler damit niemanden mehr freigeben könnte, löst
        der Startschlüssel aus ``bibi-ctrl bootstrap-token`` — beides gehört in
        denselben Schritt, sonst sperrt diese Zeile jeden neuen Verbund aus.
        """
        conn = job_db.connect()
        try:
            job_db.set_node_approval(conn, node_id, "approved")
        finally:
            conn.close()
        return {"node_id": node_id, "status": "approved"}

    @app.post("/-/worker/{node_id}/block", tags=["worker"],
              dependencies=[Depends(_require_approved_or_local)])
    def worker_block(node_id: str):
        conn = job_db.connect()
        try:
            job_db.set_node_approval(conn, node_id, "blocked")
        finally:
            conn.close()
        return {"node_id": node_id, "status": "blocked"}

    @app.post("/-/worker/{node_id}/disconnect", tags=["worker"],
              dependencies=[Depends(_require_approved_or_local)])
    def worker_disconnect(node_id: str,
                          x_bibi_node_id: str | None = Header(default=None)):
        """Ein Knoten meldet sich planmäßig ab (m.rau/bibi#47).

        **Die Richtung ist der Punkt:** abmelden muss der Host, der gehende
        Knoten kann nur Bescheid sagen. Deshalb liegt die Route hier und nicht
        beim Client.

        Zwei Schranken davor, und beide braucht es. Die Approval-Prüfung, damit
        nicht irgendwer die Registry leert — und darüber hinaus die
        Selbst-Bedingung: ein Knoten meldet **sich** ab, nicht andere. Ohne die
        zweite dürfte jeder approvte Knoten jeden anderen aus dem Nodes-Screen
        werfen; „approved" heißt „darf mitarbeiten", nicht „darf über fremde
        Einträge verfügen". Lokal ohne Header bleibt der Weg für den
        Host-Operator offen — für den eigenen Daemon gibt es kein Node-Konzept
        (s. ``_require_approved_or_local``).

        Die 60-Sekunden-Stale-Erkennung bleibt daneben bestehen: sie ist das
        Netz für Absturz, Netzverlust und ``kill -9``. Der Endpunkt macht nur
        den Normalfall sauber.
        """
        if x_bibi_node_id and x_bibi_node_id != node_id:
            raise HTTPException(status_code=403,
                                detail="a node may only deregister itself")
        removed = registry.remove(node_id)
        activity.emit(log, logging.INFO, "worker.disconnect",
                      "Knoten abgemeldet" if removed else "Knoten war nicht registriert",
                      role="scheduler", node_id=node_id, removed=str(removed).lower())
        return {"node_id": node_id, "removed": removed}

    @app.get("/-/worker", response_model=list[WorkerView], tags=["worker"])
    def worker_list():
        return registry.list()

def _add_worker_routes(app: FastAPI, worker: Worker) -> None:
    """Worker-gated Job-Endpunkte (§4.5) — Streams aus ``output.jsonl`` + kill.
    Reine JSON/SSE-API (§1.1). Ersetzen die 3.0-Stubs (zuerst registriert)."""

    def _job_status(job_id: str) -> str | None:
        conn = job_db.connect(worker.db_path)
        try:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        return row["status"] if row else None

    def _sse(job_id: str, stream: str | None, from_offset: int) -> StreamingResponse:
        async def gen():
            path = worker.output_path(job_id)
            sent = from_offset
            while True:
                events = output.read_events(path)
                for e in events[sent:]:
                    if stream is None or e.get("s") == stream:
                        yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                sent = len(events)
                st = _job_status(job_id)
                if st is not None and Status(st) in TERMINAL and sent >= len(output.read_events(path)):
                    break
                await asyncio.sleep(0.2)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/-/job/{id}/status", response_model=JobView, tags=["job"])
    def job_status(id: str):  # noqa: A002
        conn = job_db.connect(worker.db_path)
        try:
            job = job_db.get_job(conn, id)
        finally:
            conn.close()
        if job is None:
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        return job

    @app.post("/-/job/{id}/ping", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def job_ping(id: str):  # noqa: A002
        # Der **zweite Feeder** von ``jobs.last_ping_at`` (#76). Der erste ist
        # der Aktivitäts-Reporter des Wrappers, der jede Output-Zeile sieht;
        # diese Route ist der Notausgang für Apps, deren Aktivität nicht über
        # stdout läuft — oder die blockweise puffern und deshalb minutenlang
        # arbeiten können, ohne dass eine Zeile ankommt (s. den App-Vertrag in
        # ``bibi/job.py::activity()``).
        #
        # Hier stand bis v0.7.6: *„der Worker liest es fürs Zombie-Timeout."*
        # Das war die Beschreibung einer Absicht, nicht eines Zustands — die
        # Spalte hatte keinen Leser, und diese Route in der ganzen Engine
        # keinen Aufrufer. Gültig war die mtime von ``output.jsonl``. Zwei
        # Aktivitäts-Mechanismen parallel im Haus, und der gebaute war der
        # unsichtbare; ein Kommentar, der das Gegenteil behauptet, hält den
        # Irrtum am Leben, statt ihn auffallen zu lassen.
        conn = job_db.connect(worker.db_path)
        try:
            ok = job_db.touch_ping(conn, id)
        finally:
            conn.close()
        return {"ok": ok}

    @app.get("/-/job/{id}/log", tags=["job"])
    def job_log(id: str):  # noqa: A002
        path = worker.output_path(id)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        return Response(content=raw, media_type="application/x-ndjson")

    @app.get("/-/job/{id}/output", tags=["job"])
    def job_output(id: str):  # noqa: A002
        # Getypte Events des **laufenden** Jobs (Analogon zu /-/journal/{jid}/output,
        # aber per Job-id) — für die Live-Output-Anzeige im Controller (FE).
        path = worker.output_path(id)
        raw = output.read_events(path) if path.exists() else []
        conn = job_db.connect(worker.db_path)
        try:
            job = job_db.get_job(conn, id)
        finally:
            conn.close()
        kind = models.effective_kind((job or {}).get("payload"))
        return {"events": output_format.format_events(raw, kind), "kind": kind}

    def _formatted_sse(job_id: str, from_offset: int) -> StreamingResponse:
        # Formatierte Live-Variante von /out|/err|/stream (Follow-up zu PLAN-14):
        # dieselbe Poll-/Terminierungslogik wie _sse(), aber jeder Poll formatiert
        # die volle Roh-Historie neu (format_events ist über die Gesamtliste
        # deterministisch) und sendet nur die seit dem letzten Poll neu
        # hinzugekommenen FORMATIERTEN Events. `from` zählt hier in denselben
        # formatierten Einheiten wie der /output-Seed (Live-Box) — kein
        # Offset-Mismatch wie bei /stream, das roh zählt.
        #
        # User-Fund 2026-07-20 ("Output nicht sauber, Reload zeigt mehr"):
        # `.liveterm`s ``es.onerror = () => es.close()`` (render.py) behandelte
        # jeden Verbindungsabriss wie ein beabsichtigtes Server-Ende — der
        # Browser sieht in beiden Fällen dasselbe onerror, es gab keine
        # Möglichkeit zu unterscheiden. Drei Ergänzungen lösen das an der
        # Wurzel, alle rein additiv (kein bestehendes Feld/Verhalten geändert):
        # (1) jedes Event trägt jetzt eine ``id:``-Zeile == der `from`-Zählung
        # danach — bei einem Reconnect schickt der Browser diese automatisch
        # als `Last-Event-ID`-Header zurück (job_output_stream() liest ihn,
        # override für `from`), kein eigenes Zähl-JS im Client nötig. (2) kurz
        # vor dem regulären, beabsichtigten Schließen (Job terminal + alles
        # gesendet) ein explizites ``event: done`` — der Client schließt SELBST
        # darauf, bevor die Verbindung natürlich endet, onerror bleibt also nur
        # noch für echte Abrisse übrig (und darf dort NICHT mehr schließen,
        # s. render.py). (3) ``: ping``-Kommentarzeilen (von EventSource laut
        # Spezifikation ignoriert) bei >=15s Sendepause — Verdacht: lange
        # stille Phasen (24s+ beobachtet) reißen über Tailscale eher ab.
        # Berührt nie output.jsonl / _last_activity() — Zombie/Silence-
        # Erkennung (worker.py) bleibt komplett unbeeinflusst.
        conn = job_db.connect(worker.db_path)
        try:
            job = job_db.get_job(conn, job_id)
        finally:
            conn.close()
        kind = models.effective_kind((job or {}).get("payload"))
        path = worker.output_path(job_id)

        async def gen():
            sent = from_offset
            last_sent_at = time.time()
            while True:
                formatted = output_format.format_events(output.read_events(path), kind)
                for e in formatted[sent:]:
                    sent += 1
                    yield f"id: {sent}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
                    last_sent_at = time.time()
                st = _job_status(job_id)
                if (st is not None and Status(st) in TERMINAL
                        and sent >= len(output_format.format_events(output.read_events(path), kind))):
                    yield "event: done\ndata: {}\n\n"
                    break
                if time.time() - last_sent_at >= 15:
                    yield ": ping\n\n"
                    last_sent_at = time.time()
                await asyncio.sleep(0.2)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/-/job/{id}/output/stream", tags=["job"])
    def job_output_stream(id: str, from_: int = Query(0, alias="from"),  # noqa: A002
                           last_event_id: str | None = Header(default=None)):
        # Last-Event-ID (User-Fund 2026-07-20, s. _formatted_sse()): schickt der
        # Browser bei jedem automatischen EventSource-Reconnect selbst mit,
        # sobald Events eine `id:`-Zeile tragen — verlässlicher als der einmalig
        # eingefrorene `from`-Query-Parameter, deshalb Vorrang davor.
        if last_event_id is not None:
            try:
                from_ = int(last_event_id)
            except ValueError:
                pass
        return _formatted_sse(id, from_)

    @app.get("/-/job/{id}/out", tags=["job"])
    def job_out(id: str, from_: int = Query(0, alias="from")):  # noqa: A002
        return _sse(id, "out", from_)

    @app.get("/-/job/{id}/err", tags=["job"])
    def job_err(id: str, from_: int = Query(0, alias="from")):  # noqa: A002
        return _sse(id, "err", from_)

    @app.get("/-/job/{id}/stream", tags=["job"])
    def job_stream(id: str, from_: int = Query(0, alias="from")):  # noqa: A002
        return _sse(id, None, from_)

    @app.post("/-/job/{id}/kill", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def job_kill(id: str, req: KillRequest | None = None):  # noqa: A002
        signaled = worker.kill(id)
        # output_ref direkt mitschreiben (User-Fund 2026-07-27, "kein Output"
        # auf /-/ui/run/… nach KILL) — exakt dieselbe Race-Klasse, die
        # run_live_kill()/-reset() (gepinnte Läufe) schreibseitig schon seit
        # 2026-07-13 fixen, s. dortiger Kommentar: unser Terminal-Write hier
        # macht die Zeile terminal, der spätere Wrapper-Report MIT output_ref
        # wird dann als idempotenter Wiederholungs-Report verworfen — die
        # Journal-Zeile fror ohne Verweis ein. worker.output_path() kennt den
        # Pfad des aktuellen Laufs (dieselbe Quelle wie die Live-Routen).
        # NUR für einen tatsächlich aktiven Lauf: KILL auf complete (Lazy-
        # Rearm-Stopper, User-Redesign 2026-07-20) archiviert die Zeile zu
        # einem frischen, sofort toten Zyklus OHNE eigenen Output — der
        # Verweis des alten Laufs gehört dessen Journal-Zeile, nicht diesem.
        conn = job_db.connect(worker.db_path)
        try:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (id,)).fetchone()
            out_ref: str | None = None
            if row is not None and row["status"] in ("starting", "running", "awaiting", "deferred"):
                try:
                    out_ref = worker.output_path(id).relative_to(repo.root()).as_posix()
                except Exception:  # noqa: BLE001 — defensiv (§2.7)
                    pass
            outcome = job_db.report_status(conn, id, status="killed",
                                            reason="by_user", output_ref=out_ref)
        finally:
            conn.close()
        if outcome == "not_found":
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        if outcome == "invalid":
            return JSONResponse(status_code=409, content={"error": "job not running", "id": id})
        return {"id": id, "status": "killed", "signaled": signaled}

    @app.post("/-/job/{id}/reset", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def job_reset(id: str):  # noqa: A002  — §5.6 Verb: Terminalzustand → pending
        conn = job_db.connect(worker.db_path)
        try:
            outcome = job_db.report_status(conn, id, status="pending")
        finally:
            conn.close()
        if outcome == "not_found":
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        if outcome == "invalid":
            return JSONResponse(status_code=409, content={"error": "not resettable", "id": id})
        # Bibi4 Batch 6: RESET wischt job-eigene ~/.local/share/bibi/-Daten,
        # START (job_start() unten) rührt sie nie an — nur bei echtem
        # Übergang (nicht bei not_found/invalid oben).
        job_db.wipe_job_data(id)
        return {"id": id, "status": "pending"}

    @app.post("/-/job/{id}/start", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def job_start(id: str):  # noqa: A002  — §5.6 Verb: pending sofort fällig machen
        conn = job_db.connect(worker.db_path)
        try:
            outcome = job_db.start_now(conn, id)
        finally:
            conn.close()
        if outcome == "not_found":
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        if outcome == "invalid":
            return JSONResponse(status_code=409, content={"error": "not pending", "id": id})
        return {"id": id, "status": "started"}

    @app.post("/-/job/{id}/rebuild", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def job_rebuild_image(id: str):  # noqa: A002
        # PLAN-24 Befund 5: per-Job-Image verwerfen — eigenständige Aktion,
        # bewusst getrennt von START/RESET (die das Image nie antasten).
        conn = job_db.connect(worker.db_path)
        try:
            info = job_db.get_job_exec_mode(conn, id)
        finally:
            conn.close()
        if info is None:
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        slug, exec_mode = info
        if (exec_mode or "host").strip().lower() != "container":
            return JSONResponse(status_code=409, content={"error": "not a container job", "id": id})
        out_path = worker.output_path(id)
        if not worker.rebuild_job_image(slug, out_path=out_path):
            return JSONResponse(status_code=502, content={"error": "docker command failed", "id": id})
        return {"id": id, "slug": slug, "rebuilt": True}


def create_app(
    roles: Roles, synchronizer=None, worker: Worker | None = None, sweeper=None,
    rescanner=None, controller_client=None, controller_base_url: str | None = None,
    sync_lock=None, heartbeat=None, pinned_worker: Worker | None = None,
    bus=None, collector=None, drain_timeout: float | None = None,
    session_scoped: bool = False, subscription=None,
) -> FastAPI:
    started_at = time.time()
    # FE-Event-Bus (PLAN-36 Stufe 36.1): rollenunabhängig wie pinned_worker —
    # jeder Knoten publiziert seine eigene Sicht (E1), der Collector ist der
    # eine Poller des Knotens (E4). Injektion für Tests (autorun=False).
    # Der Default-Collector wird erst weiter unten erzeugt (nach
    # worker_registry — er braucht sie für das "nodes"-Sammel-Target, 36.3).
    if bus is None:
        from bibi.daemon.bus import Bus
        bus = Bus()
    if worker is None and roles.worker:
        worker = Worker(worker_name="local")
    # PLAN-28: rollenunabhängig — jeder Knoten hat seine eigene lokale
    # jobs.sqlite (kein zentraler, netzwerk-geteilter Scheduler), also braucht
    # jeder Knoten seinen eigenen Retry-Redispatch/Deferred-Re-Arm für
    # pinned_host==sich-selbst (gepinnte /run-Läufe), unabhängig davon, ob er
    # roles.scheduler/worker hat. Kein eigenes Komponenten-Rad (die frühere
    # LocalPinnedLoop-Erfindung fehlte z. B. das App-Port/Traefik-Routing,
    # das Worker._poll_app_routes() längst kann) — einfach ein zweiter
    # Worker mit einem Client, der nur pinned_host==dieser Host dispatcht.
    if pinned_worker is None:
        from bibi.daemon.scheduler_client import LocalScheduler
        pinned_worker = Worker(client=LocalScheduler(pinned_only=True))
    # Merge-back nach einem lokalen Abschluss (PLAN-6) läuft nicht mehr über
    # einen In-Memory-Callback hier (entfernt, PLAN-30 Ebene 1 v2, Fund
    # 2026-07-15: der detachte Wrapper-Subprozess meldet Terminal-Status per
    # Direct-SQLite und erreicht einen In-Process-Hook wie diesen nie — für
    # weder ``worker`` noch ``pinned_worker``, auf keinem Knotentyp). Stattdessen
    # feuert der Wrapper selbst den Trigger per HTTP gegen ``_add_status_route()``
    # (rollenunabhängig registriert, s. u.) — das deckt beide Worker-Instanzen
    # symmetrisch ab, ohne Sonderfall für ``roles.scheduler``.
    worker_registry = WorkerRegistry() if roles.scheduler else None
    # Das Scheduler-Abonnement (#77): rollenunabhaengig erzeugt wie der Bus,
    # aber nur wirksam, wenn dieser Knoten einen *fremden* Scheduler kennt.
    # `SchedulerEvents.start()` kehrt ohne URL sofort zurueck — ein Host hat
    # niemanden zu abonnieren, und das ist kein Fehlerfall, sondern der
    # Normalfall auf der anderen Seite derselben Verbindung.
    #
    # NICHT `config.scheduler_base_url()`: die bevorzugt bewusst
    # BIBI_DAEMON_PORT ("sprich mit MEINEM Daemon") und liefert in einem
    # Daemon-Prozess die eigene Adresse — der Knoten abonnierte sich selbst.
    # Dieselbe Unterscheidung, die `Collector._fetch_scheduler_status()` schon
    # trifft, und aus demselben Grund dort im Docstring steht.
    if subscription is None:
        from bibi.daemon.bus import SchedulerEvents
        # **Wer die Quelle ist, abonniert sie nicht** (v0.7.7). Ein
        # Scheduler-Knoten traegt in aller Regel trotzdem einen
        # `BIBI_SCHEDULER_URL` — auf sarasate steht dort seine eigene Adresse
        # (`http://localhost:8780`). Ohne diese Zeile nahm der Abonnent sie und
        # verband sich mit dem Daemon, in dem er selbst laeuft.
        #
        # Was daraus wurde, hing an einem Zufall: die eigene `node_id` des
        # Hosts stand in seiner eigenen `approved_nodes` auf `blocked`, das
        # Gate wies ihn also ab — es blieb ein Retry-Sturm gegen sich selbst.
        # **Waere sie `approved` gewesen, haette er seine eigenen Ereignisse
        # abonniert und auf denselben Bus zurueckveroeffentlicht: eine
        # Endlosschleife.** Der Kommentar unten warnte vor genau diesem Fall;
        # die Pruefung dazu fehlte.
        #
        # Die Rolle ist das richtige Kriterium, nicht die Adresse: ein
        # Scheduler *ist* die Quelle seiner Ereignisse, unabhaengig davon,
        # unter welchem Namen er sich selbst kennt. Ein Adressvergleich
        # muesste `localhost`, `127.0.0.1`, den Hostnamen und die Tailnet-IP
        # als dasselbe erkennen — und jede vergessene Schreibweise waere ein
        # stiller Rueckfall in genau diesen Fehler.
        #
        # NICHT `config.scheduler_base_url()`: die bevorzugt bewusst
        # BIBI_DAEMON_PORT ("sprich mit MEINEM Daemon") und liefert in einem
        # Daemon-Prozess die eigene Adresse. Dieselbe Unterscheidung, die
        # `Collector._fetch_scheduler_status()` schon trifft.
        _sub_url = None
        if not roles.scheduler:
            _sub_url = (os.environ.get("BIBI_SCHEDULER_URL")
                        or config.read_env().get("BIBI_SCHEDULER_URL"))
        _sub_node = None
        try:
            _sub_node = config.node_id()
        except Exception:  # noqa: BLE001 — ohne Identitaet kein Abonnement
            _sub_node = None
        subscription = SchedulerEvents(bus, url=_sub_url, node_id=_sub_node)
    if collector is None:
        from bibi.daemon.bus import Collector
        collector = Collector(bus, registry=worker_registry, heartbeat=heartbeat,
                              subscription=subscription)
    # Bugfix (User-Fund: ein erschoepfter gepinnter Job blieb auf einem reinen
    # Client fuer immer in "failed" haengen): job_db.sweep() (failed+erschoepft
    # -> error, deferred+defer_max -> inactive) lief bisher nur unter
    # roles.scheduler - genau wie pinned_worker (oben, IMMER gestartet) sind
    # gepinnte /-/run-Laeufe aber bewusst rollenunabhaengig, ihr Aufraeumer war
    # es bisher nicht. Sicher ohne Scheduler-Rolle: registry ist dann bereits
    # None (kein Team-Worker-Registry vorhanden), Sweeper.tick_once() behandelt
    # das schon als No-Op fuer den einzigen anderen Zweck (verwaiste Team-
    # Worker erkennen) - nur die eigentliche job_db.sweep() laeuft dann ueberall.
    if sweeper is None:
        from bibi.daemon.sweeper import Sweeper
        sweeper = Sweeper(registry=worker_registry,
                          local_worker_name=worker.worker_name if worker is not None else None,
                          # m.rau/bibi#46: nur ein von einer Sitzung gestarteter
                          # Daemon zählt Sitzungen und fährt bei 0 herunter.
                          session_scoped=session_scoped)
    if rescanner is None and roles.scheduler:
        from bibi.daemon.rescanner import Rescanner
        rescanner = Rescanner()

    def _scheduler_startup() -> None:
        # Beim Start: Schedules erfassen, startup-Jobs feuern (§5.2), verwaiste
        # lokale running-Jobs aus einem Vorab-Absturz aufräumen (no_process, §5.5).
        conn = job_db.connect()
        try:
            rs = job_db.rescan(conn)
            fired = job_db.fire_startup(conn)
            orphans = (job_db.reconcile_orphans(conn, worker.worker_name)
                       if worker is not None else 0)
            activity.emit(log, logging.INFO, "scheduler.startup", role="scheduler",
                          inserted=rs.get("inserted"), fired=fired, orphans=orphans)
        except Exception:
            log.warning("scheduler-startup (rescan/startup/reconcile) übersprungen")
        finally:
            conn.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if synchronizer is not None:
            await synchronizer.start()
        if roles.scheduler:
            _scheduler_startup()
        if sweeper is not None:
            await sweeper.start()
        if rescanner is not None:
            await rescanner.start()
        if heartbeat is not None:
            await heartbeat.start()
        if worker is not None:
            await worker.start()
        await pinned_worker.start()
        subscription.start()
        await collector.start()
        try:
            yield
        finally:
            await collector.stop()
            subscription.stop()
            # Job-Drain auch auf diesem Weg (m.rau/bibi#49): ein SIGTERM — vom
            # `systemctl stop`, vom Ende einer Sitzung, vom Restart-Endpunkt —
            # traf Jobs im Setup bisher genau so unkontrolliert, wie es vor #38
            # der Neustart tat. Die Zusage „ein Neustart erwischt keinen Job im
            # Setup" galt nur für einen der beiden Wege, einen Daemon zu beenden.
            #
            # Beide Worker, nicht nur der rollengebundene: ``pinned_worker``
            # läuft rollenunabhängig auf JEDEM Knoten und ist auf einem
            # Sitzungsknoten (kein `worker` im Profil) der einzige — er führt
            # dort die `bibi-ctrl run`-Läufe aus, also gerade das, was ohne Host
            # funktionieren soll. Ihn auszulassen hieße, die Lücke genau dort zu
            # belassen, wo dieses Release sie schließen will.
            drain_secs = drain_timeout if drain_timeout is not None else _resolve_drain_timeout()
            await _drain_for_shutdown(worker, timeout=drain_secs, label="worker")
            await _drain_for_shutdown(pinned_worker, timeout=drain_secs, label="pinned")
            await pinned_worker.stop()
            if worker is not None:
                await worker.stop()
            if heartbeat is not None:
                await heartbeat.stop()
            if rescanner is not None:
                await rescanner.stop()
            if sweeper is not None:
                await sweeper.stop()
            if synchronizer is not None:
                await synchronizer.stop()
            # Portdatei HIER räumen, nicht erst nach ``server.run()``
            # (m.rau/bibi#45). Live gefunden beim Rauchtest von #48: uvicorn
            # feuert in ``capture_signals()`` das eingefangene Signal am Ende
            # erneut — und da es den ursprünglichen Handler (``SIG_DFL``) davor
            # wiederherstellt, beendet dieses zweite SIGTERM den Prozess sofort.
            # Jedes ``finally`` um ``server.run()`` herum ist auf dem Signalweg
            # damit toter Code, und genau der ist der Normalfall: Sitzungsende,
            # ``systemctl stop``, Restart-Endpunkt. Hier drin läuft es
            # nachweislich (die Zeile „Application shutdown complete" steht im
            # Log davor). ``clear()`` fasst nur den EIGENEN Eintrag an, ist also
            # auch dann harmlos, wenn dieser Prozess gar keinen geschrieben hat.
            from bibi.daemon import portfile
            portfile.clear()

    app = FastAPI(
        title="bibi · daemon",
        version=openapi.CONTRACT_VERSION,
        lifespan=lifespan,
        docs_url="/-/docs",       # Swagger-UI über die eingefrorene Spec
        redoc_url="/-/redoc",     # ReDoc-Alternative
        openapi_url="/-/openapi.json",
    )

    # Der Bus gehört zur App und wurde bisher nur innen benutzt. ``daemon_cmd``
    # braucht ihn ab m.rau/bibi#176 von außen: das SIGTERM erreicht den
    # Signal-Handler von uvicorn, nicht diese Datei, und dort muss jemand den
    # SSE-Strömen sagen können, dass Schluss ist — **bevor** uvicorns Frist
    # anläuft. ``app.state`` ist der dafür vorgesehene Ort, und ein Attribut
    # ist ehrlicher als ein Modul-Singleton: es gibt einen Bus **pro App**,
    # und die Tests bauen regelmäßig mehrere.
    app.state.bus = bus
    # Wie der Bus: von aussen greifbar, weil Tests und Diagnose wissen wollen,
    # ob dieser Knoten ueberhaupt abonniert und ob die Verbindung steht.
    app.state.subscription = subscription

    # Defensiv: ein Endpunkt-Fehler darf den Daemon nie killen — generischer
    # Handler liefert 500-JSON statt einer ungefangenen Exception (§2.7).
    @app.exception_handler(Exception)
    async def _on_error(request, exc):  # noqa: ANN001
        log.warning("endpoint error on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})

    # ── daemon-weit (rollenunabhängig) ──────────────────────────────────────

    @app.get("/-/health")
    def health():
        # Netzfrei, defensiv: nur statischer Lebendigkeits-Beweis.
        return {"status": "ok", "roles": roles.active_names()}

    @app.get("/-/status")
    def status():
        out: dict = {
            "roles": roles.active_names(),
            "auto_sync": state.get_auto_sync(),
            "sync_conflict": state.get_sync_conflict(),
            "maintenance": state.get_maintenance(),
            "started_at": started_at,
            # Die Serverzeit. Ein Client zeigt sie als *die* Uhr des UI
            # (m.rau, 2026-08-03: "am liebsten haette ich die scheduler
            # Uhrzeit ... rechts oben mit Ticker, und sonst nirgends") --
            # in einem verteilten System ist die fremde Uhr die
            # interessantere, und ihr Auseinanderlaufen wird nur sichtbar,
            # wenn sie jemand ausspricht.
            "now": time.time(),
            # Eigener Hostname (PLAN-21 Befund 6) — die Host-Karte zeigt ihn auf
            # Knoten ohne connect-Rolle statt des früheren "lokal"-Platzhalters.
            "hostname": socket.gethostname(),
        }
        # Wer antwortet hier. Der Scheduler meldet sich nie bei sich selbst,
        # steht also in keiner Registry — ohne diese Selbstauskunft kann ein
        # Client seine Zeile im Nodes-Screen nicht bauen und ließe ausgerechnet
        # den Knoten aus, dem die Flotte gehört (s. ``node_info``).
        try:
            out["node"] = node_info.self_entry(roles)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        # Soll/Ist der Engine (m.rau/bibi#43) — rein lokal abgeleitet, kein
        # Heartbeat-Feld und keine Host-Abhängigkeit; funktioniert also gerade
        # dann, wenn der Host nicht erreichbar ist. Defensiv: ein Knoten, der
        # seine eigene Herkunft nicht ermitteln kann, soll melden was er weiß,
        # statt /-/status zu verlieren (§2.7).
        try:
            from bibi.daemon import deploy as deploy_mod
            out["engine"] = deploy_mod.update_status()
        except Exception:  # noqa: BLE001
            out["engine"] = {"verdict": "unknown", "needs_update": False}
        if synchronizer is not None:
            out["synchronizer"] = synchronizer.status()
        if worker_registry is not None:
            workers = worker_registry.list()
            # PLAN-32 Stufe 32.1: Freischalt-Status lebt in job_db (dauerhaft),
            # nicht im In-Memory-WorkerRegistry — hier für den Nodes-Screen
            # zusammengeführt, eine Abfrage statt einer je Zeile.
            conn = job_db.connect()
            try:
                approvals = job_db.list_node_approvals(conn)
            finally:
                conn.close()
            for w in workers:
                w["approval_status"] = approvals.get(w.get("node_id"), "pending")
            out["workers"] = workers
        # Host-Verbindungsstatus (A12) — eigenständig von der Worker-Rolle (§4.8-Fix
        # 2026-07-05): ein Client meldet hier, ob sein letzter Heartbeat-Versuch
        # beim Scheduler ankam, unabhängig davon, ob er selbst Jobs ausführt.
        if heartbeat is not None:
            out["connect"] = {"ok": heartbeat.last_ok, "last_at": heartbeat.last_at}
        # Verdikt „läuft alles?" — DB-nah, nur am Knoten mit Scheduler-Rolle (der
        # die Job-DB besitzt). Föderation aggregiert je-Knoten-/-/status (§2.2).
        if roles.scheduler:
            conn = job_db.connect()
            try:
                out["verdict"] = job_db.verdict(conn)
                # Stat-Grid-Grundlage fürs Lauf-Historie-Chart (PLAN-21 Befund
                # 11): aktuelle Zustands-Zählung + running-Gesamtzahl seit
                # Prozessstart. Letztere ist ein simpler In-Memory-Zähler
                # (job_db.dispatch_count(), inkrementiert in reserve_next()),
                # kein DB-State — löst sich mit dem Daemon-Neustart auf, genau
                # wie ``started_at`` selbst. ``counts_by_kind`` zusätzlich für
                # die Job-Status-Matrix (Bibi4-Iteration, job/claude/app).
                out["job_stats"] = {
                    "counts": job_db.status_counts(conn),
                    "counts_by_kind": job_db.status_counts_by_kind(conn),
                    "running_since_uptime": job_db.dispatch_count(),
                    "complete_since_uptime": job_db.count_completed_since(conn, started_at),
                    "next_due_at": job_db.next_due_at(conn),
                }
            finally:
                conn.close()
        return out

    @app.post("/-/maintenance")
    def maintenance_on():
        state.set_maintenance(True)
        activity.emit(log, logging.WARNING, "maintenance.on",
                      "Wartungsmodus AN — Job-Dispatch pausiert", role="daemon")
        return {"maintenance": True}

    @app.delete("/-/maintenance")
    def maintenance_off():
        state.set_maintenance(False)
        activity.emit(log, logging.INFO, "maintenance.off",
                      "Wartungsmodus aus — Job-Dispatch wieder aktiv", role="daemon")
        return {"maintenance": False}

    # ── /run: lokale On-Demand-Ausführung (PLAN-3 §3.3b) — Client-only ────────
    # User-Feedback 2026-07-06: hing bisher an _add_worker_routes() (nur mit
    # --worker registriert), obwohl der Dispatch selbst kein Worker-Objekt
    # braucht — sich repo_root/work_dir/db_path genau wie die CLI (run_cmd.py)
    # selbst über repo.root() auflöst. Ein reiner Client (kein --worker) konnte
    # /run dadurch nur per CLI nutzen, nie über den Browser/die API — dieselbe
    # Art Lücke wie beim Heartbeat (PLAN-17 Stufe 17.0).
    #
    # PLAN-38 (2026-07-27): läuft jetzt in-place gegen den Live-Checkout und ist
    # deshalb auf Knoten mit scheduler-/worker-Rolle gesperrt (409) — dieselbe
    # Regel wie in der CLI, gemeinsam in roles.forbids_local_run(). Der reguläre
    # Scheduler-Dispatch (execute_reservation()) ist davon unberührt und behält
    # seine Worktree-Isolation.
    @app.post("/-/run", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def run(req: RunRequest):
        blocked = roles_mod.forbids_local_run(roles)
        if blocked:
            return JSONResponse(status_code=409,
                                content={"error": roles_mod.local_run_denied_message(blocked),
                                         "roles": blocked})
        # PLAN-28: run_pinned() (Nachfolger des früheren, rein synchronen
        # run_local()) gibt dem Lauf jetzt eine echte jobs-Zeile
        # (pinned_host=dieser Host) und läuft durch dieselbe Retry/Error/
        # Deferred/Zombie-Maschine wie ein Scheduler-Job, bleibt aber hier
        # (pinned_host) und sofort (execute_reservation() kehrt
        # gleich nach dem Subprozess-Spawn zurück, kein Hintergrund-Thread
        # nötig — run_pinned() selbst blockiert nur für die kurze
        # Setup-Phase, nicht für den ganzen Lauf; GET /-/run/live[/{slug}]
        # macht den Zwischenstand abfragbar).
        if not req.slug and not req.cmd:
            return JSONResponse(status_code=400, content={"error": "slug oder cmd nötig"})
        slug = req.slug or "adhoc"
        if worker_mod.local_run_live(slug) is not None:
            return JSONResponse(status_code=409,
                                content={"error": "already running", "slug": slug})

        try:
            # register=pinned_worker._register (PLAN-28 Refactor B): derselbe
            # Proc-Registry-Callback wie beim teamweiten Worker — kein eigenes
            # Kill-Tracking mehr nötig, pinned_worker.kill() übernimmt das.
            # use_schedule_retry=True (Bugfix, User-Fund): ein laufender Daemon
            # bedient fällige Retries über den gepinnten Worker-Loop — anders
            # als beim daemonlosen CLI-Pfad (bibi-ctrl run) darf/soll hier also
            # attempts/backoff/defer_time/error_time aus der Schedule-MD
            # gelten, statt immer sofort bei Fehlschlag zu exhaustieren.
            # in_place=True (PLAN-38): lokaler Stand statt frischem trunk-Worktree.
            res = run_pinned(slug=req.slug, cmd=req.cmd, kind=req.kind,
                             register=pinned_worker._register, use_schedule_retry=True,
                             in_place=True)
        except LookupError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — Route darf nie unbehandelt crashen
            activity.emit(log, logging.ERROR, "run.pinned_error",
                          "Gepinnter /run-Lauf fehlgeschlagen", role="daemon",
                          slug=slug, error=str(exc))
            return JSONResponse(status_code=500, content={"error": str(exc)})
        return {"id": res["id"], "slug": slug, "status": "running",
                "output_ref": res["output_ref"]}

    # ── /test: entfallen (PLAN-38, 2026-07-27) ────────────────────────────────
    # Die Route war das HTTP-Gegenstück zu `bibi-ctrl test` (in-place gegen den
    # Live-Checkout) und hatte weder eine Methode im DaemonClient noch einen
    # Button im Frontend — sie war ausschließlich per CLI erreichbar. Seit
    # /-/run selbst in-place läuft, ist sie ein exaktes Duplikat und darum
    # ersatzlos entfernt; das CLI-Verb `test` bleibt für eine Übergangszeit als
    # Deprecation-Alias auf `run` (ctrl/test_cmd.py).

    # ── /run/live: Zwischenstand laufender lokaler /run-Ausführungen ──────────
    # PLAN-21 Befund 10, 2. Nachtrag — s. Kommentar bei POST /-/run. Schlank
    # (nur id+started_at je Slug) für die Jobs-Liste; die Slug-Variante trägt
    # zusätzlich den vollen, live nachgelesenen Output (dieselbe Datei, die der
    # Wrapper noch schreibt) für die Job-Detailseite.
    @app.get("/-/run/live", tags=["job"])
    def run_live_list():
        return worker_mod.local_runs_live()

    @app.get("/-/run/live/{slug}", tags=["job"])
    def run_live_detail(slug: str):
        live = worker_mod.local_run_live(slug)
        if live is None:
            return JSONResponse(status_code=404, content={"error": "not running", "slug": slug})
        path = repo.root() / live["output_ref"]
        raw = output.read_events(path) if path.exists() else []
        kind = models.effective_kind(live.get("payload"))
        # Ausbau User-Fund 2026-07-10: HITL-Status/Demand/app_url für lokale
        # App-Jobs (s. worker.local_run_signal_state()) — sonst sah die
        # Job-Detailseite eines lokal per /run gestarteten App-Jobs nie
        # "awaiting", nur "running" (der Signal-Kanal existierte schlicht nicht).
        sig_state = worker_mod.local_run_signal_state(raw)
        # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED
        # nie im Dashboard gesehen"): sig_state["status"] kommt aus den
        # BIBI:-Signal-Events in output.jsonl und kennt strukturell nur
        # "running"/"awaiting" — "deferred"/"failed" laufen im Wrapper nie über
        # diesen Kanal (s. local_run_signal_state()-Docstring), der Default
        # blieb deshalb immer "running". live["status"] (DB-Spalte, jetzt dank
        # _PINNED_LIVE_STATUSES auch fuer deferred/failed vorhanden) traegt den
        # echten Wert. "awaiting" bleibt Signal-Vorrang (positives, aktives
        # Signal — die DB-Zeile bekommt es zwar meist auch selbst geschrieben,
        # s. wrapper _handle_signal(), aber nicht garantiert fuer jeden
        # Aufrufer/Testpfad ohne Scheduler-DB), sonst gewinnt die DB-Spalte.
        status = "awaiting" if sig_state["status"] == "awaiting" else (live.get("status") or "running")
        return {"slug": slug, "id": live["id"], "started_at": live["started_at"],
                "output_ref": live["output_ref"], "kind": kind,
                "status": status, "app_url": sig_state["app_url"],
                "demand": sig_state["demand"],
                "events": output_format.format_events(raw, kind)}

    # User-Fund 2026-07-10 (HITL-Test-App-Migration): "Da müssen wir dann aber
    # wohl nochmal ran! Natürlich müssen wir kill können" — ein langlebiger
    # App-Job über /run (z. B. eine HITL-Test-App mit serve_forever()) blieb
    # sonst nur per manuellem docker kill/SIGTERM von außen beendbar, kein
    # API-Weg. Analog zu POST /-/job/{id}/kill (Scheduler-Jobs) — PLAN-28
    # Refactor B: nutzt jetzt denselben pinned_worker.kill() wie der
    # Scheduler-Pfad (container-aware, DB-PID-Fallback nach Neustart), statt
    # einer eigenen, schmaleren Kill-Implementierung.
    @app.post("/-/run/live/{slug}/kill", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def run_live_kill(slug: str):
        live = worker_mod.local_run_live(slug)
        if live is None:
            return JSONResponse(status_code=404, content={"error": "not running", "slug": slug})
        # User-Fund 2026-07-20 ("KILL auf deferred/failed: nix passiert, dient
        # aber dem Stoppen"): ein ``not signaled`` früher hier hart auf 404
        # zurückzuschicken machte KILL auf ``deferred``/``failed`` zum stillen
        # No-Op — dort läuft gerade kein OS-Prozess (der Job wartet nur auf
        # seinen nächsten Redispatch), ``pinned_worker.kill()`` kann also gar
        # nichts signalisieren, obwohl der Zustandswechsel selbst (§5.4:
        # (FAILED|DEFERRED, KILL) → KILLED) völlig legitim ist. Jetzt wie
        # run_live_reset() unten: Signal ist best-effort, der DB-Write hängt
        # nicht an dessen Erfolg.
        signaled = pinned_worker.kill(live["id"])
        # User-Fund 2026-07-13 ("KILL führt nicht zum Status Wechsel"): anders
        # als job_kill() (Host, oben) schrieb diese Route den Status bisher nie
        # selbst, sondern verließ sich komplett auf den Wrapper-Selbstreport
        # nach SIGTERM — der über einen separaten Bug (SystemExit umging
        # main()s except Exception, s. wrapper/__init__.py::_on_sigterm())
        # nie ankam. Jetzt wie beim Host: direkt schreiben, statt zu warten.
        #
        # User-Fund 2026-07-13 ("kein Output nach Kill"): report_status() setzt
        # output_ref nur, wenn explizit übergeben — sonst bleibt die Spalte, wie
        # sie ist (hier: NULL, da run_pinned()s INSERT sie nie füllt). Der
        # spätere, jetzt korrekte Wrapper-Terminal-Report käme zwar MIT
        # berechnetem output_ref, trifft aber auf eine bereits terminale Zeile
        # (unser direkter Write hier) und wird von report_status() als
        # idempotenter Wiederholungs-Report früh (target is current) ohne
        # Feld-Update verworfen — output_ref bliebe für immer NULL. live[] hat
        # den Pfad schon (lokal von local_run_live() berechnet, exakt wie es
        # der Wrapper selbst täte), also hier direkt mitschreiben.
        conn = job_db.connect(pinned_worker.db_path)
        try:
            job_db.report_status(conn, live["id"], status="killed", reason="by_user",
                                  output_ref=live["output_ref"])
        finally:
            conn.close()
        return {"slug": slug, "signaled": signaled}

    # User-Feedback 2026-07-13 ("warum nicht START, RESET und KILL wie auf
    # Host"): RESET ist der Not-Aus für eine hängen gebliebene Live-Anzeige —
    # anders als KILL oben (das nur bei tatsächlich gesendetem Signal den
    # Status schreibt, also wirkungslos bleibt, sobald kein greifbarer
    # Prozess mehr existiert, z. B. nach einem Daemon-Neustart oder einem
    # Wrapper-Absturz ohne Terminal-Report — genau die Bug-Klasse, die diese
    # Session mehrfach fand) erzwingt RESET den Terminalstatus IMMER, sobald
    # eine Zeile als running/awaiting registriert ist. Best-effort-Signal
    # zuerst (falls doch noch etwas lebt, wird es sauber beendet), aber der
    # DB-Write hängt nicht am Erfolg des Signals.
    @app.post("/-/run/live/{slug}/reset", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def run_live_reset(slug: str):
        live = worker_mod.local_run_live(slug)
        if live is None:
            # Bibi4-Iteration, User-Fund ("Reset Test Container: Laufzahl nach
            # COMPLETE -> KILL -> RESET -> START nicht zurückgesetzt"): ein
            # bereits terminaler Lauf (z. B. killed) hat keine "live"-Zeile
            # mehr (_PINNED_LIVE_STATUSES greift nicht) — vorher 404, stiller
            # No-Op, RESET wischte die Job-Daten hier nie. Jetzt: jüngste
            # Zeile unabhängig vom Status suchen und, falls vorhanden, wie
            # unten deren Job-Daten wischen (kein Signal/Status-Write nötig,
            # der Lauf ist ja schon terminal).
            row = worker_mod._pinned_last_row(slug)
            if row is None:
                return JSONResponse(status_code=404, content={"error": "not running", "slug": slug})
            job_db.wipe_job_data(row["id"])
            return {"slug": slug, "reset": True}
        pinned_worker.kill(live["id"])  # best-effort, Rückgabewert bewusst ignoriert
        # output_ref: gleicher Grund wie in run_live_kill() oben — sonst bleibt
        # die Spalte NULL und der spätere (falls doch noch einer kommt)
        # Wrapper-Report wird als No-Op verworfen.
        conn = job_db.connect(pinned_worker.db_path)
        try:
            job_db.report_status(conn, live["id"], status="killed", reason="reset_by_user",
                                  output_ref=live["output_ref"])
        finally:
            conn.close()
        # Bibi4 Batch 6 (RESET wischt Job-Daten, START bewahrt sie) galt bisher
        # nur für den Host-Pfad (job_reset() oben) — der Client-Pfad hier bekam
        # dieselbe Verdrahtung nie, obwohl RESET laut Verb-Modell uniform für
        # JOB/CLAUDE/APP gelten soll, Host wie Client.
        job_db.wipe_job_data(live["id"])
        return {"slug": slug, "reset": True}

    # User-Fund 2026-07-13 ("REBUILD müsste doch auch beim Client notwendig
    # sein, oder?"): REBUILD (PLAN-24 Befund 5) verwirft das per-Job-Image
    # eines Container-Jobs — auf dem Host längst verdrahtet (POST /-/job/{id}/
    # rebuild oben), auf dem Client bisher komplett vergessen. Anders als
    # KILL/RESET hängt REBUILD an keiner Live-Zeile — der Lookup geht direkt
    # über die Schedule-MD (local_schedule_exec_mode()), nicht über die DB.
    @app.post("/-/run/live/{slug}/rebuild", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def run_live_rebuild(slug: str):
        try:
            exec_mode = worker_mod.local_schedule_exec_mode(slug)
        except LookupError:
            return JSONResponse(status_code=404, content={"error": "unknown schedule", "slug": slug})
        if (exec_mode or "host").strip().lower() != "container":
            return JSONResponse(status_code=409, content={"error": "not a container job", "slug": slug})
        if not pinned_worker.rebuild_job_image(slug):
            return JSONResponse(status_code=502, content={"error": "docker command failed", "slug": slug})
        return {"slug": slug, "rebuilt": True}

    def _is_own_run(entry: dict | None) -> bool:
        # PLAN-28: "meine eigene /run-Historie" — domain='local' (historische
        # Zeilen vom alten CLI-Pfad, Refactor D entfernt — auf Bestandsknoten
        # können sie noch existieren) ODER pinned_host gesetzt (neuer HTTP-Pfad,
        # run_pinned() — echte jobs-Zeile, domain='scheduled', aber pinned_host
        # macht sie trotzdem eindeutig von Team-Queue-Läufen unterscheidbar).
        return entry is not None and (entry.get("domain") == "local"
                                      or entry.get("pinned_host") is not None)

    # ── /run/journal: lokale Lauf-Historie (§1.4) — rollenunabhängig ───────────
    # PLAN-17 Stufe 17.1 (Jobs-Screen): bewusst NICHT einfach /-/journal um
    # domain=local erweitert und dessen Gate gelockert — /-/journal ist Teil des
    # eingefrorenen v3.0-Vertrags (§1.1/§3.0, 501-Stub ohne scheduler-Rolle,
    # test_daemon_contract.py) und bleibt unangetastet. Diese Route ist neu,
    # ausschließlich domain="local" (die /run-Läufe DIESES Knotens), symmetrisch
    # zu /-/run selbst rollenunabhängig — ein reiner Client kann seine eigene
    # Lauf-Historie damit lesen, ohne je die scheduler-Rolle zu tragen.
    @app.get("/-/run/journal", tags=["job"])
    def run_journal(slug: str | None = None, limit: int | None = None,
                    offset: int | None = None):
        conn = job_db.connect()
        try:
            return job_db.list_journal(conn, slug=slug, mine_only=True, limit=limit, offset=offset)
        finally:
            conn.close()

    # PLAN-21 Befund 10: Einzel-Lauf-Detail für lokale /run-Läufe, symmetrisch
    # zu /-/run/journal (Liste) — rollenunabhängig, damit ein reiner Client
    # seine eigene Lauf-Historie auch im Detail sehen kann, ohne je die
    # scheduler-Rolle zu tragen. Bewusst NICHT /-/journal/{jid} wiederverwendet
    # (eingefrorener v3.0-Vertrag, 501-Stub ohne scheduler-Rolle) — nur
    # domain="local" wird ausgeliefert, alles andere 404 (kein Leck disponierter
    # Läufe über diese eigentlich rollenfreie Route).
    @app.get("/-/run/journal/{jid}", tags=["job"])
    def run_journal_get(jid: int):
        conn = job_db.connect()
        try:
            entry = job_db.get_journal(conn, jid)
        finally:
            conn.close()
        if not _is_own_run(entry):
            return JSONResponse(status_code=404,
                                content={"error": "local run not found", "id": jid})
        return entry

    # PLAN-21 Befund 10-Nachtrag (Jobs-Screen-Detail): Löschen für lokale
    # Läufe, symmetrisch zu DELETE /-/journal/{jid} (§1.4) aber rollenunab-
    # hängig und domain="local"-gated wie die beiden Routen oben — sonst
    # könnte ein reiner Client seine eigene Lauf-Historie nie aufräumen.
    @app.delete("/-/run/journal/{jid}", tags=["job"],
               dependencies=[Depends(_require_approved_or_local)])
    def run_journal_delete(jid: int):
        conn = job_db.connect()
        try:
            entry = job_db.get_journal(conn, jid)
            if not _is_own_run(entry):
                return JSONResponse(status_code=404,
                                    content={"error": "local run not found", "id": jid})
            job_db.delete_journal(conn, jid)
        finally:
            conn.close()
        return {"deleted": jid}

    def _own_run_events(jid: int) -> tuple[dict | None, list]:
        conn = job_db.connect()
        try:
            entry = job_db.get_journal(conn, jid)
        finally:
            conn.close()
        if not _is_own_run(entry):
            return None, []
        # Derselbe run_id-Fallback wie _journal_events(), s. _journal_output_path().
        return entry, output.read_events(_journal_output_path(entry))

    @app.get("/-/run/journal/{jid}/output", tags=["job"])
    def run_journal_output(jid: int):
        # Analogon zu /-/journal/{jid}/output (§4.2), nur über _is_own_run()
        # statt scheduler-gated.
        entry, raw = _own_run_events(jid)
        if entry is None:
            return JSONResponse(status_code=404,
                                content={"error": "local run not found", "id": jid})
        kind = models.effective_kind(entry.get("payload"))
        return {"id": jid, "kind": kind, "events": output_format.format_events(raw, kind),
                "output_ref": entry.get("output_ref")}

    def _own_run_sse(jid: int, stream: str | None) -> StreamingResponse | JSONResponse:
        # User-Feedback 2026-07-13 ("Warum nicht die gleiche Ansicht? Warum
        # nicht die gleiche Logik?"): execution_detail_page() unterdrückte
        # die rohen out/err/stream-Links für eigene/gepinnte Läufe bisher,
        # weil es dafür keine rollenunabhängige Route gab — Analogon zu
        # _journal_sse() (§4.2/PLAN-14 Stufe 14.0), nur über _is_own_run()
        # statt scheduler-gated.
        entry, events = _own_run_events(jid)
        if entry is None:
            return JSONResponse(status_code=404,
                                content={"error": "local run not found", "id": jid})

        def gen():
            for e in events:
                if stream is None or e.get("s") == stream:
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/-/run/journal/{jid}/out", tags=["job"])
    def run_journal_out(jid: int):
        return _own_run_sse(jid, "out")

    @app.get("/-/run/journal/{jid}/err", tags=["job"])
    def run_journal_err(jid: int):
        return _own_run_sse(jid, "err")

    @app.get("/-/run/journal/{jid}/stream", tags=["job"])
    def run_journal_stream(jid: int):
        return _own_run_sse(jid, None)

    # ── /events: der globale FE-Event-Strom (PLAN-36 Stufe 36.1) ──────────────
    # Rollenunabhängig (E1): EIN Strom pro Daemon, identisch für jeden Tab und
    # jeden maschinellen Konsumenten — kein Screen-/Slug-Filter; ein Event
    # trägt seine Ziel-ID, wer das Element nicht zeigt, ignoriert es. Reine
    # Lese-Route: EventSource kann keine Header setzen, dieselbe bewusste
    # Tailnet-Offenheit wie die bestehenden Status-/Output-Lese-Routen
    # (Case-Doku Job-Control-Approval-Bug, „Bewusst NICHT gegatet").
    # Beim Connect: einmalige Dirty-Meldungen für alle aktiven Läufe (E5) —
    # direkt nach dem Seitenaufbau ein No-op (Zustands-Events sind idempotent),
    # nach einem Reconnect die vollständige Heilung. Ping-Kommentarzeilen bei
    # >=EVENTS_PING_S Sendepause (dasselbe Tailscale-Abriss-Argument wie
    # _formatted_sse(); Modul-Konstante statt Literal — Tests takten sie
    # runter, sonst hängt jedes Stream-Schließen bis zum nächsten yield,
    # weil erst der fehlschlagende Write auf die geschlossene Verbindung
    # den Generator beendet).
    # ``limit``: Strom endet nach N data-Events (None = endlos, der Normalfall).
    # Für Diagnose (`curl /-/events?limit=10`) und Tests — der TestClient-
    # ASGI-Transport kennt keinen echten Client-Disconnect, ein endloser
    # Generator liefe dort ewig weiter.
    @app.get("/-/events", tags=["daemon"],
             dependencies=[Depends(_require_approved_if_identified)])
    async def events(limit: int | None = None):
        sub = bus.subscribe()
        conn = job_db.connect()
        try:
            active = conn.execute(
                "SELECT slug, pinned_host FROM jobs WHERE active=1 "
                "AND status IN ('starting','running','awaiting','deferred')").fetchall()
        finally:
            conn.close()
        from bibi.daemon.bus import bucket_slug
        resync = []
        for r in active:
            targets = [r["slug"]]
            b = bucket_slug(r["slug"], r["pinned_host"])
            if b:
                targets.append(b)  # gepinnte Läufe: Client-Seite adressiert per Bucket
            for t in targets:
                resync.append({"t": "state", "target": f"live:{t}"})
                resync.append({"t": "state", "target": f"journal:{t}"})
        async def gen():
            seq = 0
            try:
                yield 'data: {"t":"hello"}\n\n'
                seq += 1
                if limit is not None and seq >= limit:
                    return
                for ev in resync:
                    seq += 1
                    yield f"id: {seq}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if limit is not None and seq >= limit:
                        return
                while True:
                    batch = await bus.wait(sub, timeout=EVENTS_PING_S)
                    # m.rau/bibi#176: der Knoten fährt herunter — dann endet
                    # dieser Strom von selbst, statt in uvicorns Frist zu
                    # laufen und als abgebrochene Task im Traceback zu landen.
                    # ``begin_shutdown()`` weckt jeden Abonnenten, der Check
                    # steht deshalb direkt hinter dem Aufwachen und braucht
                    # keine eigene Schleife.
                    if bus.closing:
                        yield 'data: {"t":"bye"}\n\n'
                        return
                    if not batch:
                        # Echtes data-Event statt SSE-Kommentarzeile (PLAN-36
                        # Stufe 36.3): Kommentare erreichen JS nie (die
                        # EventSource-API liefert sie nicht aus) — der Client-
                        # Watchdog in _EVENTS_JS braucht aber ein sichtbares
                        # Lebenszeichen, um einen still gestorbenen Strom zu
                        # erkennen und neu zu verbinden (ersetzt die früheren
                        # Sicherheitsnetz-Polls vollständig). Zählt bewusst
                        # nicht gegen ``limit`` (sonst endete ein Diagnose-
                        # /Test-Stream mitten im Leerlauf).
                        yield 'data: {"t":"ping"}\n\n'
                        continue
                    for ev in batch:
                        seq += 1
                        yield f"id: {seq}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                        if limit is not None and seq >= limit:
                            return
            finally:
                bus.unsubscribe(sub)
        return StreamingResponse(gen(), media_type="text/event-stream")

    # ── /feed: Git-Historie zu Einheiten — rollenunabhängig ────────────────────
    # Reine Git-/Filesystem-Introspektion (bibi/feed.py), kein job_db-Zugriff —
    # funktioniert auf jedem Knoten, auch einem reinen Client ohne Scheduler/
    # Worker. Eigenständig abfragbar, nicht nur ins Feed-HTML gebacken.
    @app.get("/-/feed", tags=["daemon"])
    def feed(days: int | None = None):
        from bibi import feed as feed_mod
        root = repo.root()
        commits = feed_mod.collect_commits(root, since_days=days)
        slugs = feed_mod.agent_slugs(root, since_days=days)
        cases = feed_mod.discover_cases(root, case_dir_name=repo.case_dir_name())
        entries = feed_mod.group_entries(commits, slugs, cases=cases)
        # Was noch nicht committet ist, steht in keinem `git log` und waere
        # sonst der einzige Zustand des Vaults, den der Feed nicht kennt
        # (m.rau/bibi#133). Haengt bewusst nicht am `days`-Fenster: offen ist
        # offen, unabhaengig davon, wie weit man zurueckblickt.
        offen = feed_mod.uncommitted_units(root, cases=cases)
        return {
            "since_days": days,
            "commit_base_url": feed_mod.remote_commit_base_url(root),
            "uncommitted": [
                {"unit": e.unit, "last_changed": e.last_changed,
                 "author": e.author, "states": list(e.states), "changes": e.changes}
                for e in offen
            ],
            "entries": [
                {"unit": e.unit, "last_changed": e.last_changed,
                 "last_commit_sha": e.last_commit_sha,
                 "authors": sorted(e.authors), "changes": e.changes}
                for e in entries
            ],
        }

    @app.get("/-/log/stream", tags=["daemon"])
    def log_stream(n: int = Query(50, ge=0, le=1000), follow: bool = True):
        """Live-Aktivitätslog als SSE (§5.4 Slice B): Backfill der letzten ``n``
        JSONL-Zeilen + neue Events. Reine JSON/SSE-API (§1.1) — der Controller/FE
        ist nur Client. ``follow=false`` = nur Backfill (Snapshot, terminiert)."""
        path = repo.root() / "data" / "daemon-log" / activity.LOG_FILENAME
        broadcaster = activity.get_broadcaster()

        async def gen():
            for line in activity.tail_lines(path, n):
                yield f"data: {line}\n\n"
            if not follow:
                return
            q = broadcaster.subscribe()
            try:
                while True:
                    line = await q.get()
                    yield f"data: {line}\n\n"
            finally:
                broadcaster.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ── Statusmeldung: rollenunabhängig (PLAN-30 Ebene 1 v2) ────────────────
    # Zuerst registriert ⇒ gewinnt gegen den 3.0-Contract-Stub für /-/scheduler/
    # status/{id} — auf JEDEM Knoten, nicht nur mit roles.scheduler (s. Docstring
    # von _add_status_route()).
    _add_status_route(app, sync_lock=sync_lock, synchronizer=synchronizer)
    # Journal-Liste ebenso: jeder Knoten führt sein eigenes (m.rau/bibi#103).
    _add_journal_route(app)
    # /-/restart ebenso rollenunabhängig: ein Deploy trifft alle Knoten, und der
    # häufigste Adressat ist ein reiner Client (s. _add_daemon_routes()). Der
    # sync_lock wird durchgereicht, damit der Deploy-Pull nicht mit dem
    # Synchronizer ins selbe Repo greift.
    _add_daemon_routes(app, sync_lock=sync_lock, worker=worker,
                       pinned_worker=pinned_worker, session_scoped=session_scoped)

    # ── Scheduler-Rolle: übrige echte DB-Routen (PLAN-3 §3.1) ───────────────
    # Zuerst registriert ⇒ gewinnen gegen die 3.0-Contract-Stubs für /-/job.
    if roles.scheduler:
        _add_scheduler_routes(app, worker_registry,
                              sync_lock=sync_lock, synchronizer=synchronizer)

    # ── Worker-Rolle: Job-Streams + kill (PLAN-3 §3.3) ──────────────────────
    if worker is not None:
        _add_worker_routes(app, worker)

    # ── Controller-Rolle: Web-App-Wurzel auf /-/ (PLAN-4 §2.1/§4.1) ─────────
    if roles.controller:
        from bibi.controller import ControllerClient, add_controller_routes
        client = controller_client or ControllerClient(
            controller_base_url or f"http://127.0.0.1:{config.daemon_port()}")
        add_controller_routes(app, roles, client)

    # ── Gefrorener /-/-Vertrag (PLAN-3 §1.1/§3.0) ───────────────────────────
    # job/scheduler/worker/journal als versionierte Schemata + 501-Stubs; die
    # echte Implementierung kommt stufenweise (3.1–3.6). Reine JSON-API (§1.1).
    openapi.add_contract_routes(app)

    # ── Synchronizer-Laufzeit-Toggle (nur wenn Rolle aktiv) ─────────────────

    if synchronizer is not None:

        @app.post("/-/synchronizer/pull")
        def pull_on():
            synchronizer.set_pull(True)
            return synchronizer.status()

        @app.delete("/-/synchronizer/pull")
        def pull_off():
            synchronizer.set_pull(False)
            return synchronizer.status()

        @app.post("/-/synchronizer/push")
        def push_on():
            synchronizer.set_push(True)
            return synchronizer.status()

        @app.delete("/-/synchronizer/push")
        def push_off():
            synchronizer.set_push(False)
            return synchronizer.status()

    return app
