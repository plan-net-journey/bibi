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
import threading
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


def _ctrl_prefix(root: Path) -> list[str]:
    """Womit ``bibi-ctrl`` gestartet wird — und warum das nicht egal ist.

    **Bevorzugt ``uv run --project <root>``** (m.rau/bibi#56). Der erste Entwurf
    rief das ``bibi-ctrl`` aus dem bin-Verzeichnis von ``sys.executable`` direkt
    auf. Die Absicht war richtig — eine Sitzung soll nicht die Engine eines
    fremden Repos starten — der Nebeneffekt war unbeabsichtigt und teuer: mit
    ``uv run`` fiel auch der **venv-Sync gegen die Lock** weg, auf den sich die
    Analyse ausdrücklich verlassen hatte (*„Der Versions-Abgleich passiert von
    selbst"*). Ein Sitzungsknoten lief damit mit dem venv, das gerade da war,
    und ein NEED UPDATE (#43) konnte sich nie von selbst auflösen.

    ``--project`` löst dasselbe Problem besser als der direkte Aufruf: der
    Daemon läuft die Engine, die **dieses Repo** pinnt — nicht die, die zufällig
    installiert ist. Beides zusammen, Identität und Aktualität.

    Ohne ``uv`` bleibt der direkte Weg als Notnagel; dann eben ohne Sync.
    """
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(root), "bibi-ctrl"]
    candidate = Path(sys.executable).parent / "bibi-ctrl"
    if candidate.exists():
        return [str(candidate)]
    return [shutil.which("bibi-ctrl") or "bibi-ctrl"]


def _venv_bin(root: Path) -> Path | None:
    """Das bin-Verzeichnis, in dem ``bibi-ctrl`` tatsächlich liegt.

    Zwei Kandidaten, in dieser Reihenfolge: das venv **dieses Repos** und das
    des laufenden Interpreters. Der erste gewinnt aus demselben Grund, aus dem
    :func:`_ctrl_prefix` ``--project`` setzt — eine Sitzung soll die Engine
    fahren, die dieses Repo pinnt, nicht die zufällig installierte.

    Geprüft wird auf die Existenz von ``bibi-ctrl``, nicht auf den Ordnernamen:
    ein Verzeichnis ohne die CLI in den PATH zu hängen, brächte nichts.
    """
    for cand in (root / ".venv" / "bin", Path(sys.executable).parent):
        if (cand / "bibi-ctrl").exists():
            return cand
    return None


def _child_env(root: Path) -> dict[str, str]:
    """Umgebung für alles, was diese Sitzung startet (m.rau/bibi#76).

    **Der Befund, der das nötig machte:** die drei Claude-Code-Hooks in
    ``.claude/settings.json`` rufen ``bibi-ctrl`` ohne Pfad auf. Das setzt eine
    aktivierte venv voraus — genau das, was vor ``bibi`` der Fall war, wenn ein
    Mensch Claude aus seiner Shell startete. Die Sitzung erbte ``VIRTUAL_ENV``,
    ließ ``PATH`` aber unangetastet; jeder Hook scheiterte mit
    ``bibi-ctrl: command not found``.

    Bitter daran war nicht der Fehler, sondern seine Folgenlosigkeit im Log:
    er ist laut, die **Wirkung** fehlt still — kein Pull beim Start, kein
    Auto-Push am Turn-Ende, keine Konflikt-Warnung, kein Turn-Logging. Ein
    Knoten mit ``auto_sync on`` glaubt, gesichert zu sein, und ist es nicht.

    Gelöst im Engine-Verb statt in den Hooks (Entscheidung m.rau, 2026-07-31):
    hier erreicht der Fix jedes Team-Repo über den Tag-Pin, während umgeschriebene
    Hooks in jedem einzeln nachgezogen werden müssten — und bei jedem Aufruf
    einen venv-Sync kosteten.

    Gilt für **beide** Kinder, Claude und den Daemon: ein Job, den der Daemon
    startet, ruft ebenso ``bibi-ctrl``.
    """
    env = dict(os.environ)
    bin_dir = _venv_bin(root)
    if bin_dir is None:
        return env
    # Vorn einhängen, Duplikate raus — ein zweiter Eintrag desselben Pfades
    # ändert nichts und macht ein `echo $PATH` nur unleserlich.
    parts, seen = [], set()
    for p in [str(bin_dir), *env.get("PATH", "").split(os.pathsep)]:
        if p and p not in seen:
            seen.add(p)
            parts.append(p)
    env["PATH"] = os.pathsep.join(parts)
    # Wie eine Aktivierung: wer das venv sucht, findet dasselbe wie im PATH.
    env["VIRTUAL_ENV"] = str(bin_dir.parent)
    return env


def _daemon_argv(args: argparse.Namespace, root: Path) -> list[str]:
    argv = [*_ctrl_prefix(root), "daemon", "run",
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
    env = _child_env(root)
    env["BIBI_ROLE"] = SESSION_ROLE  # s. SESSION_ROLE
    log_dir = root / "data" / "daemon-log"
    log_dir.mkdir(parents=True, exist_ok=True)
    out = (log_dir / "session.out.log").open("a", encoding="utf-8")
    return subprocess.Popen(_daemon_argv(args, root), cwd=root, env=env,
                            stdout=out, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)


#: Takt des Wächters. Deutlich enger als der Sitzungs-Zähler aus #46 — der darf
#: gemächlich sein, weil ein Daemon, der zu lange lebt, niemandem wehtut. Hier
#: ist es umgekehrt: solange niemand nachsieht, steht das Dashboard still.
WATCH_INTERVAL_S = 3.0


def _watch_daemon(root: Path, args: argparse.Namespace, stop) -> None:
    """Den selbst gestarteten Daemon am Leben halten (m.rau/bibi#55).

    ``/-/restart`` sagt seine Annahme selbst: *„Es gibt keinen Neustart-Befehl
    … der Supervisor bringt den Daemon nach drei Sekunden zurück."* Auf einem
    Sitzungsknoten gibt es keinen Supervisor — der Endpunkt war dort also keine
    Neustart-, sondern eine Abschalt-Taste. Betroffen war damit auch der frisch
    gebaute Update-Knopf aus #43, der genau dieses ``/-/restart`` postet.

    Die Sitzung übernimmt die Rolle deshalb selbst, aber **nur für den Daemon,
    den sie gestartet hat**. Wer sich nur angehängt hat, hat über fremde
    Prozesse nicht zu verfügen.

    Kein Konflikt mit dem Sitzungs-Zähler aus #46: solange diese Sitzung lebt,
    ist der Zähler nicht 0, der Daemon fährt also nicht von sich aus herunter.
    Der Wächter bringt nur zurück, was ein Neustart weggenommen hat.
    """
    while not stop.wait(WATCH_INTERVAL_S):
        if portfile.read() is not None:
            continue
        # Kein lebender Daemon mehr für dieses Repo — der Neustart hat den
        # Prozess beendet und niemanden hinterlassen, der ihn zurückbringt.
        print("bibi: Daemon ist weg — starte ihn neu.", flush=True)
        try:
            proc = _start_daemon(args, root)
        except Exception as exc:  # noqa: BLE001 — ein Wächter darf nie sterben
            print(f"bibi: Neustart fehlgeschlagen: {exc}", file=sys.stderr)
            continue
        port = None
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        while time.monotonic() < deadline and not stop.is_set():
            if proc.poll() is not None:
                break
            port = portfile.read_port()
            if port:
                break
            time.sleep(0.2)
        if port and _wait_healthy(port, proc=proc):
            print(f"bibi: Daemon wieder da (Port {port}).", flush=True)
        else:
            print("bibi: Daemon kam nicht zurück — siehe "
                  "data/daemon-log/session.out.log", file=sys.stderr)


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
    stop = threading.Event()
    watcher = None
    try:
        port, started = _ensure_daemon(args, root)
        if port is None:
            return 1
        if started:
            # Nur für den eigenen Daemon (m.rau/bibi#55) — und als Daemon-Thread,
            # damit ein hängender Wächter das Sitzungsende nie aufhält.
            watcher = threading.Thread(target=_watch_daemon,
                                       args=(root, args, stop), daemon=True)
            watcher.start()
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
        # env: damit die Claude-Code-Hooks `bibi-ctrl` finden (#76).
        return subprocess.call(_claude_argv(claude_args), cwd=root,
                               env=_child_env(root))
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError as exc:
        print(f"bibi: {exc}", file=sys.stderr)
        return 1
    finally:
        # Erst den Wächter anhalten, dann abmelden: umgekehrt könnte er den
        # Daemon noch einmal hochziehen, den der Zähler aus #46 gerade gehen
        # lassen will.
        stop.set()
        if watcher is not None:
            watcher.join(timeout=WATCH_INTERVAL_S + 2)
        session_registry.unregister()


def _ensure_daemon(args: argparse.Namespace, root: Path) -> tuple[int | None, bool]:
    """Anhängen, wenn schon einer läuft — sonst starten.

    Liefert ``(Port, selbst_gestartet)``. Die zweite Angabe entscheidet, ob
    diese Sitzung den Daemon bewachen darf (m.rau/bibi#55): über einen fremden
    Prozess hat sie nicht zu verfügen.

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
            return entry["port"], False
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
            return None, True
        if not _wait_healthy(port, proc=proc):
            print(f"bibi: Daemon antwortet nicht auf Port {port} — siehe "
                  "data/daemon-log/session.out.log", file=sys.stderr)
            return None, True
        print(f"bibi: Daemon gestartet (Port {port}).", flush=True)
        return port, True
    finally:
        _release(lock)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
