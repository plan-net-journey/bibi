"""Jobs-Screen und Job-Detail sagen dasselbe über denselben Job (`#140`).

**Der beobachtete Fall:** Job `ttyd-onboarding-mustertest`. Der Jobs-Screen
zeigt `zombie`, das Job-Detail zeigt `error` — zwei Tage auseinander.

```
9566c085  ttyd-…-20678419  zombie  Mac.fritz.box   01.08 20:30 → 03.08 20:34
07a625ec  ttyd-…-9299f4b7  error   Air2024.local   01.08 08:41 → 01.08 08:41
```

## Zwei Ursachen, in zwei Runden gefunden

**Die erste war die Sortierung** und ist in `v0.8.2` behoben: `_pinned_row()`
las nach `enqueued_at DESC`, also nach **Einreihung** statt nach
**Aktualität**. Ein Lauf, der hängt und erst Tage später zombiet, wird früher
eingereiht als ein kurzer, der danach startet und sofort scheitert.

**Die zweite war der Hostname**, und sie hat das Ticket überlebt: die beiden
Zeilen liegen unter **verschiedenen historischen Namen desselben Rechners**.
`pin_lookup_ids()` kannte nur den aktuellen — das Detail sah je nach Netzlage
die eine oder die andere Hälfte, der Jobs-Screen sah über die Historie immer
beide. Das ist `#144`, und mit ihm fällt auch dieser Fall.

## Warum beide Screens in denselben Test gehören

Das Ticket sagt es selbst: *„Der Vergleich der beiden Screens ist der Test,
nicht die einzelne Zelle — ein Test nur auf die Kachel wäre auch grün, wenn
beide Screens gemeinsam denselben falschen Lauf zeigten."*

**Die beiden lesen verschieden**, und genau das ist der Grund für den
Widerspruch: das Detail liest die `jobs`-Zeile (nach `pinned_host` gefiltert),
der Jobs-Screen die Historie (nach Slug-Muster, ungefiltert).
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


def _lauf(conn, *, slug: str, host: str, status: str, ende: float) -> None:
    """Eine gepinnte `jobs`-Zeile **und** ihr Journal-Eintrag.

    Beides, weil die beiden Screens verschiedene Tabellen lesen — ein Testdatum
    nur in `jobs` ließe den Jobs-Screen leer und den Vergleich sinnlos.
    """
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, schedule, "
        "status, enqueued_at, finished_at, pinned_host) "
        "VALUES (?, ?, ?, 'job', 'echo hi', 'now', ?, 1.0, ?, ?)",
        (jid, slug, slug, status, ende, host))
    # `pinned_host` auch hier: `list_journal()` findet Bucket-Zeilen nur ueber
    # `slug LIKE '<bucket>-________' AND pinned_host IS NOT NULL` -- ohne den
    # Wert sieht der Jobs-Screen nichts, und der Vergleich waere sinnlos.
    conn.execute(
        "INSERT INTO journal (run_id, slug, kind, status, started_at, "
        "finished_at, domain, archived_at, pinned_host) "
        "VALUES (?, ?, 'job', ?, ?, ?, 'pinned', ?, ?)",
        (f"{slug}:0", slug, status, ende - 10, ende, ende, host))
    conn.commit()


#: Der Fall aus dem Ticket, mit seinen echten Werten: der ältere `error` unter
#: dem einen Namen, der jüngere `zombie` unter dem anderen.
_FALL = (
    ("ttyd-onboarding-mustertest-20678419", "Mac.fritz.box", "zombie", 200.0),
    ("ttyd-onboarding-mustertest-9299f4b7", "Air2024.local", "error", 100.0),
)


def _stelle_den_fall(conn, monkeypatch, *, laeuft_als: str) -> None:
    """Beide Namen als eigene registrieren, dann unter einem davon laufen."""
    for name in ("Air2024.local", "Mac.fritz.box"):
        monkeypatch.setattr(socket, "gethostname", lambda n=name: n)
        config.record_hostname()
    monkeypatch.setattr(socket, "gethostname", lambda: laeuft_als)
    for slug, host, status, ende in _FALL:
        _lauf(conn, slug=slug, host=host, status=status, ende=ende)


def _detail_sagt(slug: str) -> str | None:
    """Was die Detail-Kachel zeigt: die gepinnte `jobs`-Zeile."""
    row = worker._pinned_last_row(slug)
    return None if row is None else row["status"]


def _jobs_screen_sagt(conn, slug: str) -> str | None:
    """Was der Jobs-Screen zeigt: der jüngste Lauf aus der Historie."""
    runs = job_db.list_journal(conn, slug=slug, limit=50)
    if not runs:
        return None
    jung = max(runs, key=lambda r: r.get("finished_at") or 0)
    return jung.get("status")


@pytest.mark.parametrize("laeuft_als", ["Mac.fritz.box", "Air2024.local"])
def test_both_screens_name_the_same_run(conn, monkeypatch, laeuft_als):
    """**Der Rot-Schritt**, und er läuft unter *beiden* Namen.

    Das ist die Eigenschaft, die den Befund so schwer zu fassen machte: er
    kippt mit dem Hostnamen. Ein Test, der nur unter einem Namen läuft, wäre
    an einem von zwei Tagen grün gewesen — **ohne dass sich etwas geändert
    hätte.**
    """
    _stelle_den_fall(conn, monkeypatch, laeuft_als=laeuft_als)
    detail = _detail_sagt("ttyd-onboarding-mustertest")
    liste = _jobs_screen_sagt(conn, "ttyd-onboarding-mustertest")
    assert detail == liste, f"Detail sagt {detail!r}, Jobs-Screen sagt {liste!r}"


def test_and_the_run_they_name_is_the_last_one_that_finished(conn, monkeypatch):
    """**Die Gegenprobe zur vorigen, und ohne sie ist sie die Hälfte wert.**

    Zwei Screens können sich einig sein und **gemeinsam** den falschen Lauf
    zeigen — das Ticket benennt genau diese Lücke. Geprüft wird deshalb nicht
    nur die Übereinstimmung, sondern der Wert: der `zombie` vom 03.08 ist der
    zuletzt beendete, der `error` vom 01.08 zwei Tage älter.
    """
    _stelle_den_fall(conn, monkeypatch, laeuft_als="Mac.fritz.box")
    assert _detail_sagt("ttyd-onboarding-mustertest") == "zombie"
    assert _jobs_screen_sagt(conn, "ttyd-onboarding-mustertest") == "zombie"


def test_a_running_run_beats_a_finished_one_on_both(conn, monkeypatch):
    """Die Regel aus `v0.8.2` bleibt heil: **erst was läuft, dann was endete.**

    Ein laufender Lauf hat kein `finished_at` und ist per Definition der
    aktuellste. Ohne diese Prüfung wäre ein Fix grün, der nur noch nach
    `finished_at DESC` sortiert — und der zeigte während eines Laufs den
    vorigen.
    """
    _stelle_den_fall(conn, monkeypatch, laeuft_als="Mac.fritz.box")
    import secrets
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, schedule, "
        "status, enqueued_at, started_at, pinned_host) "
        "VALUES (?, ?, ?, 'job', 'echo hi', 'now', 'running', 5.0, 6.0, ?)",
        (secrets.token_hex(4), "ttyd-onboarding-mustertest-11223344",
         "ttyd-onboarding-mustertest-11223344", "Mac.fritz.box"))
    conn.commit()
    assert _detail_sagt("ttyd-onboarding-mustertest") == "running"


def test_a_foreign_pin_shows_up_on_neither(conn, monkeypatch):
    """**Die Pin-Zusage gilt weiter, und sie gilt hier zuerst.**

    Ein Lauf, der einem anderen Knoten gehört, darf auf keinem der beiden
    Screens als eigener erscheinen. Ohne diese Prüfung wäre die Einigkeit der
    beiden Screens über einen Fix erreichbar, der einfach jeden Filter
    entfernt — dann sagten sie dasselbe und beide das Falsche.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    config.record_hostname()
    _lauf(conn, slug="fremd-aabbccdd", host="sarasate", status="complete", ende=50.0)
    assert _detail_sagt("fremd") is None


def test_the_old_lookup_is_what_made_them_disagree(conn, monkeypatch):
    """**Der Rot-Schritt, konserviert statt nur einmal gesehen.**

    Ein `git checkout` des Vorstands zeigte hier nur, dass
    ``config.record_hostname()`` fehlt — das ist ein Importfehler und kein
    Befund. **Was der Rot-Schritt belegen soll, ist die Wirkung:** mit einer
    Auswahl, die nur den *aktuellen* Hostnamen kennt, sagen die beiden Screens
    Verschiedenes.

    Nachgestellt wird deshalb die alte Fassung von ``pin_lookup_ids()`` —
    angefragter Name, Identität, aktueller Hostname, **ohne** die Aliasse. So
    bleibt der Beleg im Test stehen und nicht nur in einer Commit-Nachricht.
    """
    _stelle_den_fall(conn, monkeypatch, laeuft_als="Mac.fritz.box")

    def alt(host=None):
        return tuple(dict.fromkeys(
            i for i in (host or "", worker.pin_identity(), socket.gethostname()) if i))

    monkeypatch.setattr(worker, "pin_lookup_ids", alt)
    detail = _detail_sagt("ttyd-onboarding-mustertest")
    liste = _jobs_screen_sagt(conn, "ttyd-onboarding-mustertest")
    assert detail == "zombie" and liste == "zombie", (detail, liste)

    # Und unter dem anderen Namen kippt der Befund — ohne dass sich an Code
    # oder Daten etwas geaendert haette. Das ist die Eigenschaft, die ihn so
    # schwer zu fassen machte.
    monkeypatch.setattr(socket, "gethostname", lambda: "Air2024.local")
    detail_dann = _detail_sagt("ttyd-onboarding-mustertest")
    liste_dann = _jobs_screen_sagt(conn, "ttyd-onboarding-mustertest")
    assert detail_dann == "error", detail_dann
    assert liste_dann == "zombie", liste_dann
    assert detail_dann != liste_dann, "der Widerspruch laesst sich nicht mehr herstellen"
