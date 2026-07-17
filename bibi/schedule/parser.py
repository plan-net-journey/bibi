"""Eine MD-Datei → ``ScheduleSpec`` (oder Grund, warum nicht). DESIGN §5.2/§5.3.

Drei Ausgänge (wie bibi3' Parser, auf das bibi4-Modell übertragen):

- **skip**  — kein ``schedule:``/``at:`` im Frontmatter → keine Schedule-MD.
- **error** — Trigger/Typ vorhanden, aber ungültig (kaputter Cron, unparsbares
  Datum, fehlendes/doppeltes Payload) → gemeldet, zur Laufzeit ignoriert.
- **ok**    — ``ScheduleSpec`` steht.

Trigger-Syntax (§5.2): ``schedule:`` ist ein croniter-Ausdruck **oder** ein
Spezialwert (``now``/``startup``/``never``/``autostart``); ``at:`` ein ISO-8601-Zeitpunkt.
Genau einer von beiden. Typ-Schlüssel: nur ``job:`` (PLAN-10 Stufe 10.0).
``job: claude: <prompt>`` → claude-Prefix-Expansion beim Spawn.

Slug-Ableitung (§6.6/bibi3 §2.5): explizites ``slug:`` gewinnt; sonst bei
``README.md``/``SCHEDULE.md`` der Ordnername; sonst der Dateistamm.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import croniter
from dateutil import parser as _date_parser

from bibi import frontmatter
from bibi.schedule.models import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_SILENCE_TIMEOUT,
    DEFAULT_SILENCE_TIMEOUT_APP,
    DEFAULT_SILENCE_TIMEOUT_JOB,
    Kind,
    ScheduleSpec,
    is_claude_payload,
)

_CLAUDE_PREFIX_RE = re.compile(r"^\s*claude\s*:\s*(.+)", re.DOTALL)

#: Spezialwerte von ``schedule:`` (§5.2) — keine cron-Ausdrücke.
SPECIAL_SCHEDULES: frozenset[str] = frozenset({"now", "startup", "never", "on_demand", "autostart"})

#: Dateinamen, bei denen der Ordnername den Slug bestimmt (§6.6).
SCHEDULE_FILENAMES: frozenset[str] = frozenset({"README.md", "SCHEDULE.md"})

#: Frontmatter-Key → Typ (§5.3, PLAN-10 Stufe 10.0). Nur noch ``job:``.
_TYPE_KEYS: dict[str, Kind] = {"job": Kind.JOB}

_VALID_BACKOFF: frozenset[str] = frozenset({"fixed", "linear", "exponential"})


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Ergebnis des Parsens einer MD. ``slug_explicit``/``mtime`` tragen die
    Dateisystem-Metadaten, die der Discovery/Reconcile braucht (nicht der Spec)."""

    schedule_ref: str
    spec: ScheduleSpec | None = None
    slug_explicit: bool = False
    mtime: float = 0.0
    error: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.spec is not None

    @property
    def is_skip(self) -> bool:
        return self.spec is None and self.error is None

    @property
    def is_error(self) -> bool:
        return self.error is not None


def derive_slug(path: Path, fm: dict[str, Any]) -> tuple[str, bool]:
    """``(slug, is_explicit)`` (§6.6)."""
    explicit = fm.get("slug")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip(), True
    if path.name in SCHEDULE_FILENAMES:
        return path.parent.name, False
    return path.stem, False


def _validate_cron(expr: str) -> str | None:
    if not isinstance(expr, str) or not expr.strip():
        return "schedule: cron-Ausdruck muss ein nicht-leerer String sein"
    try:
        croniter.croniter(expr)
    except (croniter.CroniterBadCronError, ValueError) as exc:
        return f"schedule: ungültiger cron-Ausdruck: {exc}"
    return None


def _to_naive_local(value: _dt.datetime) -> _dt.datetime:
    """Auf naive Lokalzeit normalisieren — der Daemon vergleicht durchweg gegen
    ein naives ``datetime.now()`` (eine tz-aware Differenz wirft TypeError)."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _validate_at(value: Any) -> tuple[str | None, str | None]:
    """``(iso_string, error)`` — genau eines ist None. ISO immer tz-naiv (lokal)."""
    if isinstance(value, _dt.datetime):
        return _to_naive_local(value).isoformat(), None
    if isinstance(value, _dt.date):
        return _dt.datetime.combine(value, _dt.time()).isoformat(), None
    if not isinstance(value, str) or not value.strip():
        return None, "at: muss ein String oder Zeitstempel sein"
    try:
        parsed = _date_parser.isoparse(value)
    except ValueError as exc:
        return None, f"at: ungültiger Zeitstempel: {exc}"
    return _to_naive_local(parsed).isoformat(), None


def _coerce_int(fm: dict[str, Any], key: str, default: int) -> tuple[int, str | None]:
    val = fm.get(key, default)
    if isinstance(val, bool):  # bool ist int-Subtyp — explizit ablehnen
        return default, f"{key}: muss eine Ganzzahl sein, nicht bool"
    if isinstance(val, int):
        return val, None
    return default, f"{key}: muss eine Ganzzahl sein, nicht {type(val).__name__}"


def parse_text(
    text: str, *, schedule_ref: str, path: Path, mtime: float = 0.0
) -> ParseResult:
    """MD-Text parsen. ``path`` für Slug/Dateiname, ``schedule_ref`` als Referenz."""
    fm, _body = frontmatter.split(text)

    has_schedule = "schedule" in fm
    has_at = "at" in fm
    if not has_schedule and not has_at:
        return ParseResult(schedule_ref=schedule_ref)  # skip
    if has_schedule and has_at:
        return ParseResult(
            schedule_ref=schedule_ref,
            error="Frontmatter hat `schedule:` UND `at:` — genau einen wählen (§5.2)",
        )

    # ── Trigger validieren (§5.2) ────────────────────────────────────────────
    schedule_val: str | None = None
    at_val: str | None = None
    if has_schedule:
        raw = fm["schedule"]
        if isinstance(raw, str) and raw.strip() in SPECIAL_SCHEDULES:
            schedule_val = raw.strip()
        else:
            err = _validate_cron(raw)
            if err:
                return ParseResult(schedule_ref=schedule_ref, error=err)
            schedule_val = raw
    else:
        at_val, err = _validate_at(fm["at"])
        if err:
            return ParseResult(schedule_ref=schedule_ref, error=err)

    # ── Typ + Payload (§5.3) — genau einer von job/claude/app ─────────────────
    present = [(k, kind) for k, kind in _TYPE_KEYS.items()
               if isinstance(fm.get(k), str) and fm[k].strip() != ""]
    if len(present) == 0:
        return ParseResult(
            schedule_ref=schedule_ref,
            error="Frontmatter braucht `job:` (§5.3); claude-Prefix: `job: claude: <prompt>`",
        )
    if len(present) > 1:
        keys = ", ".join(f"`{k}:`" for k, _ in present)
        return ParseResult(
            schedule_ref=schedule_ref,
            error=f"Frontmatter hat mehrere Typen ({keys}) — genau einen wählen (§5.3)",
        )
    type_key, kind = present[0]
    payload = fm[type_key].strip()

    # ── Ganzzahl-Felder (§5.5) ───────────────────────────────────────────────
    errors: list[str] = []
    priority, e = _coerce_int(fm, "priority", 0); errors += [e] if e else []
    attempts, e = _coerce_int(fm, "attempts", 1); errors += [e] if e else []
    # User-Feedback 2026-07-04: silence_timeout/hitl_timeout zusammengelegt —
    # claude:-Payloads (Batch, run_job, kein HITL) bekommen den kurzen Default,
    # echte Apps (long-lived, HITL-fähig über run_app) den langen. PLAN-31
    # Befund 4 (2026-07-17): "echte App" heißt jetzt tatsächlich `app_port`/
    # `app_prefix` gesetzt — vorher bekam JEDER Nicht-claude:-Job denselben
    # 48h-Default wie eine App, ein hängender einfacher Job blieb dadurch bis
    # zu 48h unbemerkt statt zeitnah als Zombie aufzufallen. Presence-Check
    # auf `fm` direkt, nicht auf die weiter unten erst noch gecoerceten
    # `app_port`/`app_prefix`-Variablen — die stehen an dieser Stelle im Code
    # noch nicht zur Verfügung.
    _is_app = "app_port" in fm or "app_prefix" in fm
    if is_claude_payload(payload):
        _default_silence = DEFAULT_SILENCE_TIMEOUT
    elif _is_app:
        _default_silence = DEFAULT_SILENCE_TIMEOUT_APP
    else:
        _default_silence = DEFAULT_SILENCE_TIMEOUT_JOB
    silence_timeout, e = _coerce_int(fm, "silence_timeout", _default_silence)
    errors += [e] if e else []
    wall_time = defer_time = defer_max = app_port = None
    if "wall_time" in fm:
        wall_time, e = _coerce_int(fm, "wall_time", 0); errors += [e] if e else []
    if "defer_time" in fm:
        defer_time, e = _coerce_int(fm, "defer_time", 0); errors += [e] if e else []
    if "defer_max" in fm:
        defer_max, e = _coerce_int(fm, "defer_max", 0); errors += [e] if e else []
    if "app_port" in fm:
        app_port, e = _coerce_int(fm, "app_port", 0); errors += [e] if e else []
    if errors:
        return ParseResult(schedule_ref=schedule_ref, error="; ".join(errors))

    backoff = fm.get("backoff", "fixed")
    if not isinstance(backoff, str) or backoff.lower() not in _VALID_BACKOFF:
        return ParseResult(
            schedule_ref=schedule_ref,
            error=f"backoff: muss eines von {sorted(_VALID_BACKOFF)} sein",
        )

    # ── claude-Prefix-Felder (gelten wenn payload mit `claude:` beginnt) ────
    model = DEFAULT_CLAUDE_MODEL
    if isinstance(fm.get("model"), str) and fm["model"].strip():
        model = fm["model"].strip()
    soul = fm["soul"].strip() if isinstance(fm.get("soul"), str) and fm["soul"].strip() else None
    session = fm["session"].strip() if isinstance(fm.get("session"), str) and fm["session"].strip() else None
    app_prefix = fm["app_prefix"].strip() if isinstance(fm.get("app_prefix"), str) and fm["app_prefix"].strip() else None
    exec_mode = fm["exec_mode"].strip().lower() if isinstance(fm.get("exec_mode"), str) and fm["exec_mode"].strip() else None
    image = fm["image"].strip() if isinstance(fm.get("image"), str) and fm["image"].strip() else None

    slug, slug_explicit = derive_slug(path, fm)

    spec = ScheduleSpec(
        slug=slug, kind=kind, payload=payload,
        schedule=schedule_val, at=at_val, priority=priority,
        model=model, soul=soul, session=session,
        attempts=attempts, backoff=backoff.lower(),
        silence_timeout=silence_timeout, wall_time=wall_time,
        defer_time=defer_time, defer_max=defer_max,
        app_port=app_port, app_prefix=app_prefix, exec_mode=exec_mode, image=image,
    )
    return ParseResult(
        schedule_ref=schedule_ref, spec=spec, slug_explicit=slug_explicit, mtime=mtime
    )


def parse_file(path: Path, *, vault_root: Path) -> ParseResult:
    """MD von Platte lesen + parsen. ``schedule_ref`` = Pfad relativ zum Vault."""
    try:
        rel = path.relative_to(vault_root)
    except ValueError:
        rel = path  # außerhalb des Vault — defensiv absolut behalten
    schedule_ref = rel.as_posix()
    try:
        text = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime
    except (OSError, UnicodeDecodeError) as exc:
        return ParseResult(schedule_ref=schedule_ref, error=f"Datei nicht lesbar: {exc}")
    return parse_text(text, schedule_ref=schedule_ref, path=path, mtime=mtime)
