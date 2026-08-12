"""Lauf-Attribute frieren bei START ein — ein Snapshot je ``run_id`` (#129).

Bis hierher folgten vier Quellen derselben Frage *„mit welcher Konfiguration
läuft dieser Lauf"* — die MD, die ``jobs``-Zeile, ``journal.snapshot`` und die
Prozess-Env des Wrappers — und nur die letzte bestimmte, was tatsächlich
passierte. Die anderen drei behaupteten es, ohne mit ihr zwangsläufig einig zu
sein; ``worker.py`` dokumentiert eine so entstandene Divergenz vom 2026-07-14.

Die Regel, die diese Tests festhalten: **ab der Reservierung gilt
``jobs.run_snapshot``.** Die ``jobs``-Zeile bleibt, was sie war — eine
Projektion der MD, die jedem Rescan folgt.

Die Prüffrage für jede Fundstelle lautet *„beschreibt diese Aussage den Lauf
oder den Job?"*. ``schedule``, ``at_iso`` und ``next_fire_at`` beschreiben den
**nächsten** Lauf und bleiben deshalb bewusst an der Zeile — läse der Scheduler
sie aus dem Snapshot, feuerte ein wiederkehrender Job nie wieder.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.schedule import parser


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _md(slug: str, **fm) -> parser.ParseResult:
    """Eine Schedule-MD als ParseResult — der Weg, auf dem ein Rescan schreibt."""
    zeilen = "".join(f"{k}: {v}\n" for k, v in fm.items())
    pr = parser.parse_text(
        f'---\nslug: {slug}\nschedule: "0 * * * *"\njob: echo hi\n{zeilen}---\n',
        schedule_ref=f"case/x/{slug}.md", path=Path(f"case/x/{slug}.md"),
    )
    assert pr.is_ok, pr.error
    return pr


def _job(conn, slug: str = "a", **fm) -> str:
    """Job anlegen und fällig stellen (der cron-Termin läge sonst in der Zukunft)."""
    jid = job_db.upsert_schedule(conn, _md(slug, **fm), 1000.0)
    conn.execute("UPDATE jobs SET next_fire_at=0 WHERE id=?", (jid,))
    return jid


def _faellig(conn, jid: str) -> None:
    conn.execute("UPDATE jobs SET next_fire_at=0 WHERE id=?", (jid,))


def _startup_job(conn, slug: str = "s", **fm) -> str:
    """Ein ``schedule: startup``-Job — der eine Trigger, den ``fire_startup()``
    per direktem SQL wieder fällig stellt, ohne ``report_status()`` zu rufen."""
    zeilen = "".join(f"{k}: {v}\n" for k, v in fm.items())
    pr = parser.parse_text(
        f"---\nslug: {slug}\nschedule: startup\njob: echo hi\n{zeilen}---\n",
        schedule_ref=f"case/x/{slug}.md", path=Path(f"case/x/{slug}.md"),
    )
    assert pr.is_ok, pr.error
    return job_db.upsert_schedule(conn, pr, 1000.0)


# ── 1: der Snapshot entsteht bei START ───────────────────────────────────────


def test_reservation_writes_the_run_snapshot(conn):
    jid = _job(conn, "a", attempts=5, wall_time=900)
    job_db.reserve_next(conn)
    row = conn.execute("SELECT run_snapshot FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["run_snapshot"] is not None
    snap = json.loads(row["run_snapshot"])
    assert snap["attempts"] == 5
    assert snap["wall_time"] == 900


def test_rescan_during_the_run_leaves_the_run_snapshot_alone(conn):
    """Der Rot-Schritt aus dem Ticket: MD ändern, Rescan, Snapshot prüfen.

    ``upsert_schedule()`` schreibt 26 Spec-Spalten der Zeile neu, ohne Rücksicht
    auf einen laufenden Lauf. Das soll so bleiben — die Zeile *ist* die
    Projektion. Nur darf der Lauf davon nichts mitbekommen.
    """
    jid = _job(conn, "a", attempts=5)
    job_db.reserve_next(conn)
    job_db.upsert_schedule(conn, _md("a", attempts=9), 2000.0)
    zeile = conn.execute(
        "SELECT attempts, run_snapshot FROM jobs WHERE id=?", (jid,)).fetchone()
    assert zeile["attempts"] == 9                              # die Projektion folgt der MD
    assert json.loads(zeile["run_snapshot"])["attempts"] == 5  # der Lauf nicht


# ── 2: ein Snapshot je run_id, nicht je Versuch ──────────────────────────────


def test_a_retry_keeps_the_configuration_of_the_first_attempt(conn):
    """``reserve_next()`` erhöht ``fire`` nur aus ``complete`` — ein Retry ist
    derselbe Lauf. Schriebe die Reservierung jedes Mal neu, trüge ein Lauf über
    seine Versuche hinweg wechselnde Konfiguration; bei ``attempts: 3`` plus
    Backoff sind das Stunden, kein Randfall."""
    jid = _job(conn, "a", attempts=5)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed", next_fire_at=0)
    job_db.upsert_schedule(conn, _md("a", attempts=9), 2000.0)
    _faellig(conn, jid)
    job_db.reserve_next(conn)  # Versuch 2 — derselbe run_id
    row = conn.execute("SELECT fire, run_snapshot FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["fire"] == 0
    assert json.loads(row["run_snapshot"])["attempts"] == 5


def test_a_new_run_id_gets_a_fresh_snapshot(conn):
    """Die Gegenprobe zum Retry: aus ``complete`` heraus beginnt ein neuer Lauf,
    und der nimmt die aktuelle Konfiguration mit. Ohne diesen Test wäre auch ein
    Snapshot grün, der nach dem ersten Schreiben für immer stehenbleibt."""
    jid = _job(conn, "a", attempts=5)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    job_db.upsert_schedule(conn, _md("a", attempts=9), 2000.0)
    _faellig(conn, jid)
    job_db.reserve_next(conn)  # lazy Rearm — fire+1, also ein neuer Lauf
    row = conn.execute("SELECT fire, run_snapshot FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["fire"] == 1
    assert json.loads(row["run_snapshot"])["attempts"] == 9


# ── 2b: ein Lauf, der über `pending` beginnt, ist auch ein neuer Lauf ────────
#
# Live-Befund 2026-08-12 (Countdown-Testjob auf sarasate): ein Job stand mit
# `attempts: 3` und `defer_time: 15` in der Zeile und lief trotzdem mit
# `attempts=0` und dem 360-s-Default — er fiel beim ersten `raise` sofort auf
# `error` statt auf `failed`. Sein Snapshot trug `fire: 0`, stammte also vom
# allerersten Lauf und war nie erneuert worden.
#
# Die Erneuerung hing an `chosen["status"] == "complete"`. Ein terminaler Job
# geht über RESET/START aber nach **pending** (`start_now()` →
# `report_status(pending)`), nie über `complete` — die Bedingung greift dort
# nie. `report_status()`s PENDING-Zweig wiederum nullt seit dem 2026-07-03
# started_at/finished_at/exit_code/output_ref mit der Begründung, „der
# Lauf-Snapshot des vorigen Zyklus" dürfe nicht stehen bleiben; die Spalte
# `run_snapshot` kam später (#129) und wurde dort nicht nachgezogen.


def test_reset_to_pending_clears_the_run_snapshot(conn):
    """Was der PENDING-Zweig für die Statusfelder tut, muss er auch für die
    Spalte tun, die dieselbe Frage beantwortet — sonst zeigt eine Zeile ohne
    Lauf weiter die Konfiguration eines abgeschlossenen."""
    jid = _job(conn, "a", attempts=5)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    assert job_db.report_status(conn, jid, status="pending") == "ok"
    row = conn.execute("SELECT run_snapshot FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["run_snapshot"] is None


def test_a_run_after_reset_gets_a_fresh_snapshot(conn):
    """Der Live-Fall als Test: Job auf ``error``, MD korrigiert, START — der
    nächste Lauf muss die korrigierten Werte tragen. Ohne den Fix läuft er mit
    denen des ersten Laufs weiter, und zwar für immer: aus ``error`` führt kein
    Weg zurück nach ``complete``."""
    jid = _job(conn, "a", attempts=5)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed")
    job_db.report_status(conn, jid, status="error")
    job_db.upsert_schedule(conn, _md("a", attempts=9), 2000.0)
    assert job_db.start_now(conn, jid) == "ok"
    job_db.reserve_next(conn)
    row = conn.execute("SELECT run_snapshot FROM jobs WHERE id=?", (jid,)).fetchone()
    assert json.loads(row["run_snapshot"])["attempts"] == 9


def test_a_startup_rearm_gets_a_fresh_snapshot(conn):
    """``fire_startup()`` setzt per direktem SQL auf ``pending`` und erhöht
    ``fire`` — ein neuer Lauf, der ``report_status()`` nie berührt. Ein Fix, der
    nur dort nullte, ließe genau diese Job-Klasse ihren allersten Snapshot über
    jeden Daemon-Neustart hinweg mitschleppen."""
    jid = _startup_job(conn, "s", attempts=5)
    _faellig(conn, jid)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    _startup_job(conn, "s", attempts=9)   # Rescan: die MD hat sich geändert
    assert job_db.fire_startup(conn) == 1
    job_db.reserve_next(conn)
    row = conn.execute("SELECT run_snapshot FROM jobs WHERE id=?", (jid,)).fetchone()
    assert json.loads(row["run_snapshot"])["attempts"] == 9


# ── 3: reservation_view() liest beim Retry aus dem Snapshot ──────────────────


def test_reservation_view_serves_the_frozen_values_on_a_retry(conn):
    """Folgt zwingend aus dem Snapshot je ``run_id``: ohne diesen Schritt liefe
    Versuch 2 mit anderen Werten als denen, die der Snapshot über ihn aussagt —
    dieselbe Lüge, nur an einer neuen Stelle."""
    jid = _job(conn, "a", attempts=5, wall_time=900)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed", next_fire_at=0)
    job_db.upsert_schedule(conn, _md("a", attempts=9, wall_time=60), 2000.0)
    _faellig(conn, jid)
    res = job_db.reserve_next(conn)
    assert res is not None
    assert res["attempts"] == 5   # der Wrapper rechnet den Backoff des Laufs
    assert res["wall_time"] == 900


def test_reservation_view_serves_the_current_values_for_a_new_run(conn):
    """Gegenprobe: ein neuer Lauf bekommt die neue Konfiguration. Sonst wäre ein
    ``reservation_view``, das stur den ältesten Snapshot liest, ebenfalls grün."""
    jid = _job(conn, "a", attempts=5)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    job_db.upsert_schedule(conn, _md("a", attempts=9), 2000.0)
    _faellig(conn, jid)
    res = job_db.reserve_next(conn)
    assert res is not None
    assert res["attempts"] == 9


# ── 4: die Leser umstellen ───────────────────────────────────────────────────


def test_the_live_slot_reports_the_frozen_silence_timeout(conn):
    """Die Uhr auf dem Screen (#76) lief bisher gegen ein Ziel, das der Wrapper
    nicht kennt: die Dauer kam frisch aus der Zeile, der Zeitpunkt vom Lauf."""
    jid = _job(conn, "a", silence_timeout=7200)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.upsert_schedule(conn, _md("a", silence_timeout=60), 2000.0)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    assert job_db.schedule_view(row)["silence_timeout"] == 7200


def test_sweep_measures_against_the_frozen_defer_max(conn):
    """``sweep()`` entscheidet mit ``defer_max``, wann ein ``deferred`` Zyklus zu
    ``inactive`` wird — eine Aussage über den Lauf, aus einer Job-Quelle."""
    jid = _job(conn, "a", defer_max=3600)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    now = time.time()
    job_db.report_status(conn, jid, status="deferred",
                         next_fire_at=now + 60, now=now - 1800)
    job_db.upsert_schedule(conn, _md("a", defer_max=60), 2000.0)  # MD verkürzt die Frist
    job_db.sweep(conn, now=now)
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "deferred"  # 1800 s < 3600 s — die Frist des Laufs läuft


def test_sweep_still_expires_a_deferred_cycle_past_its_frozen_limit(conn):
    """Gegenprobe: der eingefrorene Wert bremst nicht, er gilt nur. Ohne sie wäre
    ein ``sweep``, der ``defer_max`` gar nicht mehr auswertet, ebenfalls grün."""
    jid = _job(conn, "a", defer_max=60)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    now = time.time()
    job_db.report_status(conn, jid, status="deferred",
                         next_fire_at=now + 60, now=now - 1800)
    job_db.sweep(conn, now=now)
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "inactive"


# ── 5: der Archiv-Snapshot wird ersetzt, nicht ergänzt ───────────────────────


def test_the_journal_keeps_the_start_snapshot_not_the_archive_one(conn):
    """Ein Wert, an zwei Orten abgelegt, nie neu berechnet. Damit fällt die
    Auskunft weg, ob die MD *während* des Laufs geändert wurde — bewusst
    (m.rau, 2026-08-11): ein Diagnose-Sonderfall, dessen verständliche
    Darstellung mehr kostet, als er einbringt."""
    jid = _job(conn, "a", attempts=5)
    job_db.reserve_next(conn)
    job_db.report_status(conn, jid, status="running")
    job_db.upsert_schedule(conn, _md("a", attempts=9), 2000.0)
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    entry_id = job_db.list_journal(conn, slug="a")[0]["id"]
    snap = json.loads(job_db.get_journal(conn, entry_id)["snapshot"])
    assert snap["attempts"] == 5


# ── 6: hitl_timeout rausräumen ───────────────────────────────────────────────


def test_hitl_timeout_is_gone_from_schema_and_from_the_attribute_page(conn):
    """Karteileiche: der Parser hat sie am 2026-07-04 mit ``silence_timeout``
    zusammengelegt, ``_spec_columns()`` kennt sie nicht — die Attributseite
    führte trotzdem ein Feld, das es fachlich nicht mehr gibt."""
    from bibi.controller import render
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "hitl_timeout" not in spalten
    assert "hitl_timeout" not in render._ATTR_FELDER


# ── Der Rückweg: die Migration ist additiv ───────────────────────────────────


def test_an_older_engine_still_opens_a_migrated_db(tmp_path: Path):
    """Die harte Abbruchgrenze des Verfahrens lautet *„käme ich nach dieser
    Änderung mit einem Pin zurück?"*. ``_ensure_schema()`` kehrt bei einer
    neueren DB ohne Schreibzugriff zurück; der Test hält fest, dass das auch
    nach dieser Migration gilt und die neue Spalte nullable ist."""
    p = tmp_path / "jobs.sqlite"
    c = job_db.connect(p)
    c.execute(f"PRAGMA user_version = {job_db.SCHEMA_VERSION + 1}")  # „Zukunfts-DB"
    c.commit()
    c.close()
    c2 = job_db.connect(p)  # eine ältere Engine würde genau das tun
    assert c2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION + 1
    spalte = [r for r in c2.execute("PRAGMA table_info(jobs)") if r[1] == "run_snapshot"]
    assert spalte and spalte[0][3] == 0  # notnull == 0
    c2.close()
