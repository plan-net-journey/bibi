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
    _seed(root, "daily.md", '---\nschedule: "0 9 * * *"\njob: "claude: x"\n---\n')
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


def test_scheduler_next_reserves_priority_first(sched):
    client, root = sched
    _seed(root, "z/README.md", '---\nschedule: now\njob: "echo z"\npriority: 0\n---\n')
    _seed(root, "p/README.md", '---\nschedule: now\njob: "echo p"\npriority: 5\n---\n')
    client.post("/-/rescan")
    r = client.post("/-/scheduler/next").json()
    assert r["slug"] == "p"                       # höchste Priorität zuerst
    assert r["kind"] == "job" and r["payload"] == "echo p"
    # reservierter Job ist jetzt running
    jobs = {j["slug"]: j for j in client.get("/-/job").json()}
    assert jobs["p"]["status"] == "running"
    assert jobs["z"]["status"] == "pending"


def test_scheduler_next_empty_is_204(sched):
    client, _ = sched
    assert client.post("/-/scheduler/next").status_code == 204


def test_scheduler_next_paused_in_maintenance(sched, monkeypatch):
    # Wartungsmodus pausiert auch den Remote-Dispatch (Route gibt 204).
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    monkeypatch.setattr("bibi.state.get_maintenance", lambda: True)
    assert client.post("/-/scheduler/next").status_code == 204
    # ohne Wartung wird derselbe Job reserviert
    monkeypatch.setattr("bibi.state.get_maintenance", lambda: False)
    assert client.post("/-/scheduler/next").json()["slug"] == "a"


def test_scheduler_status_running_to_complete(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    r = client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    assert r.status_code == 200
    assert client.get(f"/-/job/{jid}").json()["status"] == "complete"


def test_scheduler_status_illegal_is_409(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.get("/-/job").json()[0]["id"]   # noch pending
    r = client.post(f"/-/scheduler/status/{jid}", json={"status": "complete"})
    assert r.status_code == 409


def test_scheduler_status_404(sched):
    client, _ = sched
    r = client.post("/-/scheduler/status/deadbeef", json={"status": "running"})
    assert r.status_code == 404


def test_non_scheduler_job_is_501_stub(team_repo):
    # Ohne scheduler-Rolle bleibt der 3.0-Contract-Stub (501) stehen.
    app = create_app(roles.resolve({"worker"}))
    with TestClient(app) as client:
        assert client.get("/-/job").status_code == 501
        assert client.post("/-/scheduler/next").status_code == 501
        assert client.post("/-/rescan").status_code in (404, 405)


# ── Slice 9.4: HITL-Relay GET/POST /-/job/{id}/input ─────────────────────────


def test_job_input_relay_no_wrapper_url_returns_503(sched):
    """GET /-/job/{id}/input ohne Wrapper-URL → 503 (kein Relay konfiguriert)."""
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.get("/-/job").json()[0]["id"]
    # Job ist pending — kein wrapper_url gesetzt → 503
    r = client.get(f"/-/job/{jid}/input")
    assert r.status_code == 503


def test_job_input_post_relay_no_wrapper_url_returns_503(sched):
    """POST /-/job/{id}/input ohne Wrapper-URL → 503."""
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.get("/-/job").json()[0]["id"]
    r = client.post(f"/-/job/{jid}/input", json={"answer": "Ja"})
    assert r.status_code == 503


def test_scheduler_status_awaiting_stores_wrapper_url(sched, free_tcp_port):
    """POST /-/scheduler/status awaiting + wrapper_url → wrapper_url gespeichert."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    # Mini-Wrapper-Server, der GET /-/job/{id}/input beantwortet
    demand = {"prompt": "Test?", "choices": ["Ja"], "mediated": True}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(demand).encode())

        def log_message(self, *_):
            pass

    srv = HTTPServer(("127.0.0.1", free_tcp_port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client, root = sched
        _seed(root, "a/README.md", '---\nschedule: now\njob: echo x\n---\n')
        client.post("/-/rescan")
        jid = client.post("/-/scheduler/next").json()["id"]
        # running → awaiting mit wrapper_url
        client.post(f"/-/scheduler/status/{jid}", json={"status": "running"})
        r = client.post(f"/-/scheduler/status/{jid}", json={
            "status": "awaiting",
            "wrapper_url": f"http://127.0.0.1:{free_tcp_port}",
        })
        assert r.status_code == 200

        # Daemon kann jetzt Relay aufbauen
        r2 = client.get(f"/-/job/{jid}/input")
        assert r2.status_code == 200
        data = r2.json()
        assert data["prompt"] == "Test?"
    finally:
        srv.shutdown()
