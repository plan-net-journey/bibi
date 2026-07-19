"""Jobs-Screen (PLAN-17 Stufe 17.1/17.2, umgebaut PLAN-21 Befund 10): lokale
Repository-Realität + Git-Status + Start-Button + lokale Lauf-Historie.

Dient ausschließlich dem Review, was **lokal** im Repository liegt (User-
Entscheidung 2026-07-07: kein Remote-Abgleich mehr, kein Netzaufruf zum
Scheduler) — „Lokal" kommt aus einem read-only Discovery-Scan des Vaults,
der Git-Status je Datei aus ``local_files_status()``. Funktioniert auch auf
einem reinen Client (kein Scheduler/Worker im Ruhezustand). Rendering ist
hier pur getestet; die Route-Tests unten verdrahten einen gefakten
``ControllerClient`` (wie ``test_controller_daemon.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── Rendering ─────────────────────────────────────────────────────────────


def _row(slug: str, *, git_status: str = "clean", live: dict | None = None,
        payload: str = "echo x", app_port: int | None = None) -> dict:
    return {"slug": slug, "schedule": "now", "at": None, "payload": payload,
            "repo_path": f"vault/case/{slug}/README.md", "git_status": git_status,
            "live": live, "app_port": app_port}


def test_jobs_table_has_no_start_button():
    # PLAN-28 User-Feedback: "CTA START soll es hier gar nicht geben, das
    # gibt es nur auf der Detail Seite" — die Übersicht dient reinem Review.
    html = render._jobs_table([_row("mein-testjob")], {}, now=100.0)
    assert "startbtn" not in html
    assert "hx-post=" not in html


def test_jobs_table_header_includes_type_column():
    assert "<th>Type</th>" in render._jobs_table([_row("a")], {}, now=100.0)


def test_jobs_row_type_column_shows_job_by_default():
    assert "<td>job</td>" in render._jobs_row(_row("a"), {}, now=100.0)


def test_jobs_row_type_column_shows_claude_for_claude_payload():
    html = render._jobs_row(_row("a", payload="claude: tell a joke"), {}, now=100.0)
    assert "<td>claude</td>" in html


def test_jobs_row_type_column_shows_app_with_port_link():
    # PLAN-29 Befund 2, User-Fund: "Type, bei Apps mit Port und als Link
    # (auch wenn die App down ist)". Bewusst eigenständig von
    # _effective_sched_type()/models.effective_kind() (PLAN-25 Befund 7
    # entfernte "app" dort absichtlich aus Schedules-Übersicht/Filter,
    # User-Entscheidung: "Jobs mit Port und Prefix sollen einfach als Jobs
    # erscheinen") — hier, in der separaten Jobs-Tabelle, soll ein App-Job
    # weiterhin als "app" + Link erkennbar sein, unabhängig vom Live-Status
    # (kein Live-Check hier, Link steht auch wenn die App down ist).
    html = render._jobs_row(_row("a", app_port=9100), {}, now=100.0,
                            public_host="example.ts.net")
    assert ('<a href="http://example.ts.net:9100/" target="_blank" '
           'rel="noopener">app :9100</a>') in html


def test_jobs_row_type_column_defaults_to_localhost():
    html = render._jobs_row(_row("a", app_port=9100), {}, now=100.0)
    assert 'href="http://localhost:9100/"' in html


def test_jobs_table_shows_git_status_chip():
    html = render._jobs_table([_row("a", git_status="new")], {}, now=100.0)
    assert 'class="chip new"' in html and ">neu<" in html


def test_jobs_table_git_status_modified_and_clean():
    html = render._jobs_table(
        [_row("a", git_status="modified"), _row("b", git_status="clean")], {}, now=100.0)
    assert 'class="chip modified"' in html and ">geändert<" in html
    assert 'class="chip clean"' in html and ">unverändert<" in html


def test_jobs_table_shows_last_local_run_status_and_links_to_run_detail():
    html = render._jobs_table([_row("a")], {"a": {"id": 42, "status": "complete"}}, now=100.0)
    assert 'class="st complete"' in html
    assert 'href="/-/ui/run/42"' in html


def test_jobs_table_no_local_run_yet_shows_placeholder_no_link():
    html = render._jobs_table([_row("a")], {}, now=100.0)
    assert "noch nie lokal gelaufen" in html
    assert 'href="/-/ui/run/' not in html


def test_jobs_table_slug_always_links_to_local_job_detail():
    # PLAN-21 Befund 10-Nachtrag: Slug verlinkt jetzt immer auf die lokale
    # Job-Detailseite, unabhängig davon, ob der Job schon mal lokal lief.
    without_run = render._jobs_table([_row("a")], {}, now=100.0)
    assert 'href="/-/ui/jobs/detail/a"' in without_run
    with_run = render._jobs_table([_row("a")], {"a": {"id": 42, "status": "complete"}}, now=100.0)
    assert 'href="/-/ui/jobs/detail/a"' in with_run
    assert 'href="/-/ui/run/42"' in with_run  # Status verlinkt weiterhin den Lauf


def test_jobs_table_empty_shows_placeholder():
    html = render._jobs_table([], {}, now=100.0)
    assert "keine Job-MDs im Repository gefunden" in html


def test_jobs_table_live_row_shows_running():
    # PLAN-21 Befund 10, 2. Nachtrag: row["live"] gesetzt → "running" statt
    # letztem (abgeschlossenem) Status, Status-Link geht auf die Detailseite
    # (kein /-/ui/run/{jid} — für den laufenden Lauf existiert noch kein
    # Journal-Eintrag).
    html = render._jobs_table(
        [_row("a", live={"id": "jid1", "started_at": 100.0})],
        {"a": {"id": 42, "status": "complete"}}, now=200.0)  # alter, abgeschlossener Lauf
    assert 'class="st running">running<' in html
    assert 'href="/-/ui/jobs/detail/a"><span class="st running"' in html
    assert 'href="/-/ui/run/42"' not in html  # alter Status tritt zurück


def test_jobs_table_shows_started_finished_and_runtime_for_last_run():
    # PLAN-28 User-Feedback: "letzter Start / letztes Ende / letzte Laufzeit".
    lr = {"id": 42, "status": "complete", "started_at": 100.0,
         "finished_at": 112.0, "exec_runtime": 12.0}
    html = render._jobs_table([_row("a")], {"a": lr}, now=200.0)
    assert render._abs_time(100.0) in html
    assert render._abs_time(112.0) in html
    assert "12 s" in html


def test_jobs_table_no_local_run_yet_shows_dash_for_started_finished_runtime():
    html = render._jobs_table([_row("a")], {}, now=100.0)
    assert "<td>—</td><td>—</td><td>—</td>" in html


def test_jobs_table_live_row_shows_started_and_ongoing_runtime():
    # "aktuelle Laufzeit" — für einen laufenden Job die bisherige Dauer
    # (now - started_at), kein "letztes Ende" (noch offen).
    html = render._jobs_table(
        [_row("a", live={"id": "jid1", "started_at": 100.0})], {}, now=130.0)
    assert render._abs_time(100.0) in html
    assert "30 s" in html


def test_jobs_table_live_row_shows_awaiting_when_signaled():
    # PLAN-27 Befund 4, User-Fund: "der Status awaiting wird in /ui/jobs
    # nicht angezeigt. Im Job Detail bereits schon." — row["live"]["status"]
    # kommt jetzt aus local_runs_live() (worker.py), analog zu
    # _local_job_meta()s bereits bestehender Fallunterscheidung.
    html = render._jobs_table(
        [_row("a", live={"id": "jid1", "started_at": 100.0, "status": "awaiting"})],
        {}, now=200.0)
    assert 'class="st awaiting">awaiting<' in html
    assert 'class="st running">running<' not in html


def test_run_history_renders_rows():
    runs = [{"id": 7, "slug": "mein-testjob", "status": "complete", "exit_code": 0,
            "exec_runtime": 3.2, "finished_at": 100.0}]
    html = render._run_history(runs, now=200.0)
    assert "mein-testjob" in html and "exit 0" in html and "3 s" in html
    assert 'href="/-/ui/run/7"' in html


def test_run_history_empty_shows_placeholder():
    assert "noch keine lokalen Läufe" in render._run_history([], now=100.0)


def test_jobs_fragment_has_no_remote_or_hostlink_text():
    # PLAN-21 Befund 10: kein Remote-Bezug mehr im Fragment, egal was aufrufe-
    # seitig übergeben würde — die Funktion nimmt gar keinen scheduler_url/
    # Remote-Parameter mehr entgegen.
    html = render.jobs_fragment([_row("a")], {}, [], now=100.0)
    assert "Remote" not in html
    assert "hostlink" not in html


def test_jobs_fragment_self_polls():
    html = render.jobs_fragment([], {}, [], now=100.0)
    assert 'id="jobsboard"' in html and 'hx-get="/-/ui/jobs/board"' in html


def test_jobs_fragment_has_no_explanatory_note():
    # PLAN-27 Befund 3, User-Fund: erklärender Text ("Lokal per
    # discovery.discover() entdeckte Job-MDs ...") soll raus.
    html = render.jobs_fragment([], {}, [], now=100.0)
    assert "discovery.discover()" not in html
    assert "bildet nur ab, was gerade im Repository liegt" not in html


def test_jobs_fragment_wraps_sections_in_own_panel_cards():
    # PLAN-29 Befund 1, User-Fund: "wieder um 'Jobs im Repository' (besser:
    # 'Jobs') und um 'Lokale Läufe' den Rahmen zeichnen" — analog zu PLAN-25
    # Befund 5/6 (Feed, Schedules), die dasselbe .panel-card-Muster schon
    # nutzen. Umbenennung "Jobs im Repository" -> "Jobs" ist Teil desselben
    # Befunds.
    runs = [{"id": 7, "slug": "mein-testjob", "status": "complete", "exit_code": 0,
            "exec_runtime": 3.2, "finished_at": 100.0}]
    html = render.jobs_fragment([_row("mein-testjob")], {}, runs, now=200.0)
    assert html.count('class="panel-card"') == 2
    assert "<h2>Jobs</h2>" in html
    assert "Jobs im Repository" not in html
    assert html.index('class="panel-card"') < html.index("Jobs</h2>")
    assert html.index("Jobs</h2>") < html.index("Lokale Läufe")


def test_jobs_page_has_header_and_nav():
    html = render.jobs_page([], {}, [], now=100.0)
    assert 'href="/-/"' in html and 'href="/-/ui/logs"' in html
    assert "<title>bibi · Jobs</title>" in html


def test_jobs_page_has_status_cards_header():
    # PLAN-28 User-Feedback: "Der Header soll auch auf der Client Job Seite
    # angezeigt werden" — derselbe feed_status_fragment()-Header wie
    # /-/ und /-/ui/schedules (PLAN-27 Befund 2 hatte das nur fürs Live-Log
    # erledigt, /-/ui/jobs blieb dabei außen vor).
    html = render.jobs_page([], {}, [], now=100.0)
    assert 'id="feedstatus"' in html


def test_jobs_route_has_status_cards_header(team_repo: Path, app_with):
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert 'id="feedstatus"' in r.text


def test_screen_nav_includes_jobs_tab():
    # Jobs nur mit connect-Rolle sichtbar (PLAN-20 Befund 6).
    html = render._screen_nav("Schedules", roles=["connect"])
    assert 'href="/-/ui/jobs"' in html and "Jobs" in html


def test_screen_nav_hides_jobs_tab_without_connect_role():
    html = render._screen_nav("Schedules", roles=["scheduler"])
    assert 'href="/-/ui/jobs"' not in html


# ── Lokale Job-Detailseite (PLAN-21 Befund 10-Nachtrag) — Rendering ──────────


def test_local_job_view_never_run_returns_none():
    assert render._local_job_view(_row("a"), None, None) is None


def test_local_job_view_last_run_carries_status_and_timestamps():
    last_run = {"id": 5, "status": "complete", "started_at": 90.0,
               "finished_at": 100.0, "reason": None}
    job = render._local_job_view(_row("a"), last_run, None)
    assert job == {"id": 5, "status": "complete", "started_at": 90.0,
                   "finished_at": 100.0, "reason": None, "app_port": None}


def test_local_job_view_live_defaults_to_running_status():
    # Dieselbe Fallunterscheidung wie vormals _local_job_meta()/_jobs_row():
    # ein live-Eintrag ohne explizites Signal gilt als "running".
    job = render._local_job_view(_row("a"), None, {"id": "jid1", "started_at": 200.0})
    assert job["status"] == "running" and job["id"] == "jid1"


def test_local_job_view_live_awaiting_from_signal():
    job = render._local_job_view(
        _row("a"), None,
        {"id": "jid1", "status": "awaiting", "app_url": "http://127.0.0.1:9100/"})
    assert job["status"] == "awaiting" and job["app_url"] == "http://127.0.0.1:9100/"


def test_local_job_view_carries_app_port_from_local_regardless_of_run_state():
    local = {**_row("a"), "app_port": 9100}
    assert render._local_job_view(local, {"id": 5, "status": "complete"}, None)["app_port"] == 9100
    assert render._local_job_view(local, None, {"id": "jid1"})["app_port"] == 9100


# ── Client-Job-Detailseite: dieselben Bausteine wie beim Host (PLAN-29 Befund 3+5) ──


def test_jobs_detail_live_fragment_never_run_shows_host_style_start_only():
    # PLAN-29 Befund 3, User-Entscheidung: "Host-Großschreibung, konsistent
    # mit dem Rest der App" statt der bisherigen Icon-Buttons (▶/↺/■).
    html = render.jobs_detail_live_fragment("a", None, _row("a"), None)
    assert '<button hx-post="/-/ui/jobs/detail/a/start" hx-target="#jobsdetail-live" hx-swap="outerHTML">START</button>' in html
    assert 'hx-post="/-/ui/jobs/detail/a/kill" hx-target="#jobsdetail-live" hx-swap="outerHTML" disabled' in html
    assert 'hx-post="/-/ui/jobs/detail/a/reset" hx-target="#jobsdetail-live" hx-swap="outerHTML" disabled' in html
    assert "▶" not in html and "↺" not in html and "■" not in html
    assert "startbtn" not in html and "killbtn" not in html and "resetbtn" not in html


def test_jobs_detail_live_fragment_while_live_enables_kill_disables_start():
    html = render.jobs_detail_live_fragment(
        "a", {"id": "jid1", "started_at": 100.0, "events": []}, _row("a"), None)
    assert 'hx-post="/-/ui/jobs/detail/a/kill" hx-target="#jobsdetail-live" hx-swap="outerHTML">KILL</button>' in html
    assert 'hx-post="/-/ui/jobs/detail/a/start" hx-target="#jobsdetail-live" hx-swap="outerHTML" disabled' in html
    assert 'class="st running">running<' in html


def test_jobs_detail_live_fragment_shows_rebuild_for_container_job():
    local = {**_row("a"), "exec_mode": "container"}
    idle = render.jobs_detail_live_fragment("a", None, local, {"id": 5, "status": "complete"})
    assert 'hx-post="/-/ui/jobs/detail/a/rebuild"' in idle
    running = render.jobs_detail_live_fragment(
        "a", {"id": "jid1", "events": []}, local, None)
    assert 'hx-post="/-/ui/jobs/detail/a/rebuild"' in running  # nicht an live gebunden


def test_jobs_detail_live_fragment_hides_rebuild_for_host_job():
    html = render.jobs_detail_live_fragment("a", None, _row("a"), {"id": 5, "status": "complete"})
    assert "rebuild" not in html.lower()


def test_jobs_detail_live_fragment_meta_line_shows_type_trigger_git():
    local = _row("a", git_status="modified")
    html = render.jobs_detail_live_fragment("a", None, local, None)
    assert "job" in html and "now" in html
    assert 'class="chip modified"' in html and ">geändert<" in html


def test_jobs_detail_live_fragment_shows_app_link_even_without_any_run():
    # Bibi4-Iteration, User-Fund: "der fehlt" — vorher lieferte
    # _local_job_view() None (nie gelaufen, nichts live), wodurch
    # _live_panel() komplett leer blieb und der App-Link trotz statisch
    # bekanntem app_port (MD-Frontmatter) nirgends auftauchte. Der Link muss
    # deshalb aus local selbst kommen, nicht aus dem lauf-abhängigen job-Dict.
    local = _row("a", app_port=9100)
    html = render.jobs_detail_live_fragment("a", None, local, None,
                                            public_host="example.ts.net")
    assert '<a href="http://example.ts.net:9100/" target="_blank" rel="noopener">Zur App →</a>' in html


def test_jobs_detail_live_fragment_no_app_link_without_app_port():
    html = render.jobs_detail_live_fragment("a", None, _row("a"), None)
    assert "Zur App" not in html


def test_jobs_detail_live_fragment_no_status_duplicated_in_meta_line():
    # _local_job_meta_line() zeigt bewusst keinen eigenen Status mehr (anders
    # als die alte _local_job_meta()) — "letzter Lauf" kommt nur noch einmal,
    # aus _live_panel()s eigenem Label.
    html = render.jobs_detail_live_fragment("a", None, _row("a"), {"id": 5, "status": "complete"})
    assert html.count("letzter Lauf") == 1


def test_jobs_detail_live_fragment_falls_back_to_last_run_output():
    # PLAN-28 User-Feedback: "beim Host wird der Output des letzten Laufes
    # immer oben angezeigt bis RESET oder START" — derselbe Fallback beim
    # Client, jetzt über _live_panel() statt der alten _local_live_output().
    html = render.jobs_detail_live_fragment(
        "a", None, _row("a"), {"id": 5, "status": "complete", "finished_at": 100.0},
        last_run_output={"kind": "job",
                         "events": [{"t": 1.0, "s": "out", "line": "archiviert"}]})
    assert "archiviert" in html


def test_jobs_detail_live_fragment_shows_hitl_panel_when_awaiting():
    html = render.jobs_detail_live_fragment(
        "a", {"id": "jid1", "status": "awaiting", "app_url": "http://127.0.0.1:9100/",
             "events": []},
        _row("a"), None)
    assert "Eingabe erforderlich" in html
    assert 'href="http://127.0.0.1:9100/"' in html


def test_jobs_detail_live_fragment_no_raw_stream_link():
    # PLAN-29 Befund 3+5: /-/job/{id}/stream existiert nur über
    # _add_worker_routes() (roles.worker) — auf einem reinen Client (Rolle
    # connect) ein toter Link (live 501 bestätigt), deshalb raw_stream_base=
    # None fest verdrahtet für die Client-Detailseite.
    html = render.jobs_detail_live_fragment(
        "a", {"id": "jid1", "started_at": 100.0, "events": []}, _row("a"), None)
    assert "roher Stream" not in html
    assert "/-/job/jid1/stream" not in html


def test_jobs_detail_page_has_attribute_link():
    html = render.jobs_detail_page("a", _row("a"), None, [], now=100.0)
    assert 'href="/-/ui/jobs/detail/a/attrs">Attribute →</a>' in html


def test_jobs_detail_attrs_page_shows_local_config():
    local = {**_row("a"), "app_port": 9100, "exec_mode": "container"}
    html = render.jobs_detail_attrs_page("a", local)
    assert "<h1>a · Attribute</h1>" in html
    assert "Konfiguration" in html
    assert "<code>echo x</code>" in html  # payload
    assert "<code>9100</code>" in html
    assert "<code>container</code>" in html


def test_jobs_detail_attrs_page_omits_scheduling_section():
    # id/next_fire_at/fire sind Scheduler-Laufzeitstand — den hat ein rein
    # lokal entdeckter Job strukturell nicht (anders als schedule_attrs_page()).
    html = render.jobs_detail_attrs_page("a", _row("a"))
    assert "Scheduling" not in html


def test_jobs_detail_attrs_page_handles_missing_local():
    # Job-MD gelöscht/umbenannt, aber die Attribute-Seite noch aufgerufen —
    # kein 500, nur Platzhalter.
    html = render.jobs_detail_attrs_page("gone", None)
    assert "<h1>gone · Attribute</h1>" in html


def test_jobs_detail_live_fragment_data_attrs_reflect_running_state():
    idle = render.jobs_detail_live_fragment("a", None, _row("a"), None)
    assert 'id="jobsdetail-live"' in idle and 'data-running="0"' in idle
    running = render.jobs_detail_live_fragment(
        "a", {"id": "jid1", "events": []}, _row("a"), None)
    assert 'data-running="1"' in running
    assert 'data-journal-url="/-/ui/jobs/detail/a/journal"' in running
    assert 'hx-get="/-/ui/jobs/detail/a/live"' in running


def test_journal_fragment_base_param_targets_local_job_detail():
    # PLAN-21 Befund 10-Nachtrag: dieselbe Journal-Tabelle wie beim Host,
    # aber gegen die lokale Route verdrahtet, wenn base gesetzt ist.
    runs = [{"id": 7, "slug": "a", "status": "complete", "finished_at": 100.0}]
    default = render.journal_fragment(runs, "a", now=200.0)
    assert 'hx-delete="/-/ui/schedule/a/run/7"' in default
    local = render.journal_fragment(runs, "a", now=200.0, base="/-/ui/jobs/detail")
    assert 'hx-delete="/-/ui/jobs/detail/a/run/7"' in local
    assert 'href="/-/ui/run/7"' in local  # Detail-Link bleibt unverändert


def test_jobs_detail_page_has_breadcrumb_meta_and_journal():
    html = render.jobs_detail_page(
        "a", _row("a"), {"status": "complete"},
        [{"id": 7, "slug": "a", "status": "complete", "finished_at": 100.0}], now=200.0)
    assert 'href="/-/ui/jobs"' in html  # ← Jobs statt ← zurück (kein Schedule-Bezug)
    assert "<h1>a</h1>" in html
    assert 'id="journal"' in html
    assert 'hx-delete="/-/ui/jobs/detail/a/run/7"' in html


def test_jobs_detail_page_unknown_slug_still_renders():
    # Job-MD gelöscht/umbenannt, aber alte Läufe noch im lokalen Journal —
    # kein 500, nur eine leere Meta-Zeile (local=None).
    html = render.jobs_detail_page("gone", None, None, [], now=100.0)
    assert "<h1>gone</h1>" in html


def test_jobs_detail_page_with_live_shows_running_and_autorefresh_js():
    html = render.jobs_detail_page(
        "a", _row("a"), None, [], now=200.0,
        live={"id": "jid1", "kind": "job", "events": [{"t": 1.0, "s": "out", "line": "hi"}]})
    assert 'data-running="1"' in html
    assert "hi" in html  # Live-Output gerendert
    assert render._JOBS_LIVE_AUTOREFRESH_JS in html


# ── Route (gefakter Client + echtes Vault-Discovery + echtes Git-Repo) ───────


class _FakeClient:
    def __init__(self, *, schedules=None, run_journal=None, live=None,
                run_outputs=None) -> None:
        self._schedules = schedules or []
        self._run_journal = run_journal or []
        self._live = live or {}  # {slug: {"id":..., "events": [...]}}
        self._run_outputs = run_outputs or {}  # {journal_id: {"events": [...], "kind": ...}}
        self.run_calls: list[dict] = []
        self.delete_calls: list[int] = []
        self.rebuild_calls: list[str] = []
        self.schedules_called = False

    def local_run_output(self, journal_id: int) -> dict:
        if journal_id not in self._run_outputs:
            raise RuntimeError("404 not found")  # spiegelt HTTPError des echten Clients
        return self._run_outputs[journal_id]

    def status(self) -> dict:
        return {}

    def schedules(self):
        # PLAN-21 Befund 10: der Jobs-Screen darf das nie mehr aufrufen — kein
        # Remote-Abgleich mehr. Flag statt Exception, damit ein versehentlicher
        # Aufruf im Test sichtbar wird statt den ganzen Request 500en zu lassen.
        self.schedules_called = True
        return self._schedules

    def journal(self, **_):
        return []

    def run_journal(self, *, slug=None, **_):
        # Spiegelt die echte HTTP-Route (job_db.list_journal()): slug filtert
        # exakt ODER (nur für gepinnte Zeilen, pinned_host gesetzt) per
        # Bucket-Präfix — s. User-Fund 2026-07-13, job_db.py::list_journal().
        if slug is None:
            return self._run_journal
        def _matches(r):
            return r.get("slug") == slug or (
                bool(r.get("pinned_host")) and r.get("slug", "").startswith(f"{slug}-"))
        return [r for r in self._run_journal if _matches(r)]

    def jobs(self, **_):
        return []

    def run(self, *, slug=None, cmd=None):
        self.run_calls.append({"slug": slug, "cmd": cmd})
        return {"id": "x", "status": "complete"}

    def local_run_delete(self, journal_id: int):
        self.delete_calls.append(journal_id)
        self._run_journal = [r for r in self._run_journal if r.get("id") != journal_id]
        return {"deleted": journal_id}

    def run_live_list(self) -> dict:
        return {slug: {"id": v["id"], "started_at": v.get("started_at", 0.0)}
               for slug, v in self._live.items()}

    def run_live(self, slug: str) -> dict:
        if slug not in self._live:
            raise RuntimeError("404 not running")  # spiegelt HTTPError des echten Clients
        return self._live[slug]

    def run_live_kill(self, slug: str) -> dict:
        if slug not in self._live:
            raise RuntimeError("404 not running")
        del self._live[slug]
        return {"slug": slug, "signaled": True}

    def run_live_reset(self, slug: str) -> dict:
        if slug not in self._live:
            raise RuntimeError("404 not running")
        del self._live[slug]
        return {"slug": slug, "reset": True}

    def run_rebuild(self, slug: str) -> dict:
        self.rebuild_calls.append(slug)
        return {"slug": slug, "rebuilt": True}


def _seed_schedule_md(root: Path, slug: str, schedule: str, payload: str,
                      *, app_port: int | None = None) -> None:
    d = root / "vault" / "case" / slug
    d.mkdir(parents=True, exist_ok=True)
    port_line = f"app_port: {app_port}\n" if app_port is not None else ""
    (d / "README.md").write_text(
        f'---\nschedule: "{schedule}"\njob: "{payload}"\n{port_line}---\n', encoding="utf-8")


@pytest.fixture
def app_with(team_repo: Path):
    def _make(client: _FakeClient):
        return create_app(roles.resolve({"controller"}), controller_client=client), client
    return _make


def test_jobs_route_shows_local_md_with_git_status_new(team_repo: Path, app_with):
    # Frisch angelegt, nie committet/geaddet → git-Status "neu".
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient()
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert r.status_code == 200
        assert "mein-testjob" in r.text
        assert 'class="chip new"' in r.text and ">neu<" in r.text


def test_jobs_route_shows_app_port_link_for_discovered_app_job(team_repo: Path, app_with):
    # PLAN-29 Befund 2: end-to-end-Nachweis, dass app_port aus der MD-
    # Frontmatter tatsächlich bis zur gerenderten Type-Spalte durchgereicht
    # wird (_local_schedules() -> _jobs_data() -> jobs_page()), nicht nur,
    # dass die reine Render-Funktion einen bereits befüllten Dict-Key
    # akzeptiert.
    _seed_schedule_md(team_repo, "hitl-test-app", "now", "python3 app.py",
                      app_port=9100)
    client = _FakeClient()
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert r.status_code == 200
        assert ('<a href="http://localhost:9100/" target="_blank" '
               'rel="noopener">app :9100</a>') in r.text


def test_jobs_detail_attrs_route(team_repo: Path, app_with):
    # PLAN-29 Befund 3+5: end-to-end-Nachweis, dass die neue, lokal gespeiste
    # Attribute-Seite tatsächlich erreichbar ist und echte MD-Daten zeigt —
    # anders als schedule_attrs()/schedule_config() (Host), das auf einem
    # Client live nur leere Platzhalter liefert (s. PLAN-29.md Befund 3+5).
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x", app_port=9100)
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob/attrs")
        assert r.status_code == 200
        assert "<h1>mein-testjob · Attribute</h1>" in r.text
        assert "<code>9100</code>" in r.text


def test_jobs_detail_page_route_links_to_attrs_route(team_repo: Path, app_with):
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob")
        assert 'href="/-/ui/jobs/detail/mein-testjob/attrs">Attribute →</a>' in r.text


def test_jobs_route_never_calls_remote_schedules_even_with_scheduler_role(
    team_repo: Path, monkeypatch,
):
    # PLAN-21 Befund 10, User-Entscheidung: kein Remote-Abgleich mehr — auch
    # nicht auf einem Knoten mit scheduler-Rolle oder konfigurierter
    # BIBI_SCHEDULER_URL. Spiegelt die vorher hier getesteten Remote-Compare-
    # Szenarien, jetzt umgekehrt: kein Netzaufruf, egal welche Rolle/Config.
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.example:8780")
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(schedules=[{"slug": "alter-cron-job", "trigger": "0 */3 * * *",
                                     "payload": "echo r"}])
    app = create_app(roles.resolve({"controller", "scheduler"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert r.status_code == 200
        assert "mein-testjob" in r.text
        assert "alter-cron-job" not in r.text  # nur remote gemeldet, nie lokal entdeckt
        assert client.schedules_called is False


def test_jobs_route_shows_local_run_history(team_repo: Path, app_with):
    client = _FakeClient(run_journal=[{"id": 5, "slug": "mein-testjob", "status": "complete",
                                       "exit_code": 0, "exec_runtime": 3.2,
                                       "finished_at": 100.0, "domain": "local"}])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert "Lokale Läufe" in r.text and "mein-testjob" in r.text
        assert 'href="/-/ui/run/5"' in r.text


def test_jobs_route_per_job_status_finds_pinned_run_by_bucket_slug(team_repo: Path, app_with):
    # User-Fund 2026-07-13: run_pinned() vergibt pro Aufruf einen eindeutigen
    # jobs.slug (f"{bucket_slug}-{token}") — die ungefilterte "Lokale Läufe"-
    # Liste unten zeigt den Lauf zwar (s. test_jobs_route_shows_local_run_
    # history), aber die Pro-Job-Statuszelle (_jobs_row(), Slug-Lookup gegen
    # den STABILEN Bucket-Slug) fand ihn nie — zeigte immer "noch nie lokal
    # gelaufen", obwohl der Job gerade erst komplett gelaufen war.
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(run_journal=[
        {"id": 5, "slug": "mein-testjob-abc12345", "status": "complete",
         "exit_code": 0, "exec_runtime": 3.2, "finished_at": 100.0,
         "domain": "scheduled", "pinned_host": "mac"},
    ])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert "noch nie lokal gelaufen" not in r.text
        assert 'href="/-/ui/run/5"><span class="st complete"' in r.text


def test_jobs_board_fragment_route(team_repo: Path, app_with):
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/board")
        assert r.status_code == 200
        assert 'id="jobsboard"' in r.text


# ── Lokale Job-Detailseite (PLAN-21 Befund 10-Nachtrag) — Routen ─────────────


def test_jobs_detail_route_shows_meta_and_only_this_slugs_runs(team_repo: Path, app_with):
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(run_journal=[
        {"id": 5, "slug": "mein-testjob", "status": "complete", "finished_at": 100.0,
         "domain": "local"},
        {"id": 6, "slug": "anderer-job", "status": "complete", "finished_at": 100.0,
         "domain": "local"},
    ])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob")
        assert r.status_code == 200
        assert "mein-testjob" in r.text
        assert 'href="/-/ui/run/5"' in r.text
        assert "anderer-job" not in r.text  # slug-Filter greift


def test_jobs_detail_route_shows_pinned_runs_for_this_slug(team_repo: Path, app_with):
    # User-Fund 2026-07-13: "Bestätigt: ein abgeschlossener COMPLETE Lauf
    # erscheint nicht in der Liste der Journaled Jobs" — die Detailseite
    # filterte exakt gegen den ephemeren jobs.slug eines gepinnten Laufs
    # (f"{bucket_slug}-{token}"), der stabile Bucket-Slug traf nie.
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(run_journal=[
        {"id": 5, "slug": "mein-testjob-abc12345", "status": "complete",
         "finished_at": 100.0, "domain": "scheduled", "pinned_host": "mac"},
        {"id": 6, "slug": "job-runner-xxxxxxxx", "status": "complete",
         "finished_at": 100.0, "domain": "scheduled", "pinned_host": "mac"},
    ])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob")
        assert r.status_code == 200
        assert 'href="/-/ui/run/5"' in r.text
        assert 'href="/-/ui/run/6"' not in r.text  # anderer Bucket-Slug, kein Treffer


def test_jobs_route_shows_running_for_live_job(team_repo: Path, app_with):
    # PLAN-21 Befund 10, 2. Nachtrag: die Jobs-Liste zeigt "running" für einen
    # gerade laufenden lokalen Job, unabhängig vom letzten ABGESCHLOSSENEN
    # Lauf im Journal.
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(
        run_journal=[{"id": 5, "slug": "mein-testjob", "status": "complete",
                     "finished_at": 100.0, "domain": "local"}],
        live={"mein-testjob": {"id": "jidlive", "started_at": 200.0}})
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert 'class="st running">running<' in r.text


def test_jobs_detail_route_shows_live_output(team_repo: Path, app_with):
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(live={"mein-testjob": {
        "id": "jidlive", "kind": "job",
        "events": [{"t": 1.0, "s": "out", "line": "läuft gerade"}],
    }})
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob")
        assert r.status_code == 200
        assert 'data-running="1"' in r.text
        assert "läuft gerade" in r.text


def test_jobs_detail_route_shows_last_run_output_when_not_live(team_repo: Path, app_with):
    # PLAN-28 User-Feedback: "bei terminalen Status wurde der Output
    # entfernt" — kein live-Eintrag mehr, aber der letzte (abgeschlossene)
    # Lauf steht im Journal, dessen archivierter Output soll trotzdem stehen
    # bleiben, bis RESET oder erneutes START.
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(
        run_journal=[{"id": 5, "slug": "mein-testjob", "status": "complete",
                     "finished_at": 100.0, "domain": "local"}],
        run_outputs={5: {"kind": "job",
                        "events": [{"t": 1.0, "s": "out", "line": "archivierte ausgabe"}]}})
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob")
        assert r.status_code == 200
        assert 'data-running="0"' in r.text
        assert "archivierte ausgabe" in r.text


def test_jobs_detail_live_fragment_route(team_repo: Path, app_with):
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(live={"mein-testjob": {"id": "jidlive", "kind": "job", "events": []}})
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob/live")
        assert r.status_code == 200
        assert 'id="jobsdetail-live"' in r.text and 'data-running="1"' in r.text


def test_jobs_detail_live_fragment_route_not_running(team_repo: Path, app_with):
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/nichts-los/live")
        assert r.status_code == 200
        assert 'data-running="0"' in r.text


def test_jobs_detail_journal_fragment_route(team_repo: Path, app_with):
    # Regressionsschutz für den Live-Verifikations-Fund: journal_url zeigte
    # zunächst auf eine nie implementierte Route (404, still von htmx
    # verworfen) — #journal blieb nach Lauf-Ende veraltet stehen, bis zum
    # nächsten manuellen Reload.
    client = _FakeClient(run_journal=[
        {"id": 7, "slug": "mein-testjob", "status": "complete", "finished_at": 100.0,
         "domain": "local"}])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/mein-testjob/journal")
        assert r.status_code == 200
        assert 'id="journal"' in r.text
        assert 'hx-delete="/-/ui/jobs/detail/mein-testjob/run/7"' in r.text


def test_jobs_detail_live_fragment_journal_url_matches_real_route():
    # Derselbe Fund als reiner Render-Test: data-journal-url muss auf eine
    # Route zeigen, die tatsächlich existiert (obiger Route-Test).
    frag = render.jobs_detail_live_fragment(
        "a", {"id": "jid1", "events": []}, {}, None)
    assert 'data-journal-url="/-/ui/jobs/detail/a/journal"' in frag


def test_jobs_detail_route_unknown_slug_still_200s(team_repo: Path, app_with):
    # Job-MD entfernt/umbenannt, aber alte Läufe noch im lokalen Journal.
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/gone")
        assert r.status_code == 200
        assert "gone" in r.text


def test_jobs_detail_runs_fragment_route(team_repo: Path, app_with):
    client = _FakeClient(run_journal=[
        {"id": 5, "slug": "a", "status": "complete", "finished_at": 100.0}])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/detail/a/runs", params={"offset": 0})
        assert r.status_code == 200
        assert 'href="/-/ui/run/5"' in r.text


def test_jobs_detail_run_delete_route(team_repo: Path, app_with):
    client = _FakeClient(run_journal=[
        {"id": 5, "slug": "a", "status": "complete", "finished_at": 100.0}])
    app, fake = app_with(client)
    with TestClient(app) as c:
        r = c.delete("/-/ui/jobs/detail/a/run/5")
        assert r.status_code == 200
        assert fake.delete_calls == [5]
        assert 'id="journal"' in r.text
        assert "noch keine Läufe" in r.text  # Journal jetzt leer, sofort sichtbar


def test_jobs_detail_start_route_posts_to_own_fragment_not_jobsboard(team_repo: Path, app_with):
    # Bug-Regressionsschutz (2026-07-10): der Start-Button auf der
    # Detailseite muss #jobsdetail-live zurückbekommen, nicht das
    # #jobsboard-Fragment der Jobs-Liste (jobs_start()).
    client = _FakeClient(live={"a": {"id": "jid1", "kind": "job", "events": []}})
    app, fake = app_with(client)
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/a/start")
        assert r.status_code == 200
        assert fake.run_calls == [{"slug": "a", "cmd": None}]
        assert 'id="jobsdetail-live"' in r.text
        assert 'id="jobsboard"' not in r.text


def test_jobs_detail_kill_route(team_repo: Path, app_with):
    client = _FakeClient(live={"a": {"id": "jid1", "kind": "job", "events": []}})
    app, fake = app_with(client)
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/a/kill")
        assert r.status_code == 200
        assert "a" not in fake._live  # gekillt, aus der Live-Registry raus
        assert 'id="jobsdetail-live"' in r.text
        assert 'data-running="0"' in r.text  # sofort sichtbar, kein Warten auf den nächsten Poll


def test_jobs_detail_reset_route(team_repo: Path, app_with):
    client = _FakeClient(live={"a": {"id": "jid1", "kind": "job", "events": []}})
    app, fake = app_with(client)
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/a/reset")
        assert r.status_code == 200
        assert "a" not in fake._live  # zurückgesetzt, aus der Live-Registry raus
        assert 'id="jobsdetail-live"' in r.text
        assert 'data-running="0"' in r.text  # sofort sichtbar, kein Warten auf den nächsten Poll


def test_jobs_detail_reset_route_survives_nothing_running(team_repo: Path, app_with):
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/nichts-los/reset")
        assert r.status_code == 200  # kein 500, auch wenn client.run_live_reset() 404t


def test_jobs_detail_kill_route_survives_nothing_running(team_repo: Path, app_with):
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/nichts-los/kill")
        assert r.status_code == 200  # kein 500, auch wenn client.run_live_kill() 404t


def test_jobs_detail_rebuild_route(team_repo: Path, app_with):
    # User-Fund 2026-07-13: "REBUILD müsste doch auch beim Client notwendig
    # sein, oder?" — Analogon zu jobs_detail_kill()/jobs_detail_reset().
    _seed_schedule_md(team_repo, "a", "now", "echo x")
    app, fake = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/a/rebuild")
        assert r.status_code == 200
        assert fake.rebuild_calls == ["a"]
        assert 'id="jobsdetail-live"' in r.text


def test_jobs_detail_rebuild_route_survives_backend_error(team_repo: Path, app_with):
    class _FailingRebuildClient(_FakeClient):
        def run_rebuild(self, slug: str) -> dict:
            raise RuntimeError("409 not a container job")
    app, _ = app_with(_FailingRebuildClient())
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/nichts-los/rebuild")
        assert r.status_code == 200  # kein 500, auch wenn client.run_rebuild() fehlschlägt
