"""Controller-Rolle (Phase 4): die Web-App auf dem Steuer-Namensraum ``/-/``.

PLAN-4 §2.1 — die App-Wurzel *ist* ``/-/`` (kein ``/-/overview``). Eine Route mit
**Content-Negotiation**:

- Browser (``Accept: text/html``) → die HTML-App (htmx, kein Theme).
- Nicht-Browser → knapper **JSON-Service-Deskriptor** (System-Info + App-Link);
  so bleibt §1.1 (reine JSON-API für Maschinen) auch an der Wurzel gewahrt.

Stufe 4.0 ist das **Skelett**: die Seite erscheint und holt sich das Verdikt live
aus ``/-/status``. Abweichungs-Liste (4.1), Detail/Output (4.2) folgen.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bibi.daemon import openapi, roles as roles_mod

_INDEX_HTML = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


def _wants_html(request: Request) -> bool:
    """Browser senden ``text/html`` im Accept-Header; Tooling (curl, ``Accept:
    application/json`` oder ``*/*``) nicht. Genau das trennt App von Deskriptor."""
    return "text/html" in request.headers.get("accept", "")


def service_descriptor(roles: roles_mod.Roles) -> dict:
    """Knapper Maschinen-Deskriptor an der Wurzel (§2.1): wer hier antwortet, was
    die App ist, welcher Vertrag gilt."""
    return {
        "service": "bibi",
        "app": "/-/",                         # die HTML-App liegt auf derselben URL
        "contract": openapi.CONTRACT_VERSION,  # /-/openapi.json
        "roles": roles.active_names(),
    }


def add_controller_routes(app: FastAPI, roles: roles_mod.Roles) -> None:
    """Die ``/-/``-Wurzel registrieren. Zuerst aufrufen, damit sie vor etwaigen
    Sammel-Stubs gewinnt."""

    @app.get("/-/", include_in_schema=False)
    def root(request: Request):
        if _wants_html(request):
            return HTMLResponse(_INDEX_HTML)
        return JSONResponse(service_descriptor(roles))
