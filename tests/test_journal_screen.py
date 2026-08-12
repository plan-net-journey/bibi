"""Der Journal-Screen (#38) — das dritte Segment zieht aus Jobs aus.

**Das ist keine Rücknahme des gestrichenen Archive-Tabs** (#130), und der
Unterschied ist der Inhalt dieser Datei: der alte Tab führte **Läufe** aller
Jobs nach Zeit und beantwortete „was lief heute Nacht?". Dieser hier führt
**Jobs**, je Slug aggregiert, und beantwortet „welche Jobs haben nur noch
Historie?". Die Frage „was lief" bleibt gestrichen — `test_job_detail.py::
test_no_archive_renderer_is_left_anywhere` hält das weiterhin fest.

Der Befund dahinter ist gemessen: der Jobs-Screen führte 37 Jobs, davon 23 im
JOURNAL-Segment (Stand 2026-08-04). Knapp zwei Drittel der Zeilen gehörten
Jobs, die es nicht mehr gibt, und standen zwischen den 14, um die es täglich
geht. Der Screen war nicht zu voll, er war falsch gewichtet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.controller import jobs_view
from bibi.controller.jobs_view import Segment, build_rows
from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.schedule.models import job_uid


class _FakeClient:
    def __init__(self, status: dict) -> None:
        self._status = status

    def status(self) -> dict:
        return self._status

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def run_journal(self, **_):
        return []

    def run_live_list(self):
        return {}

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(status: dict):
        return create_app(roles.resolve({"controller"}),
                          controller_client=_FakeClient(status))
    return _make


NOW = 1_000_000.0


def _md(slug, schedule="0 * * * *", **kw):
    return {"slug": slug, "schedule": schedule, "payload": "echo hi",
            "repo_path": f"case/x/{slug}.md", **kw}


def _historie(slug):
    """Ein Lauf im Journal, ohne MD und ohne Scheduler-Eintrag — `dropped`."""
    return {"slug": slug, "status": "complete", "archived_at": NOW - 7200}


def _zeilen(**kw):
    return build_rows(local=kw.pop("local", []), scheduler=kw.pop("scheduler", []),
                      journal=kw.pop("journal", []), now=NOW, **kw)


# ── Jobs führt zwei Sektionen ──────────────────────────────────────────────


def test_jobs_carries_two_sections_not_three():
    """Der Kern des Umzugs: SCHEDULE und ADHOC bleiben, JOURNAL geht."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    assert "SCHEDULE" in html
    assert "ADHOC" in html
    assert "JOURNAL" not in html


def test_a_journal_row_no_longer_stands_between_the_daily_ones():
    """Der gemessene Befund, als Test: was nur noch Historie hat, verdrängt
    nichts mehr auf dem Screen, um den es täglich geht."""
    zeilen = _zeilen(local=[_md("taeglich")], journal=[_historie("laengst-weg")])
    html = render.jobs_screen(zeilen, now=NOW)
    assert "taeglich" in html
    assert "laengst-weg" not in html


def test_the_third_axis_returns_to_the_jobs_screen_with_a_reason():
    """**Hier stand bis `#31` das Gegenteil**, und die Begründung war zum
    Zeitpunkt von `#38` richtig: die drei Filter wirkten nur im dritten Band,
    also wären sie auf dem Jobs-Screen tote Knöpfe gewesen.

    `#31` hat die Ursache beseitigt statt das Symptom: die Achse wirkt jetzt
    über alle Bänder, damit trifft sie hier etwas, und damit gehört sie hierher.
    **Ein toter Knopf ist schlimmer als ein fehlender — aber ein Knopf, der
    lebt, gehört dorthin, wo man ihn braucht.**"""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    for wert in ("local", "1shot", "gone"):
        assert f'data-filter="{wert}"' in html, wert


def test_the_third_axis_is_independent_of_the_bands():
    """Die Achse hing einmal an der Gruppierung — `group=off` holte sie in die
    Kopfleiste, damit sie nicht an einem fehlenden Band hing.

    Seit `#31` ist das gegenstandslos: sie wirkt auf Zeilen, nicht auf Bänder,
    und steht deshalb in beiden Ansichten gleich."""
    for gruppiert in (True, False):
        html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW, group=gruppiert)
        for wert in ("local", "1shot", "gone"):
            assert f'data-filter="{wert}"' in html, f"{wert} bei group={gruppiert}"


# ── Der eigene Screen ──────────────────────────────────────────────────────


def test_the_app_bar_carries_six_screens():
    """Sechs statt fünf, und der Test prüft die Reihenfolge mit: die App-Bar
    steht auf jedem Screen, ein verrutschter Tab fiele sonst nur dem auf, der
    hinsieht. `Journal` steht neben `Jobs`, weil es dessen Segment war."""
    assert [name for name, _ in render.SCREENS] == \
        ["Feed", "Jobs", "Journal", "Nodes", "Live", "Log"]


def test_the_journal_screen_shows_what_jobs_no_longer_does():
    html = render.journal_screen(_zeilen(journal=[_historie("laengst-weg")]), now=NOW)
    assert "laengst-weg" in html


def test_the_journal_screen_carries_only_journal_rows():
    """Es ist ein Umzug, keine zweite Jobs-Liste."""
    zeilen = _zeilen(local=[_md("taeglich")], journal=[_historie("laengst-weg")])
    html = render.journal_screen(zeilen, now=NOW)
    assert "laengst-weg" in html
    assert "taeglich" not in html


def test_the_three_journal_filters_live_here_now():
    html = render.journal_screen(_zeilen(journal=[_historie("alt")]), now=NOW)
    for wert in ("local", "1shot", "gone"):
        assert f'data-filter="{wert}"' in html, wert


def test_an_empty_journal_says_so():
    html = render.journal_screen(_zeilen(local=[_md("a")]), now=NOW)
    assert "nothing archived in this window" in html


def test_the_journal_screen_has_no_status_filter():
    """Im Journal steht Historie, die keinen laufenden Zustand hat — der
    STATUS-Filter greift dort schon in `trifft_filter()` nicht."""
    html = render.journal_screen(_zeilen(journal=[_historie("alt")]), now=NOW)
    for wert in ("waiting", "running", "stopped"):
        assert f'data-filter="{wert}"' not in html, wert


# ── Erreichbarkeit: der Test, mit dem #130 den alten Tab streichen durfte ───


def test_every_job_in_the_journal_stays_reachable(app_with, team_repo: Path):
    """**Wiederholt, nicht vorausgesetzt.** Beim Bau von #130 stimmte genau
    diese Zusage nicht: 20 von 33 lokal gelaufenen Jobs standen nicht im
    Screen, ihre Läufe wären unerreichbar geworden. Der Umzug macht dieselbe
    Zusage erneut, also wird sie erneut belegt."""
    html = render.journal_screen(_zeilen(journal=[_historie("ohne-md")]), now=NOW)
    assert f'href="/-/jobs/{job_uid("ohne-md")}"' in html


def test_the_journal_route_answers(app_with):
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        assert c.get("/-/jobs/journal").status_code == 200


def test_the_screen_does_not_sit_on_the_journal_api(app_with):
    """`/-/journal` gehört dem Scheduler — es ist die API, die der
    Controller-Client selbst aufruft. Ein Knoten mit beiden Rollen führt beide
    Routen in derselben App, und die zweite wäre still tot."""
    assert dict(render.SCREENS)["Journal"] == "/-/jobs/journal"


def test_the_journal_list_fragment_answers(app_with):
    """Das Nachlade-Ziel des Bus — ohne es stünde der Screen still."""
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        r = c.get("/-/jobs/journal/list")
        assert r.status_code == 200
        assert 'data-bus="jobs"' in r.text


def test_the_refetched_journal_stays_subscribed(app_with):
    """Der Bus swappt mit `outerHTML`: käme die Antwort ohne `data-bus`, wäre
    die Region nach genau einem Update abgemeldet."""
    fragment = render.journal_list_fragment([], NOW)
    assert 'data-bus="jobs"' in fragment
    assert 'data-bus-refetch="/-/jobs/journal/list?' in fragment


def test_the_journal_page_does_not_nest_the_wrapper_twice():
    html = render.journal_page_v5([], now=NOW)
    assert html.count('data-bus="jobs"') == 1


def test_the_journal_page_carries_the_bus_client():
    """`data-bus` allein bewirkt nichts — den Strom baut `_EVENTS_JS` auf."""
    html = render.journal_page_v5([], now=NOW)
    assert "new EventSource('/-/events')" in html


# ── #130: NEXT und 24H behaupten auf diesem Screen eine Zukunft ─────────────
#
# **Beobachtung aus dem Akzeptanz-Durchgang zu `v0.8.0`.** Jobs, die es nicht
# mehr gibt, tragen zwei Angaben über ihre Zukunft: `NEXT` steht in der
# Vergangenheit (der letzte berechnete `next_fire_at` eines seit Wochen
# gelöschten Jobs), und `24H` meldet `0/288+0 0%` — die schlechtestmögliche
# Zuverlässigkeit für einen Job, der planmäßig nicht laufen soll.
#
# **Ein Umzug ändert nichts an den Daten und alles an ihrem Gewicht.** Beide
# Spalten waren im JOURNAL-Segment schon immer so gefüllt; dort standen die
# Zeilen aber zwischen den aktiven. Der eigene Screen führt ausschließlich
# solche Zeilen — dann ist eine Spalte, die für jede Zeile dasselbe Falsche
# behauptet, keine Randnotiz mehr, sondern der Screen.
#
# **Die Spalten bleiben stehen und werden gefüllt, nicht entfernt.** Beide
# Screens teilen sich eine Zeilenfunktion; sie wegzulassen hieße einen zweiten
# Tabellenkopf zu führen — bewusst vermieden, und für die Filterzeile aus `#31`,
# die über alle Bänder wirken soll, der falsche Weg.


#: Die Spalten der Jobs-Tabelle in ihrer Reihenfolge. Die Zellen tragen **keine
#: Klassen** — bis auf `slug` sind es nackte `<td>`. Ein Test, der nach Position
#: greift, ist deshalb hier keine Nachlässigkeit, sondern die einzige ehrliche
#: Möglichkeit: eine Klasse zu erfinden, damit der Test hübscher wird, hieße den
#: Prüfgegenstand für die Prüfung zu ändern.
_SPALTEN = ("slug", "type", "client", "scheduler", "last", "next", "runtime", "24h")


def _zelle(html: str, spalte: str) -> str:
    """Der Text der Zelle `spalte` aus der ersten Datenzeile."""
    import re
    zeilen = re.findall(r"<tr[ >](?:(?!</tr>).)*</tr>", html, re.S)
    daten = [z for z in zeilen if 'class="slug"' in z]
    assert daten, f"keine Datenzeile im HTML gefunden (Spalte {spalte})"
    zellen = re.findall(r"<td[^>]*>(.*?)</td>", daten[0], re.S)
    assert len(zellen) == len(_SPALTEN), (
        f"{len(zellen)} Zellen, erwartet {len(_SPALTEN)} — die Tabelle hat "
        f"ihre Spalten geändert, dieser Helfer muss nach: {zellen}")
    roh = zellen[_SPALTEN.index(spalte)]
    return re.sub(r"<[^>]+>", "", roh).strip()


def _abgelegt_mit_zukunft(slug):
    """Die Lage, in der der Befund entstand — und sie ist nicht frei erfunden.

    `_segment_fuer()` legt einen Job ins JOURNAL, wenn es **keine MD mehr gibt**
    (`lokal is None`), der Scheduler ihn aber noch kennt und auf `active=0`
    führt. Genau dieser Scheduler-Eintrag trägt weiter seinen zuletzt
    berechneten `next_fire_at` — der Termin, der in der Vergangenheit steht.

    Ein Testdatum, das die Zeile nur ins richtige Segment schiebt, ohne diesen
    Weg zu nehmen, hat kein `next_fire_at` in der Zeile und prüft nichts: der
    erste Anlauf dieser Datei war aus diesem Grund grün, bevor irgendetwas
    behoben war."""
    return {"slug": slug, "active": 0, "schedule": "0 * * * *",
            "next_fire_at": NOW - 86_400, "last_run_at": NOW - 90_000}


def _journal_zeilen(slug="laengst-weg"):
    zeilen = _zeilen(scheduler=[_abgelegt_mit_zukunft(slug)],
                     journal=[_historie(slug)])
    assert zeilen and zeilen[0].segment is Segment.JOURNAL, (
        f"das Testdatum landet nicht im JOURNAL-Segment: {zeilen}")
    return zeilen


def test_the_journal_screen_promises_no_next_run():
    """`NEXT` ist für einen abgelegten Job ohne Aussage — also `—`.

    Der Screen beantwortet „welche Jobs haben nur noch Historie?". Eine Spalte,
    die daneben behauptet, es stehe noch etwas bevor, unterläuft genau diese
    Zusage."""
    html = render.journal_screen(_journal_zeilen(), now=NOW)
    zelle = _zelle(html, "next")
    assert zelle == "—", (
        f"NEXT zeigt {zelle!r} statt `—` — ein Termin in der Vergangenheit für "
        "einen Job, den es nicht mehr gibt")


def test_the_journal_screen_promises_no_reliability():
    """`24H` rechnet gegen eine Erwartung, die für einen abgelegten Job nicht
    mehr gilt. `0 %` ist dann kein schlechter Wert, sondern gar keiner.

    Die Quote setzt der Controller an die Zeile, nicht `build_rows()` — sie
    wird hier deshalb gesetzt statt erwartet. Ohne das ist sie `None`, rendert
    ohnehin `—`, und der Test wäre grün, ohne etwas zu prüfen."""
    zeilen = _journal_zeilen()
    zeilen[0].quote = jobs_view.Quote(complete=0, expected=288, manual=0)
    html = render.journal_screen(zeilen, now=NOW)
    zelle = _zelle(html, "24h")
    assert zelle == "—", (
        f"24H zeigt {zelle!r} statt `—` — die schlechtestmögliche Quote für "
        "einen Job, der planmäßig nicht laufen soll")


def test_the_jobs_screen_keeps_both_columns_filled():
    """Die Gegenprobe, und sie ist der Grund, warum der Fix an der Zeile hängt
    und nicht an der Spalte: auf dem Jobs-Screen sagen beide Spalten etwas, und
    dort müssen sie es weiter sagen."""
    zeilen = _zeilen(local=[_md("taeglich")],
                     scheduler=[{"slug": "taeglich", "active": 1,
                                 "schedule": "0 * * * *",
                                 "next_fire_at": NOW + 3600}])
    zeilen[0].quote = jobs_view.Quote(complete=20, expected=24, manual=0)
    html = render.jobs_screen(zeilen, now=NOW)
    assert _zelle(html, "next") != "—", (
        "NEXT ist auch auf dem Jobs-Screen leer — der Fix hat zu weit gegriffen")
    assert _zelle(html, "24h") != "—", (
        "24H ist auch auf dem Jobs-Screen leer — der Fix hat zu weit gegriffen")


def test_both_screens_still_share_one_table_head():
    """Kein zweiter Tabellenkopf. Die Spalten bleiben, ihr Inhalt ändert sich —
    das ist die Bedingung, unter der `#31` seine Filter an die Köpfe hängen
    kann, ohne sie zweimal zu bauen."""
    j = render.journal_screen(_journal_zeilen("weg"), now=NOW)
    b = render.jobs_screen(_zeilen(local=[_md("da")]), now=NOW)
    for spalte in ("NEXT", "24H"):
        assert spalte in j, f"{spalte} fehlt im Journal-Kopf"
        assert spalte in b, f"{spalte} fehlt im Jobs-Kopf"
