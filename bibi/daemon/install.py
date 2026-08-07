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

SYSTEMD_DIR = Path("/etc/systemd/system")

#: Wie lange der Supervisor auf ein sauberes Ende warten soll, bevor er SIGKILL
#: schickt (m.rau/bibi#49). Muss über uvicorns Verbindungsfrist **plus** der
#: Drain-Frist liegen — sonst schneidet der Supervisor genau das ab, wofür der
#: Drain da ist. Mit den Defaults (10 s + 10 s) ist 90 s reichlich Luft, auch
#: für ein per ``BIBI_DRAIN_TIMEOUT_S`` hochgesetztes Budget.
STOP_TIMEOUT_S = 90


def _systemd_unit_name(root: Path) -> str:
    """Unit-Name **pro Repo eindeutig** (mehrere bibi-Instanzen je Host, §4.10).

    Aus dem Repo-Ordnernamen abgeleitet (analog zum gehashten launchd-Label),
    z. B. ``…/bibi-notes`` → ``bibi-notes-daemon.service``. Verhindert, dass eine
    zweite Installation die Unit einer anderen Instanz überschreibt (früher fix
    ``bibi-daemon.service``). Annahme: Repo-Basisname je Host eindeutig — wie für
    systemd-Unit-Namen ohnehin nötig.
    """
    return f"{root.name}-daemon.service"


def _systemd_unit_path(root: Path) -> Path:
    return SYSTEMD_DIR / _systemd_unit_name(root)


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


def _exec_args(uv: str, host: str, port: int, *, connect: bool = False) -> list[str]:
    args = [uv, "run", "bibi-ctrl", "daemon", "run", "--host", host, "--port", str(port)]
    if connect:
        # --connect ist ein reiner CLI-Modifikator, kein KNOWN_ROLES-Mitglied
        # (roles.py) — BIBI_ROLE allein kann ihn nicht tragen. Ohne dieses Flag
        # hier bliebe eine installierte Client-Unit ohne Heartbeat (A12), genau
        # die Lücke, die PLAN-17 Stufe 17.0 bei Worker aufgedeckt hat.
        args.append("--connect")
    return args


def _log_dir(root: Path) -> Path:
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── reine Text-Renderer ─────────────────────────────────────────────────────

def systemd_unit_text(*, root: Path, uv: str, port: int, user: str,
                      role: str | None = None, connect: bool = False) -> str:
    uv_dir = str(Path(uv).parent)
    path = f"{uv_dir}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    lines = [
        "[Unit]",
        f"Description=bibi daemon ({root.name}) — port {port}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"WorkingDirectory={root}",
        f"Environment=HOME={Path.home()}",
        f"Environment=PATH={path}",
        f"Environment=BIBI_DAEMON_PORT={port}",
    ]
    if role:
        lines.append(f"Environment=BIBI_ROLE={role}")
    lines += [
        "ExecStart=" + " ".join(_exec_args(uv, "0.0.0.0", port, connect=connect)),
        # KillMode=process: beim Stoppen NUR den Daemon selbst signalisieren,
        # nicht die ganze cgroup. Ohne diese Zeile gilt systemds Default
        # `control-group` — jeder Prozess der Unit bekommt das Signal, also auch
        # die detachten Job-Wrapper, die einen Neustart überleben sollen.
        # `start_new_session=True` (worker.py) schützt davor NICHT: es setzt den
        # Wrapper in eigene Session und Prozessgruppe, was gegen
        # POSIX-Signalweitergabe reicht — die cgroup verlässt er dadurch nicht.
        #
        # Der Fehlte-Fund (m.rau/bibi#40, 2026-07-29): der sarasate-Host trug
        # die Zeile von Hand nachgepflegt, die per `daemon install` geschriebene
        # mustertest-Unit nicht. Die als „live gemessen" geltende
        # Job-Überlebensfähigkeit war also am Host gemessen, ein frisch
        # onboardeter Knoten verlor bei jedem Neustart alle laufenden Jobs.
        # Damit hing die Wirksamkeit des Job-Drains (#38) und des Restart-
        # Endpunkts (#39) an einer Zeile, die der Installer nie schrieb.
        # macOS ist nicht betroffen: launchd kennt kein cgroup-Äquivalent und
        # signalisiert nur den Job-Prozess, dort trägt start_new_session allein.
        "KillMode=process",
        # Ausdrücklich statt implizit (m.rau/bibi#49): seit dem Drain im
        # `lifespan`-Finally braucht ein sauberes Ende messbar Zeit — uvicorns
        # Frist für offene Verbindungen (Default 10 s) plus die Drain-Frist
        # (Default 10 s). systemds Default wäre mit 90 s zwar großzügig genug,
        # nur steht er nirgends in der Unit und niemand sieht ihn beim
        # Nachrechnen. Hier steht er.
        f"TimeoutStopSec={STOP_TIMEOUT_S}",
        "Restart=always",
        "RestartSec=3",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def launchd_plist_text(*, root: Path, uv: str, port: int, label: str,
                       log_dir: Path, role: str | None = None, connect: bool = False) -> str:
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
        f"    <key>BIBI_DAEMON_PORT</key><string>{port}</string>",
    ]
    if role:
        env.append(f"    <key>BIBI_ROLE</key><string>{role}</string>")
    env.append("  </dict>")
    args = "".join(f"<string>{a}</string>" for a in _exec_args(uv, "127.0.0.1", port, connect=connect))
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
  <!-- m.rau/bibi#49: launchd killt nach ExitTimeOut, und dessen Default sind
       20 Sekunden — knapp genug, dass uvicorns Verbindungsfrist plus der
       Job-Drain hineinlaufen und der Prozess mitten im Drain ein SIGKILL
       bekommt, also genau den unkontrollierten Abbruch, den der Drain
       verhindern soll. Anders als unter systemd ist das hier kein Kommentar,
       sondern eine notwendige Korrektur. -->
  <key>ExitTimeOut</key><integer>{STOP_TIMEOUT_S}</integer>
  <key>StandardOutPath</key><string>{log_dir}/daemon.out.log</string>
  <key>StandardErrorPath</key><string>{log_dir}/daemon.err.log</string>
</dict>
</plist>
"""


# ── Install/Uninstall (Seiteneffekte) ───────────────────────────────────────

def _plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def installed_unit(root: Path | None = None) -> str | None:
    """Name der installierten Autostart-Unit dieses Repos, sonst ``None``.

    Beantwortet „hat dieser Knoten einen Supervisor" durch Nachsehen statt durch
    Schließen (m.rau/bibi#55). Vorher leitete ``bibi-ctrl status`` die Antwort
    aus der Portdatei ab — ``session=False`` hieß dort „Unit". Das ist ein
    binärer Schalter für drei Fälle: Sitzung, Unit, und ein von Hand gestarteter
    Daemon, der keins von beidem ist und trotzdem als Unit gemeldet wurde.

    Warum das mehr als Kosmetik ist: diese Angabe ist der Weg, auf dem sich
    m.rau/bibi#180 nachprüfen lässt — bekommt ein Client einen Dienst? Wer dafür
    ``status`` liest statt ``launchctl``, bekam das Gegenteil der Wahrheit.

    Bewusst nur ein Blick ins Dateisystem, kein ``launchctl list``/``systemctl``:
    gefragt ist, ob eine Unit **installiert** ist, nicht ob sie gerade läuft —
    und ein Unterprozess je ``status``-Aufruf wäre für eine Zeile zu teuer.
    """
    root = root or repo.root_or_none()
    if root is None:
        return None
    if sys.platform == "darwin":
        label = _label(root)
        return label if _plist_path(label).exists() else None
    if sys.platform.startswith("linux"):
        return _systemd_unit_name(root) if _systemd_unit_path(root).exists() else None
    return None


def install(role: str | None = None, connect: bool = False,
            port: int | None = None) -> str:
    """Autostart-Unit schreiben.

    ``port`` (m.rau/bibi#15): explizit, sonst aus der **Konfiguration**. Beides
    ist neu und gehört zusammen.

    Das Flag, weil eine Maschine mehrere Instanzen tragen kann und jede einen
    eigenen, festen Port braucht (sarasate: Host 8780, Client 8781, Testknoten
    8782). Bisher war ``BIBI_DAEMON_PORT=… bibi-ctrl daemon install`` der einzige
    Weg dorthin — eine Umgebungsvariable als Pflichtargument getarnt.

    Und ``configured_daemon_port()`` statt ``daemon_port()``, weil die
    Port-Automatik aus #45 sonst hier hereinregierte: läuft während des
    ``install`` gerade ein Sitzungs-Daemon, lieferte ``daemon_port()`` dessen
    flüchtigen Port — und der stünde danach dauerhaft in der Unit. Eine Unit
    beschreibt, wo künftig gelauscht werden soll; die Portdatei sagt, wo gerade
    gelauscht wird. Das sind zwei Fragen.

    Damit ist #15 **nicht** von #45 miterledigt, anders als beim Planen
    vermutet: die Automatik löst den interaktiven Start, nicht den festen
    Dienst — der braucht eine Nummer, die auch morgen noch gilt.
    """
    root = repo.root()
    port = port or config.configured_daemon_port()
    uv = _nonsnap_uv()
    if sys.platform == "darwin":
        label = _label(root)
        plist = _plist_path(label)
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(launchd_plist_text(
            root=root, uv=uv, port=port, label=label, log_dir=_log_dir(root), role=role,
            connect=connect))
        subprocess.run(["launchctl", "load", str(plist)], check=False)
        return f"installed (launchd): {plist}"
    if sys.platform.startswith("linux"):
        # Nebenbefund PLAN-33 Stufe 33.4: vor diesem Check meldete install() auf
        # einem systemd-losen Host (z. B. einem Container, live gesehen: "sudo:
        # systemctl: command not found") trotzdem "installed (systemd): …" —
        # die beiden systemctl-Aufrufe unten liefen mit check=False, ihr
        # Rückgabewert wurde nie geprüft. Früh und eindeutig ablehnen statt
        # einen Fehlschlag als Erfolg zu melden.
        if shutil.which("systemctl") is None:
            return "install FAILED: systemctl nicht gefunden (kein systemd, z. B. in einem Container)"
        unit = _systemd_unit_name(root)
        unit_path = _systemd_unit_path(root)
        text = systemd_unit_text(root=root, uv=uv, port=port,
                                 user=getpass.getuser(), role=role, connect=connect)
        w = subprocess.run(["sudo", "tee", str(unit_path)],
                           input=text, capture_output=True, text=True)
        if w.returncode != 0:
            return f"install FAILED: {w.stderr.strip() or 'sudo/permission'}"
        reload_ = subprocess.run(["sudo", "systemctl", "daemon-reload"],
                                 capture_output=True, text=True)
        if reload_.returncode != 0:
            return f"install FAILED (daemon-reload): {reload_.stderr.strip() or 'sudo/permission'}"
        enable = subprocess.run(["sudo", "systemctl", "enable", "--now", unit],
                                capture_output=True, text=True)
        if enable.returncode != 0:
            return f"install FAILED (enable --now): {enable.stderr.strip() or 'sudo/permission'}"
        return f"installed (systemd): {unit_path}"
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
        unit = _systemd_unit_name(repo.root())
        unit_path = _systemd_unit_path(repo.root())
        if not unit_path.exists():
            return "not installed"
        subprocess.run(["sudo", "systemctl", "disable", "--now", unit], check=False)
        subprocess.run(["sudo", "rm", "-f", str(unit_path)], check=False)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
        return f"uninstalled (systemd): {unit_path}"
    return f"unsupported platform: {sys.platform}"
