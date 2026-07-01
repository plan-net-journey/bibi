"""Stufe 6 — Politur (Frontend-Plan §C.6): Live-Uhr, Job-ID-Links im Log,
konsistente Nav. Das ursprüngliche „Auto-Auf des aktiv-Bandes bei neuem Lauf"
(Entscheidung #6) ist mit PLAN-14 Stufe 14.4 bewusst entfallen (Klapp-Logik
komplett ersetzt durch feste Überschriften + scrollbare max-height-Areas,
User-bestätigt) — die zugehörigen Tests sind daher hier nicht mehr vorhanden."""

from __future__ import annotations

from bibi.controller import render


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
