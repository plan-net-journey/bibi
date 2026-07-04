"""Worker end-to-end: reserve → execute → report → journal (PLAN-3 §3.3)."""

from __future__ import annotations

import secrets
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


_TERMINAL = frozenset({"complete", "error", "killed", "zombie", "inactive"})


def _wait_terminal(root: Path, jid: str, timeout: float = 10.0) -> dict:
    """Warten bis Job-Status terminal ist (Wrapper meldet async via SQLite)."""
    db = root / "data" / "jobs.sqlite"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = job_db.connect(db)
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        finally:
            conn.close()
        if row and row["status"] in _TERMINAL:
            return dict(row)
        time.sleep(0.05)
    conn = job_db.connect(db)
    try:
        return dict(conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone() or {})
    finally:
        conn.close()


@pytest.mark.slow
def test_tick_runs_job_to_complete(gitrepo: Path):
    jid = _seed(gitrepo, "run1/README.md",
                '---\nschedule: now\njob: "echo hallo && echo fertig"\n---\n')
    assert _worker(gitrepo).tick_once() is True
    row = _wait_terminal(gitrepo, jid)

    assert row["status"] == "complete"
    assert row["exit_code"] == 0
    assert row["worker"] == "t"
    run_id = job_db.run_id_for("run1", jid, 0)
    assert row["output_ref"] == f"data/job/{run_id}/output.jsonl"

    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        journal = job_db.list_journal(conn)
        assert len(journal) == 1
        assert journal[0]["slug"] == "run1"
        assert journal[0]["run_id"] == run_id
        assert journal[0]["host"] is not None
        assert journal[0]["output_ref"] == f"data/job/{run_id}/output.jsonl"
    finally:
        conn.close()

    from bibi.wrapper import output
    out = gitrepo / "data" / "job" / run_id / "output.jsonl"
    assert output.lines(out, "out") == ["hallo", "fertig"]


@pytest.mark.slow
def test_branch_created_on_run(gitrepo: Path):
    jid = _seed(gitrepo, "run1/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    _worker(gitrepo).tick_once()
    _wait_terminal(gitrepo, jid)
    assert "agent/run1" in _git(gitrepo, "branch", "--list", "agent/run1")


@pytest.mark.slow
def test_failed_job_reports_failed(gitrepo: Path):
    jid = _seed(gitrepo, "boom/README.md", '---\nschedule: now\njob: "exit 7"\n---\n')
    _worker(gitrepo).tick_once()
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] == "failed" and row["exit_code"] == 7


@pytest.mark.slow
def test_tick_empty_returns_false(gitrepo: Path):
    job_db.connect(gitrepo / "data" / "jobs.sqlite").close()
    assert _worker(gitrepo).tick_once() is False


@pytest.mark.slow
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


@pytest.mark.slow
def test_wall_time_kills_job(gitrepo: Path):
    jid = _seed(gitrepo, "slow/README.md",
                '---\nschedule: now\njob: "sleep 30"\nwall_time: 1\n---\n')
    _worker(gitrepo).tick_once()
    row = _wait_terminal(gitrepo, jid, timeout=15.0)
    assert row["status"] == "killed" and row["reason"] == "by_wall_time"
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        assert job_db.list_journal(conn)[0]["reason"] == "by_wall_time"
    finally:
        conn.close()


@pytest.mark.slow
def test_silence_zombies_job(gitrepo: Path):
    jid = _seed(gitrepo, "hang/README.md",
                '---\nschedule: now\njob: "sleep 30"\nsilence_timeout: 1\n---\n')
    _worker(gitrepo).tick_once()
    row = _wait_terminal(gitrepo, jid, timeout=15.0)
    assert row["status"] == "zombie" and row["reason"] == "silence"


@pytest.mark.slow
def test_retry_then_error(gitrepo: Path, monkeypatch):
    monkeypatch.setenv("BIBI_RETRY_BASE", "0")  # kein Warten zwischen Versuchen
    jid = _seed(gitrepo, "boom/README.md",
                '---\nschedule: now\njob: "exit 1"\nattempts: 2\n---\n')
    w = _worker(gitrepo)
    dbp = gitrepo / "data" / "jobs.sqlite"

    assert w.tick_once() is True        # Versuch 1 → failed (attempt 1)
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] == "failed" and row["attempt"] == 1

    # failed → wieder reservierbar (next_fire_at=now mit base=0)
    conn = job_db.connect(dbp)
    conn.execute("UPDATE jobs SET next_fire_at=? WHERE id=?", (time.time(), jid))
    conn.commit()
    conn.close()

    assert w.tick_once() is True        # Versuch 2 (failed→running) → error (attempt 2)
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] in ("failed", "error") and row["attempt"] == 2
    conn = job_db.connect(dbp)
    job_db.sweep(conn)                  # Sweep: erschöpftes failed → error
    row2 = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row2["status"] == "error"
    assert any(j["status"] == "error" for j in job_db.list_journal(conn))
    conn.close()


@pytest.mark.slow
def test_retry_exponential_3x_to_error(gitrepo: Path, monkeypatch):
    """PLAN-10 §10.1: 3 Fehlversuche mit exponentialem Backoff → ERROR; Slot nach FAILED frei."""
    monkeypatch.setenv("BIBI_RETRY_BASE", "0")  # sofort retribar
    jid = _seed(gitrepo, "boom3/README.md",
                '---\nschedule: now\njob: "exit 2"\nattempts: 3\nbackoff: exponential\n---\n')
    w = _worker(gitrepo)
    dbp = gitrepo / "data" / "jobs.sqlite"

    for attempt_n in (1, 2, 3):
        assert w.tick_once() is True
        row = _wait_terminal(gitrepo, jid)
        if attempt_n < 3:
            assert row["status"] == "failed" and row["attempt"] == attempt_n
            # Slot nach FAILED sofort frei (Wrapper exitiert, _procs wird geleert)
            import time as _time
            deadline = _time.time() + 5.0
            while w._procs.get(jid) and w._procs[jid].poll() is None:
                _time.sleep(0.1)
                if _time.time() > deadline:
                    break
            assert jid not in w._procs or w._procs[jid].poll() is not None
            conn = job_db.connect(dbp)
            conn.execute("UPDATE jobs SET next_fire_at=? WHERE id=?", (_time.time(), jid))
            conn.commit()
            conn.close()
        else:
            # 3. Versuch erschöpft → failed oder direkt error
            assert row["attempt"] == 3

    conn = job_db.connect(dbp)
    job_db.sweep(conn)
    row_final = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert row_final["status"] == "error"


@pytest.mark.slow
def test_execute_reservation_skips_if_already_terminal(gitrepo: Path):
    # Wird der Job vor Abschluss killed, überschreibt der Wrapper nicht.
    jid = _seed(gitrepo, "r/README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    res = job_db.reserve_next(conn)  # → running
    conn.execute("UPDATE jobs SET status='killed', reason='by_user' WHERE id=?", (jid,))
    conn.commit()
    conn.close()
    from bibi.daemon.scheduler_client import LocalScheduler
    execute_reservation(
        res, repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        client=LocalScheduler(gitrepo / "data" / "jobs.sqlite"), worker_name="t",
    )
    # Wrapper läuft async — kurz warten; killed→complete ist invalid → bleibt killed.
    time.sleep(2.0)
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"] == "killed"
    conn.close()


@pytest.mark.slow
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
    row = conn.execute(
        "SELECT status, exit_code, attempt, output_ref FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert row["status"] == "failed"   # raus aus `running` (Fund B behoben)
    assert row["exit_code"] == -1 and row["attempt"] == 1
    # User-Feedback 2026-07-03: der Fehler soll im Job-Output landen, nicht nur
    # im Daemon-Log — output_ref darf hier nicht mehr None sein.
    assert row["output_ref"] is not None
    from bibi.wrapper import output as _output
    phases = _output.lines(gitrepo / row["output_ref"], "phase")
    assert any("worktree prepare kaputt" in p for p in phases)


def test_run_wrapper_logs_worktree_and_spawn_phases(gitrepo: Path, monkeypatch):
    # User-Feedback 2026-07-03: Startup-Phasen (Worktree, Wrapper-Spawn) landen
    # als erste Zeilen im selben output.jsonl, das der Wrapper weiterschreibt.
    import sys
    import types

    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    real_popen = W.subprocess.Popen

    def fake_popen(*a, **kw):
        # Nur den Wrapper-Spawn faken — worktree.prepare() braucht echtes git.
        if a and isinstance(a[0], list) and a[0][:1] == [sys.executable]:
            return types.SimpleNamespace(pid=999)
        return real_popen(*a, **kw)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)

    _, _, out_path, outcome, pid = W._run_wrapper(
        job_id="j1", slug="phasetest", kind="job", payload="echo hi",
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="phasetest:0", detach=True,
    )
    assert outcome == "detached" and pid == 999
    phases = _output.lines(out_path, "phase")
    assert any("vorbereitet" in p for p in phases)
    assert any("bereit" in p for p in phases)
    assert any("wird gestartet" in p for p in phases)


@pytest.mark.slow
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
    _wait_terminal(gitrepo, jid)   # warten bis Wrapper complete meldet
    _make_due()
    assert w.tick_once() is True   # Lauf fire=1
    _wait_terminal(gitrepo, jid)   # warten bis zweiter Lauf abgeschlossen

    run_id0 = job_db.run_id_for("tick", jid, 0)
    run_id1 = job_db.run_id_for("tick", jid, 1)
    out0 = gitrepo / "data" / "job" / run_id0 / "output.jsonl"
    out1 = gitrepo / "data" / "job" / run_id1 / "output.jsonl"
    assert output.lines(out0, "out") == ["hallo"]
    assert output.lines(out1, "out") == ["hallo"]

    conn = job_db.connect(dbp)
    try:
        journal = job_db.list_journal(conn)
        run_ids = {j["run_id"] for j in journal}
        assert run_ids == {run_id0, run_id1}
        refs = {j["output_ref"] for j in journal}
        assert refs == {f"data/job/{run_id0}/output.jsonl", f"data/job/{run_id1}/output.jsonl"}
    finally:
        conn.close()


@pytest.mark.slow
def test_output_path_resolves_current_run(gitrepo: Path):
    # Die Live-Route fragt worker.output_path(job_id) — das muss den AKTUELLEN
    # Lauf (slug:fire) treffen, nicht die stabile job_id.
    jid = _seed(gitrepo, "r/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    w = _worker(gitrepo)
    run_id = job_db.run_id_for("r", jid, 0)
    assert w.output_path(jid) == gitrepo / "data" / "job" / run_id / "output.jsonl"


@pytest.mark.slow
def test_report_level_by_status():
    import logging

    from bibi.daemon.worker import _report_level
    assert _report_level("complete") == logging.INFO
    assert _report_level("failed") == logging.WARNING
    assert _report_level("killed") == logging.WARNING
    assert _report_level("zombie") == logging.WARNING
    assert _report_level("error") == logging.ERROR


# ── PLAN-11.4: app_register-Signal → Traefik-Route (File-Provider, §7.5/§7.7) ─
# Kein echter Prozess/Docker nötig ⇒ nicht @pytest.mark.slow.


def _seed_app_job(status: str, app_port: int | None) -> str:
    jid = secrets.token_hex(4)
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, app_port, "
            "enqueued_at) VALUES (?,?,?,?,?,?,?,?)",
            (jid, jid, f"{jid}.md", "job", "true", status, app_port, time.time()),
        )
    finally:
        conn.close()
    return jid


def test_register_app_route_writes_traefik_file(team_repo: Path):
    from bibi.daemon.worker import _deregister_app_route, _register_app_route
    _register_app_route("abc123", 9100)
    path = team_repo / "data" / "traefik" / "dynamic" / "abc123.yml"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "9100" in content and "abc123" in content and "127.0.0.1" in content
    _deregister_app_route("abc123")
    assert not path.exists()


def test_deregister_app_route_missing_file_is_noop(team_repo: Path):
    from bibi.daemon.worker import _deregister_app_route
    _deregister_app_route("never-registered")  # darf nicht werfen


def test_poll_app_routes_registers_new_port(team_repo: Path, monkeypatch):
    import bibi.daemon.worker as W
    calls = []
    monkeypatch.setattr(W, "_register_app_route", lambda jid, port: calls.append((jid, port)))
    jid = _seed_app_job("running", 9100)
    W.Worker(autopoll=False, worker_name="w1")._poll_app_routes()
    assert calls == [(jid, 9100)]


def test_poll_app_routes_skips_unchanged_port(team_repo: Path, monkeypatch):
    import bibi.daemon.worker as W
    calls = []
    monkeypatch.setattr(W, "_register_app_route", lambda jid, port: calls.append((jid, port)))
    jid = _seed_app_job("running", 9100)
    w = W.Worker(autopoll=False, worker_name="w1")
    w._poll_app_routes()
    w._poll_app_routes()
    assert calls == [(jid, 9100)]  # zweiter Tick: Port unverändert ⇒ kein erneuter Call


def test_poll_app_routes_skips_pending_job(team_repo: Path, monkeypatch):
    # app_port steht schon ab Schedule-Erfassung in der DB (Frontmatter-Feld) —
    # ein `pending`-Job hat aber noch keinen laufenden Prozess. Nicht terminal
    # ist nicht dasselbe wie "hat einen Prozess" (auch failed/deferred betroffen).
    import bibi.daemon.worker as W
    calls = []
    monkeypatch.setattr(W, "_register_app_route", lambda jid, port: calls.append((jid, port)))
    _seed_app_job("pending", 9100)
    _seed_app_job("failed", 9101)
    _seed_app_job("deferred", 9102)
    W.Worker(autopoll=False, worker_name="w1")._poll_app_routes()
    assert calls == []


def test_poll_app_routes_registers_awaiting(team_repo: Path, monkeypatch):
    import bibi.daemon.worker as W
    calls = []
    monkeypatch.setattr(W, "_register_app_route", lambda jid, port: calls.append((jid, port)))
    jid = _seed_app_job("awaiting", 9100)
    W.Worker(autopoll=False, worker_name="w1")._poll_app_routes()
    assert calls == [(jid, 9100)]


def test_poll_app_routes_deregisters_on_terminal(team_repo: Path, monkeypatch):
    import bibi.daemon.worker as W
    registered, deregistered = [], []
    monkeypatch.setattr(W, "_register_app_route", lambda jid, port: registered.append((jid, port)))
    monkeypatch.setattr(W, "_deregister_app_route", lambda jid: deregistered.append(jid))
    jid = _seed_app_job("running", 9100)
    w = W.Worker(autopoll=False, worker_name="w1")
    w._poll_app_routes()
    conn = job_db.connect()
    conn.execute("UPDATE jobs SET status='complete' WHERE id=?", (jid,))
    conn.commit()
    conn.close()
    w._poll_app_routes()
    assert registered == [(jid, 9100)]
    assert deregistered == [jid]
