"""``POST /-/restart`` (m.rau/bibi#39).

Der Endpunkt beendet den Prozess — im Test darf er das natürlich nicht. Beide
gefährlichen Teile sind deshalb ersetzt: ``os.kill`` (sonst stirbt der
Testlauf) und die Signal-Ablage (sonst schriebe der Test in das echte
``data/boot/`` des Entwickler-Checkouts und der nächste Daemon-Start dort
würde unerwartet pullen).
"""

from __future__ import annotations

import signal
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import boot_signal
from bibi.daemon import roles
from bibi.daemon.app import create_app


@pytest.fixture()
def app_and_signals(tmp_path: Path, monkeypatch):
    """App plus umgeleitete Signal-Ablage; ``os.kill`` wird nur aufgezeichnet."""
    killed: list[int] = []
    monkeypatch.setattr("bibi.daemon.app.os.kill",
                        lambda pid, sig: killed.append(sig))
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")
    app = create_app(roles.resolve({"controller"}))
    return app, killed, tmp_path


def test_plain_restart_writes_no_signal(app_and_signals):
    # Ein reiner Neustart braucht keine Vorarbeit — er ist einfach ein
    # Prozessende, das der Supervisor auffängt.
    app, _killed, _tmp = app_and_signals
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
    assert r.status_code == 200
    assert r.json()["restarting"] is True
    assert r.json()["signals"] == []
    assert boot_signal.pending() == []


def test_deployment_leaves_a_signal_for_the_next_start(app_and_signals):
    app, _killed, _tmp = app_and_signals
    with TestClient(app) as c:
        r = c.post("/-/restart", json={"deployment": True})
    assert r.json()["signals"] == ["deployment"]
    assert boot_signal.pending() == ["deployment"]


def test_reset_implies_deployment_and_is_not_listed_twice(app_and_signals):
    # reset impliziert deployment (boot_signal pullt vorher) — es doppelt
    # anzufordern würde denselben Vorgang zweimal berichten.
    app, _killed, _tmp = app_and_signals
    with TestClient(app) as c:
        r = c.post("/-/restart", json={"deployment": True, "reset": True})
    assert r.json()["signals"] == ["reset"]
    assert boot_signal.pending() == ["reset"]


def test_restart_uses_sigterm_not_hard_exit(app_and_signals):
    # Entscheidend, nicht kosmetisch: nur über SIGTERM greifen uvicorns
    # timeout_graceful_shutdown und das lifespan-Finally. Ein os._exit() würde
    # genau die Garantien aushebeln, um die es beim Job-Drain (#38) geht.
    app, killed, _tmp = app_and_signals
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
        assert r.status_code == 200
        # Der Kill ist bewusst um 0.5 s verzögert, damit die Antwort noch
        # rausgeht — hier wird genau darauf gewartet, statt ein leeres Ergebnis
        # als Erfolg zu lesen.
        deadline = time.monotonic() + 5
        while not killed and time.monotonic() < deadline:
            time.sleep(0.05)
    assert killed == [signal.SIGTERM]


def test_response_arrives_before_the_process_is_killed(app_and_signals):
    # Die Verzögerung ist der Zweck: ein Aufrufer, der 200 bekommt, weiß dass
    # sein Wunsch angekommen ist. Würde sofort gekillt, käme nie eine Antwort.
    app, killed, _tmp = app_and_signals
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
        assert r.status_code == 200
        assert killed == []   # zum Antwortzeitpunkt noch nicht gekillt


def test_response_explains_the_second_start(app_and_signals):
    # Ein Aufrufer soll wissen, dass bei deployment/reset ein zweiter Neustart
    # folgt, bevor der Server wieder erreichbar ist — sonst wirkt die längere
    # Ausfallzeit wie ein Fehler.
    app, _killed, _tmp = app_and_signals
    with TestClient(app) as c:
        r = c.post("/-/restart", json={"deployment": True})
    assert "zweiter Start" in r.json()["note"]
