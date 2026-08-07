"""``bibi`` — der Sitzungsbefehl (m.rau/bibi#48).

Geprüft wird der Ablauf, nicht der echte Daemon: Repo-Prüfung, nicht-blockierender
Pull, Anhängen statt Verdoppeln, Rollenprofil, Aufräumen. Der Start eines echten
uvicorn ist in der ``--slow``-Suite besser aufgehoben.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from bibi import session
from bibi.daemon import portfile, session_registry


@pytest.fixture()
def no_side_effects(monkeypatch: pytest.MonkeyPatch):
    """Kein Browser, kein Claude, kein echter Daemon."""
    opened: list[str] = []
    claude: list[list[str]] = []
    monkeypatch.setattr(session.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setattr(session.subprocess, "call",
                        lambda argv, **kw: (claude.append(argv), 0)[1])
    monkeypatch.setattr(session, "_pull", lambda root, **kw: None)
    return {"opened": opened, "claude": claude}


class _FakeProc:
    """Ein „Daemon", der beim Start seine Portdatei schreibt — genau das, was
    der echte tut, sobald er seinen Port kennt."""

    def __init__(self, port: int = 54321, dies: bool = False) -> None:
        self._dies = dies
        if not dies:
            portfile.write(port)

    def poll(self):
        return 1 if self._dies else None


# ── Repo-Prüfung ────────────────────────────────────────────────────────────


def test_aborts_outside_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert session.main([]) == 2
    assert "kein bibi-Team-Repo" in capsys.readouterr().err


def test_aborts_in_a_git_repo_without_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Ein git-Repo allein macht noch kein Team-Repo. Einen Daemon gegen ein
    # fremdes Verzeichnis zu starten wäre der schlechtere Fehler: er liefe an
    # und schriebe in ein data/, das dort niemand erwartet.
    root = tmp_path / "fremd"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.chdir(root)
    assert session.main([]) == 2


def test_accepts_a_team_repo(team_repo: Path):
    assert session._team_repo() == team_repo


# ── Anhängen statt Verdoppeln ───────────────────────────────────────────────


def test_attaches_to_a_running_daemon(team_repo: Path, no_side_effects,
                                      monkeypatch: pytest.MonkeyPatch, capsys):
    portfile.write(54321)
    started: list = []
    monkeypatch.setattr(session, "_start_daemon",
                        lambda a, r: started.append(1))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)

    assert session.main([]) == 0
    assert started == []                       # kein zweiter Daemon
    assert "angehängt" in capsys.readouterr().out
    assert no_side_effects["opened"] == ["http://127.0.0.1:54321/-/"]


def test_starts_a_daemon_when_none_runs(team_repo: Path, no_side_effects,
                                        monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)

    assert session.main([]) == 0
    assert no_side_effects["opened"] == ["http://127.0.0.1:54321/-/"]


def test_reports_a_daemon_that_never_comes_up(team_repo: Path, no_side_effects,
                                              monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(dies=True))
    assert session.main([]) == 1
    assert "nicht hochgekommen" in capsys.readouterr().err


def test_reports_a_daemon_that_does_not_answer(team_repo: Path, no_side_effects,
                                               monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: False)
    assert session.main([]) == 1
    assert "antwortet nicht" in capsys.readouterr().err


# ── Die Sitzungs-Registry ───────────────────────────────────────────────────


def test_registers_and_unregisters(team_repo: Path, no_side_effects,
                                   monkeypatch: pytest.MonkeyPatch):
    seen: list[int] = []

    def _claude(argv, **kw):
        seen.append(session_registry.count())
        return 0

    monkeypatch.setattr(session.subprocess, "call", _claude)
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)

    session.main([])
    assert seen == [1]                          # während der Sitzung angemeldet
    assert session_registry.count() == 0        # danach abgemeldet


def test_unregisters_even_when_claude_crashes(team_repo: Path, no_side_effects,
                                              monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(session.subprocess, "call",
                        lambda argv, **kw: (_ for _ in ()).throw(RuntimeError("peng")))
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)

    with pytest.raises(RuntimeError):
        session.main([])
    assert session_registry.count() == 0


def test_registers_before_the_daemon_starts(team_repo: Path, no_side_effects,
                                            monkeypatch: pytest.MonkeyPatch):
    # Sonst sähe der Daemon beim ersten Zählen eine Null, die in Wirklichkeit
    # „die erste Sitzung ist noch nicht da" heißt.
    seen: list[int] = []

    def _start(a, r):
        seen.append(session_registry.count())
        return _FakeProc(54321)

    monkeypatch.setattr(session, "_start_daemon", _start)
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)
    session.main([])
    assert seen == [1]


# ── Das Rollenprofil ────────────────────────────────────────────────────────


def _args(**kw):
    """Realistischer Namespace — **aus dem echten Parser**, nicht von Hand.

    Vorher stand hier eine handgepflegte Liste der Flags. Sie musste bei jedem
    neuen Flag nachgezogen werden, und ein vergessenes Nachziehen erschien als
    ``AttributeError`` mitten in einem Test, der mit dem neuen Flag gar nichts
    zu tun hat — beim Bau von ``--name`` (#60) genau so passiert. Der Parser
    ist die einzige Stelle, die alle Flags kennt; ihn zu fragen kostet nichts.
    """
    ns, _rest = session._parse([])
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_daemon_argv_is_the_session_profile(team_repo: Path,
                                            monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)
    argv = session._daemon_argv(_args(), team_repo)
    i = argv.index("daemon")
    assert argv[i:] == ["daemon", "run", "--host", "127.0.0.1",
                        "--port", "auto", "--session",
                        "--synchronizer", "--controller"]


def test_daemon_argv_connects_when_a_host_is_configured(
    team_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate:8780")
    assert "--connect" in session._daemon_argv(_args(), team_repo)


def test_daemon_argv_has_no_worker_by_default(team_repo: Path,
                                              monkeypatch: pytest.MonkeyPatch):
    # Eine flüchtige Sitzung, die Jobs annimmt, ließe sie beim Beenden fallen.
    # Job-Annahme hängt an der worker-Rolle, nicht an --connect.
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)
    assert "--worker" not in session._daemon_argv(_args(), team_repo)
    assert "--worker" in session._daemon_argv(_args(worker=True), team_repo)


def test_start_daemon_pins_the_role_explicitly(team_repo: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    # Aus BIBI_ROLE geerbt wäre falsch: ein Knoten, dessen Config die
    # worker-Rolle trägt, bekäme sonst eine Sitzung, die Jobs annimmt.
    seen: dict = {}

    def _popen(argv, **kw):
        seen.update(kw)
        return _FakeProc(54321)

    monkeypatch.setenv("BIBI_ROLE", "synchronizer,worker,scheduler")
    monkeypatch.setattr(session.subprocess, "Popen", _popen)
    session._start_daemon(_args(), team_repo)
    assert seen["env"]["BIBI_ROLE"] == session.SESSION_ROLE
    # Der Daemon überlebt das Terminal dieser Sitzung — eine zweite hängt sich
    # an denselben, und ein CTRL+C hier darf ihr nicht den Boden wegziehen.
    assert seen["start_new_session"] is True


# ── Der Pull darf scheitern ─────────────────────────────────────────────────


def test_pull_failure_does_not_stop_the_session(team_repo: Path,
                                                monkeypatch: pytest.MonkeyPatch, capsys):
    from bibi import git_ops
    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(git_ops, "integrate",
                        lambda branch, **kw: (False, "unreachable"))
    session._pull(team_repo)
    err = capsys.readouterr().err
    assert "übersprungen" in err and "unreachable" in err


def test_pull_exception_does_not_stop_the_session(team_repo: Path,
                                                  monkeypatch: pytest.MonkeyPatch, capsys):
    from bibi import git_ops
    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")

    def _boom(branch, **kw):
        raise OSError("git weg")

    monkeypatch.setattr(git_ops, "integrate", _boom)
    session._pull(team_repo)
    assert "übersprungen" in capsys.readouterr().err


def test_pull_shortens_the_network_timeout(team_repo: Path,
                                           monkeypatch: pytest.MonkeyPatch):
    # Hier sitzt ein Mensch davor, der arbeiten will — ein unerreichbares Origin
    # darf ihn nicht so lange aufhalten wie einen Hintergrund-Loop.
    from bibi import git_ops
    monkeypatch.delenv("BIBI_GIT_NET_TIMEOUT", raising=False)
    monkeypatch.setattr(git_ops, "GIT_NET_TIMEOUT", 12.0)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(git_ops, "integrate", lambda branch, **kw: (True, None))
    session._pull(team_repo)
    assert git_ops.GIT_NET_TIMEOUT == session.PULL_TIMEOUT_S


def test_pull_respects_an_explicit_timeout(team_repo: Path,
                                           monkeypatch: pytest.MonkeyPatch):
    from bibi import git_ops
    monkeypatch.setenv("BIBI_GIT_NET_TIMEOUT", "30")
    monkeypatch.setattr(git_ops, "GIT_NET_TIMEOUT", 30.0)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(git_ops, "integrate", lambda branch, **kw: (True, None))
    session._pull(team_repo)
    assert git_ops.GIT_NET_TIMEOUT == 30.0


def test_pull_protects_uncommitted_work(team_repo: Path,
                                        monkeypatch: pytest.MonkeyPatch):
    # Wer eine Sitzung öffnet, hat oft genau deshalb aufgehört — uncommittete
    # Arbeit hat Vorrang vor Aktualität.
    from bibi import git_ops
    seen: dict = {}
    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(git_ops, "integrate",
                        lambda branch, **kw: (seen.update(kw), (True, None))[1])
    session._pull(team_repo)
    assert seen["guard_live_paths"] is True


# ── Flags ───────────────────────────────────────────────────────────────────


def test_no_browser(team_repo: Path, no_side_effects, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)
    session.main(["--no-browser"])
    assert no_side_effects["opened"] == []


def test_unknown_args_go_to_claude(team_repo: Path, no_side_effects,
                                   monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)
    session.main(["--no-browser", "--model", "opus", "--resume"])
    assert no_side_effects["claude"][0][1:] == ["--model", "opus", "--resume"]


def test_claude_binary_is_configurable(team_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_CLAUDE_BIN", "/opt/claude/bin/claude")
    assert session._claude_argv([])[0] == "/opt/claude/bin/claude"


def test_ctrl_prefix_syncs_the_venv_against_the_lock(team_repo: Path,
                                                     monkeypatch: pytest.MonkeyPatch):
    """Über ``uv run --project``, nicht über das venv-Binary (m.rau/bibi#56).

    Der direkte Aufruf hatte den venv-Sync gegen die Lock mitgenommen — ein
    Sitzungsknoten lief damit mit dem venv, das gerade da war, und ein
    NEED UPDATE konnte sich nie von selbst auflösen.
    """
    monkeypatch.setattr(session.shutil, "which",
                        lambda n: "/opt/homebrew/bin/uv" if n == "uv" else None)
    assert session._ctrl_prefix(team_repo) == [
        "/opt/homebrew/bin/uv", "run", "--project", str(team_repo), "bibi-ctrl"]


def test_ctrl_prefix_falls_back_without_uv(team_repo: Path,
                                           monkeypatch: pytest.MonkeyPatch):
    # Ohne uv bleibt der direkte Weg — dann eben ohne Sync, statt gar nicht.
    monkeypatch.setattr(session.shutil, "which", lambda n: None)
    prefix = session._ctrl_prefix(team_repo)
    assert len(prefix) == 1
    assert Path(prefix[0]).name == "bibi-ctrl"


# ── Der Wächter über dem eigenen Daemon (m.rau/bibi#55) ─────────────────────


def test_watcher_restarts_a_daemon_that_went_away(team_repo: Path,
                                                  monkeypatch: pytest.MonkeyPatch):
    """Der Fall, den `/-/restart` auf einem Sitzungsknoten auslöst.

    Der Endpunkt beendet den Prozess und verlässt sich darauf, dass ein
    Supervisor ihn zurückbringt. Auf einem Sitzungsknoten gibt es keinen — er
    war dort also keine Neustart-, sondern eine Abschalt-Taste. Das traf auch
    den Update-Knopf aus #43.
    """
    import threading
    starts: list[int] = []

    def _start(a, r):
        starts.append(1)
        return _FakeProc(54321)          # schreibt die Portdatei

    monkeypatch.setattr(session, "_start_daemon", _start)
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)
    monkeypatch.setattr(session, "WATCH_INTERVAL_S", 0.05)

    stop = threading.Event()
    t = threading.Thread(target=session._watch_daemon,
                         args=(team_repo, _args(), stop), daemon=True)
    t.start()
    try:
        portfile.write(54321)            # es läuft einer
        time.sleep(0.2)
        assert starts == []              # …also nichts zu tun
        portfile.clear()                 # jetzt ist er weg
        deadline = time.monotonic() + 5
        while not starts and time.monotonic() < deadline:
            time.sleep(0.05)
        assert starts == [1]
    finally:
        stop.set()
        t.join(timeout=5)


def test_watcher_only_guards_a_daemon_we_started(team_repo: Path,
                                                 monkeypatch: pytest.MonkeyPatch):
    # Wer sich nur angehängt hat, hat über fremde Prozesse nicht zu verfügen.
    started: list = []
    portfile.write(54321)                # es läuft schon einer
    monkeypatch.setattr(session, "_start_daemon",
                        lambda a, r: started.append(1))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)
    threads: list = []
    monkeypatch.setattr(session.threading, "Thread",
                        lambda **kw: (threads.append(kw), _NoThread())[1])
    monkeypatch.setattr(session.subprocess, "call", lambda argv, **kw: 0)
    monkeypatch.setattr(session, "_pull", lambda root, **kw: None)
    monkeypatch.setattr(session.webbrowser, "open", lambda u: None)

    session.main(["--no-browser"])
    assert threads == []                 # kein Wächter aufgesetzt
    assert started == []


class _NoThread:
    def start(self): pass
    def join(self, timeout=None): pass


def test_watcher_is_set_up_for_an_own_daemon(team_repo: Path,
                                             monkeypatch: pytest.MonkeyPatch):
    threads: list = []
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)
    monkeypatch.setattr(session.threading, "Thread",
                        lambda **kw: (threads.append(kw), _NoThread())[1])
    monkeypatch.setattr(session.subprocess, "call", lambda argv, **kw: 0)
    monkeypatch.setattr(session, "_pull", lambda root, **kw: None)
    monkeypatch.setattr(session.webbrowser, "open", lambda u: None)

    session.main(["--no-browser"])
    assert len(threads) == 1
    assert threads[0]["target"] is session._watch_daemon
    assert threads[0]["daemon"] is True   # darf das Sitzungsende nie aufhalten


# ── Die Startsperre ─────────────────────────────────────────────────────────


def test_start_lock_is_exclusive(team_repo: Path):
    # Zwei gleichzeitig geöffnete Terminals sind kein Randfall — ohne Sperre
    # sähen beide „kein Daemon da" und startete jedes einen.
    import fcntl
    fh = session._acquire_start_lock(team_repo)
    assert fh is not None
    other = (team_repo / "data" / "session-start.lock").open("a+")
    try:
        with pytest.raises(OSError):
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        other.close()
        session._release(fh)


def test_start_lock_is_released(team_repo: Path):
    import fcntl
    session._release(session._acquire_start_lock(team_repo))
    other = (team_repo / "data" / "session-start.lock").open("a+")
    try:
        fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # darf nicht werfen
    finally:
        fcntl.flock(other.fileno(), fcntl.LOCK_UN)
        other.close()


# ── Der Befehl ist installiert ──────────────────────────────────────────────


def test_bibi_is_declared_as_a_console_script():
    import tomllib
    root = Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["bibi"] == "bibi.session:main"
    assert scripts["bibi-ctrl"] == "bibi.ctrl:main"


# ── Die Umgebung für Kindprozesse (m.rau/bibi#76) ───────────────────────────


@pytest.fixture()
def venv_in_repo(team_repo: Path) -> Path:
    """Ein venv im Team-Repo, so wie `uv sync` es anlegt."""
    bin_dir = team_repo / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "bibi-ctrl").write_text("#!/bin/sh\n")
    (bin_dir / "bibi-ctrl").chmod(0o755)
    return bin_dir


def test_claude_finds_bibi_ctrl_on_its_path(team_repo: Path, venv_in_repo: Path,
                                            monkeypatch: pytest.MonkeyPatch):
    """Der Fehler, der das Issue ausgelöst hat, in seiner ursprünglichen Form.

    Die drei Hooks in `.claude/settings.json` rufen `bibi-ctrl` ohne Pfad auf.
    Ohne diese Umgebung scheiterte jeder von ihnen mit `command not found` —
    laut im Log, still in der Wirkung: kein Pull, kein Auto-Push, keine
    Konflikt-Warnung.
    """
    seen: dict = {}
    monkeypatch.setattr(session.subprocess, "call",
                        lambda argv, **kw: (seen.update(kw), 0)[1])
    monkeypatch.setattr(session, "_pull", lambda root, **kw: None)
    monkeypatch.setattr(session, "_ensure_daemon", lambda a, r: (54321, False))
    monkeypatch.setattr(session.webbrowser, "open", lambda u: None)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert session.main(["--no-browser"]) == 0

    path = seen["env"]["PATH"].split(os.pathsep)
    assert path[0] == str(venv_in_repo), "venv-bin gehört nach vorn, nicht irgendwohin"
    assert "/usr/bin" in path, "der geerbte PATH bleibt erhalten"
    assert seen["env"]["VIRTUAL_ENV"] == str(venv_in_repo.parent)


def test_daemon_child_gets_the_same_path(team_repo: Path, venv_in_repo: Path,
                                         monkeypatch: pytest.MonkeyPatch):
    """Nicht nur Claude — ein Job, den der Daemon startet, ruft ebenso `bibi-ctrl`."""
    seen: dict = {}

    class _Popen:
        def __init__(self, argv, **kw):
            seen.update(kw)

    monkeypatch.setattr(session.subprocess, "Popen", _Popen)
    monkeypatch.setenv("PATH", "/usr/bin")
    args = session._parse([])[0]

    session._start_daemon(args, team_repo)

    assert seen["env"]["PATH"].split(os.pathsep)[0] == str(venv_in_repo)
    assert seen["env"]["BIBI_ROLE"] == session.SESSION_ROLE, "das Rollenprofil bleibt"


def test_path_entry_is_not_duplicated(team_repo: Path, venv_in_repo: Path,
                                      monkeypatch: pytest.MonkeyPatch):
    """Eine Sitzung in einer bereits aktivierten venv verdoppelt den Eintrag nicht."""
    monkeypatch.setenv("PATH", f"{venv_in_repo}{os.pathsep}/usr/bin")
    path = session._child_env(team_repo)["PATH"].split(os.pathsep)
    assert path.count(str(venv_in_repo)) == 1
    assert path == [str(venv_in_repo), "/usr/bin"]


def test_env_is_untouched_without_a_venv(team_repo: Path,
                                         monkeypatch: pytest.MonkeyPatch):
    """Kein auffindbares `bibi-ctrl` — dann lieber nichts anfassen als raten.

    Der Fall ist real: eine global installierte Engine ohne Repo-venv. Ein
    erfundener Pfad im PATH wäre schlechter als der Status quo.
    """
    monkeypatch.setattr(session.sys, "executable", "/usr/bin/python3")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = session._child_env(team_repo)
    assert env["PATH"] == "/usr/bin:/bin"


def test_repo_venv_wins_over_the_running_interpreter(team_repo: Path, venv_in_repo: Path,
                                                     tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch):
    """Dieselbe Regel wie bei `_ctrl_prefix`: die Engine, die *dieses* Repo pinnt.

    Ein `bibi`, das aus einem fremden venv gestartet wurde, darf die Sitzung
    nicht auf dessen Engine umlenken.
    """
    other = tmp_path / "other" / "bin"
    other.mkdir(parents=True)
    (other / "bibi-ctrl").write_text("#!/bin/sh\n")
    monkeypatch.setattr(session.sys, "executable", str(other / "python3"))
    assert session._venv_bin(team_repo) == venv_in_repo


# --- `bibi --name`: Anzeigename dieser Sitzung (m.rau/bibi#60) ----------------
#
# Der Name kam bisher ausschliesslich aus `BIBI_NODE_NAME` in
# ~/.config/bibi/env, sonst dem rohen Hostnamen. Beides ist Konfiguration pro
# Nutzer und Maschine — eine einzelne Sitzung konnte davon nicht abweichen,
# ohne die Datei zu aendern. Live sichtbar am Mac, der sich als
# "Mac.fritz.box" meldete.


def test_parse_accepts_name():
    args, _rest = session._parse(["--name", "LHG-Laptop"])
    assert args.name == "LHG-Laptop"


def test_name_is_optional():
    args, _rest = session._parse([])
    assert args.name is None


def test_name_reaches_the_daemon_via_environment(tmp_path, monkeypatch):
    """Der Daemon liest ``BIBI_NODE_NAME`` aus seiner Prozess-Umgebung, und
    genau dort setzt die Sitzung den Wert — damit ergibt sich die Kette
    Flag > env-Variable > Config-Datei > Hostname von selbst, ohne dass
    irgendwo eine zweite Auflösungsreihenfolge gepflegt werden muss."""
    args, _ = session._parse(["--name", "LHG-Laptop"])
    env = session._session_env(args, tmp_path)
    assert env["BIBI_NODE_NAME"] == "LHG-Laptop"


def test_without_name_the_session_does_not_touch_the_variable(tmp_path, monkeypatch):
    """Ohne Flag darf die Sitzung ``BIBI_NODE_NAME`` nicht setzen — sonst
    überschriebe ein leerer Wert die Config-Datei und jeder Knoten hiesse
    wieder wie sein Hostname."""
    monkeypatch.delenv("BIBI_NODE_NAME", raising=False)
    args, _ = session._parse([])
    env = session._session_env(args, tmp_path)
    assert "BIBI_NODE_NAME" not in env


def test_name_is_not_written_back_to_the_config_file(team_repo: Path, tmp_path):
    """Der Wert gilt für diese Sitzung, nicht für den Knoten. Schriebe ihn
    die Sitzung in die ``env``, hiesse der Knoten auch nach ihrem Ende noch so.

    Nahm bis m.rau/bibi#52 ``XDG_CONFIG_HOME`` zur Isolation; seither leistet das
    ``team_repo``, weil die Konfiguration repo-lokal liegt.
    """
    from bibi import config
    config.write_env({"BIBI_NODE_NAME": "vorher"})
    args, _ = session._parse(["--name", "nachher"])
    session._session_env(args, tmp_path)
    assert config.read_env().get("BIBI_NODE_NAME") == "vorher"


def test_attaching_session_says_that_name_has_no_effect(team_repo: Path, capsys):
    """Hängt sich die Sitzung an einen laufenden Daemon, kommt ``--name`` zu
    spät — der Name steckt im Prozess, der schon läuft. Das still zu übergehen
    wäre die unangenehmste Variante: der Nutzer tippt das Flag, im Nodes-Screen
    ändert sich nichts, und nichts sagt ihm warum."""
    portfile.write(51234, host="127.0.0.1", roles="controller", session=True)
    port, started = session._ensure_daemon(_args(name="LHG-Laptop"), team_repo)
    assert (port, started) == (51234, False)
    out = capsys.readouterr().out
    assert "--name" in out and "läuft bereits" in out


def test_attaching_session_stays_quiet_without_name(team_repo: Path, capsys):
    portfile.write(51234, host="127.0.0.1", roles="controller", session=True)
    session._ensure_daemon(_args(), team_repo)
    assert "--name" not in capsys.readouterr().out


def test_hostless_node_starts_without_connect(team_repo: Path, monkeypatch, tmp_path):
    """Die eigentliche Folge von #61, hier festgehalten: ``_host_configured()``
    prüft nur, *ob* eine Scheduler-URL gesetzt ist. Solange ``init`` sie
    unbedingt schrieb, hängte jeder frisch eingerichtete Knoten ``--connect``
    an — und meldete sich bei einem Scheduler auf ``localhost:8769``, den es
    nicht gibt."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)
    from bibi.ctrl import main as ctrl_main
    ctrl_main(["init", "--non-interactive", "--role", "synchronizer,controller"])
    assert "--connect" not in session._daemon_argv(_args(), team_repo)


# ── Upgrade-Aufforderung beim Start (m.rau/bibi#94) ─────────────────────────


def test_start_demands_the_upgrade(team_repo: Path, no_side_effects,
                                   monkeypatch: pytest.MonkeyPatch, capsys):
    """Wartet ein Upgrade, sagt der Start es — und nennt den Weg.

    Für einen Sitzungs-Knoten endet der Deploy-Weg beim Menschen. Sagt ihm
    niemand, dass ein Upgrade bereitliegt, bleibt der Knoten beliebig lange auf
    dem alten Stand; genau der Zustand, den `release.sh` heute mit „zieht beim
    nächsten Start nach" verbucht.
    """
    from bibi import upgrade_notice
    monkeypatch.setattr(upgrade_notice, "pending",
                        lambda *a, **kw: {"expected": "v0.6.0",
                                          "running": "v0.5.3"})
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)

    assert session.main([]) == 0
    out = capsys.readouterr().out
    assert "UPGRADE" in out
    assert "v0.6.0" in out and "v0.5.3" in out
    assert "exit" in out


def test_start_is_silent_without_a_pending_upgrade(team_repo: Path, no_side_effects,
                                                   monkeypatch: pytest.MonkeyPatch,
                                                   capsys):
    """Nicht bei jedem Start nerven — fällig ist der Hinweis nur, wenn gepinnt
    ≠ laufend."""
    from bibi import upgrade_notice
    monkeypatch.setattr(upgrade_notice, "pending", lambda *a, **kw: None)
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)

    assert session.main([]) == 0
    assert "UPGRADE" not in capsys.readouterr().out


def test_start_survives_a_broken_upgrade_check(team_repo: Path, no_side_effects,
                                               monkeypatch: pytest.MonkeyPatch):
    """Der Sitzungsstart darf an der Aufforderung nicht scheitern. Sie ist eine
    Beigabe; eine Sitzung, die deshalb nicht startet, wäre der teurere Fehler."""
    from bibi import upgrade_notice

    def boom(*a, **kw):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(upgrade_notice, "pending", boom)
    monkeypatch.setattr(session, "_start_daemon", lambda a, r: _FakeProc(54321))
    monkeypatch.setattr(session, "_wait_healthy", lambda *a, **kw: True)

    assert session.main([]) == 0
