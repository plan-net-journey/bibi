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
    assert events == [{"t": "hello"}, {"t": "state", "target": "live:a"}]


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
