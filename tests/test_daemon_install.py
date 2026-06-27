"""Install-Text-Renderer (systemd/launchd) + Dispatch (PLAN-2 §2.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi.daemon import install


def test_systemd_unit_text():
    t = install.systemd_unit_text(
        root=Path("/srv/team"), uv="/home/u/.local/bin/uv", port=8769,
        user="mra", role="synchronizer",
    )
    assert "ExecStart=/home/u/.local/bin/uv run bibi-ctrl daemon run --host 0.0.0.0 --port 8769" in t
    assert "WorkingDirectory=/srv/team" in t
    assert "User=mra" in t
    assert "Environment=BIBI_ROLE=synchronizer" in t
    assert "WantedBy=multi-user.target" in t
    # uv-Verzeichnis steht vorne im PATH (Snap-Falle vermeiden).
    assert "Environment=PATH=/home/u/.local/bin:" in t


def test_systemd_unit_text_without_role_omits_env():
    t = install.systemd_unit_text(
        root=Path("/srv/team"), uv="/usr/bin/uv", port=8769, user="mra",
    )
    assert "BIBI_ROLE" not in t


def test_systemd_unit_name_is_per_repo():
    # Unit-Name aus dem Repo-Basisnamen → mehrere Instanzen je Host kollidieren nicht.
    assert install._systemd_unit_name(Path("/srv/bibi-v3")) == "bibi-v3-daemon.service"
    assert install._systemd_unit_name(
        Path("/home/u/Project/bibi-notes")) == "bibi-notes-daemon.service"
    # Verschiedene Repos → verschiedene Units (früher überschrieb der feste Name).
    assert (install._systemd_unit_name(Path("/a/bibi-notes"))
            != install._systemd_unit_name(Path("/a/bibi-v3")))
    assert str(install._systemd_unit_path(Path("/srv/bibi-v3"))) == \
        "/etc/systemd/system/bibi-v3-daemon.service"


def test_systemd_unit_text_carries_daemon_port():
    # Port landet als Env in der Unit (nicht nur in ExecStart), damit
    # config.daemon_port() zur Laufzeit den Bind-Port trifft.
    t = install.systemd_unit_text(
        root=Path("/srv/team"), uv="/usr/bin/uv", port=8780, user="mra",
    )
    assert "Environment=BIBI_DAEMON_PORT=8780" in t
    assert "--port 8780" in t


def test_launchd_plist_text():
    t = install.launchd_plist_text(
        root=Path("/Users/x/team"), uv="/opt/homebrew/bin/uv", port=9001,
        label="com.bibi.abcd1234", log_dir=Path("/Users/x/team/data"),
        role="synchronizer",
    )
    assert "<string>com.bibi.abcd1234</string>" in t
    assert "<string>bibi-ctrl</string><string>daemon</string><string>run</string>" in t
    assert "<string>9001</string>" in t
    assert "<key>BIBI_ROLE</key><string>synchronizer</string>" in t
    assert "<key>RunAtLoad</key><true/>" in t


def test_install_unsupported_platform(team_repo, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(install.sys, "platform", "sunos5")
    assert "unsupported" in install.install()
    assert "unsupported" in install.uninstall()
