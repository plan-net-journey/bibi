"""Daemon-Aktivitätslog (PLAN-5 §5.1) — Formatter, emit, Setup, CLI-Render."""

from __future__ import annotations

import json
import logging

from bibi.daemon import activity


# ── reine Helfer ──────────────────────────────────────────────────────────────

def test_role_from_logger():
    assert activity.role_from_logger("bibi.daemon.synchronizer") == "synchronizer"
    assert activity.role_from_logger("bibi.worker") == "worker"
    assert activity.role_from_logger("bibi.daemon") == "daemon"


def test_human_line_compact_and_with_context():
    assert activity.human_line(ts="08:30:00", level="INFO", role="worker",
                               event="worker.pickup") == "08:30:00 INFO worker worker.pickup"
    line = activity.human_line(ts="08:30:01", level="ERROR", role="scheduler",
                               event="scheduler.rescan", msg="done",
                               slug="echo", run_id="r1", fields={"inserted": 2})
    assert "scheduler.rescan  done" in line
    assert "slug=echo" in line and "run=r1" in line and "inserted=2" in line


# ── Formatter (über echte LogRecords) ─────────────────────────────────────────

def _record(**bibi) -> logging.LogRecord:
    rec = logging.LogRecord("bibi.worker", logging.INFO, __file__, 1,
                            bibi.pop("msg", ""), None, None)
    rec.bibi = bibi
    return rec


def test_jsonl_formatter_emits_structured_object():
    rec = _record(msg="picked", event="worker.pickup", role="worker",
                  slug="echo", run_id="r1", fields={"exit": 0})
    obj = json.loads(activity.JsonlFormatter().format(rec))
    assert obj["event"] == "worker.pickup"
    assert obj["role"] == "worker"
    assert obj["slug"] == "echo" and obj["run_id"] == "r1"
    assert obj["msg"] == "picked" and obj["exit"] == 0
    assert obj["level"] == "INFO"
    assert obj["ts"]  # vorhanden + parsebar
    from datetime import datetime
    datetime.fromisoformat(obj["ts"])


def test_jsonl_role_falls_back_to_logger_name():
    rec = _record(event="x")  # kein role im Payload
    obj = json.loads(activity.JsonlFormatter().format(rec))
    assert obj["role"] == "worker"  # aus "bibi.worker"


def test_human_formatter_one_line():
    rec = _record(msg="tick", event="sync.pull", role="synchronizer",
                  fields={"ok": True})
    out = activity.HumanFormatter().format(rec)
    assert "\n" not in out
    assert "sync.pull" in out and "synchronizer" in out and "ok=True" in out


def test_render_jsonl_line_roundtrip():
    rec = _record(msg="done", event="scheduler.rescan", role="scheduler",
                  fields={"inserted": 1})
    jline = activity.JsonlFormatter().format(rec)
    human = activity.render_jsonl_line(jline)
    assert "scheduler.rescan" in human and "inserted=1" in human and "\n" not in human


def test_render_jsonl_line_tolerates_garbage():
    assert activity.render_jsonl_line("not json") == "not json"


# ── emit + setup_logging ──────────────────────────────────────────────────────

def test_emit_attaches_payload(caplog):
    logger = logging.getLogger("bibi.test.emit")
    with caplog.at_level(logging.INFO, logger="bibi.test.emit"):
        activity.emit(logger, logging.INFO, "worker.pickup", "hi",
                      role="worker", slug="echo", run_id="r9", exit=0)
    rec = caplog.records[-1]
    assert rec.bibi["event"] == "worker.pickup"
    assert rec.bibi["slug"] == "echo"
    assert rec.bibi["fields"] == {"exit": 0}


def test_setup_logging_two_sinks_and_writes_jsonl(tmp_path):
    log_dir = tmp_path / "daemon-log"
    path = activity.setup_logging(role_names=["worker"], log_dir=log_dir,
                                  to_stdout=True)
    try:
        assert path == log_dir / "daemon.jsonl"
        logger = logging.getLogger("bibi")
        # zwei Sinks: rotierende Datei + stdout
        kinds = {type(h).__name__ for h in logger.handlers}
        assert "RotatingFileHandler" in kinds and "StreamHandler" in kinds
        activity.emit(logging.getLogger("bibi.worker"), logging.INFO,
                      "worker.pickup", role="worker", slug="echo")
        for h in logger.handlers:
            h.flush()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines and json.loads(lines[-1])["event"] == "worker.pickup"
    finally:
        for h in list(logging.getLogger("bibi").handlers):
            logging.getLogger("bibi").removeHandler(h)


def test_setup_logging_is_idempotent(tmp_path):
    activity.setup_logging(log_dir=tmp_path / "a")
    activity.setup_logging(log_dir=tmp_path / "b")  # darf nicht doppelt verdrahten
    try:
        assert len(logging.getLogger("bibi").handlers) == 2
    finally:
        for h in list(logging.getLogger("bibi").handlers):
            logging.getLogger("bibi").removeHandler(h)
