"""bibi-ctrl run: CLI-Logik (Poll-bis-terminal, Exit-Codes) — PLAN-28 Refactor C.

Schnell: _run_wrapper() wird gemockt (kein echter Subprozess) — die echte
End-to-End-Kette deckt tests/test_run_local.py ab (@pytest.mark.slow)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo
from bibi.ctrl.run_cmd import run, run_kill, run_list, run_reset
from bibi.daemon import job_db
from bibi.daemon.worker import run_pinned


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


class _Args:
    def __init__(self, slug=None, command=None, kind="job"):
        self.slug = slug
        self.command = command
        self.kind = kind


def _fake_run_wrapper(tmp_path, *, exit_code=0):
    # In echt meldet der detacht laufende Wrapper-Subprozess seinen
    # Terminal-Status später, selbständig, außerhalb dieses Aufrufs. Für einen
    # schnellen Test (kein echter Subprozess, _wait_until_terminal() würde
    # sonst ewig auf ein Ereignis warten, das nie eintritt) simuliert dieser
    # Fake genau das synchron — attempts=0 (CLI-Default) heißt beim echten
    # Wrapper (_finish(): "attempt_cur < attempts_max" ist mit 0 < 0 falsch)
    # sofort "error" bei einem Fehlschlag, nie "failed" mit Retry-Backoff.
    def fake(*, job_id, **kwargs):
        conn = job_db.connect(tmp_path / "data" / "jobs.sqlite")
        try:
            if exit_code == 0:
                job_db.report_status(conn, job_id, status="complete", exit_code=exit_code)
            else:
                # running→error ist KEIN gültiger Übergang (lifecycle.py) — der
                # echte Wrapper geht bei Erschöpfung erst über "failed" (s.
                # bibi/wrapper/__init__.py::_finish()); dieser Fake muss dieselben
                # zwei Schritte machen, sonst bleibt die Zeile für immer "running"
                # hängen und _wait_until_terminal() pollt endlos.
                job_db.report_status(conn, job_id, status="failed", exit_code=exit_code,
                                     reason="nonzero_exit", attempt=0, next_fire_at=None)
                job_db.report_status(conn, job_id, status="error", exit_code=exit_code,
                                     reason="nonzero_exit")
        finally:
            conn.close()
        out_path = tmp_path / "data" / "job" / job_id / "output.jsonl"
        return out_path, 999
    return fake


def test_run_cmd_needs_arg_returns_2(gitrepo, capsys):
    assert run(_Args()) == 2
    assert "nötig" in capsys.readouterr().err


def test_run_cmd_unknown_slug_returns_1(gitrepo, capsys):
    assert run(_Args(slug="nope")) == 1
    assert "nope" in capsys.readouterr().err


def test_run_cmd_prints_output_and_returns_0_on_complete(gitrepo, monkeypatch, capsys):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo, exit_code=0))
    rc = run(_Args(command="echo hi"))
    assert rc == 0
    err = capsys.readouterr().err
    assert "[complete]" in err and "exit=0" in err


def test_run_cmd_returns_1_on_nonzero_exit(gitrepo, monkeypatch, capsys):
    # attempts=0 (Default): kein Retry, sofortiger "error" — deckungsgleich mit
    # dem historischen /run-Verhalten (die CLI hat keinen laufenden gepinnten
    # Worker, der einen Retry je bedienen könnte).
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo, exit_code=5))
    rc = run(_Args(command="exit 5"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "[error]" in err

    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        row = conn.execute("SELECT attempts FROM jobs").fetchone()
    finally:
        conn.close()
    # 1 statt 0 seit #168: `attempts` zaehlt Gesamtversuche, und der
    # CLI-Default heisst unveraendert "ein Lauf, kein Retry" — nur mit
    # der Zahl, die das auch bedeutet. Das Verhalten darueber (Exit 1,
    # "[error]", kein zweiter Lauf) ist unveraendert und steht oben.
    assert row["attempts"] == 1


# ── PLAN-32 Stufe 32.3 — pinned-Job-Verwaltung (list/kill/reset) ────────────


class _IdArgs:
    def __init__(self, id):  # noqa: A002
        self.id = id


def _pinned_row(gitrepo, monkeypatch, *, host="mac"):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo, exit_code=0))
    # exit_code=0 dispatcht den Fake sofort in "complete" -- fuer kill/reset-
    # Tests unerheblich (die pruefen nur Zeilen-/Daten-Verwaltung, nicht den
    # Lifecycle-Uebergang selbst), reale gepinnte Jobs bleiben "running" bis
    # ihr echter Wrapper sie beendet.
    return run_pinned(cmd="echo hi", repo_root=gitrepo, host=host)


def test_run_list_shows_only_pinned_rows_for_this_host(gitrepo, monkeypatch, capsys):
    import bibi.ctrl.run_cmd as RC
    monkeypatch.setattr(RC.socket, "gethostname", lambda: "mac")
    res = _pinned_row(gitrepo, monkeypatch)
    rc = run_list(_IdArgs(None))
    assert rc == 0
    assert res["id"] in capsys.readouterr().out


def test_run_list_empty_message_when_none(gitrepo, capsys):
    rc = run_list(_IdArgs(None))
    assert rc == 0
    assert "keine" in capsys.readouterr().out


def test_run_kill_unknown_id_returns_1(gitrepo, capsys):
    assert run_kill(_IdArgs("nope")) == 1
    assert "nope" in capsys.readouterr().err


def test_run_kill_refuses_non_pinned_row(gitrepo, capsys):
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES ('x1','s1','r1','job','echo hi','running')")
    conn.close()
    assert run_kill(_IdArgs("x1")) == 1
    assert "nicht lokal gepinnt" in capsys.readouterr().err


def test_run_reset_wipes_and_deletes_pinned_row(gitrepo, monkeypatch):
    res = _pinned_row(gitrepo, monkeypatch)
    rc = run_reset(_IdArgs(res["id"]))
    assert rc == 0
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT 1 FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    assert row is None


def test_run_reset_unknown_id_returns_1(gitrepo, capsys):
    assert run_reset(_IdArgs("nope")) == 1
    assert "nope" in capsys.readouterr().err
