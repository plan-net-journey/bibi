"""``bibi`` — eine Arbeitssitzung aufmachen (m.rau/bibi#48).

Daemon hochziehen oder an einen laufenden anhängen, Stand aktualisieren,
Oberfläche öffnen, interaktives Claude Code starten — und beim Beenden
aufräumen. Der Befehl macht aus bibi eine **Installation** ein **Werkzeug**.

**Der tragende Grund kam bei der Analyse dazu: LHG startet hostlos.** Das eigene
Dashboard braucht einen laufenden Daemon, und der einzige Weg dahin war bisher
``daemon install``, also ein Autostart-Dienst. Für ein neues Teammitglied am
ersten Tag ist ein permanenter Hintergrunddienst auf dem eigenen Rechner eine
Hürde; für den nicht-technischen Rollenanteil wäre er undenkbar.

**Warum als Engine-Verb und nicht als Shell-Skript im Team-Repo:** der Ablauf
braucht Engine-Wissen — Port-Auflösung, Rollenzusammenstellung, Warten auf
``/-/health``, Pull, sauberes Beenden. Als Skript im Blueprint würde er all das
duplizieren und mit jeder Engine-Änderung driften. So erbt jedes Team-Repo den
Befehl über seinen Tag-Pin, ohne dass im Blueprint etwas nachgezogen wird.

``bibi`` startet, ``bibi-ctrl`` steuert — daher der Name (``[project.scripts]``
deklarierte bisher nur ``bibi-ctrl``, ``bibi`` war frei). ``/bibi-setup`` wird
**nicht** ersetzt: das bleibt das einmalige Onboarding (Config, git-Identität,
Freigabe am Host). Was entfällt, ist allein ``daemon install``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from bibi import config, repo
from bibi.daemon import portfile, session_registry

#: Wie lange auf den Fetch beim Start gewartet wird, wenn der Nutzer nichts
#: anderes gesagt hat. Kürzer als ``git_ops``' Default von 12 s — hier sitzt ein
#: Mensch davor, der arbeiten will, und ein unerreichbares Origin darf ihn nicht
#: aufhalten (so von m.rau entschieden: *„Eher sogar das Timeout verkürzen, und
#: dann claude starten."*).
PULL_TIMEOUT_S = 6.0

#: Wie lange auf ``/-/health`` gewartet wird, bevor der Start als gescheitert
#: gilt. Großzügig: ein frisch gesyncter ``uv``-Lauf braucht beim ersten Mal
#: spürbar länger als beim zweiten.
HEALTH_TIMEOUT_S = 45.0

#: Rollenprofil einer Sitzung. Buchstäblich das, was Mac und sarasate-Client
#: heute fahren. **Explizit gesetzt statt aus ``BIBI_ROLE`` geerbt:** ein Knoten,
#: dessen Config die ``worker``-Rolle trägt, bekäme sonst eine flüchtige Sitzung,
#: die Jobs annimmt und beim Beenden fallen lässt. Wer das will, sagt es mit
#: ``--worker``.
SESSION_ROLE = "synchronizer,controller"


def _team_repo() -> Path | None:
    """Das Team-Repo, in dem dieser Aufruf steht — oder ``None``.

    Ein Daemon gegen ein fremdes Verzeichnis zu starten wäre der schlechtere
    Fehler als ein klarer Abbruch: er liefe an, würde in ein ``data/`` schreiben,
    das dort niemand erwartet, und der Nutzer sähe ein leeres Dashboard ohne zu
    wissen warum.
    """
    root = repo.root_or_none()
    if root is None or not (root / "vault").is_dir():
        return None
    return root


def _pull(root: Path, *, quiet: bool = False) -> None:
    """Origin integrieren — **darf scheitern**.

    Der 409-Weg des Restart-Endpunkts wäre hier die falsche Härte: bei einem
    unbeaufsichtigten Deploy ist Abbrechen richtig, bei einem Menschen, der
    arbeiten will, nicht. Schlägt der Pull fehl, gibt es eine sichtbare Warnung
    und die Sitzung startet trotzdem — was der Nutzer dann sieht, ist ein Knoten
    auf altem Stand, und genau dafür ist die NEED-UPDATE-Anzeige aus #43 da.

    ``guard_live_paths=True``: uncommittete Arbeit im Working Tree hat Vorrang
    vor Aktualität. Wer eine Sitzung öffnet, hat oft genau deshalb aufgehört.
    """
    from bibi import git_ops
    # Der Timeout wird am Modul gesetzt, nicht per Parameter: ``integrate()``
    # reicht keinen durch, der Fetch liest ``GIT_NET_TIMEOUT`` beim Aufruf. Eine
    # ausdrückliche Angabe des Nutzers bleibt unangetastet.
    if not os.environ.get("BIBI_GIT_NET_TIMEOUT", "").strip():
        git_ops.GIT_NET_TIMEOUT = PULL_TIMEOUT_S
    branch = git_ops.current_branch() or "trunk"
    try:
        ok, kind = git_ops.integrate(branch, guard_live_paths=True)
    except Exception as exc:  # noqa: BLE001 — Netz weg, git kaputt, egal
        ok, kind = False, str(exc)
    if ok:
        if not quiet:
            print(f"bibi: {branch} auf Stand.", flush=True)
        return
    print(f"bibi: Pull übersprungen ({kind}) — Sitzung startet auf dem "
          f"lokalen Stand.", file=sys.stderr)


def _ctrl_binary() -> str:
    """``bibi-ctrl`` **derselben** Installation, aus der dieser Prozess läuft.

    Über das bin-Verzeichnis von ``sys.executable``, nicht über den PATH: sonst
    könnte eine Sitzung aus Repo A den Daemon mit der Engine aus Repo B starten
    — die beiden pinnen über ihre ``uv.lock`` unterschiedliche Stände, und der
    Fehler wäre praktisch unauffindbar. Der PATH bleibt der Notnagel.
    """
    candidate = Path(sys.executable).parent / "bibi-ctrl"
    if candidate.exists():
        return str(candidate)
    return shutil.which("bibi-ctrl") or "bibi-ctrl"


def _daemon_argv(args: argparse.Namespace) -> list[str]:
    argv = [_ctrl_binary(), "daemon", "run",
            "--host", "127.0.0.1",
            # Kein fester Port: der Nutzer soll beim Start „so gut wie nix zu
            # tun" haben, und zwei Repos auf einer Maschine müssen sich nicht
            # absprechen (m.rau/bibi#45).
            "--port", "auto",
            # Diese Sitzung darf den Daemon mit der letzten beenden (#46).
            "--session",
            "--synchronizer", "--controller"]
    if args.worker:
        argv.append("--worker")
    if _host_configured():
        argv.append("--connect")
    return argv


def _host_configured() -> bool:
    return bool((os.environ.get("BIBI_SCHEDULER_URL", "").strip()
                 or config.read_env().get("BIBI_SCHEDULER_URL", "").strip()))


def _start_daemon(args: argparse.Namespace, root: Path) -> subprocess.Popen:
    """Den Daemon im Hintergrund starten.

    ``start_new_session=True``: er soll das Terminal überleben, in dem diese
    Sitzung läuft — eine zweite Sitzung hängt sich an denselben Daemon, und ein
    ``CTRL+C`` in Fenster A darf ihr nicht den Boden wegziehen. Wann er endet,
    entscheidet der Sitzungs-Zähler aus #46, nicht die Prozessgruppe.
    """
    env = dict(os.environ)
    env["BIBI_ROLE"] = SESSION_ROLE  # s. SESSION_ROLE
    log_dir = root / "data" / "daemon-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    out = (log_dir / "session.out.log").open("a", encoding="utf-8")
    return subprocess.Popen(_daemon_argv(args), cwd=root, env=env,
                            stdout=out, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)


def _wait_healthy(port: int, *, timeout: float = HEALTH_TIMEOUT_S,
                  proc: subprocess.Popen | None = None) -> bool:
    """Auf ``/-/health`` warten.

    Beendet sich der gestartete Prozess vorher, wird nicht weitergewartet — die
    volle Frist gegen einen bereits toten Daemon abzusitzen wäre nur Wartezeit
    ohne Erkenntnis.
    """
    url = f"http://127.0.0.1:{port}/-/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def _acquire_start_lock(root: Path):
    """Exklusives Recht, den Daemon zu starten — oder ``None``.

    Zwei gleichzeitig geöffnete Terminals sind kein Randfall, und ohne diese
    Sperre sähen beide „kein Daemon da" und startete jedes einen. Genau zwei
    Daemons auf einem Repo verhindert #46 — der ``sync_lock`` ist prozess-lokal,
    zwei Synchronizer würden gleichzeitig ins selbe Verzeichnis pullen und
    pushen.

    ``flock`` statt einer selbstgebauten Lock-Datei: der Kernel gibt es beim
    Prozessende von selbst frei, es gibt also keine verwaiste Sperre nach einem
    ``kill -9``.
    """
    import fcntl
    path = root / "data" / "session-start.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        fh.close()
        return None
    return fh


def _release(fh) -> None:
    if fh is None:
        return
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _claude_argv(extra: list[str]) -> list[str]:
    binary = (os.environ.get("BIBI_CLAUDE_BIN", "").strip()
              or config.read_env().get("BIBI_CLAUDE_BIN", "").strip()
              or "claude")
    return [binary, *extra]


def _install_exit_handlers() -> None:
    """``SIGTERM``/``SIGHUP`` in ein reguläres Ende übersetzen.

    Damit läuft das ``finally`` in :func:`main` auch dann, wenn jemand den
    Prozess killt oder das Terminalfenster zuklappt (das schickt ``SIGHUP``).
    ``SIGINT`` bleibt bewusst außen vor: das bekommt Claude Code als
    Vordergrundprozess und behandelt es selbst.

    Nicht abfangbar bleiben ``SIGKILL`` und der harte Rechnerausfall — dafür
    greifen die PID-Prüfung der Sitzungs-Registry (#46) und die
    60-Sekunden-Stale-Erkennung am Host. Kein Mechanismus allein ist die
    Antwort, beide zusammen sind es.
    """
    def _bye(signum, _frame):
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _bye)
        except (ValueError, OSError):
            pass


def _parse(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(
        prog="bibi",
        description="Eine bibi-Arbeitssitzung öffnen: Daemon, Oberfläche, "
                    "Claude Code — und beim Beenden aufräumen.",
        epilog="Unbekannte Argumente werden an claude durchgereicht.")
    p.add_argument("--no-browser", action="store_true",
                   help="die Oberfläche nicht im Browser öffnen "
                        "(Container, reine Terminal-Sitzung)")
    p.add_argument("--no-pull", action="store_true",
                   help="den Stand nicht aktualisieren")
    p.add_argument("--no-claude", action="store_true",
                   help="nur Daemon und Oberfläche, kein Claude Code — die "
                        "Sitzung hält, bis CTRL+C kommt")
    p.add_argument("--worker", action="store_true",
                   help="Jobs annehmen (Default: nein — eine flüchtige Sitzung "
                        "ließe zugeteilte Jobs beim Beenden fallen)")
    return p.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, claude_args = _parse(argv)

    root = _team_repo()
    if root is None:
        print("bibi: hier ist kein bibi-Team-Repo (kein git-Repo mit vault/).\n"
              "      In das Team-Repo wechseln und erneut starten.",
              file=sys.stderr)
        return 2

    if not args.no_pull:
        _pull(root)

    _install_exit_handlers()
    # Vor dem Daemon-Start anmelden: so sieht er nie eine Null, die in
    # Wirklichkeit „die erste ist noch nicht da" heißt.
    session_registry.register(label=f"pid {os.getpid()}")
    try:
        port = _ensure_daemon(args, root)
        if port is None:
            return 1
        url = f"http://127.0.0.1:{port}/-/"
        print(f"bibi: Oberfläche auf {url}", flush=True)
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 — headless, kein Browser, egal
                pass
        if args.no_claude:
            print("bibi: Sitzung offen — CTRL+C beendet sie.", flush=True)
            try:
                signal.pause()
            except (KeyboardInterrupt, AttributeError):
                pass
            return 0
        return subprocess.call(_claude_argv(claude_args), cwd=root)
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError as exc:
        print(f"bibi: {exc}", file=sys.stderr)
        return 1
    finally:
        session_registry.unregister()


def _ensure_daemon(args: argparse.Namespace, root: Path) -> int | None:
    """Anhängen, wenn schon einer läuft — sonst starten. Liefert den Port.

    Die Prüfung und der Start liegen zusammen unter der Sperre; getrennt wäre
    genau das Fenster offen, in dem zwei gleichzeitig gestartete Sitzungen
    beide „kein Daemon da" lesen.
    """
    lock = _acquire_start_lock(root)
    try:
        entry = portfile.read()
        if entry is not None:
            print(f"bibi: an laufenden Daemon angehängt (Port {entry['port']}).",
                  flush=True)
            return entry["port"]
        proc = _start_daemon(args, root)
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        port = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            port = portfile.read_port()
            if port:
                break
            time.sleep(0.2)
        if not port:
            print("bibi: Daemon ist nicht hochgekommen — siehe "
                  "data/daemon-log/session.out.log", file=sys.stderr)
            return None
        if not _wait_healthy(port, proc=proc):
            print(f"bibi: Daemon antwortet nicht auf Port {port} — siehe "
                  "data/daemon-log/session.out.log", file=sys.stderr)
            return None
        print(f"bibi: Daemon gestartet (Port {port}).", flush=True)
        return port
    finally:
        _release(lock)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
