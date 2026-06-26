"""HTML-Rendering der Controller-App (PLAN-4 §4.1 ff.) — **pure** Funktionen:
Daten-dict (aus den ``/-/``-JSON-Endpunkten) → HTML. Kein HTTP, kein DB-Zugriff,
damit voll unit-testbar. Look: Terminal/Konsole-nah, minimal (§2.5)."""

from __future__ import annotations

import html
import time

_HTMX = "https://unpkg.com/htmx.org@1.9.12"

_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem;
       max-width: 64rem; margin-inline: auto; }
header { display: flex; align-items: baseline; gap: .75rem; }
h1 { font-size: 1.4rem; margin: 0; }
.muted { color: #888; font-size: .85rem; }
.banner { margin: 1.25rem 0 .5rem; padding: 1rem 1.25rem; border-radius: .6rem;
          border: 1px solid #8884; font-size: 1.25rem; font-weight: 600; }
.banner.ok  { background: #1a7f3722; }
.banner.bad { background: #c0392b22; }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th { text-align: left; color: #888; font-weight: 500; padding: .35rem .5rem;
     border-bottom: 1px solid #8883; }
td { padding: .4rem .5rem; border-bottom: 1px solid #8882; }
.st { font-family: ui-monospace, monospace; }
.st.failed, .st.error, .st.killed, .st.zombie { color: #e06c5a; }
.st.overdue { color: #d6a23e; }
a.slug { font-weight: 600; text-decoration: none; }
a.slug:hover { text-decoration: underline; }
h2 { font-size: .95rem; color: #888; margin: 1.5rem 0 .4rem; font-weight: 600; }
"""


def _plural(n: int, sing: str, plur: str) -> str:
    return sing if n == 1 else plur


def _ago(ts: float | None, now: float) -> str:
    if ts is None:
        return "—"
    d = max(0, int(now - ts))
    if d < 60:
        return f"vor {d}s"
    if d < 3600:
        return f"vor {d // 60} min"
    if d < 86400:
        return f"vor {d // 3600} h"
    return f"vor {d // 86400} d"


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _banner(v: dict) -> str:
    if v.get("ok"):
        return '<div class="banner ok">✓ alles lief</div>'
    problems, overdue = v.get("problems", 0), v.get("overdue", 0)
    parts = []
    if problems:
        parts.append(f"{problems} {_plural(problems, 'Problem', 'Probleme')}")
    if overdue:
        parts.append(f"{overdue} überfällig")
    return f'<div class="banner bad">⚠ {" · ".join(parts) or "Problem"}</div>'


def _slug_link(slug: str) -> str:
    s = _e(slug)
    return f'<a class="slug" href="/-/ui/schedule/{s}">{s}</a>'


def _deviation_rows(deviations: list[dict], now: float) -> str:
    if not deviations:
        return ""
    rows = []
    for d in deviations:
        when = d.get("finished_at") or d.get("started_at")
        rows.append(
            "<tr>"
            f"<td>{_slug_link(d.get('slug'))}</td>"
            f'<td class="st {_e(d.get("status"))}">{_e(d.get("status"))}</td>'
            f"<td>{_e(d.get('reason'))}</td>"
            f"<td>{_ago(when, now)}</td>"
            f"<td>{_e(d.get('host'))}</td>"
            "</tr>"
        )
    return ('<h2>Abweichungen</h2><table><thead><tr><th>Schedule</th>'
            '<th>Status</th><th>Grund</th><th>seit</th><th>Knoten</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _overdue_rows(overdue_jobs: list[dict], now: float) -> str:
    if not overdue_jobs:
        return ""
    rows = []
    for o in overdue_jobs:
        rows.append(
            "<tr>"
            f"<td>{_slug_link(o.get('slug'))}</td>"
            '<td class="st overdue">überfällig</td>'
            f"<td>seit {_ago(o.get('next_fire_at'), now)}</td>"
            f"<td>{_e(o.get('host'))}</td>"
            "</tr>"
        )
    return ('<h2>Überfällig</h2><table><thead><tr><th>Schedule</th>'
            '<th>Status</th><th>fällig</th><th>Knoten</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def verdict_fragment(status: dict, now: float | None = None) -> str:
    """Der selbst-pollende Verdikt-Block: Banner + Abweichungs-/Überfällig-Listen.
    ``status`` ist die Antwort von ``GET /-/status``."""
    now = time.time() if now is None else now
    attrs = ('id="verdict" hx-get="/-/ui/verdict" hx-trigger="every 5s" '
             'hx-swap="outerHTML"')
    v = status.get("verdict")
    if v is None:
        return (f'<div {attrs}><div class="banner">Kein Verdikt — '
                "Scheduler-Rolle nötig.</div></div>")
    body = _banner(v) + _deviation_rows(v.get("deviations", []), now) \
        + _overdue_rows(v.get("overdue_jobs", []), now)
    return f"<div {attrs}>{body}</div>"


def dashboard_page(status: dict, now: float | None = None) -> str:
    """Die App-Wurzel ``/-/`` (Browser): Server-Render inkl. initialem Verdikt."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        '<header><h1>bibi</h1><span class="muted">Health- &amp; Anomalie-Sicht'
        "</span></header>"
        f"{verdict_fragment(status, now)}"
        '<p class="muted">Klick auf einen Schedule → Detail &amp; Output (4.2).</p>'
        "</body></html>"
    )
