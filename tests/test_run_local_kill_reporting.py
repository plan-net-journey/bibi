"""PLAN-22 Befund 5: ein per local_run_kill() beendeter /run-Lauf muss sich im
Journal als "killed"/"by_user" melden statt als nicht unterscheidbares
"failed" mit zufälligem Exit-Code (Grund-Spalte bislang immer leer).

Schnell: _run_wrapper() wird gemockt (kein echter Subprozess), wie in
test_run_local_app_fields.py."""

from __future__ import annotations

from pathlib import Path

from bibi.daemon import worker
from bibi.daemon.worker import run_local


def _seed_app_schedule(root: Path, slug: str, *, app_port: int = 9100) -> None:
    d = root / "vault" / "case" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text(
        f'---\nschedule: "never"\njob: "python3 {slug}.py"\napp_port: {app_port}\n---\n'
        f"# {slug}\n",
        encoding="utf-8",
    )


def test_run_local_reports_killed_when_kill_flag_set(team_repo: Path, monkeypatch):
    _seed_app_schedule(team_repo, "hitl")
    monkeypatch.setattr(worker, "_local_runs_killed", {"hitl"})
    monkeypatch.setattr(worker, "_run_wrapper",
                        lambda **kw: (1, None, team_repo / "data" / "job" / "jid" / "output.jsonl",
                                      "ok", None))
    written = {}
    monkeypatch.setattr(worker.job_db, "write_local_journal",
                        lambda *a, **k: written.update(k))

    res = run_local(slug="hitl", repo_root=team_repo, work_dir=team_repo / "data" / "worktrees",
                    db_path=team_repo / "data" / "jobs.sqlite")

    assert written["status"] == "killed"
    assert written["reason"] == "by_user"
    assert res["status"] == "killed"
    assert "hitl" not in worker._local_runs_killed  # Flag konsumiert


def test_run_local_normal_failure_unaffected(team_repo: Path, monkeypatch):
    # Regressionsschutz: ohne Kill-Flag bleibt die bisherige Exit-Code-Ableitung
    # unverändert — ein echter Absturz zeigt weiterhin "failed", kein "Grund".
    _seed_app_schedule(team_repo, "hitl2")
    monkeypatch.setattr(worker, "_local_runs_killed", set())
    monkeypatch.setattr(worker, "_run_wrapper",
                        lambda **kw: (1, None, team_repo / "data" / "job" / "jid" / "output.jsonl",
                                      "ok", None))
    written = {}
    monkeypatch.setattr(worker.job_db, "write_local_journal",
                        lambda *a, **k: written.update(k))

    res = run_local(slug="hitl2", repo_root=team_repo, work_dir=team_repo / "data" / "worktrees",
                    db_path=team_repo / "data" / "jobs.sqlite")

    assert written["status"] == "failed"
    assert written.get("reason") is None
    assert res["status"] == "failed"


def test_local_run_kill_marks_slug_before_terminating(team_repo: Path, monkeypatch):
    # local_run_kill() muss das Flag setzen, bevor/während es terminiert —
    # unabhängig vom konkreten _terminate()-Verhalten (dort schon getestet).
    monkeypatch.setattr(worker, "_local_runs_killed", set())
    monkeypatch.setattr(worker, "_terminate", lambda proc, **kw: None)

    class _FakeProc:
        pid = 2_147_400_000

        def poll(self):
            return None

    worker._local_runs_live["hitl3"] = {"id": "abc", "output_ref": "x", "kind": "job",
                                        "payload": "p", "started_at": 0.0}
    worker._local_runs_procs["hitl3"] = _FakeProc()
    try:
        assert worker.local_run_kill("hitl3") is True
        assert "hitl3" in worker._local_runs_killed
    finally:
        worker._local_runs_live.pop("hitl3", None)
        worker._local_runs_procs.pop("hitl3", None)


def test_local_run_end_clears_killed_flag(monkeypatch):
    # Sicherheitsnetz: local_run_end() räumt auch _local_runs_killed auf, damit
    # ein nie konsumiertes Flag (z. B. Kill kurz vor einem Fehler in
    # _run_wrapper() selbst) keinen späteren, unabhängigen Lauf desselben Slugs
    # fälschlich als "killed" meldet.
    monkeypatch.setattr(worker, "_local_runs_killed", {"orphaned"})
    worker.local_run_end("orphaned")
    assert "orphaned" not in worker._local_runs_killed
