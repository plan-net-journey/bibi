"""config.daemon_port + state.maintenance (PLAN-2 §2.2/§2.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import config, state


@pytest.fixture
def cfg_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Isoliert gegen die reale ~/.config/bibi/env + Shell-Env des Test-Hosts —
    # sonst hängt daemon_port() (seit dem BIBI_SCHEDULER_URL-Fallback) vom
    # jeweiligen Rechner ab statt deterministisch zu sein.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)
    monkeypatch.delenv("BIBI_CONFIG_PATH", raising=False)
    return tmp_path


def test_daemon_port_default(cfg_home: Path):
    assert config.daemon_port() == 8769


def test_daemon_port_from_env(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "9000")
    assert config.daemon_port() == 9000


def test_daemon_port_invalid_falls_back(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "not-a-port")
    assert config.daemon_port() == 8769


def test_daemon_port_from_scheduler_url_env(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://localhost:8780")
    assert config.daemon_port() == 8780


def test_daemon_port_from_scheduler_url_config_file(cfg_home: Path):
    config.write_env({"BIBI_SCHEDULER_URL": "http://localhost:8780"})
    assert config.daemon_port() == 8780


def test_daemon_port_env_takes_precedence_over_scheduler_url(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "9000")
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://localhost:8780")
    assert config.daemon_port() == 9000


def test_daemon_port_invalid_falls_back_to_scheduler_url(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "not-a-port")
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://localhost:8780")
    assert config.daemon_port() == 8780


def test_maintenance_toggle(team_repo):
    assert state.get_maintenance() is False
    state.set_maintenance(True)
    assert state.get_maintenance() is True
    state.set_maintenance(False)
    assert state.get_maintenance() is False
