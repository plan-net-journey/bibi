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


def test_host_card_shows_next_due_and_client_count_on_host_branch():
    # Bibi4-Iteration, User-Fund: "next in ..." wandert von der Job-Status-
    # Kachel hierher, plus Anzahl verbundener Clients.
    status = {
        "hostname": "sarasate",
        "job_stats": {"next_due_at": 400.0},
        "workers": [{"stale": False}, {"stale": False}, {"stale": True}],
    }
    html = render._host_card(status, "http://localhost:8780", now=100.0)
    assert '<div class="sub">next in 5 min · 2 clients connected</div>' in html


def test_host_card_next_due_none_and_no_clients_shows_dash_and_zero():
    status = {"hostname": "sarasate"}
    html = render._host_card(status, "http://localhost:8780", now=100.0)
    assert '<div class="sub">next — · 0 clients connected</div>' in html


def test_host_card_client_branch_has_no_next_due_sub_line():
    # Die Client-Karte (mit connect-Rolle) zeigt weiterhin nur Heartbeat,
    # kein "next"/Client-Count — das ist ausschließlich Host-Perspektive.
    html = render._host_card(
        {"connect": {"ok": True, "last_at": 96.0}},
        "http://sarasate.tail9f9173.ts.net:8780", now=100.0)
    assert "next" not in html
    assert "clients connected" not in html


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


def test_job_status_card_complete_uses_cumulative_counter_not_live_count():
    # complete_since_uptime (kumulativ seit Start), NICHT counts["complete"]
    # (Live-Zählung aktiver Jobs — sinkt, sobald abgeschlossene Jobs
    # archiviert werden).
    job_stats = {"counts_by_kind": {}, "complete_since_uptime": 47, "next_due_at": None}
    html = render._job_status_card(job_stats, now=100.0)
    assert "47 complete" in html


def test_job_status_card_sub_line_shows_only_complete_count():
    # Bibi4-Iteration, User-Fund: "next in ..." wandert in die Host-Kachel
    # (s. test_host_card_shows_next_due_and_client_count_on_host_branch) —
    # hier bleibt nur noch der Complete-Zähler übrig.
    html = render._job_status_card(
        {"counts_by_kind": {}, "complete_since_uptime": 3, "next_due_at": 400.0}, now=100.0)
    assert '<div class="sub">3 complete</div>' in html
    assert "next" not in html


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


def test_feed_status_fragment_includes_job_status_card_when_present():
    html = render.feed_status_fragment(
        {"job_stats": {"counts": {"running": 1}, "complete_since_uptime": 5,
                       "next_due_at": None}},
        None, None, now=100.0)
    assert html.count('<div class="card">') == 4
    assert '<div class="jobstatus-grid">' in html


def test_feed_status_fragment_omits_job_status_card_without_job_stats_or_client_rows():
    # Weder job_stats (job_db, scheduler-Rolle) noch client_rows (Discovery-
    # Liste, Bibi4-Iteration) vorhanden — z. B. Job-/Run-Detailseiten — keine
    # leere 4. Kachel.
    html = render.feed_status_fragment({}, None, None, now=100.0)
    assert html.count('<div class="card">') == 3
    assert '<div class="jobstatus-grid">' not in html


def test_feed_status_fragment_shows_client_card_when_client_rows_given():
    # Bibi4-Iteration, User-Brainstorm: 4. Kachel für Knoten ohne job_stats,
    # gefüttert aus derselben Discovery-Liste wie die Jobs-Tabelle.
    rows = [{"payload": "echo x", "app_port": None, "git_status": "modified"}]
    html = render.feed_status_fragment({}, None, None, now=100.0, client_rows=rows)
    assert html.count('<div class="card">') == 4
    assert '<div class="jobstatus-grid">' in html
    assert '<div class="jsg-k">Modified</div><div class="jsg-v">1</div>' in html


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


def test_feed_status_fragment_prefers_host_card_when_both_present():
    # job_stats (scheduler) gewinnt, falls aus irgendeinem Grund beides
    # übergeben würde — die Host-Karte ist die maßgebliche für diese Rolle.
    status = {"job_stats": {"counts_by_kind": {}, "complete_since_uptime": 0,
                            "next_due_at": None}}
    html = render.feed_status_fragment(
        status, None, None, now=100.0, client_rows=[{"payload": "x", "app_port": None}])
    assert '<div class="jsg-h"></div><div class="jsg-h">Job</div>' in html


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
    assert 'hx-trigger="every 30s [window.bibiFollow], bibiMaintChanged from:body"' in html
    assert 'hx-swap="outerHTML"' in html


def test_feed_status_fragment_uses_explicit_poll_interval():
    html = render.feed_status_fragment({}, None, None, now=100.0, poll_interval_s=60)
    assert 'hx-trigger="every 60s [window.bibiFollow], bibiMaintChanged from:body"' in html


# --- Job-Status-Kachel: eigener, schnellerer Poll (Bibi4-Iteration, User-Fund:
# --- "da es sich um eine sqlite db Abfrage handelt, sollte eine 1-2 Sekunden
# --- Abfrage aber möglich sein") -------------------------------------------


def test_job_status_fragment_self_polls_with_default_interval():
    html = render.job_status_fragment(
        {"counts_by_kind": {}, "complete_since_uptime": 0, "next_due_at": None}, now=100.0)
    assert 'id="jobstatuscard"' in html
    assert 'hx-get="/-/ui/feed/jobstatus"' in html
    assert 'hx-trigger="every 2s [window.bibiFollow]"' in html
    assert '<div class="jobstatus-grid">' in html


def test_job_status_fragment_uses_explicit_poll_interval():
    html = render.job_status_fragment(
        {"counts_by_kind": {}, "complete_since_uptime": 0, "next_due_at": None},
        now=100.0, poll_interval_s=1)
    assert 'hx-trigger="every 1s [window.bibiFollow]"' in html


def test_job_status_fragment_empty_without_job_stats():
    assert render.job_status_fragment(None, now=100.0) == ""


def test_feed_status_fragment_nests_job_status_fragment_with_its_own_poll():
    # Job Status pollt jetzt schneller als der Rest (Host/Mode/Git bleiben am
    # 30s-Bundle, s. Docstring) — beide Poll-Container stehen verschachtelt
    # im selben .statuscards-Grid.
    html = render.feed_status_fragment(
        {"job_stats": {"counts_by_kind": {}, "complete_since_uptime": 5, "next_due_at": None}},
        None, None, now=100.0, job_status_poll_interval_s=1)
    assert 'id="feedstatus"' in html
    assert 'hx-trigger="every 30s [window.bibiFollow], bibiMaintChanged from:body"' in html
    assert 'id="jobstatuscard"' in html and 'hx-trigger="every 1s [window.bibiFollow]"' in html
    assert html.count('<div class="card">') == 4


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


def test_feed_row_uses_time_toggle_cell():
    # Bibi4-Iteration, User-Fund: "der Timer funktioniert gut bei Job und
    # Archive. Er muss aber die Zeiten ebenfalls im Feed umschalten!" — bisher
    # fest absolut über _abs_datetime(), unabhängig vom Time-Toggle.
    e = {"kind": "vault", "name": "x.md", "last_changed": 90.0,
        "authors": ["Alice"], "all_agent": False}
    html = render._feed_row(e, now=100.0)
    assert '<span class="t"><span class="tt-abs">' in html
    assert 'class="tt-relonly"' in html and 'class="tt-relboth"' in html


def test_frow_children_allow_wrapping():
    # Bibi4-Iteration, User-Fund: "der Umbruch im Feed funktioniert noch
    # nicht fehlerfrei" — lange Slugs und die kommagetrennte Autorenliste
    # liefen über den Rand, weil Flex-Items ohne min-width:0 nicht unter ihre
    # Content-Breite schrumpfen, egal was overflow-wrap sagt.
    css = render._CSS
    assert ".frow .msg { flex: 1; min-width: 0; overflow-wrap: anywhere; }" in css
    assert "overflow-wrap: anywhere;" in css.split(".frow .who {")[1].split("}")[0]


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


def test_feed_row_agent_entity_marks_who_column_automated():
    # Bibi4-Iteration, User-Fund: "warum erscheint hier mein Name" — all_agent
    # steckte bisher nur in data-agent (fürs Filtern), nicht sichtbar in der
    # Autor-Spalte selbst. Der rohe Git-Autor bleibt "m.rau" (ambiente
    # Identität, s. _feed_row()-Docstring) — deshalb hier ein Zusatz statt
    # eines anderen Namens.
    e = {"kind": "vault", "name": "x.md", "last_changed": 90.0,
        "authors": ["m.rau"], "all_agent": True}
    html = render._feed_row(e, now=100.0)
    assert '<span class="who">m.rau · automatisiert</span>' in html


def test_feed_row_non_agent_entity_who_column_unchanged():
    e = {"kind": "vault", "name": "x.md", "last_changed": 90.0,
        "authors": ["m.rau"], "all_agent": False}
    html = render._feed_row(e, now=100.0)
    assert '<span class="who">m.rau</span>' in html
    assert "automatisiert" not in html


def test_feed_row_agent_entity_without_authors_shows_bare_dash():
    # Verteidigend: all_agent=True bei leerer Autorenliste (sollte praktisch
    # nie vorkommen, s. feed.py::group_entities()) darf kein "— · automatisiert"
    # ergeben.
    e = {"kind": "vault", "name": "x.md", "last_changed": 90.0,
        "authors": [], "all_agent": True}
    html = render._feed_row(e, now=100.0)
    assert '<span class="who">—</span>' in html


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
        assert '<div class="k">Konflikte</div>' in r.text
        assert '<div class="v sync-conflict">1</div>' in r.text


def test_root_route_status_cards_use_configured_poll_interval(
    app_with, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("BIBI_STATUS_POLL_INTERVAL", "45")
    app = app_with(_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/", headers={"Accept": "text/html"})
        assert 'hx-trigger="every 45s [window.bibiFollow], bibiMaintChanged from:body"' in r.text
