"""``wall_time`` begrenzt den Job, nicht den Versuch (m.rau/bibi#189).

Entscheidung m.rau, 2026-08-14: **brutto**. Damit gilt wörtlich, was das
Zustandsmodell aus dem ``v0.8.9``-Durchgang sagt:

    Ein Job, der zwischen ersten ``started_at`` und jetzt die ``wall_time``
    überschreitet, wird ``killed``.

Bis dahin mass ``_wall_monitor()`` gegen ``time.time()`` zum Start **dieses**
Wrapper-Prozesses. Ein Wrapper lebt je Trial — die Grösse war also die
Netto-Zeit eines Versuchs, und ein Job mit ``attempts: 3`` durfte dreimal
``wall_time`` verbrauchen.

**Warum es so lange niemand sah:** solange ``started_at`` bei jedem Trial neu
gesetzt wurde (#166), waren beide Lesarten dasselbe. Sie gehen erst seit dem
``started_at``-Umbau auseinander, und auch dann nur bei einem Job, der einen
Retry braucht — bei einem ohne fallen sie weiterhin zusammen. Der letzte Test
dieser Datei prüft genau diesen Fall, damit der Fix nicht dort bejubelt wird,
wo er nie etwas ändert.

Drei Ebenen, deshalb eine gemeinsame Datei statt drei verstreuter Tests:

1. die Zeile trägt den ersten Start und gibt ihn an den Worker weiter,
2. der Wrapper misst dagegen statt gegen seinen eigenen Start,
3. ein Job, dessen Budget beim Start schon erschöpft ist, läuft gar nicht an.

Punkt 3 steht nicht im Ticket-Text, sondern in seinem Nebensatz *„ein wartender
Job braucht eine Ausnahme, sonst killt ihn sein eigener Backoff"*. Ausgeschrieben
ist das der Regelfall: ein Job mit ``wall_time: 3600``, der um 10:00 startet, um
10:05 scheitert und zwei Stunden Backoff hat, begänne seinen zweiten Versuch mit
2h05 Brutto-Zeit — und stürbe binnen einer Sekunde, nach fünf Minuten
tatsächlicher Arbeit. **Ohne Punkt 3 ist der Fix nicht falsch, aber unbrauchbar.**
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bibi import wrapper as _wrapper
from bibi.daemon import job_db


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _insert(conn, slug="a", *, wall_time=None):
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, priority, "
        "status, enqueued_at, next_fire_at, wall_time) "
        "VALUES (?,?,?,?,?,0,'pending',?,0,?)",
        (jid, slug, f"{slug}.md", "job", "echo hi", time.time(), wall_time),
    )
    return jid


# ── 1: der erste Start erreicht den Worker ──────────────────────────────────


def test_reservation_traegt_den_ersten_start(conn):
    """Ohne dieses Feld kann der Wrapper die Brutto-Zeit nicht kennen.

    ``started_at`` steht bewusst **nicht** in den eingefrorenen Lauf-Attributen:
    es ist keine Konfiguration, sondern eine Tatsache über die Zeile — dieselbe
    Klasse wie ``attempt`` und ``fire``, die aus demselben Grund direkt aus
    ``row`` kommen.
    """
    _insert(conn, "a")
    r = job_db.reserve_next(conn)
    assert r["started_at"] is not None
    row = conn.execute("SELECT started_at FROM jobs WHERE id=?", (r["id"],)).fetchone()
    assert r["started_at"] == row["started_at"]


def test_der_erste_start_ueberlebt_den_retry(conn):
    """Der Kern der Brutto-Lesart, an der Stelle, an der sie entsteht.

    ``reserve_next()`` behält ``started_at`` bei einer Fortsetzung aus ``failed``
    bereits seit #166 — geprüft wird hier, dass die **Reservierung** denselben
    Wert weiterreicht und nicht den Zeitpunkt des zweiten Versuchs.
    """
    jid = _insert(conn, "a")
    erste = job_db.reserve_next(conn)
    conn.execute("UPDATE jobs SET status='failed', locked_at=NULL WHERE id=?", (jid,))
    conn.commit()
    time.sleep(0.01)
    zweite = job_db.reserve_next(conn)

    assert zweite["id"] == jid
    assert zweite["started_at"] == erste["started_at"], (
        "Der zweite Versuch meldet einen neuen Start — damit misst wall_time "
        "wieder den Versuch statt den Job."
    )


# ── 2: der Wrapper misst gegen den ersten Start ─────────────────────────────


def test_wall_monitor_misst_gegen_den_uebergebenen_start(tmp_path, monkeypatch):
    """Diese Ebene war schon vorher richtig — sie hält den Fix an seinem Platz.

    ``_wall_monitor()`` bekommt ``started`` als Argument und hat nie selbst
    gemessen. Der Fehler sass beim Aufrufer. Wandert die Messung später in den
    Monitor, fällt dieser Test.
    """
    killed = []
    monkeypatch.setattr(_wrapper, "_terminate_proc", lambda proc, env=None: killed.append(proc))
    out_path = tmp_path / "output.jsonl"
    proc = SimpleNamespace(poll=lambda: None)
    outcome = [""]

    _wrapper._wall_monitor(proc, 60, time.time() - 100, outcome, out_path,
                           threading.Lock(), {})

    assert outcome[0] == "wall_time"
    assert killed == [proc]


def test_wrapper_nimmt_den_job_start_aus_der_umgebung(monkeypatch):
    """Der Aufrufer im Wrapper: ``BIBI_JOB_STARTED_AT`` schlägt ``time.time()``.

    Geprüft wird die Auswahl, nicht der Monitor — deshalb an der Funktion, die
    sie trifft, und nicht über einen ganzen Lauf. Ein Ende-zu-Ende-Test hier
    bräuchte einen echten Prozess, der lange genug lebt, und misst dann die
    Geduld des Harnischs statt die Regel.
    """
    jetzt = 1_000_000.0
    monkeypatch.setattr(_wrapper.time, "time", lambda: jetzt)

    assert _wrapper._wall_start({"BIBI_JOB_STARTED_AT": "999000.0"}) == 999000.0
    # Ohne die Variable — ein direkt gestarteter Wrapper ohne Scheduler-Zeile —
    # bleibt es bei jetzt. Das ist kein Bestandsschutz, sondern die Bedingung
    # dafuer, dass ein solcher Lauf ueberhaupt eine Grenze hat.
    assert _wrapper._wall_start({}) == jetzt
    # Unlesbar ⇒ wie nicht gesetzt. Ein Wrapper darf an einer kaputten
    # Umgebungsvariable nicht sterben; er verliert dann die Brutto-Sicht,
    # nicht seine Grenze.
    assert _wrapper._wall_start({"BIBI_JOB_STARTED_AT": "kaputt"}) == jetzt


# ── 3: ein erschöpftes Budget verhindert den Start ──────────────────────────


def test_erschoepftes_budget_startet_nicht(conn):
    """Der Fall aus dem Nebensatz des Tickets, und der Grund fuer Punkt 3.

    Der Job hat 5 s Rechenzeit verbraucht und 2 h gewartet. Brutto ist sein
    Budget von 3600 s laengst weg — er darf nicht anlaufen.
    """
    jid = _insert(conn, "a", wall_time=3600)
    r = job_db.reserve_next(conn)
    conn.execute(
        "UPDATE jobs SET status='failed', locked_at=NULL, started_at=? WHERE id=?",
        (time.time() - 7200, jid))
    conn.commit()
    zweite = job_db.reserve_next(conn)

    assert job_db.budget_erschoepft(zweite) is True


def test_budget_im_rahmen_startet(conn):
    """Gegenprobe. Ohne sie waere der Test darueber auch dann gruen, wenn
    ``budget_erschoepft()`` stur ``True`` zurueckgaebe — ein Waechter, der jeden
    aufhaelt, meldet nie einen Fehler und ist trotzdem kaputt."""
    jid = _insert(conn, "a", wall_time=3600)
    job_db.reserve_next(conn)
    conn.execute(
        "UPDATE jobs SET status='failed', locked_at=NULL, started_at=? WHERE id=?",
        (time.time() - 60, jid))
    conn.commit()
    zweite = job_db.reserve_next(conn)

    assert job_db.budget_erschoepft(zweite) is False


def test_ohne_wall_time_gibt_es_kein_budget(conn):
    """``wall_time: None`` ist der Normalfall und heisst *keine Grenze*."""
    jid = _insert(conn, "a", wall_time=None)
    job_db.reserve_next(conn)
    conn.execute(
        "UPDATE jobs SET status='failed', locked_at=NULL, started_at=? WHERE id=?",
        (time.time() - 999999, jid))
    conn.commit()
    zweite = job_db.reserve_next(conn)

    assert job_db.budget_erschoepft(zweite) is False


def test_ein_job_ohne_retry_verhaelt_sich_unveraendert(conn):
    """**Der Fall, an dem der Fehler nie auffiel, und deshalb der wichtigste.**

    Ein Job ohne Retry hat genau einen Versuch; erster Start und Wrapper-Start
    fallen zusammen. Waere die Brutto-Umstellung hier sichtbar, haette sie etwas
    kaputtgemacht, das vorher richtig war.
    """
    _insert(conn, "a", wall_time=3600)
    r = job_db.reserve_next(conn)

    assert job_db.budget_erschoepft(r) is False
    # Und der Start, gegen den gemessen wird, liegt in der Gegenwart — nicht
    # etwa beim Einreihen des Jobs.
    assert abs(r["started_at"] - time.time()) < 5
