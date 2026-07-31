"""Sitzungs-Registry (m.rau/bibi#46).

Mehrere Sitzungen teilen sich einen Daemon; er endet mit der **letzten**, nicht
mit der ersten. Ein Daemon aus einer Autostart-Unit wird von keiner Sitzung
gestoppt.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import pytest

from bibi.ctrl import daemon_cmd
from bibi.daemon import session_registry
from bibi.daemon.sweeper import Sweeper


def _dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


# ── An- und Abmelden ────────────────────────────────────────────────────────


def test_register_then_count(team_repo: Path):
    session_registry.register()
    assert session_registry.count() == 1


def test_register_uses_the_pid_as_filename(team_repo: Path):
    p = session_registry.register()
    assert p.name == f"{os.getpid()}.json"
    assert p.parent == team_repo / "data" / session_registry.DIRNAME


def test_two_sessions_do_not_overwrite_each_other(team_repo: Path):
    session_registry.register(pid=os.getpid())
    session_registry.register(pid=os.getppid())
    assert session_registry.count() == 2


def test_unregister_removes_only_that_session(team_repo: Path):
    session_registry.register(pid=os.getpid())
    session_registry.register(pid=os.getppid())
    assert session_registry.unregister(pid=os.getppid()) is True
    assert session_registry.live_pids() == [os.getpid()]


def test_unregister_of_an_unknown_session_is_false(team_repo: Path):
    assert session_registry.unregister(pid=999_999) is False


def test_count_is_zero_without_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    assert session_registry.sessions_dir() is None
    assert session_registry.register() is None
    assert session_registry.count() == 0


def test_count_is_zero_before_anything_registered(team_repo: Path):
    assert session_registry.count() == 0


# ── Der Kern: eine abgestürzte Sitzung zählt nicht mit ──────────────────────


def test_a_dead_session_does_not_count(team_repo: Path):
    # Das ist der Grund für die PID statt eines Zählers: wer abstürzt,
    # dekrementiert nie — seine PID lebt aber auch nicht mehr.
    session_registry.register(pid=_dead_pid())
    assert session_registry.count() == 0


def test_a_dead_session_is_pruned(team_repo: Path):
    dead = _dead_pid()
    session_registry.register(pid=dead)
    session_registry.count()
    assert not (team_repo / "data" / session_registry.DIRNAME / f"{dead}.json").exists()


def test_a_live_session_survives_the_prune(team_repo: Path):
    session_registry.register()
    session_registry.count()
    assert session_registry.count() == 1


def test_garbage_files_are_ignored(team_repo: Path):
    d = team_repo / "data" / session_registry.DIRNAME
    d.mkdir(parents=True)
    (d / "kein-pid.json").write_text("{}", encoding="utf-8")
    assert session_registry.count() == 0


# ── Der Sweeper zählt und fährt herunter ────────────────────────────────────


def _sweeper(**kw) -> Sweeper:
    calls: list[int] = []
    s = Sweeper(autorun=False, session_scoped=True,
                on_last_session_gone=lambda: calls.append(1), **kw)
    s.shutdowns = calls  # Testhilfe, kein Produktionsattribut
    return s


def _t(offset: float) -> float:
    """Zeitstempel relativ zu *jetzt*.

    Der Sweeper setzt ``_last_session_check`` beim Bau auf ``time.time()``,
    damit die erste Prüfung nach einem vollen Intervall kommt statt sofort —
    ein kleiner absoluter ``now``-Wert läge dagegen immer *vor* dem Startwert
    und die Prüfung liefe nie an.
    """
    import time
    return time.time() + offset


def test_sweeper_shuts_down_when_the_last_session_ends(team_repo: Path):
    s = _sweeper()
    session_registry.register()
    s.tick_once(now=_t(100))         # erste Zählung: eine Sitzung da
    assert s.shutdowns == []
    session_registry.unregister()
    s.tick_once(now=_t(200))         # …und weg
    assert s.shutdowns == [1]


def test_sweeper_waits_for_the_LAST_session(team_repo: Path):
    # Der Einwand, aus dem das Issue entstand: Sitzung A startet den Daemon,
    # B hängt sich an, A endet — B darf nicht ohne Daemon dastehen.
    s = _sweeper()
    session_registry.register(pid=os.getpid())
    session_registry.register(pid=os.getppid())
    s.tick_once(now=_t(100))
    session_registry.unregister(pid=os.getpid())
    s.tick_once(now=_t(200))
    assert s.shutdowns == []          # B lebt noch
    session_registry.unregister(pid=os.getppid())
    s.tick_once(now=_t(300))
    assert s.shutdowns == [1]


def test_sweeper_does_not_shut_down_before_any_session_appeared(team_repo: Path):
    # Startet der Daemon eine Handbreit vor „seiner" Sitzung, stünde der Zähler
    # beim ersten Durchlauf auf 0 — das heißt „die erste ist noch nicht da",
    # nicht „die letzte ist gegangen".
    s = _sweeper()
    s.tick_once(now=_t(100))
    s.tick_once(now=_t(200))
    assert s.shutdowns == []


def test_sweeper_ignores_sessions_when_not_session_scoped(team_repo: Path):
    # Ein Daemon aus einer Autostart-Unit wird NIE von einer Sitzung gestoppt,
    # egal wie der Zähler steht.
    calls: list[int] = []
    s = Sweeper(autorun=False, session_scoped=False,
                on_last_session_gone=lambda: calls.append(1))
    session_registry.register()
    s.tick_once(now=_t(100))
    session_registry.unregister()
    s.tick_once(now=_t(200))
    assert calls == []


def test_sweeper_respects_the_check_interval(team_repo: Path):
    # Im 2-Sekunden-Takt des Sweepers wäre die Zählung Verschwendung — sie läuft
    # im groben 45-s-Takt der PID-Prüfung aus #38.
    s = _sweeper()
    session_registry.register()
    s.tick_once(now=_t(100))
    session_registry.unregister()
    s.tick_once(now=_t(110))         # zu früh, wird übersprungen
    assert s.shutdowns == []
    s.tick_once(now=_t(200))
    assert s.shutdowns == [1]


def test_default_shutdown_uses_sigterm(monkeypatch: pytest.MonkeyPatch):
    # Über SIGTERM, nicht os._exit(): nur so greifen uvicorns Frist und der
    # Job-Drain im lifespan-Finally (#49).
    import signal
    from bibi.daemon import sweeper as sweeper_mod
    sent: list = []
    monkeypatch.setattr(sweeper_mod.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    sweeper_mod._shutdown_self()
    assert sent == [(os.getpid(), signal.SIGTERM)]


# ── Das Flag am Startbefehl ─────────────────────────────────────────────────


def test_run_parser_has_session_flag():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    daemon_cmd.register(sub)
    assert parser.parse_args(["daemon", "run"]).session is False
    assert parser.parse_args(["daemon", "run", "--session"]).session is True


def test_run_passes_session_scoped_to_create_app(env_iso_for_session, monkeypatch):
    import uvicorn
    from bibi.daemon import app as app_mod
    seen: dict = {}
    real = app_mod.create_app

    def _spy(*a, **kw):
        seen["session_scoped"] = kw.get("session_scoped")
        return real(*a, **kw)

    monkeypatch.setattr("bibi.daemon.app.create_app", _spy)
    monkeypatch.setattr(uvicorn.Server, "run",
                        lambda self, sockets=None: sockets and sockets[0].close())
    monkeypatch.setenv("BIBI_DAEMON_PORT", "")

    daemon_cmd.run(argparse.Namespace(
        synchronizer=False, scheduler=False, worker=False, controller=True,
        connect=False, pull=False, push=False, session=True,
        host="127.0.0.1", port="auto", log_level=None))

    assert seen["session_scoped"] is True


@pytest.fixture
def env_iso_for_session(team_repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("BIBI_CONFIG_PATH", raising=False)
    return team_repo
