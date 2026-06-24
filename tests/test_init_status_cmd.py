"""Integrationstests für `bibi-ctrl init` und `bibi-ctrl status`."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import config
from bibi.ctrl import main


@pytest.fixture
def cfg_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _feed_input(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *_: next(it))


def test_status_without_config(cfg_home: Path, capsys):
    rc = main(["status"])
    assert rc == 0
    assert "init" in capsys.readouterr().out


def test_init_writes_env(cfg_home: Path, monkeypatch, capsys):
    _feed_input(monkeypatch, ["http://sarasate:8769", "worker,synchronizer", "git@x/r.git"])
    rc = main(["init"])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://sarasate:8769"
    assert env["BIBI_ROLE"] == "worker,synchronizer"
    assert env["BIBI_REMOTE"] == "git@x/r.git"


def test_init_empty_input_uses_defaults(cfg_home: Path, monkeypatch):
    _feed_input(monkeypatch, ["", "", ""])
    main(["init"])
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == config.KEYS["BIBI_SCHEDULER_URL"]
    assert env["BIBI_ROLE"] == config.KEYS["BIBI_ROLE"]


def test_init_idempotent_decline_keeps_existing(cfg_home: Path, monkeypatch):
    config.write_env({"BIBI_ROLE": "synchronizer", "BIBI_SCHEDULER_URL": "http://old"})
    _feed_input(monkeypatch, ["N"])  # Überschreiben? → Nein
    rc = main(["init"])
    assert rc == 0
    assert config.read_env()["BIBI_SCHEDULER_URL"] == "http://old"


def test_init_force_skips_confirmation(cfg_home: Path, monkeypatch):
    config.write_env({"BIBI_ROLE": "synchronizer"})
    _feed_input(monkeypatch, ["http://new", "worker", ""])  # keine j/N-Frage
    rc = main(["init", "--force"])
    assert rc == 0
    assert config.read_env()["BIBI_SCHEDULER_URL"] == "http://new"


def test_status_shows_values(cfg_home: Path, capsys):
    config.write_env({
        "BIBI_SCHEDULER_URL": "http://sarasate:8769",
        "BIBI_ROLE": "worker",
        "BIBI_REMOTE": "git@x/r.git",
    })
    main(["status"])
    out = capsys.readouterr().out
    assert "http://sarasate:8769" in out
    assert "worker" in out
