"""Gemeinsame Test-Fixtures.

``team_repo`` baut ein echtes, temporäres git-Repo mit der Team-Repo-Struktur
(DESIGN §3.2) und parkt das cwd hinein — so arbeiten ``repo``/``state``/
``case_store`` gegen einen isolierten Baum statt gegen das echte Repo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _init_repo(root: Path, branch: str = "trunk") -> None:
    (root / ".claude").mkdir(parents=True)
    (root / "vault" / "case").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    _git(root, "init", "-q", "-b", branch)
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")


@pytest.fixture
def team_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporäres Team-Repo; cwd ist hineingeparkt. Gibt den Repo-Root zurück."""
    root = tmp_path / "team"
    _init_repo(root)

    # cwd parken; keine geleakte BIBI_CASE_DIR-Übersteuerung aus der Umgebung.
    monkeypatch.chdir(root)
    monkeypatch.delenv("BIBI_CASE_DIR", raising=False)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


@pytest.fixture
def repo_with_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Team-Repo mit echtem bare-Origin (branch `trunk`), Initial-Commit gepusht.

    Erlaubt push/pull/integrate/Konflikt ohne Netz. Gibt (root, origin) zurück;
    cwd ist in `root` geparkt.
    """
    root = tmp_path / "team"
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _init_repo(root)
    _git(origin, "init", "-q", "--bare", "-b", "trunk")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "push", "-q", "-u", "origin", "trunk")

    monkeypatch.chdir(root)
    monkeypatch.delenv("BIBI_CASE_DIR", raising=False)
    repo._root_of.cache_clear()
    yield root, origin
    repo._root_of.cache_clear()
