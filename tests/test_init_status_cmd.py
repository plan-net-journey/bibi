"""Integrationstests für `bibi-ctrl init` und `bibi-ctrl status`."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import case_store, config, frontmatter, repo, state
from bibi.ctrl import main
from tests.conftest import _init_repo


def _feed_input(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> None:
    """Antworten **nach Prompt-Stichwort**, nicht nach Position.

    Vorher war das eine Liste in Prompt-Reihenfolge. Sie brach, sobald die
    Reihenfolge sich änderte — bei #61 zog die Rollenfrage nach vorn, und drei
    Tests scheiterten, die mit der Änderung inhaltlich nichts zu tun hatten.
    Genau die Fragilität, die ``init_cmd``s Moduldoc für gescriptete
    Stdin-Eingaben beschreibt („jede Prompt-Reihenfolge-Änderung bricht es
    leise"); für Tests gilt sie nicht weniger. Nicht getroffene Prompts
    bekommen den leeren String, also den angebotenen Default.
    """
    def _input(prompt: str = "") -> str:
        for needle, value in answers.items():
            if needle in prompt:
                return value
        return ""
    monkeypatch.setattr("builtins.input", _input)


def test_status_without_config(cfg_home: Path, capsys):
    rc = main(["status"])
    assert rc == 0
    assert "init" in capsys.readouterr().out


def test_init_writes_env(cfg_home: Path, monkeypatch, capsys):
    _feed_input(monkeypatch, {
        "Rollen": "worker,synchronizer,connect",
        "Scheduler": "http://sarasate:8769",
        "Git-Remote": "git@x/r.git",
        "claude-Binary": "/opt/bin/claude",
        "Knoten-Name": "sarasate-client",
        "erreichbarer": "sarasate.tail9f9173.ts.net",
    })
    rc = main(["init"])
    assert rc == 0
    env = config.read_env()
    assert env["BIBI_SCHEDULER_URL"] == "http://sarasate:8769"
    assert env["BIBI_ROLE"] == "worker,synchronizer,connect"
    assert env["BIBI_REMOTE"] == "git@x/r.git"
    assert env["BIBI_CLAUDE_BIN"] == "/opt/bin/claude"
    assert env["BIBI_NODE_NAME"] == "sarasate-client"
    assert env["BIBI_PUBLIC_HOST"] == "sarasate.tail9f9173.ts.net"


def test_init_empty_input_uses_defaults(cfg_home: Path, monkeypatch):
    _feed_input(monkeypatch, {})   # überall Enter → Defaults
    main(["init"])
    env = config.read_env()
    # Die Scheduler-URL ist hier ausdrücklich NICHT dabei: die Default-Rolle
    # trägt kein `connect`, und ohne das gibt es keinen Scheduler, dessen
    # Adresse man voreinstellen könnte (m.rau/bibi#61). Wer alles durchklickt,
    # bekommt einen hostlosen Knoten — und der ist eine gültige Aufstellung,
    # keine unvollständige.
    assert env["BIBI_SCHEDULER_URL"] == ""
    assert "connect" not in config.KEYS["BIBI_ROLE"]
    assert env["BIBI_ROLE"] == config.KEYS["BIBI_ROLE"]
    assert env["BIBI_CLAUDE_BIN"] == config.KEYS["BIBI_CLAUDE_BIN"]
    assert env["BIBI_NODE_NAME"] == config.KEYS["BIBI_NODE_NAME"]
    assert env["BIBI_PUBLIC_HOST"] == config.KEYS["BIBI_PUBLIC_HOST"]


def test_init_idempotent_decline_keeps_existing(cfg_home: Path, monkeypatch):
    config.write_env({"BIBI_ROLE": "synchronizer", "BIBI_SCHEDULER_URL": "http://old"})
    _feed_input(monkeypatch, {"Überschreiben": "N"})
    rc = main(["init"])
    assert rc == 0
    assert config.read_env()["BIBI_SCHEDULER_URL"] == "http://old"


def test_init_force_skips_confirmation(cfg_home: Path, monkeypatch):
    config.write_env({"BIBI_ROLE": "synchronizer"})
    _feed_input(monkeypatch, {"Scheduler": "http://new", "Rollen": "worker,connect"})
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


def test_status_names_markers_of_other_sessions(team_repo: Path, monkeypatch, capsys):
    """m.rau/bibi#97: ``path: (none)`` allein verschweigt, dass daneben Marken
    auf einen Case zeigen — und genau daran ist am 2026-08-01 nichts aufgefallen."""
    folder = case_store.create_case("Testfall")
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    state.set_path(f"case/{folder.name}")

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")   # Wiederverbindung
    main(["status"])
    out = capsys.readouterr().out
    assert "path: (none)" in out
    assert "park_foreign:" in out
    assert folder.name in out


def test_status_stays_quiet_when_nothing_was_ever_parked(team_repo: Path, monkeypatch, capsys):
    """Die Gegenprobe: „nie geparkt" bleibt eine stille, normale Lage."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")
    main(["status"])
    out = capsys.readouterr().out
    assert "path: (none)" in out
    assert "park_foreign:" not in out


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
    assert "sync_conflict:" not in capsys.readouterr().out


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
    assert "merge_stuck:" not in capsys.readouterr().out


def test_status_no_merge_stuck_line_when_none(team_repo: Path, capsys):
    main(["status"])
    assert "merge_stuck:" not in capsys.readouterr().out


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


# --- Scheduler-URL nur, wenn es einen Scheduler gibt (m.rau/bibi#61) ---------
#
# Gemessen am 2026-07-31, nicht abgeleitet: `init --non-interactive --role
# "synchronizer,controller"` schrieb bisher still
# BIBI_SCHEDULER_URL=http://localhost:8769 — eine Adresse, an der nie etwas
# antwortet. `--connect` ist der einzige Grund, warum die URL existiert.


def test_init_leaves_scheduler_url_empty_without_connect(cfg_home: Path):
    main(["init", "--non-interactive", "--role", "synchronizer,controller"])
    assert config.read_env().get("BIBI_SCHEDULER_URL") == ""


def test_init_applies_the_default_when_connect_is_in_the_roles(cfg_home: Path):
    main(["init", "--non-interactive", "--role", "synchronizer,connect"])
    assert config.read_env().get("BIBI_SCHEDULER_URL") == "http://localhost:8769"


def test_init_keeps_an_existing_url_when_connect_is_dropped(cfg_home: Path):
    """Kein Datenverlust: wer die Rollen umstellt, soll seine Adresse nicht
    verlieren — nur der *Default* wird nicht mehr aufgedrängt."""
    config.write_env({"BIBI_SCHEDULER_URL": "http://sarasate:8780", "BIBI_ROLE": "connect"})
    main(["init", "--non-interactive", "--role", "synchronizer"])
    assert config.read_env().get("BIBI_SCHEDULER_URL") == "http://sarasate:8780"


def test_init_explicit_url_wins_without_connect(cfg_home: Path):
    """Ein ausdrücklich gesetztes Flag ist eine Ansage und wird nicht
    wegoptimiert — etwa, wenn `connect` erst später dazukommt."""
    main(["init", "--non-interactive", "--role", "synchronizer",
          "--scheduler-url", "http://sarasate:8780"])
    assert config.read_env().get("BIBI_SCHEDULER_URL") == "http://sarasate:8780"


def test_init_does_not_prompt_for_the_url_without_connect(cfg_home: Path, monkeypatch):
    """Interaktiv ist das die eigentliche Auskunft: die erste Frage des
    Onboardings war bisher die einzige, auf die es hostlos keine richtige
    Antwort gibt."""
    asked: list[str] = []

    def _input(prompt: str = "") -> str:
        asked.append(prompt)
        return "synchronizer,controller" if "Rollen" in prompt else ""

    monkeypatch.setattr("builtins.input", _input)
    main(["init"])
    assert not any("Scheduler" in p for p in asked)


def test_init_prompts_for_the_url_with_connect(cfg_home: Path, monkeypatch):
    asked: list[str] = []

    def _input(prompt: str = "") -> str:
        asked.append(prompt)
        return "synchronizer,connect" if "Rollen" in prompt else ""

    monkeypatch.setattr("builtins.input", _input)
    main(["init"])
    assert any("Scheduler" in p for p in asked)


# --- m.rau/bibi#174: Profile als Eingabe, Rollen als Innenleben --------------


def test_init_profile_client_derives_the_roles(cfg_home: Path, monkeypatch):
    _forbid_input(monkeypatch)
    rc = main(["init", "--non-interactive", "--profile", "client"])
    assert rc == 0
    assert config.read_env()["BIBI_ROLE"] == "synchronizer,controller"


def test_init_profile_scheduler_has_no_controller(cfg_home: Path, monkeypatch):
    # Entscheidung m.rau, 2026-08-06: der Scheduler ist Backend.
    _forbid_input(monkeypatch)
    assert main(["init", "--non-interactive", "--profile", "scheduler"]) == 0
    assert config.read_env()["BIBI_ROLE"] == "synchronizer,scheduler"


def test_init_with_ui_gives_the_first_node_a_surface(cfg_home: Path, monkeypatch):
    _forbid_input(monkeypatch)
    assert main(["init", "--non-interactive", "--profile", "scheduler+worker",
                 "--with-ui"]) == 0
    assert config.read_env()["BIBI_ROLE"] == "synchronizer,scheduler,worker,controller"


def test_init_profile_and_role_together_are_refused(cfg_home: Path, monkeypatch, capsys):
    # Zwei Antworten auf dieselbe Frage — welche gilt, wäre geraten.
    _forbid_input(monkeypatch)
    rc = main(["init", "--non-interactive", "--profile", "client",
               "--role", "synchronizer,worker"])
    assert rc == 2
    assert "--profile" in capsys.readouterr().err


def test_init_unknown_profile_names_the_known_ones(cfg_home: Path, monkeypatch, capsys):
    _forbid_input(monkeypatch)
    rc = main(["init", "--non-interactive", "--profile", "host"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "host" in err and "scheduler+worker" in err


def test_init_worker_without_a_scheduler_is_refused(cfg_home: Path, monkeypatch, capsys):
    # Ein Worker ohne Scheduler hat niemanden, der ihm Aufträge gibt: er
    # startet, meldet sich gesund und empfängt nie etwas.
    _forbid_input(monkeypatch)
    rc = main(["init", "--non-interactive", "--profile", "worker"])
    assert rc == 2
    assert "scheduler" in capsys.readouterr().err.lower()


def test_init_worker_with_an_existing_url_is_fine(cfg_home: Path, monkeypatch):
    # Geprüft wird der wirksame Wert, nicht ob das Flag mitkam.
    config.write_env({"BIBI_SCHEDULER_URL": "http://sarasate:8780"})
    _forbid_input(monkeypatch)
    assert main(["init", "--non-interactive", "--profile", "worker"]) == 0
    assert config.read_env()["BIBI_ROLE"] == "synchronizer,worker"


def test_init_asks_for_the_node_kind_first(cfg_home: Path, monkeypatch):
    asked: list[str] = []

    def _input(prompt: str = "") -> str:
        asked.append(prompt)
        return "client" if "Knotenart" in prompt else ""

    monkeypatch.setattr("builtins.input", _input)
    main(["init"])
    assert "Knotenart" in asked[0]                      # zuerst, nicht irgendwo
    assert config.read_env()["BIBI_ROLE"] == "synchronizer,controller"


def test_init_still_accepts_a_role_list_as_the_expert_path(cfg_home: Path, monkeypatch):
    # #174 verlangt ausdrücklich, dass die Rollenliste nicht verschwindet —
    # sie ist nur nicht mehr die erste Frage.
    def _input(prompt: str = "") -> str:
        return "synchronizer,worker,controller" if "Knotenart" in prompt else ""

    monkeypatch.setattr("builtins.input", _input)
    main(["init"])
    assert config.read_env()["BIBI_ROLE"] == "synchronizer,worker,controller"


def test_a_client_profile_gets_asked_for_the_scheduler_url(cfg_home: Path, monkeypatch):
    # Die Lücke, die das Profil schließt: bisher entschied das Wort "connect"
    # im Rollen-String darüber, ob nach der URL gefragt wird — ein Token, das
    # gar keine Rolle ist und das parse_role_env danach wegwirft. Wer "client"
    # eingab, wurde nie gefragt und hatte hinterher keinen Scheduler.
    asked: list[str] = []

    def _input(prompt: str = "") -> str:
        asked.append(prompt)
        return "client" if "Knotenart" in prompt else ""

    monkeypatch.setattr("builtins.input", _input)
    main(["init"])
    assert any("Scheduler" in p for p in asked)


# --- m.rau/bibi#177: der Startschlüssel nur, wenn er etwas bedeutet ----------


def _prompts_for(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> list[str]:
    """Interaktiv durchlaufen und **alle** Prompt-Texte einsammeln."""
    asked: list[str] = []

    def _input(prompt: str = "") -> str:
        asked.append(prompt)
        for needle, value in answers.items():
            if needle in prompt:
                return value
        return ""

    monkeypatch.setattr("builtins.input", _input)
    main(["init"])
    return asked


def test_no_bootstrap_token_question_without_a_scheduler(cfg_home: Path, monkeypatch):
    # Ein hostloser Client hat niemanden, bei dem er sich anmelden könnte —
    # der Startschlüssel ist dort ein Feld ohne Bedeutung.
    asked = _prompts_for(monkeypatch, {"Knotenart": "client"})
    assert not any("chlüssel" in p or "BOOTSTRAP" in p for p in asked)


def test_bootstrap_token_is_asked_when_there_is_a_scheduler(cfg_home: Path, monkeypatch):
    asked = _prompts_for(monkeypatch, {"Knotenart": "client",
                                       "Scheduler": "http://sarasate:8780"})
    assert any("chlüssel" in p for p in asked)


def test_bootstrap_prompt_explains_instead_of_naming_the_variable(cfg_home: Path,
                                                                  monkeypatch):
    # #177s zweite Hälfte: der rohe Variablenname sagt einem neuen Menschen
    # nichts. Er soll lesen, was der Wert tut und was ein leerer bedeutet.
    asked = _prompts_for(monkeypatch, {"Knotenart": "client",
                                       "Scheduler": "http://sarasate:8780"})
    prompt = next(p for p in asked if "chlüssel" in p)
    assert "BIBI_BOOTSTRAP_TOKEN" not in prompt
    assert "leer" in prompt.lower()          # sagt, was ein leerer Wert heißt


def test_bootstrap_token_still_settable_by_flag_without_connect(cfg_home: Path,
                                                               monkeypatch):
    # Nicht fragen heißt nicht verbieten: ein ausdrückliches Flag ist eine
    # Ansage und wird nicht wegoptimiert — dieselbe Regel wie bei der
    # Scheduler-URL seit #61.
    _forbid_input(monkeypatch)
    assert main(["init", "--non-interactive", "--profile", "client",
                 "--token", "abc123"]) == 0
    assert config.read_env()["BIBI_BOOTSTRAP_TOKEN"] == "abc123"


# --- m.rau/bibi#52: eine fremde Konfiguration kann gar nicht mehr entstehen ---
#
# Hier stand bis zum 2026-08-07 ein Block, der den Backup-Mechanismus aus
# m.rau/bibi#173 prüfte: ``init`` erkannte an ``BIBI_REMOTE``, dass die Datei zu
# einem anderen Team-Repo gehört, legte ``env.bak-<stamp>`` an und sagte es.
#
# **Der Mechanismus ist entfallen, weil sein Anlass entfallen ist.** Die
# Konfiguration liegt seit #52 in ``<repo>/data/env``; zwei Instanzen auf einer
# Maschine sind zwei Repos und damit zwei Dateien, die einander nicht sehen. Ein
# Backup gegen ein Überschreiben, das nicht mehr stattfinden kann, wäre Pflege
# ohne Gegenwert — und ein Leser müsste raten, wovor es schützt.
#
# Was an seine Stelle tritt, ist die Zusage darunter: die Trennung selbst.


def test_two_repos_on_one_machine_keep_separate_configs(tmp_path: Path, monkeypatch):
    """Der Live-Fall vom 2026-08-06, diesmal ohne Schaden.

    Der Rechner betreibt eine Instanz; jemand richtet eine zweite ein. Vorher
    überschrieb das die erste — erst still, ab #173 mit Backup. Jetzt gehen sich
    beide gar nicht mehr an.
    """
    erste, zweite = tmp_path / "erste", tmp_path / "zweite"
    for r in (erste, zweite):
        r.mkdir()
        _init_repo(r)
    _forbid_input(monkeypatch)

    monkeypatch.chdir(erste)
    repo._root_of.cache_clear()
    assert main(["init", "--non-interactive", "--profile", "client",
                 "--remote", "https://github.com/org/erste.git"]) == 0
    id_erste = config.read_env()["BIBI_NODE_ID"]

    monkeypatch.chdir(zweite)
    repo._root_of.cache_clear()
    assert main(["init", "--non-interactive", "--profile", "client",
                 "--remote", "https://github.com/org/zweite.git"]) == 0
    id_zweite = config.read_env()["BIBI_NODE_ID"]

    # Zwei Knoten, zwei Identitäten — der Kern von #173, jetzt ohne Sonderweg.
    assert id_erste != id_zweite
    assert not list(zweite.glob("data/env.bak-*")), "kein Backup nötig"

    # Und die erste ist unversehrt: eigene Identität, eigenes Remote.
    monkeypatch.chdir(erste)
    repo._root_of.cache_clear()
    env = config.read_env()
    assert env["BIBI_NODE_ID"] == id_erste
    assert env["BIBI_REMOTE"] == "https://github.com/org/erste.git"


def test_rerun_on_the_same_repo_keeps_the_node_identity(cfg_home: Path, monkeypatch):
    """Idempotenz: derselbe Knoten, zweimal eingerichtet, bleibt derselbe.

    Ein Repo ist ein Knoten. Dass ``init`` ein zweites Mal läuft — etwa nach
    einem Umzug des Remotes — ändert daran nichts.
    """
    _forbid_input(monkeypatch)
    assert main(["init", "--non-interactive", "--profile", "client",
                 "--remote", "https://github.com/org/erste.git"]) == 0
    erst = config.read_env()["BIBI_NODE_ID"]
    assert main(["init", "--non-interactive", "--profile", "client",
                 "--remote", "https://github.com/org/umgezogen.git"]) == 0
    assert config.read_env()["BIBI_NODE_ID"] == erst
    assert config.read_env()["BIBI_REMOTE"] == "https://github.com/org/umgezogen.git"


def test_init_carries_credentials_over(cfg_home: Path, monkeypatch):
    """``BIBI_JOB_ENV_*`` überlebt ein ``init`` (m.rau/bibi#51).

    Der dokumentierte Weg, ein Credential auf einen Knoten zu bringen, ist ein
    ``>>``-Append an diese Datei. Ein späteres ``init`` hat es bis zum
    2026-08-07 verworfen — ohne Sicherung, ohne Meldung, und die Sicherung aus
    #173 griff hier nicht, weil sie an ``_foreign`` hing.
    """
    config.write_env({"BIBI_ROLE": "synchronizer", "BIBI_JOB_ENV_TOKEN": "geheim"})
    _forbid_input(monkeypatch)
    assert main(["init", "--non-interactive", "--profile", "client"]) == 0
    assert config.read_env()["BIBI_JOB_ENV_TOKEN"] == "geheim"
