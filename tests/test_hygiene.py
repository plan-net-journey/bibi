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


def test_conventions_finding():
    assert hygiene.conventions_finding(True) == []
    f = hygiene.conventions_finding(False)
    assert f and f[0].kind == "conventions-missing"
    assert f[0].path == "vault/CONVENTIONS.md"


# ── PLAN-13 Stufe 13.3: job-doctor-Checks ─────────────────────────────────────


def test_orphan_worktrees_flags_unknown_slug_only():
    findings = hygiene.check_orphan_worktrees(
        worktree_slugs=["Runner", "gone-job"],
        known_slugs={"Runner", "Witz"},
    )
    assert [f.path for f in findings] == ["data/worktrees/gone-job"]
    assert findings[0].kind == "orphan-worktree"


def test_orphan_worktrees_deactivated_but_known_slug_is_not_orphan():
    # Ein pausierter/deaktivierter Slug hat noch eine jobs-Zeile — kein Befund.
    findings = hygiene.check_orphan_worktrees(
        worktree_slugs=["paused-job"],
        known_slugs={"paused-job"},
    )
    assert findings == []


def test_orphan_worktrees_empty_input_no_findings():
    assert hygiene.check_orphan_worktrees([], set()) == []


def test_invalid_schedules_reports_parser_errors():
    from bibi.schedule.parser import ParseResult
    errors = [
        ParseResult(schedule_ref="vault/case/x/Broken.md", error="Frontmatter braucht `job:`"),
    ]
    findings = hygiene.check_invalid_schedules(errors)
    assert len(findings) == 1
    assert findings[0].kind == "invalid-schedule"
    assert findings[0].path == "vault/case/x/Broken.md"
    assert findings[0].detail == "Frontmatter braucht `job:`"


def test_invalid_schedules_empty_input_no_findings():
    assert hygiene.check_invalid_schedules([]) == []


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
    # Repo-Invariant: jedes bibi-team-Repo führt vault/CONVENTIONS.md (sonst Befund).
    (root / "vault").mkdir()
    (root / "vault" / "CONVENTIONS.md").write_text("# conventions\n", encoding="utf-8")
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
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"x" * (600 * 1024))  # 600 KiB, kein LFS (kein .gitattributes)
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "big")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1 and "large-unmanaged" in out and "vault/huge.bin" in out


def test_doctor_flags_missing_conventions(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    _git(gitrepo, "rm", "-q", "vault/CONVENTIONS.md")
    _git(gitrepo, "commit", "-q", "-m", "drop conventions")
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1 and "conventions-missing" in out and "vault/CONVENTIONS.md" in out


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


def test_doctor_flags_orphan_worktree(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    (gitrepo / "data" / "worktrees" / "gone-job").mkdir(parents=True)
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1
    assert "orphan-worktree" in out and "data/worktrees/gone-job" in out


def test_doctor_ignores_worktree_with_known_slug(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    (gitrepo / "data" / "worktrees" / "Runner").mkdir(parents=True)
    d = gitrepo / "vault" / "case" / "20260717.Test-aaaaaaaa"
    d.mkdir(parents=True)
    (d / "Runner.md").write_text('---\nschedule: "*/5 * * * *"\njob: "echo hi"\n---\n',
                                 encoding="utf-8")
    from bibi.daemon import job_db as jdb
    conn = jdb.connect(gitrepo / "data" / "jobs.sqlite")
    jdb.rescan(conn, vault_root=gitrepo / "vault" / "case")
    conn.close()
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "orphan-worktree" not in out


def test_doctor_flags_invalid_schedule(gitrepo: Path, capsys, monkeypatch):
    monkeypatch.setattr(hygiene, "git_lfs_installed", lambda: True)
    d = gitrepo / "vault" / "case" / "20260717.Test-aaaaaaaa"
    d.mkdir(parents=True)
    (d / "Broken.md").write_text("---\nschedule: \"*/5 * * * *\"\n---\n", encoding="utf-8")  # kein job:
    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 1
    assert "invalid-schedule" in out and "Broken.md" in out
