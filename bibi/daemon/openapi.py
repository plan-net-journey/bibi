"""Gefrorener ``/-/``-API-Vertrag: Schemata + Route-Stubs (DESIGN §4.4/§4.5/§1.4;
PLAN-3 §1.1/§3.0).

Stufe 3.0 materialisiert die *Wirbelsäule*: der gesamte Job-/Scheduler-/Worker-/
Journal-Vertrag wird als **versionierte** Spec definiert (Pydantic-Schemata →
``/-/openapi.json``), die Routen existieren als **Stubs (501 Not Implemented)**.
Nichts führt aus — aber der Vertrag steht und ist ab hier ein bewusst designtes,
eingefrorenes Artefakt (§1.1).

**Reine JSON/SSE-API — kein HTML in Routen** (§1.1, Korrektur an bibi3): jede
Antwort ist JSON; spätere SSE-Streams (§4.5) tragen ``text/event-stream``. Das
Web-FE ist ein separater Konsument (PLAN-4), kein Bestandteil dieser Spec.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bibi.schedule.models import Kind, Reason, Status

#: Vertrags-Version — bei Änderungen am ``/-/``-Vertrag bewusst hochzählen (§1.1).
CONTRACT_VERSION = "3.1"


# ── Schemata (§4.4/§4.5/§1.4) ───────────────────────────────────────────────


class JobView(BaseModel):
    """Eine Job-Zeile aus der Scheduler-DB (§4.4). Output-frei — ``output_ref``
    zeigt auf die ``output.jsonl`` beim Worker (§1.4)."""

    id: str
    slug: str
    kind: Kind
    status: Status
    reason: Reason | None = None
    priority: int = 0
    enqueued_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    attempt: int = 0
    host: str | None = None
    worker: str | None = None
    output_ref: str | None = None
    next_fire_at: float | None = None  # nächster geplanter Trigger (überfällig-Anzeige)


class ScheduleView(BaseModel):
    """Ein erfasster Schedule, korreliert mit der letzten Ausführung (§4.4, A13)."""

    slug: str
    kind: Kind
    trigger: str
    next_fire_at: float | None = None
    last_status: Status | None = None
    last_run_at: float | None = None


class NextRequest(BaseModel):
    """``POST /-/scheduler/next`` — optional mit Worker-Feature-Attributen (§4.4)."""

    worker: str | None = None
    feature: dict[str, object] | None = None


class JobReservation(BaseModel):
    """Antwort auf ``/-/scheduler/next``: der atomar reservierte Job + Ausführungs-
    Env für den Wrapper (§4.4/§7.5)."""

    id: str
    slug: str
    kind: Kind
    payload: str
    model: str | None = None
    soul: str | None = None
    session: str | None = None
    attempt: int = 0
    attempts: int = 1
    backoff: str = "fixed"
    wall_time: int | None = None
    silence_timeout: int | None = None
    env: dict[str, str] = Field(default_factory=dict)


class StatusReport(BaseModel):
    """``POST /-/scheduler/status/{id}`` — Worker meldet Zustandswechsel zurück.
    Der Scheduler ist **output-frei** (§4.4): nur Zustände, nie Output."""

    status: Status
    reason: Reason | None = None
    exit_code: int | None = None
    host: str | None = None
    worker: str | None = None
    output_ref: str | None = None
    attempt: int | None = None        # Retry-Accounting (§3.5/§3.6)
    next_fire_at: float | None = None  # Backoff-Zeitpunkt
    commit_sha: str | None = None      # Worktree-Commit des Laufs (v6, F7-Link)
    branch: str | None = None          # agent/<slug> (v6)


class KillRequest(BaseModel):
    """``POST /-/job/{id}/kill`` — optionale Kill-Ursache (§5.5)."""

    reason: Reason | None = None


class JournalEntryView(BaseModel):
    """Eine Journal-Zeile (§1.4). ``host``/``worker`` first-class (föderierte
    A13-Sicht), ``output_ref`` referenziert die ``output.jsonl``."""

    id: int | None = None  # DB-Zeilen-ID — Schlüssel für DELETE /-/journal/{id}
    run_id: str
    slug: str
    kind: Kind
    status: Status
    reason: Reason | None = None
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    exec_runtime: float | None = None
    host: str | None = None
    worker: str | None = None
    output_ref: str | None = None
    commit_sha: str | None = None  # Worktree-Commit des Laufs (v6, F7-Link)
    branch: str | None = None      # agent/<slug> (v6)
    domain: str = "scheduled"  # 'scheduled' (disponiert) | 'local' (/run), §1.4


class RunRequest(BaseModel):
    """``POST /-/run`` — lokale On-Demand-Ausführung (§3.3b). Entweder ``slug``
    (eine erfasste Schedule-MD) **oder** ``cmd`` (ad-hoc, rein lokal)."""

    slug: str | None = None
    cmd: str | None = None
    kind: str = "job"


class WorkerHeartbeat(BaseModel):
    """``POST /-/worker`` — Anmeldung/Heartbeat eines verbundenen Workers (A12, §3.6)."""

    worker: str
    host: str
    git_status: str | None = None


class WorkerView(BaseModel):
    """Ein beim Scheduler angemeldeter Worker (§4.5, A12) — Heartbeat + Git-Status."""

    worker: str
    host: str
    connected_at: float | None = None
    last_heartbeat: float | None = None
    git_status: str | None = None
    stale: bool = False


def _todo(endpoint: str) -> JSONResponse:
    """Einheitlicher 501-Stub — JSON, nie HTML (§1.1)."""
    return JSONResponse(
        status_code=501,
        content={
            "error": "not implemented",
            "stufe": "3.0 (Vertrag eingefroren, Implementierung folgt)",
            "endpoint": endpoint,
        },
    )


def add_contract_routes(app: FastAPI) -> None:
    """Registriert die gefrorenen ``/-/``-Routen als 501-Stubs am Daemon.

    Die ``response_model``-Deklarationen tragen die Schemata in
    ``/-/openapi.json`` ein (der eingefrorene Vertrag); die Handler liefern
    bewusst 501, bis die jeweilige Stufe sie ersetzt (3.1 DB/Listen, 3.2
    Scheduler-Auswahl, 3.3 Worker/Streams, 3.5 Journal, 3.6 Worker-Verbund).
    """

    # ── Scheduler (§4.4) ─────────────────────────────────────────────────────
    @app.post("/-/scheduler/next", response_model=JobReservation, tags=["scheduler"])
    def scheduler_next(req: NextRequest | None = None):  # noqa: ARG001
        return _todo("POST /-/scheduler/next")

    @app.post("/-/scheduler/status/{id}", tags=["scheduler"])
    def scheduler_status(id: str, report: StatusReport):  # noqa: A002, ARG001
        return _todo("POST /-/scheduler/status/{id}")

    # ── Job: Scheduler-Sicht (DB-Rows, §4.4) ─────────────────────────────────
    @app.get("/-/job", response_model=list[JobView], tags=["job"])
    def job_list(status: Status | None = None):  # noqa: ARG001
        return _todo("GET /-/job")

    @app.get("/-/job/{id}", response_model=JobView, tags=["job"])
    def job_get(id: str):  # noqa: A002, ARG001
        return _todo("GET /-/job/{id}")

    # ── Job: Worker-Sicht (Status/Streams/Verben, §4.5/§5.6) ─────────────────
    @app.get("/-/job/{id}/status", response_model=JobView, tags=["job"])
    def job_status(id: str):  # noqa: A002, ARG001
        return _todo("GET /-/job/{id}/status")

    @app.get("/-/job/{id}/out", tags=["job"])
    def job_out(id: str, from_: int = 0):  # noqa: A002, ARG001
        return _todo("GET /-/job/{id}/out")  # SSE (§4.5), folgt in 3.3

    @app.get("/-/job/{id}/err", tags=["job"])
    def job_err(id: str, from_: int = 0):  # noqa: A002, ARG001
        return _todo("GET /-/job/{id}/err")

    @app.get("/-/job/{id}/log", tags=["job"])
    def job_log(id: str):  # noqa: A002, ARG001
        return _todo("GET /-/job/{id}/log")  # rohe output.jsonl

    @app.get("/-/job/{id}/stream", tags=["job"])
    def job_stream(id: str, from_: int = 0):  # noqa: A002, ARG001
        return _todo("GET /-/job/{id}/stream")

    @app.post("/-/job/{id}/kill", tags=["job"])
    def job_kill(id: str, req: KillRequest | None = None):  # noqa: A002, ARG001
        return _todo("POST /-/job/{id}/kill")

    @app.post("/-/job/{id}/start", tags=["job"])
    def job_start(id: str):  # noqa: A002, ARG001
        return _todo("POST /-/job/{id}/start")  # §5.6 Verb

    @app.post("/-/job/{id}/reset", tags=["job"])
    def job_reset(id: str):  # noqa: A002, ARG001
        return _todo("POST /-/job/{id}/reset")  # §5.6 Verb

    # ── Worker-Verbund (§4.5, A12) ───────────────────────────────────────────
    @app.get("/-/worker", response_model=list[WorkerView], tags=["worker"])
    def worker_list():
        return _todo("GET /-/worker")

    # ── Journal (§1.4) ───────────────────────────────────────────────────────
    @app.get("/-/journal", response_model=list[JournalEntryView], tags=["journal"])
    def journal_list(slug: str | None = None, host: str | None = None):  # noqa: ARG001
        return _todo("GET /-/journal")

    @app.delete("/-/journal/{jid}", tags=["journal"])
    def journal_delete(jid: int):  # noqa: ARG001
        return _todo("DELETE /-/journal/{id}")
