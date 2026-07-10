"""run_local() reicht app_port/app_prefix/exec_mode ans Wrapper durch (Bug
gefunden 2026-07-10 bei der HITL-Test-App-Migration). Vorher gingen diese drei
Schedule-MD-Felder spurlos verloren, sobald ein Job über /run statt über den
Scheduler lief — anders als execute_reservation() (Scheduler-Dispatch-Pfad),
der reservation.get("app_port"/"app_prefix"/"exec_mode") längst korrekt an
_run_wrapper() weiterreicht. Betraf jeden App-Typ-Job über /run: kein App-Port
im Wrapper-Env, kein Docker-Port-Mapping, kein Traefik-Routing, exec_mode:-
Override im MD ohne Wirkung.

Schnell: _run_wrapper() wird gemockt (kein echter Subprozess) — die echte
End-to-End-Kette deckt tests/test_run_local.py ab (@pytest.mark.slow)."""

from __future__ import annotations

from pathlib import Path

from bibi.daemon.worker import run_local


def _seed_app_schedule(root: Path, slug: str, *, app_port: int = 9100,
                       app_prefix: str | None = None,
                       exec_mode: str | None = None) -> None:
    d = root / "vault" / "case" / slug
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", 'schedule: "never"', f'job: "python3 {slug}.py"',
             f"app_port: {app_port}"]
    if app_prefix is not None:
        lines.append(f"app_prefix: {app_prefix}")
    if exec_mode is not None:
        lines.append(f"exec_mode: {exec_mode}")
    lines += ["---", f"# {slug}"]
    (d / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_run_local_passes_app_port_and_exec_mode_to_wrapper(team_repo: Path, monkeypatch):
    _seed_app_schedule(team_repo, "myapp", app_port=9100, app_prefix="/myapp",
                       exec_mode="host")
    captured = {}

    def fake_run_wrapper(**kwargs):
        captured.update(kwargs)
        return 0, None, team_repo / "data" / "job" / "jid" / "output.jsonl", "ok", None

    monkeypatch.setattr("bibi.daemon.worker._run_wrapper", fake_run_wrapper)
    monkeypatch.setattr(
        "bibi.daemon.worker.job_db.write_local_journal", lambda *a, **k: None)

    run_local(slug="myapp", repo_root=team_repo, work_dir=team_repo / "data" / "worktrees",
              db_path=team_repo / "data" / "jobs.sqlite")

    assert captured["app_port"] == 9100
    assert captured["app_prefix"] == "/myapp"
    assert captured["exec_mode"] == "host"


def test_run_local_plain_job_passes_none_for_app_fields(team_repo: Path, monkeypatch):
    # Ein normaler (Nicht-App-)Job hat keine app_port/exec_mode-Frontmatter —
    # die Felder müssen dann sauber None bleiben, nicht z. B. 0/"" (was
    # _run_wrapper()/exec_backend.build_exec() als "gesetzt" missverstehen
    # könnte).
    d = team_repo / "vault" / "case" / "plainjob"
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text(
        '---\nschedule: "never"\njob: "echo hi"\n---\n# plain\n', encoding="utf-8")
    captured = {}

    def fake_run_wrapper(**kwargs):
        captured.update(kwargs)
        return 0, None, team_repo / "data" / "job" / "jid" / "output.jsonl", "ok", None

    monkeypatch.setattr("bibi.daemon.worker._run_wrapper", fake_run_wrapper)
    monkeypatch.setattr(
        "bibi.daemon.worker.job_db.write_local_journal", lambda *a, **k: None)

    run_local(slug="plainjob", repo_root=team_repo, work_dir=team_repo / "data" / "worktrees",
              db_path=team_repo / "data" / "jobs.sqlite")

    assert captured["app_port"] is None
    assert captured["app_prefix"] is None
    assert captured["exec_mode"] is None


def test_run_local_by_cmd_has_no_app_fields(team_repo: Path, monkeypatch):
    # Ad-hoc-Kommando (kein Slug/MD) — es gibt kein Frontmatter, aus dem
    # app_port/exec_mode kommen könnten; muss weiterhin funktionieren.
    captured = {}

    def fake_run_wrapper(**kwargs):
        captured.update(kwargs)
        return 0, None, team_repo / "data" / "job" / "jid" / "output.jsonl", "ok", None

    monkeypatch.setattr("bibi.daemon.worker._run_wrapper", fake_run_wrapper)
    monkeypatch.setattr(
        "bibi.daemon.worker.job_db.write_local_journal", lambda *a, **k: None)

    run_local(cmd="echo hi", repo_root=team_repo, work_dir=team_repo / "data" / "worktrees",
              db_path=team_repo / "data" / "jobs.sqlite")

    assert captured["app_port"] is None
    assert captured["app_prefix"] is None
    assert captured["exec_mode"] is None
