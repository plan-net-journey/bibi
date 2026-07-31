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
                              "sarasate.tail9f9173.ts.net"])
    rc = main(["init"])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://sarasate:8769"
    assert env["BIBI_ROLE"] == "worker,synchronizer"
    assert env["BIBI_REMOTE"] == "git@x/r.git"
    assert env["BIBI_CLAUDE_BIN"] == "/opt/bin/claude"
    assert env["BIBI_NODE_NAME"] == "sarasate-client"
    assert env["BIBI_PUBLIC_HOST"] == "sarasate.tail9f9173.ts.net"


def test_init_empty_input_uses_defaults(cfg_home: Path, monkeypatch):
    _feed_input(monkeypatch, ["", "", "", "", "", ""])
    main(["init"])
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == config.KEYS["BIBI_SCHEDULER_URL"]
    assert env["BIBI_ROLE"] == config.KEYS["BIBI_ROLE"]
    assert env["BIBI_CLAUDE_BIN"] == config.KEYS["BIBI_CLAUDE_BIN"]
    assert env["BIBI_NODE_NAME"] == config.KEYS["BIBI_NODE_NAME"]
    assert env["BIBI_PUBLIC_HOST"] == config.KEYS["BIBI_PUBLIC_HOST"]


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
    ])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://sarasate:8769"
    assert env["BIBI_ROLE"] == "connect,synchronizer"
    assert env["BIBI_REMOTE"] == "git@x/r.git"
    assert env["BIBI_CLAUDE_BIN"] == "/opt/bin/claude"
    assert env["BIBI_NODE_NAME"] == "m.mustertest-container"
    assert env["BIBI_PUBLIC_HOST"] == "sarasate.tail9f9173.ts.net"
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


# --- Dritter Block: der LAUFENDE Daemon (m.rau/bibi#59) -----------------------
#
# Die beiden Blöcke davor zeigen Soll-Werte: den Repo-State und die Knoten-
# Config. Was tatsächlich läuft — und unter welcher Adresse man es im Browser
# findet — stand nirgends. Mit der Port-Automatik aus #45 ist das der Normalfall
# und nicht mehr der Randfall: der Port wird zur Laufzeit gewählt.


def test_status_shows_running_daemon_with_port_and_url(team_repo: Path, cfg_home: Path, capsys):
    from bibi.daemon import portfile
    portfile.write(63913, host="127.0.0.1", roles="synchronizer,controller", session=True)
    main(["status"])
    out = capsys.readouterr().out
    # Die URL ist der eigentliche Zweck: kopierbar, mit /-/ Präfix, nicht nur die Zahl.
    assert "http://localhost:63913/-/" in out
    assert "63913" in out


def test_status_names_the_daemons_origin(team_repo: Path, cfg_home: Path, capsys):
    """Sitzung oder Unit — der Unterschied entscheidet, ob ein Neustart von
    außen den Daemon zurückbringt oder eine Sitzung ohne Dashboard hinterlässt."""
    from bibi.daemon import portfile
    portfile.write(8769, host="127.0.0.1", roles="worker", session=True)
    main(["status"])
    assert "Sitzung" in capsys.readouterr().out

    portfile.write(8769, host="127.0.0.1", roles="worker", session=False)
    main(["status"])
    assert "Unit" in capsys.readouterr().out


def test_status_says_when_no_daemon_runs(team_repo: Path, cfg_home: Path, capsys):
    main(["status"])
    out = capsys.readouterr().out
    assert "läuft nicht" in out


def test_status_ignores_stale_portfile(team_repo: Path, cfg_home: Path, capsys):
    """Ein ``kill -9`` lässt die Datei stehen. Eine Portnummer ohne
    Lebendigkeitsprüfung wäre eine Falle, die auf einen toten Port zeigt."""
    import json
    from bibi.daemon import portfile
    p = portfile.port_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    # PID 2**31-1 existiert praktisch nie; write() würde die eigene, lebende setzen.
    p.write_text(json.dumps({"port": 63913, "pid": 2**31 - 1}), encoding="utf-8")
    main(["status"])
    out = capsys.readouterr().out
    assert "läuft nicht" in out
    assert "63913" not in out


def test_status_separates_configured_from_running_roles(team_repo: Path, cfg_home: Path, capsys):
    """Der Soll/Ist-Unterschied ist die eigentliche Auskunft und muss im
    Wortlaut sichtbar sein — ein Daemon kann mit anderen Rollen laufen, als in
    der ``env`` stehen (etwa ein ``--connect`` der Sitzung)."""
    from bibi.daemon import portfile
    config.write_env({"BIBI_ROLE": "synchronizer", "BIBI_SCHEDULER_URL": "http://h:8769"})
    portfile.write(63913, host="127.0.0.1", roles="synchronizer,controller,connect", session=True)
    main(["status"])
    out = capsys.readouterr().out
    assert "konfiguriert" in out
    assert "laufend" in out
    assert "synchronizer,controller,connect" in out


def test_status_url_uses_public_host_when_bound_to_all_interfaces(
        team_repo: Path, cfg_home: Path, monkeypatch, capsys):
    """An 0.0.0.0 gebunden ist ``localhost`` für einen Remote-Host die falsche
    Auskunft — dann gilt ``BIBI_PUBLIC_HOST``. Bei 127.0.0.1 dagegen bleibt es
    bei localhost, auch wenn ein Public-Host gesetzt ist: der Daemon ist von
    außen dann gar nicht erreichbar."""
    from bibi.daemon import portfile
    monkeypatch.setenv("BIBI_PUBLIC_HOST", "sarasate.example")
    portfile.write(8780, host="0.0.0.0", roles="worker", session=False)
    main(["status"])
    assert "http://sarasate.example:8780/-/" in capsys.readouterr().out

    portfile.write(8780, host="127.0.0.1", roles="worker", session=False)
    main(["status"])
    assert "http://localhost:8780/-/" in capsys.readouterr().out
