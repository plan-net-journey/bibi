"""Merge-back ``agent/<slug>`` → trunk (PLAN-6 Slice A; Worker-Analyse §6)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from bibi.daemon import mergeback, worktree as wt

pytestmark = pytest.mark.slow


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


def _run_in_worktree(repo: Path, slug: str, filename: str, content: str) -> str:
    """Einen Job simulieren: frischer Worktree, Datei schreiben, committen → SHA."""
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug=slug)
    (path / filename).write_text(content)
    return wt.commit(worktree=path, message=f"{slug}: run", slug=slug)


def test_merge_back_fast_forward(repo: Path):
    sha = _run_in_worktree(repo, "run1", "note.md", "Witz\n")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "merged"
    # Kernkriterium (PLAN-6 §5.1): Commit von trunk aus erreichbar.
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)
    assert (repo / "note.md").read_text() == "Witz\n"
    assert res.trunk_sha == _git(repo, "rev-parse", "HEAD")


def test_merge_back_real_merge_after_trunk_advanced(repo: Path):
    sha = _run_in_worktree(repo, "run1", "note.md", "vom Job\n")
    # trunk rückt unabhängig vor (anderer Pfad, kein Konflikt):
    (repo / "other.txt").write_text("trunk moved\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk advance")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "merged"
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)
    assert (repo / "note.md").exists() and (repo / "other.txt").exists()


def test_merge_back_conflict_aborts_and_preserves(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")  # ändert dieselbe Datei
    trunk_before = _git(repo, "rev-parse", "HEAD")
    # trunk ändert dieselbe Datei anders → Konflikt:
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict")
    trunk_after = _git(repo, "rev-parse", "HEAD")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "conflict"
    # trunk unverändert (Merge sauber abgebrochen), Branch intakt:
    assert _git(repo, "rev-parse", "HEAD") == trunk_after
    assert "agent/run1" in _git(repo, "branch", "--list", "agent/run1")
    # kein laufender Merge mehr (MERGE_HEAD weg):
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert trunk_before != trunk_after  # sanity


def test_merge_back_up_to_date_when_no_commit(repo: Path):
    # echo-artiger Job: Worktree, aber keine Änderung → commit() == "".
    work = repo / "data" / "worktrees"
    wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "up_to_date"


def test_merge_back_missing_branch_is_error(repo: Path):
    res = mergeback.merge_back(repo_root=repo, slug="nope")
    assert res.status == "error"


def test_unmerged_agent_branches_lists_only_unmerged(repo: Path):
    _run_in_worktree(repo, "done", "a.md", "x\n")
    mergeback.merge_back(repo_root=repo, slug="done")        # gemergt
    _run_in_worktree(repo, "pending", "b.md", "y\n")          # nicht gemergt
    unmerged = mergeback.unmerged_agent_branches(repo_root=repo)
    assert unmerged == ["agent/pending"]


def test_unmerged_ignores_branch_without_new_commits(repo: Path):
    # Branch == trunk-HEAD (kein Commit) → nicht "unmerged".
    work = repo / "data" / "worktrees"
    wt.prepare(repo_root=repo, work_dir=work, slug="empty")
    assert mergeback.unmerged_agent_branches(repo_root=repo) == []


def test_remerge_all_merges_leftovers(repo: Path):
    sha = _run_in_worktree(repo, "left", "c.md", "z\n")
    results = mergeback.remerge_all(repo_root=repo)
    assert results == {"agent/left": "merged"}
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)
    assert mergeback.unmerged_agent_branches(repo_root=repo) == []


def test_merge_back_holds_lock(repo: Path):
    _run_in_worktree(repo, "run1", "note.md", "x\n")
    lock = threading.Lock()
    lock.acquire()
    done: list[str] = []

    def attempt():
        res = mergeback.merge_back(repo_root=repo, slug="run1", lock=lock)
        done.append(res.status)

    th = threading.Thread(target=attempt)
    th.start()
    th.join(timeout=0.3)
    assert done == []  # blockiert, solange der Lock gehalten wird
    lock.release()
    th.join(timeout=2)
    assert done == ["merged"]
