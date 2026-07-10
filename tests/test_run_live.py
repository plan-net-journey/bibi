"""Live-Zwischenstand laufender lokaler /run-Ausführungen (PLAN-21 Befund 10,
2. Nachtrag — User-Fund 2026-07-09: "warum erscheinen keine Details während
des Laufes?"). Schnell: die reine Registry (worker.py) braucht keinen
Subprozess, und die Route-Logik (Hintergrund-Thread, 409 bei Doppelstart,
Bereinigung) wird gegen einen gefakten run_local() getestet statt gegen einen
echten — die echte End-to-End-Kette (echter Subprozess, echte output.jsonl)
deckt tests/test_run_local.py ab (@pytest.mark.slow)."""

from __future__ import annotations

import json
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
    worker._local_runs_procs.clear()
    yield
    worker._local_runs_live.clear()
    worker._local_runs_procs.clear()


class _FakeProc:
    """Minimaler subprocess.Popen-Stand-in für local_run_kill()-Tests — kein
    echter Subprozess nötig, nur .poll()/.pid, die _terminate() (worker.py)
    tatsächlich anfasst."""
    def __init__(self, *, alive: bool = True) -> None:
        self.pid = 999999  # garantiert kein echter Prozess — os.killpg no-opt defensiv
        self._alive = alive
        self.terminated = False

    def poll(self):
        return None if self._alive else 0


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


# ── local_run_signal_state() (Ausbau User-Fund 2026-07-10: awaiting/app_url ──
# für lokale App-Jobs — vorher gingen deren BIBI-Signale spurlos verloren,
# jetzt landen sie als "signal"-Events in output.jsonl, s. _record_signal()
# in bibi/wrapper/__init__.py.) ───────────────────────────────────────────────


def _sig_event(sig: dict) -> dict:
    return {"t": 0.0, "s": "signal", "line": json.dumps(sig)}


def test_signal_state_defaults_to_running_with_no_events():
    state = worker.local_run_signal_state([])
    assert state == {"status": "running", "app_url": None, "demand": None}


def test_signal_state_awaiting_sets_status_demand_and_app_url():
    events = [_sig_event({"name": "awaiting", "input_request": "ja/j?",
                          "input_format": "text", "port": 9100})]
    state = worker.local_run_signal_state(events)
    assert state["status"] == "awaiting"
    assert state["app_url"] == "http://localhost:9100/"
    assert state["demand"] == {"input_request": "ja/j?", "input_format": "text", "port": 9100}


def test_signal_state_awaiting_without_port_leaves_app_url_unset():
    events = [_sig_event({"name": "awaiting", "input_request": "?"})]
    state = worker.local_run_signal_state(events)
    assert state["status"] == "awaiting"
    assert state["app_url"] is None


def test_signal_state_running_after_awaiting_clears_demand_keeps_app_url():
    # Der Port bleibt für die Lebensdauer des Prozesses gültig — nur der
    # Eingabe-Bedarf (demand) verschwindet, wenn der Job weiterläuft.
    events = [
        _sig_event({"name": "awaiting", "input_request": "?", "port": 9100}),
        _sig_event({"name": "running"}),
    ]
    state = worker.local_run_signal_state(events)
    assert state["status"] == "running"
    assert state["app_url"] == "http://localhost:9100/"
    assert state["demand"] is None


def test_signal_state_app_url_uses_configured_public_host(monkeypatch):
    # PLAN-22 Befund 6: die Adresse war zuvor hart auf 127.0.0.1 kodiert — auf
    # einem Remote-Host (z. B. sarasate) für einen Client-Browser tot.
    monkeypatch.setenv("BIBI_PUBLIC_HOST", "sarasate.tail9f9173.ts.net")
    events = [_sig_event({"name": "awaiting", "input_request": "?", "port": 9100})]
    state = worker.local_run_signal_state(events)
    assert state["app_url"] == "http://sarasate.tail9f9173.ts.net:9100/"


def test_signal_state_app_register_sets_app_url_without_awaiting():
    events = [_sig_event({"name": "app_register", "port": 9200})]
    state = worker.local_run_signal_state(events)
    assert state["status"] == "running"
    assert state["app_url"] == "http://localhost:9200/"
    assert state["demand"] is None


def test_signal_state_ignores_non_signal_events():
    events = [{"t": 0.0, "s": "phase", "line": "worktree: bereit"},
             {"t": 0.0, "s": "out", "line": "hallo"}]
    state = worker.local_run_signal_state(events)
    assert state == {"status": "running", "app_url": None, "demand": None}


def test_signal_state_malformed_signal_line_is_skipped_not_crashed():
    events = [{"t": 0.0, "s": "signal", "line": "{ungültiges json"}]
    state = worker.local_run_signal_state(events)
    assert state == {"status": "running", "app_url": None, "demand": None}


# ── local_run_kill() (User-Fund 2026-07-10: "natürlich müssen wir kill können") ─


def test_local_run_kill_terminates_and_returns_true(monkeypatch):
    # _terminate() (worker.py) fragt _is_container() ab, was ohne Mock den
    # globalen Knoten-Default liest (auf diesem Mac: container) und einen
    # echten `docker stop`-Subprozess anstoßen würde — hier wie in
    # test_worker_container.py hermetisch weggemockt.
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: None)
    proc = _FakeProc(alive=True)
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi", proc)
    assert worker.local_run_kill("a") is True


def test_local_run_kill_false_when_nothing_running():
    assert worker.local_run_kill("nope") is False


def test_local_run_kill_false_when_no_proc_handle():
    # register()-Callback nie erreicht (z. B. Fehler vor dem Spawn) — kein
    # Prozess-Handle vorhanden, kill() darf nicht crashen, nur False liefern.
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi")  # proc=None
    assert worker.local_run_kill("a") is False


def test_local_run_kill_false_when_proc_already_exited():
    proc = _FakeProc(alive=False)  # .poll() != None → schon beendet
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi", proc)
    assert worker.local_run_kill("a") is False


def test_local_run_end_clears_proc_handle_too(monkeypatch):
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: None)
    proc = _FakeProc(alive=True)
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi", proc)
    worker.local_run_end("a")
    assert worker.local_run_kill("a") is False  # kein Handle mehr da


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


def test_run_live_detail_includes_signal_derived_status_and_app_url(client_only, team_repo):
    # Ausbau User-Fund 2026-07-10: /-/run/live/{slug} muss awaiting/app_url aus
    # den "signal"-Events in output.jsonl ableiten (worker.local_run_signal_
    # state()), nicht nur "running" pauschal für jeden laufenden lokalen Job.
    out_ref = "data/job/jid1/output.jsonl"
    out_path = team_repo / out_ref
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"t": 0.0, "s": "signal",
         "line": json.dumps({"name": "awaiting", "input_request": "ja/j?", "port": 9100})}
    ) + "\n")
    worker.local_run_start("myjob", "jid1", out_ref, "job", "python3 app.py")
    body = client_only.get("/-/run/live/myjob").json()
    assert body["status"] == "awaiting"
    assert body["app_url"] == "http://localhost:9100/"
    assert body["demand"] == {"input_request": "ja/j?", "port": 9100}


def test_run_live_detail_status_running_when_no_signal_events(client_only, team_repo):
    out_ref = "data/job/jid2/output.jsonl"
    (team_repo / "data" / "job" / "jid2").mkdir(parents=True)
    worker.local_run_start("plainjob", "jid2", out_ref, "job", "echo hi")
    body = client_only.get("/-/run/live/plainjob").json()
    assert body["status"] == "running"
    assert body["app_url"] is None
    assert body["demand"] is None


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


# ── POST /-/run/live/{slug}/kill (User-Fund 2026-07-10) ─────────────────────


def test_run_live_kill_route_404_when_nothing_running(client_only):
    r = client_only.post("/-/run/live/nope/kill")
    assert r.status_code == 404


def test_run_live_kill_route_signals_running_job(client_only, monkeypatch):
    # on_spawn() (app.py) reicht proc nur über register() durch — hier direkt
    # über worker.local_run_start() nachgestellt, denn der gefakte run_local()
    # in diesem Modul ruft register() ohne echten Prozess auf. Der laufende
    # Slug wird stattdessen unabhängig vom /-/run-Fake registriert, wie es
    # on_spawn() in Produktion via local_run_start(..., proc) täte.
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: None)
    proc = _FakeProc(alive=True)
    worker.local_run_start("myjob", "jid1", "data/job/jid1/output.jsonl", "job", "echo hi", proc)
    r = client_only.post("/-/run/live/myjob/kill")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "signaled": True}


def test_run_live_kill_route_404_when_proc_already_exited(client_only):
    proc = _FakeProc(alive=False)
    worker.local_run_start("myjob", "jid1", "ref", "job", "echo hi", proc)
    r = client_only.post("/-/run/live/myjob/kill")
    assert r.status_code == 404
