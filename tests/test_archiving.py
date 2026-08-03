"""Die Archivierungsregel A1/A2 (m.rau/bibi#101, Zustandsmodell §3).

Der Uebergang von der Scheduler-DB ins Journal ist **nicht statusgetrieben**,
sondern hat genau zwei Ausloeser:

**A1 — `complete` archiviert sofort und von selbst.** Der Lauf wird unverzueglich
ins Journal geschrieben, die Zeile im selben Zug neu initialisiert. `complete`
ist deshalb in der Scheduler-DB nie sichtbar: ein Durchgangszustand von der
Dauer eines Schreibvorgangs.

**A2 — jeder andere terminale Zustand archiviert erst auf Anweisung.** `error`,
`inactive`, `zombie` und `killed` bleiben stehen, bis ein Mensch START oder
RESET ausloest. Erst diese Aktion schreibt den Lauf ins Journal.

Die Begruendung ist die Asymmetrie der Folgen, nicht eine Vorliebe fuer
Symmetrie: **ein Fehler, der sich selbst archiviert, verschwindet unbemerkt** —
niemand erfaehrt, dass etwas schiefging. **Ein `complete`, das stehenbleibt,
blockiert den naechsten Lauf** — die Automatisierung stuende still. Die Regel
behandelt beide so, wie es ihre Folge verlangt.

Daraus folgt die Konsequenz, die benannt sein muss: ein terminaler Fehler haelt
den Job an. Das ist eine bewusste Entscheidung mit Preis (m.rau, 2026-08-02:
"error bleibt terminal"), abgefedert durch die Retry-Kette davor — `error` wird
erst nach `exhaust` erreicht, also nachdem die Wiederholungen aufgebraucht sind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.schedule import parser
from bibi.schedule.models import Status


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _job(conn, slug: str = "J", *, schedule: str = "0 * * * *") -> str:
    pr = parser.parse_text(
        f"---\nslug: {slug}\nschedule: \"{schedule}\"\njob: echo hi\n---\n",
        schedule_ref=f"case/x/{slug}.md", path=Path(f"case/x/{slug}.md"),
    )
    assert pr.is_ok, pr.error
    return job_db.upsert_schedule(conn, pr, 1000.0)


def _run_until(conn, job_id: str, *states: str) -> None:
    """Den Slot ueber die Zustandsmaschine bis zum gewuenschten Zustand fahren."""
    for s in states:
        assert job_db.report_status(conn, job_id, status=s) == "ok", s


def _journal(conn, job_id: str | None = None) -> list[dict]:
    return job_db.list_journal(conn)


def _slot(conn, job_id: str) -> str:
    return conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()["status"]


# ── A1: complete archiviert sich selbst ─────────────────────────────────────


def test_complete_archives_immediately(conn):
    jid = _job(conn)
    _run_until(conn, jid, "starting", "running", "complete")
    rows = _journal(conn)
    assert [r["status"] for r in rows] == ["complete"]


def test_complete_does_not_block_the_next_run(conn):
    """Der eigentliche Zweck von A1: ein `complete`, das den Platz nicht
    freigibt, wuerde die Automatisierung anhalten. Der Slot traegt danach
    wieder einen naechsten Termin.

    **Nicht** geprueft wird hier, ob die Zeile in der Zwischenzeit noch
    `complete` anzeigt — das ist eine offene Frage zwischen Zustandsmodell §3
    ("nie sichtbar") und FE-Spezifikation §4.5 ("`complete`, sofern ein `next`
    gesetzt ist", zaehlt als `waiting`). Ein Test darf sie nicht im
    Vorbeigehen entscheiden."""
    jid = _job(conn)
    _run_until(conn, jid, "starting", "running", "complete")
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] is not None


# ── A2: alles andere wartet auf einen Menschen ──────────────────────────────


@pytest.mark.parametrize("terminal,path", [
    # `error` ist nur ueber `failed` erreichbar (exhaust) — die Retry-Kette ist
    # der automatische Versuch, der dem Anhalten vorausgeht.
    ("error", ("starting", "running", "failed", "error")),
    ("killed", ("starting", "running", "killed")),
    ("zombie", ("starting", "running", "zombie")),
    ("inactive", ("starting", "running", "deferred", "inactive")),
])
def test_other_terminals_do_not_archive_themselves(conn, terminal, path):
    """Der Kern von A2: ein Fehler, der sich selbst archiviert, verschwindet
    unbemerkt. Er bleibt im Slot stehen, wo er zu sehen ist."""
    jid = _job(conn, terminal)
    _run_until(conn, jid, *path)
    assert _slot(conn, jid) == terminal
    assert _journal(conn) == []


def test_start_archives_the_blocked_run_and_makes_it_due(conn):
    """START raeumt den Slot und macht sofort faellig (`next_fire_at = now`)."""
    jid = _job(conn, "S")
    _run_until(conn, jid, "starting", "running", "failed", "error")
    assert _journal(conn) == []  # A2: noch nichts archiviert

    assert job_db.start_now(conn, jid, now=5000.0) == "ok"
    rows = _journal(conn)
    assert [r["status"] for r in rows] == ["error"]
    assert _slot(conn, jid) == str(Status.PENDING)
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] == 5000.0


def test_reset_archives_the_blocked_run_and_restores_the_schedule(conn):
    """RESET archiviert ebenso, setzt den Slot aber auf den **regulaeren**
    naechsten Termin statt auf jetzt. Der Unterschied zwischen START und RESET
    auf einem terminalen Zustand ist allein dieser Zeitpunkt — RESET ist der
    blanke Uebergang nach `pending` (so ruft ihn die Route `/-/job/{id}/reset`),
    START derselbe Uebergang mit erzwungener Faelligkeit."""
    jid = _job(conn, "R")
    _run_until(conn, jid, "starting", "running", "killed")
    assert _journal(conn) == []

    assert job_db.report_status(conn, jid, status="pending", now=5000.0) == "ok"
    rows = _journal(conn)
    assert [r["status"] for r in rows] == ["killed"]
    assert _slot(conn, jid) == str(Status.PENDING)
    row = conn.execute("SELECT next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["next_fire_at"] != 5000.0  # regulaerer Termin, nicht "jetzt"


def test_the_archived_row_keeps_the_time_the_run_actually_ended(conn):
    """`finished_at` bleibt der Zeitpunkt des Laufs, `archived_at` traegt den des
    Aufraeumens. Genau deshalb sortiert die Lauf-Liste nach `finished_at`: sonst
    erschiene ein Lauf, der tagelang blockiert stand, beim Aufraeumen ganz oben
    unter dem heutigen Datum."""
    jid = _job(conn, "T")
    _run_until(conn, jid, "starting", "running", "failed")
    job_db.report_status(conn, jid, status="error", now=1000.0)
    job_db.start_now(conn, jid, now=9000.0)

    entry = _journal(conn)[0]
    assert entry["finished_at"] == 1000.0
    assert entry["archived_at"] == 9000.0


def test_the_commit_of_a_blocked_run_survives_until_it_is_archived(conn):
    """Der Slot traegt alles ueber seinen Lauf, solange er dort steht.

    `commit_sha` und `branch` kamen bisher als Parameter des Terminal-Reports
    direkt in die Journal-Zeile — sie hatten nie einen Platz im Slot, weil
    zwischen Report und Archivierung kein Zeitraum lag. Unter A2 liegen jetzt
    Tage dazwischen, und ohne Ablage waere der Worktree-Commit genau dort
    verloren, wo er am interessantesten ist: bei einem Lauf, der schiefging.
    """
    jid = _job(conn, "C")
    _run_until(conn, jid, "starting", "running", "failed")
    job_db.report_status(conn, jid, status="error",
                         commit_sha="abc1234", branch="agent/C", now=1000.0)
    assert _journal(conn) == []

    job_db.start_now(conn, jid, now=5000.0)
    entry = _journal(conn)[0]
    assert entry["commit_sha"] == "abc1234"
    assert entry["branch"] == "agent/C"


def test_a_blocked_slot_is_not_dispatched(conn):
    """Die Konsequenz, die benannt sein muss: solange der Slot blockiert ist,
    feuert nichts mehr — auch wenn der Cron-Ausdruck weiter gilt. Der Slot ist
    nicht freigegeben, und ein Platz, der besetzt ist, wird nicht vergeben."""
    jid = _job(conn, "B")
    _run_until(conn, jid, "starting", "running", "zombie")
    assert job_db.reserve_next(conn) is None


def test_archiving_happens_once_not_twice(conn):
    """Kein Doppeleintrag: der Lauf wandert genau einmal ins Journal, egal ob
    ihn START oder RESET abraeumt."""
    jid = _job(conn, "O")
    _run_until(conn, jid, "starting", "running", "failed", "error")
    job_db.start_now(conn, jid, now=5000.0)
    _run_until(conn, jid, "starting", "running", "complete")
    assert [r["status"] for r in _journal(conn)] == ["complete", "error"]
