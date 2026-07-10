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
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
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
from bibi.daemon.worker import Worker, run_local
from bibi.daemon.worker_registry import WorkerRegistry
from bibi.schedule.lifecycle import TERMINAL
from bibi.schedule.models import Status
from bibi.wrapper import output

log = logging.getLogger("bibi.daemon")


def _merge_back(branch: str, *, sync_lock=None, synchronizer=None) -> None:
    """``agent/<slug>`` nach trunk mergen (PLAN-6) und — bei Zustimmung — pushen.

    Defensiv: jeder Fehler bleibt hier (der Lauf ist bereits terminal ``complete``,
    der Commit über den Branch erreichbar). Konflikt → ``sync_conflict`` (auflösbar)."""
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
        state.set_sync_conflict(True)
        activity.emit(log, logging.WARNING, "worker.merge_conflict",
                      "Merge-back-Konflikt — trunk unverändert, Branch intakt (/sync)",
                      role="scheduler", slug=slug, detail=res.detail)
    elif res.status == "error":
        activity.emit(log, logging.ERROR, "worker.merge_error",
                      "Merge-back-Fehler", role="scheduler", slug=slug, detail=res.detail)


def _add_scheduler_routes(app: FastAPI, registry: WorkerRegistry,
                          *, sync_lock=None, synchronizer=None) -> None:
    """Echte DB-gestützte Scheduler-Routen (PLAN-3 §3.1) — nur bei aktiver
    ``scheduler``-Rolle (sie hält die Job-DB, §4.4). Ersetzen die 3.0-Stubs für
    ``/-/job``/``/-/job/{id}`` (zuerst registriert ⇒ gewinnen) und den
    Phase-2-Stub von ``/-/rescan``/``/-/schedule``. Reine JSON-API (§1.1).

    ``sync_lock``/``synchronizer``: Merge-back ``agent/<slug>`` → trunk nach einem
    erfolgreichen Lauf (PLAN-6) — scheduler-seitig (hier lebt das trunk-Repo)."""

    # Optionaler Shared-Secret-Schutz für Verbund-Endpunkte (§1.3): ist
    # BIBI_CONNECT_SECRET gesetzt, müssen Remote-Worker den Header mitschicken;
    # ohne Secret gilt die Loopback-/Trust-Netz-Annahme (Single-Node/Tailscale).
    _secret = os.environ.get("BIBI_CONNECT_SECRET")

    def _auth(x_bibi_secret: str | None = Header(default=None)):
        if _secret and x_bibi_secret != _secret:
            raise HTTPException(status_code=401, detail="bad or missing shared secret")

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

    @app.get("/-/job", response_model=list[JobView], tags=["job"])
    def job_list(status: str | None = None):
        conn = job_db.connect()
        try:
            return job_db.list_jobs(conn, status=status)
        finally:
            conn.close()

    @app.get("/-/job/{id}", response_model=JobView, tags=["job"])
    def job_get(id: str):  # noqa: A002
        conn = job_db.connect()
        try:
            job = job_db.get_job(conn, id)
        finally:
            conn.close()
        if job is None:
            return JSONResponse(status_code=404, content={"error": "job not found", "id": id})
        return job

    # ── Scheduler-Auswahl: Reservierung + Statusmeldung (PLAN-3 §3.2) ─────────
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

    @app.post("/-/scheduler/status/{id}", tags=["scheduler"], dependencies=[Depends(_auth)])
    def scheduler_status(id: str, report: StatusReport):  # noqa: A002
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
    @app.post("/-/worker", tags=["worker"], dependencies=[Depends(_auth)])
    def worker_heartbeat(hb: WorkerHeartbeat):
        return registry.heartbeat(hb.worker, hb.host, hb.git_status)

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

    @app.post("/-/job/{id}/ping", tags=["job"])
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
        conn = job_db.connect(worker.db_path)
        try:
            job = job_db.get_job(conn, job_id)
        finally:
            conn.close()
        kind = models.effective_kind((job or {}).get("payload"))
        path = worker.output_path(job_id)

        async def gen():
            sent = from_offset
            while True:
                formatted = output_format.format_events(output.read_events(path), kind)
                for e in formatted[sent:]:
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                sent = len(formatted)
                st = _job_status(job_id)
                if (st is not None and Status(st) in TERMINAL
                        and sent >= len(output_format.format_events(output.read_events(path), kind))):
                    break
                await asyncio.sleep(0.2)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/-/job/{id}/output/stream", tags=["job"])
    def job_output_stream(id: str, from_: int = Query(0, alias="from")):  # noqa: A002
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

    @app.post("/-/job/{id}/kill", tags=["job"])
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

    @app.post("/-/job/{id}/reset", tags=["job"])
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
        return {"id": id, "status": "pending"}

    @app.post("/-/job/{id}/start", tags=["job"])
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


def create_app(
    roles: Roles, synchronizer=None, worker: Worker | None = None, sweeper=None,
    rescanner=None, controller_client=None, controller_base_url: str | None = None,
    sync_lock=None, heartbeat=None,
) -> FastAPI:
    started_at = time.time()
    if worker is None and roles.worker:
        worker = Worker(worker_name="local")
    # Merge-back für den **lokalen** Worker (PLAN-6): der geht nicht über die
    # HTTP-Route /-/scheduler/status, darum den Hook direkt an den LocalScheduler
    # hängen. Nur am Knoten mit Scheduler-Rolle (besitzt trunk-Repo + Job-DB).
    if roles.scheduler and worker is not None:
        from bibi.daemon.scheduler_client import LocalScheduler
        if isinstance(getattr(worker, "client", None), LocalScheduler):
            worker.client.on_complete = lambda branch: _merge_back(
                branch, sync_lock=sync_lock, synchronizer=synchronizer)
    worker_registry = WorkerRegistry() if roles.scheduler else None
    if sweeper is None and roles.scheduler:
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
        try:
            yield
        finally:
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
            out["workers"] = worker_registry.list()
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
                # wie ``started_at`` selbst.
                out["job_stats"] = {
                    "counts": job_db.status_counts(conn),
                    "running_since_uptime": job_db.dispatch_count(),
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
    # --worker registriert), obwohl run_local() selbst kein Worker-Objekt
    # braucht — sich repo_root/work_dir/db_path genau wie die CLI (run_cmd.py)
    # selbst über repo.root() auflöst. Ein reiner Client (kein --worker) konnte
    # /run dadurch nur per CLI nutzen, nie über den Browser/die API — dieselbe
    # Art Lücke wie beim Heartbeat (PLAN-17 Stufe 17.0).
    #
    # PLAN-21 Befund 10, 2. Nachtrag (User-Fund 2026-07-09: "warum erscheinen
    # keine Details während des Laufes?"): run_local() läuft komplett synchron
    # (Subprozess spawnen → blockierend warten → Journal schreiben → erst dann
    # zurückkehren) — vorher blockierte diese Route bis zum Lauf-Ende, nichts
    # war währenddessen abfragbar (lokale Läufe haben bewusst keinen
    # jobs-Eintrag, s. run_local()-Docstring). run_local() selbst bleibt
    # SYNCHRON (die CLI, bibi-ctrl run, ruft es direkt auf und muss blockieren)
    # — nur diese Route startet es jetzt in einem Hintergrund-Thread und
    # antwortet, sobald der Wrapper-Subprozess gespawnt ist (über den längst
    # vorhandenen ``register``-Callback von _run_wrapper(), vorher nur fürs
    # Kill-Tracking scheduler-seitiger Jobs genutzt) — nicht erst, wenn der
    # Lauf fertig ist. GET /-/run/live[/{slug}] macht den Zwischenstand dann
    # abfragbar (dieselbe längst inkrementell geschriebene output.jsonl, s.
    # dort).
    @app.post("/-/run", tags=["job"])
    def run(req: RunRequest):
        if not req.slug and not req.cmd:
            return JSONResponse(status_code=400, content={"error": "slug oder cmd nötig"})
        slug = req.slug or "adhoc"
        if worker_mod.local_run_live(slug) is not None:
            return JSONResponse(status_code=409,
                                content={"error": "already running", "slug": slug})

        ready = threading.Event()
        handle: dict = {}
        root = repo.root()

        def on_spawn(job_id: str, proc) -> None:
            out_ref = (root / "data" / "job" / job_id / "output.jsonl").relative_to(root).as_posix()
            worker_mod.local_run_start(
                slug, job_id, out_ref, req.kind, req.cmd or req.slug or "", proc)
            handle["id"] = job_id
            handle["output_ref"] = out_ref
            ready.set()

        def go() -> None:
            try:
                run_local(slug=req.slug, cmd=req.cmd, kind=req.kind, register=on_spawn)
            except LookupError as exc:
                handle["error"] = str(exc)
                handle["status_code"] = 404
                ready.set()
            except Exception as exc:  # noqa: BLE001 — Hintergrund-Thread darf nie
                # unbeobachtet sterben: die auslösende Response ist zu diesem
                # Zeitpunkt oft schon zurück (register() hat ready schon
                # gesetzt), ein stiller Traceback auf stderr wäre die einzige
                # Spur. Wenigstens ins Activity-Log (§2.7).
                #
                # Bug gefunden bei der Live-Verifikation 2026-07-10: eine
                # Exception VOR dem Wrapper-Spawn (register() nie erreicht —
                # z. B. GitOpError bei worktree.prepare(), live beobachtet an
                # einem Repo mit liegengebliebenem Merge-Konflikt) ließ diesen
                # Zweig nur loggen, ohne ready.set() — die Route wartete dann
                # sinnlos bis zum vollen 30s-Timeout statt sofort den Fehler
                # zurückzugeben. ready.is_set() unterscheidet die beiden
                # Fälle: schon gesetzt (register() lief, Response ist längst
                # raus) → nur loggen; noch nicht gesetzt → Fehler + sofort
                # freigeben (500, nicht 404 — kein "not found"-Fall wie
                # LookupError, sondern ein echter Startfehler, z. B. ein
                # Worktree-Konflikt).
                if not ready.is_set():
                    handle["error"] = str(exc)
                    handle["status_code"] = 500
                    ready.set()
                activity.emit(log, logging.ERROR, "run.local_background_error",
                              "Lokaler /run-Hintergrund-Lauf abgebrochen", role="daemon",
                              slug=slug, error=str(exc))
            finally:
                worker_mod.local_run_end(slug)

        threading.Thread(target=go, daemon=True).start()
        if not ready.wait(timeout=30):
            return JSONResponse(status_code=504, content={"error": "timeout starting run"})
        if "error" in handle:
            return JSONResponse(status_code=handle["status_code"],
                                content={"error": handle["error"]})
        return {"id": handle["id"], "slug": slug, "status": "running",
                "output_ref": handle["output_ref"]}

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
        return {"slug": slug, "id": live["id"], "started_at": live["started_at"],
                "output_ref": live["output_ref"], "kind": kind,
                "status": sig_state["status"], "app_url": sig_state["app_url"],
                "demand": sig_state["demand"],
                "events": output_format.format_events(raw, kind)}

    # User-Fund 2026-07-10 (HITL-Test-App-Migration): "Da müssen wir dann aber
    # wohl nochmal ran! Natürlich müssen wir kill können" — ein langlebiger
    # App-Job über /run (z. B. eine HITL-Test-App mit serve_forever()) blieb
    # sonst nur per manuellem docker kill/SIGTERM von außen beendbar, kein
    # API-Weg. Analog zu POST /-/job/{id}/kill (Scheduler-Jobs), aber
    # rollenunabhängig + domain="local"-only (worker_mod.local_run_kill()).
    @app.post("/-/run/live/{slug}/kill", tags=["job"])
    def run_live_kill(slug: str):
        signaled = worker_mod.local_run_kill(slug)
        if not signaled:
            return JSONResponse(status_code=404, content={"error": "not running", "slug": slug})
        return {"slug": slug, "signaled": True}

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
            return job_db.list_journal(conn, slug=slug, domain="local", limit=limit, offset=offset)
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
        if entry is None or entry.get("domain") != "local":
            return JSONResponse(status_code=404,
                                content={"error": "local run not found", "id": jid})
        return entry

    # PLAN-21 Befund 10-Nachtrag (Jobs-Screen-Detail): Löschen für lokale
    # Läufe, symmetrisch zu DELETE /-/journal/{jid} (§1.4) aber rollenunab-
    # hängig und domain="local"-gated wie die beiden Routen oben — sonst
    # könnte ein reiner Client seine eigene Lauf-Historie nie aufräumen.
    @app.delete("/-/run/journal/{jid}", tags=["job"])
    def run_journal_delete(jid: int):
        conn = job_db.connect()
        try:
            entry = job_db.get_journal(conn, jid)
            if entry is None or entry.get("domain") != "local":
                return JSONResponse(status_code=404,
                                    content={"error": "local run not found", "id": jid})
            job_db.delete_journal(conn, jid)
        finally:
            conn.close()
        return {"deleted": jid}

    @app.get("/-/run/journal/{jid}/output", tags=["job"])
    def run_journal_output(jid: int):
        # Analogon zu /-/journal/{jid}/output (§4.2), nur domain="local".
        conn = job_db.connect()
        try:
            entry = job_db.get_journal(conn, jid)
        finally:
            conn.close()
        if entry is None or entry.get("domain") != "local":
            return JSONResponse(status_code=404,
                                content={"error": "local run not found", "id": jid})
        ref = entry.get("output_ref")
        raw = output.read_events(repo.root() / ref) if ref else []
        kind = models.effective_kind(entry.get("payload"))
        return {"id": jid, "kind": kind, "events": output_format.format_events(raw, kind),
                "output_ref": entry.get("output_ref")}

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

    # ── Scheduler-Rolle: echte DB-Routen (PLAN-3 §3.1) ──────────────────────
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
