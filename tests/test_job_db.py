"""Job-DB: Schema/Migrationen, CRUD, Rescan (DESIGN §5.4/§1.4; PLAN-3 §3.1)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bibi.daemon import job_db


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_schema_version_set(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION


def test_wal_mode(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_tables_exist(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "journal"} <= names


def test_connect_idempotent(tmp_path: Path):
    p = tmp_path / "jobs.sqlite"
    job_db.connect(p).close()
    c2 = job_db.connect(p)  # zweiter Connect darf nicht crashen
    assert c2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION


# ── compute_next_fire (§5.2) ─────────────────────────────────────────────────


def test_next_fire_now_is_now():
    from bibi.schedule.models import Kind, ScheduleSpec
    s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="echo", schedule="now")
    assert job_db.compute_next_fire(s, now=1000.0) == 1000.0


def test_next_fire_never_and_startup_are_none():
    from bibi.schedule.models import Kind, ScheduleSpec
    for sched in ("never", "startup"):
        s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="e", schedule=sched)
        assert job_db.compute_next_fire(s, now=1000.0) is None


def test_next_fire_cron_advances():
    from bibi.schedule.models import Kind, ScheduleSpec
    s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="e", schedule="0 9 * * *")
    nf = job_db.compute_next_fire(s, now=1000.0)
    assert nf is not None and nf > 1000.0


def test_next_fire_at_parsed():
    from bibi.schedule.models import Kind, ScheduleSpec
    s = ScheduleSpec(slug="x", kind=Kind.JOB, payload="e", at="2099-01-01T00:00:00")
    nf = job_db.compute_next_fire(s, now=1000.0)
    assert nf is not None and nf > 1000.0


# ── rescan ────────────────────────────────────────────────────────────────


def test_rescan_inserts_then_lists(conn, tmp_path: Path):
    _write(tmp_path / "case" / "hello" / "README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert res["inserted"] == 1 and res["updated"] == 0 and res["removed"] == 0
    jobs = job_db.list_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["slug"] == "hello"
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["kind"] == "job"
    assert len(jobs[0]["id"]) == 8  # Hash-ID (§4.4)


def test_rescan_update_preserves_id_and_status(conn, tmp_path: Path):
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: now\njob: "echo a"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = job_db.list_jobs(conn)[0]["id"]
    # Status simuliert weiter (running) — rescan darf ihn nicht zurücksetzen
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,)); conn.commit()
    _write(md, '---\nschedule: now\njob: "echo b"\npriority: 5\n---\n')
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert res["updated"] == 1 and res["inserted"] == 0
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["payload"] == "echo b"    # Spec aktualisiert
    assert row["priority"] == 5
    assert row["status"] == "running"    # Live-Status erhalten
    assert job_db.list_jobs(conn)[0]["id"] == jid  # gleiche ID


def test_rescan_preserves_next_fire_at_while_failed(conn, tmp_path: Path):
    # User-Feedback 2026-07-01: next_fire_at bei `failed` ist der 30s-Backoff-
    # Timer von Worker/Sweep, kein aus dem Schedule ableitbares Datum. Rescan
    # (z.B. periodischer Sync) darf ihn nicht auf den nächsten (fernen) Cron-Tick
    # überschreiben — sonst retryt/eskaliert der Job nie.
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: "05 */2 * * *"\njob: "echo a"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = job_db.list_jobs(conn)[0]["id"]
    backoff_deadline = time.time() + 30
    conn.execute("UPDATE jobs SET status='failed', next_fire_at=? WHERE id=?",
                (backoff_deadline, jid))
    conn.commit()
    job_db.rescan(conn, vault_root=tmp_path / "case")  # z.B. periodischer Sync
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] == backoff_deadline


def test_rescan_preserves_next_fire_at_while_complete(conn, tmp_path: Path):
    # Lazy Rearm (§5.2): next_fire_at eines complete-Jobs ist der Timer bis zum
    # nächsten Dispatch — ein Rescan darf ihn nicht auf einen anderen Cron-Tick
    # verschieben (derselbe Bug wie bei `failed`/`deferred`, s.o.).
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: "05 */2 * * *"\njob: "echo a"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = job_db.list_jobs(conn)[0]["id"]
    next_tick = time.time() + 60
    conn.execute("UPDATE jobs SET status='complete', next_fire_at=? WHERE id=?",
                (next_tick, jid))
    conn.commit()
    job_db.rescan(conn, vault_root=tmp_path / "case")  # z.B. periodischer Sync
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] == next_tick


def test_rescan_recomputes_next_fire_at_when_stuck_at_none_while_complete(conn, tmp_path: Path):
    # Real beobachtet 2026-07-05 (gmail-transfer): next_fire_at eines
    # complete-Jobs kann NULL werden (z.B. manueller Start traf einen
    # Zwischenstand). Die Preserve-Regel (s.o.) darf das NICHT für immer
    # einfrieren — ohne echten Timer muss ein Rescan neu rechnen dürfen,
    # sonst verlangt reserve_next() ("next_fire_at IS NOT NULL") auf ewig
    # ins Leere.
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: "05 */2 * * *"\njob: "echo a"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = job_db.list_jobs(conn)[0]["id"]
    conn.execute("UPDATE jobs SET status='complete', next_fire_at=NULL WHERE id=?", (jid,))
    conn.commit()
    job_db.rescan(conn, vault_root=tmp_path / "case")  # z.B. periodischer Sync
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] is not None
    assert row["next_fire_at"] > time.time()


def test_rescan_deactivates_vanished_instead_of_deleting(conn, tmp_path: Path):
    # PLAN-14 Stufe 14.5: die Zeile bleibt (Journal-Historie erreichbar), nur
    # active=0 statt DELETE — ersetzt den früheren test_rescan_removes_vanished.
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: now\njob: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = conn.execute("SELECT id FROM jobs WHERE slug='hello'").fetchone()["id"]
    md.unlink()
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert res["removed"] == 1
    row = conn.execute("SELECT active FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row is not None and row["active"] == 0
    assert job_db.list_jobs(conn) == []  # list_jobs blendet inaktive aus (Root-Bänder)


def test_rescan_reactivates_rediscovered_slug(conn, tmp_path: Path):
    md = tmp_path / "case" / "hello" / "README.md"
    _write(md, '---\nschedule: now\njob: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    md.unlink()
    job_db.rescan(conn, vault_root=tmp_path / "case")
    _write(md, '---\nschedule: now\njob: "x"\n---\n')  # MD kommt zurück
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jobs = job_db.list_jobs(conn)
    assert len(jobs) == 1 and jobs[0]["slug"] == "hello"


def test_rescan_reports_errors_and_collisions(conn, tmp_path: Path):
    _write(tmp_path / "case" / "bad.md", '---\nschedule: "broken"\njob: "x"\n---\n')
    _write(tmp_path / "case" / "a.md", '---\nslug: dup\nschedule: now\njob: "x"\n---\n')
    _write(tmp_path / "case" / "b.md", '---\nslug: dup\nschedule: now\njob: "y"\n---\n')
    res = job_db.rescan(conn, vault_root=tmp_path / "case")
    assert any("cron" in e["error"].lower() for e in res["errors"])
    assert res["collisions"][0]["slug"] == "dup"
    assert job_db.list_jobs(conn) == []  # weder Fehler noch Kollisionen eingefügt


def test_list_jobs_status_filter_and_get(conn, tmp_path: Path):
    _write(tmp_path / "case" / "a" / "README.md", '---\nschedule: now\njob: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = job_db.list_jobs(conn, status="pending")[0]["id"]
    assert job_db.list_jobs(conn, status="running") == []
    assert job_db.get_job(conn, jid)["slug"] == "a"
    assert job_db.get_job(conn, "nope") is None


def test_schedule_view_trigger_and_status(conn, tmp_path: Path):
    _write(tmp_path / "case" / "daily.md", '---\nschedule: "0 9 * * *"\njob: "claude: x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    sched = job_db.list_schedules(conn)
    assert sched[0]["slug"] == "daily"
    assert sched[0]["trigger"] == "0 9 * * *"
    assert sched[0]["kind"] == "job"
    assert sched[0]["last_status"] == "pending"   # nie gelaufen → Zeilen-Status


def test_schedule_view_exposes_payload_and_app_port(conn, tmp_path: Path):
    # kind ist seit PLAN-10 immer "job" (Unified Job Model) — payload/app_port
    # sind die einzige Quelle, um claude-/app-artige Schedules zu unterscheiden
    # (FE-Typ-Filter, §C.3).
    _write(tmp_path / "case" / "app.md",
          '---\nschedule: never\njob: "python3 app.py"\napp_port: 9100\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    sched = next(s for s in job_db.list_schedules(conn) if s["slug"] == "app")
    assert sched["payload"] == "python3 app.py"
    assert sched["app_port"] == 9100


# ── PLAN-14 Stufe 14.6 — Schedules-Übersicht: active-Flag + Journal-Phantome ──


def test_schedule_view_exposes_active_flag(conn, tmp_path: Path):
    _write(tmp_path / "case" / "a.md", '---\nschedule: now\njob: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    assert job_db.list_schedules(conn)[0]["active"] is True


def test_schedule_view_active_false_after_deactivation(conn, tmp_path: Path):
    md = tmp_path / "case" / "a.md"
    _write(md, '---\nschedule: now\njob: "x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    md.unlink()
    job_db.rescan(conn, vault_root=tmp_path / "case")
    assert job_db.list_schedules(conn)[0]["active"] is False


def test_list_schedules_includes_journal_only_phantom_entries(conn):
    # Simuliert eine Alt-DB (vor Stufe 14.5): journal-Zeile domain='scheduled'
    # ohne zugehörige jobs-Zeile (früher durch remove_slugs() gelöscht statt
    # deaktiviert).
    conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, finished_at, "
        "archived_at, domain) VALUES ('ghost:1','ghost','job','complete', 2.0, 2.0, 'scheduled')")
    items = job_db.list_schedules(conn)
    ghost = next(s for s in items if s["slug"] == "ghost")
    assert ghost["active"] is None
    assert ghost["last_status"] == "complete"


def test_list_schedules_excludes_local_domain_phantom_entries(conn):
    # /run-lokale Läufe sind nie Schedules gewesen — kein Phantom-Eintrag.
    job_db.write_local_journal(
        conn, run_id="adhoc:1", slug="adhoc", kind="job", status="complete",
        exit_code=0, output_ref=None, host="h", worker="w",
        started_at=1.0, finished_at=2.0)
    items = job_db.list_schedules(conn)
    assert not any(s["slug"] == "adhoc" for s in items)


def test_run_id_for_includes_job_id_suffix():
    assert job_db.run_id_for("Witz", "bf63ab4f", 12) == "Witz:12:bf63ab4f"


def test_journal_entries_from_different_job_incarnations_do_not_collide(conn, tmp_path: Path):
    # User-Feedback 2026-07-01 (live reproduziert): `fire` startet bei jeder neuen
    # Job-Zeile wieder bei 0 — ein heutiger Lauf traf denselben run_id wie ein
    # Jahre alter Lauf einer früheren Job-Inkarnation desselben Slugs (z.B. nach
    # Maschinenwechsel/DB-Reset). Ohne den job_id-Suffix in run_id_for() wurde
    # der echte neue Journal-Eintrag stillschweigend verworfen (Dedup-Kollision)
    # und der zugehörige Output-Pfad auf Platte hätte den alten Lauf überschrieben.
    _write(tmp_path / "case" / "flaky.md", '---\nschedule: never\njob: "echo x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    old_jid = conn.execute("SELECT id FROM jobs WHERE slug='flaky'").fetchone()["id"]

    # Alte Job-Inkarnation erreicht fire=3 und schließt ab (echte Historie).
    conn.execute("UPDATE jobs SET fire=3 WHERE id=?", (old_jid,))
    job_db.report_status(conn, old_jid, status="running")
    job_db.report_status(conn, old_jid, status="complete", now=1000.0)

    # Job-Zeile wird gelöscht + neu angelegt (neue job_id, fire startet wieder bei 0).
    conn.execute("DELETE FROM jobs WHERE id=?", (old_jid,))
    job_db.rescan(conn, vault_root=tmp_path / "case")
    new_jid = conn.execute("SELECT id FROM jobs WHERE slug='flaky'").fetchone()["id"]
    assert new_jid != old_jid
    conn.execute("UPDATE jobs SET fire=3 WHERE id=?", (new_jid,))  # erreicht denselben fire-Wert
    job_db.report_status(conn, new_jid, status="running")
    job_db.report_status(conn, new_jid, status="complete", now=2000.0)

    rows = [r for r in job_db.list_journal(conn) if r["slug"] == "flaky"]
    assert len(rows) == 2  # beide Abschlüsse landen im Journal, keiner wird verschluckt
    assert len({r["run_id"] for r in rows}) == 2  # unterschiedliche run_ids (job_id-Suffix)


def test_schedule_list_status_is_last_run_when_complete_and_idle(conn, tmp_path: Path):
    # Lazy Rearm (§5.2): ein wiederkehrender Job bleibt nach `complete` sichtbar
    # `complete`, bis reserve_next() ihn beim fälligen Tick selbst redispatcht —
    # row_status und last_status stimmen hier also überein.
    _write(tmp_path / "case" / "rec.md", '---\nschedule: "0 9 * * *"\njob: "echo x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    conn.execute("UPDATE jobs SET next_fire_at=1.0 WHERE slug='rec'")  # fällig machen
    res = job_db.reserve_next(conn, worker="w", host="h")
    job_db.report_status(conn, res["id"], status="complete", exit_code=0,
                         branch="agent/rec", commit_sha="a" * 40)
    sched = next(s for s in job_db.list_schedules(conn) if s["slug"] == "rec")
    assert sched["row_status"] == "complete"     # kein Sofort-Rearm mehr
    assert sched["last_status"] == "complete"    # letzter Lauf: complete
    assert sched["last_run_at"] is not None
    journal_id = conn.execute(
        "SELECT id FROM journal WHERE slug='rec'").fetchone()["id"]
    assert sched["last_run_id"] == journal_id    # Ziel für "Lauf Details"-Link


def test_schedule_list_status_shows_live_failed_not_stale_journal_error(conn, tmp_path: Path):
    # User-Feedback 2026-07-01 (live reproduziert): ein Job mitten im neuen
    # failed-Retry zeigte in der Schedule-Liste weiterhin das "error" vom
    # vorherigen, bereits abgeschlossenen Zyklus — weil nur `running` als
    # "live" galt. failed/awaiting/deferred brauchen dieselbe Behandlung.
    _write(tmp_path / "case" / "flaky.md", '---\nschedule: never\njob: "exit 1"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = conn.execute("SELECT id FROM jobs WHERE slug='flaky'").fetchone()["id"]

    # Erster Zyklus: erschöpft zu error (echte, abgeschlossene Journal-Zeile).
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed")
    job_db.report_status(conn, jid, status="error")

    # Neuer Zyklus (RESET + Dispatch): running -> failed, NICHT weiter zu error.
    job_db.report_status(conn, jid, status="pending")
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed")

    sched = next(s for s in job_db.list_schedules(conn) if s["slug"] == "flaky")
    assert sched["row_status"] == "failed"
    assert sched["last_status"] == "failed"   # nicht das alte "error" aus dem Journal


def test_schedule_list_running_shows_started_at_not_dash(conn, tmp_path: Path):
    # User-Feedback 2026-07-01: "letzter / seit" muss bei running die Laufzeit
    # zeigen (started_at) statt "—" — finished_at ist bei einem laufenden Job
    # zwangsläufig noch None.
    _write(tmp_path / "case" / "r.md", '---\nschedule: never\njob: "echo x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    conn.execute("UPDATE jobs SET next_fire_at=1.0 WHERE slug='r'")
    res = job_db.reserve_next(conn, worker="w", host="h")
    sched = next(s for s in job_db.list_schedules(conn) if s["slug"] == "r")
    assert sched["last_status"] == "running"
    assert sched["last_run_at"] is not None
    row = conn.execute("SELECT started_at, finished_at FROM jobs WHERE id=?",
                       (res["id"],)).fetchone()
    assert row["finished_at"] is None
    assert sched["last_run_at"] == row["started_at"]


def test_reset_increments_fire_and_allows_new_journal_entry(conn, tmp_path: Path):
    # Regression: ohne fire++ beim RESET blockiert der Dedup-Check (run_id, status) den
    # Journal-Eintrag des zweiten Laufs, wenn er denselben Terminal-Status hat.
    _write(tmp_path / "case" / "once.md", '---\nschedule: never\njob: "echo x"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = conn.execute("SELECT id FROM jobs WHERE slug='once'").fetchone()["id"]

    # Erster Lauf → error
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed")
    job_db.report_status(conn, jid, status="error")
    fire1 = conn.execute("SELECT fire FROM jobs WHERE id=?", (jid,)).fetchone()["fire"]
    j1 = conn.execute("SELECT run_id, status FROM journal WHERE slug='once' ORDER BY id").fetchall()
    assert len(j1) == 1 and j1[0]["status"] == "error"

    # RESET muss fire++ (neue run_id für den nächsten Lauf)
    job_db.report_status(conn, jid, status="pending")
    fire2 = conn.execute("SELECT fire FROM jobs WHERE id=?", (jid,)).fetchone()["fire"]
    assert fire2 == fire1 + 1, "fire muss beim RESET erhöht werden"

    # Zweiter Lauf → gleicher Terminal-Status (error), muss trotzdem in den Journal
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="failed")
    job_db.report_status(conn, jid, status="error")
    j2 = conn.execute("SELECT run_id, status FROM journal WHERE slug='once' ORDER BY id").fetchall()
    assert len(j2) == 2, "zweiter Lauf muss eigenen Journal-Eintrag bekommen"
    assert j2[0]["run_id"] != j2[1]["run_id"], "run_id muss sich zwischen den Läufen unterscheiden"

    # schedule_view: last_status soll den letzten Lauf zeigen (error, neuere finished_at)
    scheds = job_db.list_schedules(conn)
    s = next(x for x in scheds if x["slug"] == "once")
    assert s["last_status"] == "error"


# ── PLAN-11.2: last_ping_at + demand ─────────────────────────────────────────


def _insert_job(conn, slug: str = "j") -> str:
    """Minimalen Job-Eintrag direkt einfügen; gibt die ID zurück."""
    import secrets
    job_id = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, slug, f"{slug}.md", "job", "echo hi", "running"),
    )
    return job_id


def test_schema_v11_columns(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "last_ping_at" in cols
    assert "demand" in cols


def test_touch_ping_updates_timestamp(conn):
    jid = _insert_job(conn)
    before = time.time()
    result = job_db.touch_ping(conn, jid)
    after = time.time()
    assert result is True
    ts = conn.execute("SELECT last_ping_at FROM jobs WHERE id=?", (jid,)).fetchone()["last_ping_at"]
    assert before <= ts <= after


def test_touch_ping_returns_false_for_unknown(conn):
    assert job_db.touch_ping(conn, "does-not-exist") is False


def test_set_and_get_demand(conn):
    jid = _insert_job(conn)
    demand = {"input_request": "Wie viele?", "input_format": "number"}
    job_db.set_demand(conn, jid, demand)
    result = job_db.get_demand(conn, jid)
    assert result == demand


def test_get_demand_returns_none_if_not_set(conn):
    jid = _insert_job(conn)
    assert job_db.get_demand(conn, jid) is None


def test_set_demand_overwrites(conn):
    jid = _insert_job(conn)
    job_db.set_demand(conn, jid, {"input_request": "alt", "input_format": "text"})
    job_db.set_demand(conn, jid, {"input_request": "neu", "input_format": "number"})
    assert job_db.get_demand(conn, jid)["input_request"] == "neu"


def test_migration_v10_to_v11(tmp_path: Path):
    """Bestehende v10-DB bekommt last_ping_at + demand per Migration."""
    import sqlite3 as _sqlite3
    p = tmp_path / "old.sqlite"

    # v10-DB manuell aufbauen (ohne die neuen Felder)
    c = _sqlite3.connect(p)
    c.row_factory = _sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
            schedule_ref TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', app_url TEXT
        )
    """)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
              "slug TEXT, kind TEXT, status TEXT, archived_at REAL NOT NULL, "
              "snapshot TEXT NOT NULL DEFAULT '{}', domain TEXT NOT NULL DEFAULT 'scheduled')")
    c.execute("PRAGMA user_version = 10")
    c.commit()
    c.close()

    # connect() soll migrieren
    conn2 = job_db.connect(p)
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(jobs)")}
    assert "last_ping_at" in cols
    assert "demand" in cols
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    conn2.close()


def test_migration_v12_to_v13_adds_jobs_active(tmp_path: Path):
    """PLAN-14 Stufe 14.5: bestehende v12-DB bekommt jobs.active per Migration."""
    import sqlite3 as _sqlite3
    p = tmp_path / "old.sqlite"
    c = _sqlite3.connect(p)
    c.row_factory = _sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
            schedule_ref TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', app_url TEXT,
            last_ping_at REAL, demand TEXT
        )
    """)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
              "slug TEXT, kind TEXT, status TEXT, archived_at REAL NOT NULL, "
              "snapshot TEXT NOT NULL DEFAULT '{}', domain TEXT NOT NULL DEFAULT 'scheduled', "
              "payload TEXT)")
    c.execute("PRAGMA user_version = 12")
    c.commit()
    c.close()

    conn2 = job_db.connect(p)
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(jobs)")}
    assert "active" in cols
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    conn2.close()


# ── PLAN-12 Stufe 12.1: payload auf job_view/journal_view ───────────────────


def test_job_view_exposes_payload(conn):
    jid = _insert_job(conn)
    assert job_db.get_job(conn, jid)["payload"] == "echo hi"


def test_journal_view_exposes_payload_after_real_run(conn, tmp_path: Path):
    _write(tmp_path / "case" / "once.md", '---\nschedule: never\njob: "claude: tu was"\n---\n')
    job_db.rescan(conn, vault_root=tmp_path / "case")
    jid = conn.execute("SELECT id FROM jobs WHERE slug='once'").fetchone()["id"]
    job_db.report_status(conn, jid, status="running")
    job_db.report_status(conn, jid, status="complete", exit_code=0)
    entry = conn.execute("SELECT id FROM journal WHERE slug='once'").fetchone()
    assert job_db.journal_view(
        conn.execute("SELECT * FROM journal WHERE id=?", (entry["id"],)).fetchone()
    )["payload"] == "claude: tu was"


def test_write_local_journal_accepts_payload(conn):
    job_db.write_local_journal(
        conn, run_id="adhoc:1", slug="adhoc", kind="job", status="complete",
        exit_code=0, output_ref=None, host="h", worker="w",
        started_at=1.0, finished_at=2.0, payload="echo local",
    )
    row = conn.execute("SELECT * FROM journal WHERE run_id='adhoc:1'").fetchone()
    assert job_db.journal_view(row)["payload"] == "echo local"


def test_write_local_journal_payload_defaults_to_none(conn):
    job_db.write_local_journal(
        conn, run_id="adhoc:2", slug="adhoc", kind="job", status="complete",
        exit_code=0, output_ref=None, host="h", worker="w",
        started_at=1.0, finished_at=2.0,
    )
    row = conn.execute("SELECT * FROM journal WHERE run_id='adhoc:2'").fetchone()
    assert job_db.journal_view(row)["payload"] is None


def test_migration_v11_to_v12_adds_journal_payload(tmp_path: Path):
    """Bestehende v11-DB (journal ohne payload-Spalte) bekommt sie per Migration."""
    import sqlite3 as _sqlite3
    p = tmp_path / "old.sqlite"

    c = _sqlite3.connect(p)
    c.row_factory = _sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
            schedule_ref TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', app_url TEXT,
            last_ping_at REAL, demand TEXT
        )
    """)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
              "slug TEXT, kind TEXT, status TEXT, archived_at REAL NOT NULL, "
              "snapshot TEXT NOT NULL DEFAULT '{}', domain TEXT NOT NULL DEFAULT 'scheduled')")
    c.execute("PRAGMA user_version = 11")
    c.commit()
    c.close()

    conn2 = job_db.connect(p)
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(journal)")}
    assert "payload" in cols
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    conn2.close()


# ── PLAN-14 Stufe 14.3 — Journal sortiert nach finished_at, nicht archived_at ─


def test_list_journal_orders_by_finished_at_not_archived_at(conn):
    # archived_at bewusst gegenläufig zu finished_at, um die Umstellung
    # sichtbar zu machen (in echten Läufen liegen beide fast identisch).
    conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, finished_at, archived_at) "
        "VALUES ('a:1','a','job','complete', 100, 50)")
    conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, finished_at, archived_at) "
        "VALUES ('b:1','b','job','complete', 50, 100)")
    rows = job_db.list_journal(conn)
    assert [r["run_id"] for r in rows] == ["a:1", "b:1"]


def test_list_journal_respects_limit_and_offset(conn):
    for i in range(5):
        conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, finished_at, archived_at) "
            "VALUES (?,?,?,?,?,?)",
            (f"a:{i}", "a", "job", "complete", 100 - i, 100 - i),
        )
    page1 = job_db.list_journal(conn, limit=2, offset=0)
    page2 = job_db.list_journal(conn, limit=2, offset=2)
    assert [r["run_id"] for r in page1] == ["a:0", "a:1"]  # DESC nach finished_at
    assert [r["run_id"] for r in page2] == ["a:2", "a:3"]
    assert len(job_db.list_journal(conn, limit=2, offset=4)) == 1
    assert len(job_db.list_journal(conn)) == 5  # ohne limit weiterhin unbegrenzt
