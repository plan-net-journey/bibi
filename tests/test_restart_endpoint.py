"""``POST /-/restart`` (m.rau/bibi#39).

Der Endpunkt beendet den Prozess und pullt — im Test darf er beides nicht. Drei
Dinge sind deshalb ersetzt: ``os.kill`` (sonst stirbt der Testlauf), der Pull
(sonst würde gegen ein echtes Remote gearbeitet) und die Signal-Ablage (sonst
schriebe der Test in das echte ``data/boot/`` des Entwickler-Checkouts, und der
nächste Daemon-Start dort würde unerwartet das venv wegwerfen).
"""

from __future__ import annotations

import signal
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import app as app_mod
from bibi.daemon import boot_signal, roles
from bibi.daemon.app import create_app


@pytest.fixture()
def harness(tmp_path: Path, monkeypatch):
    """App plus Aufzeichnung von Kill und Pull; Signal-Ablage umgeleitet."""
    killed: list[int] = []
    pulls: list[object] = []
    monkeypatch.setattr("bibi.daemon.app.os.kill",
                        lambda pid, sig: killed.append(sig))
    monkeypatch.setattr(app_mod, "_pull_for_deploy",
                        lambda lock=None: (pulls.append(lock), (True, None))[1])
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")
    app = create_app(roles.resolve({"controller"}))
    return app, killed, pulls


def test_plain_restart_neither_pulls_nor_signals(harness):
    # Ein reiner Neustart braucht keine Vorarbeit — er ist einfach ein
    # Prozessende, das der Supervisor auffängt.
    app, _killed, pulls = harness
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["restarting"] is True
    assert body["pulled"] is False
    assert body["signals"] == []
    assert pulls == []
    assert boot_signal.pending() == []


def test_deployment_pulls_in_the_request_and_needs_no_signal(harness):
    # Der Kern des Einwands von m.rau: liegt die neue Lock schon vor dem ERSTEN
    # Neustart im Checkout, synct uv run sofort dagegen — ein Durchlauf statt
    # zweier, und kein Boot-Signal nötig.
    app, _killed, pulls = harness
    with TestClient(app) as c:
        r = c.post("/-/restart", json={"deployment": True})
    assert r.status_code == 200
    assert r.json()["pulled"] is True
    assert r.json()["signals"] == []
    assert len(pulls) == 1
    assert boot_signal.pending() == []


def test_failed_pull_aborts_the_restart(harness, monkeypatch):
    # Ein Neustart auf den alten Stand wäre nur Ausfallzeit ohne Nutzen. Der
    # Aufrufer erfährt den Grund, statt ihn im Log suchen zu müssen.
    app, killed, _pulls = harness
    monkeypatch.setattr(app_mod, "_pull_for_deploy",
                        lambda lock=None: (False, "conflict"))
    with TestClient(app) as c:
        r = c.post("/-/restart", json={"deployment": True})
    assert r.status_code == 409
    assert "conflict" in r.json()["detail"]
    assert killed == []          # kein Neustart
    assert boot_signal.pending() == []


def test_reset_pulls_and_leaves_a_signal(harness):
    # reset ist der einzige Fall, der zwei Neustarts braucht: ein Prozess kann
    # sein eigenes venv nicht unter sich austauschen.
    app, _killed, pulls = harness
    with TestClient(app) as c:
        r = c.post("/-/restart", json={"reset": True})
    assert r.json()["signals"] == ["reset"]
    assert r.json()["pulled"] is True
    assert len(pulls) == 1
    assert boot_signal.pending() == ["reset"]


def test_reset_and_deployment_pull_only_once(harness):
    app, _killed, pulls = harness
    with TestClient(app) as c:
        r = c.post("/-/restart", json={"deployment": True, "reset": True})
    assert r.json()["signals"] == ["reset"]
    assert len(pulls) == 1


def test_sync_lock_is_passed_to_the_pull(tmp_path: Path, monkeypatch):
    # Ohne den Lock griffen Deploy-Pull und Synchronizer gleichzeitig ins selbe
    # Repo — genau das koordiniert er (PLAN-6 §3 D2).
    import threading
    pulls: list[object] = []
    lock = threading.Lock()
    monkeypatch.setattr("bibi.daemon.app.os.kill", lambda pid, sig: None)
    monkeypatch.setattr(app_mod, "_pull_for_deploy",
                        lambda l=None: (pulls.append(l), (True, None))[1])
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")
    app = create_app(roles.resolve({"controller"}), sync_lock=lock)
    with TestClient(app) as c:
        c.post("/-/restart", json={"deployment": True})
    assert pulls == [lock]


def test_pull_for_deploy_calls_git_ops_correctly(monkeypatch):
    """Die eine Stelle, die alle anderen Tests wegmocken — und genau dort saß
    der Fehler.

    Beim ersten scharfen Einsatz (2026-07-30) antwortete der Endpunkt auf allen
    drei Knoten mit ``current_branch() takes 0 positional arguments but 1 was
    given``: ``_pull_for_deploy`` reichte ein Repo-Root durch, das die Funktion
    gar nicht annimmt (``git_ops._git()`` arbeitet im Prozess-cwd). Sämtliche
    Endpunkt-Tests hatten ``_pull_for_deploy`` ersetzt und konnten den
    Signaturfehler deshalb nicht sehen. Dieser Test ruft die echte Funktion und
    mockt eine Ebene tiefer.
    """
    from bibi import git_ops
    seen: dict = {}

    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(git_ops, "integrate",
                        lambda branch, **kw: (seen.update(branch=branch, kw=kw), (True, None))[1])

    ok, kind = app_mod._pull_for_deploy()
    assert (ok, kind) == (True, None)
    assert seen["branch"] == "trunk"
    # Der Live-Edit-Guard muss aus sein: er schützt unbeaufsichtigte Läufe vor
    # einem tippenden Menschen — ein angefordertes Deployment ist das Gegenteil,
    # und ein stiller Skip wäre hier der Fehler.
    assert seen["kw"]["guard_live_paths"] is False


def test_pull_for_deploy_holds_the_sync_lock(monkeypatch):
    import threading
    from bibi import git_ops
    lock = threading.Lock()
    held: list[bool] = []

    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(git_ops, "integrate",
                        lambda branch, **kw: (held.append(lock.locked()), (True, None))[1])

    app_mod._pull_for_deploy(lock)
    assert held == [True]        # während des Pulls gehalten
    assert lock.locked() is False  # danach wieder frei


def test_restart_uses_sigterm_not_hard_exit(harness):
    # Entscheidend, nicht kosmetisch: nur über SIGTERM greifen uvicorns
    # timeout_graceful_shutdown und das lifespan-Finally. Ein os._exit() würde
    # genau die Garantien aushebeln, um die es beim Job-Drain (#38) geht.
    app, killed, _pulls = harness
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
        assert r.status_code == 200
        # Der Kill ist bewusst um 0.5 s verzögert, damit die Antwort noch
        # rausgeht — hier wird darauf gewartet, statt ein leeres Ergebnis als
        # Erfolg zu lesen.
        deadline = time.monotonic() + 5
        while not killed and time.monotonic() < deadline:
            time.sleep(0.05)
    assert killed == [signal.SIGTERM]


def test_response_arrives_before_the_process_is_killed(harness):
    # Die Verzögerung ist der Zweck: wer 200 bekommt, weiß dass sein Wunsch
    # angekommen ist. Würde sofort gekillt, käme nie eine Antwort.
    app, killed, _pulls = harness
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
        assert r.status_code == 200
        assert killed == []


# ── Job-Drain vor dem Neustart (m.rau/bibi#38) ─────────────────────────────


class _FakeWorker:
    """Minimaler Worker-Ersatz: zeichnet auf, ob gedraint wurde."""

    def __init__(self, drained: bool = True, starting: int = 0) -> None:
        self.calls = 0
        self._result = {"drained": drained, "starting": starting}
        # create_app liest diese Attribute beim Verdrahten der Routen.
        self.worker_name = "test-worker"
        self.db_path = None

    async def drain(self, timeout: float = 120.0) -> dict:
        self.calls += 1
        return self._result

    async def start(self) -> None:   # vom Lifespan aufgerufen
        pass

    async def stop(self) -> None:
        pass


def test_restart_drains_before_killing(tmp_path: Path, monkeypatch):
    # Ohne Drain wäre ein Restart ein Würfelwurf auf das Setup-Fenster: ein Job
    # zwischen Reservierung und Wrapper-Spawn hat noch keinen eigenen Prozess
    # und überlebt den Neustart deshalb nicht.
    killed: list[int] = []
    monkeypatch.setattr("bibi.daemon.app.os.kill", lambda pid, sig: killed.append(sig))
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")
    w = _FakeWorker()
    app = create_app(roles.resolve({"controller"}), worker=w)
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
        assert r.status_code == 200
        assert w.calls == 1                 # der Endpunkt hat gedraint
        assert r.json()["drained"] is True
    # …und das Herunterfahren drainet seit m.rau/bibi#49 noch einmal. Doppelt
    # ist kein Fehler, sondern die Folge davon, dass jetzt BEIDE Wege die Zusage
    # aus #38 halten: der Endpunkt schickt sich SIGTERM, und der landet im
    # lifespan-Finally. Der zweite Drain findet nichts mehr vor und kostet nichts.
    assert w.calls == 2


def test_restart_warns_when_drain_times_out(tmp_path: Path, monkeypatch):
    # Ein hängender Image-Build darf einen Deploy nicht dauerhaft blockieren —
    # aber er darf auch nicht unbemerkt bleiben. Also: neu starten, und es
    # sagen.
    monkeypatch.setattr("bibi.daemon.app.os.kill", lambda pid, sig: None)
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")
    app = create_app(roles.resolve({"controller"}),
                     worker=_FakeWorker(drained=False, starting=2))
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
    body = r.json()
    assert body["restarting"] is True      # kein Abbruch
    assert body["drained"] is False
    assert "2 Job(s) noch im Setup" in body["note"]


# ── m.rau/bibi#44: ohne Supervisor kommt niemand zurück ────────────────────


def test_supervised_daemon_keeps_the_supervisor_promise(harness):
    # Der unveränderte Normalfall: systemd/launchd tragen Restart=always bzw.
    # KeepAlive, ein sauberes Prozessende genügt wirklich.
    app, _killed, _pulls = harness
    with TestClient(app) as c:
        body = c.post("/-/restart", json={}).json()
    assert body["supervised"] is True
    assert "Supervisor startet neu" in body["note"]


def test_session_daemon_does_not_promise_a_supervisor(tmp_path: Path, monkeypatch):
    """Der Befund von m.rau am 2026-08-01: am Mac ist seit `v0.5.1` kein
    ``com.bibi.*`` mehr geladen. Der Knopf beendete den Daemon und meldete
    „Supervisor startet neu" — eine Erfolgsmeldung für etwas, das nicht
    stattfindet, dieselbe Fehlerform wie in #88, #90 und #95.

    Entschieden ist: der Knopf beendet **und sagt es ehrlich**. Nicht: der Mac
    bekommt seinen launchd-Dienst zurück."""
    monkeypatch.setattr("bibi.daemon.app.os.kill", lambda pid, sig: None)
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")
    app = create_app(roles.resolve({"controller"}), session_scoped=True)
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
    assert r.status_code == 200                       # er endet trotzdem
    body = r.json()
    assert body["restarting"] is True
    assert body["supervised"] is False
    assert "Supervisor startet neu" not in body["note"]   # kein falsches Versprechen
    assert "kein Supervisor" in body["note"]              # sondern der wahre Grund
    assert "bibi" in body["note"]                         # und der Weg zurück


def test_session_daemon_still_pulls_for_a_deployment(tmp_path: Path, monkeypatch):
    """Ein Deploy auf einem Sitzungs-Knoten bleibt sinnvoll: die neue Lock liegt
    danach im Checkout, der nächste ``bibi``-Start zieht sie. Nur das Versprechen
    ändert sich, nicht der Vorgang."""
    pulls: list[object] = []
    monkeypatch.setattr("bibi.daemon.app.os.kill", lambda pid, sig: None)
    monkeypatch.setattr(app_mod, "_pull_for_deploy",
                        lambda lock=None: (pulls.append(lock), (True, None))[1])
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")
    app = create_app(roles.resolve({"controller"}), session_scoped=True)
    with TestClient(app) as c:
        body = c.post("/-/restart", json={"deployment": True}).json()
    assert body["pulled"] is True
    assert len(pulls) == 1
    assert body["supervised"] is False
