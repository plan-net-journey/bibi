"""bibi-ctrl test: CLI-Logik (Poll-bis-terminal, Exit-Codes, in-place) — mirrors
test_run_cmd.py, User-Fund 2026-07-14 (bibi-ctrl test).

Schnell: _run_wrapper() wird gemockt (kein echter Subprozess)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo
from bibi.ctrl.test_cmd import test as _run_test_cmd
from bibi.daemon import job_db


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


def _fake_run_wrapper(tmp_path, captured: dict, *, exit_code=0):
    # Wie test_run_cmd.py's Fake — zusätzlich fängt captured die kwargs ab,
    # damit wir bestätigen können, dass in_place=True tatsächlich bis
    # _run_wrapper() durchgereicht wird (der eigentliche Zweck von `test`).
    def fake(*, job_id, **kwargs):
        captured.update(kwargs)
        conn = job_db.connect(tmp_path / "data" / "jobs.sqlite")
        try:
            if exit_code == 0:
                job_db.report_status(conn, job_id, status="complete", exit_code=exit_code)
            else:
                job_db.report_status(conn, job_id, status="failed", exit_code=exit_code,
                                     reason="nonzero_exit", attempt=0, next_fire_at=None)
                job_db.report_status(conn, job_id, status="error", exit_code=exit_code,
                                     reason="nonzero_exit")
        finally:
            conn.close()
        out_path = tmp_path / "data" / "job" / job_id / "output.jsonl"
        return exit_code, None, out_path, "detached", 999
    return fake


def test_test_cmd_needs_arg_returns_2(gitrepo, capsys):
    assert _run_test_cmd(_Args()) == 2
    assert "nötig" in capsys.readouterr().err


def test_test_cmd_unknown_slug_returns_1(gitrepo, capsys):
    assert _run_test_cmd(_Args(slug="nope")) == 1
    assert "nope" in capsys.readouterr().err


def test_test_cmd_passes_in_place_true_to_run_wrapper(gitrepo, monkeypatch, capsys):
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo, captured, exit_code=0))
    rc = _run_test_cmd(_Args(command="echo hi"))
    assert rc == 0
    assert captured["in_place"] is True
    err = capsys.readouterr().err
    assert "[complete]" in err and "exit=0" in err and "in-place" in err


def test_test_cmd_returns_1_on_nonzero_exit(gitrepo, monkeypatch, capsys):
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo, captured, exit_code=5))
    rc = _run_test_cmd(_Args(command="exit 5"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "[error]" in err
