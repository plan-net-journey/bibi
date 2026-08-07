"""``daemon install --port`` und die Trennung der beiden Port-Fragen (#15/#45).

Beim Planen war vermutet worden, #45 erledige #15 mit. Beim Bauen zeigt sich das
Gegenteil: die Automatik löst den *interaktiven* Start, nicht den *festen*
Dienst — und ohne Gegenmaßnahme hätte sie ``install`` sogar verschlechtert.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bibi import config, repo
from bibi.ctrl import daemon_cmd
from bibi.daemon import install, portfile


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_DAEMON_PORT", raising=False)
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)
    monkeypatch.delenv("BIBI_CONFIG_PATH", raising=False)


# ── Die beiden Fragen sind verschieden ──────────────────────────────────────


def test_configured_port_ignores_a_running_daemon(cfg, team_repo: Path):
    """Der Kern: eine Unit beschreibt, wo künftig gelauscht werden soll.

    Ohne diese Trennung wanderte der flüchtige Port eines gerade laufenden
    Sitzungs-Daemons in eine dauerhafte Unit — eine Nummer, die nie jemand
    gewählt hat und die beim nächsten Sitzungsstart schon eine andere wäre.
    """
    portfile.write(54321)
    assert config.daemon_port() == 54321            # „wo lauscht es gerade"
    assert config.configured_daemon_port() == 8769  # „wo soll es künftig"


def test_configured_port_follows_the_env(cfg, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DAEMON_PORT", "8781")
    assert config.configured_daemon_port() == 8781


def test_configured_port_follows_the_scheduler_url(cfg, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate:8780")
    assert config.configured_daemon_port() == 8780


def test_configured_port_defaults(cfg, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(repo, "root_or_none", lambda: None)
    assert config.configured_daemon_port() == 8769


# ── Das Flag ────────────────────────────────────────────────────────────────


class _NoSubprocess:
    """Ersetzt den Namen ``subprocess`` **innerhalb von install** — nicht dessen
    ``run``-Attribut. Letzteres wäre dasselbe Modulobjekt, das auch
    ``repo._root_of()`` benutzt; der Test hätte git mit lahmgelegt (genau so
    einmal gefunden)."""

    @staticmethod
    def run(*_a, **_kw):
        return None


@pytest.fixture
def install_harness(team_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``install()`` aufrufbar machen, ohne eine echte launchd-Unit auf dem
    Entwicklerrechner zu hinterlassen."""
    seen: dict = {}
    repo.root()  # lru_cache füllen, solange git noch erreichbar ist
    monkeypatch.setattr(install, "_nonsnap_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(install.sys, "platform", "darwin")
    monkeypatch.setattr(install, "subprocess", _NoSubprocess)
    monkeypatch.setattr(install, "_plist_path", lambda label: tmp_path / f"{label}.plist")
    real = install.launchd_plist_text
    monkeypatch.setattr(install, "launchd_plist_text",
                        lambda **kw: (seen.update(kw), real(**kw))[1])
    return seen


def test_install_takes_an_explicit_port(cfg, install_harness):
    # Eine Maschine kann mehrere Instanzen tragen, jede mit eigenem festen Port
    # (sarasate: Host 8780, Client 8781, Testknoten 8782). Bisher war
    # `BIBI_DAEMON_PORT=… daemon install` der einzige Weg dorthin — eine
    # Umgebungsvariable als Pflichtargument getarnt.
    install.install(port=8782)
    assert install_harness["port"] == 8782


def test_install_without_a_port_uses_the_configuration(cfg, install_harness,
                                                       monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate:8780")
    portfile.write(54321)  # ein laufender Sitzungs-Daemon darf nicht gewinnen
    install.install()
    assert install_harness["port"] == 8780


def test_install_parser_has_port():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    daemon_cmd.register(sub)
    assert parser.parse_args(["daemon", "install"]).port == 0
    assert parser.parse_args(["daemon", "install", "--port", "8782"]).port == 8782


def test_install_cmd_passes_the_port(monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}
    monkeypatch.setattr(install, "install",
                        lambda **kw: (seen.update(kw), "ok")[1])
    daemon_cmd.install_cmd(argparse.Namespace(role=None, connect=False, port=8782))
    assert seen["port"] == 8782
    daemon_cmd.install_cmd(argparse.Namespace(role=None, connect=False, port=0))
    assert seen["port"] is None  # 0 heißt „nichts angegeben", nicht Port 0
