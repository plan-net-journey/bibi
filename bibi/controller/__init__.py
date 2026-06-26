"""Controller-Rolle (Phase 4): die Web-App auf dem Steuer-Namensraum ``/-/``.

PLAN-4 §2.1 — die App-Wurzel *ist* ``/-/`` (kein ``/-/overview``):

- Browser (``Accept: text/html``) → die HTML-App (htmx, kein Theme), **server-seitig**
  gerendert aus den ``/-/``-JSON-Endpunkten (via :class:`ControllerClient`, kein
  direkter DB-Zugriff — Akzeptanz §5).
- Nicht-Browser → knapper **JSON-Service-Deskriptor** (System-Info + App-Link);
  so bleibt §1.1 (reine JSON-API für Maschinen) auch an der Wurzel gewahrt.

Stufe 4.1: Verdikt (Ebene 0) + Abweichungs-/Überfällig-Listen (Ebene 1), htmx-Poll.
Fragment-Routen liegen unter ``/-/ui/`` (App-Namensraum, kollidiert nicht mit der
gefrorenen Daten-API ``/-/<noun>``).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bibi.daemon import openapi, roles as roles_mod

from . import render
from .client import ControllerClient

__all__ = ["ControllerClient", "add_controller_routes", "render", "service_descriptor"]


def _wants_html(request: Request) -> bool:
    """Browser senden ``text/html`` im Accept-Header; Tooling (curl, ``Accept:
    application/json`` oder ``*/*``) nicht. Genau das trennt App von Deskriptor."""
    return "text/html" in request.headers.get("accept", "")


def service_descriptor(roles: roles_mod.Roles) -> dict:
    """Knapper Maschinen-Deskriptor an der Wurzel (§2.1)."""
    return {
        "service": "bibi",
        "app": "/-/",
        "contract": openapi.CONTRACT_VERSION,
        "roles": roles.active_names(),
    }


def add_controller_routes(
    app: FastAPI, roles: roles_mod.Roles, client: ControllerClient
) -> None:
    """Die ``/-/``-Wurzel + ``/-/ui/``-Fragmente registrieren."""

    def _status() -> dict:
        try:
            return client.status()
        except Exception:  # noqa: BLE001 — Daemon-Selbstaufruf, defensiv (§2.7)
            return {}

    def _schedules() -> list:
        try:
            return client.schedules()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

    @app.get("/-/", include_in_schema=False)
    def root(request: Request):
        if _wants_html(request):
            return HTMLResponse(render.dashboard_page(_status(), _schedules()))
        return JSONResponse(service_descriptor(roles))

    @app.get("/-/ui/verdict", include_in_schema=False)
    def verdict_fragment():
        return HTMLResponse(render.verdict_fragment(_status()))

    def _detail_data(slug: str):
        try:
            schedule = next((s for s in client.schedules()
                             if s.get("slug") == slug), None)
            runs = client.journal(slug=slug)
            job = next((j for j in client.jobs() if j.get("slug") == slug), None)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            schedule, runs, job = None, [], None
        return schedule, runs, job

    @app.get("/-/ui/schedule/{slug}", include_in_schema=False)
    def schedule_detail(slug: str):
        schedule, runs, job = _detail_data(slug)
        return HTMLResponse(render.schedule_detail_page(schedule, runs, job, slug=slug))

    @app.get("/-/ui/run/{jid}/output", include_in_schema=False)
    def run_output(jid: int):
        try:
            data = client.run_output(jid)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            data = {}
        return HTMLResponse(render.output_block(
            data.get("events", []), data.get("kind", "job")))

    # ── Verben (§5.6) + Löschen (§4.0) — wirken, dann #detail neu rendern ─────
    # Sichtbarkeit/Scope (read-only vs. operator) wird in 4.6 (Traefik) erzwungen.
    @app.post("/-/ui/schedule/{slug}/{verb}", include_in_schema=False)
    def schedule_action(slug: str, verb: str):
        if verb not in render._VERBS:
            return JSONResponse(status_code=404, content={"error": "unknown verb"})
        _, _, job = _detail_data(slug)
        if job and job.get("id"):
            try:
                client.job_action(job["id"], verb)
            except Exception:  # noqa: BLE001 — defensiv (§2.7)
                pass
        schedule, runs, job = _detail_data(slug)
        return HTMLResponse(render.schedule_detail_inner(schedule, runs, job, slug=slug))

    @app.delete("/-/ui/schedule/{slug}/run/{jid}", include_in_schema=False)
    def run_delete(slug: str, jid: int):
        try:
            client.delete_journal(jid)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        schedule, runs, job = _detail_data(slug)
        return HTMLResponse(render.schedule_detail_inner(schedule, runs, job, slug=slug))
