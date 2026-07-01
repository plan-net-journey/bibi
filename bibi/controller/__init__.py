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
        # Home = Feed (Frontend-Plan, Entscheidung #5). Browser → Feed-Screen;
        # Nicht-Browser → JSON-Deskriptor (§1.1 bleibt an der Wurzel gewahrt).
        if _wants_html(request):
            return HTMLResponse(render.feed_page(_journal(), jobs=_jobs(), status=_status()))
        return JSONResponse(service_descriptor(roles))

    @app.get("/-/ui/dashboard", include_in_schema=False)
    def dashboard():
        # Health-/Anomalie-Sicht + Ops-Handles (RESCAN/MAINT) — über die Nav („Status")
        # erreichbar, seit der Feed die Home ist.
        return HTMLResponse(render.dashboard_page(_status(), _schedules()))

    @app.get("/-/ui/verdict", include_in_schema=False)
    def verdict_fragment():
        return HTMLResponse(render.verdict_fragment(_status()))

    @app.get("/-/ui/schedules", include_in_schema=False)
    def schedules_screen(typ: str | None = None, status: str | None = None):
        # Der Schedules-Screen (Seite): Nav + Filter + gefilterte, self-pollende Liste.
        items = render.filter_schedules(_schedules(), typ=typ, status=status)
        return HTMLResponse(render.schedules_page(items, typ=typ, status=status))

    @app.get("/-/ui/schedules/list", include_in_schema=False)
    def schedules_list_fragment(typ: str | None = None, status: str | None = None):
        # Filter-fähiges Fragment — Self-Poll-Ziel + Ziel der Filter-Dropdowns.
        items = render.filter_schedules(_schedules(), typ=typ, status=status)
        return HTMLResponse(render.schedules_fragment(items, typ=typ, status=status))

    @app.post("/-/ui/rescan", include_in_schema=False)
    def ui_rescan():
        try:
            client.rescan()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        return HTMLResponse(render.schedules_fragment(_schedules()))

    @app.post("/-/ui/maintenance", include_in_schema=False)
    def ui_maintenance():
        # Toggle: aktuellen Zustand lesen, umschalten, den Handle neu rendern.
        on = bool(_status().get("maintenance"))
        try:
            client.maintenance(not on)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        return HTMLResponse(render.maint_handle(_status()))

    @app.get("/-/ui/logs", include_in_schema=False)
    def logs_page():
        return HTMLResponse(render.log_page())

    def _journal() -> list:
        # Feed-Quelle = Journal (Frontend-Plan §A). Letzte 50 (DESC) als Backfill;
        # neueste landen via feed_list unten. Live-Push besorgt /-/feed/stream.
        try:
            return client.journal()[:50]
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

    def _jobs() -> list:
        # Band-Quelle = jobs-Tabelle (Live-State, §C.2). Defensiv (ein FakeClient
        # ohne jobs() oder ein Daemon-Hänger darf den Feed nicht killen).
        try:
            return client.jobs()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

    @app.get("/-/ui/feed", include_in_schema=False)
    def feed_screen():
        return HTMLResponse(render.feed_page(_journal(), jobs=_jobs(), status=_status()))

    @app.get("/-/ui/feed/list", include_in_schema=False)
    def feed_list_fragment():
        return HTMLResponse(render.feed_list(_journal()))

    @app.get("/-/ui/feed/bands", include_in_schema=False)
    def feed_bands_fragment():
        return HTMLResponse(render.bands_fragment(_jobs(), _journal()))

    def _detail_data(slug: str):
        try:
            schedule = next((s for s in client.schedules()
                             if s.get("slug") == slug), None)
            runs = client.journal(slug=slug)
            job = next((j for j in client.jobs() if j.get("slug") == slug), None)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            schedule, runs, job = None, [], None
        return schedule, runs, job

    def _detail_outputs(job: dict | None):
        # Live-Output **default expanded** für den aktuellen Job — auch nach
        # einem Terminal-Übergang, bis der nächste Lauf ihn ersetzt (User-
        # Feedback 2026-07-01: "archiviert wird erst vor dem nächsten Rerun" —
        # die Job-Zeile trägt den letzten Lauf ja weiter, bis sie neu dispatcht
        # wird; kein Grund, den Output vorher auszublenden). Kein Auto-Fetch
        # für ÄLTERE Journal-Läufe (User-Feedback: stand sonst kontextlos nach
        # der ganzen Tabelle, jeder Lauf hat einheitlich nur seinen Detail-Link,
        # PLAN-11-Nacharbeit) — das bleibt unverändert.
        live_output = None
        try:
            if job and job.get("id"):
                live_output = client.job_output(job["id"])
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        return live_output

    @app.get("/-/ui/schedule/{slug}", include_in_schema=False)
    def schedule_detail(slug: str):
        schedule, runs, job = _detail_data(slug)
        live_output = _detail_outputs(job)
        return HTMLResponse(render.schedule_detail_page(
            schedule, runs, job, slug=slug, live_output=live_output))

    @app.get("/-/ui/schedule/{slug}/detail", include_in_schema=False)
    def schedule_detail_fragment(slug: str):
        # Self-Poll-Ziel von #detail: Live-Block aktualisiert pending→running→…
        schedule, runs, job = _detail_data(slug)
        live_output = _detail_outputs(job)
        return HTMLResponse(render.schedule_detail_inner(
            schedule, runs, job, slug=slug, live_output=live_output))

    @app.get("/-/ui/schedule/{slug}/attrs", include_in_schema=False)
    def schedule_attrs(slug: str):
        try:
            data = client.schedule_config(slug)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            data = {}
        return HTMLResponse(render.schedule_attrs_page(slug, data))

    @app.get("/-/ui/run/{jid}", include_in_schema=False)
    def run_detail(jid: int):
        # Execution-Detail (§C.4): ein Lauf — Meta (Journal-Zeile) + voller Output.
        try:
            entry = client.journal_entry(jid)
            data = client.run_output(jid)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            entry, data = {}, {}
        # schedule_ref lebt nur am aktuellen Job (nicht im Journal) — Live-Lookup
        # per Slug, best-effort (User-Feedback 2026-07-01: "wo ist die schedule_ref?").
        # Existiert der Schedule nicht mehr (gelöscht/umbenannt), bleibt sie schlicht leer.
        if entry.get("slug"):
            try:
                schedule_ref = client.schedule_config(entry["slug"]).get("schedule_ref")
                if schedule_ref:
                    entry = {**entry, "schedule_ref": schedule_ref}
            except Exception:  # noqa: BLE001 — defensiv (§2.7)
                pass
        return HTMLResponse(render.execution_detail_page(
            entry, data.get("events", []), data.get("kind") or entry.get("kind", "job")))

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
