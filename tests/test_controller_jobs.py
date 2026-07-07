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


def _row(slug: str, *, git_status: str = "clean") -> dict:
    return {"slug": slug, "schedule": "now", "at": None, "payload": "echo x",
            "repo_path": f"vault/case/{slug}/README.md", "git_status": git_status}


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


def test_jobs_table_empty_shows_placeholder():
    html = render._jobs_table([], {}, now=100.0)
    assert "keine Job-MDs im Repository gefunden" in html


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


# ── Route (gefakter Client + echtes Vault-Discovery + echtes Git-Repo) ───────


class _FakeClient:
    def __init__(self, *, schedules=None, run_journal=None) -> None:
        self._schedules = schedules or []
        self._run_journal = run_journal or []
        self.run_calls: list[dict] = []
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

    def run_journal(self, **_):
        return self._run_journal

    def jobs(self, **_):
        return []

    def run(self, *, slug=None, cmd=None):
        self.run_calls.append({"slug": slug, "cmd": cmd})
        return {"id": "x", "status": "complete"}


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
