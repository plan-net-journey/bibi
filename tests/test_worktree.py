"""Git-Worktree-Lifecycle (DESIGN §1.3/§7.7; PLAN-3 §3.3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi.daemon import worktree as wt


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


def test_branch_name():
    assert wt.branch_name("hello") == "agent/hello"


def test_branch_name_sanitizes_unsafe_characters():
    assert wt.branch_name("Runner 1") == "agent/Runner-1"
    assert wt.branch_name("a:b~c^d?e*f[g") == "agent/a-b-c-d-e-f-g"
    assert wt.branch_name("..leading..dots..") == "agent/leading-dots"
    assert wt.branch_name("   ") == "agent/job"


def test_prepare_with_space_in_slug_creates_valid_branch(repo: Path):
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug="Runner 1")
    assert path.exists() and (path / "f.txt").exists()
    branches = _git(repo, "branch", "--list", "agent/Runner-1")
    assert "agent/Runner-1" in branches


def test_prepare_creates_worktree_and_branch(repo: Path):
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    assert path.exists() and (path / "f.txt").exists()
    branches = _git(repo, "branch", "--list", "agent/run1")
    assert "agent/run1" in branches


def test_prepare_is_fresh_each_run(repo: Path):
    work = repo / "data" / "worktrees"
    p1 = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    (p1 / "scratch.txt").write_text("dirty\n")
    p2 = wt.prepare(repo_root=repo, work_dir=work, slug="run1")  # neu von trunk
    assert not (p2 / "scratch.txt").exists()


def test_commit_noop_when_clean(repo: Path):
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    assert wt.commit(worktree=path, message="x", slug="run1") == ""


def test_commit_returns_sha_on_change(repo: Path):
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    (path / "new.txt").write_text("hi\n")
    sha = wt.commit(worktree=path, message="add", slug="run1")
    assert len(sha) == 40
    # PLAN-21 Befund 8: dynamischer bibi/<slug>-Name statt der vorherigen
    # festen "Bibi" — im Log/Blame je Job unterscheidbar, konstante Email
    # gruppiert bei Gitea trotzdem zu einer Bot-Identität.
    author = _git(path, "log", "-1", "--format=%an <%ae>")
    assert author == "bibi/run1 <bibi@local>"


def test_commit_without_slug_falls_back_to_plain_bibi(repo: Path):
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    (path / "new.txt").write_text("hi\n")
    wt.commit(worktree=path, message="add")  # kein slug übergeben
    author = _git(path, "log", "-1", "--format=%an")
    assert author == "bibi"


def test_bot_identity_with_and_without_slug():
    assert wt.bot_identity("Witz") == ("bibi/Witz", "bibi@local")
    assert wt.bot_identity() == ("bibi", "bibi@local")


def test_prepare_refuses_unmerged_branch(repo: Path):
    # F-b (PLAN-7): ungemergte Commits voraus von trunk dürfen nicht via -B verworfen
    # werden — prepare bricht ab, der Branch bleibt intakt.
    work = repo / "data" / "worktrees"
    p = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    (p / "new.txt").write_text("x\n")
    wt.commit(worktree=p, message="add", slug="run1")   # agent/run1 jetzt voraus
    assert wt.is_ahead(repo_root=repo, branch="agent/run1", trunk="trunk")
    with pytest.raises(wt.GitOpError):
        wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    # Commit nicht verloren:
    assert wt.is_ahead(repo_root=repo, branch="agent/run1", trunk="trunk")


def test_remove_idempotent(repo: Path):
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    wt.remove(repo_root=repo, worktree=path)
    assert not path.exists()
    wt.remove(repo_root=repo, worktree=path)  # zweites Mal kein Fehler


def test_remove_refuses_when_worktree_is_repo_root(repo: Path):
    # Defense-in-Depth (User-Fund 2026-07-14, bibi-ctrl test/in_place): wenn
    # worktree auf repo_root auflöst (in_place-Läufe setzen wt_path=repo_root),
    # darf remove() NIE git-worktree-remove/rmtree ausführen — das würde das
    # Live-Repo löschen, .git eingeschlossen. Zweite, von der ephemeral-Flag-
    # Weitergabe unabhängige Sicherung.
    wt.remove(repo_root=repo, worktree=repo)
    assert repo.exists() and (repo / ".git").exists() and (repo / "f.txt").exists()


def test_remove_refuses_via_non_canonical_path_to_repo_root(repo: Path):
    # Guard vergleicht resolvte Pfade, nicht rohe Strings — ein Pfad mit
    # einem ".."-Segment, der auf denselben Ort auflöst, muss genauso
    # geschützt sein wie der exakte repo_root-Pfad selbst.
    sneaky = repo / "data" / ".."  # == repo, nach .resolve()
    assert sneaky.resolve() == repo.resolve()
    wt.remove(repo_root=repo, worktree=sneaky)
    assert repo.exists() and (repo / ".git").exists()
