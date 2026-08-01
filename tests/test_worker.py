"""Worker end-to-end: reserve → execute → report → journal (PLAN-3 §3.3)."""

from __future__ import annotations

import secrets
import sqlite3
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
    # Committen, nicht nur aufs Dateisystem schreiben: der Worker-Worktree ist
    # ein `git worktree add … trunk` — ein Job-cwd unterhalb der MD (§ Job-cwd-
    # Fix 2026-07-05) existiert darin nur, wenn die Datei schon auf trunk sitzt.
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"seed {rel}")
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


#: Wartefrist für einen terminalen Job-Status. Grosszügig, weil die Maschine,
#: die die --slow-Suite fährt, zugleich die Produktions-Daemons trägt: der
#: CI-Lauf konkurriert mit ihnen um CPU und Platte (m.rau/bibi#41, #87). Eine
#: knappe Frist macht aus diesen Tests einen Lastmesser — dieselbe Abwägung
#: wie bei #69, nur an der anderen Stelle der Suite.
_TERMINAL_TIMEOUT_S = 30.0


def _wait_terminal(root: Path, jid: str, timeout: float = _TERMINAL_TIMEOUT_S) -> dict:
    """Warten bis Job-Status terminal ist (Wrapper meldet async via SQLite).

    **Schlägt beim Ablauf der Frist fehl, statt still die letzte Zeile
    zurückzugeben** (m.rau/bibi#87). Vorher tat er genau das — der aufrufende
    Test scheiterte danach an seiner eigenen Assertion, mit einer Meldung wie
    ``assert 'running' == 'killed'``, die wie ein Logikfehler aussieht, obwohl
    es ein Lastproblem war.

    Genau daran hing der wandernde Fehler: in drei aufeinanderfolgenden
    CI-Läufen war jedes Mal ein anderer Test dieser Datei rot, und welcher, war
    Zufall — der langsamste des jeweiligen Laufs. Für den CI-Melder hiess das
    eine bei jedem Lauf andere Fehlermenge und damit einen neuen Fingerabdruck:
    ein eigenes Ticket pro Lauf (#83).
    """
    db = root / "data" / "jobs.sqlite"
    started = time.monotonic()
    deadline = started + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        conn = job_db.connect(db)
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        finally:
            conn.close()
        if row:
            last = dict(row)
            if row["status"] in _TERMINAL:
                return last
        time.sleep(0.05)
    raise AssertionError(
        f"Job {jid} wurde nach {time.monotonic() - started:.1f}s nicht terminal — "
        f"Status: {last.get('status', '(keine Zeile)')}, reason: {last.get('reason')}. "
        "Das ist eine abgelaufene Wartefrist, kein falsches Ergebnis: unter Last "
        "dauert der Lauf länger als erwartet.")


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
def test_job_runs_with_cwd_at_schedule_md_directory(gitrepo: Path):
    """User-Feedback 2026-07-05: Job-cwd = Verzeichnis der Schedule-MD, nicht
    Worktree-Root — auch wenn die MD tiefer im Case verschachtelt liegt."""
    jid = _seed(gitrepo, "run1/sub/README.md",
                '---\nschedule: now\nslug: nested\njob: "pwd > here.txt"\n---\n')
    assert _worker(gitrepo).tick_once() is True
    _wait_terminal(gitrepo, jid)
    job_dir = gitrepo / "data" / "worktrees" / "nested" / "vault" / "case" / "run1" / "sub"
    probe = job_dir / "here.txt"
    assert probe.exists()
    assert probe.read_text().strip() == str(job_dir)


@pytest.mark.slow
def test_branch_created_on_run(gitrepo: Path):
    jid = _seed(gitrepo, "run1/README.md", '---\nschedule: now\njob: "echo x"\n---\n')
    _worker(gitrepo).tick_once()
    _wait_terminal(gitrepo, jid)
    assert "agent/run1" in _git(gitrepo, "branch", "--list", "agent/run1")


@pytest.mark.slow
def test_failing_job_without_explicit_attempts_reaches_error_immediately(gitrepo: Path):
    # War bis zum attempts-Default-Fix (Batch 6, 2026-07-21, `7a64545`) noch
    # "failed" (Default damals 1, ein ungenutzter Retry blieb liegen, da
    # dieser Test nie ein zweites tick_once() macht — _wait_terminal() nahm
    # nur den 10s-Timeout-Fallback). Ohne explizites attempts: ist der Default
    # seither 0 (kein automatischer Retry) — dieselbe synchrone Erschöpfung
    # wie in test_attempts_zero_reaches_error_without_hanging, nur über den
    # Parser-Default statt expliziter Frontmatter-Angabe. Test war bis jetzt
    # nicht nachgezogen worden (@pytest.mark.slow, lief nicht in der schnellen
    # Suite, die der attempts-Fix damals validiert hat).
    jid = _seed(gitrepo, "boom/README.md", '---\nschedule: now\njob: "exit 7"\n---\n')
    _worker(gitrepo).tick_once()
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] == "error" and row["exit_code"] == 7


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
    row = _wait_terminal(gitrepo, jid)
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
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] == "zombie" and row["reason"] == "silence"


@pytest.mark.slow
def test_retry_then_error(gitrepo: Path, monkeypatch):
    # Bugfix (User-Fund: "ein Failed wechselt sofort nach Ende auf ERROR, falls
    # keine Versuche mehr uebrig sind" - beobachtet aber ein Failed, das nie
    # wieder dispatcht und stattdessen nur durch einen externen Sweep zu error
    # gezwungen wurde): attempts=2 gewaehrt zwei Retries (Versuch 1+2), attempt
    # erreicht nach Versuch 2 den Wert 2 (== attempts) - das ist der zuletzt
    # GEWAEHRTE, noch nicht VERBRAUCHTE Versuch, kein Erschoepfen. Erst der
    # DRITTE Versuch (attempt_cur=2 >= attempts_max=2) loest in _finish() die
    # synchrone Erschoepfung aus (failed->error im selben Wrapper-Aufruf,
    # kein Sweep mehr noetig).
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

    assert w.tick_once() is True        # Versuch 2 (failed→running) → failed (attempt 2, letzter gewaehrter Versuch)
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] == "failed" and row["attempt"] == 2

    # failed (attempt==attempts) → weiterhin reservierbar, kein Sweep noetig
    conn = job_db.connect(dbp)
    conn.execute("UPDATE jobs SET next_fire_at=? WHERE id=?", (time.time(), jid))
    conn.commit()
    conn.close()

    assert w.tick_once() is True        # Versuch 3 (failed→running) → erschoepft, SYNCHRON error
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] == "error"
    conn = job_db.connect(dbp)
    assert any(j["status"] == "error" for j in job_db.list_journal(conn))
    conn.close()


@pytest.mark.slow
def test_attempts_zero_reaches_error_without_hanging(gitrepo: Path, monkeypatch):
    """User-Fund 2026-07-14 (gmail-transfer via /run): execute_reservation()
    las reservation.get("attempts") or 1 — bei absichtlich auf 0 gesetzten
    attempts (run_pinned()s Default für /run: "kein Retry") macht Pythons
    or aus der 0 fälschlich eine 1. Der Wrapper nahm dann bei Fehlschlag den
    Retry-Zweig (failed + next_fire_at) statt sofort zu erschöpfen
    (failed→error) — ohne laufenden Scheduler-Loop (CLI/`/-/run`) wird dieser
    Retry nie bedient: der Job blieb für immer "failed" hängen (nicht
    TERMINAL), landete nie im Journal."""
    monkeypatch.setenv("BIBI_RETRY_BASE", "0")
    jid = _seed(gitrepo, "boom0/README.md",
                '---\nschedule: now\njob: "exit 1"\nattempts: 0\n---\n')
    assert _worker(gitrepo).tick_once() is True
    row = _wait_terminal(gitrepo, jid)
    assert row["status"] == "error"
    assert row["attempt"] == 0
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        assert any(j["status"] == "error" for j in job_db.list_journal(conn))
    finally:
        conn.close()


@pytest.mark.slow
def test_retry_exponential_3x_to_error(gitrepo: Path, monkeypatch):
    """PLAN-10 §10.1: 3 Fehlversuche mit exponentialem Backoff → ERROR; Slot nach FAILED frei.

    Bugfix (User-Fund, s. test_retry_then_error oben): attempts=3 gewaehrt drei
    Retries -> vier Dispatches insgesamt (Versuch 1-3 -> failed mit attempt
    1/2/3, erst der VIERTE Versuch erschoepft synchron zu error, kein Sweep
    mehr noetig)."""
    monkeypatch.setenv("BIBI_RETRY_BASE", "0")  # sofort retribar
    jid = _seed(gitrepo, "boom3/README.md",
                '---\nschedule: now\njob: "exit 2"\nattempts: 3\nbackoff: exponential\n---\n')
    w = _worker(gitrepo)
    dbp = gitrepo / "data" / "jobs.sqlite"

    for attempt_n in (1, 2, 3, 4):
        assert w.tick_once() is True
        row = _wait_terminal(gitrepo, jid)
        if attempt_n < 4:
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
            # 4. Versuch: erschoepft (attempt_cur=3 >= attempts_max=3) -> synchron error
            assert row["status"] == "error"

    conn = job_db.connect(dbp)
    assert any(j["status"] == "error" for j in job_db.list_journal(conn))
    conn.close()


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


def test_execute_reservation_retries_pid_report_after_lock_error(gitrepo: Path, monkeypatch):
    # PLAN-31 Baustein B: ein kurzer Lock beim PID-Report direkt nach dem
    # Wrapper-Start darf den Job nicht als Setup-Fehler markieren.
    import bibi.daemon.worker as W
    jid = _seed(gitrepo, "lockpid/README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    res = job_db.reserve_next(conn)
    conn.close()

    out_path = gitrepo / "data" / "job" / "dummy" / "output.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(W, "_run_wrapper",
                        lambda **_kw: (0, None, out_path, "detached", 999999))

    real_connect = job_db.connect
    calls = {"n": 0}

    def flaky_connect(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path)
    monkeypatch.setattr(W.job_db, "connect", flaky_connect)

    from bibi.daemon.scheduler_client import LocalScheduler
    out = execute_reservation(
        res, repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        client=LocalScheduler(gitrepo / "data" / "jobs.sqlite"), worker_name="t",
    )
    assert out["outcome"] == "detached"  # kein setup_error trotz erstem Lock
    assert calls["n"] == 2  # ein Fehlschlag, ein erfolgreicher Retry
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    assert conn.execute("SELECT pid FROM jobs WHERE id=?", (jid,)).fetchone()["pid"] == 999999
    conn.close()


# ── PLAN-31 Baustein C — _report_terminal() überlebt/loggt einen Lock ───────


def test_report_terminal_survives_single_lock_error(tmp_path: Path, monkeypatch, caplog):
    from bibi import wrapper
    db_path = tmp_path / "jobs.sqlite"
    real_connect = job_db.connect
    calls = {"n": 0}

    def flaky_connect(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path)
    monkeypatch.setattr(job_db, "connect", flaky_connect)

    env = {"BIBI_JOB_ID": "abc123", "BIBI_SCHEDULER_DB_PATH": str(db_path)}
    with caplog.at_level("WARNING", logger="bibi.wrapper"):
        wrapper._report_terminal(env, status="complete", exit_code=0)
    assert calls["n"] == 2  # ein Fehlschlag, ein erfolgreicher Retry
    assert "report_status_failed" not in caplog.text


def test_report_terminal_logs_warning_when_lock_never_clears(tmp_path: Path, monkeypatch, caplog):
    # Vorher: "except Exception: pass" — der Completion-Report verschwand
    # spurlos (Live-Vorfall `Runner`, 2026-07-17). Jetzt: mindestens eine
    # Log-Zeile, statt gar nichts.
    from bibi import wrapper
    db_path = tmp_path / "jobs.sqlite"

    def always_locked(path):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(job_db, "connect", always_locked)

    env = {"BIBI_JOB_ID": "abc123", "BIBI_SCHEDULER_DB_PATH": str(db_path)}
    with caplog.at_level("WARNING", logger="bibi.wrapper"):
        wrapper._report_terminal(env, status="complete", exit_code=0)  # darf NICHT werfen
    assert "report_status_failed" in caplog.text
    assert "abc123" in caplog.text


def test_execute_reservation_passes_schedule_image_override(gitrepo: Path, monkeypatch):
    # PLAN-24 Befund 1: image: aus dem Schedule-MD landet in der DB
    # (job_db._spec_columns), reservation_view() gab es aber nie an den Worker
    # weiter — komplett totes Feld, exakt wie oneshot vor PLAN-23.
    import bibi.daemon.worker as W
    jid = _seed(gitrepo, "customimg/README.md",
                '---\nschedule: now\njob: "echo hi"\nimage: "registry.local/custom:7"\n---\n')
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    res = job_db.reserve_next(conn)
    conn.close()
    assert res["id"] == jid
    assert res["image"] == "registry.local/custom:7"

    captured = {}

    def fake_run_wrapper(**kwargs):
        captured.update(kwargs)
        return 0, None, gitrepo / "data" / "job" / "jid" / "output.jsonl", "detached", 999
    monkeypatch.setattr(W, "_run_wrapper", fake_run_wrapper)

    from bibi.daemon.scheduler_client import LocalScheduler
    execute_reservation(
        res, repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        client=LocalScheduler(gitrepo / "data" / "jobs.sqlite"), worker_name="t",
    )
    assert captured["image"] == "registry.local/custom:7"


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


def test_run_wrapper_in_place_skips_worktree_and_never_sets_ephemeral(
    gitrepo: Path, monkeypatch):
    # User-Fund 2026-07-14 (bibi-ctrl test): in_place=True läuft direkt gegen
    # repo_root — kein `git worktree add`, kein agent/<slug>-Branch. Und:
    # selbst wenn ephemeral=True (fälschlich) mitgegeben wird, darf
    # BIBI_EPHEMERAL nie gesetzt werden — sonst würde der Wrapper später
    # worktree.remove() auf repo_root selbst aufrufen (rm-rf-Risiko, s.
    # worker.py::run_pinned()s Docstring und worktree.py::remove()s Guard).
    import sys
    import types

    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    real_popen = W.subprocess.Popen
    captured_env: dict = {}

    def fake_popen(*a, **kw):
        if a and isinstance(a[0], list) and a[0][:1] == [sys.executable]:
            captured_env.update(kw.get("env") or {})
            return types.SimpleNamespace(pid=999)
        return real_popen(*a, **kw)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)

    _, _, out_path, outcome, pid = W._run_wrapper(
        job_id="j1", slug="inplacejob", kind="job", payload="echo hi",
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="inplacejob:0", detach=True,
        in_place=True, ephemeral=True,  # ephemeral=True bewusst falsch mitgegeben
    )
    assert outcome == "detached" and pid == 999
    assert captured_env["BIBI_WORKTREE"] == str(gitrepo)
    assert captured_env["BIBI_IN_PLACE"] == "1"
    assert captured_env.get("BIBI_EPHEMERAL") != "1"
    assert captured_env["BIBI_REPO_ROOT"] == str(gitrepo)  # output_ref muss weiter funktionieren

    # Kein Worktree-Verzeichnis, kein agent/<slug>-Branch entstanden:
    assert not (gitrepo / "data" / "worktrees" / "inplacejob").exists()
    branches = _git(gitrepo, "branch", "--list", "agent/inplacejob")
    assert branches == ""

    phases = _output.lines(out_path, "phase")
    assert any("übersprungen" in p for p in phases)
    assert not any(p.startswith("worktree: wird vorbereitet") for p in phases)


def test_run_wrapper_respects_schedule_exec_mode_override_for_cleanup(
    gitrepo: Path, monkeypatch):
    # PLAN-22 Befund 3: Knoten-weite Config sagt "container" (z. B. Mac-Dogfood-
    # Setup), das Schedule-MD sagt explizit exec_mode: host — die
    # Container-Cleanup-Phase davor muss den Schedule-Override respektieren,
    # nicht die globale Config erneut lesen (_is_container() tat genau das).
    import sys
    import types

    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    monkeypatch.setattr(W.config, "read_env", lambda: {"BIBI_EXEC_MODE": "container"})
    monkeypatch.delenv("BIBI_EXEC_MODE", raising=False)

    docker_calls: list[list[str]] = []
    monkeypatch.setattr(W, "_docker", lambda args: docker_calls.append(args))

    real_popen = W.subprocess.Popen

    def fake_popen(*a, **kw):
        if a and isinstance(a[0], list) and a[0][:1] == [sys.executable]:
            return types.SimpleNamespace(pid=999)
        return real_popen(*a, **kw)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)

    _, _, out_path, outcome, pid = W._run_wrapper(
        job_id="j1", slug="hostoverride", kind="job", payload="echo hi",
        exec_mode="host",
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="hostoverride:0", detach=True,
    )
    assert outcome == "detached" and pid == 999
    phases = _output.lines(out_path, "phase")
    assert not any("alte Instanz" in p for p in phases)
    assert docker_calls == []


def test_run_wrapper_sets_job_image_env_from_schedule_override(gitrepo: Path, monkeypatch):
    # PLAN-24 Befund 1: image aus dem Schedule muss env["BIBI_JOB_IMAGE"]
    # überschreiben (nach der Knoten-Config aus _exec_config(), analog zum
    # bestehenden exec_mode-Override zwei Zeilen darüber im echten Code).
    import sys
    import types

    import bibi.daemon.worker as W

    captured_env: dict = {}
    real_popen = W.subprocess.Popen

    def fake_popen(*a, **kw):
        if a and isinstance(a[0], list) and a[0][:1] == [sys.executable]:
            captured_env.update(kw.get("env") or {})
            return types.SimpleNamespace(pid=999)
        return real_popen(*a, **kw)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)

    _, _, out_path, outcome, pid = W._run_wrapper(
        job_id="j1", slug="customimg", kind="job", payload="echo hi",
        image="registry.local/custom:7",
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="customimg:0", detach=True,
    )
    assert outcome == "detached" and pid == 999
    assert captured_env["BIBI_JOB_IMAGE"] == "registry.local/custom:7"


def test_ensure_default_image_built_skips_when_image_present(tmp_path: Path, monkeypatch):
    # PLAN-24 Befund 1: bibi-base:dev existiert bereits → kein Bau, keine
    # Feedback-Zeile (Grundfall bleibt unauffällig, analog PLAN-22 Befund 4).
    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(W.subprocess, "run", fake_run)
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")

    out_path = tmp_path / "output.jsonl"
    W._ensure_default_image_built(out_path)

    assert len(calls) == 1  # nur der Inspect-Check, kein Build
    assert calls[0][:3] == ["/usr/bin/docker", "image", "inspect"]
    assert _output.lines(out_path, "phase") == []


def test_ensure_default_image_built_builds_when_missing(tmp_path: Path, monkeypatch):
    # PLAN-24 Befund 1: fehlt bibi-base:dev, wird es synchron gebaut — mit
    # Feedback-Zeile im Output (User-Auflage bei der On-Demand-Entscheidung).
    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="No such image")
        assert argv[1] == "build"
        return subprocess.CompletedProcess(argv, 0, stdout="built", stderr="")
    monkeypatch.setattr(W.subprocess, "run", fake_run)
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")

    out_path = tmp_path / "output.jsonl"
    W._ensure_default_image_built(out_path)

    assert len(calls) == 2
    build_argv = calls[1]
    assert build_argv[:3] == ["/usr/bin/docker", "build", "-t"]
    assert build_argv[3] == W.exec_backend.DEFAULT_IMAGE
    assert "-f" in build_argv
    dockerfile = Path(build_argv[build_argv.index("-f") + 1])
    assert dockerfile.name == "Dockerfile" and dockerfile.parent.name == "bibi-base"

    phases = _output.lines(out_path, "phase")
    assert any("wird gebaut" in p for p in phases)
    assert any(p.endswith("gebaut.") for p in phases)


def test_ensure_default_image_built_reports_build_failure(tmp_path: Path, monkeypatch):
    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    def fake_run(argv, **kw):
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Dockerfile: syntax error")
    monkeypatch.setattr(W.subprocess, "run", fake_run)
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")

    out_path = tmp_path / "output.jsonl"
    W._ensure_default_image_built(out_path)

    phases = _output.lines(out_path, "phase")
    assert any("fehlgeschlagen" in p and "syntax error" in p for p in phases)


def test_run_wrapper_auto_builds_only_for_default_image(gitrepo: Path, monkeypatch):
    # PLAN-24 Befund 1: Scope-Eingrenzung aus dem Design-Dialog — Auto-Build
    # nur fürs Default-Image, ein Schedule-eigenes image: bleibt Autors-
    # Verantwortung (Auto-Build kennt kein beliebiges fremdes Dockerfile).
    import sys
    import types

    import bibi.daemon.worker as W

    build_calls: list[Path] = []
    monkeypatch.setattr(W, "_ensure_default_image_built", lambda out_path: build_calls.append(out_path))
    # PLAN-24 Befund 5: _ensure_job_image() prüft zuerst das per-Job-Image —
    # hier deterministisch "existiert nicht" simulieren, damit dieser Test
    # (Scope-Frage: Default- vs. Custom-Image) nicht von echtem Docker abhängt.
    monkeypatch.setattr(W, "_docker_image_exists", lambda bin_, image: False)
    monkeypatch.setattr(W, "_docker", lambda args: None)

    real_popen = W.subprocess.Popen

    def fake_popen(*a, **kw):
        if a and isinstance(a[0], list) and a[0][:1] == [sys.executable]:
            return types.SimpleNamespace(pid=999)
        return real_popen(*a, **kw)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)

    W._run_wrapper(
        job_id="j1", slug="customimg", kind="job", payload="echo hi",
        exec_mode="container", image="registry.local/custom:1",
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="customimg:0", detach=True,
    )
    assert build_calls == []  # Custom-Image → kein Auto-Build-Versuch

    W._run_wrapper(
        job_id="j2", slug="defaultimg", kind="job", payload="echo hi",
        exec_mode="container",
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="defaultimg:0", detach=True,
    )
    assert len(build_calls) == 1  # kein Override, kein Job-Image → Default-Check greift


def test_ensure_job_image_noop_with_explicit_override(tmp_path: Path, monkeypatch):
    # PLAN-24 Befund 5: ein expliziter Override (Schedule- oder Knoten-Config-
    # image:) bleibt Autors-Verantwortung — kein Job-Image-Check, keine
    # Persistenz, kein Auto-Build.
    import bibi.daemon.worker as W

    calls: list = []
    monkeypatch.setattr(W, "_docker_image_exists", lambda bin_, image: calls.append(image) or True)
    monkeypatch.setattr(W, "_ensure_default_image_built", lambda out_path: calls.append("build"))

    env = {"BIBI_JOB_IMAGE": "registry.local/custom:1"}
    W._ensure_job_image(tmp_path / "output.jsonl", env, "some-slug")

    assert calls == []
    assert env == {"BIBI_JOB_IMAGE": "registry.local/custom:1"}  # unverändert, kein PERSIST-Flag


def test_ensure_job_image_reuses_existing_job_tag(tmp_path: Path, monkeypatch):
    # PLAN-24 Befund 5: existiert bibi-job-<slug>:latest aus einem früheren
    # Lauf, wird es bevorzugt statt des Default-Images — kein Auto-Build nötig.
    import bibi.daemon.worker as W

    seen_tags: list[str] = []

    def fake_exists(bin_, image):
        seen_tags.append(image)
        return True
    monkeypatch.setattr(W, "_docker_image_exists", fake_exists)
    build_calls: list = []
    monkeypatch.setattr(W, "_ensure_default_image_built", lambda out_path: build_calls.append(out_path))

    env: dict = {}
    W._ensure_job_image(tmp_path / "output.jsonl", env, "MySlug!")

    assert seen_tags == [W.exec_backend.job_image_tag("MySlug!")]
    assert env["BIBI_JOB_IMAGE"] == W.exec_backend.job_image_tag("MySlug!")
    assert env["BIBI_JOB_IMAGE_PERSIST"] == "1"
    assert build_calls == []


def test_ensure_job_image_falls_back_to_default_when_missing(tmp_path: Path, monkeypatch):
    # PLAN-24 Befund 5: Erstlauf — noch kein per-Job-Image vorhanden → Default-
    # Image samt Auto-Build (Befund 1) greift weiter, BIBI_JOB_IMAGE bleibt
    # ungesetzt (build_exec()s eigener DEFAULT_IMAGE-Fallback übernimmt).
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_docker_image_exists", lambda bin_, image: False)
    build_calls: list = []
    monkeypatch.setattr(W, "_ensure_default_image_built", lambda out_path: build_calls.append(out_path))

    env: dict = {}
    out_path = tmp_path / "output.jsonl"
    W._ensure_job_image(out_path, env, "freshslug")

    assert "BIBI_JOB_IMAGE" not in env
    assert env["BIBI_JOB_IMAGE_PERSIST"] == "1"
    assert build_calls == [out_path]


def test_rebuild_job_image_logs_phase_lines_when_tag_existed(tmp_path: Path, monkeypatch):
    # User-Fund 2026-07-12: "ich habe nicht den Eindruck, dass REBUILD einen
    # Effekt hat. Es zeigt auch keinen Output/Log/Fortschritt." — mit
    # out_path muss die Live-/Output-Ansicht jetzt sichtbare Bestätigung
    # bekommen, unterschieden nach "Tag existierte" vs. "existierte nicht".
    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    monkeypatch.setattr(W, "_docker_image_exists", lambda bin_, image: True)
    monkeypatch.setattr(W.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0))
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")
    w = W.Worker(autopoll=False, worker_name="t")
    out_path = tmp_path / "output.jsonl"

    assert w.rebuild_job_image("myjob", out_path=out_path) is True
    phases = _output.lines(out_path, "phase")
    assert any("wird verworfen" in p for p in phases)
    assert any("erledigt" in p for p in phases)


def test_rebuild_job_image_logs_noop_when_tag_missing(tmp_path: Path, monkeypatch):
    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    monkeypatch.setattr(W, "_docker_image_exists", lambda bin_, image: False)
    monkeypatch.setattr(W.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1))
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")
    w = W.Worker(autopoll=False, worker_name="t")
    out_path = tmp_path / "output.jsonl"

    assert w.rebuild_job_image("myjob", out_path=out_path) is True
    phases = _output.lines(out_path, "phase")
    assert any("existiert nicht" in p for p in phases)
    assert not any("erledigt" in p for p in phases)  # kein echtes Ergebnis, nichts zu tun


def test_rebuild_job_image_removes_tag(monkeypatch):
    # PLAN-24 Befund 5, REBUILD-Aktion: eigenständig von START/RESET, verwirft
    # nur das per-Job-Image — der nächste Lauf fällt automatisch auf das
    # Default-Image zurück (derselbe Mechanismus wie beim Erstlauf).
    import bibi.daemon.worker as W

    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(W.subprocess, "run", fake_run)
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")

    w = W.Worker(autopoll=False, worker_name="t")
    assert w.rebuild_job_image("myjob") is True
    # Existenz-Check (docker image inspect) vor dem eigentlichen rmi — für
    # die Phase-Zeilen-Unterscheidung "existierte"/"existierte nicht" nötig.
    assert calls == [
        ["/usr/bin/docker", "image", "inspect", "bibi-job-myjob:latest"],
        ["/usr/bin/docker", "rmi", "-f", "bibi-job-myjob:latest"],
    ]


def test_rebuild_job_image_missing_tag_still_ok(monkeypatch):
    # docker rmi auf ein bereits fehlendes Tag liefert einen Fehler-Returncode
    # — zählt trotzdem als Erfolg (Ziel schon erreicht), kein Sonderfall nötig.
    import bibi.daemon.worker as W

    monkeypatch.setattr(W.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stderr="No such image"))
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")
    w = W.Worker(autopoll=False, worker_name="t")
    assert w.rebuild_job_image("myjob") is True


def test_rebuild_job_image_docker_unreachable_is_false(monkeypatch):
    import bibi.daemon.worker as W

    def fake_run(*a, **kw):
        raise OSError("docker not found")
    monkeypatch.setattr(W.subprocess, "run", fake_run)
    monkeypatch.setattr(W.exec_backend, "resolve_docker_bin", lambda env: "/usr/bin/docker")
    w = W.Worker(autopoll=False, worker_name="t")
    assert w.rebuild_job_image("myjob") is False


class _FakeProc:
    pid = 2_147_400_000  # existiert nicht → killpg wirft ProcessLookupError (gefangen)

    def poll(self) -> int | None:
        return 0


def test_terminate_explicit_is_container_overrides_node_default(monkeypatch):
    # Bug gefunden 2026-07-12 (User-Fund, live reproduziert): _terminate()
    # prüfte bisher nur den Knoten-Default (_is_container()) für den
    # docker-stop-Aufruf — ein explizit übergebener is_container-Wert (vom
    # Aufrufer aus dem Job-eigenen exec_mode aufgelöst) muss ihn übersteuern.
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_is_container", lambda: False)  # Knoten-Default: host
    calls: list[list[str]] = []
    monkeypatch.setattr(W, "_docker", lambda args: calls.append(args))
    W._terminate(_FakeProc(), job_id="abc", is_container=True)  # Job sagt container
    assert calls == [["stop", "bibi-abc"]]


def test_terminate_without_explicit_flag_falls_back_to_node_default(monkeypatch):
    # Rückwärtskompatibilität: kein is_container übergeben → altes Verhalten
    # (Knoten-Default), damit bestehende Aufrufer unverändert funktionieren.
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_is_container", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(W, "_docker", lambda args: calls.append(args))
    W._terminate(_FakeProc(), job_id="abc")
    assert calls == [["stop", "bibi-abc"]]


def test_kill_uses_job_exec_mode_not_node_default(gitrepo: Path, monkeypatch):
    # Live reproduziert (2026-07-12): hitl-test-app-container.md hat
    # exec_mode: container, sarasates Knoten-Config hat kein BIBI_EXEC_MODE
    # (Default host). Worker.kill() prüfte bislang nur _is_container()
    # (Knoten-weit) — der Container blieb nach KILL verwaist laufen (docker
    # ps zeigte ihn "Up", obwohl der docker-run-CLI-Prozess längst tot war).
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_is_container", lambda: False)  # Knoten-Default: host
    calls: list[list[str]] = []
    monkeypatch.setattr(W, "_docker", lambda args: calls.append(args))
    w = W.Worker(autopoll=False, worker_name="t",
                db_path=gitrepo / "data" / "jobs.sqlite")
    conn = job_db.connect(w.db_path)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
        "enqueued_at, exec_mode) VALUES (?,?,?,?,?, 'running', ?, 'container')",
        ("j1", "containerjob", "containerjob.md", "job", "echo hi", time.time()))
    conn.commit()
    conn.close()

    assert w.kill("j1") is True  # kein Proc registriert → "Wrapper weg"-Zweig
    assert calls == [["kill", "bibi-j1"]]


class _FakeAliveProc:
    """Anders als _FakeProc: poll() is None → kill() muss die
    _terminate()-Branch nehmen (Prozess gilt als noch laufend)."""
    pid = 2_147_400_001

    def poll(self) -> int | None:
        return None


def test_kill_proc_alive_uses_job_exec_mode(gitrepo: Path, monkeypatch):
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_is_container", lambda: False)  # Knoten-Default: host
    calls: list[list[str]] = []
    monkeypatch.setattr(W, "_docker", lambda args: calls.append(args))
    w = W.Worker(autopoll=False, worker_name="t",
                db_path=gitrepo / "data" / "jobs.sqlite")
    conn = job_db.connect(w.db_path)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
        "enqueued_at, exec_mode) VALUES (?,?,?,?,?, 'running', ?, 'container')",
        ("j2", "containerjob2", "containerjob2.md", "job", "echo hi", time.time()))
    conn.commit()
    conn.close()
    w._procs["j2"] = _FakeAliveProc()

    assert w.kill("j2") is True
    assert calls == [["stop", "bibi-j2"]]  # _terminate()s Zweig, nicht der Backstop


def test_kill_respects_host_override_on_container_node(gitrepo: Path, monkeypatch):
    # Kehrfall: Job hat exec_mode: host, Knoten-Default ist container — auch
    # hier gewinnt der Job-eigene Wert, kein docker-Aufruf.
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_is_container", lambda: True)  # Knoten-Default: container
    calls: list[list[str]] = []
    monkeypatch.setattr(W, "_docker", lambda args: calls.append(args))
    w = W.Worker(autopoll=False, worker_name="t",
                db_path=gitrepo / "data" / "jobs.sqlite")
    conn = job_db.connect(w.db_path)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
        "enqueued_at, exec_mode) VALUES (?,?,?,?,?, 'running', ?, 'host')",
        ("j3", "hostjob", "hostjob.md", "job", "echo hi", time.time()))
    conn.commit()
    conn.close()

    assert w.kill("j3") is False  # kein Proc, kein Container, keine DB-PID → False
    assert calls == []


def test_kill_unknown_job_falls_back_to_node_default(gitrepo: Path, monkeypatch):
    # Kein DB-Eintrag (z. B. schon gelöscht) → fällt auf den Knoten-Default
    # zurück statt zu crashen — bisheriges Verhalten bleibt erhalten.
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_is_container", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(W, "_docker", lambda args: calls.append(args))
    w = W.Worker(autopoll=False, worker_name="t",
                db_path=gitrepo / "data" / "jobs.sqlite")

    assert w.kill("gone") is True
    assert calls == [["kill", "bibi-gone"]]


def test_terminate_logs_kill_phase_line_when_out_path_given(tmp_path: Path, monkeypatch):
    # User-Fund 2026-07-12: "ich sehe beim Kill gar nichts im Output/Log/
    # Fortschritt" — Start hat Phase-Zeilen, Teardown bisher keine einzige.
    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    monkeypatch.setattr(W, "_docker", lambda args: None)
    out_path = tmp_path / "output.jsonl"
    W._terminate(_FakeProc(), job_id="abc", is_container=True, out_path=out_path)
    phases = _output.lines(out_path, "phase")
    assert any("wird beendet" in p for p in phases)


def test_terminate_without_out_path_writes_nothing(monkeypatch):
    # Rückwärtskompatibel: kein out_path → kein Schreibversuch (Aufrufer wie
    # local_run_kill() vor diesem Fix übergaben ihn schlicht nicht).
    import bibi.daemon.worker as W

    monkeypatch.setattr(W, "_docker", lambda args: None)
    monkeypatch.setattr(W.output, "append", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("output.append() sollte ohne out_path nie aufgerufen werden")))
    W._terminate(_FakeProc(), job_id="abc", is_container=False)


def test_kill_container_backstop_logs_phase_line(gitrepo: Path, monkeypatch):
    # "Wrapper weg, Container evtl. noch da"-Zweig — auch hier eine
    # sichtbare Phase-Zeile statt Stille.
    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    monkeypatch.setattr(W, "_is_container", lambda: True)
    monkeypatch.setattr(W, "_docker", lambda args: None)
    w = W.Worker(autopoll=False, worker_name="t",
                db_path=gitrepo / "data" / "jobs.sqlite")

    assert w.kill("gone") is True
    out_path = w.output_path("gone")
    phases = _output.lines(out_path, "phase")
    assert any("verwaister Container" in p for p in phases)


def test_run_wrapper_host_mode_no_cleanup_when_port_free(gitrepo: Path, monkeypatch):
    # PLAN-22 Befund 4: kein Vorgänger auf dem app_port → kein SIGTERM, keine
    # Phase-Meldung, kein Zeitverlust (Grundfall bleibt unauffällig).
    import sys
    import types

    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    monkeypatch.setattr(W, "_port_holder_pids", lambda port: [])
    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(W.os, "kill", lambda pid, sig: kills.append((pid, sig)))

    real_popen = W.subprocess.Popen

    def fake_popen(*a, **kw):
        if a and isinstance(a[0], list) and a[0][:1] == [sys.executable]:
            return types.SimpleNamespace(pid=999)
        return real_popen(*a, **kw)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)

    _, _, out_path, outcome, pid = W._run_wrapper(
        job_id="j1", slug="portfree", kind="job", payload="echo hi",
        exec_mode="host", app_port=9100,
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="portfree:0", detach=True,
    )
    assert outcome == "detached" and pid == 999
    assert kills == []
    phases = _output.lines(out_path, "phase")
    assert not any("Vorgänger-Prozess" in p for p in phases)


def test_run_wrapper_host_mode_frees_stale_app_port(gitrepo: Path, monkeypatch):
    # PLAN-22 Befund 4: ein Vorgänger-Prozess hält den festen app_port noch
    # (z. B. nach einem Daemon-Neustart überlebter Wrapper-Child) — Host-Mode
    # muss ihn vor dem nächsten Start beenden, analog zu `docker rm -f` im
    # Container-Modus (sonst OSError: Address already in use, live beobachtet).
    import sys
    import types

    import bibi.daemon.worker as W
    from bibi.wrapper import output as _output

    calls = {"n": 0}

    def fake_holder(port):
        calls["n"] += 1
        return [4242] if calls["n"] == 1 else []
    monkeypatch.setattr(W, "_port_holder_pids", fake_holder)
    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(W.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)

    real_popen = W.subprocess.Popen

    def fake_popen(*a, **kw):
        if a and isinstance(a[0], list) and a[0][:1] == [sys.executable]:
            return types.SimpleNamespace(pid=999)
        return real_popen(*a, **kw)
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)

    _, _, out_path, outcome, pid = W._run_wrapper(
        job_id="j1", slug="portstale", kind="job", payload="echo hi",
        exec_mode="host", app_port=9100,
        repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        run_id="portstale:0", detach=True,
    )
    assert outcome == "detached" and pid == 999
    assert kills == [(4242, W.signal.SIGTERM)]
    phases = _output.lines(out_path, "phase")
    assert any("Vorgänger-Prozess" in p for p in phases)


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


# ── _loop() Timing (PLAN-28) ─────────────────────────────────────────────────


def test_loop_does_not_tick_immediately_on_start(monkeypatch):
    # PLAN-28: ein Worker läuft jetzt auch rollenunabhängig (gepinnte Läufe,
    # s. create_app()) und damit in praktisch jedem Test mit. Ein Sofort-Tick
    # beim Start würde per run_in_executor() in einem eigenen Thread sofort
    # job_db.connect() gegen dieselbe frische jobs.sqlite auslösen, mit der
    # ein Test selbst synchron arbeitet — live gefunden als "database is
    # locked" beim ersten LocalPinnedLoop-Entwurf. Erst schlafen, dann
    # ticken, verhindert das für alle Tests, die (wie praktisch alle)
    # deutlich unter einem Poll-Intervall laufen.
    import asyncio

    calls = []
    monkeypatch.setattr(Worker, "tick_once", lambda self: calls.append(1) or False)
    w = Worker(worker_name="t", poll_interval=10.0)

    async def run():
        await w.start()
        await asyncio.sleep(0.05)  # deutlich kürzer als das Poll-Intervall
        await w.stop()

    asyncio.run(run())
    assert calls == []


# --- Der Warte-Helfer darf nicht stumm aufgeben (m.rau/bibi#87) --------------

def test_wait_terminal_names_the_timeout(gitrepo: Path):
    """**Der eigentliche Befund hinter dem wandernden Timing-Test.**

    `_wait_terminal()` gab beim Ablauf der Frist still die letzte Zeile zurück.
    Der Test scheiterte danach an seiner eigenen Assertion — mit einer Meldung
    wie ``assert 'running' == 'killed'``, die wie ein Logikfehler aussieht,
    obwohl es ein Lastproblem war. In drei aufeinanderfolgenden CI-Läufen traf
    es jedes Mal einen anderen Test aus dieser Datei; welchen, war Zufall.

    Ein Timeout muss sich als Timeout melden. Sonst sucht man den Fehler im
    Code, wo keiner ist — und das kostet mehr als die Wartezeit selbst.
    """
    jid = _seed(gitrepo, "nie/README.md",
                '---\nschedule: never\njob: "echo unerreichbar"\n---\n')
    with pytest.raises(AssertionError, match="terminal"):
        _wait_terminal(gitrepo, jid, timeout=0.3)
