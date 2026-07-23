"""Worker-Lifecycle im Container-Modus (PLAN-8 Slice B): Exec-Konfig + graceful kill."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bibi.daemon import worker
from bibi.daemon.worker import Worker
from bibi.wrapper import exec_backend

pytestmark = pytest.mark.slow


# ── Exec-Konfig (Env > Config) ───────────────────────────────────────────────

def test_exec_config_and_is_container(monkeypatch, team_repo):
    monkeypatch.setattr("bibi.daemon.worker.config.read_env",
                        lambda: {"BIBI_EXEC_MODE": "container", "BIBI_JOB_IMAGE": "img:9",
                                 "ANTHROPIC_API_KEY": "sk-x"})
    monkeypatch.delenv("BIBI_EXEC_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = worker._exec_config()
    assert cfg["BIBI_EXEC_MODE"] == "container"
    assert cfg["BIBI_JOB_IMAGE"] == "img:9"
    assert cfg["ANTHROPIC_API_KEY"] == "sk-x"
    assert worker._is_container() is True


def test_exec_config_reads_prefixed_job_env_credential(monkeypatch, team_repo):
    # PLAN-32 Stufe 32.0: ANTHROPIC_API_KEY/CLAUDE_CODE_OAUTH_TOKEN wandern
    # unter BIBI_JOB_ENV_-Präfix — dieselbe Namenskonvention wie jedes andere
    # Job-Credential, kein Sonderfall mehr über den generischen Präfix-Scan hinaus.
    monkeypatch.setattr("bibi.daemon.worker.config.read_env",
                        lambda: {"BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-prefixed"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BIBI_JOB_ENV_ANTHROPIC_API_KEY", raising=False)
    cfg = worker._exec_config()
    assert cfg["ANTHROPIC_API_KEY"] == "sk-prefixed"


def test_exec_config_prefixed_credential_wins_over_bare_fallback(monkeypatch, team_repo):
    # Migration: sind beide Formen gesetzt (Übergangszeit), gewinnt die
    # präfigierte — der bare Fallback ist nur für Knoten gedacht, die noch
    # nicht umbenannt haben.
    monkeypatch.setattr("bibi.daemon.worker.config.read_env",
                        lambda: {"BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-prefixed",
                                 "ANTHROPIC_API_KEY": "sk-legacy"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BIBI_JOB_ENV_ANTHROPIC_API_KEY", raising=False)
    cfg = worker._exec_config()
    assert cfg["ANTHROPIC_API_KEY"] == "sk-prefixed"


def test_exec_config_includes_distributed_job_env_value(monkeypatch, team_repo):
    # PLAN-32 Stufe 32.2: vom Host verteilte BIBI_JOB_ENV_*-Werte fließen in
    # die Job-Injection ein, niedrigste Präzedenz.
    monkeypatch.setattr("bibi.daemon.worker.config.read_env", lambda: {})
    monkeypatch.setattr("bibi.daemon.worker.config.read_distributed_env",
                        lambda: {"BIBI_JOB_ENV_TEAM_KEY": "sk-distributed"})
    monkeypatch.delenv("BIBI_JOB_ENV_TEAM_KEY", raising=False)
    cfg = worker._exec_config()
    assert cfg["TEAM_KEY"] == "sk-distributed"


def test_exec_config_local_env_wins_over_distributed(monkeypatch, team_repo):
    # Entscheidung 4 (PLAN-32): lokal gewinnt immer — derselbe Key sowohl
    # verteilt als auch lokal in ~/.config/bibi/env gesetzt.
    monkeypatch.setattr("bibi.daemon.worker.config.read_env",
                        lambda: {"BIBI_JOB_ENV_TEAM_KEY": "sk-local"})
    monkeypatch.setattr("bibi.daemon.worker.config.read_distributed_env",
                        lambda: {"BIBI_JOB_ENV_TEAM_KEY": "sk-distributed"})
    monkeypatch.delenv("BIBI_JOB_ENV_TEAM_KEY", raising=False)
    cfg = worker._exec_config()
    assert cfg["TEAM_KEY"] == "sk-local"


def test_is_container_default_host(monkeypatch, team_repo):
    monkeypatch.setattr("bibi.daemon.worker.config.read_env", lambda: {})
    monkeypatch.delenv("BIBI_EXEC_MODE", raising=False)
    assert worker._is_container() is False


# ── Terminierung container-aware ─────────────────────────────────────────────

class _FakeProc:
    pid = 2_147_400_000  # existiert nicht → killpg wirft ProcessLookupError (gefangen)

    def poll(self) -> int | None:
        # Backstop-Thread (_terminate._escalate) fragt nach 5s proc.poll() ab —
        # ohne echten Subprozess "bereits beendet" simulieren, sonst AttributeError
        # im Daemon-Thread (bleedet als Warning in einen späteren, zeitgleichen Test).
        return 0


def test_terminate_container_calls_docker_stop(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: True)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: calls.append(args))
    worker._terminate(_FakeProc(), job_id="abc")
    assert calls == [["stop", "bibi-abc"]]


def test_terminate_host_no_docker(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: calls.append(args))
    worker._terminate(_FakeProc(), job_id="abc")
    assert calls == []


def test_kill_backstop_docker_kill_when_wrapper_gone(monkeypatch, team_repo):
    calls: list[list[str]] = []
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: True)
    monkeypatch.setattr("bibi.daemon.worker._docker", lambda args: calls.append(args))
    w = Worker(autopoll=False, worker_name="t")
    assert w.kill("gone") is True              # kein Proc registriert
    assert calls == [["kill", "bibi-gone"]]    # Container-Backstop


def test_kill_host_no_proc_is_false(monkeypatch, team_repo):
    monkeypatch.setattr("bibi.daemon.worker._is_container", lambda: False)
    w = Worker(autopoll=False, worker_name="t")
    assert w.kill("gone") is False


# ── Smoke gegen echtes Docker ────────────────────────────────────────────────

def _docker_ok() -> bool:
    bin_ = exec_backend.resolve_docker_bin(dict(os.environ))
    env = dict(os.environ)
    env["PATH"] = str(Path(bin_).parent) + os.pathsep + env.get("PATH", "")
    try:
        r = subprocess.run([bin_, "version", "--format", "{{.Server.Version}}"],
                           capture_output=True, text=True, env=env, timeout=15)
        return r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


docker = pytest.mark.skipif(not _docker_ok(), reason="kein laufendes Docker")


@docker
def test_smoke_docker_stop_terminates_running_container():
    bin_ = exec_backend.resolve_docker_bin(dict(os.environ))
    env = dict(os.environ)
    env["PATH"] = str(Path(bin_).parent) + os.pathsep + env.get("PATH", "")
    name = "bibi-smoke" + os.urandom(4).hex()
    subprocess.run([bin_, "run", "-d", "--rm", "--name", name, "bash:5", "sleep", "60"],
                   check=True, capture_output=True, env=env, timeout=60)
    try:
        worker._docker(["stop", name])  # das, was kill/_terminate intern aufruft
        r = subprocess.run([bin_, "ps", "--filter", f"name={name}", "--format", "{{.Names}}"],
                           capture_output=True, text=True, env=env, timeout=15)
        assert name not in r.stdout      # Container ist weg (gestoppt + --rm)
    finally:
        subprocess.run([bin_, "kill", name], capture_output=True, env=env, timeout=15)


def test_last_activity_clamps_to_run_start(tmp_path):
    # Veraltete output.jsonl (wiederverwendet pro job_id) darf Silence nicht sofort
    # auslösen: _last_activity nie vor dem Lauf-Start (default).
    import os as _os
    f = tmp_path / "out.jsonl"
    f.write_text("x")
    _os.utime(f, (1000.0, 1000.0))          # alte mtime
    assert worker._last_activity(f, default=5000.0) == 5000.0   # geklemmt auf Start
    _os.utime(f, (9000.0, 9000.0))          # frische mtime
    assert worker._last_activity(f, default=5000.0) == 9000.0   # echte Aktivität
    assert worker._last_activity(tmp_path / "none", default=5000.0) == 5000.0
