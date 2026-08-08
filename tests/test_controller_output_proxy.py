"""Der Output eines Scheduler-Laufs erreicht den Client (#78).

**Befund m.rau, 2026-08-08:** *„Aber das dritte (Output) stört mich ebenfalls.
Bei running jobs brauchen wir hier dann quasi einen 2. Strom."*

Die ``append``-Ereignisse entstehen, indem der Collector eine **lokale** Datei
tailt — ``data/job/<run_id>/output.jsonl``. Für einen Lauf auf dem Scheduler
gibt es diese Datei auf dem Client nicht. Was der Client stattdessen tat: ein
einmaliger HTTP-Abruf. Ein Schnappschuss, der nicht wächst.

**Der Kanal existiert seit dem 2026-07-20.**
``/-/job/{id}/output/stream`` wächst zur Laufzeit, sendet ``event: done`` als
eindeutiges Ende und trägt ``id:``-Zeilen für lückenloses Wiederaufsetzen.
Genutzt wurde er nicht, weil PLAN-36 Stufe 36.2 die Output-EventSource
abgeschafft hat — *„es habe sie auf Client-Knoten gar nicht gegeben"*, richtig
beobachtet und falsch geschlossen: sie war auf den *eigenen* Knoten gerichtet.

**Es fehlte der Durchreicher.** Der Browser kann den Scheduler nicht direkt
ansprechen (keine CORS-Header, dieselbe Lage, aus der ``_ops_ziel()`` entstand).
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


class _SchedulerStub:
    """Ein Scheduler, der einen echten ``/-/job/{id}/output/stream`` serviert."""

    def __init__(self) -> None:
        self.zeilen: list[str] = []
        self.pfade: list[str] = []
        self.kopfzeilen: list[dict] = []
        self.fertig = False
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                stub.pfade.append(self.path)
                stub.kopfzeilen.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                gesendet = 0
                try:
                    while True:
                        with stub._lock:
                            neu, gesendet = stub.zeilen[gesendet:], len(stub.zeilen)
                        for z in neu:
                            self.wfile.write(z.encode())
                            self.wfile.flush()
                        if stub.fertig:
                            self.wfile.write(b"event: done\ndata: {}\n\n")
                            self.wfile.flush()
                            return
                        time.sleep(0.01)
                except (BrokenPipeError, OSError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def sende(self, off: int, text: str) -> None:
        with self._lock:
            self.zeilen.append(f'id: {off}\ndata: {{"s":"out","line":"{text}"}}\n\n')

    def output_pfad(self) -> str | None:
        """Der Output-Aufruf — seit #77 abonniert der Client zusaetzlich
        ``/-/events``, und der landet hier ebenfalls."""
        return next((p for p in self.pfade if "/output/stream" in p), None)

    def stop(self) -> None:
        self.fertig = True
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def scheduler():
    s = _SchedulerStub()
    yield s
    s.stop()


@pytest.fixture
def client_app(team_repo, monkeypatch, scheduler):
    """Ein reiner Client, der den Stub als seinen Scheduler kennt."""
    monkeypatch.setenv("BIBI_SCHEDULER_URL", scheduler.url)
    return create_app(roles.resolve({"controller"}))


def test_a_line_written_on_the_scheduler_arrives_through_the_proxy(client_app, scheduler):
    """Der Rot-Schritt von #78: es gab den Durchreicher nicht."""
    scheduler.sende(1, "erste Zeile")
    scheduler.fertig = True
    with TestClient(client_app) as c:
        r = c.get("/-/ui/jobs/j1/output/stream")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
    assert "erste Zeile" in r.text
    assert (scheduler.output_pfad() or "").startswith("/-/job/j1/output/stream")


def test_the_end_is_passed_through_exactly_once(client_app, scheduler):
    """`event: done` ist der Unterschied zwischen Ende und Abriss.

    Der Browser sieht bei beidem dasselbe `onerror` (Befund 2026-07-20) — nur
    dieses Ereignis erlaubt ihm, selbst zu schließen, statt zu raten. Ginge es
    im Durchreicher verloren, sähe jedes Laufende wie ein Abriss aus."""
    scheduler.sende(1, "letzte")
    scheduler.fertig = True
    with TestClient(client_app) as c:
        text = c.get("/-/ui/jobs/j1/output/stream").text
    assert text.count("event: done") == 1


def test_the_resume_offset_travels_upstream(client_app, scheduler):
    """Lückenloses Wiederaufsetzen — der Browser schickt `Last-Event-ID`, und
    der Scheduler kann nur danach handeln, wenn es ihn erreicht."""
    scheduler.fertig = True
    with TestClient(client_app) as c:
        c.get("/-/ui/jobs/j1/output/stream", headers={"Last-Event-ID": "17"})
    assert "from=17" in (scheduler.output_pfad() or "")


def test_the_query_offset_travels_upstream_too(client_app, scheduler):
    scheduler.fertig = True
    with TestClient(client_app) as c:
        c.get("/-/ui/jobs/j1/output/stream", params={"from": 5})
    assert "from=5" in (scheduler.output_pfad() or "")


def test_the_proxy_identifies_itself(client_app, scheduler):
    """Derselbe Ausweis wie jeder andere Scheduler-Aufruf des Clients."""
    scheduler.fertig = True
    with TestClient(client_app) as c:
        c.get("/-/ui/jobs/j1/output/stream")
    kopf = next(k for p, k in zip(scheduler.pfade, scheduler.kopfzeilen)
                if "/output/stream" in p)
    assert kopf.get("X-Bibi-Node-Id")


def test_too_many_open_streams_are_refused(client_app, scheduler, monkeypatch):
    """Die Obergrenze, und warum sie hier steht.

    Der Durchreicher hält je offener Output-Box je Tab **eine** Verbindung zum
    Scheduler. Ohne Deckel multipliziert sich das mit den Tabs, und ein
    vergessenes Fenster kostet dauerhaft Verbindungen auf dem Host. Der
    abgewiesene Fall ist nicht schlimm: die Box behält ihren server-seitigen
    Seed und bleibt lesbar, sie wächst nur nicht mit."""
    from bibi import controller as controller_mod
    monkeypatch.setattr(controller_mod, "_MAX_OUTPUT_PROXIES", 0)
    with TestClient(client_app) as c:
        r = c.get("/-/ui/jobs/j1/output/stream")
    assert r.status_code == 429


def test_a_node_without_a_scheduler_has_nothing_to_pass_through(team_repo, monkeypatch):
    """Auf dem Host ist der eigene Strom der richtige — der Umweg über sich
    selbst wäre keiner."""
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)
    app = create_app(roles.resolve({"controller"}))
    with TestClient(app) as c:
        assert c.get("/-/ui/jobs/j1/output/stream").status_code == 404


# ── Die Box weiß, woher sie wächst ──────────────────────────────────────────


def test_the_live_box_names_its_stream_when_the_run_is_remote():
    """Ohne diese Angabe fände das FE den zweiten Strom nie.

    Der globale Bus trägt ihn nicht: seine `append`-Ereignisse entstehen aus
    einer lokalen Datei, die es für einen Scheduler-Lauf hier nicht gibt."""
    html = render.live_output_box("j7", [], kind="job", stream_url="/-/ui/jobs/j7/output/stream")
    assert 'data-stream="/-/ui/jobs/j7/output/stream"' in html


def test_the_live_box_stays_as_it_was_for_a_local_run():
    """Rein additiv: ein lokaler Lauf wird weiter vom globalen Bus gespeist."""
    html = render.live_output_box("j7", [], kind="job")
    assert "data-stream" not in html


# ── Die Verdrahtung: trägt der Screen die Angabe wirklich? ───────────────────


def test_a_running_scheduler_run_gets_its_stream_on_the_client(client_app):
    """Ohne diese Zeile wäre der Durchreicher gebaut und unbenutzt — genau der
    Zustand, aus dem #78 überhaupt entstanden ist."""
    job = {"id": "j9", "status": "running", "slug": "a"}
    html = render.live_fragment({"slug": "a"}, [], job, slug="a",
                                live_output={"events": [], "kind": "job"},
                                output_stream_url="/-/ui/jobs/j9/output/stream")
    assert 'data-stream="/-/ui/jobs/j9/output/stream"' in html


def test_a_terminal_run_gets_no_stream():
    """Klarstellung m.rau: *„Bei terminalen Läufen nicht, ich weiss."*"""
    job = {"id": "j9", "status": "complete", "slug": "a"}
    html = render.live_fragment({"slug": "a"}, [], job, slug="a",
                                live_output={"events": [{"s": "out", "line": "x"}],
                                             "kind": "job"},
                                output_stream_url="/-/ui/jobs/j9/output/stream")
    assert "data-stream" not in html


def test_the_frontend_knows_how_to_consume_the_second_stream():
    """Der Verbraucher im FE — und dass er auf `done` schließt statt auf einen
    Fehler zu raten (die 2026-07-20-Lektion, die diese EventSource überhaupt
    erst zurückkommen lässt)."""
    js = render._EVENTS_JS
    assert "data-stream" in js
    assert "attachRemote" in js
    assert "'done'" in js


# ── Die Invariante, die dreimal gebrochen wurde ─────────────────────────────


def test_read_timeouts_outlast_the_servers_ping():
    """Wer einen SSE-Strom liest, muss länger warten können, als der Server
    schweigt.

    **Dreimal am 2026-08-08 verletzt, jedes Mal mit demselben Muster:** ein
    kurzer Socket-Timeout, gewählt damit ein Abbruch schnell greift — und nach
    `socket.timeout` ist der `http.client`-Stream unbrauchbar. Der Leser fiel
    heraus und verband neu, im Takt des Timeouts. Beim Ereignis-Abonnement
    (#77) waren das 100 Verbindungsaufbauten in zwei Minuten; beim
    Output-Durchreicher (#78) hätte es jede Box getroffen, deren Job gerade
    nachdenkt.

    Die Regel ist einfach genug, um sie zu prüfen statt sie zu erinnern: **der
    Lese-Timeout ist größer als das Ping-Intervall der Gegenseite.** Ein
    Timeout heißt dann *tot*, nicht *still* — und nur dann ist ein Neuaufbau
    die richtige Antwort."""
    from bibi.controller import _PROXY_READ_TICK_S
    from bibi.daemon.app import EVENTS_PING_S
    from bibi.daemon.bus import _SUB_WATCHDOG_S

    # Beide Ströme, die dieser Knoten liest, gegen die Sendepause der Gegenseite.
    assert _SUB_WATCHDOG_S > EVENTS_PING_S, (
        "das Abonnement wirft die Verbindung weg, bevor der Scheduler pingt")
    assert _PROXY_READ_TICK_S > 15, (
        "der Durchreicher wirft die Verbindung weg, bevor der Output-Strom pingt "
        "(_formatted_sse sendet ': ping' bei >=15 s Sendepause)")
