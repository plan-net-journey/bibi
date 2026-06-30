"""Integrationstests für `bibi-ctrl save` (A10-Scope + Sync-Matrix §4.9)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from bibi import case_store, state
from bibi.ctrl import main

import pytest
pytestmark = pytest.mark.slow


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _origin_head(origin: Path) -> str:
    return _sh(origin, "log", "-1", "--pretty=%s").strip()


def _local_head(root: Path) -> str:
    return _sh(root, "log", "-1", "--pretty=%s").strip()


# --- A10: Scope ---

def test_save_no_active_case_full_scope(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    rc = main(["save", "--push"])
    assert rc == 0
    files = _sh(root, "show", "--name-only", "--pretty=format:", "HEAD")
    assert "a.txt" in files and "b.txt" in files  # ganzes Repo


def test_save_active_case_path_scope(repo_with_origin):
    root, origin = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    (root / "outside.txt").write_text("x", encoding="utf-8")  # scope-fremd
    os.chdir(folder)  # Case parken → aktiver Case
    rc = main(["save", "--push"])
    assert rc == 0
    files = _sh(root, "show", "--name-only", "--pretty=format:", "HEAD")
    assert any("AlphaFeature" in f for f in files.splitlines())
    assert "outside.txt" not in files
    assert "save: 20" in _local_head(root)  # "save: <YYYYmmdd…folder>"


# --- Sync-Matrix §4.9 ---

def test_save_auto_push_off_commits_but_does_not_push(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    rc = main(["save"])  # auto_sync default off, kein --push
    assert rc == 0
    assert _local_head(root).startswith("save:")     # lokal committed
    assert _origin_head(origin) == "init"            # NICHT gepusht


def test_save_auto_push_on_pushes(repo_with_origin):
    root, origin = repo_with_origin
    state.set_auto_sync(True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    rc = main(["save"])
    assert rc == 0
    assert _origin_head(origin).startswith("save:")   # gepusht


def test_save_force_push_overrides_off(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    rc = main(["save", "--push"])
    assert rc == 0
    assert _origin_head(origin).startswith("save:")


def test_save_custom_message(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    main(["save", "--push", "-m", "save: eigener Text"])
    assert _origin_head(origin) == "save: eigener Text"


def test_save_nothing_to_commit_is_ok(repo_with_origin):
    root, origin = repo_with_origin
    rc = main(["save", "--push"])
    assert rc == 0


# --- Konflikt ---

def test_save_conflict_sets_flag_and_returns_1(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = tmp_path / "other"
    _sh(tmp_path, "clone", "-q", str(origin), "other")
    _sh(other, "config", "user.name", "O"); _sh(other, "config", "user.email", "o@e.x")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote"); _sh(other, "push", "-q", "origin", "trunk")

    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")
    rc = main(["save", "--push"])
    assert rc == 1
    assert state.get_sync_conflict() is True
