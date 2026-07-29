"""Ausgabefilter: stream-json → Klartext + Tool-Use-Summaries (PLAN-12 Stufe 12.4).

Reine Funktionen — kein I/O, keine Subprozesse.
"""

from __future__ import annotations

import json

from bibi.daemon import output_format


def _ev(line: str, *, s: str = "out", t: float | None = 1.0) -> dict:
    return {"t": t, "s": s, "line": line}


def _assistant_text(text: str) -> str:
    return json.dumps({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": text}]}})


def _assistant_tool_use(name: str, tool_input: dict) -> str:
    return json.dumps({"type": "assistant",
                       "message": {"content": [{"type": "tool_use", "name": name,
                                                "input": tool_input}]}})


def test_format_events_job_is_identity_passthrough():
    events = [_ev("hallo"), _ev("oops", s="err")]
    assert output_format.format_events(events, "job") == events


def test_claude_extracts_text_from_assistant_message():
    events = [_ev(_assistant_text("Hallo Welt"))]
    out = output_format.format_events(events, "claude")
    assert out == [{"t": 1.0, "s": "out", "line": "Hallo Welt"}]


def test_claude_extracts_multiline_text_split():
    events = [_ev(_assistant_text("Zeile 1\nZeile 2"))]
    out = output_format.format_events(events, "claude")
    assert [e["line"] for e in out] == ["Zeile 1", "Zeile 2"]


def test_claude_tool_use_bash_summary():
    events = [_ev(_assistant_tool_use("Bash", {"command": "ls -la"}))]
    out = output_format.format_events(events, "claude")
    lines = [e["line"] for e in out]
    assert lines == ["", "→ Bash: ls -la", ""]


def test_claude_tool_use_edit_write_read_notebookedit_summary():
    for name in ("Edit", "Write", "Read"):
        events = [_ev(_assistant_tool_use(name, {"file_path": "/x/y.py"}))]
        out = output_format.format_events(events, "claude")
        assert f"→ {name}: /x/y.py" in [e["line"] for e in out]
    events = [_ev(_assistant_tool_use("NotebookEdit", {"notebook_path": "/x/nb.ipynb"}))]
    out = output_format.format_events(events, "claude")
    assert "→ NotebookEdit: /x/nb.ipynb" in [e["line"] for e in out]


def test_claude_tool_use_task_with_subagent_summary():
    events = [_ev(_assistant_tool_use(
        "Task", {"subagent_type": "Explore", "description": "find the bug"}))]
    out = output_format.format_events(events, "claude")
    assert "→ Task (Explore): find the bug" in [e["line"] for e in out]


def test_claude_tool_use_task_without_subagent_summary():
    events = [_ev(_assistant_tool_use("Task", {"description": "generic task"}))]
    out = output_format.format_events(events, "claude")
    assert "→ Task: generic task" in [e["line"] for e in out]


def test_claude_tool_use_generic_fallback_first_string_field():
    events = [_ev(_assistant_tool_use("Grep", {"pattern": "foo", "path": "."}))]
    out = output_format.format_events(events, "claude")
    assert "→ Grep: foo" in [e["line"] for e in out]


def test_claude_tool_use_generic_fallback_no_string_field_only_toolname():
    events = [_ev(_assistant_tool_use("Weird", {"count": 5}))]
    out = output_format.format_events(events, "claude")
    assert "→ Weird" in [e["line"] for e in out]


def test_claude_truncates_long_bash_command_exact_length():
    long_cmd = "x" * 200
    events = [_ev(_assistant_tool_use("Bash", {"command": long_cmd}))]
    out = output_format.format_events(events, "claude")
    summary = next(e["line"] for e in out if e["line"].startswith("→ Bash:"))
    body = summary[len("→ Bash: "):]
    assert len(body) == 120
    assert body.endswith("…")


def test_claude_system_result_rate_limit_and_unknown_type_no_output():
    for t in ("system", "result", "rate_limit_event", "some_future_type"):
        events = [_ev(json.dumps({"type": t, "foo": "bar"}))]
        assert output_format.format_events(events, "claude") == []


def test_claude_user_type_no_output_events():
    events = [_ev(json.dumps({"type": "user", "message": {"content": []}}))]
    assert output_format.format_events(events, "claude") == []


def test_claude_stderr_lines_pass_through_unparsed():
    events = [_ev(_assistant_text("wird nie geparst"), s="err")]
    out = output_format.format_events(events, "claude")
    assert out == events


def test_claude_non_json_out_line_passes_through_raw():
    events = [_ev("Ganz normaler Klartext-Output (Alt-Lauf ohne stream-json)")]
    out = output_format.format_events(events, "claude")
    assert out == events


# ── Follow-up PLAN-14 — Token-Level-Deltas (--include-partial-messages) ─────


def _stream_event(inner_type: str, **kwargs) -> str:
    return json.dumps({"type": "stream_event", "event": {"type": inner_type, **kwargs}})


def test_claude_text_delta_first_chunk_starts_line_rest_append():
    events = [
        _ev(_stream_event("message_start")),
        _ev(_stream_event("content_block_start", content_block={"type": "text", "text": ""})),
        _ev(_stream_event("content_block_delta", delta={"type": "text_delta", "text": "Hal"})),
        _ev(_stream_event("content_block_delta", delta={"type": "text_delta", "text": "lo!"})),
    ]
    out = output_format.format_events(events, "claude")
    assert [(e["s"], e["line"], e["delta"]) for e in out] == [
        ("out", "Hal", False), ("out", "lo!", True),
    ]


def test_claude_thinking_delta_uses_thinking_marker():
    events = [
        _ev(_stream_event("message_start")),
        _ev(_stream_event("content_block_start", content_block={"type": "thinking", "thinking": ""})),
        _ev(_stream_event("content_block_delta", delta={"type": "thinking_delta", "thinking": "Hmm"})),
    ]
    out = output_format.format_events(events, "claude")
    assert out == [{"t": 1.0, "s": "thinking", "line": "Hmm", "delta": False}]


def test_claude_message_start_content_block_stop_message_delta_stop_no_output():
    for line in (_stream_event("message_start"), _stream_event("content_block_stop"),
                 _stream_event("message_delta", delta={"stop_reason": "end_turn"}),
                 _stream_event("message_stop")):
        assert output_format.format_events([_ev(line)], "claude") == []


def test_claude_complete_assistant_text_suppressed_after_delta_seen():
    # Die komplette assistant-Nachricht kommt nach den Deltas zusätzlich —
    # der Text darf nicht doppelt erscheinen.
    events = [
        _ev(_stream_event("message_start")),
        _ev(_stream_event("content_block_start", content_block={"type": "text", "text": ""})),
        _ev(_stream_event("content_block_delta", delta={"type": "text_delta", "text": "Hallo!"})),
        _ev(_stream_event("content_block_stop")),
        _ev(_assistant_text("Hallo!")),
    ]
    out = output_format.format_events(events, "claude")
    assert [e["line"] for e in out if e.get("s") == "out"] == ["Hallo!"]


def test_claude_complete_assistant_text_shown_without_deltas():
    # Alt-Lauf ohne --include-partial-messages (oder Journal vor diesem
    # Feature) — die komplette Nachricht bleibt die einzige Quelle.
    events = [_ev(_assistant_text("Hallo Welt"))]
    out = output_format.format_events(events, "claude")
    assert out == [{"t": 1.0, "s": "out", "line": "Hallo Welt"}]


def test_claude_tool_use_still_rendered_after_text_delta():
    events = [
        _ev(_stream_event("message_start")),
        _ev(_stream_event("content_block_start", content_block={"type": "text", "text": ""})),
        _ev(_stream_event("content_block_delta", delta={"type": "text_delta", "text": "ok"})),
        _ev(_stream_event("content_block_stop")),
        _ev(_assistant_tool_use("Bash", {"command": "ls"})),
    ]
    out = output_format.format_events(events, "claude")
    assert "→ Bash: ls" in [e["line"] for e in out if e.get("s") == "out"]
