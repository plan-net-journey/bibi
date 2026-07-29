"""FE-Event-Bus (PLAN-36 Stufe 36.1): Bus-Pub/Sub + Collector-Diff, pur.

Der Collector wird mit einem Aufzeichnungs-Bus getestet (kein Loop nötig);
die Bus-Klasse selbst mit echten asyncio-Subscribern via ``asyncio.run``."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.daemon.bus import Bus, Collector, bucket_slug
from bibi.wrapper import output


# ── bucket_slug ──────────────────────────────────────────────────────────────


def test_bucket_slug_strips_pin_suffix():
    assert bucket_slug("fe-live-probe-742ab201", "Mac") == "fe-live-probe"


def test_bucket_slug_none_without_pin_or_suffix():
    assert bucket_slug("fe-live-probe-742ab201", None) is None
    assert bucket_slug("daily-digest", "Mac") is None  # kein 8-Hex-Suffix


# ── Bus (echte asyncio-Subscriber) ───────────────────────────────────────────


def test_bus_state_events_coalesce_per_target():
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        bus.publish_state("live:a")
        bus.publish_state("live:a")   # doppelt → einmal
        bus.publish_state("journal:a")
        return await bus.wait(sub, timeout=1.0)
    events = asyncio.run(run())
    assert events == [{"t": "state", "target": "live:a"},
                      {"t": "state", "target": "journal:a"}]


def test_bus_appends_keep_order_after_states():
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        bus.publish_append("out:j1", 1, {"line": "eins"})
        bus.publish_state("live:a")
        bus.publish_append("out:j1", 2, {"line": "zwei"})
        return await bus.wait(sub, timeout=1.0)
    events = asyncio.run(run())
    # Zustands-Events zuerst (idempotent), Appends danach in Reihenfolge.
    assert events[0] == {"t": "state", "target": "live:a"}
    assert [e["off"] for e in events[1:]] == [1, 2]


def test_bus_append_overflow_becomes_dirty():
    async def run():
        bus = Bus(append_limit=2)
        sub = bus.subscribe()
        for i in range(1, 5):
            bus.publish_append("out:j1", i, {"line": str(i)})
        return await bus.wait(sub, timeout=1.0)
    events = asyncio.run(run())
    appends = [e for e in events if e["t"] == "append"]
    states = [e for e in events if e["t"] == "state"]
    assert [e["off"] for e in appends] == [1, 2]           # Limit griff
    assert states == [{"t": "state", "target": "out:j1"}]  # Lücke → Dirty-Heilung


def test_bus_wait_timeout_returns_empty():
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        return await bus.wait(sub, timeout=0.05)
    assert asyncio.run(run()) == []


def test_bus_unsubscribe_stops_delivery():
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        bus.unsubscribe(sub)
        bus.publish_state("live:a")
        return bus.subscriber_count(), await bus.wait(sub, timeout=0.05)
    count, events = asyncio.run(run())
    assert count == 0 and events == []


def test_bus_publish_from_foreign_thread():
    # Der Collector publiziert aus einem Executor-Thread (run_in_executor,
    # Muster Sweeper) — _wake() muss loop-sicher sein.
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, bus.publish_state, "live:x")
        return await bus.wait(sub, timeout=1.0)
    assert asyncio.run(run()) == [{"t": "state", "target": "live:x"}]


# ── Collector (Aufzeichnungs-Bus, echte Job-DB im team_repo) ────────────────


class _RecordingBus:
    def __init__(self):
        self.states: list[str] = []
        self.appends: list[tuple] = []

    def publish_state(self, target):
        self.states.append(target)

    def publish_append(self, target, off, event):
        self.appends.append((target, off, event))


def _insert_job(conn, job_id="j1", slug="a", status="pending", *,
                fire=0, payload="echo hi", pinned_host=None):
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, fire, pinned_host) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, slug, f"{slug}.md", "job", payload, status, fire, pinned_host))


@pytest.fixture
def collector(team_repo: Path):
    bus = _RecordingBus()
    return Collector(bus, repo_root=team_repo, autorun=False), bus, team_repo


def test_collector_first_tick_primes_silently(collector):
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="running")
    conn.close()
    col.tick_once()
    assert bus.states == [] and bus.appends == []  # Priming: kein Dirty-Sturm


def test_collector_detects_status_change(collector):
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="pending")
    col.tick_once()  # prime
    conn.execute("UPDATE jobs SET status='running' WHERE id='j1'")
    col.tick_once()
    conn.close()
    assert "live:a" in bus.states


def test_collector_detects_journal_insert(collector):
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="running")
    col.tick_once()  # prime
    job_db.report_status(conn, "j1", status="complete", exit_code=0)
    col.tick_once()
    conn.close()
    assert "journal:a" in bus.states
    assert "live:a" in bus.states  # Statuswechsel running→complete


def test_collector_tails_running_output(collector):
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="pending")
    col.tick_once()  # prime
    conn.execute("UPDATE jobs SET status='running' WHERE id='j1'")
    run_id = job_db.run_id_for("a", "j1", 0)
    out_path = root / "data" / "job" / run_id / "output.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.append(out_path, "out", "zeile eins")
    col.tick_once()
    output.append(out_path, "out", "zeile zwei")
    col.tick_once()
    conn.close()
    offs = [(t, off) for (t, off, _e) in bus.appends]
    assert offs == [("out:j1", 1), ("out:j1", 2)]
    assert bus.appends[0][2].get("line") == "zeile eins"


def test_collector_priming_mid_run_skips_backlog(collector):
    # Daemon-Neustart mitten in einem Lauf: Bestand trägt der Seiten-Seed,
    # der Collector streamt nur Neues (E5).
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="running")
    run_id = job_db.run_id_for("a", "j1", 0)
    out_path = root / "data" / "job" / run_id / "output.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.append(out_path, "out", "alt")
    col.tick_once()  # prime — "alt" darf NICHT als Append kommen
    output.append(out_path, "out", "neu")
    col.tick_once()
    conn.close()
    assert [e.get("line") for (_t, _o, e) in bus.appends] == ["neu"]


def test_collector_final_flush_then_drops_tail(collector):
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="pending")
    col.tick_once()  # prime
    conn.execute("UPDATE jobs SET status='running' WHERE id='j1'")
    run_id = job_db.run_id_for("a", "j1", 0)
    out_path = root / "data" / "job" / run_id / "output.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    col.tick_once()
    # Terminal + letzte Zeile im selben Fenster: der Übergangs-Tick liest final.
    output.append(out_path, "out", "letzte")
    job_db.report_status(conn, "j1", status="complete", exit_code=0)
    col.tick_once()
    conn.close()
    assert [e.get("line") for (_t, _o, e) in bus.appends] == ["letzte"]
    assert col._tails == {}  # Tail entlassen


def test_collector_pinned_run_publishes_bucket_targets(collector):
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, job_id="p1", slug="probe-742ab201", status="pending",
                pinned_host="Mac")
    col.tick_once()  # prime
    conn.execute("UPDATE jobs SET status='running' WHERE id='p1'")
    col.tick_once()
    conn.close()
    assert "live:probe-742ab201" in bus.states
    assert "live:probe" in bus.states


# ── Sammel-Targets + Diffs (PLAN-36 Stufe 36.3) ─────────────────────────────


def test_collector_job_change_publishes_collective_targets(collector):
    # Listen-Screens hören auf EIN Target: jede Job-Zustandsänderung macht
    # "jobs" + "feedstatus" dreckig (Job-Status-Kachel hängt an "jobs").
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="pending")
    col.tick_once()  # prime
    conn.execute("UPDATE jobs SET status='running' WHERE id='j1'")
    col.tick_once()
    conn.close()
    assert "jobs" in bus.states and "feedstatus" in bus.states
    assert "chart" not in bus.states  # kein Journal-INSERT → Chart bleibt ruhig


def test_collector_journal_insert_publishes_chart(collector):
    # Das Chart zählt terminale Landungen — nur ein Journal-INSERT ändert es.
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="running")
    col.tick_once()  # prime
    job_db.report_status(conn, "j1", status="complete", exit_code=0)
    col.tick_once()
    conn.close()
    assert "chart" in bus.states and "jobs" in bus.states


def test_collector_quiet_tick_publishes_nothing_collective(collector):
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="running")
    conn.close()
    col.tick_once()  # prime
    col.tick_once()  # nichts passiert
    assert bus.states == []


class _FakeRegistry:
    def __init__(self):
        self.rows: list[dict] = []

    def list(self):
        return self.rows


def test_collector_nodes_diff_fires_on_registry_change(collector):
    col, bus, root = collector
    col.registry = _FakeRegistry()
    col.tick_once()  # prime (leere Registry als Baseline)
    col.registry.rows = [{"worker": "w1", "node_id": "n1", "last_beat": 1.0,
                          "stale": False, "git_status": "trunk · clean · synced",
                          "git_user": "m.rau"}]
    col.tick_once()
    assert bus.states.count("nodes") == 1
    col.tick_once()  # unverändert → ruhig
    assert bus.states.count("nodes") == 1
    # stale-Übergang OHNE neuen Heartbeat (list() berechnet stale zeitbasiert)
    col.registry.rows = [dict(col.registry.rows[0], stale=True)]
    col.tick_once()
    assert bus.states.count("nodes") == 2


def test_collector_flags_diff_fires_feedstatus(collector, monkeypatch):
    from bibi import state
    col, bus, root = collector
    col.tick_once()  # prime (Flags-Baseline)
    assert "feedstatus" not in bus.states
    state.set_maintenance(True)
    col.tick_once()
    assert "feedstatus" in bus.states
    state.set_maintenance(False)
