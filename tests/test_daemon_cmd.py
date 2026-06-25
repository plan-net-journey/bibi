"""``bibi-ctrl daemon`` — Rollen-Auflösung + Subkommando-Verdrahtung (PLAN-2 §2.1)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bibi.ctrl import daemon_cmd, main


@pytest.fixture
def env_iso(team_repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # ~/.config/bibi/env isolieren, damit kein echtes BIBI_ROLE durchsickert.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return team_repo


def _args(**kw):
    ns = argparse.Namespace(synchronizer=False, scheduler=False, worker=False,
                            connect=False, pull=False, push=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_resolve_synchronizer_ok(env_iso):
    r, errs = daemon_cmd.resolve_from_args(_args(synchronizer=True))
    assert r.synchronizer is True
    assert errs == []


def test_resolve_scheduler_connect_invariant(env_iso):
    _r, errs = daemon_cmd.resolve_from_args(_args(scheduler=True, connect=True))
    assert any("connect" in e.lower() for e in errs)


def test_resolve_worker_startable(env_iso):
    # Ab Stufe 3.0 startbar (Vertrag als Stubs) — keine Fehler mehr.
    _r, errs = daemon_cmd.resolve_from_args(_args(worker=True))
    assert errs == []


def test_resolve_connect_not_yet(env_iso):
    _r, errs = daemon_cmd.resolve_from_args(_args(worker=True, connect=True))
    assert any("connect" in e.lower() for e in errs)


def test_resolve_role_from_env(env_iso, monkeypatch: pytest.MonkeyPatch):
    from bibi import config
    config.write_env({"BIBI_ROLE": "synchronizer"})
    r, errs = daemon_cmd.resolve_from_args(_args())  # keine CLI-Flags
    assert r.synchronizer is True and errs == []


def test_run_returns_2_on_validation_error(env_iso):
    # main() parst + ruft run(); validierungsbedingter Frühausstieg (vor uvicorn).
    # connect ist noch nicht gebaut (Stufe 3.6) → Frühausstieg statt uvicorn-Start.
    assert main(["daemon", "run", "--worker", "--connect"]) == 2
    assert main(["daemon", "run", "--scheduler", "--connect"]) == 2


def test_status_unreachable_returns_1(env_iso):
    assert main(["daemon", "status", "--port", "59999"]) == 1


def test_daemon_no_subcommand_prints_help(env_iso):
    assert main(["daemon"]) == 1
