"""Exec-Backend: Host vs. Container (PLAN-8 Slice A)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bibi.wrapper import exec_backend, output, run_job


# ── reine argv-Konstruktion (kein Docker) ────────────────────────────────────

def test_host_mode_is_child_argv_with_worktree_cwd():
    spec = exec_backend.build_exec(
        ["bash", "-c", "echo hi"], {"BIBI_WORKTREE": "/wt"})
    assert spec.argv == ["bash", "-c", "echo hi"]
    assert spec.cwd == "/wt"


def test_container_mode_wraps_in_docker_run():
    env = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
           "BIBI_JOB_ID": "abc123", "BIBI_DOCKER_BIN": "/d/docker",
           "BIBI_JOB_IMAGE": "img:1", "PATH": "/usr/bin"}
    spec = exec_backend.build_exec(["claude", "-p", "x"], env)
    assert spec.cwd is None
    assert spec.argv[:9] == [
        "/d/docker", "run", "--rm", "--name", "bibi-abc123",
        "-v", "/wt:/workspace", "-w", "/workspace"]
    assert spec.argv[-3:] == ["img:1", "claude", "-p", "x"][-3:]
    assert spec.argv[-4:] == ["img:1", "claude", "-p", "x"]
    # docker-bin-Dir vorne im PATH (Cred-Helper)
    assert spec.env["PATH"].startswith("/d" + os.pathsep)


def test_container_passes_api_key_only_when_set():
    base = {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
            "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker"}
    without = exec_backend.build_exec(["sh"], base)
    assert "-e" not in without.argv
    with_key = exec_backend.build_exec(["sh"], {**base, "ANTHROPIC_API_KEY": "sk-x"})
    assert "-e" in with_key.argv
    i = with_key.argv.index("-e")
    assert with_key.argv[i + 1] == "ANTHROPIC_API_KEY"   # nur Name, Wert vom Host


def test_container_passes_oauth_token():
    # claude-code Abo-Auth via CLAUDE_CODE_OAUTH_TOKEN (sk-ant-oat…), nicht API-Key.
    spec = exec_backend.build_exec(["claude"], {
        "BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt", "BIBI_JOB_ID": "j",
        "BIBI_DOCKER_BIN": "/d/docker", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x"})
    assert "CLAUDE_CODE_OAUTH_TOKEN" in spec.argv
    i = spec.argv.index("CLAUDE_CODE_OAUTH_TOKEN")
    assert spec.argv[i - 1] == "-e"   # -e CLAUDE_CODE_OAUTH_TOKEN (Wert vom Host)


def test_default_image_when_unset():
    spec = exec_backend.build_exec(
        ["sh"], {"BIBI_EXEC_MODE": "container", "BIBI_WORKTREE": "/wt",
                 "BIBI_JOB_ID": "j", "BIBI_DOCKER_BIN": "/d/docker"})
    assert exec_backend.DEFAULT_IMAGE in spec.argv


def test_container_name():
    assert exec_backend.container_name("deadbeef") == "bibi-deadbeef"


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
def test_smoke_container_job_writes_workspace_and_output(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    out = tmp_path / "output.jsonl"
    env = {
        **os.environ,
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": "job",
        "BIBI_JOB_CMD": "echo hallo && echo neu > probe.txt && echo fertig",
        "BIBI_JOB_IMAGE": "bash:5",
        "BIBI_WORKTREE": str(wt),
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_JOB_ID": "smoke" + os.urandom(3).hex(),
    }
    code = run_job(env)
    assert code == 0
    # Output gepumpt (Host fängt die Container-stdout):
    assert output.lines(out, "out") == ["hallo", "fertig"]
    # Container schrieb in /workspace ⇒ Host-Worktree:
    assert (wt / "probe.txt").read_text().strip() == "neu"
