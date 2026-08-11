"""Welchen gepinnten Lauf eine Ansicht zeigt (#140).

**Der Befund:** Der Jobs-Screen zeigt für ``ttyd-onboarding-mustertest``
``zombie``, das Job-Detail ``error`` — derselbe Job, zwei Screens, zwei
Zustände. ``/run`` vergibt pro Aufruf einen eigenen ``jobs.slug``
(``f"{bucket}-{token}"``), für diesen Job gibt es dadurch 26 Zeilen.

**Zwei Ursachen, und nur die erste gehört hierher.** Die Auswahl im Detail
folgte ``enqueued_at DESC`` — also dem zuletzt *eingereihten* Lauf, nicht dem
zuletzt *beendeten*. Bei einem Lauf, der lange hängt und erst Tage später
zombiet, sind das verschiedene Zeilen. Die zweite Ursache ist die
Hostnamen-Spaltung dieses Rechners; sie steht in einem eigenen Ticket, weil ihr
Fix die Pin-Zusage berührt.

Geprüft wird die **Regel**, nicht die einzelne Zelle: welcher Lauf einer Kachel
gehört, ist eine Aussage über Aktualität, und die muss unabhängig davon halten,
in welcher Reihenfolge die Läufe eingereiht wurden.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.daemon import worker as worker_mod


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "jobs.sqlite"
    job_db.connect(p).close()
    return p


def _pinned(db: Path, *, jid: str, bucket: str, token: str, host: str,
            status: str, enqueued: float, finished: float | None) -> None:
    """Eine ``/run``-Zeile, so wie ``run_pinned()`` sie anlegt."""
    c = job_db.connect(db)
    c.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
        "pinned_host, enqueued_at, started_at, finished_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (jid, f"{bucket}-{token}", f"{bucket}.md", "job", "echo hi", status,
         host, enqueued, enqueued, finished),
    )
    c.commit()
    c.close()


def test_the_tile_picks_the_last_finished_run_not_the_last_enqueued(db, monkeypatch):
    """Der Rot-Schritt aus dem Ticket, an der Konstellation, die ihn erzeugt.

    Ein Lauf, der hängt und erst Tage später zombiet, wird **früher**
    eingereiht als ein kurzer Lauf, der danach startet und sofort scheitert.
    Nach ``enqueued_at`` gewinnt der kurze, nach Aktualität der lange.
    """
    monkeypatch.setattr(worker_mod, "pin_lookup_ids", lambda host=None: ("H",))
    jetzt = time.time()
    _pinned(db, jid="lang", bucket="b", token="aaaaaaaa", host="H",
            status="zombie", enqueued=jetzt - 3600, finished=jetzt - 60)
    _pinned(db, jid="kurz", bucket="b", token="bbbbbbbb", host="H",
            status="error", enqueued=jetzt - 1800, finished=jetzt - 1790)
    row = worker_mod._pinned_last_row("b", db_path=db)
    assert row is not None
    assert row["id"] == "lang"
    assert row["status"] == "zombie"


def test_a_running_run_outranks_every_finished_one(db, monkeypatch):
    """Gegenprobe: ein Lauf, der noch läuft, ist der aktuellste — auch wenn er
    früher eingereiht wurde als der zuletzt beendete. Ohne sie wäre eine
    Sortierung allein nach ``finished_at`` grün und zeigte während eines Laufs
    den vorigen."""
    monkeypatch.setattr(worker_mod, "pin_lookup_ids", lambda host=None: ("H",))
    jetzt = time.time()
    _pinned(db, jid="laeuft", bucket="b", token="aaaaaaaa", host="H",
            status="running", enqueued=jetzt - 3600, finished=None)
    _pinned(db, jid="fertig", bucket="b", token="bbbbbbbb", host="H",
            status="complete", enqueued=jetzt - 1800, finished=jetzt - 60)
    row = worker_mod._pinned_last_row("b", db_path=db)
    assert row is not None
    assert row["id"] == "laeuft"


def test_a_foreign_pin_is_still_out_of_reach(db, monkeypatch):
    """Die Pin-Zusage gilt weiter in beide Richtungen: die neue Sortierung
    erweitert nicht, was ein Knoten sehen darf. Ohne diese Gegenprobe könnte
    ein späterer Griff an die Auswahl den Filter mitnehmen, ohne dass es
    auffällt."""
    monkeypatch.setattr(worker_mod, "pin_lookup_ids", lambda host=None: ("H",))
    jetzt = time.time()
    _pinned(db, jid="fremd", bucket="b", token="aaaaaaaa", host="ANDERER",
            status="zombie", enqueued=jetzt - 3600, finished=jetzt - 60)
    assert worker_mod._pinned_last_row("b", db_path=db) is None


def test_the_live_selection_keeps_its_own_narrower_view(db, monkeypatch):
    """``_pinned_live_row()`` beantwortet eine andere Frage — *läuft hier gerade
    etwas* — und darf von der neuen Sortierung nicht auf einen terminalen Lauf
    umgelenkt werden."""
    monkeypatch.setattr(worker_mod, "pin_lookup_ids", lambda host=None: ("H",))
    jetzt = time.time()
    _pinned(db, jid="fertig", bucket="b", token="bbbbbbbb", host="H",
            status="complete", enqueued=jetzt - 60, finished=jetzt - 30)
    assert worker_mod._pinned_live_row("b", db_path=db) is None
