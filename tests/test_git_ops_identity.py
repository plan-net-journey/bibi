"""stage_and_commit()-Identitätsüberschreibung (PLAN-21 Befund 8).

Bewusst getrennt von tests/test_git_ops.py (dort pytestmark = slow wegen der
repo_with_origin-Push/Integrate-Szenarien) — dieser Test braucht keinen
Origin, nur team_repo (schnell, kein Netz)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bibi import git_ops


def _head_author(cwd: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"], cwd=cwd, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def test_commit_without_identity_keeps_ambient_config(team_repo):
    (team_repo / "f.txt").write_text("x\n")
    assert git_ops.stage_and_commit(None, "msg") is True
    author = _head_author(team_repo)
    assert "bibi" not in author  # ambiente Test-Identität aus conftest, kein Bot


def test_commit_with_identity_overrides_author(team_repo):
    (team_repo / "f.txt").write_text("y\n")
    assert git_ops.stage_and_commit(None, "msg", identity=("bibi/sync", "bibi@local")) is True
    assert _head_author(team_repo) == "bibi/sync <bibi@local>"
