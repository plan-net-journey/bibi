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


# ── node_id-Rekeying (Bibi4-Iteration, Connected-Clients-Screen) ────────────
# User-Fund: derselbe physische Client tauchte je nach Netzwerk mit anderem
# worker-Namen auf, alte Registry-Einträge blieben stale liegen. node_id ist
# jetzt der eigentliche Schlüssel, worker nur noch Anzeigename.


def test_registry_keys_by_node_id_not_worker_name():
    reg = WorkerRegistry()
    reg.heartbeat("air2024", "mac", "trunk", node_id="stable-uuid", now=0)
    # gleicher physischer Client, anderer Netzwerk-Hostname beim nächsten Beat:
    reg.heartbeat("air-home", "mac", "trunk", node_id="stable-uuid", now=10)
    lst = reg.list(now=10)
    assert len(lst) == 1  # eine Zeile, nicht zwei
    assert lst[0]["worker"] == "air-home"  # jüngster Anzeigename gewinnt
    assert lst[0]["connected_at"] == 0  # aber der ursprüngliche connected_at bleibt


def test_registry_different_node_ids_stay_separate_even_with_same_worker_name():
    reg = WorkerRegistry()
    reg.heartbeat("client", "h1", node_id="uuid-a", now=0)
    reg.heartbeat("client", "h2", node_id="uuid-b", now=0)
    assert len(reg.list(now=0)) == 2


def test_registry_falls_back_to_worker_name_key_without_node_id():
    # Rückwärtskompatibel: ein Heartbeat ohne node_id (älterer Client) verhält
    # sich wie vor dieser Änderung — worker-Name selbst ist der Schlüssel.
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", now=0)
    reg.heartbeat("w1", "h2", now=10)
    assert len(reg.list(now=10)) == 1


def test_registry_stores_git_user():
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", node_id="uuid-a", git_user="m.rau", now=0)
    assert reg.list(now=0)[0]["git_user"] == "m.rau"


def test_registry_stores_role():
    # Bibi4-Iteration, User-Fund: "Client Übersicht braucht die Rollen je
    # Client" — derselbe Präzedenzfall wie git_user/node_id.
    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", node_id="uuid-a", role="synchronizer,controller", now=0)
    assert reg.list(now=0)[0]["role"] == "synchronizer,controller"


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


def test_sweeper_never_reconciles_local_worker_even_if_registry_entry_stale(tmp_path: Path):
    # Live-Fund 2026-07-11 (sarasate Host+Client): ein fremder --connect-Knoten
    # kann zufällig unter demselben Namen wie der co-located lokale Worker
    # heartbeaten (Hostname-Kollision) und dessen Registry-Eintrag veralten
    # lassen — das darf den eigenen, echt laufenden lokalen Job NIE killen.
    import secrets
    import time as _t

    from bibi.daemon import job_db
    from bibi.daemon.sweeper import Sweeper
    p = tmp_path / "j.sqlite"
    conn = job_db.connect(p)
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, worker, "
        "enqueued_at, next_fire_at) VALUES (?,?,?,?,?, 'running', 'sarasate', ?, 0)",
        (jid, "x", "x.md", "job", "e", _t.time()))
    conn.close()
    reg = WorkerRegistry()
    reg.heartbeat("sarasate", "h", now=0)  # veralteter Fremd-Heartbeat, gleicher Name
    sw = Sweeper(db_path=p, registry=reg, autorun=False, local_worker_name="sarasate")
    assert sw.tick_once()["no_process"] == 0
    conn = job_db.connect(p)
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "running"
    conn.close()


def test_sweeper_still_reconciles_other_stale_workers_when_local_name_set(tmp_path: Path):
    # local_worker_name schützt nur den einen Namen — echte Fremd-Orphans
    # anderer Worker werden weiterhin normal reconciliert.
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
    reg.heartbeat("gone", "h", now=0)
    sw = Sweeper(db_path=p, registry=reg, autorun=False, local_worker_name="sarasate")
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


def test_worker_heartbeat_passes_node_id_and_git_user_through(sched):
    r = sched.post("/-/worker", json={
        "worker": "air2024", "host": "mac", "git_status": "trunk",
        "node_id": "stable-uuid", "git_user": "m.rau",
    })
    assert r.status_code == 200
    workers = sched.get("/-/worker").json()
    w = next(w for w in workers if w["worker"] == "air2024")
    assert w["node_id"] == "stable-uuid" and w["git_user"] == "m.rau"
    # gleiche node_id, anderer Anzeigename (Netzwerkwechsel) -> dieselbe Zeile
    sched.post("/-/worker", json={
        "worker": "air-home", "host": "mac", "node_id": "stable-uuid",
    })
    workers = sched.get("/-/worker").json()
    assert sum(1 for w in workers if w["node_id"] == "stable-uuid") == 1


def test_worker_heartbeat_passes_role_through(sched):
    r = sched.post("/-/worker", json={
        "worker": "air2024", "host": "mac", "node_id": "stable-uuid",
        "role": "synchronizer,controller",
    })
    assert r.status_code == 200
    workers = sched.get("/-/worker").json()
    w = next(w for w in workers if w["worker"] == "air2024")
    assert w["role"] == "synchronizer,controller"


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
