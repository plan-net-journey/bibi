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


def test_log_page_includes_feed_status_header():
    # PLAN-27 Befund 2, User-Fund: denselben Host/Mode/Git/Job-Status-Kopf
    # wie auf /-/ und /-/ui/schedules auch im Live-Log zeigen.
    html = render.log_page(
        daemon_status={"job_stats": {"counts": {"running": 1}, "complete_since_uptime": 3,
                                     "next_due_at": None}},
        git_status={"tree": "clean", "sync": "synced", "branch": "trunk"},
        host_url="http://sarasate.tail9f9173.ts.net:8780")
    assert 'id="feedstatus"' in html
    # bibi5: zwei Bloecke nach Herkunft statt vier Kacheln — links dieser
    # Knoten, rechts der Scheduler (FE-Spezifikation §2).
    assert html.count('class="hdr-block') == 2


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
    #
    # Seit #68 stehen die Werte als --term-*-Token statt als Literale. Geprüft
    # wird deshalb der Sachverhalt und nicht mehr die Schreibweise: dass alle
    # drei Boxen dieselbe Quelle benutzen UND diese Quelle in beiden Themes
    # denselben Wert trägt. Das ist schärfer als vorher — ein Literal konnte
    # nur an einer Stelle falsch sein, ein Token kann in einem Satz abweichen,
    # und genau das würde die Boxen theme-abhängig machen.
    import re

    for box in (".term {", ".logbox {", ".md pre {"):
        decl = render._CSS[render._CSS.index(box):][:220]
        assert "var(--term-bg)" in decl, box
        assert "var(--term-text)" in decl, box

    vals: dict[str, set[str]] = {}
    for sel in (":root", ':root[data-theme="light"]', ':root[data-theme="dark"]'):
        m = re.search(re.escape(sel) + r"\s*\{(.*?)\n\}", render._CSS, re.S)
        assert m, sel
        for tok in ("--term-bg", "--term-text"):
            hit = re.search(tok + r"\s*:\s*([^;]+);", m.group(1))
            assert hit, f"{tok} fehlt in {sel}"
            vals.setdefault(tok, set()).add(hit.group(1).strip())
    for tok, seen in vals.items():
        assert len(seen) == 1, f"{tok} ist theme-abhängig geworden: {seen}"
