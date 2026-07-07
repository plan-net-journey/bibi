"""Merge-Commit-Identität beim Merge-back (PLAN-21 Befund 8).

Bewusst getrennt von tests/test_mergeback.py (dort pytestmark = slow) — dieser
Test braucht nur lokale Git-Objekte (kein Origin), darum als eigene schnelle
Datei mit derselben tmp_path-Repo-Konstruktion wie tests/test_worktree.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi.daemon import mergeback, worktree


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_merge_back_commits_as_bibi_identity(repo: Path):
    work = repo / "data" / "worktrees"
    path = worktree.prepare(repo_root=repo, work_dir=work, slug="run1")
    (path / "new.txt").write_text("hi\n")
    worktree.commit(worktree=path, message="add", slug="run1")

    result = mergeback.merge_back(repo_root=repo, slug="run1", trunk="trunk")

    assert result.status == "merged"
    author = _git(repo, "log", "-1", "--format=%an <%ae>")
    assert author == "bibi/run1 <bibi@local>"
