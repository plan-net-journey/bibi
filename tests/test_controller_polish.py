"""Stufe 6 — Politur (Frontend-Plan §C.6): Live-Uhr, Job-ID-Links im Log,
Auto-Auf des aktiv-Bandes bei neuem Lauf, konsistente Nav."""

from __future__ import annotations

from bibi.controller import render


def _job(slug, status, *, jid=None):
    return {"id": jid or slug, "slug": slug, "kind": "job", "status": status,
            "reason": None, "started_at": 900.0, "finished_at": None,
            "next_fire_at": None, "exit_code": None, "host": "h", "worker": "w",
            "output_ref": None, "priority": 0, "enqueued_at": 0, "attempt": 0}


# ── Live-Uhr ──────────────────────────────────────────────────────────────────


def test_live_clock_markup():
    html = render._live_clock()
    assert 'id="liveclock"' in html and "live" in html


def test_feed_page_ticks_clock():
    html = render.feed_page([], jobs=[], now=1.0)
    assert 'id="liveclock"' in html
    assert "setInterval" in html and "toLocaleTimeString" in html  # tickt clientseitig


def test_schedules_page_has_clock():
    assert 'id="liveclock"' in render.schedules_page([], now=1.0)


# ── Nav-Konsistenz + Log-Links ────────────────────────────────────────────────


def test_log_page_has_nav_and_clock():
    html = render.log_page()
    assert 'id="liveclock"' in html
    assert 'href="/-/ui/feed"' in html and 'href="/-/ui/schedules"' in html
    assert "new EventSource('/-/log/stream" in html  # Live-Quelle bleibt


def test_log_links_slug_to_schedule_detail():
    # Die Log-Zeilen-JS baut slug als Link zum Schedule-Detail.
    assert "/-/ui/schedule/" in render.log_page()


# ── Auto-Auf des aktiv-Bandes ─────────────────────────────────────────────────


def test_aktiv_row_marks_running_for_autoopen():
    html = render.bands_fragment([_job("a", "running", jid="j1")], now=1.0)
    assert 'data-running="j1"' in html


def test_bands_js_auto_opens_aktiv_on_new_run():
    # Die Band-JS öffnet das aktiv-Band bei einem neuen Lauf (Entscheidung #6).
    html = render.feed_page([], jobs=[], now=1.0)
    assert "data-running" in html and "bibiBand.aktiv" in html
