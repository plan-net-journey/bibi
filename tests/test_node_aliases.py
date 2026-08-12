"""Ein Knoten kennt die Namen, die er selbst getragen hat (`#144`).

**`#88` hat die Regel gelöst, nicht den Bestand.** Neue gepinnte Zeilen tragen
seither die stabile ``node_id``; die rund 130 vom 2026-08-01 auf diesem Mac
tragen noch Hostnamen — und zwar **zwei verschiedene**, weil der Rechner seinen
Namen im Betrieb wechselt (``Air2024.local`` ↔ ``Mac.fritz.box``).

``pin_lookup_ids()`` kannte drei Namen: den angefragten, ``config.node_id()``
und den **aktuellen** ``socket.gethostname()``. Der jeweils andere historische
Name war nicht dabei. Jede Ansicht, die über ``pinned_host`` filtert, sah
deshalb nur die Hälfte der eigenen Läufe — **welche Hälfte, entschied das
Netz.**

## Warum das so schwer zu bemerken war

**Der Befund kippt mit dem Hostnamen.** Am 2026-08-11 hieß der Rechner
``Air2024.local``, und das Job-Detail zeigte einen `error` von 08:41. Am
2026-08-12 heißt er ``Mac.fritz.box``, und dieselbe Seite zeigt einen `zombie`
— ohne dass sich an Code oder Daten etwas geändert hätte. Wer ihn nachstellen
will und dabei zufällig den anderen Namen trägt, findet ihn nicht und hält ihn
für behoben.

## Warum es eine eigene Entscheidung gebraucht hat

Der Fix **erweitert, was ein Knoten als das Seine ansieht** — und die
Pin-Zusage gilt in beide Richtungen: was einem anderen Knoten gehört, ist in
keiner der Angaben enthalten. **Zwei Rechner, die je einmal ``Air.local``
hießen, dürfen einander nicht erben.** Das ist eine Sicherheitsaussage und
kein Anzeigedetail; m.rau hat sie sich im Ticket vorbehalten.

**Entschieden ist Weg 1** (m.rau, 2026-08-12, auf Vorlage): Aliasse im
Knoten-State. **Die eine Zeile, an der die Zusage hängt, ist die Herkunft der
Liste** — sie wächst aus dem eigenen Lauf und **nie** aus der Datenbank. Ein
Alias, der aus einer `jobs`-Zeile stammte, wäre genau der Weg, auf dem ein
fremder Name in die eigene Menge käme.
"""

from __future__ import annotations

import socket

import pytest

from bibi import config
from bibi.daemon import job_db, worker


@pytest.fixture
def conn(team_repo):  # noqa: ARG001 — parkt cwd im Test-Repo
    c = job_db.connect()
    yield c
    c.close()


def _seed(conn, *, slug: str, pinned_host: str, status: str = "complete",
          finished_at: float = 2.0) -> str:
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, schedule, "
        "status, enqueued_at, finished_at, pinned_host) "
        "VALUES (?, ?, ?, 'job', 'echo hi', 'now', ?, 1.0, ?, ?)",
        (jid, slug, slug, status, finished_at, pinned_host))
    conn.commit()
    return jid


# ── Die Liste selbst ────────────────────────────────────────────────────────


def test_a_node_remembers_the_name_it_is_running_under(team_repo):  # noqa: ARG001
    """**Der Rot-Schritt.** Der eigene Hostname landet in der Alias-Liste."""
    config.record_hostname()
    assert socket.gethostname() in config.node_aliases()


def test_the_list_grows_with_every_name_the_node_has_worn(team_repo, monkeypatch):  # noqa: ARG001
    """Zwei Starts unter zwei Namen — beide bleiben bekannt.

    **Das ist der Fall aus dem Ticket**, und er ist der Grund für die Liste:
    ein Rechner, der seinen Namen wechselt, hat unter beiden gearbeitet und
    unter beiden gepinnt.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    monkeypatch.setattr(socket, "gethostname", lambda: "Mac.fritz.box")
    config.record_hostname()
    assert set(config.node_aliases()) >= {"Air2024.local", "Mac.fritz.box"}


def test_the_same_name_twice_does_not_grow_the_list(team_repo, monkeypatch):  # noqa: ARG001
    """Ein Neustart unter demselben Namen ändert nichts.

    Ohne diese Prüfung wüchse die Liste bei jedem Daemon-Start um einen
    Eintrag — und eine Liste, die nur wächst, ist irgendwann eine
    ``IN``-Klausel mit tausend Parametern.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    config.record_hostname()
    config.record_hostname()
    assert list(config.node_aliases()).count("Air2024.local") == 1


def test_reading_the_list_does_not_write_to_it(team_repo, monkeypatch):  # noqa: ARG001
    """**Lesen und Eintragen sind getrennt, und das ist Absicht.**

    ``pin_lookup_ids()`` läuft in jeder Query. Wäre das Eintragen dort
    eingebaut — nach dem Vorbild von ``node_id()``, das sich beim ersten
    Zugriff selbst heilt —, schriebe jede Datenbankabfrage in die
    Konfigurationsdatei.

    Vor allem aber: **eingetragen wird beim Start, nicht beim Nachschlagen.**
    Wer nachschlägt, fragt oft unter einem fremden Namen — genau dafür ist der
    ``host``-Parameter da.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    monkeypatch.setattr(socket, "gethostname", lambda: "Fremd.local")
    config.node_aliases()
    worker.pin_lookup_ids("Fremd.local")
    assert "Fremd.local" not in config.node_aliases()


# ── Was daraus für die Suche folgt ──────────────────────────────────────────


def test_the_lookup_covers_every_name_the_node_has_worn(team_repo, monkeypatch):  # noqa: ARG001
    """Beide historischen Namen stehen im Vergleich."""
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    monkeypatch.setattr(socket, "gethostname", lambda: "Mac.fritz.box")
    config.record_hostname()
    ids = worker.pin_lookup_ids()
    assert "Air2024.local" in ids and "Mac.fritz.box" in ids


def test_a_node_finds_its_runs_under_both_of_its_names(conn, monkeypatch):
    """**Der Rot-Schritt des Tickets, an der Sache statt an der Liste.**

    Zwei gepinnte Zeilen desselben Buckets unter zwei Namen; gefragt wird unter
    einem. Heute findet der Test **eine statt zweier** — und welche, entscheidet
    der Name, den der Rechner gerade trägt.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    monkeypatch.setattr(socket, "gethostname", lambda: "Mac.fritz.box")
    config.record_hostname()

    _seed(conn, slug="ttyd-aabbccdd", pinned_host="Air2024.local",
          status="error", finished_at=100.0)
    _seed(conn, slug="ttyd-eeff0011", pinned_host="Mac.fritz.box",
          status="zombie", finished_at=200.0)

    ids = worker.pin_lookup_ids("Mac.fritz.box")
    platzhalter = ",".join("?" * len(ids))
    treffer = conn.execute(
        f"SELECT pinned_host FROM jobs WHERE pinned_host IN ({platzhalter}) "
        "AND slug LIKE 'ttyd-________'", tuple(ids)).fetchall()
    assert len(treffer) == 2, [r["pinned_host"] for r in treffer]


def test_the_newest_of_both_names_wins_the_detail_tile(conn, monkeypatch):
    """Und damit zeigt die Detail-Kachel denselben Lauf wie der Jobs-Screen.

    **Das ist `#140`**, der eigentliche Anlass: der `zombie` vom 03.08 ist der
    zuletzt beendete, der `error` vom 01.08 zwei Tage älter. Der Jobs-Screen
    liest die Historie (ungefiltert) und sah immer den `zombie`; das Detail las
    die `jobs`-Zeile und sah, je nach Hostname, den einen oder den anderen.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    monkeypatch.setattr(socket, "gethostname", lambda: "Mac.fritz.box")
    config.record_hostname()

    _seed(conn, slug="ttyd-aabbccdd", pinned_host="Air2024.local",
          status="error", finished_at=100.0)
    _seed(conn, slug="ttyd-eeff0011", pinned_host="Mac.fritz.box",
          status="zombie", finished_at=200.0)

    row = worker._pinned_row("ttyd")
    assert row is not None and row["status"] == "zombie", dict(row) if row else None


# ── Die Gegenprobe, und sie ist die eigentliche Prüfung ─────────────────────


def test_a_name_this_node_never_wore_stays_unreachable(conn, monkeypatch):
    """**Ohne sie wäre ein Fix grün, der schlicht jeden Filter entfernt.**

    Die Pin-Zusage gilt in beide Richtungen: was einem anderen Knoten gehört,
    steht unter dessen Namen und ist in keiner Angabe enthalten. Zwei Rechner,
    die je einmal ``Air.local`` hießen, dürfen einander nicht erben — und
    genau deshalb wächst die Liste **nur aus dem eigenen Lauf**.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()

    _seed(conn, slug="fremd-aabbccdd", pinned_host="sarasate")
    ids = worker.pin_lookup_ids("Air2024.local")
    assert "sarasate" not in ids, ids

    platzhalter = ",".join("?" * len(ids))
    treffer = conn.execute(
        f"SELECT id FROM jobs WHERE pinned_host IN ({platzhalter}) "
        "AND slug LIKE 'fremd-________'", tuple(ids)).fetchall()
    assert treffer == [], "eine fremde Zeile ist erreichbar geworden"


def test_the_aliases_never_come_from_the_database(conn, monkeypatch):
    """**Die Zeile, an der die ganze Zusage hängt.**

    Ein Alias, der aus einer ``jobs``-Zeile stammte, wäre der Weg, auf dem ein
    fremder Name in die eigene Menge käme — und zwar unbemerkt, weil er dann
    aussähe wie ein eigener. Die Liste wächst ausschließlich aus
    ``record_hostname()``, also aus dem eigenen Lauf.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    _seed(conn, slug="fremd-aabbccdd", pinned_host="ganz-fremder-name")
    worker.pin_lookup_ids()
    worker._pinned_row("fremd")
    assert "ganz-fremder-name" not in config.node_aliases()


def test_the_identity_still_beats_the_hostname_for_writing(team_repo, monkeypatch):  # noqa: ARG001
    """`#88` bleibt heil: **geschrieben** wird weiterhin nur unter der ID.

    Die Aliasse erweitern das Nachschlagen, nicht das Pinnen. Ohne diese
    Prüfung wäre ein Fix grün, der wieder Hostnamen in neue Zeilen schreibt —
    und dann wüchse der Bestand, den die Liste zusammenhalten muss, weiter.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    assert worker.pin_identity() == config.node_id()
    assert worker.pin_identity() not in ("Air2024.local", "Mac.fritz.box")


# ── Und der Eintrag passiert im Betrieb, nicht nur im Test ──────────────────


def test_starting_a_daemon_records_the_name(team_repo, monkeypatch):  # noqa: ARG001
    """**Ohne diesen Aufruf ist der ganze Fix wirkungslos.**

    Die Liste wächst aus dem eigenen Lauf — dafür muss der Lauf sie füttern.
    `record_hostname()` könnte tadellos gebaut sein und nie gerufen werden;
    die Alias-Liste bliebe leer, `pin_lookup_ids()` fände nichts Zusätzliches,
    und alle Tests darüber wären trotzdem grün, weil sie den Eintrag selbst
    vornehmen.

    **Das ist dieselbe Form wie die falsch grünen Tests, die `#164` verdeckt
    haben:** eine Zusicherung, die geprüft wird, wo sie hergestellt wird, und
    nicht dort, wo sie gebraucht wird.
    """
    from bibi.daemon import roles as roles_mod
    from bibi.daemon.app import create_app

    monkeypatch.setattr(socket, "gethostname", lambda: "Frisch.local")
    assert "Frisch.local" not in config.node_aliases()
    create_app(roles_mod.resolve({"controller"}))
    assert "Frisch.local" in config.node_aliases()
