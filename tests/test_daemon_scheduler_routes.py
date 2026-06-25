"""Scheduler-gated DB-Routen: /-/rescan, /-/schedule, /-/job (PLAN-3 §3.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app


@pytest.fixture
def sched(team_repo: Path, monkeypatch):
    # Job-DB in eine isolierte data/ des Test-Repos legen.
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as client:
        yield client, team_repo


def _seed(repo_root: Path, rel: str, text: str) -> None:
    p = repo_root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_rescan_inserts_and_job_list(sched):
    client, root = sched
    _seed(root, "hello/README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    r = client.post("/-/rescan").json()
    assert r["inserted"] == 1
    jobs = client.get("/-/job").json()
    assert len(jobs) == 1
    assert jobs[0]["slug"] == "hello" and jobs[0]["status"] == "pending"
    assert jobs[0]["kind"] == "job"


def test_schedule_lists(sched):
    client, root = sched
    _seed(root, "daily.md", '---\nschedule: "0 9 * * *"\nclaude: "x"\n---\n')
    client.post("/-/rescan")
    items = client.get("/-/schedule").json()["schedules"]
    assert any(s["slug"] == "daily" and s["trigger"] == "0 9 * * *" for s in items)


def test_job_get_and_404(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.get("/-/job").json()[0]["id"]
    assert client.get(f"/-/job/{jid}").json()["slug"] == "a"
    r = client.get("/-/job/deadbeef")
    assert r.status_code == 404


def test_job_status_filter(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    assert len(client.get("/-/job", params={"status": "pending"}).json()) == 1
    assert client.get("/-/job", params={"status": "running"}).json() == []


def test_rescan_reports_collision(sched):
    client, root = sched
    _seed(root, "a.md", '---\nslug: dup\nschedule: now\njob: "x"\n---\n')
    _seed(root, "b.md", '---\nslug: dup\nschedule: now\njob: "y"\n---\n')
    r = client.post("/-/rescan").json()
    assert r["collisions"][0]["slug"] == "dup"
    assert client.get("/-/job").json() == []


def test_non_scheduler_job_is_501_stub(team_repo):
    # Ohne scheduler-Rolle bleibt der 3.0-Contract-Stub (501) stehen.
    app = create_app(roles.resolve({"worker"}))
    with TestClient(app) as client:
        assert client.get("/-/job").status_code == 501
        assert client.post("/-/rescan").status_code in (404, 405)
