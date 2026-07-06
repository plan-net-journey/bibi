"""Worker-Verbund: Registry, /-/worker, Secret-Auth, RemoteScheduler (PLAN-3 §3.6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.daemon.scheduler_client import RemoteScheduler
from bibi.daemon.worker_registry import WorkerRegistry


# ── WorkerRegistry (rein) ────────────────────────────────────────────────────


def test_registry_heartbeat_and_list():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", "trunk", now=100)
    lst = reg.list(now=100)
    assert len(lst) == 1
    assert lst[0]["worker"] == "w1" and lst[0]["host"] == "h1"
    assert lst[0]["git_status"] == "trunk" and lst[0]["stale"] is False


def test_registry_stale_after_timeout():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", now=0)
    assert reg.list(now=1000, stale_after=60)[0]["stale"] is True
    assert reg.fresh_count(now=1000, stale_after=60) == 0


def test_registry_update_keeps_connected_at():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", now=0)
    reg.heartbeat("w1", "h2", now=10)
    lst = reg.list(now=10)
    assert len(lst) == 1
    assert lst[0]["host"] == "h2" and lst[0]["connected_at"] == 0


def test_registry_stale_workers():
    reg = WorkerRegistry()
    reg.heartbeat("dead", "h", now=0)
    reg.heartbeat("alive", "h", now=100)
    assert reg.stale_workers(now=100, stale_after=60) == {"dead"}


def test_sweeper_reconciles_no_process(tmp_path: Path):
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'gone', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    conn.close()
    reg = WorkerRegistry()
    reg.heartbeat("gone", "h", now=0)  # last_heartbeat=0 ⇒ stale gegen now
    sw = Sweeper(db_path=p, registry=reg, autorun=False)
    assert sw.tick_once()["no_process"] == 1
    conn = job_db.connect(p)
    row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "killed" and row["reason"] == "no_process"
    conn.close()


# ── /-/worker-Routen (Scheduler-Rolle) ───────────────────────────────────────


@pytest.fixture
def sched(team_repo: Path):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as c:
        yield c


def test_worker_heartbeat_then_listed(sched):
    r = sched.post("/-/worker", json={"worker": "w1", "host": "air", "git_status": "trunk"})
    assert r.status_code == 200
    workers = sched.get("/-/worker").json()
    assert any(w["worker"] == "w1" and w["host"] == "air" for w in workers)


def test_status_includes_workers(sched):
    sched.post("/-/worker", json={"worker": "w2", "host": "box"})
    status = sched.get("/-/status").json()
    assert any(w["worker"] == "w2" for w in status["workers"])


# ── Shared-Secret-Auth (§1.3) ────────────────────────────────────────────────


def test_secret_required_when_configured(team_repo: Path, monkeypatch):
    monkeypatch.setenv("BIBI_CONNECT_SECRET", "s3cret")
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as c:
        # ohne Header → 401
        assert c.post("/-/scheduler/next").status_code == 401
        assert c.post("/-/worker", json={"worker": "w", "host": "h"}).status_code == 401
        # mit korrektem Header → erlaubt
        h = {"X-Bibi-Secret": "s3cret"}
        assert c.post("/-/scheduler/next", headers=h).status_code == 204
        assert c.post("/-/worker", json={"worker": "w", "host": "h"}, headers=h).status_code == 200


def test_no_secret_means_open(sched):
    # ohne konfiguriertes Secret bleibt der Verbund offen (Loopback/Trust-Netz)
    assert sched.post("/-/scheduler/next").status_code == 204


# ── RemoteScheduler (HTTP-Mapping, _post gemockt) ────────────────────────────


def test_remote_next_maps_200_and_204(monkeypatch):
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_post", lambda p, pl: (204, None))
    assert rs.next() is None
    monkeypatch.setattr(rs, "_post", lambda p, pl: (200, {"id": "j"}))
    assert rs.next(worker="w")["id"] == "j"


def test_remote_report_maps_codes(monkeypatch):
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_post", lambda p, pl: (200, None))
    assert rs.report("id", status="complete") == "ok"
    monkeypatch.setattr(rs, "_post", lambda p, pl: (409, None))
    assert rs.report("id", status="complete") == "invalid"
    monkeypatch.setattr(rs, "_post", lambda p, pl: (404, None))
    assert rs.report("id", status="complete") == "not_found"


def test_remote_report_omits_none_fields(monkeypatch):
    rs = RemoteScheduler("http://x")
    captured: dict = {}

    def fake(path, payload):
        captured.update(payload)
        return (200, None)

    monkeypatch.setattr(rs, "_post", fake)
    rs.report("id", status="complete", reason=None, exit_code=0)
    assert "reason" not in captured  # None weggelassen
    assert captured["exit_code"] == 0 and captured["status"] == "complete"


def test_remote_schedules_gets_schedule_list(monkeypatch):
    # PLAN-17 Befund 2 Punkt 3: Jobs-Screen-Remote-Seite braucht einen GET-
    # Wrapper (next/report/register sind reine POST-Verben für den Dispatch-Pfad).
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_get", lambda p: {"schedules": [{"slug": "a"}]})
    assert rs.schedules() == [{"slug": "a"}]


def test_remote_schedules_empty_on_bad_shape(monkeypatch):
    rs = RemoteScheduler("http://x")
    monkeypatch.setattr(rs, "_get", lambda p: None)
    assert rs.schedules() == []
