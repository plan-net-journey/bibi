"""Live-Zwischenstand laufender lokaler /run-Ausführungen (PLAN-21 Befund 10,
2. Nachtrag — User-Fund 2026-07-09: "warum erscheinen keine Details während
des Laufes?"). Schnell: die reine Registry (worker.py) braucht keinen
Subprozess, und die Route-Logik (Hintergrund-Thread, 409 bei Doppelstart,
Bereinigung) wird gegen einen gefakten run_local() getestet statt gegen einen
echten — die echte End-to-End-Kette (echter Subprozess, echte output.jsonl)
deckt tests/test_run_local.py ab (@pytest.mark.slow)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles, worker
from bibi.daemon.app import create_app


@pytest.fixture(autouse=True)
def _reset_live_registry():
    # Modul-globaler State (wie job_db._dispatch_count) — zwischen Tests
    # zurücksetzen, sonst leaken Einträge früherer Tests in denselben
    # pytest-Prozess hinein.
    worker._local_runs_live.clear()
    yield
    worker._local_runs_live.clear()


# ── Reine Registry (worker.py) ───────────────────────────────────────────────


def test_local_run_start_and_live():
    worker.local_run_start("a", "jid1", "data/job/jid1/output.jsonl", "job", "echo hi")
    live = worker.local_run_live("a")
    assert live["id"] == "jid1" and live["output_ref"] == "data/job/jid1/output.jsonl"
    assert live["kind"] == "job" and "started_at" in live


def test_local_run_live_none_when_not_running():
    assert worker.local_run_live("nope") is None


def test_local_run_end_removes_entry():
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi")
    worker.local_run_end("a")
    assert worker.local_run_live("a") is None


def test_local_run_end_unknown_slug_is_noop():
    worker.local_run_end("never-started")  # kein KeyError


def test_local_runs_live_lists_all_slim():
    worker.local_run_start("a", "jid1", "ref-a", "job", "echo a")
    worker.local_run_start("b", "jid2", "ref-b", "job", "echo b")
    live = worker.local_runs_live()
    assert set(live) == {"a", "b"}
    assert live["a"]["id"] == "jid1" and "output_ref" not in live["a"]  # schlank


def test_local_run_live_returns_copy_not_live_reference():
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi")
    snap = worker.local_run_live("a")
    snap["id"] = "mutated"
    assert worker.local_run_live("a")["id"] == "jid1"


# ── POST /-/run + GET /-/run/live (gefakter run_local, kein echter Subprozess) ─


@pytest.fixture
def client_only(team_repo: Path):
    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        yield c


def _fake_run_local_factory(hold: threading.Event, *, jid: str = "fakejid"):
    def fake(*, slug=None, cmd=None, kind="job", register=None):
        register(jid, None)
        hold.wait(timeout=5)
        return {"id": jid, "slug": slug or "adhoc", "kind": kind, "status": "complete",
                "exit_code": 0, "output_ref": f"data/job/{jid}/output.jsonl", "commit": None}
    return fake


def _wait_until(predicate, *, timeout=2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_run_route_returns_running_immediately(client_only, monkeypatch):
    hold = threading.Event()
    monkeypatch.setattr("bibi.daemon.app.run_local", _fake_run_local_factory(hold))
    r = client_only.post("/-/run", json={"cmd": "irrelevant"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"id": "fakejid", "slug": "adhoc", "status": "running",
                    "output_ref": "data/job/fakejid/output.jsonl"}
    hold.set()  # Hintergrund-Thread freigeben, sonst Leak in den nächsten Test


def test_run_live_list_shows_entry_while_running_then_clears(client_only, monkeypatch):
    hold = threading.Event()
    monkeypatch.setattr("bibi.daemon.app.run_local", _fake_run_local_factory(hold))
    client_only.post("/-/run", json={"slug": "myjob", "cmd": "irrelevant"})
    assert "myjob" in client_only.get("/-/run/live").json()
    hold.set()
    assert _wait_until(lambda: "myjob" not in client_only.get("/-/run/live").json())


def test_run_live_detail_404_when_not_running(client_only):
    assert client_only.get("/-/run/live/nope").status_code == 404


def test_run_second_start_same_slug_is_409_while_first_still_running(client_only, monkeypatch):
    hold = threading.Event()
    monkeypatch.setattr("bibi.daemon.app.run_local", _fake_run_local_factory(hold))
    r1 = client_only.post("/-/run", json={"slug": "myjob", "cmd": "irrelevant"})
    assert r1.status_code == 200
    r2 = client_only.post("/-/run", json={"slug": "myjob", "cmd": "irrelevant"})
    assert r2.status_code == 409
    hold.set()
    assert _wait_until(lambda: "myjob" not in client_only.get("/-/run/live").json())
    # nach Ende wieder frei:
    r3 = client_only.post("/-/run", json={"slug": "myjob", "cmd": "irrelevant"})
    assert r3.status_code == 200
    hold.set()


def test_run_route_404_on_lookup_error_does_not_hang(client_only, monkeypatch):
    def fake_raises(*, slug=None, cmd=None, kind="job", register=None):
        raise LookupError(f"kein Schedule mit Slug {slug!r}")
    monkeypatch.setattr("bibi.daemon.app.run_local", fake_raises)
    r = client_only.post("/-/run", json={"slug": "nope"})
    assert r.status_code == 404
    assert worker.local_run_live("nope") is None  # nie registriert


def test_run_live_registry_cleared_even_on_background_exception(client_only, monkeypatch):
    # register() feuert, aber run_local() wirft danach (z. B. Commit-Fehler) —
    # local_run_end() muss trotzdem laufen (finally), sonst bleibt der Slug
    # für immer als "läuft" hängen.
    def fake_boom(*, slug=None, cmd=None, kind="job", register=None):
        register("jidboom", None)
        raise RuntimeError("boom")
    monkeypatch.setattr("bibi.daemon.app.run_local", fake_boom)
    client_only.post("/-/run", json={"slug": "boomjob", "cmd": "irrelevant"})
    assert _wait_until(lambda: worker.local_run_live("boomjob") is None)


def test_run_route_500s_immediately_on_generic_exception_before_spawn(client_only, monkeypatch):
    # Live-Fund 2026-07-10: eine Exception VOR register() (z. B. GitOpError
    # bei worktree.prepare() — hier mit einer generischen RuntimeError
    # nachgestellt, register() wird nie aufgerufen) ließ die Route bisher bis
    # zum vollen 30s-Timeout warten (504) statt sofort den echten Fehler
    # zurückzugeben. Muss 500 sein (kein "not found"-Fall wie LookupError).
    def fake_boom_before_spawn(*, slug=None, cmd=None, kind="job", register=None):
        raise RuntimeError("worktree conflict")  # register() nie aufgerufen
    monkeypatch.setattr("bibi.daemon.app.run_local", fake_boom_before_spawn)
    r = client_only.post("/-/run", json={"slug": "conflictjob", "cmd": "irrelevant"})
    assert r.status_code == 500
    assert r.json() == {"error": "worktree conflict"}
    assert worker.local_run_live("conflictjob") is None
