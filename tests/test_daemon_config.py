"""config.daemon_port + state.maintenance (PLAN-2 §2.2/§2.5)."""

from __future__ import annotations

import pytest

from bibi import config, state


def test_daemon_port_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    assert config.daemon_port() == 8769


def test_daemon_port_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "9000")
    assert config.daemon_port() == 9000


def test_daemon_port_invalid_falls_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "not-a-port")
    assert config.daemon_port() == 8769


def test_maintenance_toggle(team_repo):
    assert state.get_maintenance() is False
    state.set_maintenance(True)
    assert state.get_maintenance() is True
    state.set_maintenance(False)
    assert state.get_maintenance() is False
