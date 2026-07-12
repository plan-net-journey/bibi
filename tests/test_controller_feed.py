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


def test_git_segment_card_conflict_shows_hash_and_both_deltas():
    # "conflict" = divergiert (ahead UND behind > 0), kein echter Merge-
    # Konflikt mit <<<<<<<-Markern — s. git_status.working_tree_status().
    html = render._git_segment_card(
        {"tree": "clean", "sync": "conflict", "branch": "trunk",
         "oid": "95a04a7197fd3e5dfb63283f591e8e77458bf401", "ahead": 23, "behind": 3})
    assert "conflict: 95a04a7 (+23, -3)" in html


def test_git_segment_card_without_oid_falls_back_to_plain_sync():
    # Ältere Aufrufer/Tests ohne oid-Feld — kein Crash, reines Zustandswort.
    html = render._git_segment_card({"tree": "clean", "sync": "synced", "branch": "trunk"})
    assert 'class="v sync-synced"' in html


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


# --- Feed-Kachel-Grid: jetzt 3 statt 6 (PLAN-19 Befund 4) -----------------------


def test_feed_status_cards_has_three_cards_no_rollen():
    html = render.feed_status_fragment(
        {"roles": ["connect"], "connect": {"ok": True, "last_at": 99.0}},
        {"tree": "clean", "sync": "synced", "branch": "trunk"},
        "http://sarasate.tail9f9173.ts.net:8780", now=100.0)
    assert html.count('<div class="card">') == 3
    assert "Rollen" not in html
    # PLAN-21 Befund 6: mit connect-Rolle heißt die erste Karte "Client".
    assert "Client" in html and "Mode" in html and "Git" in html


def test_feed_status_fragment_self_polls_with_default_interval():
    # PLAN-25 Befund 4, User-Fund: die Feed-Status-Kacheln wurden bisher nur
    # beim initialen Seitenaufbau gerendert, kein Polling — jetzt self-pollend
    # mit konfigurierbarem Intervall (Default 30s, BIBI_STATUS_POLL_INTERVAL).
    html = render.feed_status_fragment({}, None, None, now=100.0)
    assert 'id="feedstatus"' in html
    assert 'hx-get="/-/ui/feed/status"' in html
    assert 'hx-trigger="every 30s [window.bibiFollow]"' in html
    assert 'hx-swap="outerHTML"' in html


def test_feed_status_fragment_uses_explicit_poll_interval():
    html = render.feed_status_fragment({}, None, None, now=100.0, poll_interval_s=60)
    assert 'hx-trigger="every 60s [window.bibiFollow]"' in html


def test_status_cards_unchanged_after_refactor():
    # Regressionsschutz für die _status_card_list()-Extraktion: _status_cards()
    # muss weiterhin genau 4 Kacheln liefern, keine Git-Kachel.
    html = render._status_cards({"roles": ["connect"]}, now=100.0)
    assert html.count('<div class="card">') == 4


# --- Heatmap ---------------------------------------------------------------------


def test_heatmap_level_thresholds():
    assert render._heatmap_level(0) == 0
    assert render._heatmap_level(2) == 1
    assert render._heatmap_level(5) == 2
    assert render._heatmap_level(10) == 3
    assert render._heatmap_level(11) == 4


def test_heatmap_html_has_day_labels_and_correct_cell_count():
    grid = [[[0] * 8 for _ in range(7)] for _ in range(5)]
    grid[0][2][3] = 7
    html = render._heatmap_html(grid, now=100.0)
    # Alle 7 Wochentagsnamen tauchen irgendwo auf (rollierend, PLAN-19 Befund 5
    # — Reihenfolge/Position hängt jetzt vom Wochentag von "heute" ab, nicht
    # mehr fix Mo-So).
    for d in ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"):
        assert f">{d}<" in html
    assert html.count('class="hm-cell"') == 5 * 7 * 8 + 5  # + 5 Legende-Zellen
    assert 'data-lvl="3"' in html  # 7 Änderungen → Stufe 3


def test_heatmap_html_heading_is_short():
    # PLAN-25 Befund 5, User-Fund: Feed braucht Rahmen; dabei das sperrige
    # Label auf "Aktivität" kürzen statt der technischen Erklärung.
    grid = [[[0] * 8 for _ in range(7)] for _ in range(5)]
    html = render._heatmap_html(grid, now=100.0)
    assert "<h2>Aktivität</h2>" in html
    assert "1 Zeile je Woche" not in html


def test_heatmap_html_row_label_is_week_start_date():
    # PLAN-21 Befund 5, User-Fund: Datum des Wochenstarts statt "vor N
    # Wochen". 2026-07-08 ist ein Mittwoch; Woche 0 beginnt 6 Tage davor.
    import datetime
    now = datetime.datetime(2026, 7, 8, 10, 30).timestamp()
    grid = [[[0] * 8 for _ in range(7)] for _ in range(5)]
    html = render._heatmap_html(grid, now=now)
    assert ">02.07.<" in html
    assert "diese Woche" not in html


def test_heatmap_row_labels_are_rolling_week_starts():
    import datetime
    now = datetime.datetime(2026, 7, 8, 10, 30).timestamp()
    assert render._heatmap_row_labels(now, 3) == ["02.07.", "25.06.", "18.06."]


def test_heatmap_col_labels_last_column_is_todays_weekday():
    # PLAN-19 Befund 5, User-Entscheidung: Wochentag-Labels relativ zu heute.
    # 2026-07-08 10:30 ist ein Mittwoch ("Mi").
    import datetime
    now = datetime.datetime(2026, 7, 8, 10, 30).timestamp()
    labels = render._heatmap_col_labels(now)
    assert labels[-1] == "Mi"
    assert labels == ["Do", "Fr", "Sa", "So", "Mo", "Di", "Mi"]


# --- Feed-Filter (PLAN-20 Befund 1: 3-State statt Checkbox) --------------------


def test_feed_filter_bar_has_three_state_agent_select():
    html = render._feed_filter_bar()
    assert '<select id="feedagent"' in html
    assert '<option value="alle">alle</option>' in html
    assert '<option value="agents">nur Agents</option>' in html
    assert '<option value="team">nur Team</option>' in html
    assert "feedhideagents" not in html  # alte Checkbox weg


def test_feed_filter_js_matches_agents_only():
    js = render._FEED_FILTER_JS
    assert "agent === 'agents'" in js and "row.dataset.agent === '1'" in js


def test_feed_filter_js_matches_team_only():
    js = render._FEED_FILTER_JS
    assert "agent === 'team'" in js and "row.dataset.agent === '0'" in js


# --- Feed-Zeilen -----------------------------------------------------------------


def test_feed_row_shows_kind_badge_and_authors():
    e = {"kind": "case", "name": "20260601.FooBar", "last_changed": 90.0,
        "authors": ["Alice", "Bob"], "all_agent": False}
    html = render._feed_row(e, now=100.0)
    assert 'class="lvl case"' in html and "20260601.FooBar" in html
    assert "Alice, Bob" in html
    assert 'data-agent="0"' in html


def test_feed_row_shows_absolute_time_not_relative():
    # PLAN-19 Befund 6, User-Fund: absolutes Datum+Uhrzeit statt "vor 4 h".
    import datetime
    now = datetime.datetime(2026, 7, 8, 14, 0).timestamp()
    changed = datetime.datetime(2026, 7, 8, 10, 0).timestamp()  # heute
    e = {"kind": "vault", "name": "x.md", "last_changed": changed,
        "authors": ["Alice"], "all_agent": False}
    html = render._feed_row(e, now=now)
    assert "10:00" in html
    assert "vor " not in html


def test_feed_row_links_commit_hash_when_base_url_given():
    e = {"kind": "case", "name": "x", "last_changed": 90.0, "authors": ["Alice"],
        "all_agent": False, "last_commit_sha": "cf88e049ed9504cc045edb2b40ef5d476422e8fd"}
    html = render._feed_row(e, now=100.0,
                            commit_base_url="http://sarasate.tail9f9173.ts.net:3000/m.rau/bibi-notes")
    assert ('href="http://sarasate.tail9f9173.ts.net:3000/m.rau/bibi-notes/commit/'
           'cf88e049ed9504cc045edb2b40ef5d476422e8fd"') in html
    assert ">cf88e04<" in html  # kurzer Hash (7 Zeichen)


def test_feed_row_commit_hash_plain_without_base_url():
    e = {"kind": "case", "name": "x", "last_changed": 90.0, "authors": ["Alice"],
        "all_agent": False, "last_commit_sha": "cf88e049ed9504cc045edb2b40ef5d476422e8fd"}
    html = render._feed_row(e, now=100.0, commit_base_url=None)
    assert "<a " not in html
    assert 'class="commit">cf88e04<' in html


def test_feed_row_marks_agent_only_entity():
    e = {"kind": "vault", "name": "x.md", "last_changed": 90.0,
        "authors": ["bot"], "all_agent": True}
    html = render._feed_row(e, now=100.0)
    assert "is-agent" in html and 'data-agent="1"' in html


def test_feed_list_empty_placeholder():
    assert "keine Änderungen" in render._feed_list([], now=100.0)


# --- Fragment / Page ---------------------------------------------------------------


def test_feed_fragment_includes_heatmap_list_and_load_more():
    feed_data = {"entities": [{"kind": "system", "name": "System", "last_changed": 1.0,
                              "authors": ["a"], "all_agent": False}],
                "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_fragment(feed_data, days=3, now=100.0)
    assert 'id="feedboard"' in html
    assert "System" in html
    assert "mehr laden (4 Tage)" in html
    assert "gesamte Historie" not in html  # Fähigkeit gestrichen (PLAN-19 Befund 7)


def test_feed_fragment_hides_load_more_without_days():
    html = render.feed_fragment({"entities": [], "heatmap": []}, days=None, now=100.0)
    assert "mehr laden" not in html
    assert "gesamte Historie" not in html


# --- Heatmap-Nachladen, entkoppelt von days (PLAN-20 Befund 3) -----------------


def test_feed_fragment_heatmap_has_own_load_more_button():
    feed_data = {"entities": [], "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_fragment(feed_data, days=3, now=100.0)
    assert "mehr laden (6 Wochen)" in html
    assert 'hx-get="/-/ui/feed/board?days=3&weeks=6"' in html
    # Liste-Button hält das aktuelle (aus der Grid-Länge abgeleitete) Wochen-
    # Fenster konstant, statt es beim Nachladen zurückzusetzen.
    assert 'hx-get="/-/ui/feed/board?days=4&weeks=5"' in html


def test_feed_fragment_heatmap_load_more_uses_explicit_weeks_over_grid_length():
    feed_data = {"entities": [], "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_fragment(feed_data, days=3, weeks=8, now=100.0)
    assert 'hx-get="/-/ui/feed/board?days=3&weeks=9"' in html


def test_feed_fragment_no_heatmap_load_more_when_grid_empty():
    html = render.feed_fragment({"entities": [], "heatmap": []}, days=3, now=100.0)
    assert html.count("mehr laden (") == 1  # nur der Tage-Button, kein Wochen-Button
    assert 'hx-get="/-/ui/feed/board?days=4"' in html  # kein weeks= ohne Grid-Daten


def test_feed_fragment_wraps_heatmap_and_changes_in_own_panel_cards():
    # PLAN-25 Befund 5, User-Fund: Feed-Seite braucht Rahmen — Heatmap und
    # Änderungen-Block bekommen je ein eigenes .panel-card, wie Schedules es
    # schon nutzt.
    feed_data = {"entities": [{"kind": "system", "name": "System", "last_changed": 1.0,
                              "authors": ["a"], "all_agent": False}],
                "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_fragment(feed_data, days=3, now=100.0)
    assert html.count('class="panel-card"') == 2
    assert html.index('class="panel-card"') < html.index("Änderungen")


def test_feed_page_has_header_nav_and_status_cards():
    feed_data = {"entities": [], "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_page(feed_data, git_status={"tree": "clean", "sync": "synced",
                                                   "branch": "trunk"},
                            daemon_status={"roles": ["scheduler", "connect"]}, now=100.0)
    assert 'href="/-/ui/jobs"' in html and 'href="/-/ui/schedules"' in html
    assert "<title>bibi · Feed</title>" in html
    assert 'class="statuscards"' in html


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


def test_feed_status_fragment_route(app_with):
    # PLAN-25 Befund 4: Self-Poll-Ziel von #feedstatus.
    app = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/feed/status")
        assert r.status_code == 200
        assert 'id="feedstatus"' in r.text
        assert 'class="statuscards"' in r.text


def test_root_route_status_cards_use_configured_poll_interval(
    app_with, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("BIBI_STATUS_POLL_INTERVAL", "45")
    app = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/", headers={"Accept": "text/html"})
        assert 'hx-trigger="every 45s [window.bibiFollow]"' in r.text
