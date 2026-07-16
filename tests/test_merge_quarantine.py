"""Persistente Merge-Quarantäne (PLAN-30 Ebene 2) — reine Speicher-Logik, kein Git."""

from __future__ import annotations

from pathlib import Path

from bibi.daemon import merge_quarantine


def test_get_missing_entry_is_none(tmp_path: Path):
    assert merge_quarantine.get(tmp_path, "agent/nope") is None


def test_record_failure_creates_then_increments(tmp_path: Path):
    first = merge_quarantine.record_failure(tmp_path, "agent/x", trunk_sha="sha1")
    assert first == merge_quarantine.Entry(trunk_sha="sha1", failures=1)
    second = merge_quarantine.record_failure(tmp_path, "agent/x", trunk_sha="sha2")
    assert second == merge_quarantine.Entry(trunk_sha="sha2", failures=2)
    assert merge_quarantine.get(tmp_path, "agent/x") == second


def test_clear_removes_entry_idempotently(tmp_path: Path):
    merge_quarantine.record_failure(tmp_path, "agent/x", trunk_sha="sha1")
    merge_quarantine.clear(tmp_path, "agent/x")
    assert merge_quarantine.get(tmp_path, "agent/x") is None
    merge_quarantine.clear(tmp_path, "agent/x")  # kein Fehler bei erneutem Clear


def test_escalated_excludes_branches_below_threshold(tmp_path: Path):
    for trunk_sha in ("sha1", "sha2"):  # nur 2 Fehlschläge — unter ESCALATE_AFTER
        merge_quarantine.record_failure(tmp_path, "agent/almost", trunk_sha=trunk_sha)
    assert merge_quarantine.escalated(tmp_path) == []


def test_escalated_includes_branches_at_threshold(tmp_path: Path):
    for trunk_sha in ("sha1", "sha2", "sha3"):
        merge_quarantine.record_failure(tmp_path, "agent/stuck", trunk_sha=trunk_sha)
    assert merge_quarantine.escalated(tmp_path) == ["agent/stuck"]


def test_escalated_sorted_and_multiple(tmp_path: Path):
    for branch in ("agent/z", "agent/a"):
        for trunk_sha in ("sha1", "sha2", "sha3"):
            merge_quarantine.record_failure(tmp_path, branch, trunk_sha=trunk_sha)
    assert merge_quarantine.escalated(tmp_path) == ["agent/a", "agent/z"]


def test_prune_keeps_only_listed_branches(tmp_path: Path):
    merge_quarantine.record_failure(tmp_path, "agent/keep", trunk_sha="sha1")
    merge_quarantine.record_failure(tmp_path, "agent/gone", trunk_sha="sha1")
    merge_quarantine.prune(tmp_path, keep_branches={"agent/keep"})
    assert merge_quarantine.get(tmp_path, "agent/keep") is not None
    assert merge_quarantine.get(tmp_path, "agent/gone") is None


def test_load_survives_missing_file(tmp_path: Path):
    assert merge_quarantine.get(tmp_path, "agent/x") is None


def test_load_survives_malformed_json(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "merge_quarantine.json").write_text("{not json", encoding="utf-8")
    assert merge_quarantine.get(tmp_path, "agent/x") is None


def test_load_survives_non_object_json(tmp_path: Path):
    # Review-Runde 3, Fund 3: "[]"/"null" warfen zuvor AttributeError auf
    # raw.items() und rissen den gesamten Merge-back mit, statt defensiv
    # auf "keine Quarantäne" zurückzufallen.
    (tmp_path / "data").mkdir()
    for content in ("[]", "null", '"a string"', "42"):
        (tmp_path / "data" / "merge_quarantine.json").write_text(content, encoding="utf-8")
        assert merge_quarantine.get(tmp_path, "agent/x") is None


def test_load_survives_entry_with_wrong_shape(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "merge_quarantine.json").write_text(
        '{"agent/x": "not-an-object"}', encoding="utf-8")
    assert merge_quarantine.get(tmp_path, "agent/x") is None
