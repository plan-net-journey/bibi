"""Worker-Lifecycle im Container-Modus (PLAN-8 Slice B): Exec-Konfig + graceful kill."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bibi.daemon import worker
from bibi.daemon.worker import Worker
from bibi.wrapper import exec_backend


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


def test_is_container_default_host(monkeypatch, team_repo):
    monkeypatch.setattr("bibi.daemon.worker.config.read_env", lambda: {})
    monkeypatch.delenv("BIBI_EXEC_MODE", raising=False)
    assert worker._is_container() is False


# ── Terminierung container-aware ─────────────────────────────────────────────

class _FakeProc:
    pid = 2_147_400_000  # existiert nicht → killpg wirft ProcessLookupError (gefangen)


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
