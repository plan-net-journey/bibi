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


def _row(slug: str, *, git_status: str = "clean", live: dict | None = None) -> dict:
    return {"slug": slug, "schedule": "now", "at": None, "payload": "echo x",
            "repo_path": f"vault/case/{slug}/README.md", "git_status": git_status,
            "live": live}


def test_jobs_table_start_button_always_enabled():
    # Jede Zeile kommt aus dem lokalen Discovery-Scan (kein "nur remote"-Fall
    # mehr) — der Start-Button ist also nie mehr deaktiviert.
    html = render._jobs_table([_row("mein-testjob")], {}, now=100.0)
    assert 'hx-post="/-/ui/jobs/start/mein-testjob"' in html
    assert "disabled" not in html


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


def test_jobs_table_live_row_shows_running_and_disables_start():
    # PLAN-21 Befund 10, 2. Nachtrag: row["live"] gesetzt → "running" statt
    # letztem (abgeschlossenem) Status, Status-Link geht auf die Detailseite
    # (kein /-/ui/run/{jid} — für den laufenden Lauf existiert noch kein
    # Journal-Eintrag), Start-Button deaktiviert (Server lehnt mit 409 ab).
    html = render._jobs_table(
        [_row("a", live={"id": "jid1", "started_at": 100.0})],
        {"a": {"id": 42, "status": "complete"}}, now=200.0)  # alter, abgeschlossener Lauf
    assert 'class="st running">running<' in html
    assert 'href="/-/ui/jobs/detail/a"><span class="st running"' in html
    assert 'href="/-/ui/run/42"' not in html  # alter Status tritt zurück
    assert "disabled" in html


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


def test_jobs_page_has_header_and_nav():
    html = render.jobs_page([], {}, [], now=100.0)
    assert 'href="/-/"' in html and 'href="/-/ui/logs"' in html
    assert "<title>bibi · Jobs</title>" in html


def test_screen_nav_includes_jobs_tab():
    # Jobs nur mit connect-Rolle sichtbar (PLAN-20 Befund 6).
    html = render._screen_nav("Schedules", roles=["connect"])
    assert 'href="/-/ui/jobs"' in html and "Jobs" in html


def test_screen_nav_hides_jobs_tab_without_connect_role():
    html = render._screen_nav("Schedules", roles=["scheduler"])
    assert 'href="/-/ui/jobs"' not in html


# ── Lokale Job-Detailseite (PLAN-21 Befund 10-Nachtrag) — Rendering ──────────


def test_local_job_meta_shows_type_trigger_git_and_last_status():
    local = _row("a", git_status="modified")
    html = render._local_job_meta("a", local, {"status": "complete"})
    assert "job" in html and "now" in html
    assert 'class="chip modified"' in html and ">geändert<" in html
    assert 'class="st complete">complete' in html
    assert 'hx-post="/-/ui/jobs/detail/a/start"' in html


def test_local_job_meta_no_last_run_omits_status():
    html = render._local_job_meta("a", _row("a"), None)
    assert "letzter Lauf" not in html


def test_local_job_meta_live_shows_running_and_disables_start():
    # PLAN-21 Befund 10, 2. Nachtrag: live gesetzt → "running" statt letztem
    # Status, Start-Button deaktiviert, Ziel jetzt #jobsdetail-live.
    html = render._local_job_meta("a", _row("a"), {"status": "complete"},
                                  live={"id": "jid1", "events": []})
    assert 'class="st running">running<' in html
    assert "letzter Lauf" not in html  # tritt zurück, solange live
    assert 'class="startbtn" hx-post="/-/ui/jobs/detail/a/start"' in html
    assert 'class="startbtn" hx-post="/-/ui/jobs/detail/a/start" hx-target="#jobsdetail-live" hx-swap="outerHTML" disabled' in html
    assert 'hx-target="#jobsdetail-live"' in html


def test_local_job_meta_kill_button_only_enabled_while_live():
    # User-Fund 2026-07-10: "natürlich müssen wir kill können" — KILL nur
    # aktiv, solange wirklich etwas läuft.
    idle = render._local_job_meta("a", _row("a"), {"status": "complete"})
    assert 'class="killbtn" hx-post="/-/ui/jobs/detail/a/kill" hx-target="#jobsdetail-live" hx-swap="outerHTML" disabled' in idle

    running = render._local_job_meta("a", _row("a"), None, live={"id": "jid1", "events": []})
    assert 'class="killbtn" hx-post="/-/ui/jobs/detail/a/kill" hx-target="#jobsdetail-live" hx-swap="outerHTML" title=' in running
    assert "killbtn\" hx-post=\"/-/ui/jobs/detail/a/kill\" hx-target=\"#jobsdetail-live\" hx-swap=\"outerHTML\" disabled" not in running


def test_local_live_output_empty_when_not_running():
    assert render._local_live_output(None) == ""


def test_local_live_output_renders_events():
    html = render._local_live_output(
        {"kind": "job", "events": [{"t": 1.0, "s": "out", "line": "hallo welt"}]})
    assert "hallo welt" in html and "Output" in html


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
    def __init__(self, *, schedules=None, run_journal=None, live=None) -> None:
        self._schedules = schedules or []
        self._run_journal = run_journal or []
        self._live = live or {}  # {slug: {"id":..., "events": [...]}}
        self.run_calls: list[dict] = []
        self.delete_calls: list[int] = []
        self.schedules_called = False

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
        # Spiegelt die echte HTTP-Route: slug filtert, wenn gesetzt.
        if slug is None:
            return self._run_journal
        return [r for r in self._run_journal if r.get("slug") == slug]

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


def _seed_schedule_md(root: Path, slug: str, schedule: str, payload: str) -> None:
    d = root / "vault" / "case" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text(
        f'---\nschedule: "{schedule}"\njob: "{payload}"\n---\n', encoding="utf-8")


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


def test_jobs_start_route_calls_client_run_and_returns_board(team_repo: Path, app_with):
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient()
    app, fake = app_with(client)
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/start/mein-testjob")
        assert r.status_code == 200
        assert fake.run_calls == [{"slug": "mein-testjob", "cmd": None}]
        assert 'id="jobsboard"' in r.text


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


def test_jobs_detail_kill_route_survives_nothing_running(team_repo: Path, app_with):
    app, _ = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/detail/nichts-los/kill")
        assert r.status_code == 200  # kein 500, auch wenn client.run_live_kill() 404t
