"""Worker-Routen: /-/job/{id}/status|log|out|stream|kill + /-/journal (§4.5/§1.4)."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi import repo
from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app
from bibi.daemon.worker import Worker
from bibi.wrapper import output


@pytest.fixture
def client(team_repo: Path):
    # autopoll=False ⇒ nur Routen bedienen, kein Pull-Loop (deterministisch).
    w = Worker(autopoll=False, worker_name="w1")
    app = create_app(roles.resolve({"scheduler", "worker"}), worker=w)
    with TestClient(app) as c:
        yield c


def _seed_complete(lines: list[tuple[str, str]]) -> str:
    jid = secrets.token_hex(4)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, host, "
            "worker, output_ref, enqueued_at) VALUES (?,?,?,?,?, 'complete', 'h','w1',?,?)",
            (jid, "run1", "run1.md", "job", "echo", f"data/job/{jid}/output.jsonl", time.time()),
        )
    finally:
        conn.close()
    out = repo.data() / "job" / jid / "output.jsonl"
    for stream, line in lines:
        output.append(out, stream, line)
    return jid


def _seed_status(status: str) -> str:
    jid = secrets.token_hex(4)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, enqueued_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (jid, "s", "s.md", "job", "sleep 9", status, time.time()),
        )
    finally:
        conn.close()
    return jid


def test_log_returns_raw_jsonl(client):
    jid = _seed_complete([("out", "hallo"), ("out", "fertig")])
    r = client.get(f"/-/job/{jid}/log")
    assert r.status_code == 200
    assert "hallo" in r.text and "fertig" in r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")


def test_stream_replays_all(client):
    jid = _seed_complete([("out", "hallo"), ("err", "warnung"), ("out", "fertig")])
    r = client.get(f"/-/job/{jid}/stream")
    assert r.status_code == 200
    assert "hallo" in r.text and "warnung" in r.text and "fertig" in r.text


def test_out_filters_stream(client):
    jid = _seed_complete([("out", "hallo"), ("err", "warnung")])
    r = client.get(f"/-/job/{jid}/out")
    assert "hallo" in r.text and "warnung" not in r.text


def test_err_filters_stream(client):
    jid = _seed_complete([("out", "hallo"), ("err", "warnung")])
    r = client.get(f"/-/job/{jid}/err")
    assert "warnung" in r.text and "hallo" not in r.text


def test_status_endpoint(client):
    jid = _seed_complete([("out", "x")])
    r = client.get(f"/-/job/{jid}/status")
    assert r.status_code == 200 and r.json()["status"] == "complete"
    assert client.get("/-/job/deadbeef/status").status_code == 404


def test_kill_running_job(client):
    jid = _seed_status("running")
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "killed" and body["signaled"] is False  # kein echter Prozess
    assert client.get(f"/-/job/{jid}/status").json()["reason"] == "by_user"


def test_kill_pending_is_409(client):
    jid = _seed_status("pending")  # pending → killed ist verboten (§5.4)
    assert client.post(f"/-/job/{jid}/kill").status_code == 409


def test_kill_missing_is_404(client):
    assert client.post("/-/job/deadbeef/kill").status_code == 404


def test_reset_complete_to_pending(client):
    jid = _seed_complete([("out", "x")])
    r = client.post(f"/-/job/{jid}/reset")
    assert r.status_code == 200
    assert client.get(f"/-/job/{jid}/status").json()["status"] == "pending"


def test_reset_running_is_409(client):
    jid = _seed_status("running")  # running ist kein Terminalzustand
    assert client.post(f"/-/job/{jid}/reset").status_code == 409


def test_reset_missing_is_404(client):
    assert client.post("/-/job/deadbeef/reset").status_code == 404


def test_journal_lists_terminal_runs(client):
    # Einen Lauf simulieren: running → complete schreibt eine Journal-Zeile.
    jid = _seed_status("running")
    client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    rows = client.get("/-/journal").json()
    assert any(r["slug"] == "s" and r["status"] == "complete" for r in rows)


def test_worker_routes_absent_without_worker_role(team_repo):
    app = create_app(roles.resolve({"scheduler"}))  # kein worker
    with TestClient(app) as c:
        # 3.0-Contract-Stub bleibt (501), keine echte Worker-Route
        assert c.get("/-/job/x/log").status_code == 501