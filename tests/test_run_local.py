"""/run: lokale On-Demand-Ausführung (DESIGN §1.4; PLAN-3 §3.3b)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo
from bibi.ctrl import main
from bibi.daemon import job_db
from bibi.daemon.worker import run_local
from bibi.wrapper import output


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


def _conn(root: Path):
    return job_db.connect(root / "data" / "jobs.sqlite")


def test_run_local_by_cmd_writes_local_journal(gitrepo: Path):
    res = run_local(cmd="echo hallo && echo fertig", repo_root=gitrepo,
                    work_dir=gitrepo / "data" / "worktrees",
                    db_path=gitrepo / "data" / "jobs.sqlite")
    assert res["status"] == "complete" and res["exit_code"] == 0
    out = gitrepo / "data" / "job" / res["id"] / "output.jsonl"
    assert output.lines(out, "out") == ["hallo", "fertig"]

    conn = _conn(gitrepo)
    try:
        jrows = job_db.list_journal(conn)
        assert len(jrows) == 1
        assert jrows[0]["domain"] == "local"          # lokale Domäne (§1.4)
        assert jrows[0]["output_ref"] == res["output_ref"]
        # KEIN jobs-Eintrag — die zentrale Queue sieht /run nie
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        conn.close()


def test_run_local_does_not_enter_scheduler_queue(gitrepo: Path):
    run_local(cmd="echo x", repo_root=gitrepo,
              work_dir=gitrepo / "data" / "worktrees",
              db_path=gitrepo / "data" / "jobs.sqlite")
    conn = _conn(gitrepo)
    try:
        # reserve_next findet nichts — /run legt nichts in die Queue
        assert job_db.reserve_next(conn) is None
    finally:
        conn.close()


def test_run_local_by_slug(gitrepo: Path):
    (gitrepo / "vault" / "case" / "hello" / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (gitrepo / "vault" / "case" / "hello" / "README.md").write_text(
        '---\nschedule: now\njob: "echo viaslug"\n---\n', encoding="utf-8")
    res = run_local(slug="hello", repo_root=gitrepo,
                    work_dir=gitrepo / "data" / "worktrees",
                    db_path=gitrepo / "data" / "jobs.sqlite")
    assert res["slug"] == "hello" and res["status"] == "complete"
    out = gitrepo / "data" / "job" / res["id"] / "output.jsonl"
    assert output.lines(out, "out") == ["viaslug"]


def test_run_local_unknown_slug_raises(gitrepo: Path):
    with pytest.raises(LookupError):
        run_local(slug="nope", repo_root=gitrepo,
                  work_dir=gitrepo / "data" / "worktrees",
                  db_path=gitrepo / "data" / "jobs.sqlite")


def test_run_local_ephemeral_worktree_removed(gitrepo: Path):
    run_local(cmd="echo x", slug="eph", repo_root=gitrepo,
              work_dir=gitrepo / "data" / "worktrees",
              db_path=gitrepo / "data" / "jobs.sqlite")
    assert not (gitrepo / "data" / "worktrees" / "eph").exists()  # aufgeräumt (§3.3b)


def test_run_local_failed_cmd(gitrepo: Path):
    res = run_local(cmd="exit 5", repo_root=gitrepo,
                    work_dir=gitrepo / "data" / "worktrees",
                    db_path=gitrepo / "data" / "jobs.sqlite")
    assert res["status"] == "failed" and res["exit_code"] == 5


# ── CLI: bibi-ctrl run (in-process, kein Daemon nötig) ───────────────────────


def test_cli_run_cmd(gitrepo: Path, capsys):
    rc = main(["run", "--cmd", "echo cli-hallo"])
    assert rc == 0
    assert "cli-hallo" in capsys.readouterr().out
    conn = _conn(gitrepo)
    try:
        assert job_db.list_journal(conn, domain="local")
    finally:
        conn.close()


def test_cli_run_unknown_slug(gitrepo: Path, capsys):
    assert main(["run", "nope"]) == 1
    assert "nope" in capsys.readouterr().err


def test_cli_run_needs_arg(gitrepo: Path):
    assert main(["run"]) == 2


# ── POST /-/run (worker-gated) ───────────────────────────────────────────────


def test_run_endpoint(gitrepo: Path):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    from bibi.daemon.worker import Worker

    w = Worker(autopoll=False, repo_root=gitrepo,
               work_dir=gitrepo / "data" / "worktrees",
               db_path=gitrepo / "data" / "jobs.sqlite")
    app = create_app(roles.resolve({"worker"}), worker=w)
    with TestClient(app) as c:
        r = c.post("/-/run", json={"cmd": "echo via-endpoint"})
        assert r.status_code == 200
        assert r.json()["status"] == "complete"
        # rein lokal: nichts in der Scheduler-Queue
        conn = _conn(gitrepo)
        try:
            assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
            assert any(j["domain"] == "local" for j in job_db.list_journal(conn))
        finally:
            conn.close()
        # unbekannter slug → 404
        assert c.post("/-/run", json={"slug": "nope"}).status_code == 404
        # weder slug noch cmd → 400
        assert c.post("/-/run", json={}).status_code == 400
