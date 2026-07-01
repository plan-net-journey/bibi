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


def _events_from_message(msg: dict, t: float | None, *, skip_text: bool = False) -> list[dict]:
    out: list[dict] = []
    for block in msg.get("content", []) or []:
        btype = block.get("type")
        if btype == "text":
            # Follow-up PLAN-14: bei --include-partial-messages kam der Text
            # schon per text_delta live — die komplette Nachricht würde ihn
            # sonst doppelt zeigen.
            if skip_text:
                continue
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


#: Content-Block-Typen, für die wir Token-Deltas live anzeigen (Follow-up
#: PLAN-14, --include-partial-messages). tool_use-Argumente streamen zwar auch
#: (input_json_delta), werden aber bewusst nicht live gezeigt — die fertige
#: Tool-Summary aus der kompletten assistant-Nachricht reicht.
_DELTA_BLOCK_KINDS = {"text": "out", "thinking": "thinking"}


class _ClaudeDeltaState:
    """Zustand für Token-Level-Deltas über eine Event-Liste hinweg — pro
    ``_format_claude()``-Aufruf frisch (die volle Roh-Historie wird bei jedem
    Poll neu verarbeitet, kein Zustand muss über Aufrufe hinweg leben)."""

    def __init__(self) -> None:
        self.open_kind: str | None = None
        self.started_line = False
        self.text_seen_via_delta = False

    def handle(self, ev: dict, t: float | None) -> list[dict]:
        etype = ev.get("type")
        if etype == "message_start":
            self.open_kind = None
            self.started_line = False
            self.text_seen_via_delta = False
        elif etype == "content_block_start":
            btype = (ev.get("content_block") or {}).get("type")
            self.open_kind = btype if btype in _DELTA_BLOCK_KINDS else None
            self.started_line = False
        elif etype == "content_block_delta":
            return self._delta(ev.get("delta") or {}, t)
        elif etype == "content_block_stop":
            self.open_kind = None
            self.started_line = False
        # message_delta/message_stop/unbekannte stream_event-Typen: kein Display.
        return []

    def _delta(self, delta: dict, t: float | None) -> list[dict]:
        dtype = delta.get("type")
        if dtype == "text_delta" and self.open_kind == "text":
            chunk = delta.get("text", "")
            if not chunk:
                return []
            self.text_seen_via_delta = True
            e = {"t": t, "s": "out", "line": chunk, "delta": self.started_line}
            self.started_line = True
            return [e]
        if dtype == "thinking_delta" and self.open_kind == "thinking":
            chunk = delta.get("thinking", "")
            if not chunk:
                return []
            e = {"t": t, "s": "thinking", "line": chunk, "delta": self.started_line}
            self.started_line = True
            return [e]
        return []


def _format_claude(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    state = _ClaudeDeltaState()
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
        t = e.get("t")
        otype = obj.get("type")
        if otype == "assistant":
            out.extend(_events_from_message(obj.get("message") or {}, t,
                                            skip_text=state.text_seen_via_delta))
        elif otype == "stream_event":
            out.extend(state.handle(obj.get("event") or {}, t))
        # system/user/result/rate_limit_event/unbekannte künftige Typen: bewusst
        # kein Display-Text (Roh-JSON bleibt über /log, /out einsehbar) — nie crashen.
    return out


_FORMATTERS: dict[str, Callable[[list[dict]], list[dict]]] = {
    "claude": _format_claude,
}


def format_events(events: list[dict], kind: str) -> list[dict]:
    """Dispatcht nach effektivem Typ. Unbekannte kinds: Identity (pass-through)."""
    formatter = _FORMATTERS.get(kind)
    return formatter(events) if formatter else events
