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
    # Output liegt am run_id-Pfad (run_id_for), den die Live-Route auflöst; fire=0.
    run_id = job_db.run_id_for("run1", jid, 0)
    conn = job_db.connect()
    try:
        # schedule gesetzt (wiederkehrend): PLAN-23 Befund 3 sperrt RESET nur
        # für complete+oneshot (schedule=None) — dieser Helper testet den
        # generischen Fall, nicht die oneshot-Sperre selbst.
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, host, "
            "worker, output_ref, enqueued_at, schedule) "
            "VALUES (?,?,?,?,?, 'complete', 'h','w1',?,?,?)",
            (jid, "run1", "run1.md", "job", "echo", f"data/job/{run_id}/output.jsonl", time.time(),
             "0 9 * * *"),
        )
    finally:
        conn.close()
    out = repo.data() / "job" / run_id / "output.jsonl"
    for stream, line in lines:
        output.append(out, stream, line)
    return jid


def _seed_claude_complete(stream_json_lines: list[str]) -> str:
    jid = secrets.token_hex(4)
    run_id = job_db.run_id_for("claude1", jid, 0)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, host, "
            "worker, output_ref, enqueued_at) VALUES (?,?,?,?,?, 'complete', 'h','w1',?,?)",
            (jid, "claude1", "claude1.md", "job", "claude: tu was",
             f"data/job/{run_id}/output.jsonl", time.time()),
        )
    finally:
        conn.close()
    out = repo.data() / "job" / run_id / "output.jsonl"
    for line in stream_json_lines:
        output.append(out, "out", line)
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


def test_job_output_returns_typed_events_and_kind(client):
    jid = _seed_complete([("out", "ein witz"), ("err", "hm")])
    r = client.get(f"/-/job/{jid}/output")
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "job"
    assert [e["line"] for e in data["events"]] == ["ein witz", "hm"]


def test_job_output_empty_for_unknown(client):
    r = client.get("/-/job/deadbeef/output")
    assert r.status_code == 200
    assert r.json() == {"events": [], "kind": "job"}


def test_job_output_formats_claude_stream_json(client):
    # PLAN-12 Stufe 12.5: claude:-Payload → effektiver kind="claude", die
    # rohen stream-json-Zeilen werden zu Klartext/Tool-Use-Summaries formatiert.
    import json as _json
    lines = [
        _json.dumps({"type": "assistant",
                     "message": {"content": [{"type": "text", "text": "Hallo!"}]}}),
        _json.dumps({"type": "assistant",
                     "message": {"content": [
                         {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}),
    ]
    jid = _seed_claude_complete(lines)
    r = client.get(f"/-/job/{jid}/output")
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "claude"
    text = [e["line"] for e in data["events"]]
    assert "Hallo!" in text
    assert "→ Bash: ls" in text
    # die rohe JSON-Zeile darf nicht mehr auftauchen
    assert not any(ln.startswith("{") for ln in text)


def test_job_log_and_stream_stay_raw_for_claude_jobs(client):
    # Raw-Routen (User-bestätigt) — unberührt vom Ausgabefilter: die rohe
    # stream-json-Zeile bleibt unformatiert (kein extrahiertes "Hallo!" allein).
    import json as _json
    raw_line = _json.dumps({"type": "assistant",
                            "message": {"content": [{"type": "text", "text": "Hallo!"}]}})
    jid = _seed_claude_complete([raw_line])
    log = client.get(f"/-/job/{jid}/log")
    assert "assistant" in log.text and "Hallo!" in log.text
    stream = client.get(f"/-/job/{jid}/stream")
    assert "assistant" in stream.text and "Hallo!" in stream.text


def test_err_filters_stream(client):
    jid = _seed_complete([("out", "hallo"), ("err", "warnung")])
    r = client.get(f"/-/job/{jid}/err")
    assert "warnung" in r.text and "hallo" not in r.text


# ── Formatierter Live-Stream: /-/job/{id}/output/stream (Follow-up PLAN-14) ──
# Die Live-Box hing bislang an /stream (roh) — für Claude-Jobs sah man dort
# rohes stream-json statt formatiertem Text. Neuer Endpoint liefert dieselbe
# Formatierung wie /output, aber als SSE inkrementell (from=N zählt in
# FORMATIERTEN Einheiten, passend zum /output-Seed — kein Offset-Mismatch
# wie bei /stream, das roh zählt).


def test_output_stream_formats_claude_events(client):
    import json as _json
    raw_line = _json.dumps({"type": "assistant",
                            "message": {"content": [{"type": "text", "text": "Hallo!"}]}})
    jid = _seed_claude_complete([raw_line])
    r = client.get(f"/-/job/{jid}/output/stream")
    assert r.status_code == 200
    assert "Hallo!" in r.text
    assert "assistant" not in r.text  # kein rohes JSON mehr, wie bei /output


def test_output_stream_respects_from_offset(client):
    import json as _json
    lines = [
        _json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Eins"}]}}),
        _json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Zwei"}]}}),
    ]
    jid = _seed_claude_complete(lines)
    full = client.get(f"/-/job/{jid}/output/stream")
    assert "Eins" in full.text and "Zwei" in full.text
    partial = client.get(f"/-/job/{jid}/output/stream?from=1")
    assert "Zwei" in partial.text and "Eins" not in partial.text


def test_output_stream_stays_raw_passthrough_for_plain_job(client):
    jid = _seed_complete([("out", "hallo"), ("err", "warnung")])
    r = client.get(f"/-/job/{jid}/output/stream")
    assert "hallo" in r.text and "warnung" in r.text


# ── User-Fund 2026-07-20: id:/Last-Event-ID/event:done/Heartbeat ────────────
# .liveterm schloss bei JEDEM onerror (render.py) -- ununterscheidbar, ob der
# Server absichtlich beendet hat (Job fertig) oder die Verbindung nur abriss.
# Ein noch laufender Job fror dann fuer immer in der Live-Box ein.


def test_output_stream_events_carry_running_id(client):
    jid = _seed_complete([("out", "eins"), ("out", "zwei")])
    r = client.get(f"/-/job/{jid}/output/stream")
    assert "id: 1\ndata:" in r.text
    assert "id: 2\ndata:" in r.text


def test_output_stream_sends_done_event_when_job_is_terminal(client):
    jid = _seed_complete([("out", "hallo")])
    r = client.get(f"/-/job/{jid}/output/stream")
    assert "event: done\ndata: {}" in r.text
    # done kommt NACH dem letzten echten Event, nicht davor.
    assert r.text.index("id: 1") < r.text.index("event: done")


def test_output_stream_last_event_id_header_overrides_from_query(client):
    jid = _seed_complete([("out", "eins"), ("out", "zwei")])
    # Last-Event-ID=1 == "ich habe Event 1 schon", genau wie from=1 -- muss
    # denselben Effekt haben (Browser schickt das automatisch bei Reconnect).
    r = client.get(f"/-/job/{jid}/output/stream", headers={"Last-Event-ID": "1"})
    assert "zwei" in r.text and "eins" not in r.text


def test_output_stream_invalid_last_event_id_falls_back_to_from_query(client):
    jid = _seed_complete([("out", "eins"), ("out", "zwei")])
    r = client.get(f"/-/job/{jid}/output/stream?from=1", headers={"Last-Event-ID": "garbage"})
    assert "zwei" in r.text and "eins" not in r.text


def test_output_stream_pings_during_silence_on_still_running_job(client, monkeypatch):
    # Heartbeat darf output.jsonl/_last_activity() (Zombie-Erkennung, worker.py)
    # nie beruehren -- rein clientseitig zum Verbindung-Warmhalten. Simuliert
    # per Fake-Clock: jeder time.time()-Aufruf springt um 20s weiter, die
    # 15s-Schwelle greift also schon in der ersten Schleifenrunde. Treibt den
    # Route-Handler direkt an (statt ueber TestClient/HTTP) -- ein noch
    # laufender Job hat einen echt unendlichen gen()-Loop, das waere ueber
    # einen synchronen HTTP-Request nicht sicher/zeitbegrenzt abzubrechen.
    import asyncio
    import itertools

    from bibi.daemon import app as app_module

    jid = _seed_status("running")
    fake_now = itertools.count(1_000_000, 20)
    monkeypatch.setattr(app_module.time, "time", lambda: next(fake_now))

    route = next(r for r in client.app.routes if getattr(r, "path", None) == "/-/job/{id}/output/stream")
    resp = route.endpoint(id=jid, from_=0, last_event_id=None)

    async def collect_until_ping():
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
            if ": ping" in chunk:
                return chunks
        raise AssertionError("Generator endete ohne Ping")

    chunks = asyncio.run(asyncio.wait_for(collect_until_ping(), timeout=5))
    assert any(": ping" in c for c in chunks)


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


def test_kill_writes_output_ref_into_journal(client):
    # User-Fund 2026-07-27 ("kein Output" auf /-/ui/run/… nach KILL): der
    # daemon-seitige killed-Report macht die Zeile terminal und schreibt das
    # Journal, der spätere Wrapper-Report MIT output_ref wird als
    # idempotenter Wiederholungs-Report verworfen — job_kill() muss den
    # Verweis deshalb selbst mitschreiben (dieselbe Verdrahtung wie
    # run_live_kill()/-reset() für gepinnte Läufe seit 2026-07-13).
    jid = _seed_status("running")
    conn = job_db.connect()
    try:
        run_id = job_db.run_id_for("s", jid, 0)
    finally:
        conn.close()
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    conn = job_db.connect()
    try:
        # A2 (m.rau/bibi#101): `killed` blockiert den Slot, die Journal-Zeile
        # entsteht erst beim Abraeumen. Der Verweis muss die Wartezeit im Slot
        # ueberstehen — genau das prueft dieser Test jetzt zusaetzlich mit.
        assert job_db.list_journal(conn) == []
        job_db.start_now(conn, jid)
        row = conn.execute(
            "SELECT output_ref FROM journal WHERE run_id=? AND status='killed'",
            (run_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["output_ref"] == f"data/job/{run_id}/output.jsonl"


def test_kill_running_job_falls_back_to_db_pid_after_restart(client, monkeypatch):
    # Kein In-Memory-Popen (z. B. Job hat einen Daemon-Neustart überlebt, s. A.2) —
    # kill() muss die PID aus der DB reanimieren und SIGTERM senden.
    import os
    # #38: report_pid() schaltet nur aus 'starting' heraus — genau so läuft es
    # im Betrieb (reserve_next → starting, Spawn, dann report_pid → running).
    # Auf 'running' geseedet träfe das Update null Zeilen und die PID fehlte.
    jid = _seed_status("starting")
    conn = job_db.connect()
    try:
        assert job_db.report_pid(conn, jid, 4321, "ts-77") is True
    finally:
        conn.close()
    monkeypatch.setattr(job_db, "proc_started_at", lambda pid: "ts-77")
    signaled = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signaled.append((pid, sig)))
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    assert r.json()["signaled"] is True
    import signal
    assert (4321, signal.SIGTERM) in signaled


def test_kill_running_job_db_pid_dead_not_signaled(client):
    # PID in DB, aber nicht mehr lebendig (proc_started_at weicht ab) — kein
    # echter Prozess mehr zu killen, signaled bleibt False.
    jid = _seed_status("running")
    conn = job_db.connect()
    try:
        job_db.report_pid(conn, jid, 4321, "stale-ts")
    finally:
        conn.close()
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    assert r.json()["signaled"] is False


def test_kill_pending_ok(client):
    # pending → killed ("aus dem Schedule nehmen") ist jetzt erlaubt (§5.4).
    jid = _seed_status("pending")
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    assert r.json()["status"] == "killed"


def test_kill_failed_ok(client):
    jid = _seed_status("failed")
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    assert r.json()["status"] == "killed"


def test_kill_deferred_ok(client):
    jid = _seed_status("deferred")
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    assert r.json()["status"] == "killed"


def test_kill_complete_archives_and_lands_on_killed(client):
    # User-Redesign 2026-07-20 (widerruft den complete-Ausschluss von
    # 2026-07-03): Lazy Rearm dispatcht einen wiederkehrenden complete-Job
    # sonst irgendwann von selbst neu — KILL muss ihn dauerhaft anhalten
    # können, ohne die MD zu editieren.
    jid = _seed_complete([("out", "x")])
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    assert r.json()["status"] == "killed"

    conn = job_db.connect()
    try:
        row = conn.execute(
            "SELECT status, attempt, started_at, exit_code, output_ref, next_fire_at, fire "
            "FROM jobs WHERE id=?", (jid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "killed"
    # archiviert wie ein RESET: der abgeschlossene Lauf hinterlässt keine
    # stale Werte in der frischen, sofort toten Zyklus-Zeile.
    assert row["attempt"] == 0
    assert row["started_at"] is None
    assert row["exit_code"] is None
    assert row["output_ref"] is None
    assert row["next_fire_at"] is None  # kein Lazy Rearm mehr möglich
    assert row["fire"] == 1  # eigener run_id, getrennt vom alten complete-Journal-Eintrag


def test_kill_missing_is_404(client):
    assert client.post("/-/job/deadbeef/kill").status_code == 404


def test_kill_already_killed_job_ok(client):
    # PLAN-14 Stufe 14.1: erneutes Kill auf einem bereits `killed`-Job ist
    # idempotent erlaubt (target == current, report_status kurzschließt).
    jid = _seed_status("killed")
    r = client.post(f"/-/job/{jid}/kill")
    assert r.status_code == 200
    assert r.json()["status"] == "killed"


def test_kill_error_is_409(client):
    # PLAN-14 Stufe 14.1 Bug #1: (ERROR, KILL) existiert nicht in der
    # Übergangstabelle — schon vor dem Fix so, hier als Vertrag verankert.
    jid = _seed_status("error")
    assert client.post(f"/-/job/{jid}/kill").status_code == 409


def test_reset_complete_to_pending(client):
    jid = _seed_complete([("out", "x")])
    r = client.post(f"/-/job/{jid}/reset")
    assert r.status_code == 200
    assert client.get(f"/-/job/{jid}/status").json()["status"] == "pending"


def test_reset_completed_oneshot_is_409(client):
    # PLAN-23 Befund 3: ein abgeschlossener oneshot (`at:`, schedule=None)
    # darf über die Route nicht mehr resettbar sein — Gegenstück zu
    # test_reset_complete_to_pending (dort: wiederkehrend, schedule gesetzt).
    jid = secrets.token_hex(4)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
            "enqueued_at, schedule) VALUES (?,?,?,?,?, 'complete', ?, NULL)",
            (jid, "once", "once.md", "job", "echo", time.time()),
        )
    finally:
        conn.close()
    r = client.post(f"/-/job/{jid}/reset")
    assert r.status_code == 409
    assert client.get(f"/-/job/{jid}/status").json()["status"] == "complete"


def test_reset_running_is_409(client):
    jid = _seed_status("running")  # running ist kein Terminalzustand
    assert client.post(f"/-/job/{jid}/reset").status_code == 409


def test_reset_missing_is_404(client):
    assert client.post("/-/job/deadbeef/reset").status_code == 404


# ── RESET wischt ~/.local/share/bibi/, START nie (Bibi4 Batch 6) ────────────
# Path.home() ist hier nicht global gesandboxt (anders als XDG_CONFIG_HOME,
# s. conftest._isolate_node_config) — HOME explizit monkeypatchen, sonst
# Zugriff auf die echte ~/.local/share/bibi/ im Testlauf.


def test_reset_wipes_job_data_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    jid = _seed_complete([("out", "x")])
    job_dir = tmp_path / ".local" / "share" / "bibi" / "reset-test" / jid
    job_dir.mkdir(parents=True)
    (job_dir / "counter.txt").write_text("3")
    r = client.post(f"/-/job/{jid}/reset")
    assert r.status_code == 200
    assert not job_dir.exists()


def test_reset_rejected_leaves_job_data_dir_untouched(client, tmp_path, monkeypatch):
    # 409/404-Fälle (kein echter Übergang) dürfen nichts löschen — wipe_job_data()
    # sitzt hinter den outcome-Checks in job_reset(), nicht davor.
    monkeypatch.setenv("HOME", str(tmp_path))
    jid = _seed_status("running")  # running ist kein Terminalzustand → 409
    job_dir = tmp_path / ".local" / "share" / "bibi" / "reset-test" / jid
    job_dir.mkdir(parents=True)
    assert client.post(f"/-/job/{jid}/reset").status_code == 409
    assert job_dir.is_dir()


def test_start_does_not_wipe_job_data_dir(client, tmp_path, monkeypatch):
    # Kernversprechen der Verb-Semantik: START rührt job-eigene Daten nie an,
    # auch nicht über den archivierenden _ARCHIVE_AND_START-Pfad (killed → start).
    monkeypatch.setenv("HOME", str(tmp_path))
    jid = _seed_status("killed")
    job_dir = tmp_path / ".local" / "share" / "bibi" / "reset-test" / jid
    job_dir.mkdir(parents=True)
    (job_dir / "counter.txt").write_text("3")
    r = client.post(f"/-/job/{jid}/start")
    assert r.status_code == 200
    assert job_dir.is_dir()
    assert (job_dir / "counter.txt").read_text() == "3"


def test_start_pending_ok(client):
    jid = _seed_status("pending")
    assert client.post(f"/-/job/{jid}/start").status_code == 200


def test_start_running_is_409(client):
    jid = _seed_status("running")  # nur pending ist startbar
    assert client.post(f"/-/job/{jid}/start").status_code == 409


def test_start_missing_is_404(client):
    assert client.post("/-/job/deadbeef/start").status_code == 404


def test_start_error_archives_to_pending(client):
    # PLAN-14 Stufe 14.2: der START-Button bei error war schon vorher sichtbar,
    # scheiterte aber immer mit 409 — jetzt archiviert er (→ pending).
    jid = _seed_status("error")
    r = client.post(f"/-/job/{jid}/start")
    assert r.status_code == 200
    assert client.get(f"/-/job/{jid}/status").json()["status"] == "pending"


def test_start_failed_dispatches_immediately(client):
    # User-Entscheidung (Job Lifecycle §START/failed): kein Attempts-Reset, nur
    # next_fire_at=now überspringt den Backoff-Timer, status bleibt `failed`.
    jid = _seed_status("failed")
    r = client.post(f"/-/job/{jid}/start")
    assert r.status_code == 200
    assert client.get(f"/-/job/{jid}/status").json()["status"] == "failed"


def test_start_deferred_dispatches_immediately(client):
    # Follow-up: deferred braucht keine attempts-1-Logik, war fälschlich mit
    # failed zusammen ausgeschlossen worden.
    jid = _seed_status("deferred")
    assert client.post(f"/-/job/{jid}/start").status_code == 200


def _seed_with_exec_mode(exec_mode: str | None) -> str:
    jid = secrets.token_hex(4)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
            "enqueued_at, exec_mode) VALUES (?,?,?,?,?, 'pending', ?, ?)",
            (jid, "rebuildjob", "rebuildjob.md", "job", "echo hi", time.time(), exec_mode),
        )
    finally:
        conn.close()
    return jid


def test_rebuild_missing_is_404(client):
    assert client.post("/-/job/deadbeef/rebuild").status_code == 404


def test_rebuild_host_mode_is_409(client):
    # PLAN-24 Befund 5: REBUILD betrifft nur Container-Jobs — ein Host-Mode-
    # Job hat kein per-Job-Image, das verworfen werden könnte.
    jid = _seed_with_exec_mode(None)
    assert client.post(f"/-/job/{jid}/rebuild").status_code == 409


def test_rebuild_container_mode_ok(client, monkeypatch):
    jid = _seed_with_exec_mode("container")
    calls: list[str] = []
    monkeypatch.setattr(
        "bibi.daemon.app.Worker.rebuild_job_image",
        lambda self, slug, out_path=None: calls.append(slug) or True,
    )
    r = client.post(f"/-/job/{jid}/rebuild")
    assert r.status_code == 200
    assert r.json() == {"id": jid, "slug": "rebuildjob", "rebuilt": True}
    assert calls == ["rebuildjob"]


def test_rebuild_docker_failure_is_502(client, monkeypatch):
    jid = _seed_with_exec_mode("container")
    monkeypatch.setattr(
        "bibi.daemon.app.Worker.rebuild_job_image", lambda self, slug, out_path=None: False)
    assert client.post(f"/-/job/{jid}/rebuild").status_code == 502


def test_ping_writes_last_ping_at(client):
    jid = _seed_status("running")
    r = client.post(f"/-/job/{jid}/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    conn = job_db.connect()
    try:
        assert conn.execute(
            "SELECT last_ping_at FROM jobs WHERE id=?", (jid,)).fetchone()["last_ping_at"] is not None
    finally:
        conn.close()


def test_ping_missing_job_returns_ok_false(client):
    r = client.post("/-/job/deadbeef/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": False}


def test_journal_lists_terminal_runs(client):
    # Einen Lauf simulieren: running → complete schreibt eine Journal-Zeile.
    jid = _seed_status("running")
    client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    rows = client.get("/-/journal").json()
    assert any(r["slug"] == "s" and r["status"] == "complete" for r in rows)


def test_journal_route_limit_offset_params(client):
    conn = job_db.connect()
    try:
        for i in range(3):
            conn.execute(
                "INSERT INTO journal (run_id, slug, kind, status, finished_at, archived_at) "
                "VALUES (?,?,?,?,?,?)",
                (f"j:{i}", "j", "job", "complete", 100 - i, 100 - i),
            )
    finally:
        conn.close()
    rows = client.get("/-/journal", params={"slug": "j", "limit": 2, "offset": 1}).json()
    assert [r["run_id"] for r in rows] == ["j:1", "j:2"]


def test_worker_routes_absent_without_worker_role(team_repo):
    app = create_app(roles.resolve({"scheduler"}))  # kein worker
    with TestClient(app) as c:
        # 3.0-Contract-Stub bleibt (501), keine echte Worker-Route
        assert c.get("/-/job/x/log").status_code == 501