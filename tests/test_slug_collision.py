"""Eine Slug-Kollision ist ein eigener Zustand, kein Verschwinden (#142).

**Was bisher passierte:** Beanspruchen zwei MDs denselben Slug, landet er in
``DiscoveryResult.collisions`` statt in ``found``. ``rescan()`` bildet die
Deaktivierungsmenge aber aus ``existing - found`` — der Job wurde damit
behandelt, als wäre seine MD **gelöscht**: ``active=0``, kein Fire mehr.

``job_uid()`` benennt die Absicht ausdrücklich: *„Die Kollision soll auffallen …
statt zu zwei stillschweigend getrennten Jobs zu werden."* **Die Absicht ist
richtig, die Wirkung verfehlte sie:** ein Job, der still verschwindet, fällt
nicht auf — er fehlt. Und er sah dabei genauso aus wie einer, dessen MD wirklich
gelöscht wurde, obwohl beide MDs noch im Vault liegen.

**Entscheidung m.rau, 2026-08-11:** *„Konflikt melden. UND: Konfliktäre Jobs
können nicht gestartet werden."*

**Die Falle im Rot-Schritt, und sie ist der Grund für die Bauart dieser Tests:**
*„Der Job startet nicht"* ist heute schon wahr — er ist ja deaktiviert. Ein Test,
der nur die Abwesenheit eines Laufs prüft, wäre vor der Änderung grün und
belegte nichts. Geprüft wird deshalb der **Grund**: dass ``active`` bleibt, dass
beide Pfade genannt sind, und dass die Startverweigerung den Konflikt nennt
statt der Inaktivität.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bibi.daemon import job_db


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _md(vault: Path, rel: str, *, schedule: str = "0 * * * *") -> Path:
    """Eine Schedule-MD ohne ``slug:`` — der Slug kommt aus dem Dateistamm."""
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'---\nschedule: "{schedule}"\njob: echo hi\n---\n', encoding="utf-8")
    return p


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _zeile(conn, slug: str):
    return conn.execute("SELECT * FROM jobs WHERE slug=?", (slug,)).fetchone()


# ── Nicht deaktivieren ───────────────────────────────────────────────────────


def test_a_collision_does_not_deactivate_the_job(conn, vault):
    """``active`` sagt *„MD im Vault vorhanden"*. Beide MDs **sind** vorhanden;
    die Zeile auf ``active=0`` zu setzen ist schlicht falsch."""
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)              # ein Job, sauber entdeckt
    assert _zeile(conn, "report")["active"] == 1

    _md(vault, "b/report.md")               # zweite MD beansprucht denselben Slug
    job_db.rescan(conn, vault)
    assert _zeile(conn, "report")["active"] == 1


def test_a_deleted_md_still_deactivates(conn, vault):
    """Gegenprobe, und sie ist die wichtigere Hälfte: das Verschwinden einer MD
    muss weiter deaktivieren. Ohne sie wäre ein Fix grün, der die
    Deaktivierung insgesamt abschaltet."""
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)
    (vault / "a/report.md").unlink()
    job_db.rescan(conn, vault)
    assert _zeile(conn, "report")["active"] == 0


# ── Melden, mit beiden Pfaden ────────────────────────────────────────────────


def test_the_conflict_names_both_paths(conn, vault):
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)
    _md(vault, "b/report.md")
    job_db.rescan(conn, vault)
    refs = json.loads(_zeile(conn, "report")["conflict_refs"])
    assert sorted(refs) == ["a/report.md", "b/report.md"]


def test_two_new_colliding_mds_create_no_job_at_all(conn, vault):
    """**Die Grenze der Zusage, und sie ist Absicht.** Kollidieren zwei MDs, die
    beide neu sind, entsteht *keine* Zeile — es gibt keine entscheidbare Spec,
    und einen der beiden Zufallssieger anzulegen wäre schlimmer als nichts zu
    tun. Sichtbar ist der Fall trotzdem: der Jobs-Screen bildet den
    `duplicate`-Chip aus der lokalen MD-Liste, nicht aus der Datenbank.

    Der Fall, um den es in diesem Ticket geht, ist der andere: ein Job, der
    lief, und dem eine zweite MD nachträglich seinen Slug streitig macht."""
    _md(vault, "a/report.md")
    _md(vault, "b/report.md")
    ergebnis = job_db.rescan(conn, vault)
    assert _zeile(conn, "report") is None
    assert [c["slug"] for c in ergebnis["collisions"]] == ["report"]


def test_resolving_the_collision_clears_the_conflict(conn, vault):
    """Ein Konflikt ist ein Zustand, kein Brandmal — er geht wieder weg."""
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)
    _md(vault, "b/report.md")
    job_db.rescan(conn, vault)
    assert _zeile(conn, "report")["conflict_refs"] is not None
    (vault / "b/report.md").unlink()
    job_db.rescan(conn, vault)
    assert _zeile(conn, "report")["conflict_refs"] is None


# ── Blockieren, mit Nennung des Grundes ──────────────────────────────────────


def test_a_conflicted_job_is_active_and_still_not_dispatched(conn, vault):
    """**Beide Hälften in einem Test**, weil erst zusammen etwas belegt ist:
    die Zeile ist aktiv (sonst wäre die Nicht-Ausführung nur die alte
    Deaktivierung) und wird trotzdem nicht reserviert."""
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)
    _md(vault, "b/report.md")
    job_db.rescan(conn, vault)
    conn.execute("UPDATE jobs SET next_fire_at=0 WHERE slug=?", ("report",))
    assert _zeile(conn, "report")["active"] == 1
    assert job_db.reserve_next(conn) is None


def test_an_unconflicted_job_is_still_dispatched(conn, vault):
    """Gegenprobe: die Sperre trifft den Konflikt und nicht die Warteschlange."""
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)
    conn.execute("UPDATE jobs SET next_fire_at=0 WHERE slug=?", ("report",))
    res = job_db.reserve_next(conn)
    assert res is not None and res["slug"] == "report"


def test_start_refuses_a_conflicted_job_and_says_why(conn, vault):
    """Die Verweigerung nennt den Konflikt, nicht die Inaktivität — sonst
    schickt sie den Menschen an die falsche Stelle."""
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)
    _md(vault, "b/report.md")
    job_db.rescan(conn, vault)
    jid = _zeile(conn, "report")["id"]
    assert job_db.start_now(conn, jid) == "conflict"


def test_start_still_works_without_a_conflict(conn, vault):
    """Gegenprobe zur Verweigerung."""
    _md(vault, "a/report.md")
    job_db.rescan(conn, vault)
    jid = _zeile(conn, "report")["id"]
    assert job_db.start_now(conn, jid) == "ok"


# ── Und an der Route, denn dort drückt der Mensch ────────────────────────────


def _fall(client, root: Path, rel: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('---\nschedule: "0 * * * *"\njob: echo hi\n---\n', encoding="utf-8")


@pytest.fixture
def sched(team_repo: Path):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    # Beide Rollen: `/-/rescan` gehört dem Scheduler, `/-/job/{id}/start` dem
    # Worker — ohne den zweiten antwortet die Route mit 501 statt zu verweigern.
    with TestClient(create_app(roles.resolve({"scheduler", "worker"}))) as client:
        yield client, team_repo


def test_the_start_route_refuses_a_conflict_instead_of_reporting_success(sched):
    """**Der Ausgang muss oben ankommen.** Eine Verweigerung, die die Route als
    ``started`` zurückmeldet, ist schlimmer als keine: der Mensch sieht eine
    Bestätigung und wartet auf einen Lauf, der nie kommt."""
    client, root = sched
    _fall(client, root, "a/report.md")
    client.post("/-/rescan")
    jid = client.get("/-/job").json()[0]["id"]
    _fall(client, root, "b/report.md")
    client.post("/-/rescan")
    r = client.post(f"/-/job/{jid}/start")
    assert r.status_code == 409
    assert "conflict" in r.json()["error"]
    # `schedule_ref` ist case-dir-relativ, nicht repo-relativ — dieselbe Angabe,
    # die auch die Attributseite und der `duplicate`-Chip tragen.
    assert sorted(r.json()["paths"]) == ["a/report.md", "b/report.md"]
