"""Datenmodell der Scheduling-Domäne (DESIGN §5.2–§5.5; PLAN-3 §3.0)."""

from __future__ import annotations

import json

from bibi.schedule.models import (
    DEFAULT_CLAUDE_MODEL,
    JobRow,
    JournalEntry,
    Kind,
    Owner,
    Reason,
    ScheduleSpec,
    Status,
)


def test_status_values_match_design_5_4():
    assert {s.value for s in Status} == {
        "pending", "running", "failed", "error", "deferred",
        "inactive", "awaiting", "complete", "zombie", "killed",
    }


def test_kind_uses_renamed_types():
    # PLAN-3 §1.2: exec→job, prompt→claude; app fürs Modell vorhanden.
    assert {k.value for k in Kind} == {"job", "claude", "app"}
    assert "exec" not in {k.value for k in Kind}
    assert "prompt" not in {k.value for k in Kind}


def test_reason_root_causes_match_design_5_5():
    assert {r.value for r in Reason} == {
        "silence", "activity_timeout", "deferred_expired",
        "no_process", "by_user", "by_wall_time",
    }


def test_owner_values():
    assert {o.value for o in Owner} == {"scheduler", "worker"}


def test_strenum_is_json_and_str_friendly():
    # StrEnum: Werte landen ohne Konvertierung in JSON/SQLite.
    assert str(Status.PENDING) == "pending"
    assert json.dumps({"status": Status.RUNNING}) == '{"status": "running"}'
    assert Kind.CLAUDE == "claude"


def test_schedule_spec_defaults():
    s = ScheduleSpec(slug="hello", kind=Kind.CLAUDE, payload="Hallo?")
    assert s.priority == 0
    assert s.model == DEFAULT_CLAUDE_MODEL == "claude-sonnet-4-6"
    assert s.attempts == 1
    assert s.silence_timeout == 3600
    assert s.hitl_timeout == 48 * 3600
    assert s.at is None and s.schedule is None


def test_schedule_spec_is_frozen():
    s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="echo hi")
    try:
        s.slug = "y"  # type: ignore[misc]
    except Exception as exc:
        assert exc.__class__.__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("ScheduleSpec sollte frozen sein")


def test_job_row_minimal_and_output_ref():
    row = JobRow(id="ab12", slug="hello", kind=Kind.JOB, status=Status.PENDING)
    assert row.reason is None
    assert row.output_ref is None
    assert row.attempt == 0
    full = JobRow(
        id="ab12", slug="hello", kind=Kind.JOB, status=Status.KILLED,
        reason=Reason.BY_USER, output_ref="data/job/ab12/output.jsonl",
    )
    assert full.reason is Reason.BY_USER
    assert full.output_ref.endswith("output.jsonl")


def test_journal_entry_run_id_and_host_first_class():
    e = JournalEntry(
        run_id="hello:1", slug="hello", kind=Kind.CLAUDE, status=Status.COMPLETE,
        host="air2024", worker="w1", output_ref="data/job/ab12/output.jsonl",
    )
    assert e.run_id == "hello:1"
    assert e.host == "air2024"
    assert e.output_ref is not None  # referenziert, enthält nicht (§1.4)
