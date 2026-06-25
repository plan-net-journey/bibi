"""Worker end-to-end: reserve → execute → report → journal (PLAN-3 §3.3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo
from bibi.daemon import job_db
from bibi.daemon.worker import Worker, execute_reservation


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def gitrepo(tmp_path: Path, monkeypatch):
    root = tmp_path / "r"
    (root / "vault" / "case").mkdir(parents=True)
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname="t"\nversion="0"\n', encoding="utf-8")
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


def _seed(root: Path, rel: str, body: str) -> str:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    conn = job_db.connect(root / "data" / "jobs.sqlite")
    try:
        job_db.rescan(conn, vault_root=root / "vault" / "case")
        return conn.execute("SELECT id FROM jobs WHERE status='pending' LIMIT 1").fetchone()["id"]
    finally:
        conn.close()


def _worker(root: Path) -> Worker:
    return Worker(
        repo_root=root, work_dir=root / "data" / "worktrees",
        db_path=root / "data" / "jobs.sqlite", worker_name="t",
    )


def test_tick_runs_job_to_complete(gitrepo: Path):
    jid = _seed(gitrepo, "run1/README.md",
                '---\nschedule: now\njob: "echo hallo && echo fertig"\n---\n')
    assert _worker(gitrepo).tick_once() is True

    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        assert row["status"] == "complete"
        assert row["exit_code"] == 0
        assert row["worker"] == "t"
        assert row["output_ref"] == f"data/job/{jid}/output.jsonl"
        journal = job_db.list_journal(conn)
        assert len(journal) == 1
        assert journal[0]["slug"] == "run1"
        assert journal[0]["host"] is not None         # host first-class (§1.4)
        assert journal[0]["output_ref"] is not None    # referenziert (§1.4)
    finally:
        conn.close()

    # output.jsonl trägt die zwei Zeilen
    from bibi.wrapper import output
    out = gitrepo / "data" / "job" / jid / "output.jsonl"
    assert output.lines(out, "out") == ["hallo", "fertig"]


def test_branch_created_on_run(gitrepo: Path):
    _seed(gitrepo, "run1/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    _worker(gitrepo).tick_once()
    assert "agent/run1" in _git(gitrepo, "branch", "--list", "agent/run1")


def test_failed_job_reports_failed(gitrepo: Path):
    jid = _seed(gitrepo, "boom/README.md", '---\nschedule: now\njob: "exit 7"\n---\n')
    _worker(gitrepo).tick_once()
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        row = conn.execute("SELECT status, exit_code FROM jobs WHERE id=?", (jid,)).fetchone()
        assert row["status"] == "failed" and row["exit_code"] == 7
    finally:
        conn.close()


def test_tick_empty_returns_false(gitrepo: Path):
    job_db.connect(gitrepo / "data" / "jobs.sqlite").close()
    assert _worker(gitrepo).tick_once() is False


def test_execute_reservation_skips_if_already_terminal(gitrepo: Path):
    # Wird der Job vor Abschluss killed, überschreibt der Worker nicht.
    jid = _seed(gitrepo, "r/README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    res = job_db.reserve_next(conn)  # → running
    conn.execute("UPDATE jobs SET status='killed', reason='by_user' WHERE id=?", (jid,))
    conn.close()
    out = execute_reservation(
        res, repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        db_path=gitrepo / "data" / "jobs.sqlite", worker_name="t",
    )
    assert out["status"] is None  # nichts gemeldet
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "killed"
    conn.close()
