"""Controller-Rolle (Phase 4): die Web-App auf dem Steuer-Namensraum ``/-/``.

PLAN-4 §2.1 — die App-Wurzel *ist* ``/-/`` (kein ``/-/overview``):

- Browser (``Accept: text/html``) → die HTML-App (htmx, kein Theme), **server-seitig**
  gerendert aus den ``/-/``-JSON-Endpunkten (via :class:`ControllerClient`, kein
  direkter DB-Zugriff — Akzeptanz §5).
- Nicht-Browser → knapper **JSON-Service-Deskriptor** (System-Info + App-Link);
  so bleibt §1.1 (reine JSON-API für Maschinen) auch an der Wurzel gewahrt.

Home = Feed (PLAN-18 Stufe 18.3, 2026-07-06) — löst die 2026-07-04-Entscheidung
„Home = Schedules" bewusst ab (Client-Umbau, ``Client Requirements.md``).
Schedules bleibt unter ``/-/ui/schedules`` vollständig erreichbar, ist nur
nicht mehr die Root selbst. Fragment-Routen liegen unter ``/-/ui/``
(App-Namensraum, kollidiert nicht mit der gefrorenen Daten-API ``/-/<noun>``).
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bibi.daemon import activity, openapi, roles as roles_mod

from . import render
from .client import ControllerClient

log = logging.getLogger("bibi.controller")

__all__ = ["ControllerClient", "add_controller_routes", "render", "service_descriptor"]


def _wants_html(request: Request) -> bool:
    """Browser senden ``text/html`` im Accept-Header; Tooling (curl, ``Accept:
    application/json`` oder ``*/*``) nicht. Genau das trennt App von Deskriptor."""
    return "text/html" in request.headers.get("accept", "")


def _local_schedules() -> dict[str, dict]:
    """Read-only Discovery-Scan des Vaults (PLAN-17 Befund 2 Punkt 1) — bewusst
    **ohne** ``job_db.upsert_schedule()``, kein DB-Schreiben, kein Dispatch. Ein
    Client trägt im Ruhezustand keine Rolle, die ``vault/case/`` sonst einliest.

    ``repo_path`` (repo-root-relative, POSIX) ist neu (PLAN-21 Befund 10) —
    Grundlage für ``local_files_status()``, die git-Pfade repo-root-relativ
    erwartet, während ``pr.schedule_ref`` case-dir-relativ ist."""
    from bibi import repo
    from bibi.schedule import discovery
    try:
        case_dir = repo.case_dir()
        root = repo.root()
        found = discovery.discover(case_dir).found
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        return {}
    return {
        slug: {
            "schedule": pr.spec.schedule, "at": pr.spec.at, "payload": pr.spec.payload,
            "repo_path": (case_dir / pr.schedule_ref).relative_to(root).as_posix(),
        }
        for slug, pr in found.items()
    }


def _scheduler_url() -> str | None:
    from bibi import config
    return os.environ.get("BIBI_SCHEDULER_URL") or config.read_env().get("BIBI_SCHEDULER_URL")


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

    def _landings() -> list:
        # /-/landings ist scheduler-gated (501 ohne scheduler-Rolle, PLAN-21
        # Befund 11) — auf einem reinen Client bleibt das Chart dann leer statt
        # den Screen zu brechen (§2.7, wie _status()/_schedules() oben).
        try:
            return client.landings()
        except Exception:  # noqa: BLE001
            return []

    def _effective_days(days: int | None) -> int | None:
        """``days`` fehlt im Query (allererster Seitenaufruf) → Default 1 Tag
        (PLAN-18 Design-Pass), nicht unbegrenzt — ein voller, unbegrenzter Log
        über die echte bibi-notes-Historie brauchte live 5,7s, über dem
        5s-Timeout des Controller-Selbstaufrufs (User-Fund 2026-07-06, „Feed
        ist auf einmal leer"). Die „gesamte Historie"-Fähigkeit (vormals über
        ein explizites ``days=0``-Sentinel) ist gestrichen (PLAN-19 Befund 7,
        User-Entscheidung) — kein Weg mehr zu einem unbegrenzten Fenster."""
        return 1 if days is None else days

    def _effective_weeks(weeks: int | None) -> int:
        # weeks entkoppelt von days (PLAN-20 Befund 3) — eigenes Default aus
        # bibi.feed.HEATMAP_WEEKS, damit Route + Fragment denselben Default
        # kennen, ohne die Konstante zu duplizieren.
        from bibi.feed import HEATMAP_WEEKS
        return weeks if weeks is not None else HEATMAP_WEEKS

    def _feed_data(days: int | None, weeks: int | None = None) -> dict:
        try:
            return client.feed(days=days, weeks=weeks)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            activity.emit(log, logging.WARNING, "controller.feed_unreachable",
                         "Feed-Selbstaufruf fehlgeschlagen (Timeout/Fehler?) — "
                         "zeige leeren Feed statt abzustürzen", role="controller")
            return {"entities": [], "heatmap": []}

    def _feed_git_status() -> dict | None:
        # Rein lokal (kein Heartbeat/Netzwerk nötig, PLAN-18 Befund 1) — dieselbe
        # working_tree_status()-Basis wie Heartbeat/CLI-Statusline.
        from bibi import repo as repo_mod
        from bibi.git_status import working_tree_status
        try:
            s = working_tree_status(repo_mod.root())
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return None
        if s is None:
            return None
        return {"tree": s.tree, "sync": s.sync, "branch": s.branch}

    @app.get("/-/", include_in_schema=False)
    def root(request: Request, days: int | None = None, weeks: int | None = None):
        # Home = Feed (PLAN-18 Stufe 18.3, löst 2026-07-04 "Home = Schedules"
        # bewusst ab). Browser → Feed-Screen; Nicht-Browser → JSON-Deskriptor
        # (§1.1 bleibt an der Wurzel gewahrt). Schedules bleibt unter
        # /-/ui/schedules erreichbar, unverändert.
        if _wants_html(request):
            eff_days = _effective_days(days)
            eff_weeks = _effective_weeks(weeks)
            return HTMLResponse(render.feed_page(
                _feed_data(eff_days, eff_weeks), git_status=_feed_git_status(),
                host_url=_scheduler_url(), days=eff_days, weeks=eff_weeks,
                daemon_status=_status()))
        return JSONResponse(service_descriptor(roles))

    @app.get("/-/ui/feed/board", include_in_schema=False)
    def feed_board(days: int | None = None, weeks: int | None = None):
        eff_days = _effective_days(days)
        eff_weeks = _effective_weeks(weeks)
        return HTMLResponse(render.feed_fragment(
            _feed_data(eff_days, eff_weeks), days=eff_days, weeks=eff_weeks))

    @app.get("/-/ui/schedules", include_in_schema=False)
    def schedules_screen(typ: str | None = None, status: str | None = None):
        # Der Schedules-Screen (Seite): Nav + Ops-Handles + Stat-Grid/Landungs-
        # Histogramm (PLAN-21 Befund 11) + Filter + gefilterte, self-pollende Liste.
        items = render.filter_schedules(_schedules(), typ=typ, status=status)
        return HTMLResponse(render.schedules_page(
            items, typ=typ, status=status, daemon_status=_status(),
            landings=_landings()))

    @app.get("/-/ui/schedules/list", include_in_schema=False)
    def schedules_list_fragment(typ: str | None = None, status: str | None = None):
        # Filter-fähiges Fragment — Self-Poll-Ziel + Ziel der Filter-Dropdowns.
        items = render.filter_schedules(_schedules(), typ=typ, status=status)
        return HTMLResponse(render.schedules_fragment(items, typ=typ, status=status))

    @app.get("/-/ui/schedules/timeseries", include_in_schema=False)
    def schedules_timeseries_fragment(res: int = render._DEFAULT_RESOLUTION_MINUTES):
        # Self-Poll-Ziel des Stat-Grid/Charts — eigene Route, eigene
        # Datenquelle (journal_landings/job_stats statt /-/schedule). ``res``
        # trägt die vom User gewählte Auflösung (Bucket-Minuten) über den
        # 2s-Poll hinweg (s. render.timeseries_fragment()'s Self-Poll-URL).
        return HTMLResponse(render.timeseries_fragment(
            _landings(), _status().get("job_stats"), bucket_minutes=res))

    @app.get("/-/ui/logs", include_in_schema=False)
    def logs_page():
        return HTMLResponse(render.log_page(daemon_status=_status()))

    def _jobs_data() -> tuple[list, dict, list]:
        """PLAN-21 Befund 10, User-Entscheidung: der Jobs-Screen dient
        ausschließlich dem Review der lokalen Repository-Realität, kein
        Remote-Abgleich mehr (weder Netzaufruf noch Vergleichsspalte).
        ``rows`` = lokal entdeckte Job-MDs + echter Git-Status je Datei
        (``local_files_status()``) — gelöschte MDs tauchen von selbst nicht
        mehr auf, da ``discovery.discover()`` sie nicht mehr findet."""
        from bibi import repo
        from bibi.git_status import local_files_status
        local = _local_schedules()
        try:
            git_by_path = local_files_status(
                repo.root(), [s["repo_path"] for s in local.values()])
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            git_by_path = {}
        try:
            live_by_slug = client.run_live_list()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            live_by_slug = {}
        rows = [
            {"slug": slug, **s, "git_status": git_by_path.get(s["repo_path"], "clean"),
             "live": live_by_slug.get(slug)}
            for slug, s in sorted(local.items())
        ]
        try:
            run_journal = client.run_journal(limit=200)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            run_journal = []
        # Sortiert nach finished_at DESC (job_db.list_journal) — der erste
        # Treffer je Slug ist damit schon der jeweils letzte Lauf.
        local_runs: dict[str, dict] = {}
        for run in run_journal:
            local_runs.setdefault(run["slug"], run)
        return rows, local_runs, run_journal[:20]

    @app.get("/-/ui/jobs", include_in_schema=False)
    def jobs_screen():
        rows, local_runs, runs = _jobs_data()
        return HTMLResponse(render.jobs_page(rows, local_runs, runs, daemon_status=_status()))

    @app.get("/-/ui/jobs/board", include_in_schema=False)
    def jobs_board():
        # Self-Poll-Ziel von #jobsboard (wie #live/#journal bei Schedules).
        rows, local_runs, runs = _jobs_data()
        return HTMLResponse(render.jobs_fragment(rows, local_runs, runs))

    @app.post("/-/ui/jobs/start/{slug}", include_in_schema=False)
    def jobs_start(slug: str):
        try:
            client.run(slug=slug)
        except Exception:  # noqa: BLE001 — Board zeigt Fehlschlag beim nächsten Poll (defensiv, §2.7)
            pass
        rows, local_runs, runs = _jobs_data()
        return HTMLResponse(render.jobs_fragment(rows, local_runs, runs))

    def _job_detail_data(slug: str):
        # Gegenstück zu _detail_data() (Host), aber lokal gespeist (PLAN-21
        # Befund 10-Nachtrag) — MD-Discovery + Git-Status statt Scheduler-DB,
        # run_journal(slug=...) statt journal(slug=...) für die Historie.
        from bibi import repo
        from bibi.git_status import local_files_status
        local = _local_schedules().get(slug)
        if local is not None:
            try:
                git_by_path = local_files_status(repo.root(), [local["repo_path"]])
                local = {**local, "git_status": git_by_path.get(local["repo_path"], "clean")}
            except Exception:  # noqa: BLE001 — defensiv (§2.7)
                local = {**local, "git_status": "clean"}
        try:
            runs = client.run_journal(slug=slug, limit=render._JOURNAL_PAGE_SIZE, offset=0)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            runs = []
        last_run = runs[0] if runs else None
        return local, last_run, runs

    def _job_live(slug: str) -> dict | None:
        # PLAN-21 Befund 10, 2. Nachtrag: /-/run/live/{slug} 404t, wenn gerade
        # nichts läuft — HTTPError, wie überall sonst in diesem Client (§2.7).
        try:
            return client.run_live(slug)
        except Exception:  # noqa: BLE001
            return None

    @app.get("/-/ui/jobs/detail/{slug}", include_in_schema=False)
    def jobs_detail(slug: str):
        local, last_run, runs = _job_detail_data(slug)
        return HTMLResponse(render.jobs_detail_page(
            slug, local, last_run, runs, daemon_status=_status(), live=_job_live(slug)))

    @app.get("/-/ui/jobs/detail/{slug}/live", include_in_schema=False)
    def jobs_detail_live_fragment(slug: str):
        # Self-Poll-Ziel von #jobsdetail-live (wie #live bei Schedules).
        local, last_run, _runs = _job_detail_data(slug)
        return HTMLResponse(render.jobs_detail_live_fragment(
            slug, _job_live(slug), local, last_run))

    @app.get("/-/ui/jobs/detail/{slug}/runs", include_in_schema=False)
    def jobs_detail_runs_fragment(slug: str, offset: int = 0):
        # Nächste Journal-Batch fürs Infinite Scroll — Analogon zu
        # schedule_runs_fragment(), gegen die lokale Route/base.
        try:
            runs = client.run_journal(slug=slug, limit=render._JOURNAL_PAGE_SIZE, offset=offset)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            runs = []
        return HTMLResponse(render.journal_runs_fragment(
            runs, slug, time.time(), offset, base="/-/ui/jobs/detail"))

    @app.get("/-/ui/jobs/detail/{slug}/journal", include_in_schema=False)
    def jobs_detail_journal_fragment(slug: str):
        # Ziel von _JOBS_LIVE_AUTOREFRESH_JS (PLAN-21 Befund 10, 2. Nachtrag,
        # Live-Streaming) — Analogon zu schedule_journal_fragment(): ein
        # RUNNING-Lauf, der terminal endet, lädt #journal automatisch auf
        # Seite 1 neu. War beim ersten Cut vergessen (journal_url zeigte ins
        # Leere, 404, still von htmx verworfen) — beim Live-Test aufgefallen:
        # die Journal-Tabelle blieb nach Lauf-Ende veraltet, bis zum nächsten
        # manuellen Reload.
        try:
            runs = client.run_journal(slug=slug, limit=render._JOURNAL_PAGE_SIZE, offset=0)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            runs = []
        return HTMLResponse(render.journal_fragment(runs, slug, time.time(), base="/-/ui/jobs/detail"))

    @app.delete("/-/ui/jobs/detail/{slug}/run/{jid}", include_in_schema=False)
    def jobs_detail_run_delete(slug: str, jid: int):
        # Analogon zu run_delete() (Host), aber local_run_delete() (nur
        # domain="local" — rollenunabhängig, s. client.py/app.py).
        try:
            client.local_run_delete(jid)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        _, _, runs = _job_detail_data(slug)
        return HTMLResponse(render.journal_fragment(
            runs, slug, time.time(), base="/-/ui/jobs/detail"))

    def _detail_data(slug: str):
        try:
            schedule = next((s for s in client.schedules()
                             if s.get("slug") == slug), None)
            # Erste Seite der Journal-Historie — der Rest lädt per Infinite Scroll
            # nach (GET .../runs?offset=N, render.journal_runs_fragment).
            runs = client.journal(slug=slug, limit=render._JOURNAL_PAGE_SIZE, offset=0)
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
            schedule, runs, job, slug=slug, live_output=live_output,
            daemon_status=_status()))

    @app.get("/-/ui/schedule/{slug}/live", include_in_schema=False)
    def schedule_live_fragment(slug: str):
        # Self-Poll-Ziel von #live: Live-Block aktualisiert pending→running→…
        # #journal pollt bewusst NICHT mit (würde nachgeladene Infinite-Scroll-
        # Zeilen jeden Tick wieder plattmachen) — braucht `runs` trotzdem für
        # den last_status-Fallback in der Meta-Zeile.
        schedule, runs, job = _detail_data(slug)
        live_output = _detail_outputs(job)
        return HTMLResponse(render.live_fragment(
            schedule, runs, job, slug=slug, live_output=live_output))

    @app.get("/-/ui/schedule/{slug}/runs", include_in_schema=False)
    def schedule_runs_fragment(slug: str, offset: int = 0):
        # Nächste Journal-Batch fürs Infinite Scroll — ersetzt die Sentinel-
        # Zeile (outerHTML) durch neue Zeilen + ggf. frische Sentinel-Zeile.
        try:
            runs = client.journal(slug=slug, limit=render._JOURNAL_PAGE_SIZE, offset=offset)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            runs = []
        return HTMLResponse(render.journal_runs_fragment(runs, slug, time.time(), offset))

    @app.get("/-/ui/schedule/{slug}/journal", include_in_schema=False)
    def schedule_journal_fragment(slug: str):
        # Ziel von _JOURNAL_AUTOREFRESH_JS (User-Feedback 2026-07-03): ein
        # RUNNING-Lauf, der ohne Button-Klick terminal endet, lädt #journal
        # jetzt automatisch auf Seite 1 neu, statt erst beim nächsten Reload.
        try:
            runs = client.journal(slug=slug, limit=render._JOURNAL_PAGE_SIZE, offset=0)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            runs = []
        return HTMLResponse(render.journal_fragment(runs, slug, time.time()))

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
        if not entry:
            # /-/journal/{jid} ist scheduler-gated (501 ohne scheduler-Rolle,
            # HTTPError → oben abgefangen) — auf einem reinen Client fällt das
            # hier auf die rollenunabhängige lokale Route zurück (PLAN-21
            # Befund 10). Trägt dieser Knoten die scheduler-Rolle, liefert
            # client.journal_entry() bereits JEDE Domäne (get_journal() filtert
            # nicht nach domain), dieser Zweig greift dann nie.
            try:
                entry = client.local_run_entry(jid)
                data = client.local_run_output(jid)
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
            entry, data.get("events", []), data.get("kind") or entry.get("kind", "job"),
            daemon_status=_status()))

    @app.get("/-/ui/run/{jid}/output", include_in_schema=False)
    def run_output(jid: int):
        try:
            data = client.run_output(jid)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            data = {}
        return HTMLResponse(render.output_block(
            data.get("events", []), data.get("kind", "job")))

    # ── Verben (§5.6) + Löschen (§4.0) — wirken, dann #live neu rendern ───────
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
        now = time.time()
        # #journal pollt nicht mit (s.o.) — eine Aktion kann aber sofort eine neue
        # Journal-Zeile erzeugen (z. B. KILL), deshalb hier explizit per Out-of-
        # Band-Swap auf Seite 1 zurücksetzen, statt auf den nächsten Scroll zu warten.
        return HTMLResponse(
            render.live_fragment(schedule, runs, job, slug=slug, now=now)
            + render.journal_fragment(runs, slug, now, oob=True)
        )

    @app.delete("/-/ui/schedule/{slug}/run/{jid}", include_in_schema=False)
    def run_delete(slug: str, jid: int):
        try:
            client.delete_journal(jid)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        _, runs, _ = _detail_data(slug)
        return HTMLResponse(render.journal_fragment(runs, slug, time.time()))
