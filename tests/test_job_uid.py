"""``job_uid`` — die Job-Identität (m.rau/bibi#102, Zustandsmodell §6).

Ein Job hat genau eine Identität, und sie leitet sich deterministisch aus dem
Slug ab: ``md5(slug)``. Das leistet zweierlei — sie ist ein kurzer, fester
Schlüssel für Joins und URLs, und sie macht eine Slug-Kollision zwischen zwei
Ordnern zu einer *erkennbaren* Kollision statt zu zwei stillschweigend
getrennten Jobs.

Der Join, um den es eigentlich geht, ist die kombinierte Lauf-Liste im Job
Detail: Scheduler-Läufe und lokale Läufe desselben Jobs stehen in zwei
getrennten Datenbanken und müssen zusammenfinden. Heute geschieht das über ein
*Muster* (``LIKE '{slug}-________'``), mit ``job_uid`` über einen *Vergleich*.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.schedule import parser
from bibi.schedule.models import job_uid


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _upsert(conn, slug: str, *, ref: str, schedule: str = "0 * * * *") -> str:
    pr = parser.parse_text(
        f"---\nslug: {slug}\nschedule: \"{schedule}\"\njob: echo hi\n---\n",
        schedule_ref=ref, path=Path(ref),
    )
    assert pr.is_ok, pr.error
    return job_db.upsert_schedule(conn, pr, 1000.0)


# ── die Funktion ────────────────────────────────────────────────────────────


def test_job_uid_is_the_md5_of_the_slug():
    """Deterministisch und ohne Zustand — derselbe Slug ergibt überall dieselbe
    Identität, auch auf einem anderen Knoten und in einer anderen Datenbank.
    Genau das macht ihn als Join-Schlüssel zwischen zwei unabhängigen DBs
    brauchbar."""
    assert job_uid("EngineCI") == hashlib.md5(b"EngineCI").hexdigest()
    assert job_uid("EngineCI") == job_uid("EngineCI")
    assert job_uid("EngineCI") != job_uid("engineci")


def test_job_uid_does_not_look_at_the_path():
    """Ordnerübergreifend, ausdrückliche Entscheidung m.rau (2026-08-02):
    „Ordnerübergreifend. Slugs müssen unique sein. Das gewährt das md5.\"

    Dass zwei gleich benannte MDs in verschiedenen Ordnern dieselbe Identität
    bekommen, ist die *beabsichtigte* Wirkung — die Kollision soll auffallen
    (m.rau/bibi#112), nicht durch den Pfad verdeckt werden."""
    a = parser.parse_text(
        "---\nschedule: never\njob: echo hi\n---\n",
        schedule_ref="case/eins/Backup.md", path=Path("case/eins/Backup.md"))
    b = parser.parse_text(
        "---\nschedule: never\njob: echo hi\n---\n",
        schedule_ref="case/zwei/Backup.md", path=Path("case/zwei/Backup.md"))
    assert a.spec.slug == b.spec.slug == "Backup"
    assert job_uid(a.spec.slug) == job_uid(b.spec.slug)


# ── in der Datenbank ────────────────────────────────────────────────────────


def test_upsert_writes_the_job_uid(conn):
    """Geschrieben wird beim Upsert, nicht bei jeder Abfrage berechnet — sonst
    könnte man nicht danach joinen."""
    jid = _upsert(conn, "EngineCI", ref="case/ci/EngineCI.md")
    row = conn.execute("SELECT job_uid FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["job_uid"] == job_uid("EngineCI")


def test_the_journal_row_carries_the_job_uid_of_its_job(conn):
    """Der Kern: die Journal-Zeile erbt die Identität aus der Job-Zeile, statt
    sie erneut aus ihrem eigenen Slug abzuleiten. Das ist der Unterschied, der
    gepinnte Läufe heilt (unten)."""
    jid = _upsert(conn, "EngineCI", ref="case/ci/EngineCI.md")
    conn.execute("UPDATE jobs SET status='complete', started_at=1, finished_at=2 WHERE id=?",
                 (jid,))
    job_db._write_journal(conn, jid, 3.0)
    row = conn.execute("SELECT job_uid, slug FROM journal WHERE slug='EngineCI'").fetchone()
    assert row is not None, "Journal-Zeile fehlt"
    assert row["job_uid"] == job_uid("EngineCI")


def test_a_pinned_run_keeps_the_identity_of_its_base_job(conn):
    """**Das ist der Punkt der ganzen Änderung.** `bibi-ctrl run` legt je Lauf
    einen Pseudo-Job mit eigenem Slug an (`EngineCI-46ec57c7`) — live stehen
    deshalb 252 distinkte Slugs für 33 echte Jobs. Ein lokaler Lauf von
    `EngineCI` hatte damit mit dem Slot von `EngineCI` nichts zu tun.

    Die Identität kommt deshalb nicht aus dem gepinnten Slug, sondern aus dem
    Basis-Slug, den der Aufrufer kennt — `job_uid()` selbst rät nicht am
    Suffix herum. Ohne das findet die kombinierte Lauf-Liste ihre lokalen Läufe
    nicht wieder."""
    base = "EngineCI"
    conn.execute(
        "INSERT INTO jobs (id, slug, job_uid, schedule_ref, kind, payload, status, "
        "pinned_host, started_at, finished_at) "
        "VALUES ('aa11', ?, ?, 'case/ci/EngineCI.md', 'job', 'echo hi', 'complete', 'mac', 1, 2)",
        (f"{base}-46ec57c7", job_uid(base)),
    )
    job_db._write_journal(conn, "aa11", 3.0)
    row = conn.execute("SELECT job_uid FROM journal WHERE slug=?",
                       (f"{base}-46ec57c7",)).fetchone()
    assert row is not None, "Journal-Zeile fehlt"
    assert row["job_uid"] == job_uid(base), \
        "der gepinnte Lauf muss unter der Identität seines Basis-Jobs auffindbar sein"


def test_both_sides_of_a_slug_join_over_job_uid(conn, tmp_path: Path):
    """Zwei unabhängige Datenbanken — Scheduler und Client — finden denselben
    Job über `job_uid` zusammen, ohne dass eine von der anderen weiß. Das ist
    die Voraussetzung für die kombinierte Lauf-Liste (FE-Spezifikation §5.1),
    und sie ist erfüllt, weil die Identität aus dem Slug folgt und nicht aus
    einer vergebenen ID."""
    scheduler_uid = _upsert(conn, "EngineCI", ref="case/ci/EngineCI.md")
    local = job_db.connect(tmp_path / "local.sqlite")
    try:
        local_uid = _upsert(local, "EngineCI", ref="anderer/pfad/EngineCI.md")
        a = conn.execute("SELECT job_uid FROM jobs WHERE id=?", (scheduler_uid,)).fetchone()
        b = local.execute("SELECT job_uid FROM jobs WHERE id=?", (local_uid,)).fetchone()
        assert a["job_uid"] == b["job_uid"]
    finally:
        local.close()


# ── Migration ───────────────────────────────────────────────────────────────


def test_migration_adds_job_uid_to_an_existing_db(tmp_path: Path):
    """Bestehende DBs werden über die Kette mitgenommen, nicht neu aufgesetzt
    (Umbauplan §5). **Ohne Backfill:** Entscheidung m.rau (2026-08-03) — die
    Lauf-Historie muss nicht migriert werden, `job_uid` darf bei 0 anfangen.
    Alte Zeilen tragen deshalb `NULL` und verschwinden nicht; sie sind über den
    Slug weiter auffindbar, nur nicht über den neuen Join."""
    p = tmp_path / "alt.sqlite"
    old = sqlite3.connect(p)
    old.executescript(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, "
        "schedule_ref TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending');"
        "CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, "
        "slug TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, "
        "snapshot TEXT NOT NULL DEFAULT '{}', archived_at REAL NOT NULL);"
        "INSERT INTO jobs (id,slug,schedule_ref,kind,payload) "
        "VALUES ('old1','Alt','a.md','job','echo hi');"
        "INSERT INTO journal (run_id,slug,kind,status,archived_at) "
        "VALUES ('Alt:0','Alt','job','complete',1.0);"
        "PRAGMA user_version = 19;"
    )
    old.commit()
    old.close()

    c = job_db.connect(p)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        assert "job_uid" in cols
        cols = {r[1] for r in c.execute("PRAGMA table_info(journal)")}
        assert "job_uid" in cols
        assert c.execute("SELECT job_uid FROM jobs WHERE id='old1'").fetchone()[0] is None
        assert c.execute("SELECT job_uid FROM journal WHERE run_id='Alt:0'").fetchone()[0] is None
        assert c.execute("SELECT slug FROM jobs WHERE id='old1'").fetchone()[0] == "Alt"
    finally:
        c.close()
