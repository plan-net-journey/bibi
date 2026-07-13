"""Live-Zwischenstand laufender gepinnter /run-Ausführungen (PLAN-21 Befund 10,
2. Nachtrag; PLAN-28: jobs-tabellen-basiert statt In-Memory-Dict).

Schnell: die reine Registry-Query (worker.py) braucht keinen Subprozess, und
die Route-Logik (409 bei Doppelstart, Fehlerpfade) wird gegen einen gefakten
run_pinned() getestet statt gegen einen echten — die echte End-to-End-Kette
(echter Subprozess, echte output.jsonl) deckt tests/test_run_local.py ab
(@pytest.mark.slow)."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import job_db, roles, worker
from bibi.daemon.app import create_app
from bibi.wrapper import output


@pytest.fixture(autouse=True)
def _reset_proc_registry():
    # Modul-globaler State (wie job_db._dispatch_count) — zwischen Tests
    # zurücksetzen, sonst leaken Einträge früherer Tests in denselben
    # pytest-Prozess hinein. Die "läuft gerade?"-Metadaten selbst kommen
    # jetzt aus der jobs-Tabelle (pinned_host), s. Modul-Kommentar in worker.py.
    worker._local_runs_procs.clear()
    yield
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


def _seed_pinned_job(root: Path, bucket_slug: str, *, status: str = "running",
                     output_ref: str | None = None, host: str | None = None) -> str:
    """Legt eine echte, gepinnte ``jobs``-Zeile an (PLAN-28: die reale Quelle
    für local_run_live()/local_runs_live()) — analog zu run_pinned()s eigenem
    INSERT, nur direkt per SQL für Tests, ohne echten Dispatch."""
    import socket
    host = host or socket.gethostname()
    jid = secrets.token_hex(4)
    unique_slug = f"{bucket_slug}:{secrets.token_hex(2)}"
    output_ref = output_ref or f"data/job/{jid}/output.jsonl"
    conn = job_db.connect(root / "data" / "jobs.sqlite")
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
            "pinned_host, output_ref, started_at, enqueued_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?)",
            (jid, unique_slug, unique_slug, "job", "echo hi", status, host,
             output_ref, time.time(), time.time()),
        )
    finally:
        conn.close()
    return jid


# ── Reine Registry-Query (worker.py, jobs-tabellen-basiert) ─────────────────


def test_local_run_live_finds_seeded_pinned_row(team_repo: Path):
    jid = _seed_pinned_job(team_repo, "a", output_ref="data/job/jid1/output.jsonl")
    live = worker.local_run_live("a")
    assert live["id"] == jid and live["output_ref"] == "data/job/jid1/output.jsonl"
    assert live["kind"] == "job" and "started_at" in live


def test_local_run_live_none_when_not_running(team_repo: Path):
    assert worker.local_run_live("nope") is None


def test_local_run_live_ignores_terminal_status(team_repo: Path):
    _seed_pinned_job(team_repo, "a", status="complete")
    assert worker.local_run_live("a") is None


def test_local_run_live_ignores_other_hosts_pinned_jobs(team_repo: Path):
    _seed_pinned_job(team_repo, "a", host="sarasate")
    assert worker.local_run_live("a", host="mac") is None


def test_local_runs_live_lists_all_by_bucket_slug(team_repo: Path):
    _seed_pinned_job(team_repo, "a")
    _seed_pinned_job(team_repo, "b")
    live = worker.local_runs_live()
    assert set(live) == {"a", "b"}
    assert "id" in live["a"] and "started_at" in live["a"] and "status" in live["a"]


def test_local_runs_live_includes_awaiting_status(team_repo: Path):
    # PLAN-27 Befund 4 / PLAN-28: status kommt jetzt direkt aus der jobs-Zeile,
    # kein Output-Read mehr nötig.
    _seed_pinned_job(team_repo, "a", status="awaiting")
    live = worker.local_runs_live()
    assert live["a"]["status"] == "awaiting"


def test_local_runs_live_excludes_terminal_rows(team_repo: Path):
    _seed_pinned_job(team_repo, "a", status="complete")
    assert worker.local_runs_live() == {}


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
# PLAN-28: braucht jetzt eine echte gepinnte jobs-Zeile (lokal_run_live()) UND
# den Proc-Handle (_local_runs_procs, weiterhin per local_run_start() gesetzt).


def test_local_run_kill_terminates_and_returns_true(monkeypatch, team_repo: Path):
    # _terminate() (worker.py) fragt _is_container() ab, was ohne Mock den
    # globalen Knoten-Default liest (auf diesem Mac: container) und einen
    # echten `docker stop`-Subprozess anstoßen würde — hier wie in
    # test_worker_container.py hermetisch weggemockt.
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: None)
    _seed_pinned_job(team_repo, "a", output_ref="ref")
    proc = _FakeProc(alive=True)
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi", proc)
    assert worker.local_run_kill("a") is True


def test_local_run_kill_false_when_nothing_running(team_repo: Path):
    assert worker.local_run_kill("nope") is False


def test_local_run_kill_false_when_no_proc_handle(team_repo: Path):
    # register()-Callback nie erreicht (z. B. Fehler vor dem Spawn) — kein
    # Prozess-Handle vorhanden, kill() darf nicht crashen, nur False liefern.
    _seed_pinned_job(team_repo, "a", output_ref="ref")
    assert worker.local_run_kill("a") is False


def test_local_run_kill_false_when_proc_already_exited(team_repo: Path):
    _seed_pinned_job(team_repo, "a", output_ref="ref")
    proc = _FakeProc(alive=False)  # .poll() != None → schon beendet
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi", proc)
    assert worker.local_run_kill("a") is False


def test_local_run_kill_false_when_job_row_not_live(team_repo: Path):
    # Proc-Handle vorhanden, aber die jobs-Zeile ist schon terminal (z. B. der
    # Wrapper hat gerade selbst "complete" gemeldet) — kein Kill mehr nötig.
    _seed_pinned_job(team_repo, "a", status="complete", output_ref="ref")
    proc = _FakeProc(alive=True)
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi", proc)
    assert worker.local_run_kill("a") is False


def test_local_run_end_clears_proc_handle_too(monkeypatch, team_repo: Path):
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: None)
    _seed_pinned_job(team_repo, "a", output_ref="ref")
    proc = _FakeProc(alive=True)
    worker.local_run_start("a", "jid1", "ref", "job", "echo hi", proc)
    worker.local_run_end("a")
    assert worker.local_run_kill("a") is False  # kein Handle mehr da


# ── POST /-/run + GET /-/run/live (gefakter run_pinned, kein echter Subprozess) ─


@pytest.fixture
def client_only(team_repo: Path):
    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        yield c


def _fake_run_pinned_factory(*, jid: str = "fakejid", slug_suffix: str = "abcd"):
    def fake(*, slug=None, cmd=None, kind="job", register=None, **_kw):
        bucket = slug or "adhoc"
        if register is not None:
            register(jid, None)
        return {"id": jid, "slug": f"{bucket}:{slug_suffix}", "kind": kind,
                "output_ref": f"data/job/{jid}/output.jsonl"}
    return fake


def test_run_route_returns_running_immediately(client_only, monkeypatch):
    # PLAN-28: run_pinned() kehrt synchron zurück (detach=True, kein
    # Hintergrund-Thread mehr in der Route nötig) — kein Warten/Event mehr.
    monkeypatch.setattr("bibi.daemon.app.run_pinned", _fake_run_pinned_factory())
    r = client_only.post("/-/run", json={"cmd": "irrelevant"})
    assert r.status_code == 200
    assert r.json() == {"id": "fakejid", "slug": "adhoc", "status": "running",
                        "output_ref": "data/job/fakejid/output.jsonl"}


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
    _seed_pinned_job(team_repo, "myjob", output_ref=out_ref)
    body = client_only.get("/-/run/live/myjob").json()
    assert body["status"] == "awaiting"
    assert body["app_url"] == "http://localhost:9100/"
    assert body["demand"] == {"input_request": "ja/j?", "port": 9100}


def test_run_live_detail_status_running_when_no_signal_events(client_only, team_repo):
    out_ref = "data/job/jid2/output.jsonl"
    (team_repo / "data" / "job" / "jid2").mkdir(parents=True)
    _seed_pinned_job(team_repo, "plainjob", output_ref=out_ref)
    body = client_only.get("/-/run/live/plainjob").json()
    assert body["status"] == "running"
    assert body["app_url"] is None
    assert body["demand"] is None


def test_run_second_start_same_slug_is_409_while_first_still_running(client_only, team_repo,
                                                                      monkeypatch):
    _seed_pinned_job(team_repo, "myjob")  # simuliert den ersten, noch laufenden Lauf
    r2 = client_only.post("/-/run", json={"slug": "myjob", "cmd": "irrelevant"})
    assert r2.status_code == 409

    # nach Ende (Zeile terminal) wieder frei:
    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    conn.execute("UPDATE jobs SET status='complete' WHERE slug LIKE 'myjob:%'")
    conn.close()
    monkeypatch.setattr("bibi.daemon.app.run_pinned", _fake_run_pinned_factory())
    r3 = client_only.post("/-/run", json={"slug": "myjob", "cmd": "irrelevant"})
    assert r3.status_code == 200


def test_run_route_404_on_lookup_error(client_only, monkeypatch):
    def fake_raises(*, slug=None, cmd=None, kind="job", register=None, **_kw):
        raise LookupError(f"kein Schedule mit Slug {slug!r}")
    monkeypatch.setattr("bibi.daemon.app.run_pinned", fake_raises)
    r = client_only.post("/-/run", json={"slug": "nope"})
    assert r.status_code == 404
    assert worker.local_run_live("nope") is None  # nie registriert


def test_run_route_500s_on_generic_exception(client_only, monkeypatch):
    # Live-Fund 2026-07-10 (galt für den alten Hintergrund-Thread-Pfad, gilt
    # als API-Vertrag weiter): ein Startfehler (z. B. GitOpError bei
    # worktree.prepare()) muss als 500 mit der echten Fehlermeldung zurück-
    # kommen, kein "not found"-Fall wie LookupError.
    def fake_boom(*, slug=None, cmd=None, kind="job", register=None, **_kw):
        raise RuntimeError("worktree conflict")
    monkeypatch.setattr("bibi.daemon.app.run_pinned", fake_boom)
    r = client_only.post("/-/run", json={"slug": "conflictjob", "cmd": "irrelevant"})
    assert r.status_code == 500
    assert r.json() == {"error": "worktree conflict"}


# ── POST /-/run/live/{slug}/kill (User-Fund 2026-07-10) ─────────────────────


def test_run_live_kill_route_404_when_nothing_running(client_only):
    r = client_only.post("/-/run/live/nope/kill")
    assert r.status_code == 404


def test_run_live_kill_route_signals_running_job(client_only, team_repo, monkeypatch):
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: None)
    _seed_pinned_job(team_repo, "myjob", output_ref="data/job/jid1/output.jsonl")
    proc = _FakeProc(alive=True)
    worker.local_run_start("myjob", "jid1", "data/job/jid1/output.jsonl", "job", "echo hi", proc)
    r = client_only.post("/-/run/live/myjob/kill")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "signaled": True}


def test_run_live_kill_route_404_when_proc_already_exited(client_only, team_repo):
    _seed_pinned_job(team_repo, "myjob", output_ref="ref")
    proc = _FakeProc(alive=False)
    worker.local_run_start("myjob", "jid1", "ref", "job", "echo hi", proc)
    r = client_only.post("/-/run/live/myjob/kill")
    assert r.status_code == 404
