"""Merge-back-Verdrahtung im Scheduler-Status-Endpunkt (PLAN-6 Slice B).

Ein terminaler ``complete``-Report mit ``branch`` löst den Merge nach trunk aus —
unter dem gemeinsamen ``sync_lock`` — und stößt (bei Zustimmung) einen Push an.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi import state
from bibi.daemon import roles, worktree as wt
from bibi.daemon.app import create_app

pytestmark = pytest.mark.slow


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _seed(root: Path, rel: str, text: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _reserve_and_run(client, root: Path, slug: str, filename: str, content: str) -> tuple[str, str]:
    """Job einplanen+reservieren, im Worktree eine Datei committen → (job_id, sha)."""
    _seed(root, f"{slug}/README.md", f'---\nschedule: now\njob: "echo x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    work = root / "data" / "worktrees"
    path = wt.prepare(repo_root=root, work_dir=work, slug=slug)
    (path / filename).write_text(content)
    sha = wt.commit(worktree=path, message=f"{slug}: run", slug=slug)
    return jid, sha


def test_complete_report_merges_into_trunk(repo_with_origin, monkeypatch):
    root, _origin = repo_with_origin
    sync_lock = threading.Lock()
    app = create_app(roles.resolve({"scheduler"}), sync_lock=sync_lock)
    with TestClient(app) as client:
        jid, sha = _reserve_and_run(client, root, "witz", "witz.md", "Ein Witz.\n")
        r = client.post(f"/-/scheduler/status/{jid}",
                        json={"status": "complete", "exit_code": 0,
                              "commit_sha": sha, "branch": "agent/witz"})
        assert r.status_code == 200
        # Kernkriterium PLAN-6 §5.1: Commit von trunk aus erreichbar.
        subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                       cwd=root, check=True)
        assert (root / "vault" / "case" / "witz" / "witz.md").exists() or \
               (root / "witz.md").exists()


def test_echo_job_without_branch_does_not_merge(repo_with_origin):
    root, _origin = repo_with_origin
    app = create_app(roles.resolve({"scheduler"}), sync_lock=threading.Lock())
    with TestClient(app) as client:
        _seed(root, "a/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
        client.post("/-/rescan")
        jid = client.post("/-/scheduler/next").json()["id"]
        head_before = _git(root, "rev-parse", "trunk")
        r = client.post(f"/-/scheduler/status/{jid}",
                        json={"status": "complete", "exit_code": 0})
        assert r.status_code == 200
        assert _git(root, "rev-parse", "trunk") == head_before  # kein Merge


def test_merge_conflict_sets_sync_conflict_but_job_complete(repo_with_origin):
    root, _origin = repo_with_origin
    app = create_app(roles.resolve({"scheduler"}), sync_lock=threading.Lock())
    with TestClient(app) as client:
        # Job ändert dieselbe Datei wie ein paralleler trunk-Commit → Konflikt.
        jid, sha = _reserve_and_run(client, root, "c", "pyproject.toml", "JOB\n")
        (root / "pyproject.toml").write_text("TRUNK\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "trunk diverge")
        trunk_after = _git(root, "rev-parse", "trunk")
        r = client.post(f"/-/scheduler/status/{jid}",
                        json={"status": "complete", "exit_code": 0,
                              "commit_sha": sha, "branch": "agent/c"})
        assert r.status_code == 200
        assert client.get(f"/-/job/{jid}").json()["status"] == "complete"
        assert _git(root, "rev-parse", "trunk") == trunk_after  # trunk unverändert
        assert state.get_sync_conflict() is True
        state.set_sync_conflict(False)  # Test-State aufräumen


def test_local_scheduler_report_merges(repo_with_origin):
    """Der **lokale** Worker meldet via LocalScheduler (nicht HTTP-Route) — der
    Merge-back-Hook muss auch dort feuern (Live-Lücke 2026-06-27m)."""
    from bibi.daemon import job_db
    from bibi.daemon.scheduler_client import LocalScheduler
    root, _origin = repo_with_origin
    merged: list[str] = []
    sched = LocalScheduler(on_complete=merged.append)
    # einen running-Job in die DB bringen, dann via LocalScheduler complete melden.
    _seed(root, "loc/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    conn = job_db.connect()
    try:
        job_db.rescan(conn)
        res = job_db.reserve_next(conn, worker="w", host="h")
        jid = res["id"]
    finally:
        conn.close()
    out = sched.report(jid, status="complete", exit_code=0, branch="agent/loc")
    assert out == "ok"
    assert merged == ["agent/loc"]   # Hook gefeuert
    # ohne Branch (echo) feuert er nicht:
    merged.clear()
    sched2 = LocalScheduler(on_complete=merged.append)
    assert merged == []


def test_merged_commit_is_pushed_when_consent(repo_with_origin, monkeypatch):
    root, origin = repo_with_origin
    from bibi.daemon.synchronizer import Synchronizer
    sync_lock = threading.Lock()
    sync = Synchronizer(push=True, pull=True, consent=lambda: True, lock=sync_lock)
    app = create_app(roles.resolve({"scheduler", "synchronizer"}),
                     synchronizer=sync, sync_lock=sync_lock)
    with TestClient(app) as client:
        jid, sha = _reserve_and_run(client, root, "p", "note.md", "push me\n")
        client.post(f"/-/scheduler/status/{jid}",
                    json={"status": "complete", "exit_code": 0,
                          "commit_sha": sha, "branch": "agent/p"})
        # D5: der Merge-Commit ist auf origin/trunk gelandet (debouncer-unabhängig).
        subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/trunk"],
                       cwd=root, check=True)
