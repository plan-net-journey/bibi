"""Daemon-Aktivitätslog (PLAN-5 §5.1) — „Was *macht* der Daemon gerade?".

Die dritte Frage neben „was ist los" (Status/Verdikt) und „was ist passiert"
(Journal). Strukturierte Events der Rollen (scheduler/worker/synchronizer) mit
**zwei Sinks**: maschinenlesbares JSONL (rotierend, unter gitignored ``data/``)
und eine menschenlesbare Zeile auf stdout — letztere *ist* der Live-Tail beim
Vordergrund-Start.

Bewusst auf stdlib-``logging`` aufgesetzt (die Codebasis nutzt schon benannte
Logger ``bibi.*``). Ein Event wird via ``emit(...)`` als ``extra={"bibi": …}`` an
den Record gehängt; die beiden Formatter rendern daraus JSONL bzw. Klartext. Die
reinen Funktionen (``human_line``/``render_jsonl_line``) sind ohne laufenden
Daemon testbar (Akzeptanz §5.1).

Abgrenzung: Das ist das *rollenübergreifende Daemon-Log*, **nicht** die per-Job-
Output-Streams (§4.5) — die bleiben getrennt (ein Job = ein Stream).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILENAME = "daemon.jsonl"
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_BACKUPS = 5

# Log-Level-Namen (case-insensitiv) → stdlib-Level. Bei Vollabdeckung (§5.4) ist
# der Knopf nötig, damit der Live-Tail nicht im DEBUG-Firehose ersäuft.
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
          "warning": logging.WARNING, "warn": logging.WARNING,
          "error": logging.ERROR}


def resolve_level(cli: str | None = None, env: str | None = None,
                  default: int = logging.INFO) -> int:
    """Log-Level wählen: CLI (``--log-level``) > env (``BIBI_LOG_LEVEL``) > Default.

    Unbekannte/leere Werte werden übersprungen (defensiv → Default).
    """
    for raw in (cli, env):
        if raw:
            lvl = LEVELS.get(raw.strip().lower())
            if lvl is not None:
                return lvl
    return default

# Felder, die im JSONL-Objekt erste Klasse sind (nicht in „fields" gemischt).
_KNOWN = ("ts", "level", "role", "event", "msg", "slug", "run_id")


# ── reine Helfer (testbar ohne Logging-Infrastruktur) ───────────────────────

def role_from_logger(name: str) -> str:
    """``bibi.daemon.synchronizer`` → ``synchronizer``; ``bibi.worker`` → ``worker``."""
    tail = name.rsplit(".", 1)[-1]
    return tail or name


def human_line(*, ts: str, level: str, role: str, event: str, msg: str = "",
               slug: str | None = None, run_id: str | None = None,
               fields: dict | None = None) -> str:
    """Eine kompakte, menschenlesbare Aktivitätszeile bauen (rein)."""
    parts = [ts, level[:4].ljust(4), role or "-"]
    if event:
        parts.append(event)
    head = " ".join(parts)
    if msg:
        head += "  " + msg
    ctx: list[str] = []
    if slug:
        ctx.append(f"slug={slug}")
    if run_id:
        ctx.append(f"run={run_id}")
    for k, v in (fields or {}).items():
        ctx.append(f"{k}={v}")
    if ctx:
        head += "  " + " ".join(ctx)
    return head


def _payload(record: logging.LogRecord) -> dict:
    return getattr(record, "bibi", None) or {}


def _role(record: logging.LogRecord, payload: dict) -> str:
    return payload.get("role") or role_from_logger(record.name)


# ── Formatter ───────────────────────────────────────────────────────────────

class JsonlFormatter(logging.Formatter):
    """Ein Event → eine JSON-Zeile (maschinenlesbarer Sink)."""

    def format(self, record: logging.LogRecord) -> str:
        b = _payload(record)
        obj: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "role": _role(record, b),
            "event": b.get("event") or "",
            "msg": record.getMessage(),
        }
        if b.get("slug"):
            obj["slug"] = b["slug"]
        if b.get("run_id"):
            obj["run_id"] = b["run_id"]
        for k, v in (b.get("fields") or {}).items():
            if k not in _KNOWN:
                obj[k] = v
        return json.dumps(obj, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Ein Event → eine Klartextzeile (stdout-Sink / Live-Tail)."""

    def format(self, record: logging.LogRecord) -> str:
        b = _payload(record)
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return human_line(
            ts=ts, level=record.levelname, role=_role(record, b),
            event=b.get("event") or "", msg=record.getMessage(),
            slug=b.get("slug"), run_id=b.get("run_id"), fields=b.get("fields"),
        )


def render_jsonl_line(line: str) -> str:
    """Eine gespeicherte JSONL-Zeile als Klartext rendern (für ``daemon logs``)."""
    line = line.rstrip("\n")
    try:
        o = json.loads(line)
    except (ValueError, TypeError):
        return line
    ts = o.get("ts", "")
    try:
        short = datetime.fromisoformat(ts).astimezone().strftime("%H:%M:%S")
    except (ValueError, TypeError):
        short = ts[11:19] if len(ts) >= 19 else ts
    fields = {k: v for k, v in o.items() if k not in _KNOWN}
    return human_line(
        ts=short, level=o.get("level", ""), role=o.get("role", ""),
        event=o.get("event", ""), msg=o.get("msg", ""),
        slug=o.get("slug"), run_id=o.get("run_id"), fields=fields,
    )


def tail_lines(path: Path | str, n: int) -> list[str]:
    """Die letzten ``n`` Zeilen einer Datei (Backfill/CLI). Fehlt sie: ``[]``.

    ``n <= 0`` → alle Zeilen.
    """
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    return lines[-n:] if n and n > 0 else lines


# ── Live-Broadcast an SSE-Abonnenten (§5.4 Slice B) ──────────────────────────

class LogBroadcaster:
    """Fan-out der Aktivitäts-Events an verbundene SSE-Abonnenten.

    **Thread-sicher beim Publizieren** (der Handler feuert aus Worker-/Sync-/
    Sweeper-Threads), **asyncio-freundlich beim Lesen** (SSE-Endpunkt im uvicorn-
    Loop). Jeder Abonnent hält eine ``asyncio.Queue``; publizierte Zeilen werden
    via ``loop.call_soon_threadsafe`` eingespeist. Volle Queue (langsamer Consumer)
    → Zeile wird **verworfen**, blockiert nie den Logging-Pfad.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._subs: set[tuple] = set()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.add((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, line: str) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(self._offer, q, line)
            except RuntimeError:
                pass  # Loop geschlossen → wird beim nächsten unsubscribe entfernt

    @staticmethod
    def _offer(q: asyncio.Queue, line: str) -> None:
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass  # langsamer Consumer: verwerfen statt blockieren


_broadcaster = LogBroadcaster()


def get_broadcaster() -> LogBroadcaster:
    """Der prozessweite Broadcaster (vom SSE-Endpunkt abonniert)."""
    return _broadcaster


class _BroadcastHandler(logging.Handler):
    """Logging-Handler, der jede Zeile (JSONL) an den Broadcaster weiterreicht."""

    def __init__(self, broadcaster: LogBroadcaster) -> None:
        super().__init__()
        self._b = broadcaster
        self.setFormatter(JsonlFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._b.publish(self.format(record))
        except Exception:  # noqa: BLE001 — Logging darf nie den Aufrufer killen
            self.handleError(record)


# ── emit + Setup ─────────────────────────────────────────────────────────────

def emit(logger: logging.Logger, level: int, event: str, msg: str = "", *,
         role: str | None = None, slug: str | None = None,
         run_id: str | None = None, **fields) -> None:
    """Ein strukturiertes Aktivitäts-Event loggen (Sinks via ``setup_logging``)."""
    logger.log(level, msg, extra={"bibi": {
        "event": event, "role": role, "slug": slug, "run_id": run_id,
        "fields": fields,
    }})


def setup_logging(*, role_names: list[str] | None = None, log_dir: Path | str,
                  to_stdout: bool = True, level: int = logging.INFO,
                  max_bytes: int = DEFAULT_MAX_BYTES,
                  backups: int = DEFAULT_BACKUPS) -> Path:
    """Den ``bibi``-Logger mit zwei Sinks verdrahten (idempotent).

    JSONL (rotierend) nach ``<log_dir>/daemon.jsonl`` + optional Klartext auf
    stdout. Gibt den JSONL-Pfad zurück. ``role_names`` ist nur informativ.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_FILENAME

    logger = logging.getLogger("bibi")
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)

    fh = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups,
                             encoding="utf-8")
    fh.setFormatter(JsonlFormatter())
    logger.addHandler(fh)

    # Live-Fan-out an SSE-Abonnenten (§5.4 Slice B) — kostet nichts ohne Abonnenten.
    logger.addHandler(_BroadcastHandler(_broadcaster))

    if to_stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(HumanFormatter())
        logger.addHandler(sh)

    return path
