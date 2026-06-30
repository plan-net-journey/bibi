"""PLAN-11.3: stdout-Signalprotokoll — Parser + Handler (unit tests, kein Subprocess)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bibi.wrapper import _parse_bibi_line, _handle_signal
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
    assert row["app_url"] == "http://127.0.0.1:9100/"


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
    assert row["app_url"] == "http://127.0.0.1:9200/"


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
