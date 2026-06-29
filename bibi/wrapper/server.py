"""Wrapper-HTTP-Server (PLAN-9 §2/§3, Phase 6).

Läuft auf Port 8080 (``BIBI_WRAPPER_PORT``) im selben Container wie die App.
Bedient nach außen (via Traefik) ``/-/job/{id}/*``; intern (localhost) kommen
``/-/signal/*``-Aufrufe von der App.

Slice 9.0: minimale Route ``GET /-/job/{id}/status``.
Slice 9.1: ``POST /-/signal/awaiting`` + ``POST /-/signal/running`` + HITL-Demand.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel


class WrapperState:
    """In-memory-Zustand des Wrappers. Thread-safe (Lock)."""

    def __init__(self, job_id: str, *,
                 scheduler_url: str | None = None,
                 scheduler_db_path: str | None = None,
                 app_port: int | None = None,
                 hitl_timeout: int | None = None,
                 wrapper_url: str | None = None) -> None:
        self.job_id = job_id
        self.scheduler_url = scheduler_url
        self.scheduler_db_path = scheduler_db_path
        self.app_port = app_port
        self.hitl_timeout = hitl_timeout
        self.wrapper_url = wrapper_url
        self.app_url: str | None = None  # HITL-Eingabe-Endpunkt (gesetzt via /signal/awaiting)
        self._status = "running"
        self._demand: dict | None = None
        self._last_activity = time.monotonic()
        self._lock = threading.Lock()
        self.deferred_time: int | None = None  # gesetzt durch /signal/deferred

    def touch(self) -> None:
        """Activity-Timer zurücksetzen (stdout/stderr-Zeile, /input, /ping)."""
        with self._lock:
            self._last_activity = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        """Sekunden seit letzter Activity."""
        with self._lock:
            return time.monotonic() - self._last_activity

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @status.setter
    def status(self, value: str) -> None:
        with self._lock:
            self._status = value

    @property
    def demand(self) -> dict | None:
        with self._lock:
            return self._demand

    @demand.setter
    def demand(self, value: dict | None) -> None:
        with self._lock:
            self._demand = value

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self._status,
                "job_id": self.job_id,
                "status": self._status,
                "demand": self._demand,
            }

    def report(self, status: str, *, reason: str | None = None,
               exit_code: int | None = None, output_ref: str | None = None) -> None:
        """Statuswechsel best-effort beim Scheduler melden (PLAN-9 §8 E2).

        Bevorzugt direkten SQLite-Zugriff (scheduler_db_path), sonst HTTP."""
        body: dict = {"status": status}
        if reason is not None:
            body["reason"] = reason
        if exit_code is not None:
            body["exit_code"] = exit_code
        if output_ref is not None:
            body["output_ref"] = output_ref
        if status == "awaiting" and self.app_url:
            body["app_url"] = self.app_url

        if self.scheduler_db_path:
            try:
                from pathlib import Path as _Path
                from bibi.daemon import job_db as _jdb
                conn = _jdb.connect(_Path(self.scheduler_db_path))
                try:
                    _jdb.report_status(conn, self.job_id, **{
                        k: v for k, v in body.items()
                        if k in ("status", "reason", "exit_code", "output_ref")
                    })
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass
            return

        if not self.scheduler_url:
            return
        url = f"{self.scheduler_url.rstrip('/')}/-/scheduler/status/{self.job_id}"
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0):  # noqa: S310
                pass
        except (urllib.error.URLError, OSError):
            pass


class AwaitingSignal(BaseModel):
    url: str                         # voller HITL-Eingabe-Endpunkt der App
    input_request: str               # Beschreibung / Prompt für den User
    input_format: str = "text"       # "text" | "choices" | …


class DeferredSignal(BaseModel):
    defer_time: int | None = None  # Sekunden bis zum nächsten Versuch (None → Wrapper-Default)


def make_app(state: WrapperState) -> FastAPI:
    """FastAPI-App für den Wrapper-Server."""
    app = FastAPI(docs_url=None, redoc_url=None)

    @app.get("/-/job/{job_id}/status")
    def get_status(job_id: str):
        if job_id != state.job_id:
            raise HTTPException(status_code=404, detail="job not found")
        return state.snapshot()

    @app.get("/-/job/{job_id}/input")
    def get_input(job_id: str):
        """HITL-Demand abrufen (Slice 9.1). Nur verfügbar solange state = awaiting."""
        if job_id != state.job_id:
            raise HTTPException(status_code=404, detail="job not found")
        demand = state.demand
        if demand is None:
            raise HTTPException(status_code=404, detail="no demand (not awaiting)")
        return demand

    @app.post("/-/signal/awaiting")
    def signal_awaiting(body: AwaitingSignal):
        """App meldet: warte auf Eingabe (PLAN-10 §10.4). FE postet direkt an body.url."""
        state.app_url = body.url
        state.demand = {
            "url": body.url,
            "input_request": body.input_request,
            "input_format": body.input_format,
        }
        state.status = "awaiting"
        state.report("awaiting")
        return {"ok": True}

    @app.post("/-/signal/running")
    def signal_running():
        """App meldet: aktiv. Demand + app_url werden gelöscht."""
        state.app_url = None
        state.demand = None
        state.status = "running"
        state.report("running")
        return {"ok": True}

    @app.post("/-/signal/deferred")
    def signal_deferred(body: DeferredSignal):
        """App meldet: selbst-defer (PLAN-10 §10.1). Wrapper killt Child + meldet DEFERRED."""
        state.deferred_time = body.defer_time  # None → _finish nutzt BIBI_DEFER_TIME / Default
        state.status = "deferred"
        return {"ok": True}

    @app.post("/-/job/{job_id}/ping")
    def post_ping(job_id: str):
        """Explizites Keepalive: Activity-Timer zurücksetzen (PLAN-9 §6, Slice 9.3)."""
        if job_id != state.job_id:
            raise HTTPException(status_code=404, detail="job not found")
        state.touch()
        return {"ok": True}

    return app


def start_server(state: WrapperState, *, port: int = 8080) -> uvicorn.Server:
    """FastAPI-Server in einem Daemon-Thread starten.

    Gibt das ``uvicorn.Server``-Objekt zurück; ``server.should_exit = True``
    stoppt es sauber. Da der Thread als Daemon läuft, stirbt er spätestens
    mit dem Wrapper-Prozess."""
    config = uvicorn.Config(
        make_app(state), host="0.0.0.0", port=port,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="wrapper-http")
    thread.start()
    return server
