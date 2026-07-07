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


def test_schedules_page_ticks_clock():
    html = render.schedules_page([], now=1.0)
    assert 'id="liveclock"' in html
    assert "setInterval" in html and "toLocaleTimeString" in html  # tickt clientseitig


def test_schedules_page_has_clock():
    assert 'id="liveclock"' in render.schedules_page([], now=1.0)


# ── Nav-Konsistenz + Log-Links ────────────────────────────────────────────────


def test_log_page_has_nav_and_clock():
    html = render.log_page()
    assert 'id="liveclock"' in html
    assert 'href="/-/"' in html
    assert "new EventSource('/-/log/stream" in html  # Live-Quelle bleibt


def test_log_links_slug_to_schedule_detail():
    # Die Log-Zeilen-JS baut slug als Link zum Schedule-Detail.
    assert "/-/ui/schedule/" in render.log_page()


# ── Terminal-Kontrast (PLAN-19 Stufe 19.1) ───────────────────────────────────


def test_term_and_logbox_stay_dark_regardless_of_theme():
    # User-Fund 2026-07-06: #0008 (halbtransparentes Schwarz) ergab im
    # Light-Mode nur mittelgrau statt dunkel, dazu erbte unfarbiger Text die
    # Body-Textfarbe (dunkel im Light-Mode) — auf jetzt dunklem Grund
    # unleserlich. Fester Hintergrund + feste helle Textfarbe, unabhängig von
    # :root[data-theme].
    assert ".term { background: #1a1a1a; color: #ddd;" in render._CSS
    assert ".logbox { height: 72vh; overflow-y: auto; background: #1a1a1a; color: #ddd;" in render._CSS
    assert ".md pre { background: #1a1a1a; color: #ddd;" in render._CSS
