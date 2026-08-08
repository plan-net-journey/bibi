"""``GET /-/events`` (PLAN-36 Stufe 36.1): der globale FE-Event-Strom.

Der Strom ist im Normalfall endlos — der TestClient-ASGI-Transport kennt aber
keinen echten Client-Disconnect, ein endloser Generator liefe dort ewig.
Deshalb nutzen die Tests den ``limit``-Parameter der Route (Strom endet nach
N data-Events; auch für ``curl``-Diagnose gedacht) und lesen die Antwort als
gewöhnlichen, terminierenden Response-Body. Collector mit ``autorun=False``
injiziert: Ticks werden explizit ausgelöst."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app
from bibi.daemon.bus import Bus, Collector


def _insert_job(conn, job_id="j1", slug="a", status="running"):
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, slug, f"{slug}.md", "job", "echo hi", status))


def _data_events(body: str) -> list[dict]:
    # Pings rausfiltern (seit 36.3 echte data-Events statt SSE-Kommentare,
    # damit der Client-Watchdog sie sieht) — dieselbe Behandlung wie im
    # Browser-Client: nur Lebenszeichen, kein Inhalt.
    evts = [json.loads(ln[len("data: "):])
            for ln in body.splitlines() if ln.startswith("data: ")]
    return [e for e in evts if e.get("t") != "ping"]


def _ping_count(body: str) -> int:
    return body.count('data: {"t": "ping"}') + body.count('data: {"t":"ping"}')


@pytest.fixture
def app_env(team_repo: Path, monkeypatch):
    # Ping-Intervall runtergetaktet — falls ein Test sein limit verfehlt,
    # produziert der Strom schnelle Pings statt 15s-Hänger (der Bash-/CI-
    # Timeout schlägt dann sauber zu, statt still zu stehen).
    monkeypatch.setattr("bibi.daemon.app.EVENTS_PING_S", 0.1)
    bus = Bus()
    collector = Collector(bus, repo_root=team_repo, autorun=False)
    app = create_app(roles.resolve({"scheduler"}), bus=bus, collector=collector)
    return app, bus, collector


def test_events_connect_says_hello(app_env):
    app, _, _ = app_env
    with TestClient(app) as c:
        r = c.get("/-/events", params={"limit": 1})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert _data_events(r.text) == [{"t": "hello"}]


def test_events_resync_lists_active_jobs_on_connect(app_env):
    app, _, _ = app_env
    with TestClient(app) as c:
        # NACH dem App-Start seeden — der Lifespan-Startup-Rescan (scheduler-
        # Rolle) deaktiviert sonst jede handgesetzte Zeile ohne Vault-MD
        # (active=0), und der Connect-Resync fragt active=1.
        conn = job_db.connect()
        _insert_job(conn, status="running")
        conn.close()
        events = _data_events(c.get("/-/events", params={"limit": 3}).text)
    assert events[0] == {"t": "hello"}
    assert {"t": "state", "target": "live:a"} in events
    assert {"t": "state", "target": "journal:a"} in events


def test_events_delivers_collector_findings_after_connect(app_env):
    # Der Statuswechsel passiert NACH dem Connect: ein Timer-Thread schreibt
    # die DB und tickt den Collector, während der (blockierende) GET auf sein
    # limit wartet — der bereits eingeschriebene Abonnent bekommt das Event.
    app, _, collector = app_env

    def flip_to_running():
        c2 = job_db.connect()
        try:
            c2.execute("UPDATE jobs SET status='running' WHERE id='j1'")
        finally:
            c2.close()
        collector.tick_once()

    with TestClient(app) as c:
        conn = job_db.connect()  # nach App-Start, s. Kommentar im Test oben
        _insert_job(conn, status="pending")
        conn.close()
        collector.tick_once()  # prime
        t = threading.Timer(0.3, flip_to_running)
        t.start()
        try:
            events = _data_events(c.get("/-/events", params={"limit": 2}).text)
        finally:
            t.join()
    # Seit #79 traegt das Ereignis den Wert mit, den der Diff ohnehin gelesen
    # hat — der Empfaenger darf ihn ignorieren und refetchen wie bisher.
    assert events == [{"t": "hello"},
                      {"t": "state", "target": "live:a",
                       "v": {"status": "running", "fire": 0}}]


def test_events_idle_stream_sends_data_pings_not_counting_limit(app_env):
    # PLAN-36 Stufe 36.3: der Leerlauf-Ping ist ein ECHTES data-Event
    # ({"t":"ping"}) — SSE-Kommentarzeilen erreichen die EventSource-API nie,
    # der Client-Watchdog braeuchte sonst blind zu bleiben. Pings zaehlen
    # nicht gegen ``limit`` (ein Diagnose-Stream endete sonst im Leerlauf).
    app, bus, _ = app_env

    def publish_late():
        bus.publish_state("jobs")

    with TestClient(app) as c:
        t = threading.Timer(0.35, publish_late)
        t.start()
        try:
            body = c.get("/-/events", params={"limit": 2}).text
        finally:
            t.join()
    # 0.35s Leerlauf bei EVENTS_PING_S=0.1 → mehrere Pings VOR dem State-Event.
    assert _ping_count(body) >= 1
    assert _data_events(body) == [{"t": "hello"}, {"t": "state", "target": "jobs"}]


# --- m.rau/bibi#176: ein geplantes Beenden sieht nicht aus wie ein Absturz ---


def test_shutdown_ends_the_stream_instead_of_running_into_the_timeout(app_env):
    """Der Strom endet, wenn der Daemon herunterfaehrt — von selbst.

    Bisher lief er in uvicorns ``timeout_graceful_shutdown``, die Task wurde
    abgebrochen, und der Abbruch erschien als ~50 Zeilen Stacktrace mit
    ``Exception in ASGI application``. Nichts davon war kaputt; niemand konnte
    das der Ausgabe ansehen.

    **Ohne ``with TestClient(...)``, und das ist kein Stilfehler:** der GET
    laeuft hier ohne ``limit`` und endet vor dem Fix nie. Der Lifespan-Exit des
    Kontextmanagers wartet aber auf offene Anfragen — der Rot-Schritt haette
    also den Testlauf aufgehaengt statt fehlzuschlagen. Ein Test, der haengt,
    sagt nichts; einer, der scheitert, sagt alles.
    """
    import time

    app, bus, _ = app_env
    box: dict[str, str] = {}
    c = TestClient(app)

    def read() -> None:
        box["body"] = c.get("/-/events").text

    th = threading.Thread(target=read, daemon=True)
    th.start()
    time.sleep(0.3)          # der Abonnent ist eingeschrieben, der Strom steht

    begin = getattr(bus, "begin_shutdown", None)
    assert begin is not None, "der Bus kann seine Stroeme nicht schliessen"
    begin()

    th.join(timeout=5)
    assert not th.is_alive(), \
        "der Strom haengt weiter — genau das laeuft in uvicorns Frist und endet im Traceback"

    evts = _data_events(box["body"])
    assert evts[0] == {"t": "hello"}
    assert evts[-1] == {"t": "bye"}, "der Abschied sagt, dass es geplant war"


def test_a_bus_without_shutdown_keeps_streaming(app_env):
    # Gegenprobe: der Abschied kommt vom Shutdown, nicht von irgendeinem Tick.
    app, bus, _ = app_env
    with TestClient(app) as c:
        evts = _data_events(c.get("/-/events", params={"limit": 1}).text)
    assert evts == [{"t": "hello"}]
    assert not bus.closing


# ── #77: der Strom wird zum Hauptkanal, und damit schutzbedürftig ────────────
#
# `/-/events` ist bewusst ungegatet, weil eine `EventSource` keine Header setzen
# kann — solange die Route ein Nebenweg ist, hängt daran nichts. Mit #77 wird
# sie der Hauptkanal zwischen den Knoten, und dort ist der Verbraucher **kein
# Browser, sondern ein Daemon**: er kann sich ausweisen. Wer sich ausweist, wird
# geprüft; wer es nicht tut, sieht aus wie ein Browser und bleibt bei dem, was
# #19 offen lässt — die Frage, wer die Oberfläche sehen darf.


def test_events_rejects_an_identified_but_unapproved_node(app_env):
    app, _, _ = app_env
    with TestClient(app) as c:
        r = c.get("/-/events", params={"limit": 1},
                  headers={"X-Bibi-Node-Id": "nie-gesehen"})
        assert r.status_code == 403


def test_events_lets_an_approved_node_in(app_env):
    app, _, _ = app_env
    with TestClient(app) as c:
        conn = job_db.connect()
        try:
            job_db.set_node_approval(conn, "guter-knoten", "approved")
            conn.commit()
        finally:
            conn.close()
        r = c.get("/-/events", params={"limit": 1},
                  headers={"X-Bibi-Node-Id": "guter-knoten"})
        assert r.status_code == 200
        assert _data_events(r.text) == [{"t": "hello"}]


def test_events_without_a_header_stays_open(app_env):
    """Der Browser kann keinen Header setzen — für ihn ändert sich nichts."""
    app, _, _ = app_env
    with TestClient(app) as c:
        r = c.get("/-/events", params={"limit": 1})
        assert r.status_code == 200
