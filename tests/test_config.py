"""Tests für bibi.config (~/.config/bibi/env-IO)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import config


@pytest.fixture
def cfg_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BIBI_CONFIG_PATH", raising=False)
    return tmp_path


def test_env_path_respects_xdg(cfg_home: Path):
    assert config.env_path() == cfg_home / "bibi" / "env"


def test_env_path_respects_explicit_override(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    explicit = cfg_home / "client" / "bibi-env"
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(explicit))
    assert config.env_path() == explicit


def test_env_path_explicit_override_takes_precedence_over_xdg(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
):
    explicit = cfg_home / "client" / "bibi-env"
    monkeypatch.setenv("BIBI_CONFIG_PATH", str(explicit))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home / "other"))
    assert config.env_path() == explicit


def test_env_path_blank_override_falls_back_to_xdg(cfg_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_CONFIG_PATH", "  ")
    assert config.env_path() == cfg_home / "bibi" / "env"


def test_read_env_missing_file(cfg_home: Path):
    assert config.read_env() == {}


def test_write_then_read_roundtrip(cfg_home: Path):
    values = {
        "BIBI_SCHEDULER_URL": "http://sarasate:8769",
        "BIBI_ROLE": "worker,synchronizer",
        "BIBI_REMOTE": "https://example/repo.git",
        "BIBI_CLAUDE_BIN": "/home/u/.local/bin/claude",
        "BIBI_WORKER_NAME": "sarasate-client",
        "BIBI_PUBLIC_HOST": "sarasate.tail9f9173.ts.net",
        "BIBI_STATUS_POLL_INTERVAL": "30",
        "BIBI_JOB_STATUS_POLL_INTERVAL": "2",
        "BIBI_NODE_ID": "abc123",
    }
    config.write_env(values)
    assert config.read_env() == values


# ── node_id() — stabile Knoten-Identität für Connected Clients (Bibi4-Iteration) ─


def test_node_id_generates_and_persists_when_missing(cfg_home: Path):
    val = config.node_id()
    assert val and len(val) == 32  # uuid4().hex
    assert config.read_env()["BIBI_NODE_ID"] == val


def test_node_id_stable_across_calls(cfg_home: Path):
    first = config.node_id()
    second = config.node_id()
    assert first == second


def test_node_id_preserves_other_existing_keys(cfg_home: Path):
    config.write_env({"BIBI_ROLE": "worker", "BIBI_WORKER_NAME": "sarasate-client"})
    config.node_id()
    env = config.read_env()
    assert env["BIBI_ROLE"] == "worker"
    assert env["BIBI_WORKER_NAME"] == "sarasate-client"


def test_write_env_only_known_keys(cfg_home: Path):
    config.write_env({"BIBI_ROLE": "worker", "GARBAGE": "x"})
    env = config.read_env()
    assert "GARBAGE" not in env
    assert env["BIBI_ROLE"] == "worker"
    # fehlende bekannte Keys werden leer geschrieben
    assert env["BIBI_REMOTE"] == ""


def test_write_env_permissions_0600(cfg_home: Path):
    p = config.write_env({"BIBI_ROLE": "worker"})
    assert (p.stat().st_mode & 0o777) == 0o600


def test_read_env_ignores_comments_and_blanks(cfg_home: Path):
    p = config.env_path()
    p.parent.mkdir(parents=True)
    p.write_text("# Kommentar\n\nBIBI_ROLE=worker\n  \nBIBI_REMOTE = x \n", encoding="utf-8")
    env = config.read_env()
    assert env["BIBI_ROLE"] == "worker"
    assert env["BIBI_REMOTE"] == "x"  # getrimmt


# ── PLAN-32 Stufe 32.2/32.3: Credential-Distribution ─────────────────────────


def test_distributable_config_filters_by_prefix():
    env = {"BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-x", "BIBI_SCHEDULER_URL": "http://h",
          "BIBI_JOB_ENV_FOO": "bar"}
    assert config.distributable_config(env) == {
        "BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-x", "BIBI_JOB_ENV_FOO": "bar"}


def test_distributable_config_excludes_empty_values():
    assert config.distributable_config({"BIBI_JOB_ENV_FOO": ""}) == {}


def test_config_version_stable_and_order_independent():
    v1 = config.config_version({"BIBI_JOB_ENV_A": "1", "BIBI_JOB_ENV_B": "2"})
    v2 = config.config_version({"BIBI_JOB_ENV_B": "2", "BIBI_JOB_ENV_A": "1"})
    assert v1 == v2


def test_config_version_changes_when_value_changes():
    v1 = config.config_version({"BIBI_JOB_ENV_A": "1"})
    v2 = config.config_version({"BIBI_JOB_ENV_A": "2"})
    assert v1 != v2


def test_read_distributed_env_empty_when_no_file(cfg_home: Path):
    assert config.read_distributed_env() == {}
    assert config.distributed_config_version() is None


def test_write_then_read_distributed_env_roundtrip(cfg_home: Path):
    config.write_distributed_env({"BIBI_JOB_ENV_ANTHROPIC_API_KEY": "sk-x"}, version="v1")
    env = config.read_distributed_env()
    assert env["BIBI_JOB_ENV_ANTHROPIC_API_KEY"] == "sk-x"
    assert config.distributed_config_version() == "v1"


def test_write_distributed_env_permissions_0600(cfg_home: Path):
    p = config.write_distributed_env({"BIBI_JOB_ENV_X": "y"}, version="v1")
    assert (p.stat().st_mode & 0o777) == 0o600


def test_write_distributed_env_lives_next_to_main_env(cfg_home: Path):
    # Entscheidung 4: zweite, env vorgelagerte Datei — erbt automatisch
    # BIBI_CONFIG_PATHs Mehrfach-Instanz-Trennung (env_path().parent).
    p = config.write_distributed_env({"BIBI_JOB_ENV_X": "y"}, version="v1")
    assert p.parent == config.env_path().parent


def test_write_distributed_env_replaces_not_merges(cfg_home: Path):
    config.write_distributed_env({"BIBI_JOB_ENV_A": "1", "BIBI_JOB_ENV_B": "2"}, version="v1")
    config.write_distributed_env({"BIBI_JOB_ENV_A": "1"}, version="v2")
    env = config.read_distributed_env()
    assert "BIBI_JOB_ENV_B" not in env
