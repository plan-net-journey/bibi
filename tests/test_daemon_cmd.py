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


def test_resolve_worker_connect_ok(env_iso):
    # Ab Stufe 3.6: Worker + connect ist gültig (Worker-Verbund).
    r, errs = daemon_cmd.resolve_from_args(_args(worker=True, connect=True))
    assert r.worker and r.connect and errs == []


def test_resolve_role_from_env(env_iso, monkeypatch: pytest.MonkeyPatch):
    from bibi import config
    config.write_env({"BIBI_ROLE": "synchronizer"})
    r, errs = daemon_cmd.resolve_from_args(_args())  # keine CLI-Flags
    assert r.synchronizer is True and errs == []


# --- _apply_auto_sync_default (User-Fund 2026-07-07, scheduler-Default) --------


def test_apply_auto_sync_default_turns_on_for_fresh_scheduler(env_iso):
    from bibi import state
    r, errs = daemon_cmd.resolve_from_args(_args(synchronizer=True, scheduler=True))
    assert errs == []
    assert state.get_auto_sync() is False
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is True


def test_apply_auto_sync_default_respects_explicit_off_on_scheduler(env_iso):
    from bibi import state
    state.set_auto_sync(False)   # bewusst abgeschaltet, nicht nur Werkseinstellung
    r, _errs = daemon_cmd.resolve_from_args(_args(synchronizer=True, scheduler=True))
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is False


def test_apply_auto_sync_default_does_not_touch_non_scheduler(env_iso):
    from bibi import state
    r, _errs = daemon_cmd.resolve_from_args(_args(synchronizer=True))
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is False


def test_apply_auto_sync_default_push_flag_wins_regardless_of_scheduler(env_iso):
    from bibi import state
    r, _errs = daemon_cmd.resolve_from_args(_args(synchronizer=True, push=True))
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is True


def test_run_returns_2_on_validation_error(env_iso):
    # main() parst + ruft run(); validierungsbedingter Frühausstieg (vor uvicorn).
    # scheduler⊥connect ist eine harte Invariante (§4.2) → Frühausstieg.
    assert main(["daemon", "run", "--scheduler", "--connect"]) == 2


def test_status_unreachable_returns_1(env_iso):
    assert main(["daemon", "status", "--port", "59999"]) == 1


def test_daemon_no_subcommand_prints_help(env_iso):
    assert main(["daemon"]) == 1
