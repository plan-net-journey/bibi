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


class _FakeProc:
    """Minimaler subprocess.Popen-Stand-in für Kill-Tests — kein echter
    Subprozess nötig, nur .poll()/.pid, die _terminate() (worker.py)
    tatsächlich anfasst."""
    def __init__(self, *, alive: bool = True) -> None:
        self.pid = 999999  # garantiert kein echter Prozess — os.killpg no-opt defensiv
        self._alive = alive
        self.terminated = False

    def poll(self):
        return None if self._alive else 0


def _seed_pinned_job(root: Path, bucket_slug: str, *, status: str = "running",
                     output_ref: str | None = None, host: str | None = None,
                     ) -> tuple[str, str]:
    """Legt eine echte, gepinnte ``jobs``-Zeile an (PLAN-28: die reale Quelle
    für local_run_live()/local_runs_live()) — analog zu run_pinned()s eigenem
    INSERT, nur direkt per SQL für Tests, ohne echten Dispatch.

    ``output_ref`` (die DB-Spalte) bleibt bewusst ``NULL``, wenn nicht
    explizit übergeben — genau wie beim echten ``run_pinned()``-INSERT (die
    Spalte wird dort NIE mitgeschrieben, erst der Wrapper füllt sie beim
    Terminal-Report). Ein Default-Wert hier hätte den Live-Fund maskiert:
    ``local_run_live()`` las früher diese Spalte direkt, obwohl sie während
    ``running``/``awaiting`` immer ``NULL`` ist (User-Fund 2026-07-13, echter
    Client-Test auf localhost — ``TypeError`` in ``run_live_detail()``).

    Gibt ``(job_id, real_output_ref)`` zurück — Letzteres ist der Pfad, den
    ``local_run_live()`` seit dem Fix unabhängig von der DB-Spalte selbst
    berechnet (``job_db.run_id_for()`` + ``worker._output_path()``); Tests,
    die eine echte ``output.jsonl`` fürs Live-Lesen vorbereiten wollen,
    müssen sie dort ablegen, nicht unter einem frei gewählten Pfad.

    Suffix bewusst ``secrets.token_hex(4)`` (8 Hex-Zeichen) — muss exakt
    ``run_pinned()``s eigene Konvention treffen (User-Fund 2026-07-13:
    ``_pinned_live_row()``s LIKE-Muster prüft seit dem Fix für "hitl-test-app
    vs. hitl-test-app-container" genau 8 Zeichen; ein kürzerer Test-Fake-
    Suffix wie zuvor ``token_hex(2)`` würde an diesem festen Muster
    vorbeilaufen und real existierende Zeilen unauffindbar machen)."""
    import socket

    from bibi.daemon import worker as _worker
    host = host or socket.gethostname()
    jid = secrets.token_hex(4)
    unique_slug = f"{bucket_slug}-{secrets.token_hex(4)}"
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
    run_id = job_db.run_id_for(unique_slug, jid, 0)
    real_output_ref = _worker._output_path(root, run_id).relative_to(root).as_posix()
    return jid, real_output_ref


# ── Reine Registry-Query (worker.py, jobs-tabellen-basiert) ─────────────────


def test_local_run_live_finds_seeded_pinned_row(team_repo: Path):
    jid, real_output_ref = _seed_pinned_job(team_repo, "a")
    live = worker.local_run_live("a")
    assert live["id"] == jid and live["output_ref"] == real_output_ref
    assert live["kind"] == "job" and "started_at" in live


def test_local_run_live_includes_status(team_repo: Path):
    # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED nie
    # im Dashboard gesehen"): status fehlte hier komplett, Aufrufer griffen
    # stattdessen auf signal-basierte Erkennung zurueck, die deferred/failed
    # strukturell nicht kennt.
    _seed_pinned_job(team_repo, "a", status="deferred")
    live = worker.local_run_live("a")
    assert live["status"] == "deferred"


def test_local_run_live_computes_output_ref_even_when_db_column_is_null(team_repo: Path):
    # User-Fund 2026-07-13 (echter Client-Test auf localhost): run_pinned()s
    # INSERT setzt jobs.output_ref NIE (erst der Wrapper beim Terminal-Report)
    # — lesen dieser Spalte während running/awaiting lieferte also immer
    # None, und app.py::run_live_detail()s `repo.root() / live["output_ref"]`
    # crashte mit TypeError. local_run_live() muss den Pfad unabhängig von
    # der (garantiert NULL) DB-Spalte selbst berechnen.
    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    try:
        assert conn.execute(
            "SELECT output_ref FROM jobs WHERE pinned_host IS NOT NULL"
        ).fetchall() == []  # noch keine Zeile — nur zur Doku der Prämisse
    finally:
        conn.close()
    jid, real_output_ref = _seed_pinned_job(team_repo, "a")  # output_ref-Param bewusst weggelassen
    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    try:
        row = conn.execute("SELECT output_ref FROM jobs WHERE id=?", (jid,)).fetchone()
        assert row["output_ref"] is None  # Prämisse bestätigt: Spalte ist NULL
    finally:
        conn.close()
    live = worker.local_run_live("a")
    assert live is not None
    assert live["output_ref"] == real_output_ref
    assert live["output_ref"] is not None


def test_local_run_live_none_when_not_running(team_repo: Path):
    assert worker.local_run_live("nope") is None


def test_local_run_live_ignores_terminal_status(team_repo: Path):
    _seed_pinned_job(team_repo, "a", status="complete")
    assert worker.local_run_live("a") is None


def test_local_run_live_finds_deferred_row(team_repo: Path):
    # Bugfix (User-Fund, "von der defer habe ich nie etwas im FE gesehen"):
    # _PINNED_LIVE_STATUSES enthielt "deferred" bisher nicht — ein gepinnter
    # Lauf verschwand fuer die gesamte Defer-Phase komplett aus dieser Query,
    # obwohl next_fire_at bereits einen Retry vorsah (kein Terminalzustand).
    jid, _ = _seed_pinned_job(team_repo, "a", status="deferred")
    live = worker.local_run_live("a")
    assert live is not None and live["id"] == jid


def test_local_run_live_finds_failed_row(team_repo: Path):
    # Bugfix (User-Fund, "keine Log-Eintraege beim Retry sichtbar"): "failed"
    # fehlte in _PINNED_LIVE_STATUSES genauso wie zuvor "deferred" - beim
    # ersten Fix uebersehen. Ein gepinnter Lauf, der zwischen zwei
    # Fehlversuchen auf den naechsten Retry wartet, verschwand dadurch
    # ebenfalls komplett aus der Job-Detail-Seite.
    jid, _ = _seed_pinned_job(team_repo, "a", status="failed")
    live = worker.local_run_live("a")
    assert live is not None and live["id"] == jid


def test_local_run_live_ignores_other_hosts_pinned_jobs(team_repo: Path):
    _seed_pinned_job(team_repo, "a", host="sarasate")
    assert worker.local_run_live("a", host="mac") is None


def test_local_run_live_does_not_confuse_prefix_slug_with_longer_sibling(team_repo: Path):
    # User-Fund 2026-07-13 ("hitl-test-app-container und hitl-test-app geraten
    # beim Output durcheinander"): _pinned_live_row()s LIKE-Muster war bisher
    # f"{slug}-%" (offenes Wildcard) statt eines festen 8-Hex-Suffix-Musters
    # (wie job_db.list_journal(), s. dort) — "hitl-test-app-" ist ein echtes
    # Präfix von "hitl-test-app-container-<token>", der offene Wildcard matcht
    # deshalb IRRTÜMLICH auch Läufe des längeren Geschwister-Slugs.
    _seed_pinned_job(team_repo, "hitl-test-app-container")
    assert worker.local_run_live("hitl-test-app") is None


def test_local_run_live_still_finds_its_own_runs_alongside_longer_sibling(team_repo: Path):
    jid, _ = _seed_pinned_job(team_repo, "hitl-test-app")
    _seed_pinned_job(team_repo, "hitl-test-app-container")
    live = worker.local_run_live("hitl-test-app")
    assert live is not None and live["id"] == jid


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


# ── POST /-/run + GET /-/run/live (gefakter run_pinned, kein echter Subprozess) ─


@pytest.fixture
def client_only(team_repo: Path):
    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_pinned_worker(team_repo: Path):
    # PLAN-28 Refactor B: Kill für gepinnte Läufe läuft jetzt über denselben
    # Worker wie der Scheduler-Pfad (pinned_worker.kill()) statt einer eigenen
    # Registry — für Kill-Tests brauchen wir Zugriff auf genau diese Instanz,
    # um einen Fake-Proc direkt zu registrieren (wie es execute_reservation()
    # in Produktion über register=pinned_worker._register tut).
    from bibi.daemon.scheduler_client import LocalScheduler
    from bibi.daemon.worker import Worker
    pinned = Worker(client=LocalScheduler(pinned_only=True), autopoll=False)
    app = create_app(roles.resolve({"synchronizer", "controller"}), pinned_worker=pinned)
    with TestClient(app) as c:
        yield c, pinned


def _fake_run_pinned_factory(*, jid: str = "fakejid", slug_suffix: str = "abcd"):
    def fake(*, slug=None, cmd=None, kind="job", register=None, **_kw):
        bucket = slug or "adhoc"
        if register is not None:
            register(jid, None)
        return {"id": jid, "slug": f"{bucket}:{slug_suffix}", "kind": kind,
                "output_ref": f"data/job/{jid}/output.jsonl"}
    return fake


def test_run_route_returns_running_immediately(client_only, monkeypatch):
    # PLAN-28: run_pinned() kehrt synchron zurück (der Wrapper läuft
    # detacht weiter, kein
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
    # Die Datei muss unter dem von local_run_live() selbst berechneten Pfad
    # liegen (real_output_ref, s. _seed_pinned_job()) — nicht unter einem frei
    # gewählten, die jobs.output_ref-Spalte ist während running immer NULL.
    _jid, real_output_ref = _seed_pinned_job(team_repo, "myjob")
    out_path = team_repo / real_output_ref
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"t": 0.0, "s": "signal",
         "line": json.dumps({"name": "awaiting", "input_request": "ja/j?", "port": 9100})}
    ) + "\n")
    body = client_only.get("/-/run/live/myjob").json()
    assert body["status"] == "awaiting"
    assert body["app_url"] == "http://localhost:9100/"
    assert body["demand"] == {"input_request": "ja/j?", "port": 9100}


def test_run_live_detail_shows_deferred_status_from_db(client_only, team_repo):
    # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED nie
    # im Dashboard gesehen"): sig_state kennt nur running/awaiting, deferred/
    # failed muessen aus der DB-Spalte (local_run_live()) kommen.
    _seed_pinned_job(team_repo, "myjob2", status="deferred")
    body = client_only.get("/-/run/live/myjob2").json()
    assert body["status"] == "deferred"


def test_run_live_detail_shows_failed_status_from_db(client_only, team_repo):
    _seed_pinned_job(team_repo, "myjob3", status="failed")
    body = client_only.get("/-/run/live/myjob3").json()
    assert body["status"] == "failed"


def test_run_live_detail_awaiting_signal_wins_over_stale_db_running(client_only, team_repo):
    # Deckt die bestehende Praezedenz ab: ein aktives awaiting-Signal in der
    # Output-Datei gewinnt weiterhin gegen eine (noch) nicht nachgezogene
    # DB-Spalte - genau der Fall aus
    # test_run_live_detail_includes_signal_derived_status_and_app_url oben,
    # nur diesmal explizit auf die Praezedenz-Reihenfolge selbst geprueft.
    _jid, real_output_ref = _seed_pinned_job(team_repo, "myjob4", status="running")
    out_path = team_repo / real_output_ref
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"t": 0.0, "s": "signal", "line": json.dumps({"name": "awaiting"})}
    ) + "\n")
    body = client_only.get("/-/run/live/myjob4").json()
    assert body["status"] == "awaiting"


def test_run_live_detail_status_running_when_no_signal_events(client_only, team_repo):
    _jid, real_output_ref = _seed_pinned_job(team_repo, "plainjob")
    (team_repo / real_output_ref).parent.mkdir(parents=True, exist_ok=True)
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
    conn.execute("UPDATE jobs SET status='complete' WHERE slug LIKE 'myjob-%'")
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


def test_run_live_kill_route_signals_running_job(client_with_pinned_worker, team_repo,
                                                   monkeypatch):
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: None)
    c, pinned = client_with_pinned_worker
    jid, real_output_ref = _seed_pinned_job(team_repo, "myjob")
    proc = _FakeProc(alive=True)
    pinned._register(jid, proc)  # wie execute_reservation() es in Produktion täte
    r = c.post("/-/run/live/myjob/kill")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "signaled": True}

    # User-Fund 2026-07-13 ("KILL führt nicht zum Status Wechsel. Status
    # bleibt RUNNING"): anders als job_kill() (Host) schrieb diese Route nie
    # selbst den Status — verließ sich komplett auf den (separat kaputten,
    # s. test_wrapper_signals.py) Wrapper-Selbstreport nach SIGTERM.
    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT status, reason, output_ref FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert row["status"] == "killed"
    assert row["reason"] == "by_user"

    # User-Fund 2026-07-13 ("kein Output nach Kill"): ohne explizites
    # output_ref bliebe die Spalte NULL, weil run_pinned()s INSERT sie nie
    # füllt und der (jetzt korrekte) spätere Wrapper-Report auf eine bereits
    # terminale Zeile trifft und als No-Op verworfen wird (report_status()s
    # target-is-current-Kurzschluss).
    assert row["output_ref"] == real_output_ref


def test_run_live_kill_route_writes_killed_even_when_proc_already_exited(
    client_with_pinned_worker, team_repo,
):
    # User-Fund 2026-07-20: signalisieren kann fehlschlagen (Prozess schon
    # weg, oder — der eigentliche Auslöser dieses Funds — deferred/failed
    # ohne jeden greifbaren Prozess), der Statuswechsel selbst bleibt trotzdem
    # legitim. Vorher hier ein harter 404 ohne DB-Write — jetzt best-effort
    # signalisieren, aber immer schreiben, analog zu run_live_reset() unten.
    c, pinned = client_with_pinned_worker
    jid, real_output_ref = _seed_pinned_job(team_repo, "myjob")
    proc = _FakeProc(alive=False)
    pinned._register(jid, proc)
    r = c.post("/-/run/live/myjob/kill")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "signaled": False}

    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT status, reason, output_ref FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert row["status"] == "killed"
    assert row["reason"] == "by_user"
    assert row["output_ref"] == real_output_ref


def test_run_live_kill_route_writes_killed_for_deferred_job_with_no_process(
    client_with_pinned_worker, team_repo,
):
    # Der eigentliche, live gemeldete Bug ("KILL auf deferred: nix passiert,
    # dient aber dem Stoppen"): ein deferred-Job hat per Definition gerade
    # keinen laufenden Prozess (wartet auf next_fire_at) — kill() hier also
    # nie signalisiert, der Zustandswechsel (DEFERRED, KILL) → KILLED ist
    # trotzdem ein erlaubter Übergang (lifecycle.py) und muss durchgeschrieben
    # werden, kein 404.
    c, _pinned = client_with_pinned_worker
    jid, real_output_ref = _seed_pinned_job(team_repo, "myjob", status="deferred")
    r = c.post("/-/run/live/myjob/kill")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "signaled": False}

    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT status, reason, output_ref FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert row["status"] == "killed"
    assert row["reason"] == "by_user"
    assert row["output_ref"] == real_output_ref


# ── POST /-/run/live/{slug}/reset (User-Feedback 2026-07-13) ────────────────
# RESET ist der Not-Aus für eine hängen gebliebene Live-Anzeige — anders als
# KILL (das nur bei tatsächlich gesendetem Signal den Status schreibt) muss
# RESET auch dann greifen, wenn der Prozess gar nicht mehr existiert (z. B.
# nach einem Daemon-Neustart, dessen Proc-Registry leer ist) oder der Wrapper
# ohne Terminal-Report abgestürzt ist — genau die Bug-Klasse, die diese
# Session mehrfach fand.


def test_run_live_reset_route_404_when_nothing_running(client_only):
    r = client_only.post("/-/run/live/nope/reset")
    assert r.status_code == 404


def test_run_live_reset_route_forces_killed_even_without_registered_proc(
    client_with_pinned_worker, team_repo,
):
    # Kein pinned._register() hier — simuliert genau den Fall, den KILL nicht
    # abdeckt: die Zeile steht auf "running", aber es gibt keinen greifbaren
    # Prozess mehr (worker.kill() würde nichts signalisieren können).
    c, _pinned = client_with_pinned_worker
    jid, real_output_ref = _seed_pinned_job(team_repo, "myjob")
    r = c.post("/-/run/live/myjob/reset")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "reset": True}

    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT status, reason, output_ref FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert row["status"] == "killed"
    assert row["reason"] == "reset_by_user"
    # User-Fund 2026-07-13 ("kein Output nach Kill") — s. Kill-Test oben,
    # gleicher Mechanismus gilt für RESET.
    assert row["output_ref"] == real_output_ref


# ── RESET wischt ~/.local/share/bibi/ auch auf dem Client-Pfad (Bibi4-Iteration,
# User-Fund "Reset Test Container: Laufzahl nach COMPLETE -> KILL -> RESET ->
# START nicht zurückgesetzt") — job_reset() (Host) bekam die Wipe-Verdrahtung
# in Batch 6, run_live_reset() (Client) nie, weder für den Live- noch für den
# bereits-terminalen Zweig. HOME hier explizit monkeypatchen (s. Kommentar bei
# test_reset_wipes_job_data_dir in test_daemon_worker_routes.py).


def test_run_live_reset_route_wipes_job_data_for_live_row(
    client_with_pinned_worker, team_repo, tmp_path, monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    c, _pinned = client_with_pinned_worker
    jid, _ = _seed_pinned_job(team_repo, "myjob")
    job_dir = tmp_path / ".local" / "share" / "bibi" / "reset-test" / jid
    job_dir.mkdir(parents=True)
    (job_dir / "counter.txt").write_text("3")
    r = c.post("/-/run/live/myjob/reset")
    assert r.status_code == 200
    assert not job_dir.exists()


def test_run_live_reset_route_wipes_job_data_when_already_terminal(
    client_only, team_repo, tmp_path, monkeypatch,
):
    # Genau der gemeldete Bug: der Lauf ist schon "killed" (kein Eintrag mehr
    # in _PINNED_LIVE_STATUSES), local_run_live() findet also nichts mehr —
    # vorher endete das in einem stillen 404 ohne jeden Wipe.
    monkeypatch.setenv("HOME", str(tmp_path))
    jid, _ = _seed_pinned_job(team_repo, "myjob", status="killed")
    job_dir = tmp_path / ".local" / "share" / "bibi" / "reset-test" / jid
    job_dir.mkdir(parents=True)
    (job_dir / "counter.txt").write_text("3")
    r = client_only.post("/-/run/live/myjob/reset")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "reset": True}
    assert not job_dir.exists()


def test_run_live_reset_route_still_404_when_slug_never_ran(client_only, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client_only.post("/-/run/live/nope/reset")
    assert r.status_code == 404


# ── local_schedule_exec_mode() + POST /-/run/live/{slug}/rebuild ────────────
# User-Fund 2026-07-13 ("REBUILD müsste doch auch beim Client notwendig
# sein, oder?"): REBUILD (PLAN-24 Befund 5) verwirft das per-Job-Image eines
# Container-Jobs — auf dem Host längst verdrahtet (/-/job/{id}/rebuild,
# _action_bar()), auf dem Client bisher komplett fehlend. Anders als START/
# RESET/KILL hängt REBUILD an keiner bestimmten Lauf-Zeile, sondern rein am
# *Schedule* (dessen exec_mode: container-Override) — deshalb ein eigener,
# rein dateibasierter Lookup statt eines DB-Query wie bei _job_is_container().


def _seed_schedule(root: Path, rel: str, body: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_local_schedule_exec_mode_reads_container_override(team_repo: Path):
    _seed_schedule(team_repo, "myjob/myjob.md",
                  '---\nschedule: never\njob: "echo hi"\nexec_mode: container\n---\n')
    assert worker.local_schedule_exec_mode("myjob") == "container"


def test_local_schedule_exec_mode_none_for_host_default(team_repo: Path):
    _seed_schedule(team_repo, "myjob/myjob.md", '---\nschedule: never\njob: "echo hi"\n---\n')
    assert worker.local_schedule_exec_mode("myjob") is None


def test_local_schedule_exec_mode_raises_lookup_error_for_unknown_slug(team_repo: Path):
    with pytest.raises(LookupError):
        worker.local_schedule_exec_mode("nope")


def test_run_live_rebuild_route_404_for_unknown_slug(client_only):
    assert client_only.post("/-/run/live/nope/rebuild").status_code == 404


def test_run_live_rebuild_route_409_for_host_mode_job(client_only, team_repo: Path):
    _seed_schedule(team_repo, "myjob/myjob.md", '---\nschedule: never\njob: "echo hi"\n---\n')
    assert client_only.post("/-/run/live/myjob/rebuild").status_code == 409


def test_run_live_rebuild_route_ok_for_container_mode_job(client_only, team_repo: Path,
                                                          monkeypatch):
    _seed_schedule(team_repo, "myjob/myjob.md",
                  '---\nschedule: never\njob: "echo hi"\nexec_mode: container\n---\n')
    calls: list[str] = []
    monkeypatch.setattr(
        "bibi.daemon.worker.Worker.rebuild_job_image",
        lambda self, slug, out_path=None: calls.append(slug) or True,
    )
    r = client_only.post("/-/run/live/myjob/rebuild")
    assert r.status_code == 200
    assert r.json() == {"slug": "myjob", "rebuilt": True}
    assert calls == ["myjob"]


def test_run_live_rebuild_route_502_on_docker_failure(client_only, team_repo: Path, monkeypatch):
    _seed_schedule(team_repo, "myjob/myjob.md",
                  '---\nschedule: never\njob: "echo hi"\nexec_mode: container\n---\n')
    monkeypatch.setattr(
        "bibi.daemon.worker.Worker.rebuild_job_image",
        lambda self, slug, out_path=None: False,
    )
    assert client_only.post("/-/run/live/myjob/rebuild").status_code == 502
