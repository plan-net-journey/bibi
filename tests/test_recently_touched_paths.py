"""Reine mtime-Filter-Logik für PLAN-30 Ebene 4 (G1) — kein Git-Aufruf, volle
Testbarkeit mit tmp_path + os.utime() (anders als test_git_ops.py, das echte
Git-IO braucht und deshalb @pytest.mark.slow ist)."""

from __future__ import annotations

import os
from pathlib import Path

from bibi import git_ops


def _touch_at(path: Path, mtime: float) -> None:
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_recently_touched_within_window(tmp_path: Path):
    _touch_at(tmp_path / "a.md", mtime=1000.0)
    out = git_ops.recently_touched_paths(tmp_path, ["a.md"], within_s=120, now=1050.0)
    assert out == {"a.md"}


def test_not_recently_touched_outside_window(tmp_path: Path):
    _touch_at(tmp_path / "a.md", mtime=1000.0)
    out = git_ops.recently_touched_paths(tmp_path, ["a.md"], within_s=120, now=1200.0)
    assert out == set()


def test_exactly_at_boundary_is_not_recent(tmp_path: Path):
    # now - mtime < within_s (strikt) — exakt am Rand zählt nicht mehr als "kürzlich".
    _touch_at(tmp_path / "a.md", mtime=1000.0)
    out = git_ops.recently_touched_paths(tmp_path, ["a.md"], within_s=120, now=1120.0)
    assert out == set()


def test_missing_path_is_not_touched(tmp_path: Path):
    out = git_ops.recently_touched_paths(tmp_path, ["nope.md"], within_s=120, now=1000.0)
    assert out == set()


def test_mixed_paths_returns_only_recent_ones(tmp_path: Path):
    _touch_at(tmp_path / "recent.md", mtime=1000.0)
    _touch_at(tmp_path / "old.md", mtime=100.0)
    out = git_ops.recently_touched_paths(
        tmp_path, ["recent.md", "old.md", "missing.md"], within_s=120, now=1050.0)
    assert out == {"recent.md"}


def test_empty_paths_returns_empty_set(tmp_path: Path):
    assert git_ops.recently_touched_paths(tmp_path, [], within_s=120, now=1000.0) == set()


def test_now_defaults_to_real_time_and_marks_freshly_written_file(tmp_path: Path):
    p = tmp_path / "fresh.md"
    p.write_text("x", encoding="utf-8")
    out = git_ops.recently_touched_paths(tmp_path, ["fresh.md"], within_s=120)
    assert out == {"fresh.md"}
