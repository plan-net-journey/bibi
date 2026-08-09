"""Feed-Screen (PLAN-18 Stufe 18.3) — jetzt Home (``/-/``): Status-Kacheln
(inkl. neuer Git-Segment-Kachel) + Heatmap + aggregierte Änderungsliste.
Reine Render-Tests; die Routen-Tests liegen bei ``test_daemon_app_feed.py``
(neue ``/-/feed``-Route) bzw. werden über einen gefakten Client geprüft, wie
bei ``test_controller_jobs.py``/``test_controller_daemon.py``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# --- Git-Kachel (PLAN-19 Befund 4: 3 Zeilen, kein Trenner) ----------------------


# --- SYNC-Zeile mit Commit-Hash (PLAN-25 Befund 8-Nachtrag, User-Fund:
# "Release-Stand — Commit-Hash und Anzahl commits behind — reporten") --------


# --- Dritte Zeile "Konflikte" (PLAN-30 Ebene 3) — nur sichtbar bei stuck > 0 ---


# --- Host-Kachel (PLAN-19 Befund 4: Hostname statt "connected", Link) -----------


# --- Mode-Kachel (PLAN-19 Befund 4: Auto-Sync+Maintenance+Uptime zusammen) ------


# --- Job-Status-Kachel (Bibi4-Iteration: Matrix job/claude/app statt der
# bisherigen 2x2-Aggregation, User-Fund "Apps enden nicht") -----------------


def test_feed_status_header_prefers_scheduler_numbers():
    """Die Zahlen des Schedulers gewinnen gegen eine lokale Zaehlung.

    Frueher entschied sich das zwischen zwei Kacheln (Host- gegen Client-
    Job-Status); jetzt hat der rechte Block genau eine Quelle, und `client_rows`
    ist fuer ihn ohne Bedeutung.
    """
    status = {"job_stats": {"counts": {"complete": 7}, "next_due_at": None}}
    html = render.feed_status_fragment(
        status, None, None, now=100.0, client_rows=[{"payload": "x", "app_port": None}],
        scheduler={"hostname": "sarasate", "job_stats": {"counts": {"complete": 7}}})
    assert "7 finished" in html


# --- Feed-Kachel-Grid: jetzt 3 statt 6 (PLAN-19 Befund 4) -----------------------


def test_feed_status_header_has_two_blocks_by_origin():
    """Statt vier Kacheln zwei Bloecke: links dieser Knoten, rechts der
    Scheduler. Die Trennung folgt dem Ausfall — faellt der Host weg, verlieren
    genau die rechten Werte gleichzeitig ihre Gueltigkeit."""
    html = render.feed_status_fragment(
        {"roles": ["connect"], "connect": {"ok": True, "last_at": 90.0}},
        {"branch": "trunk", "tree": "clean", "sync": "synced"},
        "http://sarasate:8780", 100.0)
    assert html.count('class="hdr-block') == 2
    assert "CLIENT" in html and "SCHEDULER" in html
    assert "Rollen" not in html


def test_feed_status_fragment_is_bus_driven_with_maint_trigger():
    # PLAN-36 Stufe 36.3: kein 30s-Poll mehr — die Kacheln haengen am Bus-
    # Target "feedstatus" (Collector: Flag-Diff + Job-Aenderungen). Der
    # bibiMaintChanged-Trigger des MAINT-Toggles bleibt als htmx-Event
    # bestehen (sofortiges Feedback auf den Klick, unabhaengig vom Collector-
    # Tick).
    html = render.feed_status_fragment({}, None, None, now=100.0)
    assert 'id="feedstatus"' in html
    assert 'data-bus="feedstatus"' in html
    assert 'data-bus-refetch="/-/ui/feed/status"' in html
    assert 'hx-get="/-/ui/feed/status"' in html
    assert 'hx-trigger="bibiMaintChanged from:body"' in html
    assert 'hx-swap="outerHTML"' in html
    assert "every " not in html.split(">")[0]


# --- Job-Status-Kachel: eigenes Bus-Element (Bibi4-Iteration, seit PLAN-36
# --- Stufe 36.3 ereignisgenau statt 2s-Poll) --------------------------------


# --- Feed-Filter (PLAN-20 Befund 1: 3-State statt Checkbox) --------------------


# --- Feed-Zeilen -----------------------------------------------------------------


def test_frow_children_allow_wrapping():
    """Flex-Items schrumpfen ohne `min-width: 0` nicht unter ihre Content-Breite
    — beide Spalten brauchen es, sonst laeuft die Zeile ueber den Rand.

    **Der Slug bricht dabei nie mitten im Wort** (Befund m.rau 2026-08-04:
    `20260531.Continuou` / `sCollection-` / `a0bc0dcc`). `overflow-wrap:
    anywhere` stammt aus bibi4 und war fuer die Autorenliste gedacht; auf
    einem Namen ist es falsch. Die Urheber-Spalte behaelt es.
    """
    css = render._CSS
    msg = css.split(".frow .msg {")[1].split("}")[0]
    assert "min-width: 0" in msg
    assert "anywhere" not in msg, "der Slug darf nicht mitten im Wort brechen"
    assert "overflow-wrap: anywhere;" in css.split(".frow .who {")[1].split("}")[0]


# --- Fragment / Page ---------------------------------------------------------------


def test_feed_fragment_hides_load_more_without_days():
    html = render.feed_fragment({"entries": []}, days=None, now=100.0)
    assert "LOAD MORE" not in html


def test_panel_card_first_heading_has_no_top_margin():
    # PLAN-27 Befund 1, User-Fund: "Margins zwischen Chart und Heatmap
    # unterschiedlich" — Root Cause: die generische h2-Regel (margin-top
    # 1.5rem) addiert sich zum .panel-card-Padding, während der Chart-Kopf
    # (.ts-head h3) schon bei margin:0 sitzt. Normalisiert alle .panel-card-
    # Überschriften (Aktivität/Änderungen/Schedules) auf denselben Stand.
    assert ".panel-card > h2:first-child { margin-top: 0" in render._CSS


def test_feed_page_has_header_nav_and_status_cards():
    feed_data = {"entities": [], "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_page(feed_data, git_status={"tree": "clean", "sync": "synced",
                                                   "branch": "trunk"},
                            daemon_status={"roles": ["scheduler", "connect"]}, now=100.0)
    # Ein Ziel je Screen: der Jobs-Tab zeigt auf `/-/jobs`, auf jedem Knoten.
    # Vorher standen hier zwei Links nebeneinander — `/-/ui/jobs` für den
    # Client, `/-/ui/schedules` für den Host —, beide beschriftet „Jobs".
    assert 'href="/-/jobs"' in html and "/-/ui/schedules" not in html
    assert "<title>bibi · Feed</title>" in html
    assert 'class="hdr"' in html


# --- Route (gefakter Client) -------------------------------------------------------


class _FakeClient:
    def __init__(self, *, feed_data=None) -> None:
        self._feed = feed_data or {"entities": [], "heatmap": []}

    def status(self) -> dict:
        return {}

    def feed(self, **_):
        return self._feed

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(client: _FakeClient):
        return create_app(roles.resolve({"controller"}), controller_client=client)
    return _make


def test_root_route_renders_feed_not_schedules(app_with):
    app = app_with(_FakeClient(feed_data={
        "entities": [{"kind": "system", "name": "System", "last_changed": 1.0,
                     "authors": ["a"], "all_agent": False}],
        "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)],
    }))
    with TestClient(app) as c:
        r = c.get("/-/", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert "<title>bibi · Feed</title>" in r.text
        assert "System" in r.text


def test_feed_board_fragment_route(app_with):
    app = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/feed/board")
        assert r.status_code == 200
        assert 'id="feedboard"' in r.text


def test_feed_status_fragment_route_shows_escalated_merge_branches(app_with, team_repo: Path):
    # PLAN-30 Ebene 3, End-to-End: eskalierte Quarantäne-Einträge (Ebene 2)
    # erreichen tatsächlich die Git-Kachel der echten Route, nicht nur den
    # isolierten _git_segment_card()-Aufruf oben.
    from bibi.daemon import merge_quarantine
    for trunk_sha in ("s1", "s2", "s3"):
        merge_quarantine.record_failure(team_repo, "agent/stuck", trunk_sha=trunk_sha)
    app = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/feed/status")
        assert r.status_code == 200
        # bibi5: die Zahl steht jetzt in der `project`-Zeile des Headers
        # statt in einer eigenen Kachel — die Aussage bleibt, dass eine
        # eskalierte Quarantaene sichtbar wird und nicht still verschwindet.
        assert "1 conflict" in r.text
        assert 'class="sync-conflict"' in r.text


def test_root_route_status_cards_are_bus_driven(app_with):
    # PLAN-36 Stufe 36.3: kein konfigurierbares Poll-Intervall mehr — die
    # Kacheln kommen mit Bus-Adresse aus dem initialen Seitenaufbau.
    app = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/", headers={"Accept": "text/html"})
        assert 'data-bus="feedstatus"' in r.text
        assert 'hx-trigger="bibiMaintChanged from:body"' in r.text
