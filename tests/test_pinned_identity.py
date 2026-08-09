"""``pinned_host`` traegt eine Identitaet, keinen Anzeigenamen (m.rau/bibi#88).

``jobs.pinned_host`` ist die Zusage „diese Zeile gehoert genau diesem Knoten"
— sie entscheidet, wer einen ``bibi-ctrl run``-Lauf reservieren darf und wessen
Laeufe die Detailseite zeigt. Gespeichert wurde dafuer ``socket.gethostname()``.

**Dieser Mac wechselt seinen Namen im Betrieb**, gemessen sogar *waehrend* eines
einzelnen Laufs: ``Air2024.local`` gegen ``Mac.fritz.box``. Mit dem Namen
wechselt der Schluessel, und die eigenen ``/run``-Laeufe werden unsichtbar —
keine Kachel, keine Zeile, keine Ausgabe, obwohl alles in der Datenbank steht.

Die stabile Identitaet gibt es laengst: ``config.node_id()``, eine generierte
UUID in der ``env``-Datei, self-healing und vom Hostnamen unabhaengig. Sie wurde
an dieser Stelle nur nicht benutzt.

**Der Bestand darf dabei nicht verwaisen.** Auf diesem Mac tragen rund 130
gepinnte Zeilen einen Hostnamen; ein harter Tausch machte sie alle unauffindbar.
Gesucht wird deshalb unter beiden Namen, geschrieben nur noch unter der ID.
"""

from __future__ import annotations

import socket

import pytest

from bibi.daemon import job_db, worker


@pytest.fixture
def conn(team_repo):  # noqa: ARG001 — parkt cwd im Test-Repo
    c = job_db.connect()
    yield c
    c.close()


def _seed(conn, *, slug: str, pinned_host: str) -> str:
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, schedule, "
        "status, enqueued_at, pinned_host) "
        "VALUES (?, ?, ?, 'job', 'echo hi', 'now', 'complete', 1.0, ?)",
        (jid, slug, slug, pinned_host))
    conn.commit()
    return jid


def test_the_pin_identity_is_the_node_id_not_the_hostname(team_repo):
    """**Der Rot-Schritt von `#88`.**

    Der Name, unter dem gepinnt wird, darf sich nicht aendern, wenn der
    Anzeigename es tut."""
    from bibi import config
    assert worker.pin_identity() == config.node_id()
    assert worker.pin_identity() != socket.gethostname()


def test_a_pinned_run_survives_a_hostname_change(conn, monkeypatch):
    """Der eigentliche Schaden: die eigenen Laeufe verschwinden aus dem FE.

    Zeile unter der stabilen Identitaet anlegen, den Anzeigenamen wechseln
    lassen — sie muss weiterhin gefunden werden."""
    _seed(conn, slug="EngineCI-aabbccdd", pinned_host=worker.pin_identity())
    monkeypatch.setattr(socket, "gethostname", lambda: "Mac.fritz.box")
    assert worker._pinned_last_row("EngineCI") is not None, (
        "nach einem Namenswechsel findet der Knoten seine eigenen /run-Laeufe "
        "nicht mehr (#88)")


def test_an_old_row_under_the_hostname_is_still_found(conn):
    """Der Bestand, und er ist der Grund fuer den Rueckfall.

    Rund 130 gepinnte Zeilen auf diesem Mac tragen einen Hostnamen. Ein harter
    Tausch machte sie auf einen Schlag unauffindbar — die Historie waere da und
    unerreichbar."""
    _seed(conn, slug="alt-11223344", pinned_host=socket.gethostname())
    assert worker._pinned_last_row("alt") is not None


def test_a_row_of_another_node_stays_out_of_reach(conn):
    """Die Gegenprobe, ohne die der Rueckfall eine offene Tuer waere.

    Die Pin-Zusage gilt in beide Richtungen: was einem anderen Knoten gehoert,
    darf dieser hier weder sehen noch reservieren."""
    _seed(conn, slug="fremd-99887766", pinned_host="sarasate-client")
    assert worker._pinned_last_row("fremd") is None


def test_the_worker_can_reserve_a_row_pinned_under_its_node_id(conn):
    """Und die Reservierung muss mitziehen.

    ``WorkerLoop`` fragt unter seinem **Hostnamen** an (``self.host``), die
    Zeile traegt aber die **ID**. Ohne beide Namen im Vergleich schriebe
    ``run_pinned()`` eine Zeile, die anschliessend niemand reservieren kann —
    der Lauf bliebe fuer immer ``pending``."""
    jid = _seed(conn, slug="mine-aabbccdd", pinned_host=worker.pin_identity())
    conn.execute("UPDATE jobs SET status='pending', next_fire_at=0 WHERE id=?", (jid,))
    conn.commit()
    res = job_db.reserve_next(conn, host=socket.gethostname(), pinned_only=True)
    assert res is not None and res["id"] == jid


def test_reserving_still_refuses_a_foreign_pin(conn):
    """Gegenprobe dazu: ein fremder Pin bleibt tabu, auch mit zwei Namen im
    Vergleich."""
    jid = _seed(conn, slug="theirs-aabbccdd", pinned_host="sarasate-client")
    conn.execute("UPDATE jobs SET status='pending', next_fire_at=0 WHERE id=?", (jid,))
    conn.commit()
    assert job_db.reserve_next(conn, host=socket.gethostname(),
                               pinned_only=True) is None
