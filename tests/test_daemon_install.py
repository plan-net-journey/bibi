"""Install-Text-Renderer (systemd/launchd) + Dispatch (PLAN-2 §2.5)."""

from __future__ import annotations

import subprocess
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
    # PLAN-30 Ebene 1 v2 (Fund Review-Runde 2, 2026-07-15): anders als der
    # systemd-Pfad (Environment=BIBI_DAEMON_PORT=…) fehlte das hier bisher
    # komplett — ein Wrapper-Subprozess auf macOS hätte seinen Merge-back-
    # Trigger sonst blind gegen den Default-Port statt den echten Bind-Port
    # geschickt, sobald --port vom Default abweicht.
    assert "<key>BIBI_DAEMON_PORT</key><string>9001</string>" in t
    assert "<key>RunAtLoad</key><true/>" in t


def test_systemd_unit_text_with_connect_appends_flag():
    # PLAN-17 Stufe 17.0 Folgefund: --connect ist kein BIBI_ROLE-Mitglied (roles.py
    # KNOWN_ROLES), eine installierte Unit brauchte bisher keine Möglichkeit, den
    # Heartbeat-Modifikator zu setzen — ohne ihn bliebe ein installierter Client
    # ohne Heartbeat (dieselbe Lücke wie bei Worker, jetzt am Install-Pfad).
    t = install.systemd_unit_text(
        root=Path("/srv/team"), uv="/usr/bin/uv", port=8780, user="mra",
        role="synchronizer,controller", connect=True,
    )
    assert "ExecStart=/usr/bin/uv run bibi-ctrl daemon run --host 0.0.0.0 --port 8780 --connect" in t


def test_systemd_unit_text_without_connect_omits_flag():
    t = install.systemd_unit_text(
        root=Path("/srv/team"), uv="/usr/bin/uv", port=8780, user="mra", role="synchronizer",
    )
    assert "--connect" not in t


def test_systemd_unit_text_sets_killmode_process():
    # #40: ohne die Zeile gilt systemds Default `control-group` — beim Stoppen
    # bekommt jeder Prozess der Unit das Signal, also auch die detachten
    # Job-Wrapper, die einen Neustart überleben sollen. `start_new_session=True`
    # im Worker schützt nicht davor (eigene Session ≠ andere cgroup). Gefunden,
    # weil der sarasate-Host die Zeile von Hand trug und die per
    # `daemon install` geschriebene mustertest-Unit nicht: die Job-
    # Überlebensfähigkeit war am Host gemessen, nicht am Installer-Ergebnis.
    t = install.systemd_unit_text(
        root=Path("/srv/team"), uv="/usr/bin/uv", port=8780, user="mra",
    )
    assert "KillMode=process" in t


def test_launchd_plist_text_has_no_killmode():
    # Gegenstück: launchd kennt kein cgroup-Äquivalent und signalisiert nur den
    # Job-Prozess — dort trägt `start_new_session=True` allein. Eine
    # KillMode-Entsprechung gibt es nicht und darf nicht erfunden werden.
    t = install.launchd_plist_text(
        root=Path("/Users/x/team"), uv="/opt/homebrew/bin/uv", port=9001,
        label="com.bibi.abcd1234", log_dir=Path("/Users/x/team/data"),
    )
    assert "KillMode" not in t


def test_launchd_plist_text_with_connect_appends_flag():
    t = install.launchd_plist_text(
        root=Path("/Users/x/team"), uv="/opt/homebrew/bin/uv", port=9001,
        label="com.bibi.abcd1234", log_dir=Path("/Users/x/team/data"),
        role="synchronizer,controller", connect=True,
    )
    assert "<string>--connect</string>" in t


def test_launchd_plist_text_without_connect_omits_flag():
    t = install.launchd_plist_text(
        root=Path("/Users/x/team"), uv="/opt/homebrew/bin/uv", port=9001,
        label="com.bibi.abcd1234", log_dir=Path("/Users/x/team/data"), role="synchronizer",
    )
    assert "--connect" not in t


def test_install_unsupported_platform(team_repo, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(install.sys, "platform", "sunos5")
    assert "unsupported" in install.install()
    assert "unsupported" in install.uninstall()


# ── install() Linux-Dispatch: echte Seiteneffekte gemockt (Nebenbefund PLAN-33 33.4) ─

#: echtes subprocess.run, VOR jedem Monkeypatch gesichert -- install.subprocess IST
#: dasselbe Modulobjekt wie ueberall sonst importiert (z. B. bibi/repo.py fuer den
#: eigenen "git rev-parse"-Aufruf in repo.root(), den install() zu Beginn ausloest) --
#: ein Mock auf install.subprocess.run faengt also auch diesen unbeteiligten Aufruf ab,
#: nicht nur die sudo/systemctl-Kommandos, die dieser Test eigentlich beobachten will.
_REAL_RUN = subprocess.run


def _mock_linux(monkeypatch: pytest.MonkeyPatch, *, has_systemctl: bool = True) -> None:
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install, "_nonsnap_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(install.shutil, "which",
                        lambda name: "/usr/bin/systemctl" if (has_systemctl and name == "systemctl") else None)


def test_install_linux_without_systemctl_fails_clearly(team_repo, monkeypatch: pytest.MonkeyPatch):
    _mock_linux(monkeypatch, has_systemctl=False)

    def _run(cmd, **kw):
        if cmd and cmd[0] == "sudo":
            raise AssertionError("sudo/systemctl darf ohne systemctl gar nicht erst versucht werden")
        return _REAL_RUN(cmd, **kw)
    monkeypatch.setattr(install.subprocess, "run", _run)

    result = install.install()
    assert "FAILED" in result
    assert "systemctl" in result
    assert "installed" not in result


def test_install_linux_daemon_reload_failure_is_reported(team_repo, monkeypatch: pytest.MonkeyPatch):
    _mock_linux(monkeypatch)
    calls = []

    def _run(cmd, **kw):
        if not (cmd and cmd[0] == "sudo"):
            return _REAL_RUN(cmd, **kw)
        calls.append(cmd)
        if cmd[:2] == ["sudo", "tee"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1:3] == ["systemctl", "daemon-reload"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Failed to reload daemon")
        raise AssertionError(f"unerwarteter sudo-Aufruf: {cmd}")
    monkeypatch.setattr(install.subprocess, "run", _run)

    result = install.install()
    assert "FAILED" in result and "daemon-reload" in result
    assert "Failed to reload daemon" in result
    assert "installed" not in result
    # enable --now darf nach einem gescheiterten daemon-reload nicht mehr laufen.
    assert not any(c[1:3] == ["systemctl", "enable"] for c in calls)


def test_install_linux_enable_failure_is_reported(team_repo, monkeypatch: pytest.MonkeyPatch):
    _mock_linux(monkeypatch)

    def _run(cmd, **kw):
        if not (cmd and cmd[0] == "sudo"):
            return _REAL_RUN(cmd, **kw)
        if cmd[:2] == ["sudo", "tee"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1:3] == ["systemctl", "daemon-reload"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1:3] == ["systemctl", "enable"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Access denied")
        raise AssertionError(f"unerwarteter sudo-Aufruf: {cmd}")
    monkeypatch.setattr(install.subprocess, "run", _run)

    result = install.install()
    assert "FAILED" in result and "enable" in result
    assert "Access denied" in result
    assert "installed" not in result


def test_install_linux_full_success(team_repo, monkeypatch: pytest.MonkeyPatch):
    _mock_linux(monkeypatch)

    def _run(cmd, **kw):
        if not (cmd and cmd[0] == "sudo"):
            return _REAL_RUN(cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(install.subprocess, "run", _run)

    result = install.install(role="synchronizer,controller", connect=True)
    assert result.startswith("installed (systemd): ")
