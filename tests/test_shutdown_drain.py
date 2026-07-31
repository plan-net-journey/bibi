"""Job-Drain beim **regulären** Herunterfahren (m.rau/bibi#49).

Der Drain aus #38 hing ausschließlich am Restart-Endpunkt. Ein Session-Ende,
ein ``systemctl stop`` und jedes andere SIGTERM trafen Jobs im Setup genau so
unkontrolliert wie vor #38 der Neustart — die Zusage galt für einen der beiden
Wege, einen Daemon zu beenden.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi import config
from bibi.daemon import app as app_mod
from bibi.daemon import boot_signal, install, roles
from bibi.daemon.app import create_app


class _FakeWorker:
    """Worker-Ersatz, der die **Reihenfolge** festhält — drain vor stop ist der
    ganze Punkt, nicht bloß dass beides passiert."""

    def __init__(self, drained: bool = True, starting: int = 0) -> None:
        self.order: list[str] = []
        self.timeouts: list[float] = []
        self._result = {"drained": drained, "starting": starting}
        self.worker_name = "test-worker"
        self.db_path = None

    async def drain(self, timeout: float = 120.0) -> dict:
        self.order.append("drain")
        self.timeouts.append(timeout)
        return self._result

    def starting_count(self) -> int:
        return self._result["starting"]

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.order.append("stop")


@pytest.fixture()
def no_boot_signal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(boot_signal, "_dir", lambda root=None: tmp_path / "boot")


# ── Die Frist ───────────────────────────────────────────────────────────────


def test_drain_timeout_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_DRAIN_TIMEOUT_S", raising=False)
    assert app_mod._resolve_drain_timeout() == app_mod.DRAIN_TIMEOUT_DEFAULT_S


def test_drain_timeout_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DRAIN_TIMEOUT_S", "45")
    assert app_mod._resolve_drain_timeout() == 45.0


def test_drain_timeout_from_config_file(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BIBI_DRAIN_TIMEOUT_S", raising=False)
    # read_env() parst ungefiltert — der Wert wirkt, ohne in config.KEYS zu
    # stehen (niemand soll ihn beim init-Interview eintippen müssen).
    config.write_env({"BIBI_SCHEDULER_URL": "http://localhost:8769"})
    path = config.env_path()
    path.write_text(path.read_text(encoding="utf-8") + "BIBI_DRAIN_TIMEOUT_S=30\n",
                    encoding="utf-8")
    assert app_mod._resolve_drain_timeout() == 30.0


@pytest.mark.parametrize("raw", ["abc", "-5", "", "  "])
def test_drain_timeout_invalid_falls_back(monkeypatch: pytest.MonkeyPatch, raw: str):
    monkeypatch.setenv("BIBI_DRAIN_TIMEOUT_S", raw)
    assert app_mod._resolve_drain_timeout() == app_mod.DRAIN_TIMEOUT_DEFAULT_S


def test_drain_timeout_zero_is_valid(monkeypatch: pytest.MonkeyPatch):
    # 0 heißt „nicht warten", nicht „kein Wert gesetzt" — dieselbe Lesart wie
    # bei BIBI_SHUTDOWN_TIMEOUT_S.
    monkeypatch.setenv("BIBI_DRAIN_TIMEOUT_S", "0")
    assert app_mod._resolve_drain_timeout() == 0.0


def test_drain_timeout_stays_below_supervisor_patience():
    # Der Default muss zusammen mit uvicorns Verbindungsfrist in das Zeitbudget
    # des Supervisors passen — sonst schneidet ein SIGKILL genau das ab, wofür
    # der Drain da ist. launchd killt ohne ExitTimeOut nach 20 s; deshalb setzt
    # install.py die Frist jetzt ausdrücklich.
    from bibi.ctrl.daemon_cmd import SHUTDOWN_TIMEOUT_DEFAULT_S
    assert (app_mod.DRAIN_TIMEOUT_DEFAULT_S + SHUTDOWN_TIMEOUT_DEFAULT_S
            < install.STOP_TIMEOUT_S)


# ── Der Drain im lifespan-Finally ───────────────────────────────────────────


def test_shutdown_drains_the_worker_before_stopping_it(no_boot_signal, team_repo: Path):
    w = _FakeWorker()
    app = create_app(roles.resolve({"controller"}), worker=w, drain_timeout=0.0)
    with TestClient(app):
        pass  # Start und Ende des Lifespans
    assert w.order == ["drain", "stop"]


def test_shutdown_drains_the_pinned_worker_too(no_boot_signal, team_repo: Path):
    # Auf einem Sitzungsknoten (Profil ohne worker-Rolle) ist der pinned_worker
    # der EINZIGE — er führt die `bibi-ctrl run`-Läufe aus, also gerade das, was
    # ohne Host funktionieren soll.
    p = _FakeWorker()
    app = create_app(roles.resolve({"controller"}), pinned_worker=p, drain_timeout=0.0)
    with TestClient(app):
        pass
    assert p.order == ["drain", "stop"]


def test_shutdown_drains_both_workers(no_boot_signal, team_repo: Path):
    w, p = _FakeWorker(), _FakeWorker()
    app = create_app(roles.resolve({"controller"}), worker=w, pinned_worker=p,
                     drain_timeout=0.0)
    with TestClient(app):
        pass
    assert w.order == ["drain", "stop"]
    assert p.order == ["drain", "stop"]


def test_shutdown_drain_uses_the_resolved_timeout(no_boot_signal, team_repo: Path,
                                                  monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BIBI_DRAIN_TIMEOUT_S", "7")
    w = _FakeWorker()
    app = create_app(roles.resolve({"controller"}), worker=w)  # kein Override
    with TestClient(app):
        pass
    assert w.timeouts == [7.0]


def test_shutdown_clears_the_portfile(no_boot_signal, team_repo: Path):
    """Die Portdatei verschwindet mit dem Herunterfahren (m.rau/bibi#45).

    Warum hier und nicht in einem ``finally`` um ``server.run()``: uvicorn feuert
    das eingefangene Signal am Ende von ``capture_signals()`` erneut, nachdem es
    ``SIG_DFL`` wiederhergestellt hat — der Prozess ist damit weg, bevor ein
    äußeres ``finally`` liefe. Live gefunden beim Rauchtest von #48, wo genau
    diese Datei liegenblieb.
    """
    from bibi.daemon import portfile
    portfile.write(54321)
    app = create_app(roles.resolve({"controller"}), drain_timeout=0.0)
    with TestClient(app):
        assert portfile.read_port() == 54321
    assert not (team_repo / "data" / portfile.FILENAME).exists()


def test_shutdown_leaves_a_foreign_portfile_alone(no_boot_signal, team_repo: Path):
    # Zwei Daemons auf einem Checkout sind nicht vorgesehen — passiert es doch,
    # darf der gehende nicht den Eintrag des bleibenden wegräumen.
    import json
    import os as os_mod

    from bibi.daemon import portfile
    p = team_repo / "data" / portfile.FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"port": 54321, "pid": os_mod.getpid() + 1}),
                 encoding="utf-8")
    app = create_app(roles.resolve({"controller"}), drain_timeout=0.0)
    with TestClient(app):
        pass
    assert p.exists()


def test_shutdown_without_worker_is_a_noop(no_boot_signal, team_repo: Path):
    # Kein Worker ⇒ nichts zu drainen; das Herunterfahren darf daran nicht
    # scheitern (das Default-Profil einer Sitzung trägt keine worker-Rolle).
    app = create_app(roles.resolve({"controller"}), drain_timeout=0.0)
    with TestClient(app):
        pass


# ── Der Abbruchweg ──────────────────────────────────────────────────────────


class _SlowWorker(_FakeWorker):
    """Drainet länger, als irgendjemand warten würde — der Fall, für den es den
    Abbruchweg gibt."""

    async def drain(self, timeout: float = 120.0) -> dict:
        self.order.append("drain")
        await asyncio.sleep(300)
        return {"drained": True, "starting": 0}


def test_drain_is_awaited_by_default(monkeypatch: pytest.MonkeyPatch):
    # Warten ist der Default: ohne Abbruch kehrt _drain_for_shutdown nicht
    # vorzeitig zurück, sondern erst, wenn worker.drain() fertig ist.
    w = _FakeWorker(drained=False, starting=2)
    out = asyncio.run(app_mod._drain_for_shutdown(w, timeout=0.0, label="worker"))
    assert out == {"drained": False, "starting": 2}
    assert w.order == ["drain"]


def _fire_sigint_handler(delay: float = 0.05):
    """``signal.signal`` so ersetzen, dass der übergebene Handler kurz darauf
    von selbst feuert — ein echtes SIGINT im Testlauf träfe pytest.

    Das prüft genau die Stelle, an der der naive Ansatz scheitert: uvicorns
    eigener Handler setzt beim zweiten Signal nur ``force_exit``, was eine
    laufende Coroutine im ``lifespan``-Finally nicht abbricht. Der Handler hier
    ist der, den ``_drain_for_shutdown`` selbst installiert.
    """
    import signal as signal_mod

    def _fake_signal(sig, handler):
        if sig == signal_mod.SIGINT and callable(handler):
            try:
                asyncio.get_running_loop().call_later(delay, handler, sig, None)
            except RuntimeError:
                # asyncio.run() setzt seinen eigenen SIGINT-Handler, BEVOR der
                # Loop läuft — den nicht feuern, sonst stirbt der Testlauf an
                # einem KeyboardInterrupt statt den Drain abzubrechen.
                pass
        return signal_mod.SIG_DFL

    return _fake_signal


def test_interrupted_drain_returns_immediately_and_says_what_is_open(
    monkeypatch: pytest.MonkeyPatch,
):
    # Ein stiller Abbruch wäre schlimmer als kein Drain: wer abbricht, muss
    # erfahren, was noch im Setup steckt.
    w = _SlowWorker(starting=3)
    monkeypatch.setattr(app_mod.signal, "signal", _fire_sigint_handler())

    async def _run():
        return await asyncio.wait_for(
            app_mod._drain_for_shutdown(w, timeout=300.0, label="worker"),
            timeout=10)

    out = asyncio.run(_run())
    assert out["interrupted"] is True
    assert out["drained"] is False
    assert out["starting"] == 3


def test_drain_failure_does_not_derail_the_cleanup(no_boot_signal, team_repo: Path):
    # Der Drain sitzt im lifespan-Finally, in dem danach noch Heartbeat,
    # Rescanner, Sweeper und Synchronizer gestoppt werden. Eine Exception hier
    # würde all das überspringen — er soll das Aufräumen verbessern, nicht es
    # kippen.
    class _BrokenWorker(_FakeWorker):
        async def drain(self, timeout: float = 120.0) -> dict:
            raise RuntimeError("DB weg")

    w = _BrokenWorker()
    app = create_app(roles.resolve({"controller"}), worker=w, drain_timeout=0.0)
    with TestClient(app):
        pass
    assert w.order == ["stop"]  # gestoppt wurde trotzdem


def test_drain_skips_an_object_without_drain(no_boot_signal, team_repo: Path):
    # Nicht jeder Worker-Ersatz kann drainen (Test-Fakes, ältere Injektionen).
    # Fehlt die Methode, wird übersprungen statt geworfen.
    class _NoDrain:
        def __init__(self) -> None:
            self.stopped = False
            self.worker_name = "x"
            self.db_path = None

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            self.stopped = True

    w = _NoDrain()
    app = create_app(roles.resolve({"controller"}), pinned_worker=w, drain_timeout=0.0)
    with TestClient(app):
        pass
    assert w.stopped is True


def test_drain_survives_a_thread_without_signal_handling():
    # TestClient fährt den Lifespan in einem eigenen Thread — dort ist
    # signal.signal nicht erlaubt. Dann eben ohne Abbruchweg, statt am
    # Aufräumen zu scheitern.
    import threading
    result: dict = {}

    def _worker_thread():
        result["out"] = asyncio.run(
            app_mod._drain_for_shutdown(_FakeWorker(), timeout=0.0, label="worker"))

    t = threading.Thread(target=_worker_thread)
    t.start()
    t.join(timeout=10)
    assert result["out"] == {"drained": True, "starting": 0}


# ── Der Restart-Endpunkt zieht nach ─────────────────────────────────────────


def test_restart_drains_the_pinned_worker_too(no_boot_signal, team_repo: Path,
                                              monkeypatch: pytest.MonkeyPatch):
    # Die Asymmetrie aus #38: der Endpunkt drainte nur den rollengebundenen
    # Worker. Auf einem reinen Client ist der pinned_worker der einzige — ein
    # Deploy-Neustart hatte dort dieselbe Setup-Lücke, die der Knopf schließt.
    monkeypatch.setattr("bibi.daemon.app.os.kill", lambda pid, sig: None)
    p = _FakeWorker()
    app = create_app(roles.resolve({"controller"}), pinned_worker=p, drain_timeout=0.0)
    with TestClient(app) as c:
        r = c.post("/-/restart", json={})
    assert r.status_code == 200
    assert "drain" in p.order
    assert r.json()["drained"] is True


def test_restart_reports_both_workers_together(no_boot_signal, team_repo: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("bibi.daemon.app.os.kill", lambda pid, sig: None)
    app = create_app(roles.resolve({"controller"}),
                     worker=_FakeWorker(drained=True, starting=0),
                     pinned_worker=_FakeWorker(drained=False, starting=2),
                     drain_timeout=0.0)
    with TestClient(app) as c:
        body = c.post("/-/restart", json={}).json()
    assert body["drained"] is False
    assert "2 Job(s) noch im Setup" in body["note"]


# ── Der Supervisor bekommt die Zeit dafür ───────────────────────────────────


def test_systemd_unit_sets_a_stop_timeout():
    text = install.systemd_unit_text(root=Path("/srv/team"), uv="/usr/bin/uv",
                                     user="mra", port=8780)
    assert f"TimeoutStopSec={install.STOP_TIMEOUT_S}" in text


def test_launchd_plist_sets_an_exit_timeout(tmp_path: Path):
    # Der wichtigere der beiden: launchds Default sind 20 Sekunden, knapp genug,
    # dass Verbindungsfrist plus Drain hineinlaufen und mitten im Drain ein
    # SIGKILL kommt.
    text = install.launchd_plist_text(root=Path("/Users/x/team"), uv="/opt/uv",
                                      port=8780, label="bibi.test",
                                      log_dir=tmp_path)
    assert f"<key>ExitTimeOut</key><integer>{install.STOP_TIMEOUT_S}</integer>" in text
