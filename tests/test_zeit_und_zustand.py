"""Zeit und Zustand (`v0.8.10`) — was bedeutet ein Zeitfeld?

Das Zustandsmodell stammt von m.rau (Akzeptanz-Durchgang `v0.8.9`, 2026-08-13)
und ist in acht Punkten aufgeschrieben. Sieben davon waren gebaut; der achte —
`started_at` — trägt alle übrigen Zusagen dieser Datei:

* `started_at` ist der **initiale** Start des Jobs, nicht der des Versuchs.
* `finished_at − started_at` ist damit die **Brutto**-Zeit, die Aufenthaltsdauer
  im Scheduler.
* `exec_runtime` ist die **kumulierte Netto**-Zeit über alle Trials.
* `attempts` sind **Gesamt**versuche, nicht Retries zusätzlich zum ersten Lauf.

Sie fallen alle im Einfachfall zusammen — ein Job ohne Retry und ohne
Deferral —, und genau dort fällt ein Fehler nicht auf. Jeder Test hier baut
deshalb den mehrfachen Fall.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.schedule import backoff


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


# `attempts=3` als Default: die Tests dieser Datei bauen den mehrfachen Fall,
# und seit #168 heisst `attempts` Gesamtversuche — mit 1 waere jeder Job nach
# dem ersten Fehlschlag erschoepft, mit 0 wuerde er gar nicht erst reserviert.
def _insert(conn, slug="a", *, attempts=3, status="pending"):
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, priority, "
        "status, enqueued_at, next_fire_at, attempts) "
        "VALUES (?,?,?,?,?,0,?,?,0,?)",
        (jid, slug, f"{slug}.md", "job", "echo hi", status, time.time(), attempts),
    )
    return jid


# ── #166: started_at meint den Job, nicht den Versuch ───────────────────────


def test_started_at_ueberlebt_einen_retry(conn):
    """Der Kern der Runde: ein zweiter Versuch verschiebt den Start nicht.

    `reserve_next()` schrieb `started_at=:now` bei **jeder** Reservierung — auch
    aus `failed` heraus. Damit war `finished_at − started_at` die Netto-Zeit des
    letzten Versuchs und nicht die Aufenthaltsdauer des Jobs.
    """
    jid = _insert(conn)
    job_db.reserve_next(conn)
    erster = conn.execute("SELECT started_at FROM jobs WHERE id=?", (jid,)).fetchone()[0]

    time.sleep(0.02)
    job_db.report_status(conn, jid, status="failed", reason="boom")
    job_db.reserve_next(conn)

    zweiter = conn.execute("SELECT started_at FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    assert zweiter == erster, "ein Retry darf started_at nicht verschieben"


def test_started_at_ueberlebt_ein_deferral(conn):
    """Dieselbe Zusage für den anderen Weg zurück nach `starting`.

    `deferred` und `failed` unterscheiden sich im Attempt-Zähler, nicht in der
    Frage, ob der Job seinen Aufenthalt fortsetzt — er tut es in beiden Fällen.
    """
    jid = _insert(conn)
    job_db.reserve_next(conn)
    erster = conn.execute("SELECT started_at FROM jobs WHERE id=?", (jid,)).fetchone()[0]

    time.sleep(0.02)
    job_db.report_status(conn, jid, status="deferred")
    job_db.reserve_next(conn)

    zweiter = conn.execute("SELECT started_at FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    assert zweiter == erster, "ein Resume darf started_at nicht verschieben"


def test_started_at_wird_aus_complete_neu_gesetzt(conn):
    """Die Gegenprobe — sonst wäre der Fix ein Einfrieren statt einer Bedeutung.

    Aus `complete` heraus beginnt ein **neuer** Aufenthalt: `reserve_next()`
    zählt dort schon `fire` hoch und räumt `attempt`, `finished_at`, `reason`.
    `started_at` gehört in dieselbe Aufzählung — es fehlte dort, und das war
    der ganze Fehler.
    """
    jid = _insert(conn)
    job_db.reserve_next(conn)
    erster = conn.execute("SELECT started_at FROM jobs WHERE id=?", (jid,)).fetchone()[0]

    time.sleep(0.02)
    job_db.report_status(conn, jid, status="complete")
    conn.execute("UPDATE jobs SET next_fire_at=0 WHERE id=?", (jid,))
    job_db.reserve_next(conn)

    zweiter = conn.execute("SELECT started_at FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    assert zweiter > erster, "ein neuer Aufenthalt bekommt einen neuen Start"


# ── #167: exec_runtime summiert die Netto-Zeiten ────────────────────────────


def test_exec_runtime_summiert_ueber_trials(conn):
    """Zwei Versuche, zwei Netto-Zeiten, eine Summe.

    Der Trial-Beginn steht in `locked_at` — dieselbe Anweisung setzt beide, und
    geräumt wird `locked_at` erst in `report_status()`, in derselben
    Transaktion, in der hier aufsummiert wird. **Deshalb braucht diese Runde
    kein neues Feld und keine Schema-Migration.**
    """
    jid = _insert(conn)

    job_db.reserve_next(conn)
    time.sleep(0.05)
    job_db.report_status(conn, jid, status="failed", reason="boom")
    nach_eins = conn.execute("SELECT exec_runtime FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    assert nach_eins is not None and nach_eins >= 0.04

    job_db.reserve_next(conn)
    time.sleep(0.05)
    job_db.report_status(conn, jid, status="failed", reason="boom")
    nach_zwei = conn.execute("SELECT exec_runtime FROM jobs WHERE id=?", (jid,)).fetchone()[0]

    assert nach_zwei >= nach_eins + 0.04, "der zweite Trial muss aufsummiert werden"


def test_exec_runtime_zaehlt_die_wartezeit_nicht_mit(conn):
    """Netto heißt netto — die Frist zwischen zwei Versuchen gehört nicht dazu.

    Das ist der Unterschied, der die beiden Größen überhaupt rechtfertigt:
    brutto trägt die Wartezeit, netto nicht. Fallen sie zusammen, ist eine von
    beiden falsch gerechnet.
    """
    jid = _insert(conn)

    job_db.reserve_next(conn)
    time.sleep(0.02)
    job_db.report_status(conn, jid, status="failed", reason="boom")
    time.sleep(0.15)  # die Wartezeit zwischen den Versuchen
    job_db.reserve_next(conn)
    time.sleep(0.02)
    job_db.report_status(conn, jid, status="failed", reason="boom")

    row = conn.execute(
        "SELECT started_at, finished_at, exec_runtime FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    brutto = row["finished_at"] - row["started_at"]
    netto = row["exec_runtime"]

    assert netto < brutto - 0.1, (
        f"netto ({netto:.3f}s) muss die Wartezeit auslassen, brutto ({brutto:.3f}s) sie tragen")


# ── #168: attempts sind Gesamtversuche ──────────────────────────────────────


@pytest.mark.parametrize(
    ("attempts", "laeufe_bis_error"),
    [(1, 1), (2, 2), (3, 3)],
)
def test_attempts_sind_gesamtversuche(attempts: int, laeufe_bis_error: int):
    """Bei `attempts: N` ist nach dem N-ten Fehlschlag Schluss, nicht nach N+1.

    Gemessen wird an der **Lauf-Zahl**, nicht am Endzustand: dass ein Job
    irgendwann auf `error` landet, war auch vorher wahr — nur eben einen Lauf
    zu spät (Live-Befund m.rau: `zustand-failed` mit `attempts: 3` warf `error`
    erst im vierten Lauf).

    `attempt` ist der Zähler der **vor** diesem Lauf beendeten Versuche; nach
    ihm sind es `attempt + 1`.
    """
    laeufe = 0
    attempt_cur = 0          # Zähler, wie ihn der Wrapper aus der Reservierung erhält
    while True:
        laeufe += 1          # dieser Versuch findet statt …
        if backoff.exhausted(attempt_cur, attempts):
            break            # … und danach wird gefragt, ob ein weiterer folgt
        attempt_cur += 1
        assert laeufe <= 10, "Endlosschleife — exhausted() greift nie"
    assert laeufe == laeufe_bis_error


def test_attempts_null_startet_nicht(conn):
    """`attempts: 0` heißt „kein Versuch", nicht „ein Versuch ohne Retry".

    Das ist die Kehrseite der neuen Bedeutung und die einzige Stelle, an der
    sie einen Job **anhält** statt ihn früher zu beenden.
    """
    _insert(conn, "keiner", attempts=0)
    assert job_db.reserve_next(conn) is None


# ── #170: die Laufzeit tickt bis zum Terminalzustand ────────────────────────


def test_laufzeit_tickt_durch_eine_wartephase():
    """`failed` und `deferred` sind keine terminalen Zustände.

    Der Renderer begründete das Einfrieren mit einem Satz, der für einen
    **terminalen** Slot stimmt: *„ein blockierter Lauf steht unter A2 tagelang,
    und seine Laufzeit darf dabei nicht mitwachsen."* Für die Wartephase
    zwischen zwei Versuchen stimmt er nicht — dort läuft der Aufenthalt weiter,
    und genau das ist die sichtbarste Folge dieser Runde.
    """
    from bibi.controller import jobs_view

    jetzt = 1_000_000.0
    lauf = jobs_view.slot_run(
        {"row_status": "deferred", "slug": "a", "id": "x", "fire": 0,
         "started_at": jetzt - 30, "finished_at": jetzt - 20},
        src="SCHEDULER", now=jetzt)

    assert lauf is not None
    assert lauf["exec_runtime"] == pytest.approx(30, abs=0.5), (
        "ein wartender Lauf misst gegen jetzt, nicht gegen das Ende seines Versuchs")


def test_laufzeit_friert_im_terminalzustand_ein():
    """Die Gegenprobe — sonst wüchse die Zahl eines abgeschlossenen Laufs ewig.

    Sie ist die eigentliche Zusage: die Laufzeit friert **genau einmal** ein,
    und zwar beim Terminalzustand.
    """
    from bibi.controller import jobs_view

    jetzt = 1_000_000.0
    lauf = jobs_view.slot_run(
        {"row_status": "error", "slug": "a", "id": "x", "fire": 0,
         "started_at": jetzt - 30, "finished_at": jetzt - 20},
        src="SCHEDULER", now=jetzt)

    assert lauf is not None
    assert lauf["exec_runtime"] == pytest.approx(10, abs=0.5)


# ── #169: NEXT auch bei failed und deferred ─────────────────────────────────


@pytest.mark.parametrize("status", ["failed", "deferred"])
def test_kachel_zeigt_den_naechsten_termin_auch_ohne_pending(status: str):
    """Der Wert steht längst in der Zeile — die Bedingung war zu eng.

    `backoff` hat den Retry- bzw. Resume-Termin berechnet und in
    `next_fire_at` abgelegt; gezeigt wurde er nur bei `pending`. **Die
    Bedingung ist zu eng, nicht die Datenlage.**
    """
    from bibi.controller import render
    from bibi.controller.jobs_view import Tile

    jetzt = 1_000_000.0
    kachel = Tile(quelle="SCHEDULER", host="h", status=status,
                  slot={"next_fire_at": jetzt + 300, "slug": "a", "id": "x"},
                  aktionen=frozenset())
    html = render._slot_kachel(kachel, now=jetzt)
    assert "next " in html, f"ein {status}-Slot verschweigt seinen Termin"
