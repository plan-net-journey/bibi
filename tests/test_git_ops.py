"""Tests für bibi.git_ops (commit-scope, integrate, push) gegen echtes bare-Origin."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bibi import git_ops

import pytest
pytestmark = pytest.mark.slow


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def clone(origin: Path, dest: Path) -> Path:
    """Zweiter Arbeits-Clone des Origins (für Konflikt-Szenarien)."""
    _sh(dest.parent, "clone", "-q", str(origin), dest.name)
    _sh(dest, "config", "user.name", "Other")
    _sh(dest, "config", "user.email", "other@example.com")
    return dest


def _head_msg(cwd: Path) -> str:
    return _sh(cwd, "log", "-1", "--pretty=%s").strip()


def _head_files(cwd: Path) -> set[str]:
    out = _sh(cwd, "show", "--name-only", "--pretty=format:", "HEAD")
    return {l for l in out.strip().splitlines() if l}


# --- current_branch ---

def test_current_branch(repo_with_origin):
    root, _ = repo_with_origin
    assert git_ops.current_branch() == "trunk"


# --- stage_and_commit: scope ---

def test_commit_full_scope(repo_with_origin):
    root, _ = repo_with_origin
    (root / "newfile.txt").write_text("x", encoding="utf-8")
    committed = git_ops.stage_and_commit(None, "full commit")
    assert committed is True
    assert _head_msg(root) == "full commit"
    assert "newfile.txt" in _head_files(root)


def test_commit_path_scope_excludes_outside_changes(repo_with_origin):
    root, _ = repo_with_origin
    case = root / "vault" / "case" / "20260624.Foo-deadbeef"
    case.mkdir(parents=True)
    (case / "README.md").write_text("# Foo\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("nope", encoding="utf-8")  # außerhalb des Scope

    committed = git_ops.stage_and_commit(case, "save: Foo")
    assert committed is True
    files = _head_files(root)
    assert any("Foo" in f for f in files)
    assert "unrelated.txt" not in files
    # die scope-fremde Änderung bleibt uncommitted
    assert "unrelated.txt" in _sh(root, "status", "--porcelain")


def test_commit_nothing_to_commit(repo_with_origin):
    root, _ = repo_with_origin
    assert git_ops.stage_and_commit(None, "leer") is False


# --- dirty_paths / stage_and_commit_paths (PLAN-25 Befund 8) ---------------

def test_dirty_paths_lists_modified_and_untracked(repo_with_origin):
    root, _ = repo_with_origin
    (root / "pyproject.toml").write_text("MODIFIED\n", encoding="utf-8")
    (root / "new.txt").write_text("new", encoding="utf-8")
    paths = git_ops.dirty_paths()
    assert set(paths) == {"pyproject.toml", "new.txt"}


def test_dirty_paths_empty_on_clean_tree(repo_with_origin):
    assert git_ops.dirty_paths() == []


def test_stage_and_commit_paths_only_stages_listed_paths(repo_with_origin):
    root, _ = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    committed = git_ops.stage_and_commit_paths(["a.txt"], "sync: a only")
    assert committed is True
    files = _head_files(root)
    assert "a.txt" in files and "b.txt" not in files
    assert "b.txt" in _sh(root, "status", "--porcelain")  # bleibt uncommitted


def test_stage_and_commit_paths_empty_list_is_noop(repo_with_origin):
    root, _ = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    assert git_ops.stage_and_commit_paths([], "sync: nothing") is False
    assert "a.txt" in _sh(root, "status", "--porcelain")


# --- integrate + push ---

def test_push_when_ahead(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    git_ops.stage_and_commit(None, "local change")
    ok, kind = git_ops.integrate("trunk")
    assert ok and kind is None
    ok2, _ = git_ops.push("trunk")
    assert ok2
    # Origin trägt den Commit jetzt.
    assert "local change" in _sh(origin, "log", "-1", "--pretty=%s")


def test_integrate_fast_forwards_when_behind(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = clone(origin, tmp_path / "other")
    (other / "remote.txt").write_text("r", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote change")
    _sh(other, "push", "-q", "origin", "trunk")

    ok, kind = git_ops.integrate("trunk")
    assert ok and kind is None
    assert (root / "remote.txt").exists()  # lokal fast-forwarded


def test_integrate_rebases_on_divergence_no_conflict(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = clone(origin, tmp_path / "other")
    (other / "remote.txt").write_text("r", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote"); _sh(other, "push", "-q", "origin", "trunk")

    (root / "local.txt").write_text("l", encoding="utf-8")  # andere Datei → kein Konflikt
    git_ops.stage_and_commit(None, "local")
    ok, kind = git_ops.integrate("trunk")
    assert ok and kind is None
    assert (root / "remote.txt").exists() and (root / "local.txt").exists()


def test_integrate_conflict_aborts_and_signals(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = clone(origin, tmp_path / "other")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit"); _sh(other, "push", "-q", "origin", "trunk")

    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")  # gleiche Datei
    git_ops.stage_and_commit(None, "local edit")
    ok, kind = git_ops.integrate("trunk")
    assert ok is False
    assert kind == "conflict"
    # kein hängengebliebener Rebase
    assert not (root / ".git" / "rebase-merge").exists()


# --- strategy="merge" (bot-robuster Hintergrund-Pull, s. Synchronizer) ---

def test_integrate_merges_on_divergence_no_conflict(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = clone(origin, tmp_path / "other")
    (other / "remote.txt").write_text("r", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote"); _sh(other, "push", "-q", "origin", "trunk")

    (root / "local.txt").write_text("l", encoding="utf-8")  # andere Datei → kein Konflikt
    git_ops.stage_and_commit(None, "local")
    ok, kind = git_ops.integrate("trunk", strategy="merge")
    assert ok and kind is None
    assert (root / "remote.txt").exists() and (root / "local.txt").exists()
    # tatsächlich gemerged (Merge-Commit, zwei Eltern) statt umbasiert
    parents = _sh(root, "log", "-1", "--pretty=%P").strip().split()
    assert len(parents) == 2


def test_integrate_merge_conflict_aborts_and_signals(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = clone(origin, tmp_path / "other")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit"); _sh(other, "push", "-q", "origin", "trunk")

    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")  # gleiche Datei
    git_ops.stage_and_commit(None, "local edit")
    ok, kind = git_ops.integrate("trunk", strategy="merge")
    assert ok is False
    assert kind == "conflict"
    # kein hängengebliebener Merge
    assert not (root / ".git" / "MERGE_HEAD").exists()


# --- orchestration: commit_and_push ---

def test_commit_and_push_pushes_when_enabled(repo_with_origin):
    root, origin = repo_with_origin
    (root / "x.txt").write_text("x", encoding="utf-8")
    ok, log, kind = git_ops.commit_and_push(None, "save: full", do_push=True)
    assert ok and kind is None
    assert "save: full" in _sh(origin, "log", "-1", "--pretty=%s")


def test_commit_and_push_skips_push_when_disabled(repo_with_origin):
    root, origin = repo_with_origin
    (root / "x.txt").write_text("x", encoding="utf-8")
    ok, log, kind = git_ops.commit_and_push(None, "save: full", do_push=False)
    assert ok and kind is None
    assert _head_msg(root) == "save: full"          # lokal committed
    assert "save: full" not in _sh(origin, "log", "-1", "--pretty=%s")  # aber nicht gepusht


# --- Phase 1.5: dirty/rebase-Status + Konflikt-Auflösung ---

def _diverge_on_pyproject(root: Path, origin: Path, tmp_path: Path):
    """Remote und lokal ändern dieselbe Datei → echter Rebase-Konflikt."""
    other = clone(origin, tmp_path / "other")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit")
    _sh(other, "push", "-q", "origin", "trunk")
    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "local edit")


def test_is_dirty(repo_with_origin):
    root, _ = repo_with_origin
    assert git_ops.is_dirty() is False
    (root / "x.txt").write_text("x", encoding="utf-8")
    assert git_ops.is_dirty() is True


def test_is_rebase_in_progress_false(repo_with_origin):
    assert git_ops.is_rebase_in_progress() is False


def test_integrate_keep_conflict_leaves_rebase(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _diverge_on_pyproject(root, origin, tmp_path)
    ok, kind = git_ops.integrate("trunk", keep_conflict=True)
    assert ok is False and kind == "conflict"
    assert git_ops.is_rebase_in_progress() is True            # NICHT abgebrochen
    assert "pyproject.toml" in git_ops.conflicted_files()


def test_continue_rebase_and_push_after_resolution(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _diverge_on_pyproject(root, origin, tmp_path)
    git_ops.integrate("trunk", keep_conflict=True)
    # KI-Auflösung simulieren: Marker auflösen
    (root / "pyproject.toml").write_text("RESOLVED\n", encoding="utf-8")
    ok, log, kind = git_ops.continue_rebase_and_push()
    assert ok and kind is None
    assert git_ops.is_rebase_in_progress() is False
    assert (root / "pyproject.toml").read_text() == "RESOLVED\n"
    # Origin trägt die aufgelöste Version
    assert "RESOLVED" in _sh(origin, "show", "trunk:pyproject.toml")


def test_abort_rebase(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _diverge_on_pyproject(root, origin, tmp_path)
    git_ops.integrate("trunk", keep_conflict=True)
    assert git_ops.is_rebase_in_progress() is True
    git_ops.abort_rebase()
    assert git_ops.is_rebase_in_progress() is False
    assert (root / "pyproject.toml").read_text() == "LOCAL\n"  # lokaler Stand zurück


def test_integrate_default_still_aborts(repo_with_origin, tmp_path):
    # save/close/done verlassen sich aufs Abbrechen (unverändert)
    root, origin = repo_with_origin
    _diverge_on_pyproject(root, origin, tmp_path)
    ok, kind = git_ops.integrate("trunk")  # keep_conflict=False default
    assert ok is False and kind == "conflict"
    assert git_ops.is_rebase_in_progress() is False
