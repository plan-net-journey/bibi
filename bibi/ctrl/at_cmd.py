"""``bibi-ctrl at`` — One-shot-Schedule anlegen (DESIGN §5.2/§6.3; PLAN-3 §3.3).

Schreibt eine flache ``at:``-MD ins Case-Verzeichnis, sodass der Scheduler sie als
einmaligen Lauf erfasst, und triggert (best-effort) einen Rescan. In-Process bis
auf den Rescan — der trifft den laufenden Daemon per HTTP.

  bibi-ctrl at "<when>" "<prompt>"           # claude-Job (AI-Prompt, Default)
  bibi-ctrl at "<when>" "<cmd>" --job        # Shell-Befehl (job-Typ)

Alle Jobs verwenden ``job:`` als einzigen Frontmatter-Key (PLAN-10 §1, Unified Job
Model). Ohne ``--job`` wird der Prompt als ``claude: <prompt>`` kodiert — der Worker
erkennt das Prefix und setzt ``BIBI_JOB_TYPE=claude``.

``<when>``: ISO 8601, relativ (``+30s``/``+5min``/``+2h``/``+1d``) oder
best-effort natürlichsprachlich (via dateutil).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import secrets
import sys
import urllib.error
import urllib.request

from dateutil import parser as _date_parser

from bibi import config, repo

_REL = re.compile(r"^\+(\d+)\s*(s|sec|min|m|h|hour|d|day)s?$", re.IGNORECASE)
_UNIT = {"s": "seconds", "sec": "seconds", "min": "minutes", "m": "minutes",
         "h": "hours", "hour": "hours", "d": "days", "day": "days"}


def _to_naive_local(dt: _dt.datetime) -> _dt.datetime:
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def resolve_when(when: str, *, now: _dt.datetime | None = None) -> _dt.datetime:
    """``<when>`` → naiver lokaler ``datetime``. ``ValueError`` bei Parse-Fehler."""
    now = now or _dt.datetime.now()
    s = when.strip()
    m = _REL.match(s)
    if m:
        return now + _dt.timedelta(**{_UNIT[m.group(2).lower()]: int(m.group(1))})
    return _to_naive_local(_date_parser.parse(s))  # ISO / natürlichsprachlich


def _rescan(port: int) -> bool:
    """Best-effort POST ``/-/rescan`` an den lokalen Daemon. True bei Erfolg."""
    url = f"http://127.0.0.1:{port}/-/rescan"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=3):  # noqa: S310 (localhost)
            return True
    except (urllib.error.URLError, OSError):
        return False


def run(args: argparse.Namespace) -> int:
    try:
        dt = resolve_when(args.when)
    except (ValueError, OverflowError) as exc:
        print(f"bibi-ctrl at: '{args.when}' nicht als Zeitpunkt lesbar: {exc}", file=sys.stderr)
        return 2
    iso = dt.replace(microsecond=0).isoformat()

    slug = f"{dt:%Y%m%d}.at-{dt:%H%M%S}-{secrets.token_hex(2)}"
    case = repo.case_dir()
    case.mkdir(parents=True, exist_ok=True)
    path = case / f"{slug}.md"

    # json.dumps liefert einen gültigen (YAML-kompatiblen) Doppelquote-Skalar.
    if args.job:
        job_value = args.payload
        typ_display = "job"
    else:
        job_value = f"claude: {args.payload}"
        typ_display = "claude"
    md = (f"---\nat: {iso}\njob: {json.dumps(job_value)}\n---\n"
          f"One-shot via `/at`, geplant für {iso}.\n")
    path.write_text(md, encoding="utf-8")

    port = args.port or config.daemon_port()
    rescanned = _rescan(port)

    rel = path.relative_to(repo.root()).as_posix()
    eta = dt - _dt.datetime.now()
    eta_s = max(0, int(eta.total_seconds()))
    print(f"one-shot erstellt: {slug}")
    print(f"  at:   {iso}  (in {eta_s // 60}m {eta_s % 60}s)")
    print(f"  typ:  {typ_display}")
    print(f"  pfad: {rel}")
    print(f"  rescan: {'ok' if rescanned else f'Daemon nicht erreichbar (Port {port}) — beim nächsten Rescan/Start erfasst'}")
    print(f"beobachten: bibi-ctrl job list", file=sys.stderr)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("at", help="One-shot-Schedule anlegen (§6.3, Scheduler)")
    p.add_argument("when", help="ISO 8601 | +30s/+5min/+2h/+1d | natürlichsprachlich")
    p.add_argument("payload", help="Prompt (claude) bzw. Shell-Befehl (--job)")
    p.add_argument("--job", action="store_true", help="job-Typ (Shell) statt claude")
    p.add_argument("--port", type=int, default=0, help="0 = aus BIBI_DAEMON_PORT/Default")
    p.set_defaults(func=run)
