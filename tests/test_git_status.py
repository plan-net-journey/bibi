"""working_tree_status(): geteilte Tree/Sync/Branch-Basis (PLAN-18 Stufe 18.0)
für CLI-Statusline, Heartbeat und die künftige Feed-Kopfzeile — ein einziger
``git status --porcelain=v2 --branch``-Aufruf, keine Farben/Rendering."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bibi.git_status import working_tree_status

import pytest
pytestmark = pytest.mark.slow


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _clone(origin: Path, dest: Path) -> Path:
    _sh(dest.parent, "clone", "-q", str(origin), dest.name)
    _sh(dest, "config", "user.name", "Other")
    _sh(dest, "config", "user.email", "other@example.com")
    return dest


def test_clean_synced(repo_with_origin):
    root, _ = repo_with_origin
    s = working_tree_status(root)
    assert s.tree == "clean" and s.sync == "synced" and s.branch == "trunk"


def test_oid_is_full_commit_hash(repo_with_origin):
    # PLAN-25 Befund 8-Nachtrag: die SYNC-Zeile soll den Commit-Hash zeigen
    # (# branch.oid <hash>) — bisher geparst, aber nie im Dataclass gehalten.
    root, _ = repo_with_origin
    s = working_tree_status(root)
    head = _sh(root, "rev-parse", "HEAD").strip()
    assert s.oid == head


def test_ahead_behind_counts_exposed(repo_with_origin, tmp_path):
    # ahead/behind wurden intern schon geparst (branch.ab), aber sofort
    # verworfen — landen jetzt im Dataclass statt nur in der groben
    # sync-Kategorie aufzugehen.
    root, origin = repo_with_origin
    other = _clone(origin, tmp_path / "other")
    (other / "r.txt").write_text("r", encoding="utf-8")
    _sh(other, "add", "-A")
    _sh(other, "commit", "-q", "-m", "remote")
    _sh(other, "push", "-q", "origin", "trunk")
    (root / "l.txt").write_text("l", encoding="utf-8")
    _sh(root, "add", "-A")
    _sh(root, "commit", "-q", "-m", "local")
    _sh(root, "fetch", "-q", "origin")
    s = working_tree_status(root)
    assert s.sync == "diverged"
    assert s.ahead == 1 and s.behind == 1


def test_modified_tree(repo_with_origin):
    root, _ = repo_with_origin
    (root / "dirty.md").write_text("x", encoding="utf-8")
    s = working_tree_status(root)
    assert s.tree == "modified"


def test_ahead(repo_with_origin):
    root, _ = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    _sh(root, "add", "-A")
    _sh(root, "commit", "-q", "-m", "local")
    s = working_tree_status(root)
    assert s.sync == "ahead"


def test_behind(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = _clone(origin, tmp_path / "other")
    (other / "r.txt").write_text("r", encoding="utf-8")
    _sh(other, "add", "-A")
    _sh(other, "commit", "-q", "-m", "remote")
    _sh(other, "push", "-q", "origin", "trunk")
    _sh(root, "fetch", "-q", "origin")  # Remote-Tracking aktualisieren, kein Merge
    s = working_tree_status(root)
    assert s.sync == "behind"


def test_diverged_both_ahead_and_behind(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = _clone(origin, tmp_path / "other")
    (other / "r.txt").write_text("r", encoding="utf-8")
    _sh(other, "add", "-A")
    _sh(other, "commit", "-q", "-m", "remote")
    _sh(other, "push", "-q", "origin", "trunk")
    (root / "l.txt").write_text("l", encoding="utf-8")
    _sh(root, "add", "-A")
    _sh(root, "commit", "-q", "-m", "local")
    _sh(root, "fetch", "-q", "origin")
    s = working_tree_status(root)
    assert s.sync == "diverged"


def test_none_outside_git_repo(tmp_path: Path):
    assert working_tree_status(tmp_path) is None
