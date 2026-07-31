"""Port-Automatik (m.rau/bibi#45): Portdatei, Lebendigkeitsprüfung, ``--port auto``."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from bibi import config, repo
from bibi.ctrl import daemon_cmd
from bibi.daemon import portfile


# ── Ablage und Lebendigkeit ─────────────────────────────────────────────────


def test_write_then_read_roundtrip(team_repo: Path):
    portfile.write(54321, host="127.0.0.1", roles="synchronizer,controller")
    assert portfile.read_port() == 54321
    entry = portfile.read()
    assert entry["pid"] == os.getpid()
    assert entry["roles"] == "synchronizer,controller"


def test_write_lands_under_gitignored_data(team_repo: Path):
    p = portfile.write(9999)
    assert p == team_repo / "data" / portfile.FILENAME
    assert p.exists()


def test_read_none_without_file(team_repo: Path):
    assert portfile.read_port() is None


def test_read_none_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Kein git-Repo: port_file() liefert None statt den Prozess zu beenden.
    monkeypatch.chdir(tmp_path)
    assert portfile.port_file() is None
    assert portfile.read_port() is None
    assert portfile.write(1234) is None


def test_read_none_when_process_is_dead(team_repo: Path):
    # Der Kern der Sache: ein kill -9 lässt die Datei stehen. Ohne PID-Prüfung
    # zeigte daemon_port() danach dauerhaft auf einen toten Port.
    p = team_repo / "data" / portfile.FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"port": 54321, "pid": _dead_pid()}), encoding="utf-8")
    assert portfile.read_port() is None
    assert p.exists(), "ein Leser räumt nicht auf — das tut clear()"


def test_read_none_on_corrupt_file(team_repo: Path):
    p = team_repo / "data" / portfile.FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{kein json", encoding="utf-8")
    assert portfile.read_port() is None


def test_read_none_when_port_missing(team_repo: Path):
    p = team_repo / "data" / portfile.FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert portfile.read_port() is None


def test_clear_removes_own_entry(team_repo: Path):
    portfile.write(54321)
    portfile.clear()
    assert not (team_repo / "data" / portfile.FILENAME).exists()


def test_clear_keeps_foreign_entry(team_repo: Path):
    # Zwei Daemons auf einem Checkout sind nicht vorgesehen (#46 zählt Sitzungen
    # gerade deshalb) — passiert es doch, darf der gehende nicht den Eintrag des
    # bleibenden wegräumen.
    p = team_repo / "data" / portfile.FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"port": 54321, "pid": os.getpid() + 1}), encoding="utf-8")
    portfile.clear()
    assert p.exists()


def test_clear_is_noop_without_file(team_repo: Path):
    portfile.clear()  # darf nicht werfen


# ── Freien Port belegen ─────────────────────────────────────────────────────


def test_bind_free_returns_bound_socket():
    sock, port = portfile.bind_free("127.0.0.1")
    try:
        assert port > 0
        assert sock.getsockname()[1] == port
        # Der Socket ist gebunden und bleibt es — genau das schließt das
        # Zeitfenster, in dem ein anderer Prozess den Port wegschnappen könnte.
        other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):
                other.bind(("127.0.0.1", port))
        finally:
            other.close()
    finally:
        sock.close()


def test_bind_free_twice_gives_two_ports():
    a, pa = portfile.bind_free("127.0.0.1")
    b, pb = portfile.bind_free("127.0.0.1")
    try:
        assert pa != pb
    finally:
        a.close()
        b.close()


# ── daemon_port(): wo die Portdatei in der Kette steht ──────────────────────


def test_daemon_port_uses_running_daemon(team_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    portfile.write(54321)
    assert config.daemon_port() == 54321


def test_daemon_port_prefers_live_file_over_scheduler_url(
    team_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # Live-Befund schlägt Konfigurations-Vermutung: die URL sagt, wo der
    # Scheduler steht, die Datei sagt, was hier tatsächlich lauscht.
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate:8780")
    portfile.write(54321)
    assert config.daemon_port() == 54321


def test_daemon_port_env_still_wins_over_live_file(
    team_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # BIBI_DAEMON_PORT bleibt der explizite „sprich mit DIESEM Daemon"-Override
    # — daran hängt jedes Mehrfach-Instanz-Setup auf einem Checkout.
    monkeypatch.setenv("BIBI_DAEMON_PORT", "9000")
    portfile.write(54321)
    assert config.daemon_port() == 9000


def test_daemon_port_falls_back_when_daemon_is_gone(
    team_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate:8780")
    p = team_repo / "data" / portfile.FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"port": 54321, "pid": _dead_pid()}), encoding="utf-8")
    assert config.daemon_port() == 8780


# ── --port auto / --port <n> ────────────────────────────────────────────────


def test_auto_port_from_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    assert daemon_cmd._is_auto_port("auto") is True
    assert daemon_cmd._is_auto_port("AUTO") is True
    assert daemon_cmd._is_auto_port("8780") is False


def test_auto_port_from_env_when_no_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "auto")
    assert daemon_cmd._is_auto_port(None) is True
    # Ein explizites Flag gewinnt über die Umgebung.
    assert daemon_cmd._is_auto_port("8780") is False


def test_no_auto_port_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    assert daemon_cmd._is_auto_port(None) is False


def test_explicit_port_parsing():
    assert daemon_cmd._explicit_port("8780") == 8780
    assert daemon_cmd._explicit_port(8780) == 8780
    # 0/leer/ungültig ⇒ Aufrufer fällt auf config.daemon_port() zurück.
    assert daemon_cmd._explicit_port(None) == 0
    assert daemon_cmd._explicit_port("") == 0
    assert daemon_cmd._explicit_port("auto") == 0


def test_run_parser_accepts_auto():
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    daemon_cmd.register(sub)
    args = parser.parse_args(["daemon", "run", "--port", "auto"])
    assert args.port == "auto"


# ── repo.root_or_none() ─────────────────────────────────────────────────────


def test_root_or_none_finds_repo(team_repo: Path):
    assert repo.root_or_none() == team_repo


def test_root_or_none_returns_none_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    assert repo.root_or_none() is None


def _dead_pid() -> int:
    """Eine PID, die sicher nicht mehr lebt: ein Kindprozess, der beendet und
    abgeräumt wurde (kein Zombie mehr, also auch kein ``os.kill(pid, 0)``-Treffer)."""
    import subprocess
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid
