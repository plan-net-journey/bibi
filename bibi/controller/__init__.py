"""Controller-Rolle (Phase 4): die Web-App auf dem Steuer-Namensraum ``/-/``.

PLAN-4 §2.1 — die App-Wurzel *ist* ``/-/`` (kein ``/-/overview``):

- Browser (``Accept: text/html``) → die HTML-App (htmx, kein Theme), **server-seitig**
  gerendert aus den ``/-/``-JSON-Endpunkten (via :class:`ControllerClient`, kein
  direkter DB-Zugriff — Akzeptanz §5).
- Nicht-Browser → knapper **JSON-Service-Deskriptor** (System-Info + App-Link);
  so bleibt §1.1 (reine JSON-API für Maschinen) auch an der Wurzel gewahrt.

Home = Feed (PLAN-18 Stufe 18.3, 2026-07-06) — löst die 2026-07-04-Entscheidung
„Home = Schedules" bewusst ab (Client-Umbau, ``Client Requirements.md``).
Der Schedules-Screen ist mit dem bibi5-Umbau **gestrichen**, sein Apparat mit
m.rau/bibi#159 zurückgebaut. Hier stand bis dahin, er bleibe „unter
``/-/ui/schedules`` vollständig erreichbar" — diese Route gibt es nicht, es
gibt nur die Detailseite ``/-/ui/schedule/{slug}`` im Singular.
Fragment-Routen liegen unter ``/-/ui/`` (App-Namensraum, kollidiert nicht mit
der gefrorenen Daten-API ``/-/<noun>``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse,
)

from bibi.daemon import activity, openapi, roles as roles_mod

from . import render
from .client import ControllerClient

log = logging.getLogger("bibi.controller")

#: Wie viele Output-Ströme dieser Knoten gleichzeitig zum Scheduler
#: durchreicht (#78). Der Durchreicher hält **je offener Output-Box je Tab**
#: eine Verbindung; ohne Deckel multipliziert sich das mit den Tabs, und ein
#: vergessenes Fenster kostet den Host dauerhaft Verbindungen.
#:
#: Der abgewiesene Fall ist bewusst harmlos: die Box behält ihren
#: server-seitigen Seed und bleibt lesbar, sie wächst nur nicht mit. Ein
#: Deckel, der eine Anzeige verstümmelt, wäre schlimmer als das Problem.
_MAX_OUTPUT_PROXIES = 8

#: Zähler dazu, plus sein Schloss. Modul-global statt pro App: die Grenze
#: gilt dem *Prozess* und den Verbindungen, die er nach draußen hält — nicht
#: einer App-Instanz, von denen die Tests regelmäßig mehrere bauen.
_output_proxies = 0
_output_proxy_lock = threading.Lock()

#: Wie lange ein Read im Durchreicher hoechstens blockiert.
#:
#: **Groesser als die Sendepause der Gegenseite, und das ist die ganze Regel.**
#: `_formatted_sse()` sendet bei >=15 s Stille ein `: ping`; ein Timeout
#: darunter heisst deshalb nicht *tot*, sondern *still* — und Stille ist beim
#: Output der Normalfall, ein Job denkt nach.
#:
#: Hier stand bis `v0.7.7` eine Sekunde, mit der Begruendung, ein abgebrochener
#: Verbraucher solle schnell bemerkt werden. **Derselbe Fehler wie im
#: Ereignis-Abonnement, und am selben Tag gefunden:** nach einem
#: `socket.timeout` ist der `http.client`-Stream unbrauchbar, der Leser faellt
#: heraus, und die Box im Browser verbindet neu — im Takt des Timeouts. Bei
#: einem Job wie `burndown-app`, der alle 300 s erhebt, waere das jede Sekunde
#: gewesen.
#:
#: Der Preis ist bekannt und kleiner: bricht der Browser ab, haelt der
#: Durchreicher seine Verbindung zum Scheduler noch bis zu dieser Frist. Dafuer
#: gibt es `_MAX_OUTPUT_PROXIES`.
_PROXY_READ_TICK_S = 45.0

__all__ = ["ControllerClient", "add_controller_routes", "render", "service_descriptor"]


async def _body_value(request: Request, name: str) -> str:
    """Einen Formularwert aus einem htmx-POST lesen.

    htmx schickt die Werte von ``hx-include`` bei POST/PUT/PATCH als
    ``application/x-www-form-urlencoded`` im **Body** — nicht im Query-String,
    wo FastAPI einen ``str``-Parameter mit Default sucht. Genau daran scheiterte
    das Versionsfeld (s. ``clients_set_expected_version()``).

    Von Hand geparst statt über ``Form(...)``: das verlangt ``python-multipart``
    als zusätzliche Laufzeit-Abhängigkeit auf jedem Knoten, und für urlencoded
    ist ``parse_qs`` der zuständige Parser aus der Standardbibliothek — dieselbe
    Haltung, mit der der Rest des Codes HTTP über ``urllib`` statt über eine
    Client-Bibliothek spricht.

    Der Query-String bleibt als Fallback gültig: ein Aufruf per ``curl
    -d`` und einer per ``?version=…`` sollen beide funktionieren.
    """
    from urllib.parse import parse_qs
    try:
        raw = (await request.body()).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        raw = ""
    if raw:
        values = parse_qs(raw, keep_blank_values=True)
        if name in values and values[name]:
            return values[name][0].strip()
    return (request.query_params.get(name) or "").strip()


#: Fenster des Feed beim ersten Seitenaufruf. **Sieben Tage, nicht einer.** Der
#: frühere Ein-Tages-Default war reine Vorsicht: ein unbegrenzter Log brauchte
#: live 5,7 s und lief in das 5-s-Timeout des Controller-Selbstaufrufs. Seit
#: ``feed.agent_slugs()`` mit einem einzigen git-Aufruf auskommt, kosten 30 Tage
#: 0,18 s — die Begründung ist entfallen, und ein Tag sind mit der
#: Ordner-Aggregation nur noch rund 14 Zeilen für eine Frage, die „was ist
#: passiert" heisst. Eine unbegrenzte Historie gibt es weiterhin nicht.
_FEED_DEFAULT_DAYS = 7


class _Backoff:
    """Nach einem Fehlschlag eine Weile gar nicht erst probieren.

    Ist der Scheduler weg, wartet **jeder** Seitenaufbau den Client-Timeout ab,
    bevor er rendert (Befund m.rau: *„die Abfrage dauert lange … darf die UX
    nicht stören"*). Der Zustand „nicht erreichbar" ändert sich aber nicht im
    Sekundentakt — er einmal festgestellt und dann eine Weile geglaubt.

    Damit ist der Screen bei offline **schneller** als bei online, was richtig
    ist: es gibt nichts zu holen, und der letzte bekannte Stand steht gedimmt
    ohnehin schon da.
    """

    def __init__(self, *, pause: float = 15.0) -> None:
        self.pause = pause
        self._bis: float | None = None

    def darf(self, *, now: float) -> bool:
        return self._bis is None or now >= self._bis

    def fehlschlag(self, *, now: float) -> None:
        self._bis = now + self.pause

    def erfolg(self) -> None:
        self._bis = None


class _MitMerker:
    """Ein Scheduler-Client, der den Ausfall-Merker führt statt ihn zu ignorieren.

    **Warum hier und nicht bei den Aufrufern** (m.rau/bibi#122): der Merker lag
    zuerst nur an ``_scheduler_status()``. Der Jobs-Screen fragt den Host aber
    dreimal — Status, Journal, Schedules —, und die beiden Datenabrufe liefen
    ungeschützt in ihre vollen 5 s. Live gemessen bei abwesendem Scheduler:
    **11,9 s je Seitenaufbau**, immer wieder, weil kein Abruf sich merkte, was
    der vorige schon wusste. Das Ticket nannte 5 s.

    Jeder Aufruf durch dieses Objekt prüft den Merker **vor** dem Netz und
    wirft sofort, wenn der Host als abwesend gilt. Die Aufrufer bleiben
    unverändert: sie fangen ohnehin jede Ausnahme und liefern ihren Default —
    ein sofortiger Wurf ist für sie derselbe Fall wie ein Timeout, nur ohne die
    fünf Sekunden.

    Damit kostet der **erste** Aufbau einen Timeout statt drei (der erste
    Fehlschlag überspringt die folgenden Abrufe) und jeder weitere innerhalb
    der Pause gar keinen. Das ist die Fassung der sechsten Lehre für dieses
    Problem: nicht mehr Sorgfalt an drei Stellen, sondern eine Stelle.
    """

    def __init__(self, echt, backoff: _Backoff) -> None:
        self._echt = echt
        self._backoff = backoff

    def __getattr__(self, name: str):
        methode = getattr(self._echt, name)
        if not callable(methode):
            return methode

        def _ruf(*args, **kwargs):
            jetzt = time.time()
            if not self._backoff.darf(now=jetzt):
                raise OSError(
                    f"Scheduler gilt als nicht erreichbar, {name!r} übersprungen "
                    "(m.rau/bibi#122)")
            try:
                wert = methode(*args, **kwargs)
            except Exception:
                self._backoff.fehlschlag(now=jetzt)
                raise
            self._backoff.erfolg()
            return wert

        return _ruf

#: Suffix, den ``worker.run_pinned()`` je Lauf anhängt (``token_hex(4)``).
_PIN_SUFFIX = re.compile(r"^(.*)-[0-9a-f]{8}$")


def _local_run_status_aus(eintraege: list[dict]) -> dict:
    """Journal-Zeilen → letzter lokaler Lauf je Slug.

    Zwei Dinge, die beide einmal falsch waren:

    * **Gepinnte Läufe gehören zu ihrem Job.** ``run_pinned()`` hängt je Lauf
      acht Hex-Zeichen an; ohne Rückrechnung zerfällt ein Job in so viele
      Einträge, wie er lokale Läufe hatte. Die feste Länge **acht** trennt das
      sauber von den Vier-Hex-Suffixen der ``at``-Slugs
      (``20260728.at-150738-81ec`` bleibt unangetastet).
    * **Der neueste Lauf gewinnt, nicht der zuerst gefundene.** Die
      Journal-Reihenfolge ist nicht die Zeitreihenfolge; ein ``setdefault()``
      behielt deshalb irgendeinen. Live zeigte ``gmail-transfer`` dadurch
      ``6d 1h`` — die Standzeit eines Laufs vom 14.07., der beim Aufräumen am
      20.07. terminal gesetzt wurde (Befund m.rau).
    """
    def basis(slug: str) -> str:
        m = _PIN_SUFFIX.match(slug)
        return m.group(1) if m else slug

    aus: dict = {}
    for e in eintraege:
        b = basis(e.get("slug") or "")
        vorher = aus.get(b)
        if vorher is None or (e.get("finished_at") or 0) > (vorher.get("finished_at") or 0):
            aus[b] = e
    return aus


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
        ergebnis = discovery.discover(case_dir)
        found = ergebnis.found
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
            # PLAN-29 Befund 3+5: Grundlage für die Attribut-Seite — alles
            # hier ist statische Frontmatter (ScheduleSpec), keine
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


#: Zustaende, in denen ein Lauf noch Ausgabe nachliefern kann (#124). Dieselbe
#: Menge, die `_live_placeholder_row()` als "aktiver Lauf" fuehrt — `pending`
#: gehoert ausdruecklich nicht dazu: dort wartet nur ein Platz, es laeuft nichts.
_LEBENDE_ZUSTAENDE = ("running", "awaiting", "deferred")


def _scheduler_url() -> str | None:
    from bibi import config
    return os.environ.get("BIBI_SCHEDULER_URL") or config.read_env().get("BIBI_SCHEDULER_URL")




#: Schützt die Cache-Befüllung (Bibi4-Iteration, Sparkline-Entkopplung):
#: mehrere gleichzeitige Pro-Slug-Requests (eine je Zeile, s. render.


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

    # htmx lokal ausliefern (PLAN-36 Stufe 36.0, Befund 3 in
    # FE-Live-Update-Briefing): bisher kam htmx von unpkg.com — im
    # Tailnet-only-Setup die einzige externe Abhängigkeit der Seiten; ohne
    # Internet (oder bei CDN-Ausfall) starb damit das komplette FE-Polling.
    # Versionierter Pfad statt generischem Namen: der Browser darf aggressiv
    # cachen (immutable), ein htmx-Upgrade ändert die URL und bustet den
    # Cache von selbst. Inhalt wird einmal gelesen und im Closure gehalten
    # (48 KiB) — kein Datei-I/O pro Request.
    from pathlib import Path as _Path

    from fastapi.responses import Response as _Response

    _htmx_bytes = (_Path(__file__).parent / "static" / "htmx.min.js").read_bytes()

    @app.get("/-/static/htmx-1.9.12.min.js", include_in_schema=False)
    def htmx_asset():
        return _Response(content=_htmx_bytes, media_type="text/javascript",
                         headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # Chart.js analog (PLAN-36-Nachtrag, 2026-07-27): nach der htmx-
    # Lokalisierung war das jsdelivr-CDN die letzte externe Seiten-
    # Abhängigkeit — offline blieb das Chart auf /-/ui/schedules leer.
    # Gleiche Mechanik: einmal gelesen (~200 KiB im Closure), versionierter
    # Pfad, aggressives immutable-Caching.

    def _status() -> dict:
        try:
            return client.status()
        except Exception:  # noqa: BLE001 — Daemon-Selbstaufruf, defensiv (§2.7)
            return {}

    #: Letzter erfolgreicher Scheduler-Status samt Zeitpunkt. Der Header zeigt
    #: bei Ausfall die letzten Werte gedimmt und datiert (FE-Spezifikation §2)
    #: — dafür muss jemand sie behalten, und der Ausgefallene kann es nicht.
    _sched_cache: dict = {"status": None, "at": None}
    _sched_backoff = _Backoff()

    def _scheduler_status() -> tuple[dict | None, float | None]:
        """Status des Schedulers, plus „stale seit", wenn er nicht antwortet.

        Rückgabe ``(status, stale_since)``: ``stale_since`` bleibt ``None``,
        solange der Abruf gelingt, und trägt sonst den Zeitpunkt der letzten
        Antwort. Kam noch nie eine, sind beide ``None`` — dann gibt es nichts
        zu dimmen und der Block zeigt Striche.
        """
        url = _scheduler_url()
        if not url:
            # Der Knoten trägt die scheduler-Rolle selbst: sein eigener Status
            # *ist* der des Schedulers. Ein HTTP-Aufruf wäre ein Umweg über
            # sich selbst — und würde bei gesperrtem Port an sich scheitern.
            eigen = _status()
            if "scheduler" in (eigen.get("roles") or []):
                return eigen, None
            return None, None
        jetzt = time.time()
        if not _sched_backoff.darf(now=jetzt):
            # Kürzlich nicht erreichbar — der letzte Stand steht gedimmt da,
            # und ein zweiter Timeout brächte dieselbe Auskunft langsamer.
            return _sched_cache["status"], _sched_cache["at"]
        try:
            # 1,5 s statt 3: im Tailnet antwortet ein lebender Host in
            # Millisekunden; alles darüber ist bereits ein Ausfall.
            s = ControllerClient(url, timeout=1.5).status()
            _sched_cache["status"], _sched_cache["at"] = s, jetzt
            _sched_backoff.erfolg()
            return s, None
        except Exception:  # noqa: BLE001 — defensiv (§2.7): der Host darf ausfallen
            _sched_backoff.fehlschlag(now=jetzt)
            return _sched_cache["status"], _sched_cache["at"]

    def _schedules() -> list:
        try:
            return client.schedules()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

    def _effective_days(days: int | None) -> int | None:
        return _FEED_DEFAULT_DAYS if days is None else days

    def _feed_data(days: int | None) -> dict:
        try:
            return client.feed(days=days)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            activity.emit(log, logging.WARNING, "controller.feed_unreachable",
                         "Feed-Selbstaufruf fehlgeschlagen (Timeout/Fehler?) — "
                         "zeige leeren Feed statt abzustürzen", role="controller")
            return {"entries": []}

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
    def root(request: Request, days: int | None = None):
        _sched = _scheduler_status()
        # Home = Feed (PLAN-18 Stufe 18.3, löst 2026-07-04 "Home = Schedules"
        # bewusst ab). Browser → Feed-Screen; Nicht-Browser → JSON-Deskriptor
        # (§1.1 bleibt an der Wurzel gewahrt). Der Schedules-Screen ist
        # seit dem bibi5-Umbau gestrichen (m.rau/bibi#159) — hier stand,
        # er bleibe unter /-/ui/schedules erreichbar; die Route gibt es nicht.
        if _wants_html(request):
            from bibi import config
            eff_days = _effective_days(days)
            return HTMLResponse(render.feed_page(
                _feed_data(eff_days), git_status=_feed_git_status(),
                host_url=_scheduler_url(), days=eff_days,
                daemon_status=_status(),
                client_rows=_client_rows_for_status(), scheduler=_sched[0], scheduler_stale_since=_sched[1]))
        return JSONResponse(service_descriptor(roles))

    @app.get("/-/ui/feed/board", include_in_schema=False)
    def feed_board(days: int | None = None):
        eff_days = _effective_days(days)
        daten = _feed_data(eff_days)
        # **Das Ziel des LOAD-MORE-Knopfes wird hier bestimmt, nicht im
        # Renderer** (#34): es braucht die Einträge eines *größeren* Fensters,
        # und die zu holen ist ein Netzaufruf. Der Renderer bleibt damit rein.
        #
        # Ein zweiter Aufruf je Seitenaufbau ist der Preis, und er ist bewusst
        # bezahlt: die Alternative wäre ein Knopf, der bei jedem Klick erneut
        # zu wenig liefert — genau der Befund, um den es geht.
        if eff_days:
            gross = _feed_data(eff_days + render._LOAD_MORE_GRENZE)
            daten = {**daten, "next_days": render.naechstes_fenster(
                gross.get("entries") or [], aktuell=eff_days, now=time.time())}
        return HTMLResponse(render.feed_fragment(daten, days=eff_days))

    @app.get("/-/ui/feed/status", include_in_schema=False)
    def feed_status():
        # Bus-Refetch-Ziel von #feedstatus (Target "feedstatus", PLAN-36
        # Stufe 36.3; zusätzlich bibiMaintChanged-Trigger des MAINT-Toggles)
        # — dieselben Datenquellen wie root(), nur ohne Heatmap/Änderungsliste.
        sched, stale = _scheduler_status()
        return HTMLResponse(render.feed_status_fragment(
            _status(), _feed_git_status(), _scheduler_url(), time.time(),
            client_rows=_client_rows_for_status(),
            scheduler=sched, scheduler_stale_since=stale))

    #: 180 Tage — eine Ansichtswahl ist eine UI-Präferenz und kein Sitzungswert.
    _VIEW_COOKIE_MAX_AGE = 60 * 60 * 24 * 180

    #: Die sechs Achsen des Jobs-Screens, je mit ihrem Cookie-Namen. Die ersten
    #: drei sind Mehrfachauswahl (kommagetrennt abgelegt), die letzten drei
    #: Einzelwerte.
    _VIEW_LISTEN = (("typ", "bibi_jobs_typ"), ("status", "bibi_jobs_status"),
                    ("journal", "bibi_jobs_journal"))

    def _jobs_view(request: Request, sort: str | None, direction: str | None):
        """Die effektive Ansicht: Query gewinnt, sonst die gemerkte Wahl (#156).

        **Query gewinnt immer** — sonst wäre eine geteilte URL nicht teilbar,
        weil der Empfänger seine eigene Erinnerung darübergelegt bekäme. Das
        war schon bei #66 die Regel; sie ist der Grund, warum dies ein Rückfall
        ist und keine Vorbelegung.

        Was „Query\" heißt, entscheidet ``render.VIEW_MARKER``: eine URL mit
        ``f=1`` ist vollständig, auch wo sie schweigt — dort wurde alles
        abgewählt. Ohne den Marker ist eine leere Query nur *nichts gesagt*,
        und dann darf der Cookie antworten. Ohne diese Unterscheidung brächte
        er den eben gelöschten Filter zurück, und der Filter-Knopf wäre tot.
        """
        q = request.query_params
        explizit = render.VIEW_MARKER in q
        werte: dict = {}
        for name, cookie in _VIEW_LISTEN:
            aus_url = q.getlist(name)
            if aus_url or explizit:
                werte[name] = aus_url
            else:
                gemerkt = (request.cookies.get(cookie) or "").strip()
                werte[name] = [t for t in gemerkt.split(",") if t]
        if sort is None and not explizit:
            sort = request.cookies.get("bibi_jobs_sort") or None
            direction = direction or request.cookies.get("bibi_jobs_dir") or None
        if sort not in render.sortierbare_schluessel():
            sort = None              # alter Cookie / manipulierte URL
        if direction not in ("asc", "desc"):
            direction = "asc"
        if "group" in q:
            group = q.get("group") != "off"
        elif explizit:
            group = True
        else:
            group = (request.cookies.get("bibi_jobs_group") or "on") != "off"
        werte.update(sort=sort, direction=direction, group=group)
        return werte

    def _merke_jobs_view(resp: HTMLResponse, view: dict) -> None:
        """Die eben gezeigte Ansicht als Cookie ablegen — für die Wiederkehr.

        Nicht für den Bus-Refetch: der trägt seine Query selbst (``render.
        _jobs_view_query()``), weil zwei Browser-Tabs sich einen Cookie teilen
        und der zweite dem ersten sonst die Sicht überschriebe.
        """
        for name, cookie in _VIEW_LISTEN:
            resp.set_cookie(cookie, ",".join(view[name]),
                            max_age=_VIEW_COOKIE_MAX_AGE, httponly=True,
                            samesite="lax")
        resp.set_cookie("bibi_jobs_sort", view["sort"] or "",
                        max_age=_VIEW_COOKIE_MAX_AGE, httponly=True, samesite="lax")
        resp.set_cookie("bibi_jobs_dir", view["direction"],
                        max_age=_VIEW_COOKIE_MAX_AGE, httponly=True, samesite="lax")
        resp.set_cookie("bibi_jobs_group", "on" if view["group"] else "off",
                        max_age=_VIEW_COOKIE_MAX_AGE, httponly=True, samesite="lax")

    def _host_worker_entry() -> dict:
        """Die eigene Zeile im Nodes-Screen (Batch 9 Punkt 3, User-Fund: "wir
        können aber doch den Host, auf dem der Client Screen dargestellt wird,
        mit in die Liste aufnehmen"). ``WorkerRegistry`` kennt nur Knoten, die
        sich per Heartbeat *gemeldet* haben — bei sich selbst meldet sich
        keiner (dieselbe ``scheduler``+``connect``-Ausschluss-Invariante wie
        beim "warum sehe ich den Worker nicht"-Fund, ``daemon/roles.py``).

        Erhoben wird sie in ``daemon/node_info``, wo sie auch ``/-/status``
        ausliefert: dieselbe Auskunft für den, der hier sitzt, und für den, der
        von woanders fragt. Zwei Fassungen davon wären zwei Wahrheiten."""
        from bibi.daemon import node_info
        return node_info.self_entry(roles)

    def _worker_rows(sched: dict | None = None) -> list[dict]:
        """Die Knoten der Föderation — die des **Schedulers**, nicht die eigenen.

        **Der vierte Fall der Selbstaufruf-Falle** (gefunden am 2026-08-04 bei
        der Deploy-Abnahme): der Screen baute seine Tabelle aus ``_status()``,
        also aus dem eigenen Daemon. Auf dem Host stimmte das, denn dort *liegt*
        die Registry. Auf einem Client ist sie leer, und übrig blieb allein die
        synthetische Eigenzeile — während der Header derselben Seite zwei Zeilen
        weiter oben ``clients 2`` schrieb. Seit dem Wegfall der ``controller``-
        Rolle auf sarasate gab es keinen Knoten mehr, auf dem der Screen richtig
        funktionierte.

        Drei Quellen, in dieser Reihenfolge:

        1. die **Registry des Schedulers** — jeder Knoten, der sich gemeldet hat;
        2. seine **Selbstauskunft** (``status["node"]``) — er meldet sich
           nirgends, steht also in keiner Registry, auch nicht in seiner eigenen;
        3. die **eigene Zeile**, falls der Scheduler uns nicht kennt: kein
           ``connect``, erster Heartbeat noch aus, oder Scheduler nicht
           erreichbar. Dann steht wenigstens der Knoten da, auf dem man sitzt.

        Doppelt taucht niemand auf — verglichen wird die ``node_id``. Eine
        gemeldete Zeile schlägt dabei eine gebaute: sie trägt ``Connected seit``
        und ``Letzter Heartbeat``, die lokal nicht zu erheben sind.
        """
        status = sched if sched is not None else _scheduler_status()[0]
        if not status:
            status = _status()
        rows = [w for w in (status.get("workers") or []) if isinstance(w, dict)]
        bekannt = {w.get("node_id") for w in rows}
        selbst = status.get("node")
        if isinstance(selbst, dict) and selbst.get("node_id") not in bekannt:
            rows.insert(0, selbst)
            bekannt.add(selbst.get("node_id"))
        eigen = _host_worker_entry()
        if eigen.get("node_id") not in bekannt:
            rows.insert(0, eigen)
        return rows

    def _restart_order(workers: list[dict]) -> list[dict]:
        """Rollierend, und die Reihenfolge ist nicht beliebig: erst die übrigen
        Knoten, dann der Scheduler, zuletzt der eigene.

        **Der Scheduler trägt die Föderation** — startet er zusammen mit den
        Clients neu, laufen deren Heartbeats für die Dauer beider Neustarts ins
        Leere; dann ist die Registry beim Wiederkommen jedes Clients bereits
        wieder da. **Der eigene Knoten führt die Schleife aus** — wer sich
        selbst in der Mitte neu startet, stellt den Rest nie zu.

        Auf dem Host fielen beide Rollen zusammen, „Host zuletzt" genügte. Von
        einem Client aus sind es zwei verschiedene Knoten. Innerhalb einer Stufe
        bleibt die Reihenfolge, wie sie kam (``sorted`` ist stabil).
        """
        eigen = _host_worker_entry().get("node_id")

        def rang(w: dict) -> int:
            if w.get("node_id") == eigen:
                return 2
            return 1 if "scheduler" in (w.get("role") or "") else 0

        return sorted(workers, key=rang)

    @app.get("/-/ui/clients", include_in_schema=False)
    def clients_screen():
        _sched = _scheduler_status()
        # Nodes-Screen (Batch 9 Punkt 3 umbenannt von "Clients") — Backend
        # (WorkerRegistry, /-/worker) existierte schon lange, hier nur die
        # Darstellung. Die Tabelle zeigt die Föderation (_worker_rows()), die
        # CLIENT-Kachel des Headers den eigenen Daemon: zwei Fragen, zwei
        # Quellen, und genau ihre Vermischung war der Fehler.
        return HTMLResponse(render.clients_page(
            _worker_rows(_sched[0]), daemon_status=_status(),
            git_status=_feed_git_status(),
            host_url=_scheduler_url(), scheduler=_sched[0], scheduler_stale_since=_sched[1]))

    @app.get("/-/ui/clients/board", include_in_schema=False)
    def clients_board_fragment():
        return HTMLResponse(render.clients_fragment(_worker_rows()))

    @app.post("/-/ui/clients/{node_id}/{verb}", include_in_schema=False)
    def clients_node_action(node_id: str, verb: str):
        # PLAN-32 Stufe 32.1: Approve-/Block-Buttons im Nodes-Screen — wirkt,
        # dann #clientsboard sofort neu rendern (analog schedule_action()s
        # Sofort-Swap statt auf das nächste nodes-Bus-Event zu warten).
        if verb not in ("approve", "block", "restart", "deploy"):
            return JSONResponse(status_code=404, content={"error": "unknown verb"})
        workers = _worker_rows()
        if verb in ("restart", "deploy"):
            # m.rau/bibi#39: direkt beim Zielknoten, nicht über den Scheduler.
            # Host und Port stehen in der Registry — aus dem Heartbeat des
            # Knotens selbst, also so, wie er sich erreichbar meldet.
            target = next((w for w in workers if w.get("node_id") == node_id), None)
            if target and target.get("port"):
                client.restart_node(target.get("host") or "127.0.0.1",
                                    int(target["port"]),
                                    deployment=(verb == "deploy"))
            # Kurz warten, damit der Knoten beim Neu-Rendern schon als
            # disconnected erscheint statt scheinbar unverändert — sonst wirkt
            # der Knopf folgenlos.
            import time as _t
            _t.sleep(1.0)
            workers = _worker_rows()
            return HTMLResponse(render.clients_fragment(workers))
        try:
            client.node_action(node_id, verb)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        workers = _worker_rows()
        return HTMLResponse(render.clients_fragment(workers))

    @app.post("/-/ui/clients/expected-version", include_in_schema=False)
    async def clients_set_expected_version(request: Request, deploy: bool = False):
        """Die erwartete Engine-Version setzen (m.rau/bibi#39).

        Das Ändern **ist** der Deploy: `pyproject.toml` schreiben, `uv lock`
        regenerieren, committen, pushen. Es entsteht kein zweites Soll-Feld
        neben der Lock — sie bleibt die einzige Wahrheit, dieses Feld ist ihre
        Bedienoberfläche. Dass der Controller dafür ins Repo schreiben darf,
        ist eine ausdrückliche Entscheidung (m.rau, 2026-07-30).

        Der Rollout ist bewusst optional (`deploy`): erst sehen, was die Lock
        sagt, dann ausrollen — oder beides in einem Zug.

        **Der Wert kommt aus dem Request-Body, nicht aus dem Query-String.**
        Das war der Fehler, den m.rau am 2026-07-31 gemeldet hat: „unzulässiger
        Ref: ''", obwohl im Feld ``v0.3.0`` stand. Die Signatur lautete
        ``version: str = ""``, und ein einfacher Default ist in FastAPI ein
        *Query*-Parameter — htmx packt die Werte von ``hx-include`` bei einem
        POST aber in den Body (urlencoded). Der Parameter blieb also immer leer:
        das Feld hat nie funktioniert, seit es existiert. Die bestehenden Tests
        prüften nur das gerenderte HTML, nie einen echten POST — deshalb fiel es
        weder beim Bauen noch beim Release auf.
        """
        from bibi.daemon import deploy as deploy_mod
        version = await _body_value(request, "version")
        # Optimistisches Sperren (m.rau/bibi#57): die Seite schickt den Ref mit,
        # den sie beim Rendern gesehen hat. Weicht der vom tatsächlichen ab, ist
        # der Tab veraltet — und sein Feld trägt dann einen alten Wert, dessen
        # Rückschreiben beim nächsten Neustart JEDEN Knoten herabstufen würde.
        # Am 2026-07-31 um 16:20 genau so passiert, eine Minute nach dem
        # v0.4.0-Rollout.
        #
        # Schärfer als ein ``hx-confirm``, und zwar bewusst: eine Rückfrage
        # zeigte denselben veralteten Wert und lüde zum Bestätigen ein. Wer
        # wirklich herabstufen will, sieht nach dem Neuladen den echten Stand
        # und entscheidet dann.
        seen = await _body_value(request, "seen")
        actual = deploy_mod.current_ref()
        if seen and actual and seen != actual:
            stale = {"ok": False,
                     "error": "Seite veraltet — nichts geschrieben",
                     "detail": f"diese Seite zeigte {seen}, tatsächlich steht "
                               f"{actual}. Neu laden und erneut versuchen."}
            workers = _worker_rows()
            return HTMLResponse(render.clients_fragment(workers,
                                                        deploy_result=stale))
        res = deploy_mod.set_expected_version(version)
        if res.get("ok") and res.get("changed") and deploy:
            import time as _t
            for w in _restart_order(_worker_rows()):
                if w.get("port"):
                    client.restart_node(w.get("host") or "127.0.0.1",
                                        int(w["port"]), deployment=True)
                    _t.sleep(0.3)
            _t.sleep(1.0)
        workers = _worker_rows()
        return HTMLResponse(render.clients_fragment(workers, deploy_result=res))

    @app.post("/-/ui/clients/restart-all", include_in_schema=False)
    def clients_restart_all(deploy: bool = False):
        """„Restart all" (m.rau/bibi#39) — rollierend, nicht gleichzeitig.

        Die Reihenfolge steht in ``_restart_order()``: erst die übrigen Knoten,
        dann der Scheduler, zuletzt der eigene. Bei drei Knoten und je drei
        Sekunden ist der Unterschied klein, aber er kostet nichts.
        """
        import time as _t
        for w in _restart_order(_worker_rows()):
            if not w.get("port"):
                continue
            client.restart_node(w.get("host") or "127.0.0.1", int(w["port"]),
                                deployment=deploy)
            _t.sleep(0.3)
        _t.sleep(1.0)
        workers = _worker_rows()
        return HTMLResponse(render.clients_fragment(workers))

    @app.get("/-/ui/logs", include_in_schema=False)
    def logs_page():
        _sched = _scheduler_status()
        return HTMLResponse(render.log_page(
            daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(),
            client_rows=_client_rows_for_status(), scheduler=_sched[0], scheduler_stale_since=_sched[1]))

    def _jobs_data() -> tuple[list, dict]:
        """PLAN-21 Befund 10, User-Entscheidung: der Jobs-Screen dient
        ausschließlich dem Review der lokalen Repository-Realität, kein
        Remote-Abgleich mehr (weder Netzaufruf noch Vergleichsspalte).
        ``rows`` = lokal entdeckte Job-MDs + echter Git-Status je Datei
        (``local_files_status()``) — gelöschte MDs tauchen von selbst nicht
        mehr auf, da ``discovery.discover()`` sie nicht mehr findet.

        Gibt seit der Bibi4-Iteration nur noch ``(rows, local_runs)`` zurück —
        die dritte Rückgabe (flache Journal-Liste für "Lokale Läufe") lebt
        jetzt auf ``_local_journal_runs()``."""
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
        # weiterhin die volle Journal-Liste.
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
        for run in _local_journal_runs():
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

    def _local_journal_runs() -> list:
        # Flache Journal-Liste über alle lokalen Jobs, ungedeckelt bis zum
        # 200er-Server-Limit.
        #
        # Sie hiess `_jobs_archive_runs()` und war nach dem Archive-Screen
        # benannt, der mit m.rau/bibi#130 entfaellt — ihr einziger Aufrufer ist
        # aber `_jobs_data()`, also der Pro-Job-Lookup des Jobs-Screens. Eine
        # Funktion nach ihrem frueheren Zuhoerer zu benennen verliert ihren
        # Sinn, sobald der Zuhoerer geht (dieselbe Lehre wie beim Bus-Ereignis
        # `chart` → `archived`, #108).
        try:
            return client.run_journal(limit=200)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

    def _detail_data(slug: str):
        """Die Daten der Job-Detailseite — **vom Scheduler** (m.rau/bibi#86).

        Bis zum 2026-08-09 ging das ueber ``client``, also den **eigenen**
        Daemon. ``/-/job`` und ``/-/schedule`` gibt es aber nur unter
        ``roles.scheduler``; auf einem reinen Client antworten sie mit dem
        eingefrorenen ``501``-Stub bzw. ``404``, der defensive Fang unten
        lieferte ``(None, [], None)``, und ohne ``job`` rendert
        ``_live_panel()`` keine Box.

        Seit dem Rollenwechsel am 2026-08-04 ist der Client der **einzige**
        Knotentyp mit Oberflaeche — die Detailseite eines laufenden Jobs sah
        dort also aus wie die eines nie gelaufenen, und der Durchreicher aus
        `#78` lag auf einem Pfad, den kein Knoten erreicht.

        ``_host_client()`` ist derselbe Weg, den ``_host_schedules()`` schon
        geht; der Jobs-Screen desselben Clients zeigt Scheduler-Zeilen deshalb
        seit jeher richtig. Traegt dieser Knoten die Scheduler-Rolle selbst,
        gibt ``_host_client()`` den eigenen Client zurueck — fuer ihn aendert
        sich nichts.
        """
        ziel = _host_client()
        try:
            schedule = next((s for s in ziel.schedules()
                             if s.get("slug") == slug), None)
            # Erste Seite der Journal-Historie — der Rest lädt per Infinite Scroll
            # nach (GET .../runs?offset=N, render.journal_runs_fragment).
            runs = ziel.journal(slug=slug, limit=render._JOURNAL_PAGE_SIZE, offset=0)
            job = next((j for j in ziel.jobs() if j.get("slug") == slug), None)
            # Batch 9 Punkt 2: job["app_port"] fehlte hier komplett (anders als
            # bei _local_schedules()/jobs_data() beim Client) — _live_panel()s
            # "Zur App →"-Link (app_link, render.py) blieb dadurch auf der Host-
            # Detailseite für jeden App-Job unsichtbar, obwohl der Schedule
            # selbst app_port längst trägt (schedule_view()).
            if job is not None:
                job["app_port"] = schedule.get("app_port") if schedule else None
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
        # **Derselbe Adressat wie in _detail_data()** (m.rau/bibi#86): der Lauf
        # liegt beim Scheduler, seine `output.jsonl` also auch. Ueber `client`
        # gefragt, kaeme hier auf einem Client der 501-Stub — die Box haette
        # ihren Strom, aber keinen Anfangsbestand, und der erste sichtbare
        # Inhalt entstuende erst mit der naechsten gesendeten Zeile.
        live_output = None
        try:
            if job and job.get("id"):
                live_output = _host_client().job_output(job["id"])
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        return live_output

    def _output_stream_url(job: dict | None) -> str | None:
        """Woher die Output-Box eines laufenden Scheduler-Laufs waechst (#78).

        ``None`` heisst „vom globalen Bus", und das ist auf dem **Host** der
        richtige Weg: dort liegt die ``output.jsonl`` lokal, der Collector
        tailt sie, und ein Umweg ueber sich selbst waere keiner. Nur auf einem
        Knoten mit fremdem Scheduler fehlt die Datei — und nur dort braucht die
        Box ihren zweiten Strom.

        Auch ``None`` fuer jeden nicht laufenden Lauf: ein terminaler braucht
        keinen Strom, dort bleibt der einmalige Abruf richtig.

        **Geprueft wird die Rolle, nicht die Adresse** (m.rau/bibi#86). Bis zum
        2026-08-09 stand hier ``if not _scheduler_url()`` — das begruendete
        sein ``None`` mit „auf dem Host", verglich aber nur, **ob** eine
        Adresse gesetzt ist, nicht ob sie woanders hinzeigt. Ein Scheduler,
        dessen ``BIBI_SCHEDULER_URL`` auf ihn selbst zeigt (der Normalfall auf
        sarasate, s. ``d2c03bc``), schickte seine Box deshalb ueber einen
        Durchreicher zu sich selbst. Dieselbe Verwechslung, die ``d2c03bc``
        fuer das Abonnement bereits korrigiert hat — die Antwort ist hier
        dieselbe."""
        if not job or job.get("status") != "running" or not job.get("id"):
            return None
        if roles.scheduler:
            return None
        return f"/-/ui/jobs/{urllib.parse.quote(str(job['id']), safe='')}/output/stream"

    @app.get("/-/ui/schedule/{slug}", include_in_schema=False)
    def schedule_detail(slug: str):
        from bibi import config

        schedule, runs, job = _detail_data(slug)
        live_output = _detail_outputs(job)
        return HTMLResponse(render.schedule_detail_page(
            schedule, runs, job, slug=slug, live_output=live_output,
            daemon_status=_status(), public_host=config.public_host(),
            output_stream_url=_output_stream_url(job)))

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
            public_host=config.public_host(),
            output_stream_url=_output_stream_url(job)))

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
        # Refetch-Ziel der journal:-Zustands-Events des Bus (PLAN-36 Stufe
        # 36.2; vorher Ziel des Fingerprint-Autorefresh-Skripts). live_job
        # mitgeben — der Bus meldet journal:-dirty bei JEDEM Statuswechsel,
        # ein Refetch während des Laufs muss die Live-Platzhalterzeile
        # erhalten (s. journal_fragment()-Docstring).
        _, runs, job = _detail_data(slug)
        return HTMLResponse(render.journal_fragment(runs, slug, time.time(),
                                                    live_job=job))

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
            + render.journal_fragment(runs, slug, now, oob=True, live_job=job)
        )

    @app.delete("/-/ui/schedule/{slug}/run/{jid}", include_in_schema=False)
    def run_delete(slug: str, jid: int):
        try:
            client.delete_journal(jid)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        _, runs, job = _detail_data(slug)
        return HTMLResponse(render.journal_fragment(runs, slug, time.time(),
                                                    live_job=job))

    # ── Die sechs Screens (bibi5, FE-Spezifikation §1) ───────────────────────
    #
    # Kanonische Adressen, eine je Screen, auf jedem Knoten dieselben. Die
    # `/-/ui/`-Routen darunter bleiben, was sie sind: htmx-Fragmente, die eine
    # Seite nachlädt — keine Screens, die jemand anspringt.
    #
    # Vorher hing die Adresse an der Rolle: ein Scheduler-Knoten zeigte
    # `/-/ui/schedules`, ein Client `/-/ui/jobs`, und beide hießen "Jobs". Ein
    # Link war damit nur zusammen mit der Rolle des Knotens verständlich, und
    # die App-Bar musste verzweigen. Jetzt zeigt sie immer alle sechs — was
    # verlangt, dass es alle sechs auch überall gibt (test_controller_nav.py::
    # test_every_screen_in_the_app_bar_is_reachable).

    @app.get("/-/jobs", include_in_schema=False)
    def screen_jobs(request: Request, sort: str | None = None, dir: str | None = None):
        """Der zentrale Screen: eine Zeile je Slug, beide Seiten nebeneinander.

        Die Klassifikation (welches Band, welches Beziehungslabel, was
        überhaupt sichtbar ist) macht ``jobs_view.build_rows()`` — eine reine
        Funktion über Listen. Hier wird nur beschafft.
        """
        from bibi import config
        _sched = _scheduler_status()
        zeilen, v, jetzt = _jobs_zeilen_und_view(request, sort, dir)
        resp = HTMLResponse(render.jobs_page_v5(
            zeilen, now=jetzt, daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(), scheduler=_sched[0], scheduler_stale_since=_sched[1],
            typ=v["typ"], status=v["status"], journal=v["journal"],
            sort=v["sort"], direction=v["direction"], group=v["group"],
            public_host=config.public_host()))
        _merke_jobs_view(resp, v)
        return resp

    def _jobs_zeilen_und_view(request: Request, sort: str | None,
                              dir: str | None) -> tuple[list, dict, float]:
        """Die Zeilen des Jobs-Materials plus die effektive Ansicht.

        **Vier Routen teilen sie sich** — Jobs, Journal und je ihr
        Listen-Fragment. Sie führen dieselbe Klassifikation und dürfen sich
        deshalb nicht auseinanderentwickeln: ein Job, der auf dem einen Screen
        als `dropped` gilt und auf dem anderen nicht, wäre in keinem von
        beiden zu finden.

        Mehrfachauswahl kommt als wiederholter Query-Parameter (`?typ=job&
        typ=app`) — die Toggles sind on/off und nicht exklusiv, und eine
        Ansicht soll teilbar sein. Fehlt die Query ganz, antwortet die
        gemerkte Wahl (#156).
        """
        import time as _t

        from bibi.controller import jobs_view
        jetzt = _t.time()
        historie = _journal_for_rows()
        zeilen = jobs_view.build_rows(
            local=_local_job_mds(), scheduler=_host_schedules(), journal=historie,
            now=jetzt, local_runs=_local_run_status())
        _quoten(zeilen, historie, jetzt)
        return zeilen, _jobs_view(request, sort, dir), jetzt

    def _quoten(zeilen: list, historie: list, jetzt: float) -> None:
        """Die 24H-Kennzahl an jede Zeile haengen.

        Getrennt von ``build_rows()``: dort waere das Journal ein zweiter
        Parameter mit anderer Bedeutung (Historie ja/nein gegen Laufliste),
        und die Funktion soll klassifizieren, nicht rechnen.
        """
        from bibi.controller import jobs_view
        laeufe_je_slug: dict = {}
        for e in historie:
            laeufe_je_slug.setdefault(e.get("slug"), []).append(e)
        for z in zeilen:
            trigger = z.spec.get("schedule") or z.spec.get("trigger")
            eigene = laeufe_je_slug.get(z.slug, [])
            erwartet = jobs_view.erwartete_laeufe(trigger)
            # Von Hand ausgeloest: was ueber die Erwartung hinausgeht. Genauer
            # waere ein Flag am Lauf; solange es das nicht gibt, ist das die
            # ehrlichste Naeherung -- und sie stimmt fuer den haeufigen Fall
            # "adhoc-Job, dreimal gestartet".
            im_fenster = [e for e in eigene
                          if (e.get("archived_at") or e.get("finished_at") or 0)
                          >= jetzt - 86400]
            manuell = max(0, len(im_fenster) - erwartet)
            z.quote = jobs_view.quote_24h(runs=eigene, expected=erwartet,
                                          manual=manuell, now=jetzt)

    def _local_job_mds() -> list[dict]:
        """Die lokal entdeckten Job-MDs **als Liste**, mit git-Status.

        Eine Liste und kein Dict, weil ein Dict die Kollision verschluckt: die
        Discovery legt kollidierende Slugs bewusst nicht in ``found`` (sie sind
        zur Laufzeit ignoriert, bis sie aufgelöst sind), und ein Dict könnte
        zwei Dateien mit demselben Slug ohnehin nicht halten. Der Screen sah
        den Slug deshalb gar nicht und zeigte ``deleted`` statt ``duplicate``
        (Befund m.rau, 2026-08-03).

        Der git-Status je Datei kommt mit — er ist die einzige Quelle für
        ``modified``, und ohne ihn blieb der Chip aus.
        """
        from bibi import repo as repo_mod
        from bibi.git_status import local_files_status
        from bibi.schedule import discovery
        try:
            case_dir = repo_mod.case_dir()
            root = repo_mod.root()
            ergebnis = discovery.discover(case_dir)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

        eintraege: list[dict] = []
        for slug, pr in ergebnis.found.items():
            eintraege.append({
                "slug": slug, "schedule": pr.spec.schedule, "at": pr.spec.at,
                "payload": pr.spec.payload, "app_port": pr.spec.app_port,
                "repo_path": (case_dir / pr.schedule_ref).relative_to(root).as_posix(),
            })
        # Kollisionen: je beanspruchender Datei ein Eintrag, damit
        # `build_rows()` sie als eine Zeile mit `duplicate` zusammenfasst und
        # beide Pfade nennen kann.
        #
        # Die Dateien werden dafuer noch einmal geparst. Das ist kein Umweg:
        # `SlugCollision` traegt nur Slug und Pfade, und ohne den Trigger
        # landete die Zeile im Journal-Band statt dort, wo man sie sucht --
        # ein `adhoc`-Job, den es zweimal gibt, gehoert zu den `adhoc`-Jobs.
        # Etwas zu verstecken, das Aufmerksamkeit braucht, waere der falsche
        # Ausgang.
        from bibi.schedule import parser as _parser
        for k in ergebnis.collisions:
            for ref in k.schedule_refs:
                pfad = case_dir / ref
                try:
                    pr = _parser.parse_file(pfad, vault_root=case_dir)
                    spec = pr.spec
                except Exception:  # noqa: BLE001 — defensiv (§2.7)
                    spec = None
                eintraege.append({
                    "slug": k.slug,
                    "schedule": spec.schedule if spec else None,
                    "at": spec.at if spec else None,
                    "payload": spec.payload if spec else "",
                    "app_port": spec.app_port if spec else None,
                    "repo_path": pfad.relative_to(root).as_posix(),
                })

        try:
            git_je_pfad = local_files_status(root, [e["repo_path"] for e in eintraege])
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            git_je_pfad = {}
        for e in eintraege:
            e["git_status"] = git_je_pfad.get(e["repo_path"], "clean")
        return eintraege

    def _host_schedules() -> list:
        """Die Schedules des Hosts — die linke Hälfte jeder Zeile."""
        try:
            return _host_client().schedules() or []
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return []

    def _host_client():
        """Ein Client auf den **Scheduler**, nicht auf uns selbst.

        Derselbe Fallstrick wie im Bus-Collector: `client` zeigt auf den
        eigenen Daemon. Auf einem reinen Client gibt es dort weder Schedules
        noch Team-Journal — die Zeilen trugen deshalb alle `(new)` und leere
        Scheduler-Spalten, obwohl der Host sie sehr wohl kennt.

        Trägt dieser Knoten die scheduler-Rolle selbst, ist der eigene Client
        der richtige: ein HTTP-Aufruf über sich selbst wäre ein Umweg.

        **Der Ausfall-Merker sitzt hier** (m.rau/bibi#122, s. ``_MitMerker``):
        an der einen Stelle, die jeder Scheduler-Abruf passiert. Am eigenen
        Client hängt er bewusst nicht — dort gibt es kein Netz, das ausfallen
        könnte, und eine Sperre wäre ein Selbstblock.
        """
        url = _scheduler_url()
        if not url:
            return client
        return _MitMerker(ControllerClient(url, timeout=5.0), _sched_backoff)

    def _journal_for_rows() -> list:
        """Nur so viel Journal, wie die Klassifikation braucht: welche Slugs
        überhaupt Historie haben. Die Läufe selbst zeigt Job Detail.

        **Beide Journale, nicht nur das des Schedulers** (m.rau/bibi#130). Sie
        fragte ausschließlich den Host — ein Job, der nur lokal lief
        (``bibi-ctrl run``), hat dort keine Zeile und fiel damit aus der
        Klassifikation heraus. Live gemessen: **20 von 33** lokal gelaufenen
        Jobs standen nicht im Jobs-Screen.

        Das war hinnehmbar, solange der Archive-Screen sie führte. Mit seiner
        Streichung ist es das nicht mehr: FE §1 begründet sie ausdrücklich
        damit, dass „das JOURNAL-Segment jeden Job führt, auch den ohne MD".
        Ein heimatloser Lauf wäre sonst unerreichbar geworden.

        Der lokale Slug wird dabei auf seinen Basis-Job zurückgerechnet:
        ``run_pinned()`` hängt je Lauf einen Zufallssuffix an, und ohne das
        zerfiele ein Job in so viele Zeilen, wie er lokale Läufe hatte (live:
        252 Pseudo-Slugs für 33 Jobs). ``bucket_slug()`` schneidet nur bei
        gesetztem ``pinned_host`` — sonst wäre es Raten am Namen.
        """
        from bibi.daemon import job_db
        from bibi.daemon.bus import bucket_slug
        aus: list = []
        try:
            aus += _host_client().journal(limit=2000) or []
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        try:
            # Direkt aus der lokalen DB, nicht über ``client.run_journal()``:
            # das ginge per HTTP an den eigenen Daemon — derselbe Selbstaufruf,
            # der in diesem Umbau schon dreimal stumm zugeschlagen hat — und
            # sein Filter (``domain='local' OR pinned_host IS NOT NULL``) lässt
            # den Altbestand bis 04.07.2026 ohnehin liegen.
            conn = job_db.connect()
            try:
                for r in job_db.list_journal(conn, limit=2000):
                    slug = bucket_slug(r.get("slug") or "", r.get("pinned_host"))
                    aus.append({**r, "slug": slug or r.get("slug")})
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        return aus

    def _slug_kandidaten() -> list[str]:
        """Alle Slugs, hinter denen ein ``job_uid`` stecken kann.

        Drei Quellen, weil keine allein reicht: die lokalen MDs (auch für
        Jobs, die noch nie liefen), die Scheduler-Schedules (auch für solche
        ohne lokale MD) und das Journal (auch für heimatlose Läufe, deren MD
        gelöscht wurde — der Weg zu ihnen führt seit m.rau/bibi#130 über das
        JOURNAL-Segment des Jobs-Screens, und ohne diese Quelle liefe er ins
        Nichts).
        """
        aus: list[str] = []
        for e in _local_job_mds():
            if e.get("slug"):
                aus.append(e["slug"])
        for e in _host_schedules():
            if e.get("slug"):
                aus.append(e["slug"])
        for e in _journal_for_rows():
            if e.get("slug"):
                aus.append(e["slug"])
        return aus

    #: Das voreingestellte Zeitfenster der Lauf-Liste in Tagen. `LOAD MORE`
    #: verbreitert es (FE §5.3).
    _RUN_TAGE = 30

    # ACHTUNG, Reihenfolge: `/-/jobs/list` MUSS vor `/-/jobs/{job_uid}` stehen.
    # Starlette matcht in Registrierungsreihenfolge, nicht nach Spezifität —
    # stand die feste Route dahinter, schluckte der Platzhalter sie und
    # antwortete `404 job not found, job_uid=list`. Genau so war es von
    # `e38d29a` bis `#151`: der Bus meldete korrekt, htmx holte die URL, bekam
    # 404 und swappte nicht (htmx swappt nur bei 2xx) — der Jobs-Screen
    # aktualisierte sich nie von selbst. `test_no_static_route_is_shadowed_by_
    # an_earlier_placeholder` hält das app-weit fest.
    @app.get("/-/jobs/list", include_in_schema=False)
    def screen_jobs_list(request: Request, sort: str | None = None,
                         dir: str | None = None):
        """Nur die Liste — das Nachlade-Ziel des Bus.

        Die Seite bleibt stehen, damit Scroll-Position und Fokus erhalten
        bleiben; getauscht wird der Inhalt von ``#jobs``.
        """
        from bibi import config
        # Dieselbe Auflösung wie der Screen: der Bus trägt die Ansicht zwar in
        # seiner Refetch-URL mit (#156), aber ein Aufruf ohne Query — von Hand,
        # aus einem alten Lesezeichen — soll nicht anders antworten als die
        # Seite, in der das Fragment steckt.
        zeilen, v, jetzt = _jobs_zeilen_und_view(request, sort, dir)
        # `jobs_list_fragment`, nicht `jobs_screen`: die Antwort muss ihren
        # Bus-Wrapper mitbringen, weil `_EVENTS_JS` mit `outerHTML` swappt —
        # sonst ist die Region nach genau einem Update abgemeldet.
        resp = HTMLResponse(render.jobs_list_fragment(
            zeilen, jetzt, typ=v["typ"], status=v["status"],
            journal=v["journal"], sort=v["sort"], direction=v["direction"],
            group=v["group"], public_host=config.public_host()))
        # **Auch hier merken** (#83). Bis dahin las diese Route den Cookie und
        # schrieb ihn nie — und ein Filter-Klick geht auf genau sie. Die Wahl
        # ueberlebte damit jeden Bus-Refetch (dafuer traegt die Refetch-URL
        # seit #156 alle Parameter mit) und keinen einzigen Seitenwechsel:
        # wer wegnavigierte und ueber den Tab zurueckkam, landete auf
        # `/-/jobs`, und das las den alten Stand.
        #
        # Befund m.rau, 2026-08-08: *„er muss auch greifen, wenn ich ueber Tabs
        # navigiere. Wenn ich zurueck komme, erwarte ich gleiche Filter."*
        #
        # Dass der Bus dieselbe Route refetcht, ist dabei **kein** Problem: er
        # traegt die aktuelle Ansicht in seiner URL mit, schreibt also denselben
        # Wert zurueck, den der Cookie schon hat. Ein `Set-Cookie` je Ereignis
        # ist der Preis; ihn per Vergleich zu sparen hiesse, den gemerkten Stand
        # zusaetzlich zu lesen, um ihn nicht zu schreiben.
        _merke_jobs_view(resp, v)
        return resp

    # ── Journal: dasselbe Material, andere Sektion (#38) ────────────────────
    #
    # **Ein Umzug, kein neuer Screen.** Der gestrichene Archive-Tab (#130)
    # führte *Läufe* aller Jobs nach Zeit; dieser führt *Jobs*, je Slug
    # aggregiert, und beantwortet damit eine andere Frage. `archive_page_v5`,
    # `/-/archive` und die zwei bibi4-Altrenderer bleiben gelöscht —
    # `test_job_detail.py::test_no_archive_renderer_is_left_anywhere` hält
    # das fest.
    #
    # Die Beschaffung ist Zeile für Zeile dieselbe wie bei `/-/jobs`: es sind
    # dieselben Zeilen, nur eine andere Auswahl daraus. Getrennt zu beschaffen
    # hieße, zwei Wege zu derselben Klassifikation zu führen — genau die
    # Doppelung, an der der alte Archive-Screen zerbrochen ist.
    #
    # **Der Pfad liegt unter `/-/jobs/`, nicht auf `/-/journal`** — das ist die
    # Journal-API des Schedulers (`daemon/app.py`), und beide Rollen laufen auf
    # sarasate in derselben App. Aufgefallen ist es an
    # `test_no_static_route_is_shadowed_by_an_earlier_placeholder`, allerdings
    # nur für `/-/journal/list` gegen `/-/journal/{jid}`: die Kollision der
    # Screen-Route mit der API auf demselben Pfad war identisch und blieb
    # stumm. Deshalb steht seit #38 eine zweite Invariante daneben, die
    # doppelt vergebene Pfade meldet.
    #
    # ACHTUNG, Reihenfolge: beide MÜSSEN vor `/-/jobs/{job_uid}` und dessen
    # Unterpfaden stehen — sonst schluckt der Platzhalter sie, wie er es bis
    # `#151` schon mit `/-/jobs/list` getan hat.

    @app.get("/-/jobs/journal", include_in_schema=False)
    def screen_journal(request: Request, sort: str | None = None,
                       dir: str | None = None):
        """Jobs, die nur noch Historie haben — das dritte Segment von Jobs."""
        from bibi import config
        _sched = _scheduler_status()
        zeilen, v, jetzt = _jobs_zeilen_und_view(request, sort, dir)
        resp = HTMLResponse(render.journal_page_v5(
            zeilen, now=jetzt, daemon_status=_status(),
            git_status=_feed_git_status(), host_url=_scheduler_url(),
            scheduler=_sched[0], scheduler_stale_since=_sched[1],
            typ=v["typ"], status=v["status"], journal=v["journal"],
            sort=v["sort"], direction=v["direction"], group=v["group"],
            public_host=config.public_host()))
        _merke_jobs_view(resp, v)
        return resp

    @app.get("/-/jobs/journal/list", include_in_schema=False)
    def screen_journal_list(request: Request, sort: str | None = None,
                            dir: str | None = None):
        """Nur die Liste — das Nachlade-Ziel des Bus."""
        from bibi import config
        zeilen, v, jetzt = _jobs_zeilen_und_view(request, sort, dir)
        resp = HTMLResponse(render.journal_list_fragment(
            zeilen, jetzt, typ=v["typ"], status=v["status"],
            journal=v["journal"], sort=v["sort"], direction=v["direction"],
            group=v["group"], public_host=config.public_host()))
        _merke_jobs_view(resp, v)
        return resp

    @app.get("/-/jobs/{job_uid}", include_in_schema=False)
    def screen_job_detail(request: Request, job_uid: str,  # noqa: ARG001
                          days: int = _RUN_TAGE, status: str = "", src: str = ""):
        """Ein Job: oben die Kacheln, unten **eine** Lauf-Liste (FE §5).

        Die URL trägt den ``job_uid``; der Weg zurück zum Slug läuft über
        ``jobs_view.slug_for()`` — md5 ist nicht umkehrbar, also wird über die
        bekannten Slugs gesucht.
        """
        import time as _t

        from bibi import config
        from bibi.controller import jobs_view
        slug = jobs_view.slug_for(job_uid, _slug_kandidaten())
        if slug is None:
            return JSONResponse(status_code=404,
                                content={"error": "job not found", "job_uid": job_uid})
        spec: dict = {}
        for e in _local_job_mds():
            if e.get("slug") == slug:
                spec = e
                break
        _sched = _scheduler_status()
        jetzt = _t.time()
        liste, reach, weiter = _job_lauf_liste(slug, now=jetzt, days=days,
                                               status=status, src=src)
        return HTMLResponse(render.job_detail_page_v5(
            slug=slug, spec=spec, now=jetzt, liste=liste,
            days=days, reach=reach, weiter=weiter,
            aktiv={"status": _mehrfach(status), "src": _mehrfach(src), "days": days},
            daemon_status=_status(),
            git_status=_feed_git_status(), host_url=_scheduler_url(),
            scheduler=_sched[0], scheduler_stale_since=_sched[1],
            # Der App-Link zeigt sonst auf den Rechner des Betrachters, sobald
            # das FE aus dem Tailnet aufgerufen wird (m.rau/bibi#145).
            public_host=config.public_host()))

    def _mehrfach(wert: str) -> list[str]:
        """``complete,error`` → ``["complete", "error"]``. Leer heißt „alle"."""
        return [t for t in (wert or "").split(",") if t]

    def _job_lauf_liste(slug: str, *, now: float, days: int = _RUN_TAGE,
                        status: str = "", src: str = ""):
        """Slots und Läufe beider Quellen für ``build_run_list()`` beschaffen.

        Nur Beschaffung — welche Kachel es gibt, welche Knöpfe sie trägt und
        wie die Liste zusammenläuft, entscheidet die reine Funktion.

        **Das Limit ist die echte Grenze der Liste** (``jobs_view.RUN_LIMIT``).
        Ein zeitbasiertes Pruning gibt es nicht — der gestrichene
        Archive-Screen behauptete mit ``pruned after 3 months`` eine Schranke,
        die nie existierte (m.rau/bibi#130). Diese hier existiert, und die
        Reichweiten-Angabe nennt sie genau dann, wenn die Liste an sie stößt.
        """
        from bibi.controller import jobs_view
        grenze = jobs_view.RUN_LIMIT
        sched_slot = None
        for e in _host_schedules():
            if e.get("slug") == slug:
                sched_slot = e
                break
        # Ein Oneshot laeuft nie lokal (FE §5.1.1) — dann gibt es dort auch
        # keinen Platz zu bedienen. Die Quelle ist der Scheduler-Eintrag; die
        # MD sagt dasselbe ueber `at`, aber sie fehlt bei einem geloeschten Job.
        einmalig = bool((sched_slot or {}).get("oneshot")) or any(
            md.get("slug") == slug and md.get("at") for md in _local_job_mds())
        # Der Port fuer die Kacheln (m.rau/bibi#104): aus der MD, ersatzweise
        # aus dem Scheduler-Slot — ein geloeschter Job hat keine MD mehr, seine
        # Kachel aber weiterhin einen Knoten und damit eine gueltige Adresse.
        app_port = next((md.get("app_port") for md in _local_job_mds()
                         if md.get("slug") == slug and md.get("app_port")),
                        None) or (sched_slot or {}).get("app_port")
        lokal_slot = None
        sched_runs: list = []
        lokal_runs: list = []
        try:
            # `_host_client()`, nicht `client`: letzterer zeigt auf den eigenen
            # Daemon, und dann stünden in der SCHEDULER-Spalte die lokalen
            # Läufe — beide Quellen zeigten dasselbe. Derselbe Fehler wie beim
            # Bus und beim Jobs-Screen, zum dritten Mal (Befund 2026-08-03).
            sched_runs = _host_client().journal(slug=slug, limit=grenze) or []
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        try:
            from bibi.daemon import job_db
            from bibi.daemon import worker as worker_mod
            # **Der Client-Slot ist der `/run`-Lauf, nicht die Schedule-Zeile.**
            # Live gefunden (2026-08-04): `burndown-app` lief seit dem Vortag
            # lokal, und der Screen zeigte weder Kachel noch Zeile noch Zaehlung
            # — `bibi-ctrl run` legt seine Zeile unter `<slug>-<token>` an
            # (`run_pinned()`), gesucht wurde aber der Basis-Slug. Was dort lag,
            # war eine rescan-erzeugte Zeile: auf diesem Mac alle am 2026-07-31
            # zuletzt angefasst, seither eingefroren, ein Rest aus der Zeit, als
            # er selbst Scheduler war. Die Kachel zeigte damit eine
            # Karteileiche samt `next`-Termin, den niemand einloest.
            #
            # Die Basis-Slug-Zeile bleibt der Rueckfall, und zwar als *freier
            # Platz* (`idle`), nicht als Zustand: ohne sie haette ein Client, auf
            # dem noch nie etwas lief, gar keine Kachel — und damit keinen Weg,
            # den Job hier zu starten (FE §5.1.1: eine Kachel fehlt nur, wenn es
            # *keinen Platz* gibt, nicht wenn er leer ist).
            gepinnt = worker_mod._pinned_last_row(slug)
            conn = job_db.connect()
            try:
                if gepinnt is not None:
                    lokal_slot = dict(gepinnt)
                else:
                    zeile = conn.execute(
                        "SELECT * FROM jobs WHERE slug=?", (slug,)).fetchone()
                    lokal_slot = dict(zeile) if zeile else None
                lokal_runs = job_db.list_journal(conn, slug=slug, limit=grenze)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            pass
        gekappt = grenze if (len(sched_runs) >= grenze or len(lokal_runs) >= grenze) else 0
        liste = jobs_view.build_run_list(
            scheduler_slot=sched_slot, client_slot=lokal_slot,
            scheduler_runs=sched_runs, client_runs=lokal_runs, now=now,
            scheduler_total=len(sched_runs), client_total=len(lokal_runs),
            scheduler_host=(_scheduler_url() or "").split("//")[-1].split(":")[0] or None,
            client_host=_status().get("host"), oneshot=einmalig,
            # Der Port gehoert an die Kacheln, weil erst ihr `host` daneben
            # eine Adresse ergibt (m.rau/bibi#104). Aus derselben Quelle wie
            # `einmalig` oben — die Funktion kennt nur den Slug, nicht das Spec.
            app_port=app_port,
            # Der Platz ohne Zeile (m.rau/bibi#87): kennt dieser Knoten die MD,
            # kann der Job hier laufen — auch wenn ihm nie jemand eine Zeile
            # angelegt hat. Nur die MD, nicht der Scheduler-Eintrag: was dort
            # steht, sagt nichts darueber, ob es HIER laufen kann.
            client_slug=slug if any(md.get("slug") == slug
                                    for md in _local_job_mds()) else None,
            # Ohne erreichbaren Host ist sein Slot unbekannt, nicht leer — die
            # Kachel bleibt und sagt es (m.rau/bibi#146).
            scheduler_offline=not _scheduler_status()[0])
        gesamt = len(liste.runs)
        # Erst das Fenster, dann die Filter: `LOAD MORE` verbreitert die
        # Reichweite und darf nicht davon abhängen, was gerade ausgeblendet ist.
        weiter = jobs_view.naechstes_fenster(liste.runs, aktuell=days, jetzt=now)
        liste.runs = jobs_view.im_fenster(liste.runs, tage=days, jetzt=now)
        liste.runs = jobs_view.gefiltert(liste.runs, status=_mehrfach(status),
                                         src=_mehrfach(src))
        return liste, {"total": gesamt, "days": days, "capped": gekappt}, weiter

    #: Die drei Verben aus der Slot-Kachel. `client` wirkt auf den eigenen
    #: Daemon, `scheduler` auf den Host — dieselbe Job-ID meint auf beiden
    #: Seiten einen anderen Job, weil beide ihre eigene DB fuehren
    #: (Zustandsmodell §1). Ohne diese Unterscheidung traefe ein Klick auf der
    #: Scheduler-Kachel den lokalen Job.
    _VERBEN = {"start", "reset", "kill", "rebuild"}

    def _verb_fehler(e: Exception, verb: str, ziel: str) -> JSONResponse:
        """Den Fehler weitergeben, wie er kam.

        Ein `409` des Hosts erreichte den Klickenden bisher als
        `502 HTTP Error 409: Conflict` — die Route verpackte jeden Fehler
        gleich (Befund m.rau, 2026-08-04). „Bad Gateway" fuer einen Konflikt
        ist eine Falschaussage, und der Knopf zeigt sie im `alert()`.

        Der Unterschied ist der zwischen **fremder Absage** und **eigenem
        Scheitern**: hat der Host geantwortet, ist sein Status die Aussage und
        der Controller nur Bote. Kam gar keine Antwort, ist `502` richtig —
        dann haengt es tatsaechlich hier.

        Der Text kommt ebenfalls vom Host, wenn er einen mitschickt: „job is
        running" sagt, was zu tun ist; „HTTP Error 409: Conflict" nennt nur die
        Nummer noch einmal.
        """
        import urllib.error
        text, code = str(e), 502
        if isinstance(e, urllib.error.HTTPError):
            code = e.code
            try:
                rumpf = json.loads(e.read() or b"null")
                if isinstance(rumpf, dict):
                    text = str(rumpf.get("error") or rumpf.get("detail") or text)
            except Exception:  # noqa: BLE001 — defensiv (§2.7): kein JSON, kein Rumpf
                pass
        activity.emit(log, logging.WARNING, "controller.verb_failed",
                      f"{verb.upper()} auf {ziel} fehlgeschlagen: {text}",
                      role="controller")
        return JSONResponse(status_code=code,
                            content={"error": text, "verb": verb, "ziel": ziel})

    # ── Ops-Handles der App-Bar, ueber den Controller (m.rau/bibi#142) ──────
    #
    # **Befund m.rau, 2026-08-05:** „Das Refresh funktioniert gar nicht, oder?
    # In keinem Screen!?" — beide Handles riefen relativ auf und trafen damit
    # den eigenen Client statt den Scheduler. Gemessen:
    #
    #     POST 127.0.0.1:54824/-/rescan       → 404  (Route haengt an `scheduler`)
    #     POST 127.0.0.1:54824/-/maintenance  → 200  {"maintenance":true}  ← lokal
    #
    # Der Rescan lief ins Leere, die Maintenance-Umschaltung wirkte am falschen
    # Knoten. FE-Spezifikation §2 verlangt fuer beide den Scheduler.
    #
    # **Warum der Umweg ueber den Controller und nicht absolut aus dem Browser:**
    # der Scheduler sendet keine CORS-Header (gemessen am 2026-08-05), ein
    # Cross-Origin-POST scheiterte also. Derselbe Grund und dasselbe Muster wie
    # bei den Job-Verben unten.
    #
    # **Auf einem Knoten ohne konfigurierten Scheduler bleibt es lokal** — dort
    # *ist* dieser Daemon der Scheduler, und der relative Aufruf war nie falsch.

    def _ops_ziel() -> ControllerClient:
        """Der Knoten, der die Ops-Aktion ausfuehrt.

        Der Scheduler, wenn einer konfiguriert ist — sonst **dieser Daemon
        selbst** ueber ``127.0.0.1``. Der zweite Fall ist der Host: dort *ist*
        dieser Prozess der Scheduler, und der frueher relative Aufruf war nie
        falsch. Er wird hier nur explizit statt implizit.

        **Der Selbstaufruf blockiert nicht:** die Ziel-Routen sind ``def``,
        nicht ``async def``, laufen also im Threadpool und nicht auf dem
        Event-Loop, der diese Anfrage haelt.
        """
        url = _scheduler_url()
        if url:
            return ControllerClient(url)
        from bibi import config as config_mod
        return ControllerClient(f"http://127.0.0.1:{config_mod.daemon_port()}")

    @app.get("/-/ui/jobs/{job_id}/output/stream", include_in_schema=False)
    def output_proxy(job_id: str, from_: int = Query(0, alias="from"),
                     last_event_id: str | None = Header(default=None)):
        """Der Output eines **Scheduler**-Laufs, durchgereicht (#78).

        **Der Kanal existiert seit dem 2026-07-20**, und er kann alles, was
        gebraucht wird: ``/-/job/{id}/output/stream`` waechst zur Laufzeit,
        sendet ``event: done`` als eindeutiges Ende und traegt ``id:``-Zeilen
        fuer lueckenloses Wiederaufsetzen. Genutzt wurde er nicht, weil PLAN-36
        Stufe 36.2 die Output-EventSource abgeschafft hat — *„es habe sie auf
        Client-Knoten gar nicht gegeben"*. Richtig beobachtet, falsch
        geschlossen: sie war auf den **eigenen** Knoten gerichtet.

        **Was fehlte, ist genau diese Route.** Der Browser kann den Scheduler
        nicht direkt ansprechen (keine CORS-Header, dieselbe Lage, aus der
        ``_ops_ziel()`` entstand). Sie ist deshalb ein Durchreicher und nichts
        weiter: lesen, weiterschreiben, ``event: done`` mitnehmen. Offsets,
        Wiederaufsetzen und das eindeutige Ende sind gebaut — sie hier neu zu
        erfinden hiesse, zwei Fassungen derselben Mechanik zu pflegen.

        ``404`` auf einem Knoten mit **Scheduler-Rolle**: dort ist der eigene
        Strom (``/-/job/{id}/output/stream``) der richtige, der Umweg ueber
        sich selbst waere keiner. Bis zum 2026-08-09 stand hier stattdessen
        „ohne konfigurierten Scheduler" — dieselbe Verwechslung von Adresse und
        Rolle wie in ``_output_stream_url()`` (m.rau/bibi#86), und aus
        demselben Grund korrigiert. Ohne Adresse gibt es ohnehin nichts
        durchzureichen; auch das bleibt ``404``.

        **Nur fuer laufende Laeufe.** Ein terminaler braucht keinen Strom, dort
        bleibt der einmalige Abruf richtig (Klarstellung m.rau: *„Bei
        terminalen Laeufen nicht, ich weiss."*); die Box bekommt ihre
        ``data-stream``-Angabe deshalb nur im ``running``-Zweig von
        ``_live_panel()``.
        """
        global _output_proxies
        if roles.scheduler:
            return PlainTextResponse("", status_code=404)
        url = _scheduler_url()
        if not url:
            return PlainTextResponse("", status_code=404)
        # Last-Event-ID hat Vorrang vor `from` — dieselbe Reihenfolge wie in
        # `job_output_stream()` beim Scheduler, und aus demselben Grund: den
        # Header schickt der Browser bei jedem automatischen Reconnect selbst
        # mit, der Query-Parameter ist einmalig eingefroren.
        if last_event_id is not None:
            try:
                from_ = int(last_event_id)
            except ValueError:
                pass
        with _output_proxy_lock:
            if _output_proxies >= _MAX_OUTPUT_PROXIES:
                activity.emit(log, logging.WARNING, "controller.output_proxy_limit",
                              "Output-Durchreicher am Limit — Box bleibt beim Seed",
                              role="controller", job_id=job_id,
                              offen=str(_output_proxies))
                return PlainTextResponse("too many open output streams",
                                         status_code=429)
            _output_proxies += 1

        import urllib.request
        ziel = (f"{url.rstrip('/')}/-/job/{urllib.parse.quote(job_id, safe='')}"
                f"/output/stream?from={from_}")
        req = urllib.request.Request(ziel, headers={"Accept": "text/event-stream"})
        try:
            from bibi import config as config_mod
            req.add_header("X-Bibi-Node-Id", config_mod.node_id())
        except Exception:  # noqa: BLE001 — ohne Identitaet wie bisher
            pass

        def gen():
            global _output_proxies
            try:
                with urllib.request.urlopen(req, timeout=_PROXY_READ_TICK_S) as resp:  # noqa: S310
                    while True:
                        try:
                            zeile = resp.readline()
                        except TimeoutError:
                            # Laenger stumm, als der Output-Strom je sein darf
                            # (er pingt alle 15 s) — die Gegenseite ist weg.
                            # **Nicht** weiterlesen: nach einem socket.timeout
                            # ist der Stream unbrauchbar, und ein `continue`
                            # hier war genau der Fehler, den v0.7.7 behebt.
                            return
                        except (OSError, ValueError):
                            return
                        if not zeile:
                            return
                        yield zeile
            except Exception as exc:  # noqa: BLE001 — der Host darf ausfallen (§2.7)
                log.debug("Output-Durchreicher fuer %s beendet: %s", job_id, exc)
            finally:
                with _output_proxy_lock:
                    _output_proxies -= 1

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/-/ui/ops/rescan", include_in_schema=False)
    def screen_ops_rescan():
        """``⟳`` — Rescan **auf dem Scheduler**.

        Gibt die echte Antwort weiter (``inserted``/``updated``/``removed``),
        damit der Knopf Erfolg nicht behaupten muss. Der Fehlerfall kommt als
        ``502`` mit Text an, statt still zu verschwinden.
        """
        try:
            antwort = _ops_ziel().rescan()
        except Exception as e:  # noqa: BLE001 — die Meldung gehoert an den Knopf
            activity.emit(log, logging.WARNING, "controller.rescan_failed",
                          f"Rescan fehlgeschlagen: {e}", role="controller")
            return JSONResponse(status_code=502, content={"error": str(e)})
        return {"ok": True, "antwort": antwort}

    @app.post("/-/ui/ops/maintenance", include_in_schema=False)
    @app.delete("/-/ui/ops/maintenance", include_in_schema=False)
    def screen_ops_maintenance(request: Request):
        """``◐`` — Maintenance **auf dem Scheduler** an/aus.

        Der wirksamere der beiden Faelle: er schaltete bisher den lokalen Modus
        eines Clients, der gar keine Jobs verteilt — er tat also etwas, nur am
        falschen Knoten, und das ist schwerer zu bemerken als ein Knopf, der
        gar nichts tut.
        """
        an = request.method == "POST"
        try:
            antwort = _ops_ziel()._request(
                "POST" if an else "DELETE", "/-/maintenance") or {}
        except Exception as e:  # noqa: BLE001
            activity.emit(log, logging.WARNING, "controller.maintenance_failed",
                          f"Maintenance-Umschaltung fehlgeschlagen: {e}",
                          role="controller")
            return JSONResponse(status_code=502, content={"error": str(e)})
        return antwort

    @app.post("/-/ui/jobs/verb/{ziel}/{job_id}/{verb}", include_in_schema=False)
    def screen_job_verb(ziel: str, job_id: str, verb: str):
        """START, RESET, KILL oder REBUILD auf einem Slot ausloesen.

        Der Umweg ueber den Controller ist noetig, weil der Browser nicht
        weiss, wo der Job liegt: der Scheduler-Slot lebt auf dem Host, der
        Client-Slot hier. Beide Seiten haben dieselbe Route
        (`POST /-/job/{id}/{verb}`), nur auf verschiedenen Maschinen.

        **REBUILD nimmt auf dem Client einen anderen Weg, und das ist gemessen,
        nicht angenommen:** `POST /-/job/{id}/rebuild` gibt es dort gar nicht
        (`{"detail":"Not Found"}` — die Route haengt an der `worker`-Rolle, die
        ein reiner Client nicht traegt), waehrend `POST /-/run/live/{slug}/
        rebuild` antwortet. Letztere schlaegt den Exec-Mode ueber die
        Schedule-MD nach, nicht ueber die DB, und braucht deshalb den Slug —
        den holen wir hier aus der Job-Zeile, statt ihn durch den Browser zu
        schleifen: eine Angabe weniger im Markup, die falsch sein kann.
        """
        if verb not in _VERBEN:
            return JSONResponse(status_code=400,
                                content={"error": "unknown verb", "verb": verb})
        if ziel == "client":
            slug = _bucket_slug_of(job_id)
            if slug is None and any(md.get("slug") == job_id
                                    for md in _local_job_mds()):
                # **Die Kachel ohne Zeile** (m.rau/bibi#87). Sie traegt den Slug
                # als Kennung, weil es keine Job-ID gibt — auf einem reinen
                # Client legt niemand eine Basis-Zeile an (der Rescanner haengt
                # an der `scheduler`-Rolle). Der Weg dahinter ist derselbe:
                # `_client_verb()` nimmt ohnehin einen Slug, die ID war immer
                # nur ein Umweg, um an ihn zu kommen.
                #
                # Nur fuer **lokal bekannte** MDs, nicht fuer jede Zeichenkette:
                # sonst waere die Route ein Weg, beliebige Slugs zu starten.
                slug = job_id
            if slug is None:
                return JSONResponse(status_code=404,
                                    content={"error": "job not found", "id": job_id})
            try:
                antwort = _client_verb(verb, slug)
            except Exception as e:  # noqa: BLE001 — die Meldung gehoert an den Knopf
                return _verb_fehler(e, verb, ziel)
            return {"ok": True, "verb": verb, "ziel": ziel, "id": job_id,
                    "antwort": antwort}
        if ziel == "scheduler":
            url = _scheduler_url()
            if not url:
                return JSONResponse(status_code=503,
                                    content={"error": "no scheduler configured"})
            ziel_client = ControllerClient(url)
        else:
            return JSONResponse(status_code=400,
                                content={"error": "unknown target", "ziel": ziel})
        try:
            antwort = ziel_client.job_action(job_id, verb)
        except Exception as e:  # noqa: BLE001 — die Meldung gehoert an den Knopf
            return _verb_fehler(e, verb, ziel)
        return {"ok": True, "verb": verb, "ziel": ziel, "id": job_id,
                "antwort": antwort}

    def _bucket_slug_of(job_id: str) -> str | None:
        """Der Slug, unter dem die Schedule-MD diesen Job kennt.

        Ein `/run`-Lauf traegt einen eigenen, pro Aufruf eindeutigen Slug
        (`<bucket>-<token>`, `run_pinned()`); die Routen, die ihn bedienen,
        erwarten aber den **Bucket**-Slug — sie schlagen ueber die MD nach.
        Ohne die Rueckfuehrung triebe jeder Klick einen Lauf auf einem Slug an,
        den keine MD kennt. Aus der Zeile gelesen, nicht durchs Markup
        geschleift: eine Angabe weniger, die falsch sein kann.
        """
        from bibi.daemon import job_db
        from bibi.daemon.bus import bucket_slug
        conn = job_db.connect()
        try:
            zeile = conn.execute(
                "SELECT slug, pinned_host FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        if zeile is None:
            return None
        return bucket_slug(zeile["slug"], zeile["pinned_host"]) or zeile["slug"]

    def _client_verb(verb: str, slug: str) -> dict:
        """Die vier Verben auf **diesem** Knoten (m.rau/bibi#135).

        **Alle vier nehmen einen anderen Weg als beim Scheduler, und das ist
        gemessen, nicht angenommen** (Testclient auf :65200, 2026-08-04):
        `POST /-/job/{id}/start|reset|kill` antwortet dort `501 not
        implemented` — die Job-Verb-Routen sind ohne `scheduler`-Rolle Stubs;
        `POST /-/job/{id}/rebuild` gibt es gar nicht (`{"detail":"Not
        Found"}`, die Route haengt an der `worker`-Rolle). Was antwortet, sind
        die slug-basierten `/-/run`- und `/-/run/live/*`-Routen.

        Der Client-Slot ist damit ein **echter Slot mit vollem Lebenszyklus**,
        dem nur der Trigger fehlt (Entscheidung m.rau): `failed`/`deferred`
        verdienen auch hier einen Retry — aber ausgeloest von einem Menschen
        per START, nicht von einem Dispatcher.
        """
        if verb == "start":
            # `/-/run` legt eine echte gepinnte Zeile an und dispatcht sofort;
            # laeuft schon einer, antwortet die Route `409 already running` —
            # dieselbe Aussage, die das Zustandsmodell fuer START auf `running`
            # trifft (der Knopf ist dort ohnehin tot).
            return client.run(slug=slug)
        if verb == "kill":
            return client.run_live_kill(slug)
        if verb == "reset":
            return client.run_live_reset(slug)
        return client.run_rebuild(slug)

    def _host_slot_laeuft(job_id: str) -> bool:
        """Laeuft dieser Lauf beim Scheduler noch (#124)?

        `/-/job/{id}/output` traegt die Antwort nicht — es liefert `events` und
        `kind`, sonst nichts. Der Zustand kommt deshalb aus der Job-Liste des
        Hosts, und zwar **defensiv**: faellt der Aufruf aus, gilt der Lauf als
        nicht laufend und der Bereich bekommt sein Standbild. Ein Standbild ist
        eine Verschlechterung, ein Strom ins Leere waere ein Fehler.
        """
        try:
            for j in _host_client().jobs() or []:
                if str(j.get("id")) == str(job_id):
                    return j.get("status") in _LEBENDE_ZUSTAENDE
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            return False
        return False

    @app.get("/-/jobs/{job_uid}/slot/{ziel}/{job_id}/output", include_in_schema=False)
    def screen_job_slot_output(request: Request, job_uid: str,  # noqa: ARG001
                               ziel: str, job_id: str):
        """Die Ausgabe des Laufs, der **noch im Slot steht** (m.rau/bibi#131).

        Er hat keine Journal-Zeile: unter A2 entsteht die erst auf START/RESET,
        und ein laufender Lauf hat dort ohnehin nichts. Ohne diesen Weg wäre
        ausgerechnet der laufende Job der einzige, dessen Ausgabe niemand öffnen
        kann — und die Liste zeigte eine Zeile mit einem ``show``, das ins Leere
        greift.

        ``ziel`` sagt, **wo** der Slot liegt: dieselbe Job-ID meint auf beiden
        Seiten einen anderen Job, weil beide ihre eigene DB führen
        (Zustandsmodell §1). Dieselbe Unterscheidung wie bei den drei Verben.
        """
        if _job_by_uid(job_uid) is None:
            return PlainTextResponse("", status_code=404)
        if ziel == "scheduler":
            try:
                antwort = _host_client().job_output(job_id) or {}
            except Exception:  # noqa: BLE001 — defensiv (§2.7)
                antwort = {}
            ereignisse = antwort.get("events") or []
            if not ereignisse:
                return PlainTextResponse("", status_code=404)
            # Der Host formatiert bereits (`app.py` ruft `format_events`);
            # zusammengefuegt und ausgezeichnet wird hier — dieselbe
            # Darstellung wie fuer einen lokalen Lauf (m.rau/bibi#99).
            #
            # **Ein laufender Lauf bekommt einen Strom, kein Standbild** (#124).
            # Bis hierher lieferte diese Route ausschliesslich `output_block()`,
            # und der aufgeklappte Bereich stand still, bis jemand zu- und
            # wieder aufklappte.
            #
            # Der Zustand steht nicht in der Antwort — `/-/job/{id}/output`
            # traegt `events` und `kind`, sonst nichts. Er wird deshalb eigens
            # geholt: ein Aufruf **pro Klick**, nicht pro Tick, und damit kein
            # Beitrag zum Grundrauschen.
            if _host_slot_laeuft(job_id):
                return HTMLResponse(render.live_output_box(
                    str(job_id), ereignisse,
                    kind=antwort.get("kind") or "job",
                    # **Nicht nachbauen, rufen.** Hier stand ein von Hand
                    # zusammengesetzter Pfad, und er war zweifach falsch: die
                    # Route heisst `/-/ui/jobs/{id}/output/stream`, und auf
                    # einem Knoten mit eigener Scheduler-Rolle darf ueberhaupt
                    # kein Durchreicher stehen — die Box waechst dort ueber den
                    # globalen Bus (#86). Beides entscheidet
                    # `_output_stream_url()` seit jeher; es nachzubauen war
                    # genau die Fehlerform, gegen die dieses Ticket antritt.
                    stream_url=_output_stream_url({"status": "running",
                                                   "id": job_id})))
            return HTMLResponse(render.output_block(
                ereignisse, antwort.get("kind") or "job"))
        from bibi.daemon import job_db
        conn = job_db.connect()
        try:
            zeile = conn.execute(
                # `status` seit #124 — der Zweig unten entscheidet daran, ob
                # der Lauf noch laeuft. `payload` stand hier ebenfalls nicht,
                # obwohl `effective_kind()` es unten liest: der Typ war damit
                # immer der Default. Zwei Spalten, ein Griff.
                "SELECT id, slug, fire, output_ref, status, payload "
                "FROM jobs WHERE id=?",
                (job_id,)).fetchone()
        finally:
            conn.close()
        if zeile is None:
            return PlainTextResponse("", status_code=404)
        from bibi import repo as repo_mod
        from bibi.daemon.worker import output_path_of
        # **Erst der Verweis, dann die Neuberechnung** — hier stand nur der
        # Verweis, und der ist waehrend `running` immer NULL (der Wrapper fuellt
        # ihn beim Terminal-Report). Live gefunden 2026-08-04: `burndown-app`
        # lief seit einem Tag, 239 Zeilen Ausgabe lagen da, und genau die Zeile,
        # fuer die diese Route existiert, sagte `(no output yet)`.
        pfad = output_path_of(zeile, repo_mod.root())
        try:
            from bibi.wrapper import output as output_mod
            zeilen = output_mod.read_events(pfad)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            zeilen = []
        if not zeilen:
            # Kein Output ist eine Aussage, kein Fehler: ein Job kann
            # schweigend laufen — gerade am Anfang ist das der Normalfall.
            return PlainTextResponse("(no output yet)")
        # **Durch den Formatter, nicht daran vorbei** (m.rau/bibi#99). Hier
        # stand ein eigenes `"\n".join(...)` ueber die Roh-Events: jeder
        # Token-Delta wurde eine eigene Zeile, und im FE stand `Der Benut` /
        # `zer moechte` — Umbruch mitten im Wort, dazu das rohe Stream-JSON.
        # Alle drei Bausteine dagegen waren gebaut und wurden anderswo benutzt:
        # `format_events()` typisiert die Deltas (`s: "thinking"`),
        # `_merge_deltas()` fuegt sie zusammen, `_event_line()` setzt sie ab.
        # `output_block()` verbindet die drei — dieselbe Funktion, die der
        # Host-Screen seit jeher verwendet.
        from bibi.daemon import output_format as _of
        from bibi.schedule import models as _models
        # `zeile` ist je nach Pfad ein `sqlite3.Row` — der kennt kein `.get()`.
        _row = dict(zeile) if zeile is not None else {}
        _kind = _models.effective_kind(_row.get("payload"))
        ereignisse = _of.format_events(zeilen, _kind)
        # Lokaler Lauf: seine `output.jsonl` liegt hier, der globale Bus speist
        # die Box. Kein `stream_url` noetig — derselbe Grund wie im Zweig
        # darueber (#124), andere Quelle.
        if _row.get("status") in _LEBENDE_ZUSTAENDE:
            return HTMLResponse(render.live_output_box(
                str(job_id), ereignisse, kind=_kind))
        return HTMLResponse(render.output_block(ereignisse, _kind))

    @app.get("/-/jobs/{job_uid}/runs/{jid}/attrs", include_in_schema=False)
    def screen_run_attrs(request: Request, job_uid: str, jid: int):  # noqa: ARG001
        """Die Attribute genau dieses Laufs (#40) — die mittlere der drei
        Schichten, die es bisher nur abgelegt und nie sichtbar gab.

        **Beide Quellen, wie beim Output.** Die Journal-IDs der beiden Seiten
        sind verschiedene Zähler; wer nur lokal sucht, antwortet für fast jeden
        Lauf des Screens mit 404.
        """
        import time as _t

        treffer = _job_by_uid(job_uid)
        if treffer is None:
            return JSONResponse(status_code=404,
                                content={"error": "job not found", "job_uid": job_uid})
        slug, _ = treffer
        from bibi.daemon import job_db
        conn = job_db.connect()
        try:
            zeile = job_db.get_journal(conn, jid)
        finally:
            conn.close()
        if not zeile:
            try:
                zeile = _host_client().journal_entry(jid) or {}
            except Exception:  # noqa: BLE001 — defensiv (§2.7)
                zeile = {}
        if not zeile:
            return JSONResponse(status_code=404,
                                content={"error": "run not found", "id": jid})
        _sched = _scheduler_status()
        spec = _local_schedules().get(slug) or {}
        return HTMLResponse(render.run_attrs_page_v5(
            slug=slug, lauf=zeile, job_spec=spec,
            # Dieselben Vorgabewerte wie auf der Job-Seite (#132) — sie stehen
            # in einer Funktion, damit die beiden Seiten nicht zwei Meinungen
            # darüber bekommen, was ein Default ist.
            defaults=_vorgabewerte(spec),
            now=_t.time(), daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(), scheduler=_sched[0],
            scheduler_stale_since=_sched[1]))

    @app.get("/-/jobs/{job_uid}/runs/{jid}/output", include_in_schema=False)
    def screen_job_run_output(request: Request, job_uid: str, jid: int):  # noqa: ARG001
        """Die Ausgabe eines archivierten Laufs (FE-Spezifikation §5.4).

        **Es gibt keinen eigenen Lauf-Screen** — die Zeile klappt auf, und der
        Bereich holt sich seinen Inhalt hier. Die Liste trägt ihn nicht mit:
        sonst wäre jede Seite so groß wie alle Ausgaben zusammen, und
        `gmail-transfer` allein hat 1064 Läufe im Fenster.
        """
        if _job_by_uid(job_uid) is None:
            return PlainTextResponse("", status_code=404)
        from bibi.daemon import job_db
        conn = job_db.connect()
        try:
            zeile = job_db.get_journal(conn, jid)
        finally:
            conn.close()
        if zeile is None:
            # Nicht lokal: dann lief er drueben. Die Journal-IDs der beiden
            # Seiten sind verschiedene Zaehler (live: lokal bis 2224, beim
            # Scheduler bis 23611) — wer nur lokal sucht, antwortet fuer fast
            # jeden Lauf des Screens mit 404. Beide Seiten sind eigenstaendig
            # (Zustandsmodell §1); zusammengefuehrt wird in der Anzeige, und
            # dazu gehoert, den Output dort zu holen, wo er liegt.
            try:
                antwort = _host_client().run_output(jid) or {}
            except Exception:  # noqa: BLE001 — defensiv (§2.7)
                antwort = {}
            ereignisse = antwort.get("events") or []
            if not ereignisse:
                return PlainTextResponse("", status_code=404)
            # Der Host formatiert bereits (`app.py` ruft `format_events`);
            # zusammengefuegt und ausgezeichnet wird hier — dieselbe
            # Darstellung wie fuer einen lokalen Lauf (m.rau/bibi#99).
            return HTMLResponse(render.output_block(
                ereignisse, antwort.get("kind") or "job"))
        from bibi import repo as repo_mod
        pfad = repo_mod.root() / (zeile.get("output_ref") or "")
        try:
            from bibi.wrapper import output as output_mod
            zeilen = output_mod.read_events(pfad)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            zeilen = []
        if not zeilen:
            # Kein Output ist eine Aussage, kein Fehler: ein Job kann
            # schweigend durchlaufen.
            return PlainTextResponse("(no output)")
        # **Durch den Formatter, nicht daran vorbei** (m.rau/bibi#99). Hier
        # stand ein eigenes `"\n".join(...)` ueber die Roh-Events: jeder
        # Token-Delta wurde eine eigene Zeile, und im FE stand `Der Benut` /
        # `zer moechte` — Umbruch mitten im Wort, dazu das rohe Stream-JSON.
        # Alle drei Bausteine dagegen waren gebaut und wurden anderswo benutzt:
        # `format_events()` typisiert die Deltas (`s: "thinking"`),
        # `_merge_deltas()` fuegt sie zusammen, `_event_line()` setzt sie ab.
        # `output_block()` verbindet die drei — dieselbe Funktion, die der
        # Host-Screen seit jeher verwendet.
        from bibi.daemon import output_format as _of
        from bibi.schedule import models as _models
        # `zeile` ist je nach Pfad ein `sqlite3.Row` — der kennt kein `.get()`.
        _row = dict(zeile) if zeile is not None else {}
        _kind = _models.effective_kind(_row.get("payload"))
        return HTMLResponse(render.output_block(
            _of.format_events(zeilen, _kind), _kind))

    @app.get("/-/jobs/{job_uid}/runs", include_in_schema=False)
    def screen_job_runs(request: Request, job_uid: str,  # noqa: ARG001
                        days: int = _RUN_TAGE, status: str = "", src: str = ""):
        """Nur die Liste — das Nachlade-Ziel des ``archived``-Ereignisses.

        **Ohne die Kacheln**, und das ist Absicht: sie tragen die Knöpfe, und
        ein Nachladen mitten im Klick nähme sie unter der Hand weg.
        """
        import time as _t

        treffer = _job_by_uid(job_uid)
        if treffer is None:
            return HTMLResponse("", status_code=404)
        slug, _ = treffer
        jetzt = _t.time()
        liste, reach, weiter = _job_lauf_liste(slug, now=jetzt, days=days,
                                               status=status, src=src)
        return HTMLResponse(render.job_runs_fragment(
            liste, now=jetzt, slug=slug, job_uid=job_uid, days=days, reach=reach, weiter=weiter,
            aktiv={"status": _mehrfach(status), "src": _mehrfach(src), "days": days}))

    @app.get("/-/jobs/{job_uid}/tiles", include_in_schema=False)
    def screen_job_tiles(request: Request, job_uid: str):  # noqa: ARG001
        """Nur die Kacheln — das Nachlade-Ziel des ``live:<slug>``-Ereignisses.

        **Mit den Knöpfen**, anders als es hier zwei Tage lang stand: der
        frühere Einwand („ein Nachladen nähme sie unter der Hand weg") galt der
        direkten Listener-Bindung in ``_SLOT_JS``. Seit die delegiert hört,
        kostet ein Swap sie nichts — und nur den Statustext zu tauschen wäre
        falsch, weil die möglichen Verben am Zustand hängen: eine Leiste im
        alten Stand böte START zu einem laufenden Job an (m.rau/bibi#152).
        """
        import time as _t

        treffer = _job_by_uid(job_uid)
        if treffer is None:
            return HTMLResponse("", status_code=404)
        slug, _ = treffer
        jetzt = _t.time()
        liste, _reach, _weiter = _job_lauf_liste(slug, now=jetzt)
        return HTMLResponse(render.job_tiles_fragment(
            getattr(liste, "tiles", []), now=jetzt, slug=slug, job_uid=job_uid))

    def _job_by_uid(job_uid: str):
        """``job_uid`` → (Slug, lokale Spec) oder ``None``.

        Gemeinsam für die Detailseite und ihre Unterseiten, damit beide
        dieselbe Auflösung benutzen und nicht auseinanderlaufen können.
        """
        from bibi.controller import jobs_view
        slug = jobs_view.slug_for(job_uid, _slug_kandidaten())
        if slug is None:
            return None
        for e in _local_job_mds():
            if e.get("slug") == slug:
                return slug, e
        return slug, {}

    def _vorgabewerte(spec: dict) -> dict:
        """Die Defaults, gegen die *„gesetzt oder geerbt"* entschieden wird.

        **Eine Funktion für beide Attributseiten** (#132): die Job-Seite und die
        Lauf-Seite beantworten dieselbe Teilfrage, und zwei Kopien dieser
        Tabelle wären zwei Meinungen darüber, was ein Vorgabewert ist.

        Der ``silence_timeout`` hängt am Typ — ein App-Job darf 48 h schweigen,
        ein Shell-Job nicht.
        """
        from bibi.schedule import models
        kind = "app" if spec.get("app_port") else "job"
        return {
            "attempts": 1, "backoff": "fixed",
            "defer_time": models.DEFAULT_DEFER_TIME,
            "defer_max": models.DEFAULT_DEFER_MAX,
            "silence_timeout": (models.DEFAULT_SILENCE_TIMEOUT_APP if kind == "app"
                                else models.DEFAULT_SILENCE_TIMEOUT_JOB),
        }

    @app.get("/-/jobs/{job_uid}/attrs", include_in_schema=False)
    def screen_job_attrs(request: Request, job_uid: str):  # noqa: ARG001
        """Alle Konfigurationswerte des Jobs (FE-Spezifikation §5.5)."""
        import time as _t

        treffer = _job_by_uid(job_uid)
        if treffer is None:
            return JSONResponse(status_code=404,
                                content={"error": "job not found", "job_uid": job_uid})
        slug, _ = treffer
        # Die **volle** Spec, nicht die schmale Zeilen-Sicht aus
        # `_local_job_mds()`: die trägt nur, was der Jobs-Screen braucht
        # (Slug, Trigger, Payload, Port). Hier geht es um jeden
        # Konfigurationswert.
        spec = _local_schedules().get(slug) or {}
        vorgabe = _vorgabewerte(spec)
        _sched = _scheduler_status()
        return HTMLResponse(render.job_attrs_page_v5(
            slug=slug, spec=spec, defaults=vorgabe, now=_t.time(),
            daemon_status=_status(), git_status=_feed_git_status(),
            host_url=_scheduler_url(), scheduler=_sched[0],
            scheduler_stale_since=_sched[1]))

    def _local_run_status() -> dict:
        """Letzter lokaler Lauf je Slug — die rechte Hälfte der Zeile.

        **Zwei Speicher, nicht einer** (Zustandsmodell §1). Das Journal trägt
        die archivierten Läufe, der Slot den laufenden — und nach A1/A2 ist ein
        laufender Lauf gerade *nicht* archiviert. Wer nur das Journal liest,
        zeigt für einen Job, der in diesem Moment lokal läuft, ``—``: er sieht
        aus wie einer, der nie lokal lief.

        Live gefunden am 2026-08-04 auf dem eben umgestellten Mac-Client:
        ``burndown-app`` lief seit einem Tag, Job Detail zeigte ``running ·
        1d 9h``, diese Spalte ``—``. Was sie stattdessen zeigte, war bei
        ``hitl-test-app`` ein ``killed`` vom 4. Juli.

        **Der Slot gewinnt gegen das Archiv**, ohne Zeitvergleich: stünde er
        nicht im Slot, wäre er archiviert. Seine Laufzeit muss hier entstehen,
        weil ``exec_runtime`` erst beim Archivieren geschrieben wird — die
        Zelle liest genau dieses Feld.
        """
        import time as _t

        try:
            eintraege = client.run_journal(limit=500)
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            eintraege = []
        aus = _local_run_status_aus(eintraege)
        try:
            live = client.run_live_list()
        except Exception:  # noqa: BLE001 — defensiv (§2.7)
            live = {}
        jetzt = _t.time()
        for slug, lauf in live.items():
            begonnen = lauf.get("started_at") or jetzt
            aus[slug] = {**lauf, "exec_runtime": max(0.0, jetzt - begonnen)}
        return aus

    @app.get("/-/nodes", include_in_schema=False)
    def screen_nodes():
        return clients_screen()

    @app.get("/-/live", include_in_schema=False)
    def screen_live():
        # Noch derselbe Inhalt wie `Log`. Die Trennung — Live ohne Gedächtnis
        # (SSE, ganze englische Sätze), Log mit Historie (HTTP, Paging, Details
        # auf DEBUG) — ist in FE-Spezifikation §7 ausgearbeitet und steht als
        # m.rau/bibi#109 am Board.
        #
        # **Hier stand bis zum 2026-08-09 „kommt in Bauschritt 4".** Diesen
        # Bauschritt gab es nirgends sonst: kein Ticket, kein Eintrag im Vault,
        # keine Fundstelle außer diesem Kommentar. Eine Absicht, die nur im Code
        # steht, sieht niemand, der das Board liest — sie hat keinen Termin,
        # keinen Zuschnitt und keinen Ort, an dem ihr Fehlen auffiele.
        #
        # Bis dahin ist der Tab erreichbar statt tot: ein 404 hinter einem
        # sichtbaren Tab ist die schlechtere Zwischenlösung.
        return logs_page()

    @app.get("/-/log", include_in_schema=False)
    def screen_log():
        return logs_page()
