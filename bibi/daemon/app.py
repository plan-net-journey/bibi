"""FastAPI-Skelett des Daemons (DESIGN §4.2/§4.8, PLAN-2 §2.2).

Alles, was der Daemon serviert, liegt unter dem reservierten Präfix ``/-/`` —
so kollidiert es nie mit App-Inhalts-Routen. **Singular**, HTTP nutzt ``status``
(getrennt vom ``/state``-Skill), Verben als Aktions-Subpfad.

``create_app(roles, synchronizer=None)`` ist eine Factory — testbar ohne realen
Synchronizer und ohne globale Rollen-Erkennung. Der Daemon-Entrypoint
(``daemon_cmd``) baut die App aus den aufgelösten Rollen.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from bibi import state
from bibi.daemon import schedules
from bibi.daemon.roles import Roles

log = logging.getLogger("bibi.daemon")


def create_app(roles: Roles, synchronizer=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if synchronizer is not None:
            await synchronizer.start()
        try:
            yield
        finally:
            if synchronizer is not None:
                await synchronizer.stop()

    app = FastAPI(
        title="bibi · daemon",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
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
        return out

    @app.post("/-/maintenance")
    def maintenance_on():
        state.set_maintenance(True)
        return {"maintenance": True}

    @app.delete("/-/maintenance")
    def maintenance_off():
        state.set_maintenance(False)
        return {"maintenance": False}

    @app.post("/-/rescan")
    def rescan():
        # Phase 2: Stub. Die echte Schedule-Erkennung kommt mit dem Scheduler (Phase 3).
        return {"rescanned": False, "note": "Phase 2 stub — Scheduler folgt (Phase 3)"}

    @app.get("/-/schedule")
    def schedule():
        return {"schedules": schedules.list_schedules()}

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
