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


def test_the_journal_filters_leave_the_jobs_screen_with_their_band():
    """Sie wirkten nur im dritten Band. Ist das Band weg, sind sie hier tote
    Knöpfe — und ein toter Knopf ist schlimmer als ein fehlender."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    for wert in ("dropped", "oneshot", "local"):
        assert f'data-filter="{wert}"' not in html, wert


def test_grouping_off_no_longer_smuggles_the_journal_filters_into_the_bar():
    """`group=off` hatte sie in die Kopfleiste geholt, damit sie nicht am
    fehlenden Band hängen. Der Ort, an den sie gehören, ist jetzt ein anderer
    Screen."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW, group=False)
    for wert in ("dropped", "oneshot", "local"):
        assert f'data-filter="{wert}"' not in html, wert


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
    for wert in ("dropped", "oneshot", "local"):
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
