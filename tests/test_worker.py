"""Worker end-to-end: reserve → execute → report → journal (PLAN-3 §3.3)."""

from __future__ import annotations

import subprocess
import time
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
        # Output je **run_id** (slug:fire), nicht je stabilem job_id — `now` läuft
        # mit fire=0 (kein cron-Re-Arm). Kein Akkumulieren über Läufe (Bug 27s #4).
        assert row["output_ref"] == "data/job/run1:0/output.jsonl"
        journal = job_db.list_journal(conn)
        assert len(journal) == 1
        assert journal[0]["slug"] == "run1"
        assert journal[0]["run_id"] == "run1:0"
        assert journal[0]["host"] is not None         # host first-class (§1.4)
        assert journal[0]["output_ref"] == "data/job/run1:0/output.jsonl"  # referenziert (§1.4)
    finally:
        conn.close()

    # output.jsonl trägt die zwei Zeilen — am run_id-Pfad
    from bibi.wrapper import output
    out = gitrepo / "data" / "job" / "run1:0" / "output.jsonl"
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


def test_tick_skips_during_maintenance(gitrepo: Path, monkeypatch):
    # Wartungsmodus muss respektiert werden: kein Dispatch, Job bleibt pending.
    _seed(gitrepo, "run1/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    monkeypatch.setattr("bibi.state.get_maintenance", lambda: True)
    w = _worker(gitrepo)
    assert w.tick_once() is False
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        assert conn.execute(
            "SELECT status FROM jobs WHERE slug='run1'").fetchone()["status"] == "pending"
    finally:
        conn.close()
    # Wartung aus → wieder Dispatch
    monkeypatch.setattr("bibi.state.get_maintenance", lambda: False)
    assert w.tick_once() is True


def test_wall_time_kills_job(gitrepo: Path):
    jid = _seed(gitrepo, "slow/README.md",
                '---\nschedule: now\njob: "sleep 30"\nwall_time: 1\n---\n')
    _worker(gitrepo).tick_once()
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
        assert row["status"] == "killed" and row["reason"] == "by_wall_time"
        assert job_db.list_journal(conn)[0]["reason"] == "by_wall_time"
    finally:
        conn.close()


def test_silence_zombies_job(gitrepo: Path):
    # kein Output + silence_timeout abgelaufen → zombie(silence)
    jid = _seed(gitrepo, "hang/README.md",
                '---\nschedule: now\njob: "sleep 30"\nsilence_timeout: 1\n---\n')
    _worker(gitrepo).tick_once()
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        row = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
        assert row["status"] == "zombie" and row["reason"] == "silence"
    finally:
        conn.close()


def test_retry_then_error(gitrepo: Path, monkeypatch):
    monkeypatch.setenv("BIBI_RETRY_BASE", "0")  # kein Warten zwischen Versuchen
    jid = _seed(gitrepo, "boom/README.md",
                '---\nschedule: now\njob: "exit 1"\nattempts: 2\n---\n')
    w = _worker(gitrepo)
    dbp = gitrepo / "data" / "jobs.sqlite"

    assert w.tick_once() is True   # Versuch 1 → failed (attempt 1)
    conn = job_db.connect(dbp)
    assert conn.execute("SELECT status, attempt FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "failed"
    conn.close()

    assert w.tick_once() is True   # Versuch 2 (failed→running) → failed (attempt 2)
    conn = job_db.connect(dbp)
    row = conn.execute("SELECT status, attempt FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "failed" and row["attempt"] == 2
    conn.close()

    assert w.tick_once() is False  # erschöpft → nicht mehr reservierbar
    conn = job_db.connect(dbp)
    job_db.sweep(conn)             # Sweep: erschöpftes failed → error
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "error"
    assert any(j["status"] == "error" for j in job_db.list_journal(conn))
    conn.close()


def test_execute_reservation_skips_if_already_terminal(gitrepo: Path):
    # Wird der Job vor Abschluss killed, überschreibt der Worker nicht.
    jid = _seed(gitrepo, "r/README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    res = job_db.reserve_next(conn)  # → running
    conn.execute("UPDATE jobs SET status='killed', reason='by_user' WHERE id=?", (jid,))
    conn.close()
    from bibi.daemon.scheduler_client import LocalScheduler
    out = execute_reservation(
        res, repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        client=LocalScheduler(gitrepo / "data" / "jobs.sqlite"), worker_name="t",
    )
    assert out["status"] is None  # killed→complete ist invalid ⇒ nichts überschrieben
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "killed"
    conn.close()


def test_execute_reservation_setup_failure_does_not_hang_running(gitrepo: Path, monkeypatch):
    # Härtung Fund B (PLAN-5 §5.3): schlägt Setup/Run VOR der Statusmeldung fehl,
    # darf der Job nicht in `running` hängen — er wird als `failed` gemeldet.
    import bibi.daemon.worker as W
    jid = _seed(gitrepo, "boom/README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    res = job_db.reserve_next(conn)  # → running
    conn.close()
    assert res["id"] == jid

    def boom(**_kwargs):  # Worktree-/Wrapper-Setup scheitert
        raise RuntimeError("worktree prepare kaputt")
    monkeypatch.setattr(W, "_run_wrapper", boom)

    from bibi.daemon.scheduler_client import LocalScheduler
    out = execute_reservation(  # darf NICHT werfen
        res, repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        client=LocalScheduler(gitrepo / "data" / "jobs.sqlite"), worker_name="t",
    )
    assert out["outcome"] == "setup_error" and out["status"] == "failed"
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT status, exit_code, attempt FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert row["status"] == "failed"   # raus aus `running` (Fund B behoben)
    assert row["exit_code"] == -1 and row["attempt"] == 1


def test_per_run_output_isolation(gitrepo: Path):
    # Wiederkehrender Job läuft zweimal (fire 0, dann 1) → **getrennte** Output-
    # Dateien je run_id, kein Anhängen an eine geteilte job_id-Datei (Bug 27s #4).
    from bibi.wrapper import output
    jid = _seed(gitrepo, "tick/README.md",
                '---\nschedule: "* * * * *"\njob: "echo hallo"\n---\n')
    dbp = gitrepo / "data" / "jobs.sqlite"
    w = _worker(gitrepo)

    def _make_due() -> None:  # cron-next liegt in der Zukunft → fällig schalten
        conn = job_db.connect(dbp)
        conn.execute("UPDATE jobs SET next_fire_at=? WHERE id=?", (time.time(), jid))
        conn.commit()
        conn.close()

    _make_due()
    assert w.tick_once() is True   # Lauf fire=0 → complete → cron re-armt zu fire=1
    _make_due()
    assert w.tick_once() is True   # Lauf fire=1

    out0 = gitrepo / "data" / "job" / "tick:0" / "output.jsonl"
    out1 = gitrepo / "data" / "job" / "tick:1" / "output.jsonl"
    # Jede Datei trägt genau die EINE Zeile ihres Laufs — kein Akkumulieren.
    assert output.lines(out0, "out") == ["hallo"]
    assert output.lines(out1, "out") == ["hallo"]

    conn = job_db.connect(dbp)
    try:
        journal = job_db.list_journal(conn)
        run_ids = {j["run_id"] for j in journal}
        assert run_ids == {"tick:0", "tick:1"}
        refs = {j["output_ref"] for j in journal}
        assert refs == {"data/job/tick:0/output.jsonl", "data/job/tick:1/output.jsonl"}
    finally:
        conn.close()


def test_output_path_resolves_current_run(gitrepo: Path):
    # Die Live-Route fragt worker.output_path(job_id) — das muss den AKTUELLEN
    # Lauf (slug:fire) treffen, nicht die stabile job_id.
    jid = _seed(gitrepo, "r/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    w = _worker(gitrepo)
    assert w.output_path(jid) == gitrepo / "data" / "job" / "r:0" / "output.jsonl"


def test_report_level_by_status():
    import logging

    from bibi.daemon.worker import _report_level
    assert _report_level("complete") == logging.INFO
    assert _report_level("failed") == logging.WARNING
    assert _report_level("killed") == logging.WARNING
    assert _report_level("zombie") == logging.WARNING
    assert _report_level("error") == logging.ERROR
