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


def test_git_segment_card_clean_synced():
    html = render._git_segment_card({"tree": "clean", "sync": "synced", "branch": "trunk"})
    assert 'class="v tree-clean"' in html and 'class="v sync-synced"' in html
    assert "trunk" in html
    assert "·" not in html  # kein Trenner mehr (User-Fund 2026-07-06)


def test_git_segment_card_lines_are_labeled():
    # PLAN-20 Befund 2, User-Fund: "clean"/"sync" ganz ohne Beschriftung.
    # PLAN-21 Befund 7: Key/Value jetzt als Grid-Divs statt Spans.
    html = render._git_segment_card({"tree": "clean", "sync": "synced", "branch": "trunk"})
    assert '<div class="k">Tree</div>' in html
    assert '<div class="k">Sync</div>' in html
    assert "Branch trunk" in html


def test_git_segment_card_modified_ahead():
    html = render._git_segment_card({"tree": "modified", "sync": "ahead", "branch": "trunk"})
    assert 'class="v tree-modified"' in html and 'class="v sync-ahead"' in html


def test_git_segment_card_none_shows_dash():
    html = render._git_segment_card(None)
    assert ">—<" in html


# --- SYNC-Zeile mit Commit-Hash (PLAN-25 Befund 8-Nachtrag, User-Fund:
# "Release-Stand — Commit-Hash und Anzahl commits behind — reporten") --------


def test_git_segment_card_synced_shows_short_hash():
    html = render._git_segment_card(
        {"tree": "clean", "sync": "synced", "branch": "trunk",
         "oid": "95a04a7197fd3e5dfb63283f591e8e77458bf401", "ahead": 0, "behind": 0})
    assert "synced: 95a04a7" in html
    assert "95a04a7197fd3e5dfb63283f591e8e77458bf401" not in html  # gekürzt, nicht voll


def test_git_segment_card_behind_shows_hash_and_count():
    html = render._git_segment_card(
        {"tree": "clean", "sync": "behind", "branch": "trunk",
         "oid": "95a04a7197fd3e5dfb63283f591e8e77458bf401", "ahead": 0, "behind": 3})
    assert "behind: 95a04a7 (3)" in html


def test_git_segment_card_ahead_shows_hash_and_count():
    html = render._git_segment_card(
        {"tree": "modified", "sync": "ahead", "branch": "trunk",
         "oid": "95a04a7197fd3e5dfb63283f591e8e77458bf401", "ahead": 23, "behind": 0})
    assert "ahead: 95a04a7 (23)" in html


def test_git_segment_card_diverged_shows_hash_and_both_deltas():
    # "diverged" (bis Batch 7 Stufe 3 "conflict" genannt) = ahead UND behind
    # > 0, kein echter Merge-Konflikt mit <<<<<<<-Markern — s.
    # git_status.working_tree_status().
    html = render._git_segment_card(
        {"tree": "clean", "sync": "diverged", "branch": "trunk",
         "oid": "95a04a7197fd3e5dfb63283f591e8e77458bf401", "ahead": 23, "behind": 3})
    assert "diverged: 95a04a7 (+23, -3)" in html


def test_git_segment_card_without_oid_falls_back_to_plain_sync():
    # Ältere Aufrufer/Tests ohne oid-Feld — kein Crash, reines Zustandswort.
    html = render._git_segment_card({"tree": "clean", "sync": "synced", "branch": "trunk"})
    assert 'class="v sync-synced"' in html


# --- Dritte Zeile "Konflikte" (PLAN-30 Ebene 3) — nur sichtbar bei stuck > 0 ---


def test_git_segment_card_no_konflikte_row_when_stuck_absent():
    html = render._git_segment_card({"tree": "clean", "sync": "synced", "branch": "trunk"})
    assert "Konflikte" not in html


def test_git_segment_card_no_konflikte_row_when_stuck_zero():
    html = render._git_segment_card(
        {"tree": "clean", "sync": "synced", "branch": "trunk", "stuck": 0})
    assert "Konflikte" not in html


def test_git_segment_card_shows_konflikte_row_when_stuck():
    html = render._git_segment_card(
        {"tree": "clean", "sync": "synced", "branch": "trunk", "stuck": 2})
    assert '<div class="k">Konflikte</div>' in html
    assert '<div class="v sync-conflict">2</div>' in html


def test_git_segment_card_none_ignores_stuck():
    # kein Git-Repo -> weiterhin die "—"-Kachel, unabhängig von stuck.
    html = render._git_segment_card(None)
    assert ">—<" in html
    assert "Konflikte" not in html


# --- Host-Kachel (PLAN-19 Befund 4: Hostname statt "verbunden", Link) -----------


def test_host_card_shows_hostname_link_when_connected():
    html = render._host_card(
        {"connect": {"ok": True, "last_at": 96.0}},
        "http://sarasate.tail9f9173.ts.net:8780", now=100.0)
    assert 'class="ok"' in html
    assert 'href="http://sarasate.tail9f9173.ts.net:8780/-/"' in html
    assert ">sarasate.tail9f9173.ts.net<" in html
    assert "verbunden" not in html
    assert "Heartbeat 4s ago" in html


def test_host_card_labeled_client_with_connect_role():
    # PLAN-21 Befund 6: mit connect-Rolle heißt die Karte "Client", nicht
    # mehr "Host" — Rendering (Hostname-Link+Heartbeat) bleibt unverändert.
    html = render._host_card(
        {"connect": {"ok": True, "last_at": 96.0}},
        "http://sarasate.tail9f9173.ts.net:8780", now=100.0)
    assert '<div class="label">Client</div>' in html
    assert '<div class="label">Host</div>' not in html


def test_host_card_labeled_host_without_connect_role():
    html = render._host_card({"hostname": "sarasate"}, "http://localhost:8780", now=100.0)
    assert '<div class="label">Host</div>' in html


def test_host_card_disconnected_shows_bad():
    html = render._host_card(
        {"connect": {"ok": False, "last_at": 90.0}},
        "http://sarasate.tail9f9173.ts.net:8780", now=100.0)
    assert 'class="bad"' in html


def test_host_card_dash_without_scheduler_url():
    html = render._host_card({}, None, now=100.0)
    assert ">—<" in html


def test_host_card_shows_own_hostname_without_connect_role():
    # PLAN-21 Befund 6, revidiert PLAN-20 Befund 4 (User-Fund per Screenshot:
    # der "lokal"-Platzhalter auf dem Host selbst war nicht gewollt — "beim
    # Host anzeigen: Host > Hostname"). Ohne connect-Rolle (status hat dann
    # keinen "connect"-Key, s. app.py) zeigt die Karte jetzt status["hostname"]
    # (eigener socket.gethostname(), unabhängig von host_url/BIBI_SCHEDULER_URL).
    html = render._host_card({"hostname": "sarasate"}, "http://localhost:8780", now=100.0)
    assert "sarasate" in html
    assert "lokal" not in html
    assert "<a " not in html  # keine Verlinkung auf sich selbst


def test_host_card_dash_without_hostname_and_without_connect_role():
    html = render._host_card({}, "http://localhost:8780", now=100.0)
    assert ">—<" in html


def test_host_card_shows_next_due_client_count_and_complete_on_host_branch():
    # Bibi4-Iteration, User-Fund: "next in ..." wandert von der Job-Status-
    # Kachel hierher, plus Anzahl verbundener Clients — und (zweite Iteration,
    # User-Fund: "nur beim Host gehört 785 complete ebenfalls nach HOST") der
    # Complete-Zähler noch dazu. Zwei Zeilen (dritte Iteration, User-Fund:
    # "schreib '2 clients connected' in der ersten Zeile und 'next Job in
    # 1 min, 11 complete' in der zweiten Zeile" — next/complete auf eine
    # Zeile zusammengelegt, nicht mehr drei separate).
    status = {
        "hostname": "sarasate",
        "job_stats": {"next_due_at": 400.0, "complete_since_uptime": 785},
        "workers": [{"stale": False}, {"stale": False}, {"stale": True}],
    }
    html = render._host_card(status, "http://localhost:8780", now=100.0)
    assert '<div class="sub">2 clients connected</div>' in html
    assert '<div class="sub">next Job in 5 min, 785 complete</div>' in html


def test_host_card_next_due_none_and_no_clients_shows_dash_and_zero():
    status = {"hostname": "sarasate"}
    html = render._host_card(status, "http://localhost:8780", now=100.0)
    assert '<div class="sub">0 clients connected</div>' in html
    assert '<div class="sub">next Job —, 0 complete</div>' in html


def test_host_card_uses_cumulative_complete_counter_not_live_count():
    # complete_since_uptime (kumulativ seit Start), NICHT counts["complete"]
    # (Live-Zählung aktiver Jobs — sinkt, sobald abgeschlossene Jobs
    # archiviert werden) — dieselbe Unterscheidung, die früher in der
    # Job-Status-Kachel galt, jetzt hier, weil der Zähler mit umgezogen ist.
    status = {"hostname": "sarasate", "job_stats": {"complete_since_uptime": 47}}
    html = render._host_card(status, "http://localhost:8780", now=100.0)
    assert "47 complete" in html


def test_host_card_client_branch_has_no_next_due_sub_line():
    # Die Client-Karte (mit connect-Rolle) zeigt weiterhin nur Heartbeat,
    # kein "next"/Client-Count/Complete — das ist ausschließlich Host-
    # Perspektive.
    html = render._host_card(
        {"connect": {"ok": True, "last_at": 96.0}},
        "http://sarasate.tail9f9173.ts.net:8780", now=100.0)
    assert "next" not in html
    assert "clients connected" not in html
    assert "complete" not in html


# --- Mode-Kachel (PLAN-19 Befund 4: Auto-Sync+Maintenance+Uptime zusammen) ------


def test_mode_card_shows_all_three_values():
    # PLAN-21 Befund 7: Key/Value als Grid-Divs statt gestapelter Spans.
    html = render._mode_card(
        {"auto_sync": True, "maintenance": False, "started_at": 0.0}, now=3600.0)
    assert '<div class="k">Auto-Sync</div><div class="v ok">an</div>' in html
    assert '<div class="k">Maintenance</div><div class="v ">aus</div>' in html
    assert "Uptime 1 h" in html


def test_mode_card_maintenance_on_is_bad():
    html = render._mode_card({"auto_sync": False, "maintenance": True}, now=100.0)
    assert '<div class="k">Maintenance</div><div class="v bad">an</div>' in html


def test_mode_and_git_card_use_kvgrid_container():
    # PLAN-21 Befund 7, User-Fund: Werte sollen sich wie ein Grid/Tabelle
    # ausrichten, nicht nur gelabelt-aber-gestapelt sein.
    mode_html = render._mode_card({"auto_sync": True, "maintenance": False}, now=100.0)
    git_html = render._git_segment_card({"tree": "clean", "sync": "synced", "branch": "trunk"})
    assert '<div class="kvgrid">' in mode_html
    assert '<div class="kvgrid">' in git_html
    assert ".kvgrid {" in render._CSS


# --- Job-Status-Kachel (Bibi4-Iteration: Matrix job/claude/app statt der
# bisherigen 2x2-Aggregation, User-Fund "Apps enden nicht") -----------------


def test_job_status_card_splits_by_kind_into_matrix():
    job_stats = {
        "counts_by_kind": {
            "job": {"pending": 2, "deferred": 1, "running": 1},
            "claude": {"failed": 1, "awaiting": 1},
            "app": {"inactive": 1, "zombie": 1, "error": 1, "killed": 1},
        },
        "complete_since_uptime": 47, "next_due_at": None,
    }
    html = render._job_status_card(job_stats, now=100.0)
    assert '<div class="jsg-k">Waiting</div><div class="jsg-v">3</div><div class="jsg-v">1</div><div class="jsg-v">0</div>' in html
    assert '<div class="jsg-k">Running</div><div class="jsg-v">1</div><div class="jsg-v">1</div><div class="jsg-v">0</div>' in html
    assert '<div class="jsg-k">Stopped</div><div class="jsg-v">0</div><div class="jsg-v">0</div><div class="jsg-v">4</div>' in html
    assert '<div class="jsg-h"></div><div class="jsg-h">Job</div><div class="jsg-h">Claude</div><div class="jsg-h">App</div>' in html


def test_job_status_card_missing_kind_defaults_to_zero():
    html = render._job_status_card(
        {"counts_by_kind": {"app": {"running": 2}}, "complete_since_uptime": 0,
         "next_due_at": None}, now=100.0)
    assert '<div class="jsg-k">Running</div><div class="jsg-v">0</div><div class="jsg-v">0</div><div class="jsg-v">2</div>' in html


def test_job_status_card_has_no_sub_line():
    # Bibi4-Iteration, User-Fund: "next in ..." (Batch 7) und "785 complete"
    # (zweite Iteration, "nur beim Host gehört 785 complete ebenfalls nach
    # HOST") sind beide in die Host-Kachel gewandert — die Job-Status-Kachel
    # ist jetzt reine Matrix ohne Fußzeile, symmetrisch zu
    # _client_job_status_card().
    html = render._job_status_card(
        {"counts_by_kind": {}, "complete_since_uptime": 3, "next_due_at": 400.0}, now=100.0)
    assert '<div class="sub">' not in html
    assert "next" not in html
    assert "complete" not in html


def test_job_status_card_has_no_title_row():
    # Bibi4-Iteration, User-Fund: "entferne die Überschrift Job Status und
    # beginne ganz oben mit JOB CLAUDE APP" — die Matrix-Kopfzeile trägt die
    # Beschriftung jetzt selbst, kein eigenes <div class="label"> mehr.
    html = render._job_status_card(
        {"counts_by_kind": {}, "complete_since_uptime": 0, "next_due_at": None}, now=100.0)
    assert '<div class="label">' not in html
    assert html.startswith('<div class="card"><div class="jobstatus-grid">')


def test_job_status_card_uses_jobstatus_grid_css():
    html = render._job_status_card(
        {"counts_by_kind": {}, "complete_since_uptime": 0, "next_due_at": None}, now=100.0)
    assert '<div class="jobstatus-grid">' in html
    assert ".jobstatus-grid {" in render._CSS


def test_client_job_status_card_is_a_full_matrix_like_host():
    # Bibi4-Iteration, User-Fund: "mir gefällt die schnöde Zusammenfassung
    # nicht, ich hätte gerne die Matrix immer wie beim Host" — New/Modified/
    # Conflict x Job/Claude/App, keine Fließtext-Subline mehr.
    rows = [
        {"payload": "echo a", "app_port": None, "git_status": "clean"},
        {"payload": "echo b", "app_port": None, "git_status": "modified"},
        {"payload": "claude: x", "app_port": None, "git_status": "new"},
        {"payload": "echo c", "app_port": 9100, "git_status": "conflict"},
    ]
    html = render._client_job_status_card(rows)
    assert '<div class="jsg-k">New</div><div class="jsg-v">0</div><div class="jsg-v">1</div><div class="jsg-v">0</div>' in html
    assert '<div class="jsg-k">Modified</div><div class="jsg-v">1</div><div class="jsg-v">0</div><div class="jsg-v">0</div>' in html
    assert '<div class="jsg-k">Conflict</div><div class="jsg-v">0</div><div class="jsg-v">0</div><div class="jsg-v">1</div>' in html
    assert '<div class="sub">' not in html  # keine Fußzeile mehr


def test_client_job_status_card_shows_all_zero_rows_when_everything_clean():
    # User-Entscheidung (AskUserQuestion): immer alle 3 Zeilen zeigen, auch
    # bei 0 — volle visuelle Parität mit der Host-Matrix.
    rows = [{"payload": "echo a", "app_port": None, "git_status": "clean"}]
    html = render._client_job_status_card(rows)
    assert '<div class="jsg-k">New</div><div class="jsg-v">0</div><div class="jsg-v">0</div><div class="jsg-v">0</div>' in html
    assert '<div class="jsg-k">Modified</div><div class="jsg-v">0</div><div class="jsg-v">0</div><div class="jsg-v">0</div>' in html
    assert '<div class="jsg-k">Conflict</div><div class="jsg-v">0</div><div class="jsg-v">0</div><div class="jsg-v">0</div>' in html


def test_client_job_status_card_no_title_matches_host_shape():
    html = render._client_job_status_card([])
    assert html.startswith('<div class="card"><div class="jobstatus-grid">')
    assert '<div class="label">' not in html


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


def test_job_status_fragment_is_bus_driven():
    html = render.job_status_fragment(
        {"counts_by_kind": {}, "complete_since_uptime": 0, "next_due_at": None}, now=100.0)
    assert 'id="jobstatuscard"' in html
    assert 'data-bus="jobs"' in html
    assert 'data-bus-refetch="/-/ui/feed/jobstatus"' in html
    assert "hx-trigger" not in html
    assert '<div class="jobstatus-grid">' in html


def test_job_status_fragment_empty_without_job_stats():
    assert render.job_status_fragment(None, now=100.0) == ""


def test_status_cards_unchanged_after_refactor():
    # Regressionsschutz für die _status_card_list()-Extraktion: _status_cards()
    # muss weiterhin genau 4 Kacheln liefern, keine Git-Kachel.
    html = render._status_cards({"roles": ["connect"]}, now=100.0)
    assert html.count('<div class="card">') == 4


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


def test_feed_jobstatus_fragment_route(app_with):
    # Bibi4-Iteration: Self-Poll-Ziel von #jobstatuscard, eigene Route/eigener
    # (schnellerer) Takt getrennt von #feedstatus.
    app = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/feed/jobstatus")
        assert r.status_code == 200
        assert r.text == ""  # kein job_stats auf diesem Fake-Client (kein scheduler)


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
