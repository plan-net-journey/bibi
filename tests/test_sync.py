"""Tests für bibi.sync (geteilte Sync-Modus-Logik, §1.6 B)."""

from __future__ import annotations

from pathlib import Path

from bibi import state, sync


def test_auto_push_disabled_by_default(team_repo: Path):
    assert sync.auto_push_enabled() is False


def test_auto_push_follows_auto_sync_flag(team_repo: Path):
    state.set_auto_sync(True)
    assert sync.auto_push_enabled() is True
    state.set_auto_sync(False)
    assert sync.auto_push_enabled() is False
