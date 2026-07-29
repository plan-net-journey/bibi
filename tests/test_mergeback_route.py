"""Merge-back-Verdrahtung im Scheduler-Status-Endpunkt (PLAN-6 Slice B).

Ein terminaler ``complete``-Report mit ``branch`` löst den Merge nach trunk aus —
unter dem gemeinsamen ``sync_lock`` — und stößt (bei Zustimmung) einen Push an.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
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


def test_merge_conflict_tracked_in_quarantine_not_global_flag(repo_with_origin):
    # PLAN-30 Ebene 3: das globale sync_conflict-Flag ist NICHT mehr der Weg,
    # wie ein Job-Branch-Konflikt (Requirement 2) sichtbar wird — das war der
    # ursprüngliche Bug (Flag verschwindet beim nächsten erfolgreichen Sync,
    # egal ob dieser Branch noch hängt). Stattdessen: merge_quarantine.py.
    from bibi.daemon import merge_quarantine
    root, _origin = repo_with_origin
    app = create_app(roles.resolve({"scheduler"}), sync_lock=threading.Lock())
    with TestClient(app) as client:
        # Job ändert dieselbe Datei wie ein paralleler trunk-Commit → Konflikt.
        jid, sha = _reserve_and_run(client, root, "c", "pyproject.toml", "JOB\n")
        (root / "pyproject.toml").write_text("TRUNK\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "trunk diverge")
        # Review-Runde 7, Fund 1: dieser Weg (HTTP-Route) reicht kein now= an
        # mergeback.merge_back() durch, anders als die direkten Aufrufe in
        # test_mergeback.py/test_git_ops.py/test_sync_cmd.py — hier stattdessen
        # die Konflikt-Datei per os.utime() vordatieren, damit Ebene 4s Idle-
        # Guard (IDLE_WINDOW_S=120) sie nicht als "kürzlich bearbeitet" wertet
        # und den Versuch mit "live_edit" statt einem echten Konflikt abbricht.
        stale = time.time() - 300
        os.utime(root / "pyproject.toml", (stale, stale))
        trunk_after = _git(root, "rev-parse", "trunk")
        r = client.post(f"/-/scheduler/status/{jid}",
                        json={"status": "complete", "exit_code": 0,
                              "commit_sha": sha, "branch": "agent/c"})
        assert r.status_code == 200
        assert client.get(f"/-/job/{jid}").json()["status"] == "complete"
        assert _git(root, "rev-parse", "trunk") == trunk_after  # trunk unverändert
        assert state.get_sync_conflict() is False
        entry = merge_quarantine.get(root, "agent/c")
        assert entry is not None and entry.failures == 1


def test_local_scheduler_report_has_no_merge_side_effect(repo_with_origin):
    """``LocalScheduler.report()`` selbst löst nie einen Merge-back aus (mehr) —
    der frühere ``on_complete``-Hook wurde entfernt (PLAN-30 Ebene 1 v2, Fund
    2026-07-15: der reale, detachte Wrapper-Subprozess ruft ``.report()`` nie auf,
    der Hook war seit dem Wrapper-Refactor 2026-06-28 unerreichbarer Code — ihn
    hier weiter zu testen hätte nur eine Attrappe bestätigt). Der echte Merge-back-
    Trigger läuft jetzt per HTTP über die Status-Route, end-to-end über den
    echten Wrapper-Subprozess geprüft in ``test_wrapper_merge_trigger.py``."""
    from bibi.daemon import job_db
    from bibi.daemon.scheduler_client import LocalScheduler
    root, _origin = repo_with_origin
    sched = LocalScheduler()
    _seed(root, "loc/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    conn = job_db.connect()
    try:
        job_db.rescan(conn)
        res = job_db.reserve_next(conn, worker="w", host="h")
        jid = res["id"]
    finally:
        conn.close()
    head_before = _git(root, "rev-parse", "trunk")
    out = sched.report(jid, status="complete", exit_code=0, branch="agent/loc")
    assert out == "ok"
    assert _git(root, "rev-parse", "trunk") == head_before  # kein Merge, kein Hook mehr


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
