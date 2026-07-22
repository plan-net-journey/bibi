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
    monkeypatch.delenv("BIBI_STATUS_POLL_INTERVAL", raising=False)
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


# ── config.scheduler_base_url (PLAN-13 Stufe 13.0) ───────────────────────────


def test_scheduler_base_url_default(cfg_home: Path):
    assert config.scheduler_base_url() == "http://localhost:8769"


def test_scheduler_base_url_uses_full_remote_scheduler_url(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # Der eigentliche Bug-Fix: Host UND Port aus BIBI_SCHEDULER_URL, nicht
    # nur der Port gegen 127.0.0.1.
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.tail9f9173.ts.net:8780")
    assert config.scheduler_base_url() == "http://sarasate.tail9f9173.ts.net:8780"


def test_scheduler_base_url_from_config_file(cfg_home: Path):
    config.write_env({"BIBI_SCHEDULER_URL": "http://sarasate.tail9f9173.ts.net:8780"})
    assert config.scheduler_base_url() == "http://sarasate.tail9f9173.ts.net:8780"


def test_scheduler_base_url_strips_trailing_slash(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.tail9f9173.ts.net:8780/")
    assert config.scheduler_base_url() == "http://sarasate.tail9f9173.ts.net:8780"


def test_scheduler_base_url_daemon_port_env_wins_as_local_override(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # BIBI_DAEMON_PORT bedeutet explizit "mein eigener Daemon" — bleibt lokal,
    # auch wenn BIBI_SCHEDULER_URL auf einen entfernten Host zeigt.
    monkeypatch.setenv("BIBI_DAEMON_PORT", "9000")
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.tail9f9173.ts.net:8780")
    assert config.scheduler_base_url() == "http://127.0.0.1:9000"


def test_scheduler_base_url_on_scheduler_host_itself_stays_local(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # Auf dem Scheduler-Host selbst zeigt BIBI_SCHEDULER_URL bewusst auf sich
    # selbst (localhost) — muss unverändert korrekt bleiben.
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://localhost:8780")
    assert config.scheduler_base_url() == "http://localhost:8780"


# ── config.public_host (PLAN-22 Befund 6) ────────────────────────────────────


def test_public_host_default_localhost(cfg_home: Path):
    assert config.public_host() == "localhost"


def test_public_host_from_env(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_PUBLIC_HOST", "sarasate.tail9f9173.ts.net")
    assert config.public_host() == "sarasate.tail9f9173.ts.net"


def test_public_host_from_config_file(cfg_home: Path):
    config.write_env({"BIBI_PUBLIC_HOST": "sarasate.tail9f9173.ts.net"})
    assert config.public_host() == "sarasate.tail9f9173.ts.net"


def test_public_host_ignores_scheduler_url_falls_back_to_localhost(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # Bibi4-Iteration, User-Fund (App-Link auf dem Mac zeigt sarasates
    # Hostnamen statt localhost): die frühere "vom BIBI_SCHEDULER_URL
    # borgen"-Heuristik half laut eigener Doku nie dem Host-Rolle-Fall (der
    # braucht Stufe 1 ohnehin zwingend) und war für einen echten Remote-
    # Client wie diesen schlicht falsch — sie borgte den Hostnamen eines
    # FREMDEN Knotens. Ersatzlos entfernt: ohne explizites BIBI_PUBLIC_HOST
    # bleibt es beim reinen "localhost"-Default, kein Rätselraten mehr.
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.tail9f9173.ts.net:8780")
    assert config.public_host() == "localhost"


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


# ── config.status_poll_interval (PLAN-25 Befund 4) ──────────────────────────


def test_status_poll_interval_default_30(cfg_home: Path):
    assert config.status_poll_interval() == 30


def test_status_poll_interval_from_env(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_STATUS_POLL_INTERVAL", "60")
    assert config.status_poll_interval() == 60


def test_status_poll_interval_from_config_file(cfg_home: Path):
    config.write_env({"BIBI_STATUS_POLL_INTERVAL": "45"})
    assert config.status_poll_interval() == 45


def test_status_poll_interval_env_takes_precedence_over_config_file(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    config.write_env({"BIBI_STATUS_POLL_INTERVAL": "45"})
    monkeypatch.setenv("BIBI_STATUS_POLL_INTERVAL", "60")
    assert config.status_poll_interval() == 60


def test_status_poll_interval_invalid_falls_back_to_default(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BIBI_STATUS_POLL_INTERVAL", "not-a-number")
    assert config.status_poll_interval() == 30


# ── config.job_status_poll_interval (Bibi4-Iteration) ───────────────────────


def test_job_status_poll_interval_default_2(cfg_home: Path):
    assert config.job_status_poll_interval() == 2


def test_job_status_poll_interval_from_env(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_JOB_STATUS_POLL_INTERVAL", "1")
    assert config.job_status_poll_interval() == 1


def test_job_status_poll_interval_from_config_file(cfg_home: Path):
    config.write_env({"BIBI_JOB_STATUS_POLL_INTERVAL": "3"})
    assert config.job_status_poll_interval() == 3


def test_job_status_poll_interval_env_takes_precedence_over_config_file(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    config.write_env({"BIBI_JOB_STATUS_POLL_INTERVAL": "3"})
    monkeypatch.setenv("BIBI_JOB_STATUS_POLL_INTERVAL", "1")
    assert config.job_status_poll_interval() == 1


def test_job_status_poll_interval_invalid_falls_back_to_default(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BIBI_JOB_STATUS_POLL_INTERVAL", "not-a-number")
    assert config.job_status_poll_interval() == 2


def test_maintenance_toggle(team_repo):
    assert state.get_maintenance() is False
    state.set_maintenance(True)
    assert state.get_maintenance() is True
    state.set_maintenance(False)
    assert state.get_maintenance() is False
