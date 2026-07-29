"""PLAN-11.3: stdout-Signalprotokoll — Parser + Handler (unit tests, kein Subprocess)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bibi import wrapper as _wrapper
from bibi.wrapper import _parse_bibi_line, _handle_signal, _record_signal, output
from bibi.daemon import job_db


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _insert_job(conn, job_id: str = "j1") -> None:
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, job_id, f"{job_id}.md", "job", "echo hi", "running"),
    )


# ── _parse_bibi_line ──────────────────────────────────────────────────────────


def test_bibi_line_parsed_correctly():
    sig = _parse_bibi_line('BIBI:{"name":"running"}')
    assert sig == {"name": "running"}


def test_non_bibi_line_returns_none():
    assert _parse_bibi_line("normale Ausgabe") is None


def test_empty_line_returns_none():
    assert _parse_bibi_line("") is None


def test_bibi_prefix_only_returns_none():
    assert _parse_bibi_line("BIBI:") is None


def test_bibi_invalid_json_returns_none():
    assert _parse_bibi_line("BIBI:{ungültig}") is None


def test_bibi_awaiting_parsed():
    sig = _parse_bibi_line('BIBI:{"name":"awaiting","input_request":"?","input_format":"text"}')
    assert sig is not None
    assert sig["name"] == "awaiting"
    assert sig["input_request"] == "?"


def test_bibi_deferred_with_seconds():
    sig = _parse_bibi_line('BIBI:{"name":"deferred","seconds":120}')
    assert sig is not None
    assert sig["seconds"] == 120


def test_bibi_failed_with_seconds():
    sig = _parse_bibi_line('BIBI:{"name":"failed","seconds":10}')
    assert sig is not None
    assert sig["seconds"] == 10


# ── _handle_signal ────────────────────────────────────────────────────────────


def test_handle_running_updates_db_status(conn):
    _insert_job(conn)
    _handle_signal(conn, "j1", {"name": "running"})
    row = conn.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()
    assert row["status"] == "running"


def test_handle_awaiting_sets_status_and_demand(conn):
    _insert_job(conn)
    sig = {"name": "awaiting", "input_request": "Wie viele?", "input_format": "number"}
    _handle_signal(conn, "j1", sig)
    row = conn.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()
    assert row["status"] == "awaiting"
    demand = job_db.get_demand(conn, "j1")
    assert demand is not None
    assert demand["input_request"] == "Wie viele?"
    assert demand["input_format"] == "number"


def test_handle_app_register_sets_app_port(conn):
    _insert_job(conn)
    _handle_signal(conn, "j1", {"name": "app_register", "port": 9100})
    row = conn.execute("SELECT app_port FROM jobs WHERE id='j1'").fetchone()
    assert row["app_port"] == 9100


def test_handle_awaiting_with_port_sets_app_url(conn):
    # bibi.job.awaiting(..., port=9100) muss die FE-HITL-Verlinkung (app_url)
    # versorgen — sonst zeigt das Panel "app_url nicht verfügbar" (render.py).
    _insert_job(conn)
    sig = {"name": "awaiting", "input_request": "Wie viele?", "input_format": "number", "port": 9100}
    _handle_signal(conn, "j1", sig)
    row = conn.execute("SELECT app_url FROM jobs WHERE id='j1'").fetchone()
    assert row["app_url"] == "http://localhost:9100/"


def test_handle_awaiting_falls_back_to_job_app_port(conn):
    # Steht app_port schon aus dem Frontmatter in der DB (app_port:-Feld), muss
    # awaiting ohne explizites port-Feld im Signal trotzdem app_url setzen.
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, app_port) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("j2", "j2", "j2.md", "job", "echo hi", "running", 9200),
    )
    sig = {"name": "awaiting", "input_request": "?", "input_format": "text"}
    _handle_signal(conn, "j2", sig)
    row = conn.execute("SELECT app_url FROM jobs WHERE id='j2'").fetchone()
    assert row["app_url"] == "http://localhost:9200/"


def test_handle_awaiting_uses_configured_public_host(conn, monkeypatch):
    # PLAN-22 Befund 6: die Adresse war zuvor hart auf 127.0.0.1 kodiert — auf
    # einem Remote-Host (z. B. sarasate) für einen Client-Browser tot.
    monkeypatch.setenv("BIBI_PUBLIC_HOST", "sarasate.tail9f9173.ts.net")
    _insert_job(conn)
    sig = {"name": "awaiting", "input_request": "?", "input_format": "text", "port": 9100}
    _handle_signal(conn, "j1", sig)
    row = conn.execute("SELECT app_url FROM jobs WHERE id='j1'").fetchone()
    assert row["app_url"] == "http://sarasate.tail9f9173.ts.net:9100/"


def test_handle_awaiting_without_any_port_leaves_app_url_unset(conn):
    _insert_job(conn)  # kein app_port in der DB, kein port im Signal
    sig = {"name": "awaiting", "input_request": "?", "input_format": "text"}
    _handle_signal(conn, "j1", sig)
    row = conn.execute("SELECT app_url FROM jobs WHERE id='j1'").fetchone()
    assert row["app_url"] is None


def test_handle_unknown_name_is_noop(conn):
    _insert_job(conn)
    conn.execute("UPDATE jobs SET status='running' WHERE id='j1'")
    _handle_signal(conn, "j1", {"name": "unknown_signal", "data": 42})
    row = conn.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()
    assert row["status"] == "running"  # unverändert


def test_handle_activity_signal_is_noop(conn):
    # Reiner Herzschlag (User-Feedback 2026-07-04) — last_activity_ts aktualisiert
    # bereits der Pump-Loop, hier gibt es nichts an der DB zu ändern.
    _insert_job(conn)
    conn.execute("UPDATE jobs SET status='awaiting' WHERE id='j1'")
    _handle_signal(conn, "j1", {"name": "activity"})
    row = conn.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()
    assert row["status"] == "awaiting"  # unverändert


# ── _record_signal (Ausbau User-Fund 2026-07-10: lokale /run-App-Jobs ────────
# verwarfen awaiting/app_register bisher spurlos, weil ihnen keine
# BIBI_SCHEDULER_DB_PATH zur Verfügung steht — jetzt landen sie stattdessen
# als "signal"-Event in output.jsonl, das worker.local_run_signal_state()
# ausliest.) ──────────────────────────────────────────────────────────────────


def _record(sig, *, db_path_str=None, out_path, current_status=None):
    return _record_signal(
        sig, job_id="j1", out_path=out_path, db_path_str=db_path_str,
        current_status=current_status if current_status is not None else ["running"],
        lock=threading.Lock(),
    )


def test_record_signal_writes_to_db_when_path_given(conn, tmp_path):
    _insert_job(conn)
    db_path = tmp_path / "jobs.sqlite"
    conn.close()  # _record_signal öffnet seine eigene Connection auf denselben Pfad
    _record({"name": "running"}, db_path_str=str(db_path), out_path=tmp_path / "output.jsonl")
    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()
    c2.close()
    assert row["status"] == "running"


def test_record_signal_no_db_path_writes_output_event_instead(tmp_path):
    out_path = tmp_path / "output.jsonl"
    sig = {"name": "awaiting", "input_request": "ja/j?", "input_format": "text", "port": 9100}
    _record(sig, db_path_str=None, out_path=out_path)
    events = output.read_events(out_path)
    assert len(events) == 1
    assert events[0]["s"] == "signal"
    assert json.loads(events[0]["line"]) == sig


def test_record_signal_no_db_path_does_not_touch_any_db(tmp_path):
    # Kein db_path_str ⇒ kein DB-Zugriff überhaupt versucht (ephemeral/lokal).
    out_path = tmp_path / "output.jsonl"
    _record({"name": "app_register", "port": 9100}, db_path_str=None, out_path=out_path)
    events = output.read_events(out_path)
    assert json.loads(events[0]["line"])["name"] == "app_register"


def test_record_signal_awaiting_updates_current_status(tmp_path):
    # User-Fund: current_status[0] wurde vorher NUR im DB-Pfad aktualisiert —
    # ephemeral/lokale Läufe blieben intern für immer auf "running" stehen.
    cs = ["running"]
    _record({"name": "awaiting", "input_request": "?"}, db_path_str=None,
            out_path=tmp_path / "output.jsonl", current_status=cs)
    assert cs[0] == "awaiting"


def test_record_signal_running_after_awaiting_resets_current_status(tmp_path):
    cs = ["awaiting"]
    _record({"name": "running"}, db_path_str=None, out_path=tmp_path / "output.jsonl",
            current_status=cs)
    assert cs[0] == "running"


def test_record_signal_app_register_does_not_touch_current_status(tmp_path):
    cs = ["running"]
    _record({"name": "app_register", "port": 9100}, db_path_str=None,
            out_path=tmp_path / "output.jsonl", current_status=cs)
    assert cs[0] == "running"  # app_register ist kein running/awaiting-Übergang


def test_record_signal_db_write_failure_is_swallowed(tmp_path):
    # Ungültiger db_path_str (z. B. Verzeichnis existiert nicht) darf nie
    # crashen — best effort, wie überall im Wrapper (§2.7).
    _record({"name": "running"}, db_path_str=str(tmp_path / "nope" / "jobs.sqlite"),
            out_path=tmp_path / "output.jsonl")


# ── Monitor-Threads: Phasen-Logging beim autonomen Kill (User-Feedback 2026-07-03,
# hitl-test-app wurde nach 1h Zombie ohne jede Log-Zeile — weder Live-Log noch
# Daemon-Log, weil Wall-/Silence-/HITL-Monitor bislang nur die DB meldeten).
# 2026-07-04: silence_timeout/hitl_timeout zusammengelegt — ein Monitor für
# beide Fälle, gespeist aus last_activity_ts statt Datei-mtime ─────────────


def test_wall_monitor_logs_phase_before_terminate(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(_wrapper, "_terminate_proc", lambda proc, env=None: killed.append((proc, env)))
    out_path = tmp_path / "output.jsonl"
    proc = SimpleNamespace(poll=lambda: None)
    outcome = [""]
    lock = threading.Lock()
    env = {"BIBI_EXEC_MODE": "container", "BIBI_JOB_ID": "j1"}

    _wrapper._wall_monitor(proc, 1, time.time() - 100, outcome, out_path, lock, env)

    # env muss bis zu _terminate_proc() durchgereicht werden — sonst bleibt
    # der Container beim Wall-Time-Kill verwaist (derselbe Bug wie bei ZOMBIE).
    assert killed == [(proc, env)]
    assert outcome[0] == "wall_time"
    phase_lines = output.lines(out_path, "phase")
    assert any("wall_time" in l and "1s" in l for l in phase_lines), phase_lines


def test_silence_monitor_logs_phase_before_terminate(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(_wrapper, "_terminate_proc", lambda proc, env=None: killed.append((proc, env)))
    out_path = tmp_path / "output.jsonl"
    proc = SimpleNamespace(poll=lambda: None)
    outcome = [""]
    lock = threading.Lock()
    last_activity_ts = [time.time() - 100]
    env = {"BIBI_EXEC_MODE": "container", "BIBI_JOB_ID": "j1"}

    _wrapper._silence_monitor(proc, 1, last_activity_ts, outcome, out_path, lock, env)

    # ZOMBIE-Fix: env muss bis zu _terminate_proc() durchgereicht werden, sonst
    # weiß _terminate_proc() nicht, dass es sich um einen Container-Job handelt.
    assert killed == [(proc, env)]
    assert outcome[0] == "silence"
    phase_lines = output.lines(out_path, "phase")
    assert any("silence" in l and "1s" in l for l in phase_lines), phase_lines


def test_silence_monitor_does_not_fire_while_activity_is_recent(tmp_path, monkeypatch):
    # Kernpunkt der Zusammenlegung: last_activity_ts wird vom Pump-Loop bei
    # JEDER Zeile aktualisiert (Output wie BIBI-Signal) — solange das passiert,
    # bleibt der Monitor still, egal ob "silence" (Job) oder "awaiting" (App).
    killed = []
    monkeypatch.setattr(_wrapper, "_terminate_proc", lambda proc, env=None: killed.append((proc, env)))
    monkeypatch.setattr(_wrapper.time, "sleep", lambda s: None)
    out_path = tmp_path / "output.jsonl"
    polls = iter([None, None, "done"])  # zwei lebendige Ticks, dann Ende
    proc = SimpleNamespace(poll=lambda: next(polls))
    outcome = [""]
    lock = threading.Lock()
    last_activity_ts = [time.time()]  # gerade erst aktiv

    _wrapper._silence_monitor(proc, 3600, last_activity_ts, outcome, out_path, lock)

    assert killed == []
    assert outcome[0] == ""


def test_deferred_watcher_terminates_once_status_flips(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(_wrapper, "_terminate_proc", lambda proc, env=None: killed.append((proc, env)))
    monkeypatch.setattr(_wrapper.time, "sleep", lambda s: None)
    polls = iter([None, None])
    proc = SimpleNamespace(poll=lambda: next(polls))
    current_status = ["deferred"]
    outcome = [""]
    lock = threading.Lock()
    env = {"BIBI_EXEC_MODE": "container", "BIBI_JOB_ID": "j1"}

    _wrapper._deferred_watcher(proc, current_status, outcome, lock, env)

    assert killed == [(proc, env)]
    assert outcome[0] == "deferred"


def test_deferred_watcher_ignores_running_status(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(_wrapper, "_terminate_proc", lambda proc, env=None: killed.append((proc, env)))
    monkeypatch.setattr(_wrapper.time, "sleep", lambda s: None)
    polls = iter([None, None, "done"])
    proc = SimpleNamespace(poll=lambda: next(polls))
    current_status = ["running"]
    outcome = [""]
    lock = threading.Lock()

    _wrapper._deferred_watcher(proc, current_status, outcome, lock)

    assert killed == []
    assert outcome[0] == ""


# ── ZOMBIE-Fix: _terminate_proc()/stop_container() müssen den Container ────
# stoppen, nicht nur die lokale docker-run-CLI-Prozessgruppe (User-Fund:
# "beim lokalen Client wird mit Status ZOMBIE nicht der Container gestoppt").


def test_terminate_proc_stops_container_when_env_is_container_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(_wrapper.exec_backend, "stop_container", lambda env: calls.append(env))
    monkeypatch.setattr(_wrapper.time, "sleep", lambda s: None)
    proc = SimpleNamespace(pid=-1, poll=lambda: "done")  # pid -1 → getpgid wirft, wird geschluckt
    env = {"BIBI_EXEC_MODE": "container", "BIBI_JOB_ID": "j1"}

    _wrapper._terminate_proc(proc, env)

    assert calls == [env]


def test_terminate_proc_skips_docker_without_env(monkeypatch):
    calls = []
    monkeypatch.setattr(_wrapper.exec_backend, "stop_container", lambda env: calls.append(env))
    monkeypatch.setattr(_wrapper.time, "sleep", lambda s: None)
    proc = SimpleNamespace(pid=-1, poll=lambda: "done")

    _wrapper._terminate_proc(proc)  # kein env — wie vor dem Fix

    assert calls == []


def test_finish_silence_outcome_maps_to_silence_reason(tmp_path):
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("z10", "z10", "z10.md", "job", "echo hi", "running"),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z10", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "1"}

    _wrapper._finish(env, 0, "silence")

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, reason FROM jobs WHERE id='z10'").fetchone()
    c2.close()
    assert row["status"] == "zombie"
    assert row["reason"] == "silence"


def test_commit_worktree_skips_commit_when_in_place(tmp_path):
    # User-Fund 2026-07-14 (bibi-ctrl test): BIBI_IN_PLACE=1 überspringt den
    # Commit, ruft dafür also auch keinen git-Befehl auf — repo_root muss
    # dafür nicht mal ein echtes Repo sein.
    env = {"BIBI_IN_PLACE": "1", "BIBI_REPO_ROOT": str(tmp_path),
           "BIBI_WORKTREE": str(tmp_path), "BIBI_JOB_SLUG": "s"}
    assert _wrapper._commit_worktree(env) == (None, None)


# ── _commit_worktree: getrennte Fehlerbehandlung Commit/Remove ──────────────
# Worktree-Cleanup-Bug (Case 20260621.Bibi4-870bd9db, 2026-07-26): ein
# gemeinsamer try/except verschluckte JEDEN Fehler stillschweigend (kein Log)
# UND warf bei einem Remove-Fehler auch einen bereits erfolgreichen Commit weg.


def test_commit_worktree_logs_and_returns_none_on_commit_failure(tmp_path, monkeypatch, caplog):
    from bibi.daemon import worktree as _wt

    def _boom(**_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(_wt, "commit", _boom)
    env = {"BIBI_REPO_ROOT": str(tmp_path), "BIBI_WORKTREE": str(tmp_path / "wt"),
           "BIBI_JOB_SLUG": "s", "BIBI_JOB_ID": "j1"}
    with caplog.at_level("WARNING"):
        result = _wrapper._commit_worktree(env)
    assert result == (None, None)
    assert "commit_failed" in caplog.text


def test_commit_worktree_ephemeral_remove_failure_keeps_commit_result(tmp_path, monkeypatch, caplog):
    # Der entscheidende Fund: ein Fehler beim ephemeren Cleanup darf den
    # bereits erfolgreichen Commit/Branch nicht mit wegwerfen (vorher wurde
    # daraus (None, None) — der Merge-back-Trigger für eine tatsächlich
    # erfolgreiche Änderung wäre stillschweigend verloren gegangen).
    from bibi.daemon import worktree as _wt

    monkeypatch.setattr(_wt, "commit", lambda **_k: "deadbeef" * 5)
    monkeypatch.setattr(_wt, "branch_name", lambda slug: f"agent/{slug}")

    def _boom(**_k):
        raise RuntimeError("remove boom")

    monkeypatch.setattr(_wt, "remove", _boom)
    env = {"BIBI_REPO_ROOT": str(tmp_path), "BIBI_WORKTREE": str(tmp_path / "wt"),
           "BIBI_JOB_SLUG": "s", "BIBI_JOB_ID": "j1", "BIBI_EPHEMERAL": "1"}
    with caplog.at_level("WARNING"):
        commit_sha, branch = _wrapper._commit_worktree(env)
    assert commit_sha == "deadbeef" * 5
    assert branch == "agent/s"
    assert "ephemeral_remove_failed" in caplog.text


def test_commit_worktree_ephemeral_success_calls_remove(tmp_path, monkeypatch):
    from bibi.daemon import worktree as _wt

    monkeypatch.setattr(_wt, "commit", lambda **_k: "abc123")
    monkeypatch.setattr(_wt, "branch_name", lambda slug: f"agent/{slug}")
    removed = []
    monkeypatch.setattr(_wt, "remove", lambda **k: removed.append(k))
    env = {"BIBI_REPO_ROOT": str(tmp_path), "BIBI_WORKTREE": str(tmp_path / "wt"),
           "BIBI_JOB_SLUG": "s", "BIBI_JOB_ID": "j1", "BIBI_EPHEMERAL": "1"}
    commit_sha, branch = _wrapper._commit_worktree(env)
    assert commit_sha == "abc123" and branch == "agent/s"
    assert len(removed) == 1


def test_finish_in_place_skips_commit_but_still_sets_output_ref(tmp_path):
    # Regressionstest für den im Plan-Review gefundenen Bug: eine frühere
    # Fassung unterdrückte den Commit, indem sie BIBI_REPO_ROOT wegließ — das
    # brach output_ref STILL mit (dieselbe Variable, ganz anderer Zweck in
    # _finish() unten), Journal-Transkripte für TEST-Läufe wären für immer
    # leer geblieben. BIBI_IN_PLACE ist jetzt das eigene, unabhängige Gate für
    # NUR den Commit — BIBI_REPO_ROOT bleibt gesetzt und muss output_ref
    # weiterhin korrekt berechnen.
    db_path = tmp_path / "jobs.sqlite"
    out_path = tmp_path / "data" / "job" / "z14" / "output.jsonl"
    out_path.parent.mkdir(parents=True)
    out_path.write_text('{"t": 1.0, "s": "out", "line": "hi"}\n', encoding="utf-8")
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("z14", "z14", "z14.md", "job", "echo hi", "running"),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z14", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "1",
           "BIBI_IN_PLACE": "1", "BIBI_REPO_ROOT": str(tmp_path),
           "BIBI_WORKTREE": str(tmp_path), "BIBI_JOB_SLUG": "z14",
           "BIBI_OUTPUT_PATH": str(out_path)}

    _wrapper._finish(env, 0, "normal")

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, output_ref FROM jobs WHERE id='z14'").fetchone()
    journal_row = c2.execute(
        "SELECT commit_sha, branch FROM journal WHERE slug='z14' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    c2.close()
    assert row["status"] == "complete"
    assert row["output_ref"] == str(out_path.relative_to(tmp_path).as_posix())
    assert journal_row["commit_sha"] is None
    assert journal_row["branch"] is None


def test_finish_killed_outcome_maps_to_killed_by_user(tmp_path):
    # User-Fund 2026-07-13 ("KILL führt nicht zum Status Wechsel"): _on_sigterm()
    # (run_app()/run_job()) meldet jetzt outcome="killed" statt eine
    # BaseException durchzureichen, die main()s except Exception umging und
    # _finish() nie erreichte. _finish() selbst muss "killed" korrekt auf
    # status=killed, reason=by_user abbilden (spiegelt job_kill()s Konvention).
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("z13", "z13", "z13.md", "job", "echo hi", "running"),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z13", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "1"}

    _wrapper._finish(env, -15, "killed")

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, reason FROM jobs WHERE id='z13'").fetchone()
    c2.close()
    assert row["status"] == "killed"
    assert row["reason"] == "by_user"


def test_finish_failed_uses_explicit_error_seconds_override(tmp_path):
    # bibi.job.Failed(seconds=10) → pump() spiegelt es in BIBI_ERROR_SECONDS —
    # _finish() muss das als exakte Wartezeit nehmen, Backoff-Strategie/base
    # komplett ignorieren (analog defer_time bei bibi.job.Deferred).
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("z20", "z20", "z20.md", "job", "echo hi", "running", 0, 3),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z20", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "3",
           "BIBI_ERROR_SECONDS": "10", "BIBI_ERROR_TIME": "999", "BIBI_BACKOFF": "exponential"}

    before = time.time()
    _wrapper._finish(env, 1, "normal")
    after = time.time()

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, next_fire_at FROM jobs WHERE id='z20'").fetchone()
    c2.close()
    assert row["status"] == "failed"
    assert before + 10 <= row["next_fire_at"] <= after + 10


def test_finish_deferred_falls_back_to_default_defer_time(tmp_path):
    # Kein BIBI_DEFER_TIME (weder Deferred(seconds=N) noch Frontmatter
    # defer_time:) -> letzter Fallback ist DEFAULT_DEFER_TIME (360s), nicht
    # mehr der alte hartkodierte 60s-Wert.
    from bibi.schedule.models import DEFAULT_DEFER_TIME
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("z22", "z22", "z22.md", "job", "echo hi", "running"),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z22", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "1"}

    before = time.time()
    _wrapper._finish(env, 0, "deferred")
    after = time.time()

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, next_fire_at FROM jobs WHERE id='z22'").fetchone()
    c2.close()
    assert row["status"] == "deferred"
    assert DEFAULT_DEFER_TIME == 360
    assert before + DEFAULT_DEFER_TIME <= row["next_fire_at"] <= after + DEFAULT_DEFER_TIME


def test_finish_failed_falls_back_to_default_base_without_any_override(tmp_path):
    # Weder Failed(seconds=N) noch Frontmatter error_time: noch BIBI_RETRY_BASE
    # gesetzt -> letzter Fallback ist backoff.DEFAULT_BASE (180s), nicht mehr
    # der alte Wert (30s).
    from bibi.schedule import backoff as _backoff
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("z23", "z23", "z23.md", "job", "echo hi", "running", 0, 3),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z23", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "3"}

    before = time.time()
    _wrapper._finish(env, 1, "normal")
    after = time.time()

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, next_fire_at FROM jobs WHERE id='z23'").fetchone()
    c2.close()
    assert row["status"] == "failed"
    assert _backoff.DEFAULT_BASE == 180.0
    assert before + 180 <= row["next_fire_at"] <= after + 180


def test_finish_failed_uses_error_time_frontmatter_default(tmp_path):
    # Ohne expliziten seconds-Override (kein BIBI_ERROR_SECONDS) zählt der
    # Schedule-Frontmatter-Wert (error_time: → BIBI_ERROR_TIME) als Basis.
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("z21", "z21", "z21.md", "job", "echo hi", "running", 0, 3),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z21", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "3", "BIBI_ERROR_TIME": "10"}

    before = time.time()
    _wrapper._finish(env, 1, "normal")
    after = time.time()

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, next_fire_at FROM jobs WHERE id='z21'").fetchone()
    c2.close()
    assert row["status"] == "failed"
    assert before + 10 <= row["next_fire_at"] <= after + 10


def test_finish_exhausted_retries_reaches_error_not_stuck_running(tmp_path):
    # Bug gefunden bei PLAN-28 (erstmals beobachtet mit attempts=0, betrifft
    # aber jeden Job, der seine Retries je ausschöpft): "error" ist von
    # "running" aus KEIN gültiger lifecycle.py-Übergang (nur failed
    # --exhaust--> error) — ein direkter Report wurde von report_status() als
    # "invalid" verworfen, OHNE Exception, OHNE Journal-Eintrag — der Job
    # blieb für immer sichtbar "running", obwohl der Prozess längst beendet
    # war. _finish() muss jetzt erst den gültigen Zwischenschritt
    # running→failed melden, dann sofort synchron failed→error.
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("z11", "z11", "z11.md", "job", "echo hi", "running", 0, 0),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z11", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "0"}

    _wrapper._finish(env, 1, "normal")

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, reason, exit_code FROM jobs WHERE id='z11'").fetchone()
    jrows = job_db.list_journal(c2)
    c2.close()
    assert row["status"] == "error"
    assert row["reason"] == "nonzero_exit"
    assert row["exit_code"] == 1
    # Genau ein Journal-Eintrag — der transiente "failed"-Zwischenschritt ist
    # nicht TERMINAL, erzeugt also keinen zweiten (doppelten) Eintrag.
    assert len(jrows) == 1 and jrows[0]["status"] == "error"


def test_finish_retriable_failure_unaffected_by_exhaustion_fix(tmp_path):
    # Regressionsschutz: die normale (noch nicht erschöpfte) Retry-Meldung
    # bleibt unverändert ein einzelner running→failed-Report mit Backoff.
    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, attempts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("z12", "z12", "z12.md", "job", "echo hi", "running", 0, 3),
    )
    c.close()
    env = {"BIBI_JOB_ID": "z12", "BIBI_SCHEDULER_DB_PATH": str(db_path),
           "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "3"}

    _wrapper._finish(env, 1, "normal")

    c2 = job_db.connect(db_path)
    row = c2.execute(
        "SELECT status, attempt, next_fire_at FROM jobs WHERE id='z12'").fetchone()
    jrows = job_db.list_journal(c2)
    c2.close()
    assert row["status"] == "failed"
    assert row["attempt"] == 1
    assert row["next_fire_at"] is not None
    assert jrows == []  # "failed" ist nicht terminal, noch kein Journal-Eintrag


# ── E2E: run_app mit stdout-Signalen ─────────────────────────────────────────


@pytest.mark.slow
def test_run_app_non_bibi_stdout_to_output(tmp_path):
    """Nicht-BIBI-Zeilen landen in output.jsonl, BIBI-Zeilen nicht."""
    import sys as _sys
    from bibi import wrapper
    from bibi.wrapper import output as wp_output

    script = tmp_path / "job.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('hallo\\n'); sys.stdout.flush()\n"
        'sys.stdout.write(\'BIBI:{"name":"running"}\\n\'); sys.stdout.flush()\n'
    )

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "t1",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
    }
    code = wrapper.run_app(env)
    assert code == 0
    lines = wp_output.lines(out)
    assert "hallo" in lines
    assert not any("BIBI:" in l for l in lines)


@pytest.mark.slow
def test_run_app_deferred_via_bibi_job_exception(tmp_path):
    """bibi.job.Deferred → BIBI:deferred-Signal → outcome=deferred → status=deferred in DB."""
    import sys as _sys
    from bibi import wrapper as _wrapper

    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempts, backoff, "
        "silence_timeout, hitl_timeout) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("d1", "d1", "d1.md", "job", "echo hi", "running", 1, "fixed", 3600, 172800),
    )
    c.close()

    script = tmp_path / "defer_job.py"
    script.write_text("import bibi.job\nraise bibi.job.Deferred(seconds=30)\n")

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "d1",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
        "BIBI_SCHEDULER_DB_PATH": str(db_path),
    }
    _wrapper.run_app(env)

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status FROM jobs WHERE id='d1'").fetchone()
    c2.close()
    assert row["status"] == "deferred"


@pytest.mark.slow
def test_run_app_failed_via_bibi_job_exception_with_seconds(tmp_path):
    """bibi.job.Failed(seconds=N) → BIBI:failed-Signal → next_fire_at ≈ now+N,
    unabhängig von backoff/attempts-Skalierung (das Pendant zu Deferred)."""
    import sys as _sys
    from bibi import wrapper as _wrapper

    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, attempt, attempts, "
        "backoff, silence_timeout, hitl_timeout) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("f1", "f1", "f1.md", "job", "echo hi", "running", 0, 2, "fixed", 3600, 172800),
    )
    c.close()

    script = tmp_path / "fail_job.py"
    script.write_text("import bibi.job\nraise bibi.job.Failed(seconds=10)\n")

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "f1",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
        "BIBI_SCHEDULER_DB_PATH": str(db_path),
        "BIBI_ATTEMPT": "0", "BIBI_ATTEMPTS": "2",
    }
    before = time.time()
    _wrapper.run_app(env)
    after = time.time()

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, next_fire_at FROM jobs WHERE id='f1'").fetchone()
    c2.close()
    assert row["status"] == "failed"
    assert before + 10 <= row["next_fire_at"] <= after + 10


@pytest.mark.slow
def test_run_job_real_sigterm_reports_killed_by_user(tmp_path):
    """Echtes SIGTERM an den Wrapper-Prozess (simuliert worker.py::_terminate()s
    os.killpg gegen die Wrapper-Prozessgruppe) — User-Fund 2026-07-13: führte
    vorher zu einer BaseException (SystemExit), die main()s except Exception
    umging — _finish() lief nie, der Job blieb für immer "running"."""
    import os
    import signal as _signal
    import sys as _sys
    from bibi import wrapper as _wrapper

    db_path = tmp_path / "jobs.sqlite"
    c = job_db.connect(db_path)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("k1", "k1", "k1.md", "job", "sleep 30", "running"),
    )
    c.close()

    script = tmp_path / "long_job.py"
    script.write_text("import time\ntime.sleep(30)\n")

    out = tmp_path / "output.jsonl"
    env = {
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_ID": "k1",
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_WORKTREE": str(tmp_path),
        "BIBI_JOB_CMD": f"{_sys.executable} {script}",
        "BIBI_SCHEDULER_DB_PATH": str(db_path),
    }

    def _send_sigterm_soon():
        time.sleep(1.0)
        os.kill(os.getpid(), _signal.SIGTERM)

    threading.Thread(target=_send_sigterm_soon, daemon=True).start()

    started = time.time()
    code = _wrapper.run_job(env)
    elapsed = time.time() - started

    assert elapsed < 10.0  # deutlich vor den vollen 30s beendet — echt gekillt
    assert code != 0

    c2 = job_db.connect(db_path)
    row = c2.execute("SELECT status, reason FROM jobs WHERE id='k1'").fetchone()
    c2.close()
    assert row["status"] == "killed"
    assert row["reason"] == "by_user"
