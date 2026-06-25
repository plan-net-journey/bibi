"""Pro-Turn-Einträge an ``<case>/protocol.json`` anhängen (PLAN-1 §1.4).

Quelle ist das Session-Log (JSONL) von Claude Code, vom Stop-Hook übergeben.
Das README-Frontmatter-Feld ``protocol:`` steuert den Modus:
- ``./protocol.json``        → kompakter Eintrag
- ``./protocol.json+debug``  → kompakt + tools_used + raw_messages
- fehlt                      → kein Anhängen
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

PROTOCOL_FILENAME = "protocol.json"


def append_turn(folder: Path, protocol_field: str, cc_log_path: Path) -> None:
    """Letzten abgeschlossenen Turn aus ``cc_log_path`` an protocol.json anhängen.

    Idempotent: überspringt, wenn die turn_uuid bereits am Ende von protocol.json
    steht. Best-effort: bei jedem Parse-/IO-Fehler still zurück.
    """
    if not protocol_field:
        return

    is_debug = protocol_field.endswith("+debug")
    rel_path = protocol_field[:-len("+debug")] if is_debug else protocol_field
    target = (folder / rel_path).resolve()

    if not cc_log_path.exists():
        return

    messages = _read_jsonl(cc_log_path)
    turn = _extract_last_turn(messages)
    if turn is None:
        return

    if _already_written(target, turn["turn_uuid"]):
        return

    entry = _build_entry(turn, is_debug)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _is_real_user_message(msg: dict[str, Any]) -> bool:
    """True, wenn dies ein Nutzer-Prompt ist (kein tool_result-Wrapper)."""
    if msg.get("type") != "user":
        return False
    content = msg.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        )
    return False


def _extract_last_turn(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Letzten Nutzer-Prompt finden; alles danach einsammeln."""
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if _is_real_user_message(messages[i]):
            last_user_idx = i
            break
    if last_user_idx is None:
        return None

    turn_messages = messages[last_user_idx:]
    user_msg = turn_messages[0]

    final_assistant: dict[str, Any] | None = None
    for m in reversed(turn_messages):
        if m.get("type") == "assistant":
            final_assistant = m
            break
    if final_assistant is None:
        return None  # Turn noch nicht abgeschlossen

    prompt = _extract_text(user_msg.get("message", {}).get("content"))
    final = _extract_text(final_assistant.get("message", {}).get("content"))

    msg_meta = final_assistant.get("message", {})
    return {
        "turn_uuid": final_assistant.get("uuid", ""),
        "session_id": (
            final_assistant.get("sessionId")
            or final_assistant.get("session_id", "")
        ),
        "ts": final_assistant.get("timestamp") or _now_iso(),
        "prompt": prompt,
        "final": final,
        "model": msg_meta.get("model", ""),
        "usage": msg_meta.get("usage", {}),
        "stop_reason": msg_meta.get("stop_reason", ""),
        "messages": turn_messages,
    }


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text", "")
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _already_written(target: Path, turn_uuid: str) -> bool:
    if not target.exists() or not turn_uuid:
        return False
    try:
        with target.open("r", encoding="utf-8") as fh:
            last_line = ""
            for line in fh:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            return False
        last = json.loads(last_line)
        return last.get("turn_uuid") == turn_uuid
    except (OSError, json.JSONDecodeError):
        return False


def _build_entry(turn: dict[str, Any], is_debug: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": turn["ts"],
        "session_id": turn["session_id"],
        "turn_uuid": turn["turn_uuid"],
        "prompt": turn["prompt"],
        "final": turn["final"],
        "model": turn["model"],
        "usage": turn["usage"],
        "stop_reason": turn["stop_reason"],
    }
    if is_debug:
        entry["tools_used"] = _extract_tools_used(turn["messages"])
        entry["raw_messages"] = turn["messages"]
    return entry


def _extract_tools_used(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for m in messages:
        if m.get("type") != "assistant":
            continue
        content = m.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tools.append({"name": b.get("name"), "input": b.get("input")})
    return tools


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()
