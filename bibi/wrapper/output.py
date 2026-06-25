"""``output.jsonl`` — die eine append-only Output-Datei je Job (DESIGN §4.5).

Eine Zeile je Ereignis: ``{"t": <epoch>, "s": "out"|"err", "line": "…"}``. Quelle
für alle drei Stream-Sichten (``/out``, ``/err``, ``/stream``) — jede Zeile trägt
``t``, also sind Reihenfolge und Replay garantiert identisch. Gilt für alle Typen
(``job``/``claude``/``app``) gleich; der Wrapper lebt und pipet stdout/stderr.

Bewusst output-frei gegenüber dem Scheduler (§4.4): die Datei bleibt beim Worker
in ``data/job/{id}/`` (gitignored), der Scheduler referenziert sie nur per
``output_ref``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def append(path: Path, stream: str, line: str, *, t: float | None = None) -> None:
    """Eine Ereigniszeile anhängen. ``stream`` ∈ {``out``, ``err``}.

    Einzeilen-Invariante: eingebettete CR/LF werden zu Leerzeichen kollabiert,
    damit eine Output-Zeile genau eine physische JSONL-Zeile bleibt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = line.replace("\r", " ").replace("\n", " ")
    rec = {"t": time.time() if t is None else t, "s": stream, "line": safe}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_events(path: Path) -> list[dict]:
    """Alle Ereignisse als Liste von Dicts (leere Liste, wenn keine Datei)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def lines(path: Path, stream: str | None = None) -> list[str]:
    """Reine Textzeilen, optional auf ``out``/``err`` gefiltert (chronologisch)."""
    return [e["line"] for e in read_events(path) if stream is None or e.get("s") == stream]
