"""Follow-up PLAN-14 (User-Feedback): FOLLOW-Toggle war nur auf dem Feed-Screen
sichtbar/steuerbar — jetzt Teil des gemeinsamen ``_header()``, also auf jedem
Screen da. schedule_detail_page()/execution_detail_page() hatten bislang gar
keine Tab-Navigation (nur Kontext-Rücklinks) — bekommen jetzt _header() dazu."""

from __future__ import annotations

from bibi.controller import render


def test_header_includes_follow_toggle():
    html = render._header("Feed")
    assert 'id="follow"' in html and "bibiToggleFollow" in html


def test_ops_handles_no_longer_duplicates_follow_button():
    # FOLLOW zieht in den gemeinsamen Header um — sonst zwei Buttons auf Feed.
    html = render._ops_handles()
    assert 'id="follow"' not in html


def test_feed_page_has_exactly_one_follow_button():
    html = render.feed_page([], jobs=[], now=1.0)
    assert html.count('id="follow"') == 1


def test_follow_toggle_snaps_output_boxes_to_bottom_on_reenable():
    # User-Feedback: FOLLOW wieder anschalten muss die Live-Boxen (.liveterm auf
    # dem Job-Detail, #feed auf dem Feed) sofort ans Ende scrollen — sonst bleibt
    # "stick" auf false hängen (atBottom() sah die eingefrorene Scroll-Position)
    # und die Box folgt trotz eingeschaltetem FOLLOW nie wieder.
    js = render._FOLLOW_JS
    on_branch = js.split("if (window.bibiFollow){")[1]
    assert "querySelectorAll('.liveterm, #feed')" in on_branch
    assert "box.scrollTop = box.scrollHeight" in on_branch


def test_schedule_detail_page_has_header_nav_and_follow():
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_page({"slug": "a", "kind": "job"}, [], job, slug="a")
    assert 'href="/-/ui/feed"' in html and 'href="/-/ui/schedules"' in html
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
    assert 'href="/-/ui/feed"' in html and 'href="/-/ui/schedules"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' in html
