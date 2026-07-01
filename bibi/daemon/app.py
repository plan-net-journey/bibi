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
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse

from bibi import config, repo, state
from bibi.daemon import activity, job_db, mergeback, openapi, output_format
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
    def journal(slug: str | None = None, host: str | None = None):
        conn = job_db.connect()
        try:
            return job_db.list_journal(conn, slug=slug, host=host)
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

    @app.get("/-/feed/stream", tags=["feed"])
    def feed_stream(n: int = Query(50, ge=0, le=1000), follow: bool = True):
        """Feed-SSE (Frontend-Plan §C.0): Backfill der letzten ``n`` terminalen
        Journal-Läufe (**älteste zuerst** → der Client hängt unten an, Konsolen-Tail)
        + Live-Push bei jedem Journal-Write. Quelle ist das **Journal**, nicht der
        Log. ``follow=false`` = nur Backfill (Snapshot, terminiert). Reine SSE-API."""
        broadcaster = activity.get_feed_broadcaster()

        async def gen():
            conn = job_db.connect()
            try:
                rows = job_db.list_journal(conn)  # archived_at DESC
            finally:
                conn.close()
            recent = rows[:n] if n and n > 0 else rows
            for row in reversed(recent):  # ältester zuerst
                yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
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

    # ── /run: lokale On-Demand-Ausführung (PLAN-3 §3.3b) ──────────────────────
    @app.post("/-/run", tags=["job"])
    def run(req: RunRequest):
        if not req.slug and not req.cmd:
            return JSONResponse(status_code=400, content={"error": "slug oder cmd nötig"})
        try:
            return run_local(
                slug=req.slug, cmd=req.cmd, kind=req.kind,
                repo_root=worker.repo_root, work_dir=worker.work_dir,
                db_path=worker.db_path, register=worker._register,
            )
        except LookupError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})


def create_app(
    roles: Roles, synchronizer=None, worker: Worker | None = None, sweeper=None,
    rescanner=None, controller_client=None, controller_base_url: str | None = None,
    sync_lock=None,
) -> FastAPI:
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
        sweeper = Sweeper(registry=worker_registry)
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

    def _feed_publish(row: dict) -> None:
        # Journal-Zeile → Feed-Broadcaster (Frontend-Plan §C.0). Best-effort, blendet
        # nie in den Status-Pfad zurück (job_db._notify_journal fängt Fehler).
        activity.get_feed_broadcaster().publish(json.dumps(row, ensure_ascii=False))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if synchronizer is not None:
            await synchronizer.start()
        if roles.scheduler:
            _scheduler_startup()
            job_db.set_journal_listener(_feed_publish)  # Feed-Live-Push aktivieren
        if sweeper is not None:
            await sweeper.start()
        if rescanner is not None:
            await rescanner.start()
        if worker is not None:
            await worker.start()
        try:
            yield
        finally:
            if roles.scheduler:
                job_db.set_journal_listener(None)  # Feed-Hook wieder lösen
            if worker is not None:
                await worker.stop()
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
        }
        if synchronizer is not None:
            out["synchronizer"] = synchronizer.status()
        if worker_registry is not None:
            out["workers"] = worker_registry.list()
        # Verdikt „läuft alles?" — DB-nah, nur am Knoten mit Scheduler-Rolle (der
        # die Job-DB besitzt). Föderation aggregiert je-Knoten-/-/status (§2.2).
        if roles.scheduler:
            conn = job_db.connect()
            try:
                out["verdict"] = job_db.verdict(conn)
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
