"""Autostart-Install: macOS launchd, Linux systemd (DESIGN §4.10, PLAN-2 §2.5).

Portiert aus dem bibi-v3-Daemon. ``ExecStart`` ruft ``bibi-ctrl daemon run``
(nicht uvicorn direkt) — der Entrypoint baut die App aus den aufgelösten Rollen.
Rollen kommen aus ``~/.config/bibi/env`` (``BIBI_ROLE``); ``HOME`` ist in der
Unit gesetzt, damit ``config.env_path()`` auflöst.

Die Text-Renderer (``systemd_unit_text``/``launchd_plist_text``) sind rein und
ohne Seiteneffekt — testbar ohne das laufende System anzufassen.
"""

from __future__ import annotations

import getpass
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from bibi import config, repo

SYSTEMD_UNIT = "bibi-daemon.service"
SYSTEMD_UNIT_PATH = Path("/etc/systemd/system") / SYSTEMD_UNIT


# ── gemeinsame Helfer ───────────────────────────────────────────────────────

def _label(root: Path) -> str:
    h = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return f"com.bibi.{h}"


def _nonsnap_uv() -> str:
    """uv auflösen, Snap-Binary meiden (zerlegt eine systemd-Unit — „Snap-Falle"
    aus dem sarasate-Runbook). astral-Standalone in ~/.local/bin bevorzugen."""
    candidate = shutil.which("uv")
    if candidate and "/snap/" not in candidate:
        return candidate
    local = Path.home() / ".local" / "bin" / "uv"
    if local.exists():
        return str(local)
    if candidate:
        return candidate
    raise RuntimeError("uv nicht auf PATH (und ~/.local/bin/uv fehlt)")


def _exec_args(uv: str, host: str, port: int) -> list[str]:
    return [uv, "run", "bibi-ctrl", "daemon", "run", "--host", host, "--port", str(port)]


def _log_dir(root: Path) -> Path:
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── reine Text-Renderer ─────────────────────────────────────────────────────

def systemd_unit_text(*, root: Path, uv: str, port: int, user: str,
                      role: str | None = None) -> str:
    uv_dir = str(Path(uv).parent)
    path = f"{uv_dir}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    lines = [
        "[Unit]",
        "Description=bibi daemon (Synchronizer/Scheduler/Worker)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"WorkingDirectory={root}",
        f"Environment=HOME={Path.home()}",
        f"Environment=PATH={path}",
    ]
    if role:
        lines.append(f"Environment=BIBI_ROLE={role}")
    lines += [
        "ExecStart=" + " ".join(_exec_args(uv, "0.0.0.0", port)),
        "Restart=always",
        "RestartSec=3",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def launchd_plist_text(*, root: Path, uv: str, port: int, label: str,
                       log_dir: Path, role: str | None = None) -> str:
    extra = [
        "/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin",
        str(Path.home() / ".local" / "bin"), str(Path.home() / ".cargo" / "bin"),
    ]
    path = ":".join([*extra, "/usr/bin:/bin:/usr/sbin:/sbin"])
    env = [
        "  <key>EnvironmentVariables</key>",
        "  <dict>",
        f"    <key>PATH</key><string>{path}</string>",
        f"    <key>HOME</key><string>{Path.home()}</string>",
    ]
    if role:
        env.append(f"    <key>BIBI_ROLE</key><string>{role}</string>")
    env.append("  </dict>")
    args = "".join(f"<string>{a}</string>" for a in _exec_args(uv, "127.0.0.1", port))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>{args}</array>
  <key>WorkingDirectory</key><string>{root}</string>
{chr(10).join(env)}
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log_dir}/daemon.out.log</string>
  <key>StandardErrorPath</key><string>{log_dir}/daemon.err.log</string>
</dict>
</plist>
"""


# ── Install/Uninstall (Seiteneffekte) ───────────────────────────────────────

def _plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def install(role: str | None = None) -> str:
    root = repo.root()
    port = config.daemon_port()
    uv = _nonsnap_uv()
    if sys.platform == "darwin":
        label = _label(root)
        plist = _plist_path(label)
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(launchd_plist_text(
            root=root, uv=uv, port=port, label=label, log_dir=_log_dir(root), role=role))
        subprocess.run(["launchctl", "load", str(plist)], check=False)
        return f"installed (launchd): {plist}"
    if sys.platform.startswith("linux"):
        text = systemd_unit_text(root=root, uv=uv, port=port,
                                 user=getpass.getuser(), role=role)
        w = subprocess.run(["sudo", "tee", str(SYSTEMD_UNIT_PATH)],
                           input=text, capture_output=True, text=True)
        if w.returncode != 0:
            return f"install FAILED: {w.stderr.strip() or 'sudo/permission'}"
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
        subprocess.run(["sudo", "systemctl", "enable", "--now", SYSTEMD_UNIT], check=False)
        return f"installed (systemd): {SYSTEMD_UNIT_PATH}"
    return f"unsupported platform: {sys.platform}"


def uninstall() -> str:
    if sys.platform == "darwin":
        plist = _plist_path(_label(repo.root()))
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
            plist.unlink()
            return f"uninstalled (launchd): {plist}"
        return "not installed"
    if sys.platform.startswith("linux"):
        if not SYSTEMD_UNIT_PATH.exists():
            return "not installed"
        subprocess.run(["sudo", "systemctl", "disable", "--now", SYSTEMD_UNIT], check=False)
        subprocess.run(["sudo", "rm", "-f", str(SYSTEMD_UNIT_PATH)], check=False)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
        return f"uninstalled (systemd): {SYSTEMD_UNIT_PATH}"
    return f"unsupported platform: {sys.platform}"
