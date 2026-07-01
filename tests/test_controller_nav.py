"""Follow-up PLAN-14 (User-Feedback): FOLLOW-Toggle war nur auf dem Feed-Screen
sichtbar/steuerbar — jetzt Teil des gemeinsamen ``_header()``, also auf jedem
Screen da. schedule_detail_page()/execution_detail_page() hatten bislang gar
keine Tab-Navigation (nur Kontext-Rücklinks) — bekommen jetzt _header() dazu."""

from __future__ import annotations

from bibi.controller import render


def test_header_includes_follow_toggle():
    html = render._header("Feed")
    assert 'id="follow"' in html and "bibiToggleFollow" in html


def test_feed_handles_no_longer_duplicates_follow_button():
    # FOLLOW zieht in den gemeinsamen Header um — sonst zwei Buttons auf Feed.
    html = render._feed_handles()
    assert 'id="follow"' not in html


def test_feed_page_has_exactly_one_follow_button():
    html = render.feed_page([], jobs=[], now=1.0)
    assert html.count('id="follow"') == 1


def test_schedule_detail_page_has_header_nav_and_follow():
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_page({"slug": "a", "kind": "job"}, [], job, slug="a")
    assert 'href="/-/ui/feed"' in html and 'href="/-/ui/schedules"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' in html


def test_execution_detail_page_has_header_nav_and_follow():
    entry = {"id": 1, "run_id": "x:1", "slug": "x", "kind": "job", "status": "complete",
             "started_at": 1.0, "finished_at": 2.0, "domain": "scheduled"}
    html = render.execution_detail_page(entry, [], "job")
    assert 'href="/-/ui/feed"' in html and 'href="/-/ui/schedules"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' in html
