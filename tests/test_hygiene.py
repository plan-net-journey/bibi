"""Repo-/Vault-Hygiene (PLAN-5 §5.2) — reine Checks + doctor-CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import hygiene, repo
from bibi.ctrl import doctor_cmd


# ── reine Checks ──────────────────────────────────────────────────────────────

def test_large_unmanaged_flags_big_non_lfs_only():
    files = [
        ("vault/a.png", 900_000, False),   # groß + nicht LFS → Befund
        ("vault/b.png", 900_000, True),    # groß, aber LFS → ok
        ("README.md", 2_000, False),       # klein → ok
    ]
    findings = hygiene.check_large_unmanaged(files)
    assert [f.path for f in findings] == ["vault/a.png"]
    assert findings[0].kind == "large-unmanaged"


def test_data_committed_flags_vault_data_paths():
    paths = [
        "vault/case/news/data/2026-06-27.ndjson",  # Sammeldaten → Befund
        "vault/case/news/README.md",               # ok
        "data/jobs.sqlite",                        # root-runtime (nicht vault/) → ok
    ]
    findings = hygiene.check_data_committed(paths)
    assert [f.path for f in findings] == ["vault/case/news/data/2026-06-27.ndjson"]


def test_lfs_finding():
    assert hygiene.git_lfs_finding(True) == []
    assert hygiene.git_lfs_finding(False)[0].kind == "lfs-missing"


# ── doctor-CLI gegen ein echtes Mini-Repo ─────────────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def gitrepo(tmp_path: Path, monkeypatch):
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


def _args():
    import argparse
    return argparse.Namespace()


def test_doctor_clean_repo(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    assert doctor_cmd.run(_args()) == 0
    assert "keine Hygiene-Probleme" in capsys.readouterr().out


def test_doctor_flags_large_unmanaged_blob(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    big = gitrepo / "vault" / "huge.bin"
    big.parent.mkdir(parents=True)
    big.write_bytes(b"x" * (600 * 1024))  # 600 KiB, kein LFS (kein .gitattributes)
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "big")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1 and "large-unmanaged" in out and "vault/huge.bin" in out


def test_doctor_flags_committed_data(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    d = gitrepo / "vault" / "case" / "news" / "data"
    d.mkdir(parents=True)
    (d / "feed.ndjson").write_text('{"x":1}\n', encoding="utf-8")
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "data")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1 and "data-committed" in out
