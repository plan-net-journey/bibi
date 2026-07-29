"""Integrationstests für `bibi-ctrl protocol` und `bibi-ctrl on-stop`."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

from bibi import case_store, frontmatter, state
from bibi.ctrl import main


def _activate(topic: str) -> Path:
    folder = case_store.create_case(topic)
    os.chdir(folder)
    return folder


def _proto_field(folder: Path):
    return frontmatter.read(folder / "README.md").get("protocol")


# --- protocol toggle ---

def test_protocol_on(team_repo, capsys):
    folder = _activate("Alpha")
    assert main(["protocol", "on"]) == 0
    assert _proto_field(folder) == "./protocol.json"


def test_protocol_debug(team_repo):
    folder = _activate("Alpha")
    main(["protocol", "debug"])
    assert _proto_field(folder) == "./protocol.json+debug"


def test_protocol_off_removes_field(team_repo):
    folder = _activate("Alpha")
    main(["protocol", "on"])
    main(["protocol", "off"])
    assert _proto_field(folder) is None


def test_protocol_requires_active_case(team_repo, capsys):
    # cwd = repo root, kein aktiver Case
    assert main(["protocol", "on"]) == 2
    assert "open" in capsys.readouterr().err


# --- on-stop hook ---

def _feed_hook(monkeypatch, transcript: Path):
    payload = json.dumps({"transcript_path": str(transcript)})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))


def _make_transcript(tmp_path: Path) -> Path:
    rows = [
        {"type": "user", "uuid": "u1", "message": {"content": "frage?"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "s1",
         "timestamp": "2026-06-25T10:00:00Z",
         "message": {"model": "m", "stop_reason": "end_turn", "usage": {},
                     "content": [{"type": "text", "text": "antwort"}]}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_on_stop_appends_when_protocol_on(team_repo, tmp_path, monkeypatch):
    folder = _activate("Alpha")
    main(["protocol", "on"])
    _feed_hook(monkeypatch, _make_transcript(tmp_path))
    rc = main(["on-stop"])
    assert rc == 0
    entry = json.loads((folder / "protocol.json").read_text().splitlines()[-1])
    assert entry["prompt"] == "frage?" and entry["final"] == "antwort"


def test_on_stop_noop_when_protocol_off(team_repo, tmp_path, monkeypatch):
    folder = _activate("Alpha")  # kein protocol-Feld gesetzt
    _feed_hook(monkeypatch, _make_transcript(tmp_path))
    rc = main(["on-stop"])
    assert rc == 0
    assert not (folder / "protocol.json").exists()


def test_on_stop_always_zero_without_active_case(team_repo, monkeypatch):
    # cwd = repo root; kaputtes Payload
    monkeypatch.setattr("sys.stdin", io.StringIO("nicht-json"))
    assert main(["on-stop"]) == 0


def test_on_stop_finds_case_via_session_id_in_payload(team_repo, tmp_path, monkeypatch):
    """Der echte Hook-Fall: der Hook läuft als eigener Prozess im
    Projektverzeichnis, nie im geparkten Case-cwd. Die ``session_id`` aus dem
    Payload ist sein einziger Zugang zum aktiven Case — ohne sie lief das
    Turn-Logging still ins Leere, egal ob protocol an war."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = _activate("Alpha")
    main(["protocol", "on"])
    state.set_path(f"case/{folder.name}")  # wie /open: Park-Marke schreiben

    # Ab hier ist es ein frischer Hook-Prozess: Repo-Root, keine Session in der
    # Umgebung — nur das Payload weiß, zu welcher Session er gehört.
    os.chdir(team_repo)
    monkeypatch.delenv("BIBI_SESSION_ID")
    state.adopt_session(None)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"transcript_path": str(_make_transcript(tmp_path)), "session_id": "sess-A"})))

    assert main(["on-stop"]) == 0
    entry = json.loads((folder / "protocol.json").read_text().splitlines()[-1])
    assert entry["prompt"] == "frage?" and entry["final"] == "antwort"


def test_on_stop_ignores_a_foreign_sessions_case(team_repo, tmp_path, monkeypatch):
    """Parallele Sessions: der Hook der einen Session darf nicht ins Protokoll
    der anderen schreiben."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    folder = _activate("Alpha")
    main(["protocol", "on"])
    state.set_path(f"case/{folder.name}")

    os.chdir(team_repo)
    monkeypatch.delenv("BIBI_SESSION_ID")
    state.adopt_session(None)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"transcript_path": str(_make_transcript(tmp_path)), "session_id": "sess-B"})))

    assert main(["on-stop"]) == 0
    assert not (folder / "protocol.json").exists()
