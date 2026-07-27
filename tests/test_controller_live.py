"""Stufe 5 — Live-Output im Schedule-Detail (Frontend-Plan §C.5).

Seit PLAN-36 Stufe 36.2: die Box wird server-seitig mit dem aktuellen Output
**geseedet** (no-JS-Paint + Offset), neue Zeilen kommen als ``append``-Events
über den EINEN globalen Event-Strom (``GET /-/events``, ``_EVENTS_JS``) —
keine per-Box-EventSource mehr, kein ``hx-preserve`` (die Box wird nur noch
bei echten Zustands-Refetches ersetzt, nicht pro Poll-Tick)."""

from __future__ import annotations

from bibi.controller import render


# ── live_output_box (pure) ────────────────────────────────────────────────────


def test_live_output_box_markup():
    html = render.live_output_box("j7", [], kind="job")
    assert 'class="term liveterm"' in html
    assert 'data-job="j7"' in html
    assert 'data-from="0"' in html       # kein Seed → Appends ab off 1
    # PLAN-36 Stufe 36.2: kein hx-preserve mehr — die Box wird nur noch bei
    # echten Zustands-Refetches ersetzt (frischer Seed + data-from = Heilung),
    # das Attribut schützte zuletzt nichts und blockierte auf Client-Seiten
    # den einzigen Update-Weg (FE-Live-Update-Briefing Befund 1).
    assert "hx-preserve" not in html


def test_live_output_box_seeds_events_and_offset():
    events = [{"s": "out", "line": "zeile1"}, {"s": "err", "line": "uff"}]
    html = render.live_output_box("j", events, kind="job")
    assert "zeile1" in html and "uff" in html
    assert 'class="err"' in html         # stderr-Zeile markiert
    assert 'data-from="2"' in html       # Stream setzt nach dem Seed an (kein Dup)


def test_live_output_box_escapes():
    html = render.live_output_box("j", [{"s": "out", "line": "<script>x"}], kind="job")
    assert "<script>x" not in html
    assert "&lt;script&gt;x" in html


def test_live_output_box_thinking_gets_own_class():
    html = render.live_output_box("j", [{"s": "thinking", "line": "hmm", "t": 1.0}], kind="claude")
    assert 'class="thinking"' in html and "hmm" in html


def test_live_output_box_merges_delta_events_into_one_line():
    # Follow-up PLAN-14 (Token-Level-Deltas): delta=True hängt an die vorherige
    # Zeile an statt eine neue mit eigenem Timestamp zu erzeugen.
    events = [
        {"s": "out", "line": "Hal", "t": 1.0, "delta": False},
        {"s": "out", "line": "lo!", "t": 1.0, "delta": True},
    ]
    html = render.live_output_box("j", events, kind="claude")
    assert html.count('class="lts"') == 1
    assert "Hal" in html and "lo!" in html
    assert html.index("Hal") < html.index("lo!")


# ── _live_panel: streamende Box statt 2s-Snapshot ─────────────────────────────


def test_live_panel_running_renders_stream_box():
    job = {"id": "jX", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert "liveterm" in html and 'data-job="jX"' in html


def test_live_panel_links_to_raw_stream_for_running_job():
    # Follow-up (User-Feedback): "Es braucht auch den Zugriff/Ansicht des
    # originalen Streams (/stream)" — die Live-Box zeigt nur noch formatiert.
    job = {"id": "jX", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert 'href="/-/job/jX/stream"' in html


def test_live_panel_terminal_no_stream_box():
    job = {"id": "jX", "slug": "a", "status": "complete", "finished_at": 2.0}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert "liveterm" not in html


def test_live_panel_seeds_box_with_current_output():
    # Bestehendes Verhalten bleibt: aktueller Output sichtbar (jetzt im Box-Seed).
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    live = {"kind": "job", "events": [{"s": "out", "line": "lebt"}]}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0,
                                        live_output=live)
    assert 'class="liveout"' in html and "lebt" in html


def test_schedule_detail_page_wires_live_stream():
    # PLAN-36 Stufe 36.2: die Seite verbindet sich mit dem EINEN globalen
    # Strom (/-/events) — nicht mehr mit einer per-Box-Stream-Route.
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_page({"slug": "a", "kind": "job"}, [], job, slug="a")
    assert "EventSource('/-/events')" in html
    assert render._EVENTS_JS in html and render._SCROLL_JS in html


def test_events_js_connects_to_global_stream_only():
    # PLAN-36 Stufe 36.2: EINE EventSource auf /-/events pro Seite — keine
    # per-Box-Streams mehr (die Route existierte auf Client-Knoten ohnehin
    # nicht, FE-Live-Update-Briefing Befund 1).
    assert "EventSource('/-/events')" in render._EVENTS_JS
    assert "/output/stream" not in render._EVENTS_JS


def test_events_js_appends_delta_events_to_last_span():
    # o.delta === true ⇒ an die zuletzt gerenderte Zeile anhängen statt neue
    # Timestamp-Zeile zu erzeugen (Token-Level-Deltas) — Logik unverändert aus
    # dem früheren _LIVE_JS übernommen.
    assert "o.delta" in render._EVENTS_JS
    assert "_bibiLastSpan" in render._EVENTS_JS


def test_events_js_marks_thinking_stream_with_own_class():
    assert "'thinking'" in render._EVENTS_JS


def test_events_js_dedupes_appends_against_seed_offset():
    # Ein Bus-Refetch trägt frischen Seed + neuen data-from — nachlaufende
    # Appends mit off <= data-from müssen still verworfen werden (E2/E5).
    assert "ev.off <= from" in render._EVENTS_JS
    assert "data-bus-refetch" in render._EVENTS_JS


def test_events_js_never_closes_the_stream():
    # 2026-07-20-Lektion, unter dem Bus noch einfacher: der Strom lebt so
    # lange wie die Seite — kein done-Handling, kein close, kein eigener
    # onerror (der automatische Browser-Reconnect greift, der Server schickt
    # beim Reconnect den Resync aller aktiven Elemente, E5).
    assert "es.close" not in render._EVENTS_JS
    assert "es.onerror" not in render._EVENTS_JS


def test_events_js_scrolls_to_bottom_on_initial_bind():
    # PLAN-19 Befund 2 (2026-07-06-Lektion), unverändert gültig: eine Box mit
    # überfüllendem Seed hat scrollTop=0 — ohne initiales Ans-Ende-Springen
    # liefert atBottom() ab dem ersten Check false und FOLLOW bleibt dauerhaft
    # wirkungslos. Jetzt in initBoxes() (_EVENTS_JS).
    js = render._EVENTS_JS
    init_section = js.split("_bibiInit = true;")[1].split("appendLine")[0]
    assert "box.scrollTop = box.scrollHeight;" in init_section


def test_scroll_js_preserves_liveclamp_scroll_across_swap():
    # Swaps kommen jetzt vom Bus-Refetch + Sicherheitsnetz-Poll — das Problem
    # (frisches Element hat scrollTop=0) bleibt dasselbe; beide Regionen
    # (#live Host, #jobsdetail-live Client) sind abgedeckt.
    js = render._SCROLL_JS
    assert "htmx:beforeSwap" in js and "htmx:afterSettle" in js
    assert ".liveclamp" in js
    assert "box.scrollTop = saved" in js
    assert "jobsdetail-live" in js


def test_scroll_js_resticks_liveterm_across_swap():
    # PLAN-36 Stufe 36.0 (Befund 5, FE-Live-Update-Briefing, live doppelt
    # reproduziert): beide Fälle — war die Box unten (FOLLOW), folgt sie dem
    # NEUEN Ende (scrollHeight); war sie hochgescrollt (User liest alte
    # Zeilen), wird die absolute Position (savedTop) restauriert — vorher
    # fehlte dieser else-Zweig, der browserseitige Reset auf 0 blieb stehen.
    # Seit 36.2 lebt der Mechanismus in _SCROLL_JS (Swaps kommen jetzt vom
    # Bus-Refetch + Sicherheitsnetz-Poll statt vom 2s-Poll).
    js = render._SCROLL_JS
    assert js.count("htmx:beforeSwap") == 2 and js.count("htmx:afterSettle") == 2
    liveterm_section = js.split(".liveclamp'")[-1]
    assert "'.liveterm[data-job]'" in liveterm_section
    assert "wasAtBottom" in liveterm_section
    assert "savedTop" in liveterm_section
    assert "wasAtBottom ? box.scrollHeight : (savedTop ?? box.scrollTop)" in liveterm_section
