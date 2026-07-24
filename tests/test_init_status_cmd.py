"""Integrationstests für `bibi-ctrl init` und `bibi-ctrl status`."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import case_store, config, frontmatter, state
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
    _feed_input(monkeypatch, ["http://sarasate:8769", "worker,synchronizer",
                              "git@x/r.git", "/opt/bin/claude", "sarasate-client",
                              "sarasate.tail9f9173.ts.net", "60", "1"])
    rc = main(["init"])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://sarasate:8769"
    assert env["BIBI_ROLE"] == "worker,synchronizer"
    assert env["BIBI_REMOTE"] == "git@x/r.git"
    assert env["BIBI_CLAUDE_BIN"] == "/opt/bin/claude"
    assert env["BIBI_NODE_NAME"] == "sarasate-client"
    assert env["BIBI_PUBLIC_HOST"] == "sarasate.tail9f9173.ts.net"
    assert env["BIBI_STATUS_POLL_INTERVAL"] == "60"
    assert env["BIBI_JOB_STATUS_POLL_INTERVAL"] == "1"


def test_init_empty_input_uses_defaults(cfg_home: Path, monkeypatch):
    _feed_input(monkeypatch, ["", "", "", "", "", "", "", ""])
    main(["init"])
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == config.KEYS["BIBI_SCHEDULER_URL"]
    assert env["BIBI_ROLE"] == config.KEYS["BIBI_ROLE"]
    assert env["BIBI_CLAUDE_BIN"] == config.KEYS["BIBI_CLAUDE_BIN"]
    assert env["BIBI_NODE_NAME"] == config.KEYS["BIBI_NODE_NAME"]
    assert env["BIBI_PUBLIC_HOST"] == config.KEYS["BIBI_PUBLIC_HOST"]
    assert env["BIBI_STATUS_POLL_INTERVAL"] == config.KEYS["BIBI_STATUS_POLL_INTERVAL"]
    assert env["BIBI_JOB_STATUS_POLL_INTERVAL"] == config.KEYS["BIBI_JOB_STATUS_POLL_INTERVAL"]


def test_init_idempotent_decline_keeps_existing(cfg_home: Path, monkeypatch):
    config.write_env({"BIBI_ROLE": "synchronizer", "BIBI_SCHEDULER_URL": "http://old"})
    _feed_input(monkeypatch, ["N"])  # Überschreiben? → Nein
    rc = main(["init"])
    assert rc == 0
    assert config.read_env()["BIBI_SCHEDULER_URL"] == "http://old"


def test_init_force_skips_confirmation(cfg_home: Path, monkeypatch):
    config.write_env({"BIBI_ROLE": "synchronizer"})
    _feed_input(monkeypatch, ["http://new", "worker", "", "", "", "", "", ""])  # keine j/N-Frage
    rc = main(["init", "--force"])
    assert rc == 0
    assert config.read_env()["BIBI_SCHEDULER_URL"] == "http://new"


# --- PLAN-33 Stufe 33.3: `bibi-ctrl init --non-interactive` -------------------


def _forbid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_kw):
        raise AssertionError("input() darf im --non-interactive-Modus nie aufgerufen werden")
    monkeypatch.setattr("builtins.input", _boom)


def test_init_non_interactive_writes_explicit_flags(cfg_home: Path, monkeypatch):
    _forbid_input(monkeypatch)
    rc = main([
        "init", "--non-interactive",
        "--scheduler-url", "http://sarasate:8769",
        "--role", "connect,synchronizer",
        "--remote", "git@x/r.git",
        "--claude-bin", "/opt/bin/claude",
        "--node-name", "m.mustertest-container",
        "--public-host", "sarasate.tail9f9173.ts.net",
        "--status-poll-interval", "60",
        "--job-status-poll-interval", "1",
    ])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://sarasate:8769"
    assert env["BIBI_ROLE"] == "connect,synchronizer"
    assert env["BIBI_REMOTE"] == "git@x/r.git"
    assert env["BIBI_CLAUDE_BIN"] == "/opt/bin/claude"
    assert env["BIBI_NODE_NAME"] == "m.mustertest-container"
    assert env["BIBI_PUBLIC_HOST"] == "sarasate.tail9f9173.ts.net"
    assert env["BIBI_STATUS_POLL_INTERVAL"] == "60"
    assert env["BIBI_JOB_STATUS_POLL_INTERVAL"] == "1"
    assert env["BIBI_NODE_ID"]  # weiterhin self-healing generiert, kein Flag dafür


def test_init_non_interactive_missing_flags_use_engine_defaults(cfg_home: Path, monkeypatch):
    _forbid_input(monkeypatch)
    rc = main(["init", "--non-interactive", "--scheduler-url", "http://sarasate:8769"])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://sarasate:8769"
    assert env["BIBI_ROLE"] == config.KEYS["BIBI_ROLE"]
    assert env["BIBI_CLAUDE_BIN"] == config.KEYS["BIBI_CLAUDE_BIN"]
    assert env["BIBI_PUBLIC_HOST"] == config.KEYS["BIBI_PUBLIC_HOST"]


def test_init_non_interactive_missing_flags_preserve_existing_values(
    cfg_home: Path, monkeypatch
):
    config.write_env({"BIBI_ROLE": "worker", "BIBI_CLAUDE_BIN": "/custom/claude"})
    _forbid_input(monkeypatch)
    rc = main(["init", "--non-interactive", "--scheduler-url", "http://new:8769"])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://new:8769"
    assert env["BIBI_ROLE"] == "worker"  # unveraendert, kein --role uebergeben
    assert env["BIBI_CLAUDE_BIN"] == "/custom/claude"  # ebenso unveraendert


def test_init_non_interactive_skips_overwrite_confirmation(cfg_home: Path, monkeypatch):
    config.write_env({"BIBI_SCHEDULER_URL": "http://old:8769"})
    _forbid_input(monkeypatch)  # keine --force noetig, --non-interactive fragt nie
    rc = main(["init", "--non-interactive", "--scheduler-url", "http://new:8769"])
    assert rc == 0
    assert config.read_env()["BIBI_SCHEDULER_URL"] == "http://new:8769"


def test_init_flags_without_non_interactive_are_rejected(cfg_home: Path, capsys):
    rc = main(["init", "--scheduler-url", "http://sarasate:8769"])
    assert rc == 2
    assert "--non-interactive" in capsys.readouterr().err


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


# --- Repo-State-Tests (brauchen ein echtes Team-Repo via team_repo-Fixture) ---

def test_status_shows_path_none(team_repo: Path, capsys):
    main(["status"])
    assert "path: (none)" in capsys.readouterr().out


def test_status_shows_auto_sync_on(team_repo: Path, capsys):
    state.set_auto_sync(True)
    main(["status"])
    assert "auto_sync: on" in capsys.readouterr().out


def test_status_shows_auto_sync_off_by_default(team_repo: Path, capsys):
    main(["status"])
    assert "auto_sync: off" in capsys.readouterr().out


def test_status_shows_sync_conflict(team_repo: Path, capsys):
    state.set_sync_conflict(True)
    main(["status"])
    assert "sync_conflict: true" in capsys.readouterr().out


def test_status_no_sync_conflict_line_when_false(team_repo: Path, capsys):
    main(["status"])
    assert "sync_conflict" not in capsys.readouterr().out


def test_status_shows_protocol_when_case_active(
    team_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    from bibi import repo
    folder = case_store.create_case("Testfall")
    frontmatter.patch(folder / "README.md", protocol="./protocol.json")
    monkeypatch.chdir(folder)
    repo._root_of.cache_clear()
    main(["status"])
    out = capsys.readouterr().out
    assert "protocol: ./protocol.json" in out
    repo._root_of.cache_clear()


def test_status_no_protocol_line_when_no_case(team_repo: Path, capsys):
    main(["status"])
    assert "protocol:" not in capsys.readouterr().out


# --- PLAN-30 Ebene 3: Eskalations-Sicht (dieselbe Quarantäne-Liste aus Ebene 2) ---

def test_status_shows_escalated_merge_branches(team_repo: Path, capsys):
    from bibi.daemon import merge_quarantine
    for trunk_sha in ("s1", "s2", "s3"):
        merge_quarantine.record_failure(team_repo, "agent/stuck", trunk_sha=trunk_sha)
    main(["status"])
    out = capsys.readouterr().out
    assert "merge_stuck: 1 (agent/stuck)" in out


def test_status_no_merge_stuck_line_below_threshold(team_repo: Path, capsys):
    from bibi.daemon import merge_quarantine
    merge_quarantine.record_failure(team_repo, "agent/almost", trunk_sha="s1")
    main(["status"])
    assert "merge_stuck" not in capsys.readouterr().out


def test_status_no_merge_stuck_line_when_none(team_repo: Path, capsys):
    main(["status"])
    assert "merge_stuck" not in capsys.readouterr().out
