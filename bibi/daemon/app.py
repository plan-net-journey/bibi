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
import socket
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from bibi import config, repo, state
from bibi.daemon import activity, job_db, mergeback, openapi, output_format
from bibi.daemon import worker as worker_mod  # Modul-Alias (bibi.daemon.app.worker ist eine Worker-Instanz)
from bibi.schedule import models
from bibi.daemon.openapi import (
    JobReservation, JobView, KillRequest, NextRequest, RunRequest, StatusReport,
    WorkerHeartbeat, WorkerView,
)
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
    """Sperrt Job-Control-Routen (``/-/job*``-Aktionen, ``/-/run``, ``/-/test``)
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
    kein Teil dieses Fixes."""
    host = request.client.host if request.client else None
    if host in _LOCAL_CLIENT_HOSTS:
        return
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

    # ── Journal (disponierte Domäne, §1.4) ───────────────────────────────────
    @app.get("/-/journal", tags=["journal"])
    def journal(slug: str | None = None, host: str | None = None, domain: str | None = None,
                limit: int | None = None, offset: int | None = None):
        conn = job_db.connect()
        try:
            return job_db.list_journal(conn, slug=slug, host=host, domain=domain,
                                       limit=limit, offset=offset)
        finally:
            conn.close()

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
        ref = entry.get("output_ref")
        events = output.read_events(repo.root() / ref) if ref else []
        return entry, events

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

    # ── Lauf-Historie-Chart: Terminal-Landungen (PLAN-21 Befund 11 v2) ────────
    @app.get("/-/landings", tags=["journal"])
    def landings_list(since: float | None = None):
        # Dünne Landungs-Projektion (status+finished_at) — Bucket-Aggregation
        # für den Chart passiert im Controller/Render-Layer (reine Funktionen,
        # kein DB-Zugriff dort).
        conn = job_db.connect()
        try:
            return job_db.journal_landings(conn, since=since)
        finally:
            conn.close()

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
        if hb.node_id:
            conn = job_db.connect()
            try:
                status = job_db.node_approval_status(conn, hb.node_id)
            finally:
                conn.close()
            if status == "blocked":
                raise HTTPException(status_code=401, detail="node blocked by host operator")
        result = registry.heartbeat(hb.worker, hb.host, hb.git_status,
                                    node_id=hb.node_id, git_user=hb.git_user, role=hb.role,
                                    port=hb.port)
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

    @app.post("/-/worker/{node_id}/approve", tags=["worker"])
    def worker_approve(node_id: str):
        conn = job_db.connect()
        try:
            job_db.set_node_approval(conn, node_id, "approved")
        finally:
            conn.close()
        return {"node_id": node_id, "status": "approved"}

    @app.post("/-/worker/{node_id}/block", tags=["worker"])
    def worker_block(node_id: str):
        conn = job_db.connect()
        try:
            job_db.set_node_approval(conn, node_id, "blocked")
        finally:
            conn.close()
        return {"node_id": node_id, "status": "blocked"}

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
        # last_ping_at in der DB statt In-Memory-Timer im Wrapper (§2.5/PLAN-11.4) —
        # der Job meldet sich selbst lebendig, der Worker liest es fürs Zombie-Timeout.
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
        conn = job_db.connect(worker.db_path)
        try:
            outcome = job_db.report_status(conn, id, status="killed", reason="by_user")
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
    bus=None, collector=None,
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
    if collector is None:
        from bibi.daemon.bus import Collector
        collector = Collector(bus, registry=worker_registry)
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
                          local_worker_name=worker.worker_name if worker is not None else None)
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
            orphans = (job_db.reconcile_startup_orphans(conn, worker.worker_name)
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
        await collector.start()
        try:
            yield
        finally:
            await collector.stop()
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

    app = FastAPI(
        title="bibi · daemon",
        version=openapi.CONTRACT_VERSION,
        lifespan=lifespan,
        docs_url="/-/docs",       # Swagger-UI über die eingefrorene Spec
        redoc_url="/-/redoc",     # ReDoc-Alternative
        openapi_url="/-/openapi.json",
    )

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
            # Eigener Hostname (PLAN-21 Befund 6) — die Host-Karte zeigt ihn auf
            # Knoten ohne connect-Rolle statt des früheren "lokal"-Platzhalters.
            "hostname": socket.gethostname(),
        }
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

    # ── /run: lokale On-Demand-Ausführung (PLAN-3 §3.3b) — rollenunabhängig ────
    # User-Feedback 2026-07-06: hing bisher an _add_worker_routes() (nur mit
    # --worker registriert), obwohl der Dispatch selbst kein Worker-Objekt
    # braucht — sich repo_root/work_dir/db_path genau wie die CLI (run_cmd.py)
    # selbst über repo.root() auflöst. Ein reiner Client (kein --worker) konnte
    # /run dadurch nur per CLI nutzen, nie über den Browser/die API — dieselbe
    # Art Lücke wie beim Heartbeat (PLAN-17 Stufe 17.0).
    @app.post("/-/run", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def run(req: RunRequest):
        # PLAN-28: run_pinned() (Nachfolger des früheren, rein synchronen
        # run_local()) gibt dem Lauf jetzt eine echte jobs-Zeile
        # (pinned_host=dieser Host) und läuft durch dieselbe Retry/Error/
        # Deferred/Zombie-Maschine wie ein Scheduler-Job, bleibt aber hier
        # (pinned_host) und sofort (execute_reservation()s detach=True kehrt
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
            res = run_pinned(slug=req.slug, cmd=req.cmd, kind=req.kind,
                             register=pinned_worker._register, use_schedule_retry=True)
        except LookupError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — Route darf nie unbehandelt crashen
            activity.emit(log, logging.ERROR, "run.pinned_error",
                          "Gepinnter /run-Lauf fehlgeschlagen", role="daemon",
                          slug=slug, error=str(exc))
            return JSONResponse(status_code=500, content={"error": str(exc)})
        return {"id": res["id"], "slug": slug, "status": "running",
                "output_ref": res["output_ref"]}

    # ── /test: wie /-/run, aber in-place gegen den Live-Checkout ──────────────
    # User-Fund 2026-07-14 (bibi-ctrl test): kein frischer Worktree von trunk —
    # läuft direkt gegen repo_root (dirty erlaubt), committet nie danach.
    # Gleiches RunRequest-Schema, gleiche Rollen-Unabhängigkeit wie /-/run
    # (dieselbe Begründung: der Dispatch selbst braucht kein Worker-Objekt).
    @app.post("/-/test", tags=["job"],
             dependencies=[Depends(_require_approved_or_local)])
    def test(req: RunRequest):
        if not req.slug and not req.cmd:
            return JSONResponse(status_code=400, content={"error": "slug oder cmd nötig"})
        slug = req.slug or "adhoc"
        if worker_mod.local_run_live(slug) is not None:
            return JSONResponse(status_code=409,
                                content={"error": "already running", "slug": slug})

        try:
            res = run_pinned(slug=req.slug, cmd=req.cmd, kind=req.kind,
                             register=pinned_worker._register, in_place=True,
                             use_schedule_retry=True)
        except LookupError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — Route darf nie unbehandelt crashen
            activity.emit(log, logging.ERROR, "test.pinned_error",
                          "In-place /test-Lauf fehlgeschlagen", role="daemon",
                          slug=slug, error=str(exc))
            return JSONResponse(status_code=500, content={"error": str(exc)})
        return {"id": res["id"], "slug": slug, "status": "running",
                "output_ref": res["output_ref"]}

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
        ref = entry.get("output_ref")
        events = output.read_events(repo.root() / ref) if ref else []
        return entry, events

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
    @app.get("/-/events", tags=["daemon"])
    async def events(limit: int | None = None):
        sub = bus.subscribe()
        conn = job_db.connect()
        try:
            active = conn.execute(
                "SELECT slug, pinned_host FROM jobs WHERE active=1 "
                "AND status IN ('running','awaiting','deferred')").fetchall()
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

    # ── /feed: Git-Historie zu Entitäten + Heatmap (PLAN-18) — rollenunabhängig ─
    # Reine Git-/Filesystem-Introspektion (bibi/feed.py), kein job_db-Zugriff —
    # funktioniert auf jedem Knoten, auch einem reinen Client ohne Scheduler/
    # Worker. Eigenständig abfragbar (User-Wunsch "Heatmap auch query-fähig
    # machen"), nicht nur ins Feed-HTML gebacken.
    #
    # ``weeks`` ist **entkoppelt** von ``days`` (PLAN-20 Befund 3, User-Fund:
    # "Heatmap immer um eine Woche nachladen") — eigener ``collect_commits()``-
    # Aufruf mit eigenem Zeitfenster, nicht dieselbe (an ``days`` gebundene)
    # Commit-Liste wie die Änderungsliste. Sonst wäre die 5-Wochen-Heatmap beim
    # Default-Seitenaufruf (``days=1``) fast leer, weil sie nur die Commits
    # sähe, die die Liste ohnehin schon geladen hat.
    @app.get("/-/feed", tags=["daemon"])
    def feed(days: int | None = None, weeks: int | None = None):
        from bibi import feed as feed_mod
        root = repo.root()
        commits = feed_mod.collect_commits(root, since_days=days)
        agent_shas = feed_mod.agent_commit_shas(root, since_days=days)
        entities = feed_mod.group_entities(commits, agent_shas,
                                           case_dir_name=repo.case_dir_name())
        eff_weeks = weeks if weeks is not None else feed_mod.HEATMAP_WEEKS
        heatmap_commits = feed_mod.collect_commits(root, since_days=eff_weeks * 7)
        grid = feed_mod.heatmap_buckets(heatmap_commits, weeks=eff_weeks)
        return {
            "since_days": days,
            "weeks": eff_weeks,
            "commit_base_url": feed_mod.remote_commit_base_url(root),
            "entities": [
                {"kind": e.kind, "name": e.name, "last_changed": e.last_changed,
                 "last_commit_sha": e.last_commit_sha,
                 "authors": sorted(e.authors), "all_agent": e.all_agent}
                for e in entities
            ],
            "heatmap": grid,
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
