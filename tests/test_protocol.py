"""Tests für bibi.protocol (Turn-Extraktion aus dem CC-Transcript)."""

from __future__ import annotations

import json
from pathlib import Path

from bibi import protocol


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _transcript(tmp_path: Path, *, with_tool: bool = False) -> Path:
    rows = [
        {"type": "user", "uuid": "u1", "message": {"content": "frage?"}},
    ]
    if with_tool:
        rows.append({
            "type": "assistant", "uuid": "a0",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]},
        })
    rows.append({
        "type": "assistant", "uuid": "a1", "sessionId": "s1",
        "timestamp": "2026-06-25T10:00:00Z",
        "message": {
            "model": "claude-opus", "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
            "content": [{"type": "text", "text": "antwort"}],
        },
    })
    p = tmp_path / "transcript.jsonl"
    _write_jsonl(p, rows)
    return p


def test_append_compact_entry(tmp_path: Path):
    folder = tmp_path / "case"
    folder.mkdir()
    protocol.append_turn(folder, "./protocol.json", _transcript(tmp_path))
    entries = [json.loads(l) for l in (folder / "protocol.json").read_text().splitlines()]
    assert len(entries) == 1
    e = entries[0]
    assert e["prompt"] == "frage?"
    assert e["final"] == "antwort"
    assert e["model"] == "claude-opus"
    assert e["stop_reason"] == "end_turn"
    assert e["usage"]["output_tokens"] == 3
    assert "raw_messages" not in e  # compact


def test_append_debug_adds_tools_and_raw(tmp_path: Path):
    folder = tmp_path / "case"
    folder.mkdir()
    protocol.append_turn(folder, "./protocol.json+debug", _transcript(tmp_path, with_tool=True))
    e = json.loads((folder / "protocol.json").read_text().splitlines()[-1])
    assert "raw_messages" in e
    assert e["tools_used"] == [{"name": "Bash", "input": {"command": "ls"}}]


def test_idempotent_same_turn_not_appended_twice(tmp_path: Path):
    folder = tmp_path / "case"
    folder.mkdir()
    t = _transcript(tmp_path)
    protocol.append_turn(folder, "./protocol.json", t)
    protocol.append_turn(folder, "./protocol.json", t)  # gleicher turn_uuid
    lines = (folder / "protocol.json").read_text().splitlines()
    assert len(lines) == 1


def test_empty_protocol_field_writes_nothing(tmp_path: Path):
    folder = tmp_path / "case"
    folder.mkdir()
    protocol.append_turn(folder, "", _transcript(tmp_path))
    assert not (folder / "protocol.json").exists()


def test_missing_transcript_writes_nothing(tmp_path: Path):
    folder = tmp_path / "case"
    folder.mkdir()
    protocol.append_turn(folder, "./protocol.json", tmp_path / "nope.jsonl")
    assert not (folder / "protocol.json").exists()


def test_incomplete_turn_no_assistant(tmp_path: Path):
    folder = tmp_path / "case"
    folder.mkdir()
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [{"type": "user", "uuid": "u1", "message": {"content": "nur frage"}}])
    protocol.append_turn(folder, "./protocol.json", p)
    assert not (folder / "protocol.json").exists()
