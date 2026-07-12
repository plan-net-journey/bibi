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
    }
    config.write_env(values)
    assert config.read_env() == values


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
