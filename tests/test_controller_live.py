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


# ── _live_panel: streamende Box statt 2s-Snapshot ─────────────────────────────


def test_live_panel_running_renders_stream_box():
    job = {"id": "jX", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_inner({"slug": "a"}, [], job, slug="a", now=5.0)
    assert "liveterm" in html and 'data-job="jX"' in html


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
