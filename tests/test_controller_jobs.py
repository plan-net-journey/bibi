"""Jobs-Screen (PLAN-17 Stufe 17.1/17.2): Lokal/Remote-Abgleich + Start-Button.

Funktioniert auch auf einem reinen Client (kein Scheduler/Worker im
Ruhezustand) — „Lokal" kommt aus einem read-only Discovery-Scan des Vaults,
„Remote" aus der (ggf. entfernten) Scheduler-URL. Reine Vergleichsfunktion
(``_compare_jobs``) + Rendering sind hier pur getestet; die Route-Tests unten
verdrahten einen gefakten ``ControllerClient`` (wie ``test_controller_daemon.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── _compare_jobs (reine Vergleichsfunktion) ─────────────────────────────────


def test_compare_same_trigger_and_payload_is_same():
    local = {"a": {"schedule": "* * * * *", "at": None, "payload": "echo hi"}}
    remote = [{"slug": "a", "trigger": "* * * * *", "payload": "echo hi"}]
    rows = render._compare_jobs(local, remote)
    assert rows == [{"slug": "a", "local": local["a"], "remote": remote[0],
                     "compare": "same", "diff_hint": ""}]


def test_compare_different_trigger_is_diff_with_hint():
    local = {"a": {"schedule": "*/10 * * * *", "at": None, "payload": "echo hi"}}
    remote = [{"slug": "a", "trigger": "7-59/15 * * * *", "payload": "echo hi"}]
    rows = render._compare_jobs(local, remote)
    assert rows[0]["compare"] == "diff"
    assert rows[0]["diff_hint"] == 'schedule: "*/10 * * * *" → "7-59/15 * * * *"'


def test_compare_different_payload_only_is_diff():
    local = {"a": {"schedule": "* * * * *", "at": None, "payload": "echo alt"}}
    remote = [{"slug": "a", "trigger": "* * * * *", "payload": "echo neu"}]
    rows = render._compare_jobs(local, remote)
    assert rows[0]["compare"] == "diff"
    assert rows[0]["diff_hint"] == "payload unterschiedlich"


def test_compare_local_only_when_not_reported_remotely():
    local = {"mein-testjob": {"schedule": "now", "at": None, "payload": "echo x"}}
    rows = render._compare_jobs(local, [])
    assert rows == [{"slug": "mein-testjob", "local": local["mein-testjob"], "remote": None,
                     "compare": "local_only", "diff_hint": ""}]


def test_compare_remote_only_when_not_discovered_locally():
    remote = [{"slug": "alter-cron-job", "trigger": "0 */3 * * *", "payload": "echo r"}]
    rows = render._compare_jobs({}, remote)
    assert rows == [{"slug": "alter-cron-job", "local": None, "remote": remote[0],
                     "compare": "remote_only", "diff_hint": ""}]


def test_compare_sorts_by_slug_across_union():
    local = {"z-job": {"schedule": "now", "at": None, "payload": "echo z"}}
    remote = [{"slug": "a-job", "trigger": "now", "payload": "echo a"}]
    rows = render._compare_jobs(local, remote)
    assert [r["slug"] for r in rows] == ["a-job", "z-job"]


# ── Rendering ─────────────────────────────────────────────────────────────


def test_jobs_table_start_button_enabled_when_locally_found():
    rows = render._compare_jobs(
        {"mein-testjob": {"schedule": "now", "at": None, "payload": "echo x"}}, [])
    html = render._jobs_table(rows, {}, now=100.0)
    assert 'hx-post="/-/ui/jobs/start/mein-testjob"' in html
    assert "disabled" not in html


def test_jobs_table_start_button_disabled_when_remote_only():
    rows = render._compare_jobs({}, [{"slug": "alter-cron-job", "trigger": "0 */3 * * *",
                                      "payload": "echo r"}])
    html = render._jobs_table(rows, {}, now=100.0)
    assert "disabled" in html
    assert "Keine lokale MD gefunden" in html


def test_jobs_table_shows_compare_chips():
    rows = render._compare_jobs(
        {"a": {"schedule": "* * * * *", "at": None, "payload": "x"}},
        [{"slug": "a", "trigger": "* * * * *", "payload": "x"}],
    )
    html = render._jobs_table(rows, {}, now=100.0)
    assert 'class="chip same"' in html and "identisch" in html


def test_jobs_table_shows_last_local_run_status():
    rows = render._compare_jobs(
        {"a": {"schedule": "now", "at": None, "payload": "x"}}, [])
    html = render._jobs_table(rows, {"a": {"status": "complete"}}, now=100.0)
    assert 'class="st complete"' in html


def test_jobs_table_empty_shows_placeholder():
    assert "keine Schedules" in render._jobs_table([], {}, now=100.0)


def test_run_history_renders_rows():
    runs = [{"slug": "mein-testjob", "status": "complete", "exit_code": 0,
            "exec_runtime": 3.2, "finished_at": 100.0}]
    html = render._run_history(runs, now=200.0)
    assert "mein-testjob" in html and "exit 0" in html and "3 s" in html


def test_run_history_empty_shows_placeholder():
    assert "noch keine lokalen Läufe" in render._run_history([], now=100.0)


def test_jobs_fragment_includes_hostlink_when_scheduler_url_given():
    html = render.jobs_fragment([], {}, [], scheduler_url="http://sarasate:8780", now=100.0)
    assert "sarasate:8780/-/ui/schedules" in html


def test_jobs_fragment_self_polls():
    html = render.jobs_fragment([], {}, [], now=100.0)
    assert 'id="jobsboard"' in html and 'hx-get="/-/ui/jobs/board"' in html


def test_jobs_page_has_header_and_nav():
    html = render.jobs_page([], {}, [], now=100.0)
    assert 'href="/-/"' in html and 'href="/-/ui/daemon"' in html
    assert "<title>bibi · Jobs</title>" in html


def test_screen_nav_includes_jobs_tab():
    html = render._screen_nav("Schedules")
    assert 'href="/-/ui/jobs"' in html and "Jobs" in html


# ── Route (gefakter Client + echtes Vault-Discovery) ─────────────────────────


class _FakeClient:
    def __init__(self, *, schedules=None, run_journal=None) -> None:
        self._schedules = schedules or []
        self._run_journal = run_journal or []
        self.run_calls: list[dict] = []

    def status(self) -> dict:
        return {}

    def schedules(self):
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


def test_jobs_route_compares_local_discovery_against_remote(team_repo: Path):
    # Rolle "scheduler" hier: Selbstaufruf (client.schedules()) IST schon die
    # Remote-Wahrheit für einen kombinierten Knoten (z. B. sarasate selbst).
    _seed_schedule_md(team_repo, "mein-testjob", "now", "echo x")
    client = _FakeClient(schedules=[{"slug": "alter-cron-job", "trigger": "0 */3 * * *",
                                     "payload": "echo r"}])
    app = create_app(roles.resolve({"controller", "scheduler"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert r.status_code == 200
        assert "mein-testjob" in r.text and "nur lokal" in r.text
        assert "alter-cron-job" in r.text and "nur remote" in r.text


def test_jobs_route_fetches_remote_via_scheduler_url_when_no_scheduler_role(
    team_repo: Path, monkeypatch,
):
    # PLAN-17 Befund 2 Punkt 3: ein reiner Client (keine scheduler-Rolle) holt
    # die Remote-Seite selbst beim konfigurierten Scheduler — nicht per
    # Selbstaufruf (der hätte auf so einem Knoten ohnehin nichts zu berichten).
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.example:8780")
    from bibi.daemon.scheduler_client import RemoteScheduler
    monkeypatch.setattr(
        RemoteScheduler, "schedules",
        lambda self: [{"slug": "alter-cron-job", "trigger": "0 */3 * * *", "payload": "echo r"}])
    app = create_app(roles.resolve({"controller"}), controller_client=_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert r.status_code == 200
        assert "alter-cron-job" in r.text and "nur remote" in r.text


def test_jobs_route_shows_local_run_history(team_repo: Path, app_with):
    client = _FakeClient(run_journal=[{"slug": "mein-testjob", "status": "complete",
                                       "exit_code": 0, "exec_runtime": 3.2,
                                       "finished_at": 100.0, "domain": "local"}])
    app, _ = app_with(client)
    with TestClient(app) as c:
        r = c.get("/-/ui/jobs")
        assert "Lokale Läufe" in r.text and "mein-testjob" in r.text


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
