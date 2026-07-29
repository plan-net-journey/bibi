"""``bibi-ctrl mergeback`` — Recovery-CLI (PLAN-6 Slice C)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bibi.ctrl import main
from bibi.daemon import worktree as wt


def _run_in_worktree(repo: Path, slug: str) -> None:
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug=slug)
    (path / f"{slug}.md").write_text("x\n")
    wt.commit(worktree=path, message=f"{slug}: run", slug=slug)


def test_mergeback_clean_repo(team_repo: Path, capsys):
    # team_repo hat keinen Initial-Commit → erst einen anlegen.
    subprocess.run(["git", "add", "-A"], cwd=team_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=team_repo, check=True)
    assert main(["mergeback"]) == 0
    assert "keine unmergten" in capsys.readouterr().out


def test_mergeback_lists_then_applies(team_repo: Path, capsys):
    subprocess.run(["git", "add", "-A"], cwd=team_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=team_repo, check=True)
    _run_in_worktree(team_repo, "left")
    # ohne --apply: nur Liste, Exit 1
    assert main(["mergeback"]) == 1
    assert "agent/left" in capsys.readouterr().out
    # mit --apply: zusammengeführt, Exit 0
    assert main(["mergeback", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "agent/left: merged" in out
    head = subprocess.run(["git", "rev-parse", "trunk"], cwd=team_repo,
                          capture_output=True, text=True, check=True).stdout
    sha = subprocess.run(["git", "rev-parse", "agent/left"], cwd=team_repo,
                         capture_output=True, text=True, check=True).stdout
    subprocess.run(["git", "merge-base", "--is-ancestor", sha.strip(), head.strip()],
                   cwd=team_repo, check=True)
