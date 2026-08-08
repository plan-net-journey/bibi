"""Der Client **abonniert** den Ereignisstrom des Schedulers (#77).

**Befund m.rau, 2026-08-08:** *„Statuswechsel kommen eindeutig zu träge."*

Zu Recht, und die Messung dazu war eindeutig: der Scheduler-Collector tickt
sekündlich, der Client fragte ihn alle fünf Sekunden. **Der Scheduler war
fünfmal genauer, als der Client von ihm wissen wollte** — und die vier Sekunden
dazwischen sah man jedem Statuswechsel an.

Der Strom lag die ganze Zeit bereit: ``/-/events`` wird rollenunabhängig
serviert, Bus und Collector entstehen im App-Lifespan unbedingt. Es fehlte der
Abonnent. Der Mischer stand auch schon — ``_diff_scheduler_jobs()``
veröffentlicht seine Funde auf dem *eigenen* Bus des Clients; es ändert sich
also die Quelle, nicht die Struktur.

Die Tests hier fahren einen **echten** HTTP-Server mit echtem SSE-Strom. Ein
gefaktes Zeilen-Iterable hätte die eine Frage nicht beantworten können, um die
es geht: kommt der Wechsel schnell genug an.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bibi.daemon.bus import SchedulerEvents


class _Bus:
    def __init__(self) -> None:
        self.published: list[str] = []
        self._lock = threading.Lock()

    def publish_state(self, target: str, value: dict | None = None) -> None:
        with self._lock:
            self.published.append(target)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.published)


class _SchedulerStub:
    """Ein Scheduler, der einen echten ``/-/events``-Strom serviert.

    ``sende()`` schiebt ein Ereignis in jede offene Verbindung — damit lässt
    sich ein Statuswechsel zu einem *gewählten* Zeitpunkt auslösen und die
    Latenz bis zur Veröffentlichung messen.
    """

    def __init__(self) -> None:
        self.warteschlangen: list[list[str]] = []
        self.gesehene_node_ids: list[str | None] = []
        self.antwort_status = 200
        #: Setzt der Test, um jede offene Verbindung fallen zu lassen. Ein
        #: `server.shutdown()` genuegt dafuer nicht — es beendet nur die
        #: Annahme neuer Verbindungen, die bestehenden laufen weiter.
        self.abriss = False
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A002 — kein Testrauschen
                pass

            def do_GET(self):  # noqa: N802
                stub.gesehene_node_ids.append(self.headers.get("X-Bibi-Node-Id"))
                if stub.antwort_status != 200:
                    self.send_response(stub.antwort_status)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                eigene: list[str] = []
                with stub._lock:
                    stub.warteschlangen.append(eigene)
                self.wfile.write(b'data: {"t":"hello"}\n\n')
                self.wfile.flush()
                try:
                    while not stub.abriss:
                        with stub._lock:
                            zeilen, eigene[:] = list(eigene), []
                        for z in zeilen:
                            self.wfile.write(z.encode())
                            self.wfile.flush()
                        time.sleep(0.01)
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    with stub._lock:
                        if eigene in stub.warteschlangen:
                            stub.warteschlangen.remove(eigene)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def sende(self, ereignis: dict) -> None:
        zeile = f"data: {json.dumps(ereignis)}\n\n"
        with self._lock:
            for w in self.warteschlangen:
                w.append(zeile)

    def verbindungen(self) -> int:
        with self._lock:
            return len(self.warteschlangen)

    def stop(self) -> None:
        # Erst die Handler entlassen, dann den Server: `server_close()` joint
        # sie, und ein Handler in seiner Sendeschleife haelt sonst den Teardown.
        self.abriss = True
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def scheduler():
    s = _SchedulerStub()
    yield s
    s.stop()


def _warte_auf(bedingung, frist: float = 5.0) -> bool:
    ende = time.time() + frist
    while time.time() < ende:
        if bedingung():
            return True
        time.sleep(0.01)
    return False


def test_a_status_change_reaches_the_client_bus_in_under_a_second(scheduler):
    """Der Rot-Schritt von #77.

    Ein Zustandswechsel beim Scheduler muss den Client-Bus erreichen, **ohne**
    dass der Client irgendetwas abfragt. Vor dem Bau schlug das fehl, weil er
    erst beim nächsten Fünf-Sekunden-Poll etwas merkte.
    """
    bus = _Bus()
    abo = SchedulerEvents(bus, url=scheduler.url, node_id="n1")
    abo.start()
    try:
        assert _warte_auf(lambda: scheduler.verbindungen() >= 1), "keine Verbindung"
        assert _warte_auf(lambda: abo.live), "Abonnement meldet sich nicht als lebendig"

        beginn = time.time()
        scheduler.sende({"t": "state", "target": "live:mein-job"})
        assert _warte_auf(lambda: "live:mein-job" in bus.snapshot(), frist=1.0), \
            "Statuswechsel kam nicht binnen einer Sekunde an"
        assert time.time() - beginn < 1.0
    finally:
        abo.stop()


def test_the_value_travels_with_the_event(scheduler):
    """#77 und #79 wirken zusammen — oder gar nicht.

    Der Scheduler weiss den neuen Status, der Client zeigt ihn an. Ginge der
    Wert auf genau dieser Strecke verloren, traege ihn niemand dorthin, wo er
    gebraucht wird."""
    gesehen: list[tuple] = []

    class _WertBus(_Bus):
        def publish_state(self, target, value=None):
            gesehen.append((target, value))

    abo = SchedulerEvents(_WertBus(), url=scheduler.url, node_id="n1")
    abo.start()
    try:
        assert _warte_auf(lambda: abo.live)
        scheduler.sende({"t": "state", "target": "live:a",
                         "v": {"status": "running", "fire": 2}})
        assert _warte_auf(lambda: gesehen != [])
        assert gesehen[0] == ("live:a", {"status": "running", "fire": 2})
    finally:
        abo.stop()


def test_the_subscription_identifies_itself(scheduler):
    """Der Verbraucher ist ein Daemon, kein Browser — er kann sich ausweisen.

    Daran hängt der Schutz: eine `EventSource` kann keine Header setzen, ein
    Daemon schon. Ohne diese Identität wäre der Hauptkanal zwischen den Knoten
    eine ungegatete Route (s. `test_daemon_scheduler_routes.py` für die
    Gegenseite)."""
    bus = _Bus()
    abo = SchedulerEvents(bus, url=scheduler.url, node_id="knoten-7")
    abo.start()
    try:
        assert _warte_auf(lambda: scheduler.gesehene_node_ids != [])
        assert scheduler.gesehene_node_ids[0] == "knoten-7"
    finally:
        abo.stop()


def test_pings_and_hello_are_not_targets(scheduler):
    """Lebenszeichen sind keine Nachricht — sie dürfen nichts dreckig machen."""
    bus = _Bus()
    abo = SchedulerEvents(bus, url=scheduler.url, node_id="n1")
    abo.start()
    try:
        assert _warte_auf(lambda: abo.live)
        scheduler.sende({"t": "ping"})
        scheduler.sende({"t": "state", "target": "jobs"})
        assert _warte_auf(lambda: "jobs" in bus.snapshot())
        assert bus.snapshot() == ["jobs"]
    finally:
        abo.stop()


def test_appends_stay_out(scheduler):
    """Output-Zeilen des Schedulers gehören nicht auf den Client-Bus.

    Sie tragen Offsets in eine Datei, die es auf diesem Knoten nicht gibt —
    der Weg dorthin ist der Durchreicher aus #78, eine eigene Verbindung je
    Box, kein Sammelstrom."""
    bus = _Bus()
    abo = SchedulerEvents(bus, url=scheduler.url, node_id="n1")
    abo.start()
    try:
        assert _warte_auf(lambda: abo.live)
        scheduler.sende({"t": "append", "target": "out:j1", "off": 1, "e": {"line": "x"}})
        scheduler.sende({"t": "state", "target": "jobs"})
        assert _warte_auf(lambda: "jobs" in bus.snapshot())
        assert "out:j1" not in bus.snapshot()
    finally:
        abo.stop()


def test_the_feed_target_stays_at_home(scheduler):
    """Der Feed ist **dieser** Knoten (#80 trifft #77).

    Er zeigt das Repo, in dem er laeuft: Commits, offene Aenderungen, Cases im
    lokalen Arbeitsbaum. Der Scheduler meldet sein `feed`, wenn sich *dort*
    etwas ruehrt — uebernaehme der Client das, laedt er seine eigene Liste neu,
    weil anderswo etwas passiert ist, und zahlt dafuer einen `git log` plus
    `git status`. Denselben Commit sieht er ohnehin selbst, sobald der
    Synchronizer ihn gebracht hat, und dann meldet ihn sein eigener
    `_diff_feed()`.

    Ein Eintrag ist keine Ausnahmeliste, sondern eine Aussage. Alles andere —
    `live:`, `journal:`, `jobs`, `nodes`, `archived` — ist Scheduler-Zustand
    und gehoert uebernommen."""
    bus = _Bus()
    abo = SchedulerEvents(bus, url=scheduler.url, node_id="n1")
    abo.start()
    try:
        assert _warte_auf(lambda: abo.live)
        scheduler.sende({"t": "state", "target": "feed"})
        scheduler.sende({"t": "state", "target": "jobs"})
        assert _warte_auf(lambda: "jobs" in bus.snapshot())
        assert "feed" not in bus.snapshot()
    finally:
        abo.stop()


def test_a_rejected_node_gets_no_subscription_and_stays_dead(scheduler):
    """Ein nicht freigeschalteter Knoten bekommt keinen Strom.

    Und er weiß es: `live` bleibt falsch, womit der Poll-Rückfall im Collector
    greift — abgewiesen zu werden macht den Client nicht blind."""
    scheduler.antwort_status = 403
    bus = _Bus()
    abo = SchedulerEvents(bus, url=scheduler.url, node_id="fremder", retry=0.05)
    abo.start()
    try:
        assert _warte_auf(lambda: scheduler.gesehene_node_ids != [])
        time.sleep(0.2)
        assert abo.live is False
        assert bus.snapshot() == []
    finally:
        abo.stop()


def test_a_broken_stream_reconnects(scheduler):
    """Ein Abriss macht den Client nicht blind — er verbindet neu."""
    bus = _Bus()
    abo = SchedulerEvents(bus, url=scheduler.url, node_id="n1", retry=0.05)
    abo.start()
    try:
        assert _warte_auf(lambda: abo.live)
        # Die Verbindung stirbt unter dem Abonnenten weg — das Abonnement
        # muss das merken und von selbst zurückfinden.
        scheduler.abriss = True
        assert _warte_auf(lambda: abo.live is False, frist=3.0), "Abriss blieb unbemerkt"
        scheduler.abriss = False
        assert _warte_auf(lambda: abo.live, frist=5.0), "kein Wiederaufbau"

        # Und er trägt wieder: ein Wechsel nach dem Abriss kommt an.
        assert _warte_auf(lambda: scheduler.verbindungen() >= 1)
        scheduler.sende({"t": "state", "target": "live:danach"})
        assert _warte_auf(lambda: "live:danach" in bus.snapshot(), frist=2.0)
    finally:
        abo.stop()


def test_a_node_without_a_scheduler_never_subscribes():
    """Ein Knoten, der selbst der Scheduler ist, hat niemanden zu abonnieren."""
    bus = _Bus()
    abo = SchedulerEvents(bus, url=None, node_id="n1")
    abo.start()
    try:
        time.sleep(0.1)
        assert abo.live is False
        assert bus.snapshot() == []
    finally:
        abo.stop()
