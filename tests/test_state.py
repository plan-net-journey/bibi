"""Tests für bibi.state (cwd-Wahrheit + .state.md-Mirror)."""

from __future__ import annotations

import os
from pathlib import Path

from bibi import state


def test_get_path_none_at_repo_root(team_repo: Path):
    # cwd ist der Repo-Root, nicht in vault/ → kein aktiver Case.
    assert state.get_path() is None


def test_get_path_inside_case(team_repo: Path):
    case = team_repo / "vault" / "case" / "20260624.Foo-deadbeef"
    case.mkdir(parents=True)
    os.chdir(case)
    assert state.get_path() == "case/20260624.Foo-deadbeef"


def test_set_path_writes_mirror(team_repo: Path):
    state.set_path("case/foo-123")
    assert state.read()["path"] == "case/foo-123"


def test_set_path_none_removes_mirror(team_repo: Path):
    state.set_path("case/foo-123")
    state.set_path(None)
    assert "path" not in state.read()


def test_read_defaults_without_file(team_repo: Path):
    s = state.read()
    assert s["auto_sync"] == "off"
    assert s["sync_conflict"] is False


def test_auto_sync_roundtrip(team_repo: Path):
    assert state.get_auto_sync() is False
    state.set_auto_sync(True)
    assert state.get_auto_sync() is True
    state.set_auto_sync(False)
    assert state.get_auto_sync() is False


def test_auto_sync_was_never_set_true_before_any_write(team_repo: Path):
    assert state.auto_sync_was_never_set() is True


def test_auto_sync_was_never_set_false_after_explicit_off(team_repo: Path):
    # Wichtig: auch ein bewusstes "off" zählt als "gesetzt" — der
    # scheduler-Default (daemon_cmd.py) darf ein explizites Abschalten nicht
    # überschreiben, nur die stille Werkseinstellung.
    state.set_auto_sync(False)
    assert state.auto_sync_was_never_set() is False


def test_auto_sync_was_never_set_false_after_explicit_on(team_repo: Path):
    state.set_auto_sync(True)
    assert state.auto_sync_was_never_set() is False


def test_sync_conflict_roundtrip(team_repo: Path):
    assert state.get_sync_conflict() is False
    state.set_sync_conflict(True)
    assert state.get_sync_conflict() is True


def test_state_file_lands_in_dot_claude(team_repo: Path):
    state.set_auto_sync(True)
    assert (team_repo / ".claude" / ".state.md").exists()
