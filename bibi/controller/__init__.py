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
import threading
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
            # User-Fund 2026-07-13 ("REBUILD müsste doch auch beim Client
            # notwendig sein, oder?"): Grundlage für die REBUILD-Sichtbarkeit
            # auf der Client-Job-Detailseite (render._action_bar(), s.
            # PLAN-29 Befund 3+5) — bisher fehlte exec_mode hier komplett.
            "exec_mode": pr.spec.exec_mode,
            # PLAN-29 Befund 2, User-Fund: Type-Spalte der Jobs-Tabelle soll
            # bei Apps den Port als Link zeigen — bisher fehlten app_port/
            # app_prefix hier komplett, obwohl ScheduleSpec beide trägt.
            "app_port": pr.spec.app_port, "app_prefix": pr.spec.app_prefix,
            # PLAN-29 Befund 3+5: Grundlage für jobs_detail_attrs_page() —
            # alles hier ist statische Frontmatter (ScheduleSpec), keine
            # Scheduler-Laufzeit, deshalb genauso read-only verfügbar wie
            # die Felder oben, unabhängig von einer scheduler-Rolle.
            "priority": pr.spec.priority, "model": pr.spec.model,
            "soul": pr.spec.soul, "session": pr.spec.session,
            "attempts": pr.spec.attempts, "backoff": pr.spec.backoff,
            "silence_timeout": pr.spec.silence_timeout, "wall_time": pr.spec.wall_time,
            "defer_time": pr.spec.defer_time, "defer_max": pr.spec.defer_max,
            "error_time": pr.spec.error_time,
            "image": pr.spec.image,
        }
        for slug, pr in found.items()
    }


def _scheduler_url() -> str | None:
    from bibi import config
    return os.environ.get("BIBI_SCHEDULER_URL") or config.read_env().get("BIBI_SCHEDULER_URL")


#: TTL-Cache für _job_sparkline_series() (User-Feedback: minutengenaue
#: Frische ist für eine 30-Tage-Activity-Sparkline nicht nötig, ein
#: Stundentakt genügt) — Modul-Level statt Closure-lokal, damit ein einziger
#: Cache über alle Requests dieses Prozesses hinweg gilt, unabhängig davon,
#: wie oft add_controller_routes() aufgerufen wird.
_SPARKLINE_CACHE_TTL = 3600
_sparkline_cache: dict = {"result": None, "computed_at": 0.0, "slugs": frozenset()}
#: Schützt die Cache-Befüllung (Bibi4-Iteration, Sparkline-Entkopplung):
#: mehrere gleichzeitige Pro-Slug-Requests (eine je Zeile, s. render.
#: _sparkline_cell_lazy()) dürfen bei kaltem Cache nicht je einen eigenen
#: teuren git-log-Aufruf auslösen (thundering herd) — nur der erste
#: Anfragende rechnet tatsächlich, alle anderen warten kurz und lesen dann
#: den frisch befüllten Cache.
_sparkline_lock = threading.Lock()


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
        # PLAN-30 Ebene 3: dieselbe Quarantäne-Liste aus Ebene 2 — Anzahl
        # eskalierter Job-Branches für die dritte Git-Kachel-Zeile.
        stuck = 0
        try:
            from bibi.daemon import merge_quarantine
            stuck = len(merge_quarantine.escalated(repo_mod.root()))
        except Exception:  # noqa: BLE001 — defensiv (§2.7), Kachel bleibt sonst leer
            pass
        return {"tree": s.tree, "sync": s.sync, "branch": s.branch,
                "oid": s.oid, "ahead": s.ahead, "behind": s.behind, "stuck": stuck}

    @app.get("/-/", include_in_schema=False)
    def root(request: Request, days: int | None = None, weeks: int | None = None):
        # Home = Feed (PLAN-18 Stufe 18.3, löst 2026-07-04 "Home = Schedules"
        # bewusst ab). Browser → Feed-Screen; Nicht-Browser → JSON-Deskriptor
        # (§1.1 bleibt an der Wurzel gewahrt). Schedules bleibt unter
        # /-/ui/schedules erreichbar, unverändert.
        if _wants_html(request):
            from bibi import config
            eff_days = _effective_days(days)
            eff_weeks = _effective_weeks(weeks)
            return HTMLResponse(render.feed_page(
                _feed_data(eff_days, eff_weeks), git_status=_feed_git_status(),
                host_url=_scheduler_url(), days=eff_days, weeks=eff_weeks,
                daemon_status=_status(),
                status_poll_interval_s=config.status_poll_interval(),
                job_status_poll_interval_s=config.job_status_poll_interval(),
                client_rows=_client_rows_for_status()))
        return JSONResponse(service_descriptor(roles))

    @app.get("/-/ui/feed/board", include_in_schema=False)
    def feed_board(days: int | None = None, weeks: int | None = None):
        eff_days = _effective_days(days)
        eff_weeks = _effective_weeks(weeks)
        return HTMLResponse(render.feed_fragment(
            _feed_data(eff_days, eff_weeks), days=eff_days, weeks=eff_weeks))

    @app.get("/-/ui/feed/status", include_in_schema=False)
    def feed_status():
        # Self-Poll-Ziel von #feedstatus (PLAN-25 Befund 4) — dieselben
        # Datenquellen wie root(), nur ohne Heatmap/Änderungsliste.
        from bibi import config
        return HTMLResponse(render.feed_status_fragment(
            _status(), _feed_git_status(), _scheduler_url(), time.time(),
            poll_interval_s=config.status_poll_interval(),
            job_status_poll_interval_s=config.job_status_poll_interval(),
            client_rows=_client_rows_for_status()))

    @app.get("/-/ui/feed/jobstatus", include_in_schema=False)
    def feed_jobstatus():
        # Self-Poll-Ziel von #jobstatuscard (Bibi4-Iteration) — eigener,
        # schnellerer Takt als #feedstatus: _status() liefert job_stats aus
        # einer reinen job_db-SQLite-Abfrage, ohne den git-status-Subprozess
        # der anderen drei Karten (der bleibt bei _feed_git_status()/#feedstatus).
        from bibi import config
        return HTMLResponse(render.job_status_fragment(
            _status().get("job_stats"), time.time(),
            poll_interval_s=config.job_status_poll_interval()))

    _FILTER_COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 180 Tage — UI-Präferenz, kein Session-Cookie

    def _effective_filter(
        request: Request, typ: str | None, status: str | None,
    ) -> tuple[str | None, str | None]:
        # Query-Param gewinnt immer (explizite Wahl); fehlt er (kein ?typ=/
        # ?status= in der URL), auf das zuletzt per Cookie gemerkte Filter
        # zurückfallen (User-Fund: "die ausgewählte Auswahl in
        # /-/ui/schedules sollte erhalten bleiben. Entweder Cookies oder
        # Local Store") — ungültige/veraltete Cookie-Werte werden verworfen.
        eff_typ = typ if typ is not None else render._cookie_filter_value(
            request.cookies.get("bibi_sched_typ"), render._SCHED_TYPES)
        eff_status = status if status is not None else render._cookie_filter_value(
            request.cookies.get("bibi_sched_status"), render._SCHED_STATUSES)
        return eff_typ, eff_status

    def _set_filter_cookies(resp: HTMLResponse, typ: str | None, status: str | None) -> None:
        resp.set_cookie("bibi_sched_typ", typ or "alle",
                        max_age=_FILTER_COOKIE_MAX_AGE, httponly=True, samesite="lax")
        resp.set_cookie("bibi_sched_status", status or "alle",
                        max_age=_FILTER_COOKIE_MAX_AGE, httponly=True, samesite="lax")

    def _effective_resolution(request: Request, res: int | None) -> int:
        # Dieselbe Systematik wie _effective_filter (User-Fund: "warum wird
        # die Auflösung ... nicht gespeichert?") — Query-Param gewinnt, sonst
        # Cookie, sonst Default. res muss zusätzlich noch ein gültiges Preset
        # sein (sonst wie ein fehlender Query-Param behandelt).
        if res is not None and res in render._RESOLUTION_WINDOWS:
            return res
        cookie_res = render._cookie_resolution_value(request.cookies.get("bibi_sched_res"))
        return cookie_res if cookie_res is not None else render._DEFAULT_RESOLUTION_MINUTES

    def _set_resolution_cookie(resp: HTMLResponse, res: int) -> None:
        resp.set_cookie("bibi_sched_res", str(res),
                        max_age=_FILTER_COOKIE_MAX_AGE, httponly=True, samesite="lax")

    @app.get("/-/ui/schedules", include_in_schema=False)
    def schedules_screen(request: Request, typ: str | None = None, status: str | None = None,
                         res: int | None = None):
        # Der Schedules-Screen (Seite): Nav + Ops-Handles + Status-Kacheln
        # (Host/Mode/Git/Job-Status, wie /-/) + Stat-Grid/Landungs-Histogramm
        # (PLAN-21 Befund 11) + Filter + gefilterte, self-pollende Liste.
        from bibi import config
        eff_typ, eff_status = _effective_filter(request, typ, status)
        eff_res = _effective_resolution(request, res)
        items = render.filter_schedules(_schedules(), typ=eff_typ, status=eff_status)
        resp = HTMLResponse(render.schedules_page(
            items, typ=eff_typ, status=eff_status, daemon_status=_status(),
            landings=_landings(), git_status=_feed_git_status(), host_url=_scheduler_url(),
            status_poll_interval_s=config.status_poll_interval(),
            job_status_poll_interval_s=config.job_status_poll_interval(), bucket_minutes=eff_res,
            public_host=config.public_host()))
        _set_filter_cookies(resp, eff_typ, eff_status)
        _set_resolution_cookie(resp, eff_res)
        return resp

    @app.get("/-/ui/schedules/list", include_in_schema=False)
    def schedules_list_fragment(request: Request, typ: str | None = None, status: str | None = None):
        # Filter-fähiges Fragment — Self-Poll-Ziel + Ziel der Filter-Dropdowns
        # (der tatsächliche Request beim Ändern eines Filters, s.
        # _set_filter_cookies oben).
        from bibi import config
        eff_typ, eff_status = _effective_filter(request, typ, status)
        items = render.filter_schedules(_schedules(), typ=eff_typ, status=eff_status)
        resp = HTMLResponse(render.schedules_fragment(
            items, typ=eff_typ, status=eff_status, public_host=config.public_host()))
        _set_filter_cookies(resp, eff_typ, eff_status)
        return resp

    @app.get("/-/ui/archive", include_in_schema=False)
    def archive_screen():
        # Archive-Screen (Host, Bibi4-Iteration) — eigener Screen für Archive/
        # Journal, seit dieser Iteration nicht mehr Teil von /-/ui/schedules
        # (User-Fund: "Archive wird verschoben auf einen eigenen Screen").
        # Status-Kacheln wie jeder andere Screen (User-Fund: "Header ist in
        # Feed, Jobs, Archive (!), Live-Log sichtbar" — fehlten hier bisher).
        from bibi import config
        return HTMLResponse(render.archive_page(
            _schedules(), daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(), status_poll_interval_s=config.status_poll_interval(),
            job_status_poll_interval_s=config.job_status_poll_interval(),
            public_host=config.public_host()))

    @app.get("/-/ui/archive/list", include_in_schema=False)
    def archive_list_fragment():
        # Self-Poll-Ziel, kein Filter (die CR-Spec kennt hier keine Type/Status-
        # Filterleiste, anders als /-/ui/schedules).
        from bibi import config
        return HTMLResponse(render.archive_fragment(_schedules(), public_host=config.public_host()))

    @app.get("/-/ui/clients", include_in_schema=False)
    def clients_screen():
        # Connected-Clients-Screen (Host, Bibi4-Iteration) — Backend
        # (WorkerRegistry, /-/worker) existierte schon lange, hier nur die
        # erste Darstellung. status["workers"] kommt schon über _status()
        # (/-/status), keine neue Datenquelle nötig.
        from bibi import config
        status = _status()
        return HTMLResponse(render.clients_page(
            status.get("workers") or [], daemon_status=status, git_status=_feed_git_status(),
            host_url=_scheduler_url(), status_poll_interval_s=config.status_poll_interval(),
            job_status_poll_interval_s=config.job_status_poll_interval()))

    @app.get("/-/ui/clients/board", include_in_schema=False)
    def clients_board_fragment():
        return HTMLResponse(render.clients_fragment(_status().get("workers") or []))

    @app.get("/-/ui/schedules/timeseries", include_in_schema=False)
    def schedules_timeseries_fragment(request: Request, res: int | None = None):
        # Self-Poll-Ziel des Stat-Grid/Charts — eigene Route, eigene
        # Datenquelle (journal_landings/job_stats statt /-/schedule). ``res``
        # trägt die vom User gewählte Auflösung (Bucket-Minuten) über den
        # 2s-Poll hinweg (s. render.timeseries_fragment()'s Self-Poll-URL);
        # fehlt er (frischer Seitenaufbau), auf das Cookie zurückfallen
        # (User-Fund: "warum wird die Auflösung ... nicht gespeichert?").
        eff_res = _effective_resolution(request, res)
        resp = HTMLResponse(render.timeseries_fragment(
            _landings(), _status().get("job_stats"), bucket_minutes=eff_res))
        _set_resolution_cookie(resp, eff_res)
        return resp

    @app.get("/-/ui/logs", include_in_schema=False)
    def logs_page():
        from bibi import config
        return HTMLResponse(render.log_page(
            daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(), status_poll_interval_s=config.status_poll_interval(),
            job_status_poll_interval_s=config.job_status_poll_interval(),
            client_rows=_client_rows_for_status()))

    def _jobs_data() -> tuple[list, dict]:
        """PLAN-21 Befund 10, User-Entscheidung: der Jobs-Screen dient
        ausschließlich dem Review der lokalen Repository-Realität, kein
        Remote-Abgleich mehr (weder Netzaufruf noch Vergleichsspalte).
        ``rows`` = lokal entdeckte Job-MDs + echter Git-Status je Datei
        (``local_files_status()``) — gelöschte MDs tauchen von selbst nicht
        mehr auf, da ``discovery.discover()`` sie nicht mehr findet.

        Gibt seit der Bibi4-Iteration nur noch ``(rows, local_runs)`` zurück —
        die dritte Rückgabe (flache Journal-Liste für "Lokale Läufe") lebt
        jetzt auf ``_jobs_archive_runs()`` (eigener Screen, s. dort)."""
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
        # local_runs (Pro-Job-Status-Lookup in _jobs_row(), render.py) braucht
        # weiterhin die volle Journal-Liste, unabhängig vom Archive-Screen.
        #
        # User-Fund 2026-07-13: run_pinned() vergibt pro Aufruf einen
        # eindeutigen jobs.slug (f"{bucket_slug}-{token}"), der unverändert
        # nach journal.slug übernommen wird (job_db.py::_write_journal()).
        # Der Pro-Job-Lookup sucht aber gegen den STABILEN Bucket-Slug aus der
        # lokalen MD-Discovery — ohne Rückrechnung fand er einen gepinnten
        # Lauf deshalb nie, selbst wenn er gerade komplett gelaufen war
        # ("noch nie lokal gelaufen" trotz sichtbarem Journal-Eintrag).
        # Dieselbe Rückrechnung nutzt bereits worker.local_runs_live() für
        # den *live*-Fall.
        local_runs: dict[str, dict] = {}
        for run in _jobs_archive_runs():
            bucket = run["slug"].rsplit("-", 1)[0] if run.get("pinned_host") else run["slug"]
            local_runs.setdefault(bucket, run)
        return rows, local_runs

    def _client_rows_for_status() -> list | None:
        # Bibi4-Iteration, User-Brainstorm ("was zeigen wir an Stelle der Host
        # Job Status Card beim Client?") — die 4. Stat-Karte braucht die
        # Discovery-Liste nur auf Knoten ohne scheduler-Rolle (die zeigen dort
        # bereits die Host-Variante über job_stats/_job_status_card()); sonst
        # unnötige Arbeit (Git-Status-Subprozess pro Job) auf jedem 30s-Poll.
        if roles.scheduler:
            return None
        rows, _local_runs = _jobs_data()
        return rows

    _SPARKLINE_SINCE_DAYS = 30

    def _job_sparkline_series(rows: list[dict]) -> dict[str, list[int]]:
        """Sparkline-Zähl-Buckets je Job (Bibi4-Iteration, User-Fund: "eine
        Sparkline, die die durch den Agenten verursachten git Änderungen
        repräsentiert"). Ein einziges ``git log``-Paar (Änderungen +
        Merge-Erkennung, analog ``feed.aggregate_feed()``) für ALLE Jobs auf
        einmal, kein Aufruf je Zeile — Präfix ist der Case-Ordner des Jobs
        (``repo_path``s Verzeichnis), nicht nur die job.md-Datei selbst, damit
        z. B. begleitende Notizen im selben Case-Ordner mitzählen. Nur vom
        initialen Seitenaufbau aufgerufen (``jobs_screen()``), bewusst nicht
        vom 2s-Self-Poll (s. ``render._sparkline_cell()``-Docstring).

        ``own_paths`` (Bugfix, User-Fund: "warum haben alle Runner die
        gleiche Sparkline" — mehrere Jobs im selben Case-Ordner, z. B.
        ``Runner``/``Runner 1``.../``Runner 5``, teilten sich zuvor exakt
        dieselbe Serie, s. ``feed.activity_series_by_prefix()``-Docstring)
        disambiguiert Änderungen an einer ANDEREN Job-eigenen MD im selben
        Ordner — nur echte Begleitdateien zählen weiter für alle.

        TTL-Cache (User-Feedback: "Performance ist schlecht ... stündliches
        Update genügt"): ``git log`` über 30 Tage ist auf großen Repos
        spürbar (0,65s/Aufruf lokal gemessen, zwei Aufrufe pro Seitenaufbau)
        — Ergebnis wird ``_SPARKLINE_CACHE_TTL`` Sekunden lang wiederverwendet.
        Zusätzlich ans Slug-Set gebunden (nicht nur Zeit): ein frisch
        entdeckter Job stünde sonst bis zu eine Stunde lang ganz ohne
        Sparkline da, weil sein Slug im gecachten Ergebnis-Dict fehlt.

        ``_sparkline_lock`` (zweite Bibi4-Iteration, Sparkline-Entkopplung):
        seit ``jobs_screen()`` selbst nicht mehr eager rechnet, sondern jede
        Zeile per ``hx-trigger=\"load\"`` einen eigenen Request gegen
        ``/-/ui/jobs/{slug}/sparkline`` feuert, träfen bei kaltem Cache sonst
        N gleichzeitige Requests auf N redundante ``git log``-Läufe
        (thundering herd) — schlechter als der alte, einmalige Blockier-
        Aufruf. Double-checked locking: nur der erste Anfragende rechnet,
        alle anderen warten kurz auf den Lock und lesen danach den frisch
        befüllten Cache."""
        from pathlib import Path
        from bibi import feed as feed_mod
        from bibi import repo as repo_mod
        prefixes = {
            row["slug"]: str(Path(row["repo_path"]).parent) + "/"
            for row in rows if row.get("repo_path")
        }
        if not prefixes:
            return {}
        slugs = frozenset(prefixes)

        def _fresh(cached: dict, now: float) -> bool:
            return (cached["result"] is not None and slugs == cached["slugs"]
                    and now - cached["computed_at"] < _SPARKLINE_CACHE_TTL)

        now = time.time()
        if _fresh(_sparkline_cache, now):
            return _sparkline_cache["result"]
        with _sparkline_lock:
            now = time.time()
            cached = _sparkline_cache
            if _fresh(cached, now):  # ein anderer Thread hat inzwischen befüllt
                return cached["result"]
            own_paths = {row["slug"]: row["repo_path"] for row in rows if row.get("repo_path")}
            root = repo_mod.root()
            commits = feed_mod.collect_commits(root, since_days=_SPARKLINE_SINCE_DAYS)
            agent_shas = feed_mod.agent_commit_shas(root, since_days=_SPARKLINE_SINCE_DAYS)
            result = feed_mod.activity_series_by_prefix(
                commits, agent_shas, prefixes, since_days=_SPARKLINE_SINCE_DAYS,
                own_paths=own_paths)
            cached["result"], cached["computed_at"], cached["slugs"] = result, now, slugs
            return result

    def _jobs_archive_runs() -> list:
        # Flache Journal-Liste über alle lokalen Jobs (Bibi4-Iteration,
        # Archive-Screen Client) — dieselbe Quelle, die vorher nur für
        # "Lokale Läufe" (auf 20 gedeckelt) diente, jetzt ungedeckelt (bis zum
        # 200er-Server-Limit) für den eigenen Screen.
        try:
            return client.run_journal(limit=200)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

    @app.get("/-/ui/jobs", include_in_schema=False)
    def jobs_screen():
        # Bibi4-Iteration, Sparkline-Entkopplung (User-Fund: "Sparklines
        # dauern beim Reload immer") — der initiale Seitenaufbau rechnet die
        # Serie nicht mehr selbst (das war der blockierende Teil), sondern
        # liefert nur Platzhalter (lazy_sparklines=True); jede Zeile lädt
        # ihre eigene Sparkline per hx-trigger="load" nach, s. /-/ui/jobs/
        # {slug}/sparkline unten.
        from bibi import config
        rows, local_runs = _jobs_data()
        return HTMLResponse(render.jobs_page(
            rows, local_runs, daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(), status_poll_interval_s=config.status_poll_interval(),
            job_status_poll_interval_s=config.job_status_poll_interval(),
            public_host=config.public_host(), lazy_sparklines=True))

    @app.get("/-/ui/jobs/{slug}/sparkline", include_in_schema=False)
    def jobs_sparkline(slug: str):
        # Pro-Slug-Gegenstück zu jobs_screen()s lazy_sparklines=True — jede
        # Zeile feuert das beim initialen Laden einmal selbst (s. render.
        # _sparkline_cell_lazy()). Ruft dieselbe gecachte, jetzt gesperrte
        # _job_sparkline_series() wie zuvor auf (s. dortiger Docstring) —
        # kein neuer Berechnungspfad, nur ein neuer Zugriffspunkt darauf.
        #
        # Bugfix (User-Fund 2026-07-22, "zieht meinen ganzen Rechner in die
        # Knie. Immer noch!"): rief hier bis eben _jobs_data() — vault-weite
        # Discovery + git-status-Subprozess + zwei HTTP-Selbstaufrufe
        # (run_live_list()/run_journal()) — pro Zeile auf, obwohl
        # _job_sparkline_series() aus jeder Zeile ausschließlich slug+
        # repo_path liest; git_status/live/local_runs wurden berechnet und
        # sofort verworfen. Bei N Jobs multiplizierte das die teuerste
        # Teil-Pipeline (Discovery-Scan) von 1x auf N+1x pro Seitenaufbau —
        # jetzt _local_schedules() direkt, ohne Git-Subprozess und ohne die
        # zwei Selbstaufrufe.
        rows = [{"slug": s, "repo_path": v["repo_path"]}
                for s, v in _local_schedules().items() if v.get("repo_path")]
        sparklines = _job_sparkline_series(rows)
        return HTMLResponse(render._sparkline_cell(slug, sparklines))

    @app.get("/-/ui/jobs/board", include_in_schema=False)
    def jobs_board():
        # Self-Poll-Ziel von #jobsboard (wie #live/#journal bei Schedules).
        from bibi import config
        rows, local_runs = _jobs_data()
        return HTMLResponse(render.jobs_fragment(
            rows, local_runs, public_host=config.public_host()))

    @app.get("/-/ui/jobs/archive", include_in_schema=False)
    def jobs_archive_screen():
        # Archive-Screen (Client, Bibi4-Iteration) — eigener Screen für die
        # lokale Lauf-Historie, seit dieser Iteration nicht mehr Teil von
        # /-/ui/jobs (User-Fund: "der untere Abschnitt lokale Läufe wandert
        # in den eigenen Screen Archive"). Status-Kacheln analog zum Host-
        # Archive-Screen (fehlten hier bisher ebenfalls).
        from bibi import config
        return HTMLResponse(render.jobs_archive_page(
            _jobs_archive_runs(), daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(), status_poll_interval_s=config.status_poll_interval(),
            job_status_poll_interval_s=config.job_status_poll_interval(),
            client_rows=_client_rows_for_status()))

    @app.get("/-/ui/jobs/archive/list", include_in_schema=False)
    def jobs_archive_list_fragment():
        return HTMLResponse(render.jobs_archive_fragment(_jobs_archive_runs()))

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

    def _job_last_run_output(last_run: dict | None) -> dict | None:
        # PLAN-28 User-Feedback: "bei terminalen Status wurde der Output
        # entfernt... beim Host wird der Output des letzten Laufes immer
        # oben angezeigt bis RESET oder START" — rollenunabhängig über
        # dieselbe Route wie die Run-Detailseite (/-/run/journal/{id}/output).
        if last_run is None:
            return None
        try:
            return client.local_run_output(last_run["id"])
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return None

    @app.get("/-/ui/jobs/detail/{slug}", include_in_schema=False)
    def jobs_detail(slug: str):
        from bibi import config
        local, last_run, runs = _job_detail_data(slug)
        live = _job_live(slug)
        return HTMLResponse(render.jobs_detail_page(
            slug, local, last_run, runs, daemon_status=_status(), live=live,
            last_run_output=None if live else _job_last_run_output(last_run),
            public_host=config.public_host()))

    @app.get("/-/ui/jobs/detail/{slug}/attrs", include_in_schema=False)
    def jobs_detail_attrs(slug: str):
        # PLAN-29 Befund 3+5: Gegenstück zu schedule_attrs()/schedule_config()
        # (Host) — lokal gespeist statt scheduler-gated, funktioniert deshalb
        # auch auf einem reinen Client (dort läuft schedule_attrs() heute
        # still ins Leere, s. render.jobs_detail_attrs_page()-Docstring).
        local = _local_schedules().get(slug)
        return HTMLResponse(render.jobs_detail_attrs_page(slug, local))

    @app.get("/-/ui/jobs/detail/{slug}/live", include_in_schema=False)
    def jobs_detail_live_fragment(slug: str):
        # Self-Poll-Ziel von #jobsdetail-live (wie #live bei Schedules).
        from bibi import config
        local, last_run, _runs = _job_detail_data(slug)
        live = _job_live(slug)
        return HTMLResponse(render.jobs_detail_live_fragment(
            slug, live, local, last_run,
            last_run_output=None if live else _job_last_run_output(last_run),
            public_host=config.public_host()))

    @app.post("/-/ui/jobs/detail/{slug}/kill", include_in_schema=False)
    def jobs_detail_kill(slug: str):
        # User-Fund 2026-07-10: "natürlich müssen wir kill können" — Analogon
        # zu schedule_action()s KILL-Verb (Host), aber lokal (client.run_live_kill()).
        from bibi import config
        try:
            client.run_live_kill(slug)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        local, last_run, _runs = _job_detail_data(slug)
        live = _job_live(slug)
        return HTMLResponse(render.jobs_detail_live_fragment(
            slug, live, local, last_run,
            last_run_output=None if live else _job_last_run_output(last_run),
            public_host=config.public_host()))

    @app.post("/-/ui/jobs/detail/{slug}/reset", include_in_schema=False)
    def jobs_detail_reset(slug: str):
        # User-Feedback 2026-07-13: "warum nicht START, RESET und KILL wie
        # auf Host" — Not-Aus für eine hängen gebliebene Live-Anzeige,
        # analog zu jobs_detail_kill(), aber lokal (client.run_live_reset()).
        from bibi import config
        try:
            client.run_live_reset(slug)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        local, last_run, _runs = _job_detail_data(slug)
        live = _job_live(slug)
        return HTMLResponse(render.jobs_detail_live_fragment(
            slug, live, local, last_run,
            last_run_output=None if live else _job_last_run_output(last_run),
            public_host=config.public_host()))

    @app.post("/-/ui/jobs/detail/{slug}/rebuild", include_in_schema=False)
    def jobs_detail_rebuild(slug: str):
        # User-Fund 2026-07-13: "REBUILD müsste doch auch beim Client
        # notwendig sein, oder?" — Analogon zu schedule_action()s REBUILD-Verb
        # (Host), aber lokal (client.run_rebuild()). Anders als KILL/RESET
        # hängt REBUILD an keiner Live-Zeile, deshalb kein _job_live()-Bezug
        # nötig — nur Fragment neu rendern.
        from bibi import config
        try:
            client.run_rebuild(slug)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        local, last_run, _runs = _job_detail_data(slug)
        live = _job_live(slug)
        return HTMLResponse(render.jobs_detail_live_fragment(
            slug, live, local, last_run,
            last_run_output=None if live else _job_last_run_output(last_run),
            public_host=config.public_host()))

    @app.post("/-/ui/jobs/detail/{slug}/start", include_in_schema=False)
    def jobs_detail_start(slug: str):
        # Bug gefunden beim Bau des Kill-Buttons (2026-07-10): der Start-Button
        # auf der Detailseite postete bisher an die generische, rollenweit
        # geteilte /-/ui/jobs/start/{slug} (Ziel #jobsboard, inzwischen mit
        # dem Start-CTA der Übersicht selbst entfernt, PLAN-28 User-Feedback)
        # — deren Antwort hätte #jobsdetail-live per outerHTML mit einem
        # #jobsboard-Fragment überschrieben (falsche id, falsches Self-Poll-
        # Ziel). Eigene Route, analog zu jobs_detail_kill(), gibt das
        # richtige Fragment zurück.
        from bibi import config
        try:
            client.run(slug=slug)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        local, last_run, _runs = _job_detail_data(slug)
        live = _job_live(slug)
        return HTMLResponse(render.jobs_detail_live_fragment(
            slug, live, local, last_run,
            last_run_output=None if live else _job_last_run_output(last_run),
            public_host=config.public_host()))

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
        from bibi import config

        schedule, runs, job = _detail_data(slug)
        live_output = _detail_outputs(job)
        return HTMLResponse(render.schedule_detail_page(
            schedule, runs, job, slug=slug, live_output=live_output,
            daemon_status=_status(), public_host=config.public_host()))

    @app.get("/-/ui/schedule/{slug}/live", include_in_schema=False)
    def schedule_live_fragment(slug: str):
        # Self-Poll-Ziel von #live: Live-Block aktualisiert pending→running→…
        # #journal pollt bewusst NICHT mit (würde nachgeladene Infinite-Scroll-
        # Zeilen jeden Tick wieder plattmachen) — braucht `runs` trotzdem für
        # den last_status-Fallback in der Meta-Zeile.
        from bibi import config

        schedule, runs, job = _detail_data(slug)
        live_output = _detail_outputs(job)
        return HTMLResponse(render.live_fragment(
            schedule, runs, job, slug=slug, live_output=live_output,
            public_host=config.public_host()))

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
        from bibi import config

        if verb not in render._VERBS and verb not in render._CONTAINER_VERBS:
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
            render.live_fragment(schedule, runs, job, slug=slug, now=now,
                                 public_host=config.public_host())
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
