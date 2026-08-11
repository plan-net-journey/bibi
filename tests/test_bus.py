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

    def publish_state(self, target, value=None):
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
    # Ein blosser Zustandswechsel ist keine Archivierung — das Target bleibt
    # ruhig, sonst waere es dasselbe wie "jobs" und truege keine eigene Aussage.
    assert "archived" not in bus.states


def test_collector_next_fire_at_change_publishes_jobs(collector):
    """#110: der Jobs-Screen zeigte ein veraltetes NEXT, waehrend der Header
    (der den Scheduler direkt abfragt) weiterlief. Ursache: der Diff verglich
    nur (status, fire) — ein Job, der in seinem Status stehen bleibt (z. B.
    weiterhin "failed", naechster Retry faellig) aber ein neues next_fire_at
    bekommt, aenderte laut Diff "nichts", obwohl die NEXT-Spalte genau das
    zeigt, was sich geaendert hat."""
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="failed")
    conn.execute("UPDATE jobs SET next_fire_at=100 WHERE id='j1'")
    col.tick_once()  # prime
    conn.execute("UPDATE jobs SET next_fire_at=200 WHERE id='j1'")  # Status bleibt "failed"
    col.tick_once()
    conn.close()
    assert "jobs" in bus.states


def test_collector_publishes_archived_when_a_run_reaches_the_journal(collector):
    """m.rau/bibi#108: die einzige Verbindung zwischen Strom und Liste.

    Der Strom traegt die Liste nicht, er stoesst sie an — wird ein Lauf
    archiviert, laedt die Lauf-Liste ihre erste Seite neu. Ohne dieses Ereignis
    bleibt das Job-Detail nach einem Lauf stehen, bis jemand neu laedt.

    Das Target hiess frueher `chart` und meinte das Landungs-Histogramm. Das
    Chart ist mit m.rau/bibi#120 entfallen, das Ereignis nicht: es feuerte
    schon immer genau bei einem Journal-INSERT. Was fehlte, war ein Name, der
    sagt, was passiert ist, statt wer frueher zugehoert hat."""
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="running")
    col.tick_once()  # prime
    job_db.report_status(conn, "j1", status="complete", exit_code=0)
    col.tick_once()
    conn.close()
    assert "archived" in bus.states and "jobs" in bus.states


def test_a_blocked_run_publishes_archived_only_when_it_is_cleared(collector):
    """Unter der Archivierungsregel A2 (m.rau/bibi#101) faellt die Archivierung
    nicht mehr mit dem Terminal-Werden zusammen: ein `killed` bleibt im Slot
    stehen, bis ein Mensch ihn abraeumt. Das Ereignis folgt dem — sonst laedt
    die Lauf-Liste zum falschen Zeitpunkt nach, naemlich dann, wenn dort noch
    nichts Neues zu sehen ist."""
    col, bus, root = collector
    conn = job_db.connect()
    _insert_job(conn, status="running")
    col.tick_once()  # prime

    job_db.report_status(conn, "j1", status="killed", reason="by_user")
    col.tick_once()
    assert "archived" not in bus.states  # blockiert, noch nichts im Journal

    job_db.start_now(conn, "j1")
    col.tick_once()
    conn.close()
    assert "archived" in bus.states


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


# ── #79: Ereignisse tragen den Wert, den der Diff ohnehin gelesen hat ────────
#
# Von zwoelf Ereignisarten trugen elf keine Nutzlast: der Bus meldete „Region
# dreckig", die Region holte ihr HTML neu. Dabei lag der Wert bereits vor —
# `_diff_scheduler_jobs()` liest je Slug `(status, fire)`, vergleicht mit dem
# Vorwert und **wirft den neuen Wert dann weg**, um „dreckig" zu melden.
#
# Die Regel dahinter: *trage den Wert für das, was du ohnehin vergleichst —
# füge keinen Vergleich hinzu, um einen Wert tragen zu können.* Die erste
# Hälfte kostet nichts, die zweite waere der Firehose, den `bus.py`
# ausdruecklich ausschliesst.


def test_a_state_event_can_carry_the_compared_value():
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        bus.publish_state("live:a", {"status": "running", "fire": 3})
        return await bus.wait(sub, timeout=1.0)
    assert asyncio.run(run()) == [
        {"t": "state", "target": "live:a", "v": {"status": "running", "fire": 3}}]


def test_an_event_without_a_value_looks_exactly_as_before():
    """Rein additiv: wo kein Wert vorliegt, entsteht auch kein Feld.

    Das ist die Bedingung, unter der ein Empfaenger, der den Wert ignoriert,
    unveraendert funktioniert — er sieht dieselbe Nachricht wie bisher."""
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        bus.publish_state("feedstatus")
        return await bus.wait(sub, timeout=1.0)
    assert asyncio.run(run()) == [{"t": "state", "target": "feedstatus"}]


def test_the_newest_value_wins_and_the_target_still_coalesces():
    """Koaleszenz bleibt Koaleszenz — zwei Wechsel desselben Slugs sind eine
    Nachricht, und zwar die juengste. Historienfreiheit ist die Zusage des
    Busses, und ein mitgefuehrter Wert darf sie nicht aufweichen."""
    async def run():
        bus = Bus()
        sub = bus.subscribe()
        bus.publish_state("live:a", {"status": "pending"})
        bus.publish_state("live:a", {"status": "running"})
        return await bus.wait(sub, timeout=1.0)
    events = asyncio.run(run())
    assert len(events) == 1
    assert events[0]["v"] == {"status": "running"}


def test_the_scheduler_diff_carries_status_and_fire():
    """Der Wert, den `_diff_scheduler_jobs()` ohnehin in der Hand hat."""
    gesehen: list[tuple] = []

    class _B:
        def publish_state(self, target, value=None):
            gesehen.append((target, value))

    c = Collector(_B(), registry=None)
    c._primed = True
    c._sched_jobs_snapshot = {"a": ("pending", 1)}
    c._fetch_scheduler_jobs = lambda: [{"slug": "a", "row_status": "running", "fire": 2}]
    c._diff_scheduler_jobs()
    assert ("live:a", {"status": "running", "fire": 2}) in gesehen
    # Das Sammel-Target bleibt wertlos: „irgendwas an der Liste" ist keine
    # Aussage ueber einen einzelnen Wert.
    assert ("jobs", None) in gesehen


def test_carrying_a_value_adds_no_fetch_and_no_comparison():
    """Die Gegenrichtung, und die eigentliche Zusage von #79.

    Ein Wert, fuer den erst jemand nachsehen muesste, ist kein Beifang mehr,
    sondern ein zweiter Poll. Insbesondere bleibt die Runtime draussen: sie
    steht in keinem Fingerabdruck, und sie hineinzunehmen hiesse, jeden Tick
    jedes laufenden Jobs zu melden."""
    aufrufe = {"n": 0}

    class _B:
        def publish_state(self, target, value=None):
            pass

    c = Collector(_B(), registry=None)
    c._primed = True
    c._sched_jobs_snapshot = {"a": ("pending", 1)}

    def _fetch():
        aufrufe["n"] += 1
        return [{"slug": "a", "row_status": "running", "fire": 2, "runtime_p90": 12.0}]
    c._fetch_scheduler_jobs = _fetch
    c._diff_scheduler_jobs()
    assert aufrufe["n"] == 1
    assert c._sched_jobs_snapshot == {"a": ("running", 2)}   # Fingerabdruck unveraendert


# ── #131: ein Slot-Zustandswechsel muss die Lauf-Liste bewegen ───────────────
#
# **Befund aus dem Akzeptanz-Durchgang zu `v0.8.0`:** die Kachel meldet
# `running · 28s`, waehrend die Zeile darunter eine Minute lang `starting`
# behauptet. Beide zeigen denselben Lauf.
#
# Die Ursache ist eine Asymmetrie zwischen zwei Diffs, die dasselbe Ereignis
# verarbeiten. Der **lokale** Pfad (`tick_once()`) publiziert bei jedem
# Statuswechsel beide Ziele — `_publish_live()` fuer die Kachel und
# `_publish_journal()` fuer die Lauf-Liste, mit ausdruecklicher Begruendung im
# Kommentar dort. Der **Scheduler**-Pfad (`_diff_scheduler_jobs()`) publiziert
# nur `live:<slug>` und das Sammel-Target `jobs`. Auf einem Client laufen die
# Jobs beim Scheduler — dort greift also genau der Pfad, dem das Ziel fehlt.
#
# **Diese Tests pruefen die Wirkung, nicht die Verdrahtung.** Ein Test, der
# `data-bus="journal:<slug>"` im Markup sucht, waere auch vor dem Fix gruen:
# das Attribut steht seit #43 dort, es feuert nur niemand darauf.


def test_a_scheduler_slot_change_moves_the_run_list():
    """`starting → running` beim Scheduler meldet auch die Lauf-Liste dreckig.

    Der Uebergang ohne Journal-INSERT ist der Fall, um den es geht: archiviert
    wird dabei nichts, das `archived`-Target feuert also zu Recht nicht — und
    bis #131 feuerte deshalb ueberhaupt nichts fuer die Liste."""
    gesehen: list[tuple] = []

    class _B:
        def publish_state(self, target, value=None):
            gesehen.append((target, value))

    c = Collector(_B(), registry=None)
    c._primed = True
    c._sched_jobs_snapshot = {"a": ("starting", 1)}
    c._fetch_scheduler_jobs = lambda: [{"slug": "a", "row_status": "running", "fire": 1}]
    c._diff_scheduler_jobs()
    ziele = [t for t, _ in gesehen]
    assert "live:a" in ziele          # die Kachel — schon vor #131 richtig
    assert "journal:a" in ziele       # die Zeile darunter — der Fehler


def test_the_run_list_target_carries_no_value():
    """`journal:<slug>` bleibt wertlos, und das ist kein Versehen.

    `live:<slug>` traegt `(status, fire)`, weil die Kachel genau diese zwei
    Felder zeigt. Die Lauf-Liste zeigt eine ganze Zeile — Beginn, Runtime,
    Ausgang —, und die steht in keinem Fingerabdruck. Ein Wert daran waere die
    Zusage, etwas mitzuliefern, das der Diff gar nicht gelesen hat."""
    gesehen: list[tuple] = []

    class _B:
        def publish_state(self, target, value=None):
            gesehen.append((target, value))

    c = Collector(_B(), registry=None)
    c._primed = True
    c._sched_jobs_snapshot = {"a": ("starting", 1)}
    c._fetch_scheduler_jobs = lambda: [{"slug": "a", "row_status": "running", "fire": 1}]
    c._diff_scheduler_jobs()
    assert ("journal:a", None) in gesehen


def test_an_unchanged_slot_moves_nothing():
    """Die Gegenprobe, ohne die der Fix ein Firehose waere.

    `_diff_scheduler_jobs()` laeuft im Poll-Rueckfall alle paar Sekunden. Ein
    zusaetzliches Ziel je Tick — statt je Wechsel — liesse die Lauf-Liste
    dauernd nachladen und naehme ihr Scroll-Position und Faltzustand."""
    gesehen: list[tuple] = []

    class _B:
        def publish_state(self, target, value=None):
            gesehen.append((target, value))

    c = Collector(_B(), registry=None)
    c._primed = True
    c._sched_jobs_snapshot = {"a": ("running", 1)}
    c._fetch_scheduler_jobs = lambda: [{"slug": "a", "row_status": "running", "fire": 1}]
    c._diff_scheduler_jobs()
    assert gesehen == []
