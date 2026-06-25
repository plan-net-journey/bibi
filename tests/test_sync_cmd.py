"""Integrationstests für `bibi-ctrl sync …` (PLAN-1 §1.5, §4.9)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bibi import git_ops, state
from bibi.ctrl import main


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _origin_head(origin: Path) -> str:
    return _sh(origin, "log", "-1", "--pretty=%s").strip()


def _local_head(root: Path) -> str:
    return _sh(root, "log", "-1", "--pretty=%s").strip()


def _clone(origin: Path, dest: Path) -> Path:
    _sh(dest.parent, "clone", "-q", str(origin), dest.name)
    _sh(dest, "config", "user.name", "O"); _sh(dest, "config", "user.email", "o@e.x")
    return dest


def _remote_ahead(origin: Path, tmp_path: Path, fname="remote.txt"):
    other = _clone(origin, tmp_path / "other")
    (other / fname).write_text("r", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote change")
    _sh(other, "push", "-q", "origin", "trunk")


def _diverge(origin: Path, tmp_path: Path):
    other = _clone(origin, tmp_path / "other")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit")
    _sh(other, "push", "-q", "origin", "trunk")


# --- on/off ---

def test_sync_on_off(repo_with_origin):
    assert main(["sync", "on"]) == 0
    assert state.get_auto_sync() is True
    assert main(["sync", "off"]) == 0
    assert state.get_auto_sync() is False


# --- manueller sync (§4.9) ---

def test_sync_dirty_warns_no_commit(repo_with_origin, capsys):
    root, origin = repo_with_origin
    (root / "x.txt").write_text("x", encoding="utf-8")
    rc = main(["sync"])
    assert rc == 1
    assert "save" in capsys.readouterr().err.lower()
    assert _local_head(root) == "init"   # NICHT committet

def test_sync_clean_pulls(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _remote_ahead(origin, tmp_path)
    rc = main(["sync"])
    assert rc == 0
    assert (root / "remote.txt").exists()


def test_sync_pushes_local_ahead(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    git_ops.stage_and_commit(None, "local commit")
    rc = main(["sync"])
    assert rc == 0
    assert _origin_head(origin) == "local commit"


def test_sync_conflict_keeps_and_flags(repo_with_origin, tmp_path, capsys):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "local edit")
    rc = main(["sync"])
    assert rc == 1
    assert state.get_sync_conflict() is True
    assert git_ops.is_rebase_in_progress() is True
    assert "pyproject.toml" in capsys.readouterr().err


def test_sync_continue_resolves_and_clears_flag(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "local edit")
    main(["sync"])  # → Konflikt offen
    (root / "pyproject.toml").write_text("RESOLVED\n", encoding="utf-8")  # KI-Auflösung
    rc = main(["sync", "continue"])
    assert rc == 0
    assert git_ops.is_rebase_in_progress() is False
    assert state.get_sync_conflict() is False
    assert "RESOLVED" in _sh(origin, "show", "trunk:pyproject.toml")


def test_sync_abort_clears(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "local edit")
    main(["sync"])
    rc = main(["sync", "abort"])
    assert rc == 0
    assert git_ops.is_rebase_in_progress() is False


def test_sync_in_progress_guard(repo_with_origin, tmp_path, capsys):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "local edit")
    main(["sync"])           # Rebase offen
    rc = main(["sync"])      # erneuter sync → Guard
    assert rc == 1
    assert "continue" in capsys.readouterr().err


# --- Hooks (gated by auto_sync) ---

def test_hook_stop_noop_when_off(repo_with_origin):
    root, origin = repo_with_origin
    (root / "x.txt").write_text("x", encoding="utf-8")
    assert main(["sync", "hook-stop"]) == 0
    assert _origin_head(origin) == "init"   # nichts passiert


def test_hook_stop_commits_and_pushes_when_on(repo_with_origin):
    root, origin = repo_with_origin
    state.set_auto_sync(True)
    (root / "x.txt").write_text("x", encoding="utf-8")
    assert main(["sync", "hook-stop"]) == 0
    assert _origin_head(origin).startswith("auto:")  # transienter Auto-Commit


def test_hook_start_pulls_when_on(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    state.set_auto_sync(True)
    _remote_ahead(origin, tmp_path)
    main(["sync", "hook-start"])
    assert (root / "remote.txt").exists()


def test_hook_start_warns_on_conflict_flag(repo_with_origin, capsys):
    root, origin = repo_with_origin
    state.set_sync_conflict(True)
    rc = main(["sync", "hook-start"])
    assert rc == 1
    assert "sync" in capsys.readouterr().err.lower()
