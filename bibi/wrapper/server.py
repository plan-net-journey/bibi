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
import urllib.error
import urllib.request

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class WrapperState:
    """In-memory-Zustand des Wrappers. Thread-safe (Lock)."""

    def __init__(self, job_id: str, *, scheduler_url: str | None = None) -> None:
        self.job_id = job_id
        self.scheduler_url = scheduler_url
        self._status = "running"
        self._demand: dict | None = None
        self._lock = threading.Lock()

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

    def report(self, status: str) -> None:
        """Statuswechsel best-effort beim Scheduler melden (PLAN-9 §8 E2)."""
        if not self.scheduler_url:
            return
        url = f"{self.scheduler_url.rstrip('/')}/-/scheduler/status/{self.job_id}"
        payload = json.dumps({"status": status}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0):  # noqa: S310
                pass
        except (urllib.error.URLError, OSError):
            pass  # best-effort: Netzfehler nicht hochpropagieren


class AwaitingSignal(BaseModel):
    prompt: str
    choices: list[str] | None = None
    input_path: str | None = None


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
        """App meldet: warte auf Eingabe (PLAN-9 §3, Slice 9.1)."""
        state.demand = {
            "prompt": body.prompt,
            "choices": body.choices,
            "input_path": body.input_path,
            "mediated": body.input_path is not None,
        }
        state.status = "awaiting"
        state.report("awaiting")
        return {"ok": True}

    @app.post("/-/signal/running")
    def signal_running():
        """App meldet: aktiv (PLAN-9 §3, Slice 9.1). Demand wird gelöscht."""
        state.demand = None
        state.status = "running"
        state.report("running")
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
