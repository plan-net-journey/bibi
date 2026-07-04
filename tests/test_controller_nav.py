"""Gemeinsame Navigationsleiste (``_header()``): Tab-Leiste + FOLLOW- +
THEME-Toggle + Ops-Handles (RESCAN/MAINT) auf jedem Screen — inkl. Live-Log
(User-Feedback 2026-07-04: "ziehe Rescan und Maintenance CTA auf die obere
Navigationsleiste mit FOLLOW on/off"). Der Feed-Screen ist entfernt (User-
Feedback: "entferne den Feed, den will ich nicht mehr sehen"); Schedules ist
jetzt der Home-Screen (``/-/``)."""

from __future__ import annotations

from bibi.controller import render


def test_header_includes_follow_toggle():
    html = render._header("Schedules")
    assert 'id="follow"' in html and "bibiToggleFollow" in html


def test_header_includes_theme_toggle():
    html = render._header("Schedules")
    assert 'id="theme"' in html and "bibiToggleTheme" in html


def test_header_includes_ops_handles():
    # RESCAN/MAINT sitzen jetzt direkt im Header, nicht mehr als separater Aufruf.
    html = render._header("Schedules", {"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html


def test_screen_nav_has_no_feed_tab():
    html = render._screen_nav("Live-Log")
    assert "Feed" not in html
    assert 'href="/-/"' in html  # Schedules-Tab zeigt jetzt auf Home


def test_ops_handles_no_longer_duplicates_follow_button():
    # FOLLOW sitzt separat im gemeinsamen Header — _ops_handles() bleibt frei davon.
    html = render._ops_handles()
    assert 'id="follow"' not in html


def test_schedules_page_has_exactly_one_follow_and_theme_button():
    html = render.schedules_page([], now=1.0)
    assert html.count('id="follow"') == 1
    assert html.count('id="theme"') == 1


def test_follow_toggle_snaps_output_boxes_to_bottom_on_reenable():
    # User-Feedback: FOLLOW wieder anschalten muss die Live-Boxen (.liveterm auf
    # dem Job-Detail) sofort ans Ende scrollen — sonst bleibt "stick" auf false
    # hängen (atBottom() sah die eingefrorene Scroll-Position) und die Box folgt
    # trotz eingeschaltetem FOLLOW nie wieder. ``#feed`` ist mit dem Feed-Screen
    # entfallen (Feed entfernt).
    js = render._FOLLOW_JS
    on_branch = js.split("if (window.bibiFollow){")[1]
    assert "querySelectorAll('.liveterm')" in on_branch
    assert "box.scrollTop = box.scrollHeight" in on_branch


def test_schedule_detail_page_has_header_nav_and_follow():
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_page({"slug": "a", "kind": "job"}, [], job, slug="a")
    assert 'href="/-/"' in html and 'href="/-/ui/logs"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' in html


def test_schedule_detail_page_has_rescan_and_maint():
    # User-Feedback 2026-07-03: "brauchen den Rescan und Maintenance Button
    # auf Schedule Screen" — auf der Job-Detail-Seite ebenso wie auf der
    # Schedules-Liste (s.u.), außerhalb von #live/#journal (kein 2s-Re-Render).
    html = render.schedule_detail_page(
        {"slug": "a", "kind": "job"}, [], None, slug="a",
        daemon_status={"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html
    assert render._OPS_HANDLES_JS in html


def test_schedules_page_has_rescan_and_maint():
    html = render.schedules_page([], daemon_status={"maintenance": False})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: aus" in html
    assert render._OPS_HANDLES_JS in html


def test_execution_detail_page_has_header_nav_and_follow():
    entry = {"id": 1, "run_id": "x:1", "slug": "x", "kind": "job", "status": "complete",
             "started_at": 1.0, "finished_at": 2.0, "domain": "scheduled"}
    html = render.execution_detail_page(entry, [], "job")
    assert 'href="/-/"' in html and 'href="/-/ui/logs"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' in html


def test_execution_detail_page_has_rescan_and_maint():
    # User-Feedback 2026-07-04: Rescan/Maintenance auf der Nav-Leiste, dadurch
    # jetzt auch auf der Execution-Detail-Seite (vorher gar nicht vorhanden).
    entry = {"id": 1, "run_id": "x:1", "slug": "x", "kind": "job", "status": "complete",
             "started_at": 1.0, "finished_at": 2.0, "domain": "scheduled"}
    html = render.execution_detail_page(entry, [], "job", daemon_status={"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html


def test_log_page_has_rescan_maint_and_follow():
    # User-Feedback 2026-07-04: "Sie sind damit auch auf Live-Log sichtbar" —
    # Live-Log hatte bisher weder Ops-Handles noch ein funktionierendes FOLLOW
    # (_FOLLOW_JS fehlte).
    html = render.log_page(daemon_status={"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html
    assert 'id="follow"' in html and "bibiToggleFollow" in html
    assert render._FOLLOW_JS in html
