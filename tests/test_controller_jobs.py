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


def test_jobs_row_live_deferred_status_shown_not_collapsed_to_running():
    # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED nie
    # im Dashboard gesehen"): dieselbe Kollabierung wie in _local_job_view().
    html = render._jobs_row(
        _row("a", live={"id": "jid1", "status": "deferred", "started_at": 100.0}),
        {}, now=105.0)
    assert '<span class="st deferred">deferred</span>' in html
    assert '<span class="st running">running</span>' not in html


def test_jobs_row_live_failed_status_shown_not_collapsed_to_running():
    html = render._jobs_row(
        _row("a", live={"id": "jid1", "status": "failed", "started_at": 100.0}),
        {}, now=105.0)
    assert '<span class="st failed">failed</span>' in html
    assert '<span class="st running">running</span>' not in html


def test_jobs_table_has_no_start_button():
    # PLAN-28 User-Feedback: "CTA START soll es hier gar nicht geben, das
    # gibt es nur auf der Detail Seite" — die Übersicht dient reinem Review.
    html = render._jobs_table([_row("mein-testjob")], {}, now=100.0)
    assert "startbtn" not in html
    assert "hx-post=" not in html


def test_jobs_table_header_includes_type_column():
    # Seit m.rau/bibi#66 ist der Kopf ein Sortier-Link — der Spaltentext bleibt.
    assert ">Type" in render._jobs_table([_row("a")], {}, now=100.0)


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


def test_jobs_table_git_status_modified_chip_at_slug():
    # Bibi4-Iteration, User-Fund: keine eigene GIT-Spalte mehr, Chip sitzt
    # direkt am Slug.
    html = render._jobs_table([_row("a", git_status="modified")], {}, now=100.0)
    assert 'class="chip modified"' in html and ">geändert<" in html


def test_jobs_table_git_status_clean_shows_no_chip():
    # Bibi4-Iteration, User-Fund: "es genügt new/modified/clean, wobei wir
    # clean als Chip gar nicht anzeigen. Damit machen wir den Screen für den
    # Normalzustand ruhiger."
    html = render._jobs_table([_row("a", git_status="clean")], {}, now=100.0)
    assert "chip" not in html


def test_jobs_table_shows_git_status_conflict_chip():
    # Bibi4-Iteration, User-Fund: "sind sie lokal modifiziert, konfliktär,
    # fehlen?" — eigener Zustand, nicht mehr im "modified"-Topf.
    html = render._jobs_table([_row("a", git_status="conflict")], {}, now=100.0)
    assert 'class="chip conflict"' in html and ">konfliktär<" in html


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


def test_human_duration_thresholds():
    # Bibi4-Iteration, User-Fund: "Laufzeit soll human-readable sein ... je
    # nach Dauer ein angepasstes Delta" — zwei Einheiten je Stufe.
    assert render._human_duration(None) == "—"
    assert render._human_duration(45) == "45s"
    assert render._human_duration(192) == "3m 12s"
    assert render._human_duration(5400) == "1h 30m"
    assert render._human_duration(90000) == "1d 1h"


def test_jobs_table_shows_last_and_runtime_for_last_run():
    # Bibi4-Iteration, User-Fund: Slug/Type/Status/last-since/Runtime — EINE
    # last/since-Spalte statt getrennt Start/Ende (analog zu _sched_row()s
    # last_run_at: abgeschlossen -> Ende des letzten Laufs).
    lr = {"id": 42, "status": "complete", "started_at": 100.0,
         "finished_at": 112.0, "exec_runtime": 12.0}
    html = render._jobs_table([_row("a")], {"a": lr}, now=200.0)
    assert render._abs_time(112.0) in html
    assert "12s" in html


def test_jobs_table_no_local_run_yet_shows_dash_for_last_and_runtime():
    html = render._jobs_table([_row("a")], {}, now=100.0)
    assert '<td>—</td><td>—</td><td><span id="spark-a" hx-preserve="true"></span></td></tr>' in html


def test_jobs_table_live_row_shows_started_and_ongoing_runtime():
    # "aktuelle Laufzeit" — für einen laufenden Job die bisherige Dauer
    # (now - started_at); last/since zeigt den Start des laufenden Versuchs
    # (kein "letztes Ende", noch offen).
    html = render._jobs_table(
        [_row("a", live={"id": "jid1", "started_at": 100.0})], {}, now=130.0)
    assert render._abs_time(100.0) in html
    assert "30s" in html


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


def test_jobs_fragment_has_no_remote_or_hostlink_text():
    # PLAN-21 Befund 10: kein Remote-Bezug mehr im Fragment, egal was aufrufe-
    # seitig übergeben würde — die Funktion nimmt gar keinen scheduler_url/
    # Remote-Parameter mehr entgegen.
    html = render.jobs_fragment([_row("a")], {}, now=100.0)
    assert "Remote" not in html
    assert "hostlink" not in html


def test_jobs_fragment_is_bus_driven():
    # PLAN-36 Stufe 36.3: Board haengt am kollektiven Bus-Target "jobs".
    html = render.jobs_fragment([], {}, now=100.0)
    assert 'id="jobsboard"' in html
    assert 'data-bus="jobs"' in html
    assert 'data-bus-refetch="/-/ui/jobs/board"' in html
    assert "hx-trigger" not in html.split(">")[0]


def test_jobs_fragment_has_no_explanatory_note():
    # PLAN-27 Befund 3, User-Fund: erklärender Text ("Lokal per
    # discovery.discover() entdeckte Job-MDs ...") soll raus.
    html = render.jobs_fragment([], {}, now=100.0)
    assert "discovery.discover()" not in html
    assert "bildet nur ab, was gerade im Repository liegt" not in html


def test_jobs_fragment_has_single_panel_card_no_local_runs():
    # Bibi4-Iteration, User-Fund: "der untere Abschnitt lokale Läufe wandert
    # in den eigenen Screen Archive" — löst PLAN-29 Befund 1 (2 Panel-Cards
    # hier) auf 1 Panel-Card ab, analog zu schedules_fragment() beim Host.
    html = render.jobs_fragment([_row("mein-testjob")], {}, now=200.0)
    assert html.count('class="panel-card"') == 1
    assert "<h2>Jobs</h2>" in html
    assert "Jobs im Repository" not in html


# --- Sparkline (Bibi4-Iteration, User-Fund: "eine Sparkline, die die durch
# --- den Agenten verursachten git Änderungen repräsentiert") ------------------


def test_sparkline_svg_empty_without_activity():
    assert render._sparkline_svg([]) == ""
    assert render._sparkline_svg([0, 0, 0]) == ""


def test_sparkline_svg_renders_polyline_with_activity():
    svg = render._sparkline_svg([0, 1, 0, 2])
    assert svg.startswith('<svg class="sparkline"')
    assert "<polyline" in svg


def test_sparkline_cell_renders_svg_when_series_given():
    html = render._jobs_table(
        [_row("a")], {}, now=100.0, sparklines={"a": [0, 1, 2]})
    assert 'id="spark-a" hx-preserve="true"><svg' in html


def test_sparkline_cell_empty_placeholder_when_no_series():
    # jobs_board() (2s-Self-Poll) übergibt bewusst kein sparklines-Dict — die
    # Zelle bleibt leer, hx-preserve behält das vom Seitenaufbau vorhandene
    # Sparkline-Element (kein teurer Git-Aufruf im Sekundentakt).
    html = render._jobs_table([_row("a")], {}, now=100.0)
    assert '<span id="spark-a" hx-preserve="true"></span>' in html


def test_jobs_fragment_omits_sparklines_by_default():
    html = render.jobs_fragment([_row("a")], {}, now=100.0)
    assert '<span id="spark-a" hx-preserve="true"></span>' in html


def test_jobs_page_includes_sparklines_when_given():
    html = render.jobs_page(
        [_row("a")], {}, now=100.0, sparklines={"a": [0, 5]})
    assert 'id="spark-a" hx-preserve="true"><svg' in html
    assert "Lokale Läufe" not in html


def test_sparkline_cell_lazy_renders_load_trigger():
    # Bibi4-Iteration, User-Fund ("Sparklines dauern beim Reload immer"):
    # Entkopplung vom initialen Seitenaufbau — Platzhalter mit hx-get gegen
    # eine eigene Pro-Slug-Route statt der bisherigen blockierenden
    # _job_sparkline_series()-Berechnung inline in jobs_screen().
    html = render._sparkline_cell_lazy("a")
    assert html == (
        '<span id="spark-a" hx-preserve="true" '
        'hx-get="/-/ui/jobs/a/sparkline" hx-trigger="load" hx-swap="outerHTML"></span>'
    )


def test_sparkline_cell_lazy_survives_poll_before_it_resolves():
    # Regression, User-Fund (live nach dem Deploy): "Sparklines erscheinen
    # jetzt gar nicht mehr." Root Cause: der Lazy-Platzhalter fehlte
    # hx-preserve — der 2s-Self-Poll (jobs_board(), sparklines=None) rissn
    # ihn samt seines noch laufenden hx-get("load") aus dem DOM, bevor der
    # sich auflösen konnte; jede folgende Poll-Antwort (leere hx-preserve-
    # Zelle ohne hx-get) wurde danach für immer preserved -> dauerhaft leer.
    # hx-preserve muss auf BEIDEN Zuständen sitzen (unaufgelöst UND
    # aufgelöst), damit htmx den unaufgelösten Zustand über einen Poll
    # hinweg am Leben hält, bis sein eigener hx-get durch ist.
    lazy = render._sparkline_cell_lazy("a")
    polled = render._sparkline_cell("a", None)
    assert 'hx-preserve="true"' in lazy
    assert 'hx-preserve="true"' in polled
    assert lazy.split(" ")[0] == polled.split(" ")[0] == '<span'
    # dieselbe id in beiden Zuständen -> htmx matched sie beim Swap
    assert 'id="spark-a"' in lazy and 'id="spark-a"' in polled


def test_jobs_page_uses_lazy_sparklines_when_requested():
    html = render.jobs_page([_row("a")], {}, now=100.0, lazy_sparklines=True)
    assert 'hx-get="/-/ui/jobs/a/sparkline" hx-trigger="load"' in html


def test_jobs_page_lazy_sparklines_ignores_eager_sparklines_arg():
    # lazy_sparklines gewinnt, falls beides übergeben wird — kein Doppel-Render.
    html = render.jobs_page([_row("a")], {}, now=100.0,
                            sparklines={"a": [0, 5]}, lazy_sparklines=True)
    assert "<svg" not in html
    assert 'hx-get="/-/ui/jobs/a/sparkline"' in html


# ── Archive-Screen (Client, Bibi4-Iteration) ─────────────────────────────────


def test_client_archive_table_renders_slug_type_status_when_runtime_next():
    runs = [{"id": 7, "slug": "mein-testjob", "status": "complete",
            "payload": "claude: tu was", "exec_runtime": 3.2, "finished_at": 100.0}]
    html = render._client_archive_table(runs, now=200.0)
    # Seit m.rau/bibi#66 sind die Köpfe Sortier-Links; geprüft wird die
    # Spaltenfolge über ihren Text, nicht über das Markup.
    cols = ("Slug", "Type", "Status", "last/since", "runtime", "next")
    assert [c for c in cols if f">{c}" in html] == list(cols)
    assert 'href="/-/ui/run/7">mein-testjob<' in html
    assert '<td class="kind">claude</td>' in html
    assert 'class="st complete" href="/-/ui/run/7">complete<' in html
    assert "3s" in html
    assert "<td>—</td>" in html  # next: beim Client immer "—", nicht ausgeblendet


def test_client_archive_table_shows_finished_at_datetime():
    # Bibi4-Iteration, User-Fund: fehlendes Datum/Uhrzeit im Client-Archive —
    # analog zum Host, der last/since via _time_toggle_cell()/_ago() zeigt.
    runs = [{"id": 7, "slug": "mein-testjob", "status": "complete",
            "payload": "echo x", "exec_runtime": 3.2, "finished_at": 100.0}]
    html = render._client_archive_table(runs, now=200.0)
    assert "ago" in html or "min" in html or "s" in html  # relative _ago()-Ausgabe


def test_client_archive_table_empty_shows_placeholder():
    assert "keine lokalen Läufe" in render._client_archive_table([], now=100.0)


def test_jobs_archive_fragment_is_bus_driven():
    frag = render.jobs_archive_fragment([{"id": 1, "slug": "a", "status": "complete"}], now=1.0)
    assert 'id="archive"' in frag
    assert 'data-bus="jobs"' in frag
    assert 'data-bus-refetch="/-/ui/jobs/archive/list"' in frag
    assert "window.bibiFollow" not in frag
    assert "Archive (1)" in frag


def test_jobs_archive_page_has_active_archive_tab():
    html = render.jobs_archive_page([], now=1000.0, daemon_status={"roles": ["connect"]})
    assert html.lower().startswith("<!doctype html>")
    assert '<span class="tab-active">Archive</span>' in html
    assert 'id="archive"' in html


def test_jobs_archive_page_includes_status_cards():
    # Analog zum Host-Archive-Screen (test_controller_schedules.py) — fehlten
    # hier ebenfalls.
    html = render.jobs_archive_page([], now=1000.0, daemon_status={"roles": ["connect"]})
    assert 'id="feedstatus"' in html


def test_jobs_page_has_header_and_nav():
    html = render.jobs_page([], {}, now=100.0)
    assert 'href="/-/"' in html and 'href="/-/log"' in html
    assert "<title>bibi · Jobs</title>" in html


def test_jobs_page_has_status_cards_header():
    # PLAN-28 User-Feedback: "Der Header soll auch auf der Client Job Seite
    # angezeigt werden" — derselbe feed_status_fragment()-Header wie
    # /-/ und /-/ui/schedules (PLAN-27 Befund 2 hatte das nur fürs Live-Log
    # erledigt, /-/ui/jobs blieb dabei außen vor).
    html = render.jobs_page([], {}, now=100.0)
    assert 'id="feedstatus"' in html


# ── m.rau/bibi#90: daemon_status darf den Status-Filter nicht verdrängen ────
#
# `status` trug in jobs_page() zwei Bedeutungen: den Filterwert der Signatur
# und — lokal überschrieben — das Daemon-Status-Dict. Letzteres landete über
# jobs_fragment() in filter_schedules(), das ein nichtleeres Dict als aktiven
# Filter las und dann einen String gegen ein Dict verglich: jede Zeile fiel
# weg. Der Client-Jobs-Screen war damit beim Seitenaufbau immer leer, sobald
# überhaupt ein daemon_status anlag — also live auf jedem Knoten.
#
# Dass es nie eine Suite reissen liess, liegt an der Aufruf-Form: jeder
# bestehende jobs_page()-Test laesst daemon_status weg, dann ist der
# ueberschriebene Wert {} und damit falsy — der Filter greift zufaellig
# nicht. Genau deshalb geben die beiden Tests hier daemon_status explizit
# mit; ohne das laufen sie am Fehler vorbei.
#
# schedules_page() (Host) macht es seit jeher richtig und warnt im Docstring
# ausdruecklich vor der Verwechslung — jobs_page() war die einzige Stelle,
# die beide Bedeutungen auf einem Namen fuehrte.


def test_jobs_page_daemon_status_does_not_shadow_status_filter():
    html = render.jobs_page(
        [_row("a"), _row("b")], {}, now=100.0,
        daemon_status={"roles": ["synchronizer", "controller", "connect"]})
    assert "keine Job-MDs im Repository gefunden" not in html
    assert 'href="/-/ui/jobs/detail/a"' in html
    assert 'href="/-/ui/jobs/detail/b"' in html


def test_jobs_page_status_filter_still_applies_with_daemon_status():
    # Gegenprobe zum Test darueber: der echte Filterwert muss weiterhin
    # durchgreifen, auch wenn gleichzeitig ein daemon_status anliegt.
    html = render.jobs_page(
        [_row("a"), _row("b")], {"a": {"id": 1, "status": "complete"},
                                 "b": {"id": 2, "status": "failed"}},
        now=100.0, status="failed",
        daemon_status={"roles": ["synchronizer", "controller", "connect"]})
    assert 'href="/-/ui/jobs/detail/b"' in html
    assert 'href="/-/ui/jobs/detail/a"' not in html


def test_jobs_route_has_status_cards_header(team_repo: Path, app_with):
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert 'id="feedstatus"' in r.text


def test_screen_nav_includes_jobs_tab():
    # Jobs nur mit connect-Rolle sichtbar (PLAN-20 Befund 6).
    html = render._screen_nav("Schedules", roles=["connect"])
    assert 'href="/-/jobs"' in html and "Jobs" in html


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


def test_local_job_view_live_carries_deferred_status():
    # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED nie
    # im Dashboard gesehen"): die alte "awaiting sonst running"-Regel
    # kollabierte jeden anderen Live-Status hart auf "running" — live["status"]
    # (lokale worker.local_run_live(), jetzt mit echter DB-Spalte) muss
    # unveraendert durchgereicht werden.
    job = render._local_job_view(
        _row("a"), None, {"id": "jid1", "status": "deferred", "started_at": 200.0})
    assert job["status"] == "deferred"


def test_local_job_view_live_carries_failed_status():
    job = render._local_job_view(
        _row("a"), None, {"id": "jid1", "status": "failed", "started_at": 200.0})
    assert job["status"] == "failed"


def test_local_job_view_carries_app_port_from_local_regardless_of_run_state():
    local = {**_row("a"), "app_port": 9100}
    assert render._local_job_view(local, {"id": 5, "status": "complete"}, None)["app_port"] == 9100
    assert render._local_job_view(local, None, {"id": "jid1"})["app_port"] == 9100


# ── Client-Job-Detailseite: dieselben Bausteine wie beim Host (PLAN-29 Befund 3+5) ──


def test_jobs_detail_live_fragment_never_run_shows_host_style_start_only():
    # PLAN-29 Befund 3, User-Entscheidung: "Host-Großschreibung, konsistent
    # mit dem Rest der App" statt der bisherigen Icon-Buttons (▶/↺/■).
    html = render.jobs_detail_live_fragment("a", None, _row("a"), None)
    assert ('<button hx-post="/-/ui/jobs/detail/a/start" hx-target="#jobsdetail-live" '
            'hx-swap="outerHTML" hx-disabled-elt="this">START') in html
    assert 'hx-post="/-/ui/jobs/detail/a/kill" hx-target="#jobsdetail-live" hx-swap="outerHTML" hx-disabled-elt="this" disabled' in html
    assert 'hx-post="/-/ui/jobs/detail/a/reset" hx-target="#jobsdetail-live" hx-swap="outerHTML" hx-disabled-elt="this" disabled' in html
    assert "▶" not in html and "↺" not in html and "■" not in html
    assert "startbtn" not in html and "killbtn" not in html and "resetbtn" not in html


def test_jobs_detail_live_fragment_while_live_enables_kill_disables_start():
    html = render.jobs_detail_live_fragment(
        "a", {"id": "jid1", "started_at": 100.0, "events": []}, _row("a"), None)
    assert ('hx-post="/-/ui/jobs/detail/a/kill" hx-target="#jobsdetail-live" '
            'hx-swap="outerHTML" hx-disabled-elt="this">KILL') in html
    assert 'hx-post="/-/ui/jobs/detail/a/start" hx-target="#jobsdetail-live" hx-swap="outerHTML" hx-disabled-elt="this" disabled' in html
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


def test_jobs_detail_live_fragment_meta_line_uses_same_markup_as_host():
    # Seitenabgleich (Batch 8, User-Fund): dieselbe Meta-Zeilen-Umhüllung wie
    # live_fragment() (Host) — <div class="meta">, nicht mehr <p class="muted">
    # — auch wenn der Inhalt rollenspezifisch bleibt (Git-Status/App-Link statt
    # letzter/nächster Lauf).
    html = render.jobs_detail_live_fragment("a", None, _row("a"), None)
    assert '<div class="meta">Typ' in html
    assert 'class="muted"' not in html


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
    # PLAN-36 Stufe 36.3: Update-Weg ist der Bus, kein hx-get/Poll mehr.
    assert 'data-bus="live:a"' in running
    assert 'data-bus-refetch="/-/ui/jobs/detail/a/live"' in running
    assert "hx-get" not in running.split(">")[0]


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
    assert "<h1>a</h1>" in html
    assert 'id="journal"' in html
    assert 'hx-delete="/-/ui/jobs/detail/a/run/7"' in html


def test_jobs_detail_page_live_run_shows_journal_placeholder():
    # Job-Lifecycle-Redesign (leichte Variante statt PLAN-35, Case
    # 20260621.Bibi4-870bd9db, 2026-07-27): ein laufender lokaler Job ohne
    # echte Journal-Zeilen zeigt jetzt eine Platzhalterzeile, verlinkt auf
    # #jobsdetail-live (Client-Anker, nicht der Host-Default #live).
    live = {"id": "jid1", "status": "running", "started_at": 100.0}
    html = render.jobs_detail_page("a", _row("a", live=live), None, [], now=105.0,
                                   live=live)
    assert "noch keine Läufe" not in html
    assert '<a class="back" href="#jobsdetail-live">↑ live</a>' in html


def test_jobs_detail_page_has_no_back_link():
    # Zweite Bibi4-Iteration, User-Fund: derselbe Seitenabgleich, der
    # schedule_detail_page() den "← zurück"-Link genommen hat, gilt explizit
    # auch für den Client — die Nav-Leiste hat schon einen Jobs-Tab.
    html = render.jobs_detail_page("a", _row("a"), None, [], now=100.0)
    assert '<a class="back" href="/-/ui/jobs">← Jobs</a>' not in html


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
    # PLAN-36 Stufe 36.2: der Fingerprint-Autorefresh ist durch den globalen
    # Event-Strom ersetzt — die Client-Seite bindet jetzt dieselben Skripte
    # wie der Host ein und traegt die data-bus-Adressen fuer den Refetch.
    assert render._EVENTS_JS in html and render._SCROLL_JS in html
    assert 'data-bus="live:a"' in html
    assert 'data-bus-refetch="/-/ui/jobs/detail/a/live"' in html


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


# ── Sparkline-Entkopplung (zweite Bibi4-Iteration) ──────────────────────────


def test_jobs_route_renders_eager_sparkline_not_lazy_placeholder(team_repo: Path, app_with):
    # Revert (User-Fund 2026-07-22: die Lazy-Variante — 19 Pro-Slug-hx-get-
    # Requests gleichzeitig mit dem 2s-Self-Poll — hängte den Browser-Tab
    # komplett auf, live in mehreren frischen Tabs reproduziert; Staffelung
    # allein behob es nicht). jobs_screen() rechnet die Serie jetzt wieder
    # synchron (wie vor der Sparkline-Entkopplung), keine Pro-Slug-Requests
    # mehr vom initialen Seitenaufbau.
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient()
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert r.status_code == 200
        assert 'hx-get="/-/ui/jobs/mein-testjob/sparkline"' not in r.text


def test_jobs_sparkline_route_returns_resolved_cell(team_repo: Path, app_with):
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient()
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/mein-testjob/sparkline")
        assert r.status_code == 200
        assert 'id="spark-mein-testjob" hx-preserve="true">' in r.text
        assert 'hx-get' not in r.text  # aufgelöst, kein erneuter Lazy-Trigger


def test_jobs_sparkline_route_unknown_slug_returns_empty_cell(team_repo: Path, app_with):
    # Kein Crash bei einem Slug, der (Rennen mit Löschen/Umbenennen) nicht
    # mehr existiert — leere, aber valide Zelle statt 404/500.
    client = _FakeClient()
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/gone/sparkline")
        assert r.status_code == 200
        assert 'id="spark-gone" hx-preserve="true"></span>' in r.text


def test_jobs_sparkline_concurrent_requests_compute_once(team_repo: Path, app_with, monkeypatch):
    # Kern des Fixes: mehrere gleichzeitige Pro-Slug-Requests (wie beim
    # initialen Laden mehrerer Zeilen, alle mit hx-trigger="load") duerfen
    # bei kaltem Cache nur EINE teure git-log-Berechnung ausloesen, nicht
    # eine pro Zeile (thundering herd) — sonst waere die Entkopplung fuer
    # den Cache-Miss-Fall schlechter als der alte, einmalige Blockier-Aufruf.
    import threading
    import time as time_mod
    from bibi import feed as feed_mod

    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    calls = []
    real_collect = feed_mod.collect_commits

    def slow_collect_commits(root, **kw):
        calls.append(1)
        time_mod.sleep(0.2)  # simuliert einen teuren git log
        return real_collect(root, **kw)

    monkeypatch.setattr(feed_mod, "collect_commits", slow_collect_commits)

    client = _FakeClient()
    app, _ = app_with(client)
    with TestClient(app) as c:
        results = []

        def _fetch():
            results.append(c.get("/-/ui/jobs/mein-testjob/sparkline").status_code)

        threads = [threading.Thread(target=_fetch) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    assert results == [200] * 5
    assert len(calls) == 1


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


def test_jobs_route_no_longer_shows_local_run_history(team_repo: Path, app_with):
    # Bibi4-Iteration, User-Fund: "Lokale Läufe" wanderte auf den eigenen
    # Archive-Screen (s. test_jobs_archive_route_shows_local_run_history unten).
    client = _FakeClient(run_journal=[{"id": 5, "slug": "mein-testjob", "status": "complete",
                                       "exit_code": 0, "exec_runtime": 3.2,
                                       "finished_at": 100.0, "domain": "local"}])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert "Lokale Läufe" not in r.text


def test_jobs_archive_route_shows_local_run_history(team_repo: Path, app_with):
    client = _FakeClient(run_journal=[{"id": 5, "slug": "mein-testjob", "status": "complete",
                                       "exit_code": 0, "exec_runtime": 3.2,
                                       "finished_at": 100.0, "domain": "local"}])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/archive")
        assert "mein-testjob" in r.text
        assert 'href="/-/ui/run/5"' in r.text


def test_jobs_archive_list_fragment_route(team_repo: Path, app_with):
    client = _FakeClient(run_journal=[{"id": 5, "slug": "mein-testjob", "status": "complete"}])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs/archive/list")
        assert r.status_code == 200
        assert 'id="archive"' in r.text and "mein-testjob" in r.text


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
