"""MD → ScheduleSpec (DESIGN §5.2/§5.3; PLAN-3 §3.1; PLAN-10 Stufe 10.0)."""

from __future__ import annotations

from pathlib import Path

from bibi.schedule.models import Kind
from bibi.schedule.parser import parse_text

P = Path("vault/case/hello/README.md")


def _parse(text: str, path: Path = P):
    return parse_text(text, schedule_ref=path.as_posix(), path=path)


def test_skip_when_no_trigger():
    r = _parse("---\njob: echo hi\n---\nbody")
    assert r.is_skip and r.spec is None and r.error is None


def test_job_with_cron():
    r = _parse('---\nschedule: "0 9 * * *"\njob: "echo hi"\n---\n')
    assert r.is_ok
    assert r.spec.kind is Kind.JOB
    assert r.spec.payload == "echo hi"
    assert r.spec.schedule == "0 9 * * *"
    assert r.spec.at is None


def test_job_claude_prefix_with_model():
    # `job: claude: <prompt>` — Prefix-Expansion, Kind bleibt JOB.
    r = _parse('---\nschedule: now\njob: "claude: Antworte hallo"\nmodel: claude-haiku-4-5-20251001\n---\n')
    assert r.is_ok
    assert r.spec.kind is Kind.JOB
    assert r.spec.payload == "claude: Antworte hallo"
    assert r.spec.schedule == "now"
    assert r.spec.model == "claude-haiku-4-5-20251001"


def test_job_claude_prefix_default_model():
    r = _parse('---\nschedule: never\njob: "claude: x"\n---\n')
    assert r.is_ok
    assert r.spec.kind is Kind.JOB
    assert r.spec.model == "claude-sonnet-4-6"


def test_old_claude_key_rejected():
    # `claude:` als eigener Frontmatter-Key ist nicht mehr gültig.
    r = _parse('---\nschedule: now\nclaude: "Antworte hallo"\n---\n')
    assert r.is_error
    assert "job:" in r.error


def test_special_schedules_not_cron_validated():
    for val in ("now", "startup", "never", "autostart"):
        r = _parse(f'---\nschedule: {val}\njob: "echo hi"\n---\n')
        assert r.is_ok, val
        assert r.spec.schedule == val


def test_at_iso_normalised_to_naive():
    r = _parse('---\nat: "2026-07-01T09:00:00"\njob: "echo hi"\n---\n')
    assert r.is_ok
    assert r.spec.at.startswith("2026-07-01T09:00")
    assert r.spec.schedule is None


def test_error_both_schedule_and_at():
    r = _parse('---\nschedule: now\nat: "2026-07-01T09:00"\njob: "x"\n---\n')
    assert r.is_error and "genau einen" in r.error


def test_error_bad_cron():
    r = _parse('---\nschedule: "not a cron"\njob: "x"\n---\n')
    assert r.is_error and "cron" in r.error.lower()


def test_error_bad_at():
    r = _parse('---\nat: "not-a-date"\njob: "x"\n---\n')
    assert r.is_error and "at:" in r.error


def test_error_no_type():
    r = _parse('---\nschedule: now\n---\n')
    assert r.is_error and "job:" in r.error


def test_error_multiple_types_now_impossible():
    # Mit nur noch `job:` als Key sind mehrere Typen unmöglich.
    # `claude:` als Key ist kein Typ mehr → kein Error über multiple types, sondern kein Typ.
    r = _parse('---\nschedule: now\nclaude: "y"\n---\n')
    assert r.is_error  # kein job: → kein Typ


def test_error_bad_priority_type():
    r = _parse('---\nschedule: now\njob: "x"\npriority: "high"\n---\n')
    assert r.is_error and "priority" in r.error


def test_bool_priority_rejected():
    r = _parse('---\nschedule: now\njob: "x"\npriority: true\n---\n')
    assert r.is_error and "bool" in r.error


def test_error_bad_backoff():
    r = _parse('---\nschedule: now\njob: "x"\nbackoff: wild\n---\n')
    assert r.is_error and "backoff" in r.error


def test_slug_from_folder_for_readme():
    r = _parse('---\nschedule: now\njob: "x"\n---\n', path=Path("vault/case/hello/README.md"))
    assert r.spec.slug == "hello"
    assert r.slug_explicit is False


def test_slug_from_stem_for_named_file():
    r = _parse('---\nschedule: now\njob: "x"\n---\n', path=Path("vault/case/daily.md"))
    assert r.spec.slug == "daily"


def test_explicit_slug_wins():
    r = _parse('---\nslug: custom\nschedule: now\njob: "x"\n---\n')
    assert r.spec.slug == "custom"
    assert r.slug_explicit is True


def test_lifecycle_knobs_parsed():
    r = _parse('---\nschedule: now\njob: "x"\nattempts: 3\nwall_time: 30\nsilence_timeout: 60\nbackoff: exponential\n---\n')
    assert r.spec.attempts == 3
    assert r.spec.wall_time == 30
    assert r.spec.silence_timeout == 60
    assert r.spec.backoff == "exponential"
