"""Stufe 5 — SSE-Live-Output im Schedule-Detail (Frontend-Plan §C.5).

Der laufende Output strömt per ``/-/job/{id}/stream`` (~0.2 s) statt im 2 s-Sprung.
Die Box wird server-seitig mit dem aktuellen Output **geseedet** (no-JS-Paint +
Offset) und per EventSource ab ``?from=N`` weitergestreamt; ``hx-preserve`` hält sie
samt EventSource über den 2 s-``#detail``-Poll am Leben."""

from __future__ import annotations

from bibi.controller import render


# ── live_output_box (pure) ────────────────────────────────────────────────────


def test_live_output_box_markup():
    html = render.live_output_box("j7", [], kind="job")
    assert 'class="term liveterm"' in html
    assert 'data-job="j7"' in html
    assert 'data-from="0"' in html       # kein Seed → Stream ab 0
    assert 'hx-preserve="true"' in html  # überlebt den #detail-Poll


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
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_page({"slug": "a", "kind": "job"}, [], job, slug="a")
    assert "EventSource" in html and "/-/job/" in html and "/stream" in html


def test_live_js_connects_to_formatted_output_stream_not_raw_stream():
    # Follow-up zu PLAN-14: die Live-Box hing an /stream (roh) — für Claude-Jobs
    # sah man dort rohes stream-json statt formatiertem Text. Jetzt /output/stream
    # (formatiert, gleiche Offset-Einheit wie der /output-Seed).
    assert "/output/stream?from=" in render._LIVE_JS
    assert "'/stream?from='" not in render._LIVE_JS


def test_live_js_appends_delta_events_to_last_span():
    # o.delta === true ⇒ an die zuletzt gerenderte Zeile anhängen statt neue
    # Timestamp-Zeile zu erzeugen (Token-Level-Deltas).
    assert "o.delta" in render._LIVE_JS
    assert "_bibiLastSpan" in render._LIVE_JS


def test_live_js_marks_thinking_stream_with_own_class():
    assert "'thinking'" in render._LIVE_JS


def test_live_js_scrolls_to_bottom_on_initial_bind():
    # PLAN-19 Befund 2, live reproduziert 2026-07-06: eine Box mit bereits
    # überfüllendem Seed-Inhalt hat scrollTop=0 beim ersten attach() — atBottom()
    # liefert dann von Anfang an "false" und FOLLOW greift für den Rest der
    # Seiten-Lebenszeit nie mehr, egal wie viel neuer Output ankommt. Fix: sofort
    # ans Ende springen, bevor der erste EventSource-Event überhaupt eintrifft.
    js = render._LIVE_JS
    bind_section = js.split("bound.add(box);")[1].split("const id = box.dataset.job")[0]
    assert "box.scrollTop = box.scrollHeight;" in bind_section


def test_live_js_preserves_liveclamp_scroll_across_poll():
    # User-Feedback: .liveclamp (awaiting/terminal-Output) hat kein hx-preserve
    # wie .liveterm — der 2s-#live-Poll ersetzt es per outerHTML, ein frisches
    # Element hat scrollTop=0 und "springt" sichtbar nach oben. Scroll muss vor
    # dem Swap gemerkt und danach am neuen Element wiederhergestellt werden.
    js = render._LIVE_JS
    assert "htmx:beforeSwap" in js and "htmx:afterSettle" in js
    assert ".liveclamp" in js
    assert "box.scrollTop = saved" in js


def test_live_js_resticks_liveterm_to_bottom_across_poll():
    # User-Feedback 2026-07-07 ("ich muss manuell herunterscrollen"), live im DOM
    # gemessen: trotz hx-preserve setzt der 2s-#live-Poll scrollTop einer laufenden
    # .liveterm-Box auf 0 zurück (Browser-Nebeneffekt beim Re-Attach desselben
    # Elements) — Inhalt + EventSource überleben, der Scroll-Zustand nicht. Die
    # onmessage-Stick-Logik korrigiert das nur reaktiv bei der nächsten SSE-
    # Nachricht, dazwischen bleibt FOLLOW wirkungslos. Analog zum .liveclamp-Fix,
    # aber mit "war ich unten?"-Semantik statt absoluter Positions-Wiederherstellung
    # (die Box soll dem neuen Ende folgen, nicht zur alten Pixel-Position zurück).
    js = render._LIVE_JS
    assert js.count("htmx:beforeSwap") == 2 and js.count("htmx:afterSettle") == 2
    liveterm_section = js.split(".liveclamp'")[-1]
    assert "'.liveterm[data-job]'" in liveterm_section
    assert "wasAtBottom" in liveterm_section
    assert "box.scrollTop = box.scrollHeight" in liveterm_section
