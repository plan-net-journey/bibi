"""Tests für bibi.repo (Repo-Pfade + case_dir-Auflösung)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import repo


def test_root_finds_git_toplevel(team_repo: Path):
    assert repo.root() == team_repo.resolve()


def test_vault_and_state_path(team_repo: Path):
    assert repo.vault() == team_repo.resolve() / "vault"
    assert repo.state_path() == team_repo.resolve() / ".claude" / ".state.md"


def test_case_dir_name_default(team_repo: Path):
    assert repo.case_dir_name() == "case"
    assert repo.case_dir() == team_repo.resolve() / "vault" / "case"


def test_case_dir_name_from_pyproject(team_repo: Path):
    (team_repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\n\n'
        '[tool.bibi]\ncase_dir = "project"\n',
        encoding="utf-8",
    )
    assert repo.case_dir_name() == "project"


def test_env_overrides_pyproject(team_repo: Path, monkeypatch: pytest.MonkeyPatch):
    (team_repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\n\n'
        '[tool.bibi]\ncase_dir = "project"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BIBI_CASE_DIR", "akte")
    assert repo.case_dir_name() == "akte"


def test_malformed_pyproject_falls_back_to_default(team_repo: Path):
    (team_repo / "pyproject.toml").write_text("nicht = [valides toml\n", encoding="utf-8")
    assert repo.case_dir_name() == "case"


def test_root_outside_git_repo_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    repo._root_of.cache_clear()
    with pytest.raises(SystemExit) as exc:
        repo.root()
    assert exc.value.code == 2
    repo._root_of.cache_clear()
