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


# ── config.public_host (PLAN-22 Befund 6) ────────────────────────────────────


def test_public_host_default_localhost(cfg_home: Path):
    assert config.public_host() == "localhost"


def test_public_host_from_env(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_PUBLIC_HOST", "sarasate.tail9f9173.ts.net")
    assert config.public_host() == "sarasate.tail9f9173.ts.net"


def test_public_host_from_config_file(cfg_home: Path):
    config.write_env({"BIBI_PUBLIC_HOST": "sarasate.tail9f9173.ts.net"})
    assert config.public_host() == "sarasate.tail9f9173.ts.net"


def test_public_host_falls_back_to_scheduler_url_hostname(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # Client-Rolle-Heuristik: kein eigenes BIBI_PUBLIC_HOST gesetzt, aber ein
    # BIBI_SCHEDULER_URL — dessen Hostname ist besser als gar nichts, aber
    # kein Beweis (löst den Host-Rolle-Fall wie sarasate nicht, s. Befund 6).
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.tail9f9173.ts.net:8780")
    assert config.public_host() == "sarasate.tail9f9173.ts.net"


def test_public_host_explicit_wins_over_scheduler_url(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BIBI_PUBLIC_HOST", "explicit.example")
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://other.example:8780")
    assert config.public_host() == "explicit.example"


def test_public_host_no_config_no_scheduler_url_is_localhost(cfg_home: Path):
    # Der Host-Rolle-Fall (z. B. sarasate selbst): kein BIBI_SCHEDULER_URL,
    # weil der Knoten selbst der Scheduler ist — ohne explizites
    # BIBI_PUBLIC_HOST bleibt nur der alte, für Remote-Zugriff falsche
    # localhost-Fallback (dokumentierte Einschränkung, PLAN-22 Befund 6).
    assert config.public_host() == "localhost"


def test_maintenance_toggle(team_repo):
    assert state.get_maintenance() is False
    state.set_maintenance(True)
    assert state.get_maintenance() is True
    state.set_maintenance(False)
    assert state.get_maintenance() is False
