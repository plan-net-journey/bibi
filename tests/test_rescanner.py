"""Periodischer Auto-Rescan (PLAN-5 §5.4-Nachschlag)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo
from bibi.daemon import job_db, rescanner


def test_resolve_interval(monkeypatch):
    monkeypatch.delenv("BIBI_RESCAN_INTERVAL", raising=False)
    assert rescanner.resolve_interval() == 180.0
    monkeypatch.setenv("BIBI_RESCAN_INTERVAL", "30")
    assert rescanner.resolve_interval() == 30.0
    monkeypatch.setenv("BIBI_RESCAN_INTERVAL", "bad")
    assert rescanner.resolve_interval() == 180.0
    monkeypatch.setenv("BIBI_RESCAN_INTERVAL", "0")
    assert rescanner.resolve_interval() == 180.0  # ≤0 → Default


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def gitrepo(tmp_path: Path, monkeypatch):
    root = tmp_path / "r"
    (root / "vault" / "case").mkdir(parents=True)
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


def test_rescanner_tick_picks_up_new_schedule(gitrepo: Path):
    # Frisch abgelegte MD wird vom Tick erfasst (genau die „witz"-Lücke).
    md = gitrepo / "vault" / "case" / "joke" / "README.md"
    md.parent.mkdir(parents=True)
    md.write_text('---\nschedule: "*/3 * * * *"\nclaude: erzähl einen Witz\n---\n',
                  encoding="utf-8")
    res = rescanner.Rescanner(autorun=False).tick_once()
    assert res["inserted"] == 1
    conn = job_db.connect()
    try:
        row = conn.execute("SELECT slug, kind FROM jobs WHERE slug='joke'").fetchone()
    finally:
        conn.close()
    assert row["kind"] == "claude"
