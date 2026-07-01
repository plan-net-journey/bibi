"""Ausgabefilter für /-/job/{id}/output und /-/journal/{jid}/output (Worker-
Domäne). Reine Funktionen — output.jsonl bleibt unverändert, die Formatierung
passiert nur in der HTTP-Antwort."""
from __future__ import annotations

import json
from collections.abc import Callable

_TOOL_SUMMARY_LEN = 120


def _truncate(s: str, n: int = _TOOL_SUMMARY_LEN) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tool_summary(name: str, tool_input: dict) -> str:
    if name == "Bash":
        return f"→ Bash: {_truncate(tool_input.get('command', ''))}"
    if name in ("Edit", "Write", "Read", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path", "")
        return f"→ {name}: {path}"
    if name == "Task":
        sub = tool_input.get("subagent_type")
        desc = tool_input.get("description") or tool_input.get("prompt", "")
        label = f"Task ({sub})" if sub else "Task"
        return f"→ {label}: {_truncate(desc)}"
    for v in tool_input.values():
        if isinstance(v, str) and v:
            return f"→ {name}: {_truncate(v)}"
    return f"→ {name}"


def _events_from_message(msg: dict, t: float | None) -> list[dict]:
    out: list[dict] = []
    for block in msg.get("content", []) or []:
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if not text.strip():
                continue
            out.extend({"t": t, "s": "out", "line": ln} for ln in text.split("\n"))
        elif btype == "tool_use":
            summary = _tool_summary(block.get("name", "tool"), block.get("input") or {})
            # Leerzeilen davor/danach ⇒ render.py::_markdown() rendert die
            # Summary als eigenen <p>, statt sie in Fließtext zu kleben.
            out += [{"t": t, "s": "out", "line": ""},
                    {"t": t, "s": "out", "line": summary},
                    {"t": t, "s": "out", "line": ""}]
    return out


def _events_from_stream_json(obj: dict, t: float | None) -> list[dict]:
    if obj.get("type") == "assistant":
        return _events_from_message(obj.get("message") or {}, t)
    # system/user/result/rate_limit_event/unbekannte künftige Typen: bewusst
    # kein Display-Text (Roh-JSON bleibt über /log, /out einsehbar) — nie crashen.
    return []


def _format_claude(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in events:
        if e.get("s") != "out":
            out.append(e)  # stderr unverändert (echte claude-Fehlermeldungen)
            continue
        try:
            obj = json.loads(e.get("line", ""))
        except (json.JSONDecodeError, ValueError):
            obj = None
        if not isinstance(obj, dict):
            out.append(e)  # kein JSON (z.B. Alt-Lauf ohne stream-json) → roh durchreichen
            continue
        out.extend(_events_from_stream_json(obj, e.get("t")))
    return out


_FORMATTERS: dict[str, Callable[[list[dict]], list[dict]]] = {
    "claude": _format_claude,
}


def format_events(events: list[dict], kind: str) -> list[dict]:
    """Dispatcht nach effektivem Typ. Unbekannte kinds: Identity (pass-through)."""
    formatter = _FORMATTERS.get(kind)
    return formatter(events) if formatter else events
