"""``bibi-ctrl daemon`` — Rollen-Auflösung + Subkommando-Verdrahtung (PLAN-2 §2.1)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bibi.ctrl import daemon_cmd, main


@pytest.fixture
def env_iso(team_repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # ~/.config/bibi/env isolieren, damit kein echtes BIBI_ROLE durchsickert.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("BIBI_CONFIG_PATH", raising=False)
    monkeypatch.delenv("BIBI_NODE_NAME", raising=False)
    monkeypatch.delenv("BIBI_WORKER_NAME", raising=False)
    return team_repo


def _args(**kw):
    ns = argparse.Namespace(synchronizer=False, scheduler=False, worker=False,
                            connect=False, pull=False, push=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_resolve_synchronizer_ok(env_iso):
    r, errs = daemon_cmd.resolve_from_args(_args(synchronizer=True))
    assert r.synchronizer is True
    assert errs == []


def test_resolve_scheduler_connect_invariant(env_iso):
    _r, errs = daemon_cmd.resolve_from_args(_args(scheduler=True, connect=True))
    assert any("connect" in e.lower() for e in errs)


def test_resolve_worker_startable(env_iso):
    # Ab Stufe 3.0 startbar (Vertrag als Stubs) — keine Fehler mehr.
    _r, errs = daemon_cmd.resolve_from_args(_args(worker=True))
    assert errs == []


def test_resolve_worker_connect_ok(env_iso):
    # Ab Stufe 3.6: Worker + connect ist gültig (Worker-Verbund).
    r, errs = daemon_cmd.resolve_from_args(_args(worker=True, connect=True))
    assert r.worker and r.connect and errs == []


def test_resolve_role_from_env(env_iso, monkeypatch: pytest.MonkeyPatch):
    from bibi import config
    config.write_env({"BIBI_ROLE": "synchronizer"})
    r, errs = daemon_cmd.resolve_from_args(_args())  # keine CLI-Flags
    assert r.synchronizer is True and errs == []


# --- _apply_auto_sync_default (User-Fund 2026-07-07, scheduler-Default) --------


def test_apply_auto_sync_default_turns_on_for_fresh_scheduler(env_iso):
    from bibi import state
    r, errs = daemon_cmd.resolve_from_args(_args(synchronizer=True, scheduler=True))
    assert errs == []
    assert state.get_auto_sync() is False
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is True


def test_apply_auto_sync_default_respects_explicit_off_on_scheduler(env_iso):
    from bibi import state
    state.set_auto_sync(False)   # bewusst abgeschaltet, nicht nur Werkseinstellung
    r, _errs = daemon_cmd.resolve_from_args(_args(synchronizer=True, scheduler=True))
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is False


def test_apply_auto_sync_default_does_not_touch_non_scheduler(env_iso):
    from bibi import state
    r, _errs = daemon_cmd.resolve_from_args(_args(synchronizer=True))
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is False


def test_apply_auto_sync_default_push_flag_wins_regardless_of_scheduler(env_iso):
    from bibi import state
    r, _errs = daemon_cmd.resolve_from_args(_args(synchronizer=True, push=True))
    daemon_cmd._apply_auto_sync_default(r)
    assert state.get_auto_sync() is True


# --- _resolve_worker_name (Host+Client unter demselben Hostnamen, §4.2/A12) ---


def test_resolve_worker_name_none_by_default(env_iso):
    assert daemon_cmd._resolve_worker_name() is None


def test_resolve_worker_name_from_env(env_iso, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_NODE_NAME", "sarasate-client")
    assert daemon_cmd._resolve_worker_name() == "sarasate-client"


def test_resolve_worker_name_from_config_file(env_iso):
    from bibi import config
    config.write_env({"BIBI_NODE_NAME": "sarasate-client"})
    assert daemon_cmd._resolve_worker_name() == "sarasate-client"


def test_resolve_worker_name_env_takes_precedence_over_file(
    env_iso, monkeypatch: pytest.MonkeyPatch
):
    from bibi import config
    config.write_env({"BIBI_NODE_NAME": "from-file"})
    monkeypatch.setenv("BIBI_NODE_NAME", "from-env")
    assert daemon_cmd._resolve_worker_name() == "from-env"


def test_resolve_worker_name_blank_falls_back_to_none(
    env_iso, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BIBI_NODE_NAME", "  ")
    assert daemon_cmd._resolve_worker_name() is None


# --- PLAN-34: BIBI_WORKER_NAME als Legacy-Fallback (Migration, kein Primärname mehr) ---


def test_resolve_worker_name_legacy_env_fallback(env_iso, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_WORKER_NAME", "legacy-name")
    assert daemon_cmd._resolve_worker_name() == "legacy-name"


def test_resolve_worker_name_legacy_config_file_fallback(env_iso):
    # config.write_env() filtert auf bekannte KEYS (BIBI_WORKER_NAME ist keiner
    # mehr) — eine schon migrierte Datei kann den alten Namen so nicht mehr
    # simulieren. Reale Alt-Dateien wurden aber von der Datei-Ebene selbst nie
    # gefiltert (read_env() parst jede Zeile ungefiltert) — direkt schreiben,
    # um genau diesen (noch nicht migrierten) Bestandsfall nachzustellen.
    from bibi import config
    p = config.env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("BIBI_WORKER_NAME=legacy-name\n", encoding="utf-8")
    assert daemon_cmd._resolve_worker_name() == "legacy-name"


def test_resolve_worker_name_new_name_file_wins_over_legacy_env(
    env_iso, monkeypatch: pytest.MonkeyPatch
):
    from bibi import config
    config.write_env({"BIBI_NODE_NAME": "new-name"})
    monkeypatch.setenv("BIBI_WORKER_NAME", "legacy-env-name")
    assert daemon_cmd._resolve_worker_name() == "new-name"


# --- Graceful-Shutdown-Frist (Case-Befund 2026-07-28g) -----------------------
#
# Ohne Frist wartet uvicorn beim SIGTERM unbegrenzt auf offene Verbindungen.
# Der Event-Bus-Strom (/-/events, PLAN-36) ist genau so eine Verbindung und
# schließt nie von selbst: jeder offene Browser-Tab hielt den Daemon fest —
# unter systemd bis zum 90-s-SIGKILL, unter launchd dauerhaft (der Prozess
# lauscht nicht mehr, lebt aber, also greift KeepAlive nicht).


def test_resolve_shutdown_timeout_default(env_iso):
    assert (daemon_cmd._resolve_shutdown_timeout()
            == daemon_cmd.SHUTDOWN_TIMEOUT_DEFAULT_S)


def test_resolve_shutdown_timeout_from_env(env_iso, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_SHUTDOWN_TIMEOUT_S", "3")
    assert daemon_cmd._resolve_shutdown_timeout() == 3


def test_resolve_shutdown_timeout_from_config_file(env_iso):
    # Kein KEYS-Eintrag (write_env filtert darauf) — der Wert ist bewusst kein
    # Interview-Feld von `init`, aber in der env-Datei trotzdem wirksam, weil
    # read_env() jede Zeile ungefiltert parst.
    from bibi import config
    p = config.env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("BIBI_SHUTDOWN_TIMEOUT_S=7\n", encoding="utf-8")
    assert daemon_cmd._resolve_shutdown_timeout() == 7


def test_resolve_shutdown_timeout_env_wins_over_file(
    env_iso, monkeypatch: pytest.MonkeyPatch
):
    from bibi import config
    p = config.env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("BIBI_SHUTDOWN_TIMEOUT_S=7\n", encoding="utf-8")
    monkeypatch.setenv("BIBI_SHUTDOWN_TIMEOUT_S", "3")
    assert daemon_cmd._resolve_shutdown_timeout() == 3


def test_resolve_shutdown_timeout_zero_means_immediate(
    env_iso, monkeypatch: pytest.MonkeyPatch
):
    # 0 ist ein gültiger Wunsch ("sofort abbrechen"), nicht "kein Wert gesetzt".
    monkeypatch.setenv("BIBI_SHUTDOWN_TIMEOUT_S", "0")
    assert daemon_cmd._resolve_shutdown_timeout() == 0


@pytest.mark.parametrize("raw", ["abc", "-5", "", "  ", "2.5"])
def test_resolve_shutdown_timeout_invalid_falls_back_to_default(
    env_iso, monkeypatch: pytest.MonkeyPatch, raw: str
):
    # Nie None (= uvicorns unbegrenztes Warten) — das ist genau der Fehler.
    monkeypatch.setenv("BIBI_SHUTDOWN_TIMEOUT_S", raw)
    assert (daemon_cmd._resolve_shutdown_timeout()
            == daemon_cmd.SHUTDOWN_TIMEOUT_DEFAULT_S)


def test_run_passes_shutdown_timeout_to_uvicorn(
    env_iso, monkeypatch: pytest.MonkeyPatch
):
    import uvicorn
    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(kw))
    monkeypatch.setenv("BIBI_SHUTDOWN_TIMEOUT_S", "4")
    # run() verankert den Bind-Port absichtlich im echten os.environ (PLAN-30
    # Ebene 1 v2, für Wrapper-Subprozesse) — hier vorab durch monkeypatch
    # geschleust, damit er nach dem Test wieder verschwindet statt in andere
    # Testmodule zu lecken (test_heartbeat erwartet ihn ungesetzt).
    monkeypatch.setenv("BIBI_DAEMON_PORT", "8769")

    rc = daemon_cmd.run(_args(controller=True, host="127.0.0.1", port=8769,
                              log_level=None))

    assert rc == 0
    assert captured["timeout_graceful_shutdown"] == 4


def test_run_returns_2_on_validation_error(env_iso):
    # main() parst + ruft run(); validierungsbedingter Frühausstieg (vor uvicorn).
    # scheduler⊥connect ist eine harte Invariante (§4.2) → Frühausstieg.
    assert main(["daemon", "run", "--scheduler", "--connect"]) == 2


def test_status_unreachable_returns_1(env_iso):
    assert main(["daemon", "status", "--port", "59999"]) == 1


def test_daemon_no_subcommand_prints_help(env_iso):
    assert main(["daemon"]) == 1
