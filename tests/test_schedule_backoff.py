"""Retry-Backoff (DESIGN §5.5; PLAN-3 §3.5)."""

from __future__ import annotations

from bibi.schedule import backoff


def test_fixed():
    assert backoff.delay("fixed", 3, base=10) == 10


def test_linear():
    assert backoff.delay("linear", 1, base=10) == 10
    assert backoff.delay("linear", 3, base=10) == 30


def test_exponential():
    assert backoff.delay("exponential", 1, base=10) == 10
    assert backoff.delay("exponential", 2, base=10) == 20
    assert backoff.delay("exponential", 3, base=10) == 40


def test_unknown_strategy_is_fixed():
    assert backoff.delay("wild", 5, base=7) == 7


def test_attempt_zero_is_no_delay():
    assert backoff.delay("linear", 0, base=10) == 0


def test_default_base_is_180():
    # error-time-Default (§5.5) — nur der letzte Fallback, wenn weder ein
    # expliziter bibi.job.Failed(seconds=N) noch Schedule-Frontmatter
    # (error_time:) noch der globale BIBI_RETRY_BASE-Env etwas vorgeben.
    assert backoff.DEFAULT_BASE == 180.0


def test_delay_uses_default_base_when_omitted():
    assert backoff.delay("fixed", 1) == 180.0
