"""HTML-Rendering der Controller-App (PLAN-4 §4.1 ff.) — **pure** Funktionen:
Daten-dict (aus den ``/-/``-JSON-Endpunkten) → HTML. Kein HTTP, kein DB-Zugriff,
damit voll unit-testbar. Look: Terminal/Konsole-nah, minimal (§2.5)."""

from __future__ import annotations

import html
import re
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
.back { color: #888; text-decoration: none; font-size: .85rem; }
.meta { color: #aaa; font-size: .9rem; margin: .2rem 0 1rem; }
.term { background: #0008; border: 1px solid #8883; border-radius: .4rem;
        padding: .6rem .8rem; overflow-x: auto; font-family: ui-monospace, monospace;
        font-size: .82rem; line-height: 1.45; white-space: pre-wrap; }
.term .err { color: #e06c5a; }
.md { font-size: .92rem; }
.md pre { background: #0008; border: 1px solid #8883; border-radius: .4rem;
          padding: .6rem .8rem; overflow-x: auto; }
.md code { font-family: ui-monospace, monospace; font-size: .85em; }
.out-empty { color: #888; font-size: .85rem; font-style: italic; }
button { font: inherit; background: #8882; border: 1px solid #8884;
         border-radius: .35rem; padding: .15rem .5rem; cursor: pointer; color: inherit; }
.commit { font-family: ui-monospace, monospace; font-size: .8rem; color: #888; }
.actions { margin: .6rem 0 1.2rem; display: flex; gap: .5rem; }
.actions button { padding: .3rem .8rem; font-weight: 600; }
.logbar { display: flex; gap: .6rem; align-items: center; margin: 1rem 0 .6rem;
          flex-wrap: wrap; }
.logbar select, .logbar input { font: inherit; padding: .2rem .45rem; color: inherit;
          background: #8881; border: 1px solid #8884; border-radius: .3rem; }
.logbar input { flex: 1; min-width: 8rem; }
.logbox { height: 72vh; overflow-y: auto; background: #0008; border: 1px solid #8883;
          border-radius: .4rem; padding: .6rem .8rem; font-family: ui-monospace, monospace;
          font-size: .82rem; line-height: 1.5; white-space: pre-wrap; }
.logbox .ln.warning { color: #d6a23e; }
.logbox .ln.error   { color: #e06c5a; }
.logbox .ln.debug   { color: #888; }
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


def _until(ts: float | None, now: float) -> str:
    """Zukunfts-Distanz („in …") für „nächster Lauf". Vergangenes/None → „—"
    (ein bereits gefeuerter/erledigter Trigger hat keinen nächsten Lauf)."""
    if ts is None or ts <= now:
        return "—"
    d = int(ts - now)
    if d < 60:
        return f"in {d}s"
    if d < 3600:
        return f"in {d // 60} min"
    if d < 86400:
        return f"in {d // 3600} h"
    return f"in {d // 86400} d"


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


# ── Volle Schedule-Liste + Archiv (Ebene 2, §4.4) ────────────────────────────

#: Terminale Sicht-Zustände — ein One-shot in einem davon gilt als „abgelaufen".
_TERMINAL_VIEW = {"complete", "error", "inactive", "zombie", "killed"}


def _is_archived(s: dict) -> bool:
    # Abgelaufener One-shot (at:, schon gelaufen) → Archiv; läuft noch bevor → aktiv.
    return bool(s.get("oneshot")) and s.get("last_status") in _TERMINAL_VIEW


def _sched_row(s: dict, now: float) -> str:
    slug = _e(s.get("slug"))
    st = _e(s.get("last_status"))
    nxt = _until(s.get("next_fire_at"), now)
    return (
        "<tr>"
        f'<td><a class="slug" href="/-/ui/schedule/{slug}">{slug}</a></td>'
        f'<td class="st {st}">{st}</td>'
        f"<td>{_ago(s.get('last_run_at'), now)}</td>"
        f"<td>{nxt}</td>"
        "</tr>"
    )


def _sched_table(items: list[dict], now: float) -> str:
    rows = "".join(_sched_row(s, now) for s in items)
    return ('<table><thead><tr><th>Schedule</th><th>Status</th>'
            f'<th>letzter</th><th>nächster</th></tr></thead><tbody>{rows}'
            "</tbody></table>")


def schedule_list(schedules: list[dict], now: float | None = None) -> str:
    """Die volle, **aufklappbare** Liste (nicht primär): aktive Schedules + ein
    eingeklapptes Archiv abgelaufener One-shots (MD bleibt — A15, §4.4)."""
    now = time.time() if now is None else now
    if not schedules:
        return ('<details class="all"><summary>Alle Schedules (0)</summary>'
                '<p class="out-empty">— keine Schedules —</p></details>')
    archived = [s for s in schedules if _is_archived(s)]
    active = [s for s in schedules if not _is_archived(s)]
    body = (_sched_table(active, now) if active
            else '<p class="out-empty">— keine aktiven Schedules —</p>')
    if archived:
        body += (f'<details class="archive"><summary>Archiv ({len(archived)})'
                 f"</summary>{_sched_table(archived, now)}</details>")
    return (f'<details class="all"><summary>Alle Schedules ({len(schedules)})'
            f"</summary>{body}</details>")


def dashboard_page(
    status: dict, schedules: list[dict] | None = None, now: float | None = None
) -> str:
    """Die App-Wurzel ``/-/`` (Browser): Server-Render — Verdikt + Abweichungen
    (Ebene 0/1) zuerst, darunter die aufklappbare volle Liste (Ebene 2)."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        '<header><h1>bibi</h1><span class="muted">Health- &amp; Anomalie-Sicht'
        ' · <a class="back" href="/-/ui/logs">Live-Log</a></span></header>'
        f"{verdict_fragment(status, now)}"
        f"{schedule_list(schedules or [], now)}"
        "</body></html>"
    )


# ── Live-Log-Panel (§5.4 Slice C) — EventSource gegen /-/log/stream ──────────

_LOG_JS = """
const box = document.getElementById('log');
const lvlSel = document.getElementById('lvl');
const q = document.getElementById('q');
const RANK = {DEBUG:0, INFO:1, WARNING:2, WARN:2, ERROR:3};
const buf = [];
let paused = false;

function passes(o){
  if ((RANK[o.level] ?? 1) < parseInt(lvlSel.value, 10)) return false;
  const t = q.value.trim().toLowerCase();
  if (t){
    const hay = ((o.role||'')+' '+(o.event||'')+' '+(o.slug||'')+' '+(o.msg||'')).toLowerCase();
    if (!hay.includes(t)) return false;
  }
  return true;
}
function line(o){
  const known = new Set(['ts','level','role','event','msg','slug','run_id']);
  let ctx = '';
  if (o.slug) ctx += ' slug='+o.slug;
  if (o.run_id) ctx += ' run='+o.run_id;
  for (const k in o){ if(!known.has(k)) ctx += ' '+k+'='+o[k]; }
  const t = (o.ts||'').slice(11,19);
  const el = document.createElement('div');
  el.className = 'ln ' + (o.level||'').toLowerCase();
  el.textContent = t+' '+(o.level||'').padEnd(5)+' '+(o.role||'')+' '+(o.event||'')
                   + (o.msg ? '  '+o.msg : '') + (ctx ? '  '+ctx : '');
  return el;
}
function autoscroll(){ if(!paused) box.scrollTop = box.scrollHeight; }
function rerender(){
  box.innerHTML = '';
  for (const o of buf){ if(passes(o)) box.appendChild(line(o)); }
  autoscroll();
}
const es = new EventSource('/-/log/stream?n=200');
es.onmessage = (e) => {
  let o; try { o = JSON.parse(e.data); } catch (_) { return; }
  buf.push(o);
  if (buf.length > 2000) buf.shift();
  if (passes(o)) { box.appendChild(line(o)); autoscroll(); }
};
box.addEventListener('scroll', () => {
  paused = box.scrollTop + box.clientHeight < box.scrollHeight - 24;
});
lvlSel.onchange = rerender;
q.oninput = rerender;
"""


def log_page() -> str:
    """Live-Log-Panel (§5.4 Slice C): EventSource gegen ``/-/log/stream``, mit
    Level- + Text-Filter (Rolle/Event/slug/msg). Reines FE; der Daemon liefert
    die Events als SSE. ``pure`` (kein HTTP/DB) — voll testbar."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Live-Log</title>"
        f"<style>{_CSS}</style></head><body>"
        '<header><h1>bibi</h1><span class="muted">Live-Log · '
        '<a class="back" href="/-/">zurück</a></span></header>'
        '<div class="logbar">'
        '<label>Level <select id="lvl">'
        '<option value="0">debug</option>'
        '<option value="1" selected>info</option>'
        '<option value="2">warning</option>'
        '<option value="3">error</option></select></label>'
        '<input id="q" type="text" placeholder="Filter: Rolle/Event/slug/msg…">'
        "</div>"
        '<div id="log" class="logbox"></div>'
        f"<script>{_LOG_JS}</script>"
        "</body></html>"
    )


# ── Output-Rendering (§2.5: event-typ-fähig, nicht „alles ist eine Textzeile") ──

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def _md_inline(s: str) -> str:
    # s ist bereits HTML-escaped. Inline-Spans: code zuerst (kein Markup darin).
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return s


def _markdown(text: str) -> str:
    """Minimaler, sicherer Markdown→HTML-Renderer (Überschriften, Fett, Inline-/
    Block-Code, Listen, Absätze). Bewusst klein; deckt typische ``claude``-Ausgabe
    ab und degradiert sonst zu Absätzen. Voll-Markdown ist eine spätere Ausbaustufe."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):  # Fenced Code: roh (escaped), kein Inline
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1  # schließendes Fence
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue
        m = re.match(r"(#{1,3})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_md_inline(html.escape(m.group(2)))}</h{lvl}>")
            i += 1
            continue
        if ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_md_inline(html.escape(lines[i][2:]))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if ln.strip() == "":
            i += 1
            continue
        # Absatz: zusammenhängende Nicht-Spezial-Zeilen
        para = []
        while i < len(lines) and lines[i].strip() != "" \
                and not lines[i].startswith(("```", "- ")) \
                and not re.match(r"#{1,3}\s+", lines[i]):
            para.append(_md_inline(html.escape(lines[i])))
            i += 1
        out.append("<p>" + "<br>".join(para) + "</p>")
    return "".join(out)


def output_block(events: list[dict], kind: str) -> str:
    """Output eines Laufs rendern. **Dispatch nach Event-Typ** (``s``): heute
    ``out``/``err``; Steuer-/Fortschritts-/HITL-Events (Phase 6) docken ohne Umbau
    an. Render je **Job-Typ**: ``claude`` → Markdown flüssig, ``job``/sonst →
    Terminal/preformatted (ANSI bereinigt). Top-Prio F4."""
    if not events:
        return '<div class="out-empty">— kein Output —</div>'
    if kind == "claude":
        # stdout als Markdown-Blob; stderr als Terminal-Block darunter.
        out_text = "\n".join(_strip_ansi(e["line"]) for e in events if e.get("s") == "out")
        err_evts = [e for e in events if e.get("s") == "err"]
        html_parts = []
        if out_text.strip():
            html_parts.append(f'<div class="md">{_markdown(out_text)}</div>')
        if err_evts:
            errs = "\n".join(_e(_strip_ansi(e["line"])) for e in err_evts)
            html_parts.append(f'<pre class="term"><span class="err">{errs}</span></pre>')
        return "".join(html_parts) or '<div class="out-empty">— kein Output —</div>'
    # job/app: preformatted, stderr-Zeilen rot.
    rows = []
    for e in events:
        line = _e(_strip_ansi(e.get("line", "")))
        rows.append(f'<span class="err">{line}</span>' if e.get("s") == "err" else line)
    return f'<pre class="term">{chr(10).join(rows)}</pre>'


# ── Schedule-Detail (Ebene 3, schedule-zentriert) ────────────────────────────


def _commit_cell(run: dict) -> str:
    sha = run.get("commit_sha")
    if not sha:
        return "—"
    short = _e(sha[:7])
    branch = _e(run.get("branch") or "")
    return f'<span class="commit" title="{_e(sha)} {branch}">{short}</span>'


def _run_rows(runs: list[dict], slug: str, now: float) -> str:
    s = _e(slug)
    rows = []
    for r in runs:
        rid = r.get("id")
        st = _e(r.get("status"))
        rows.append(
            "<tr>"
            f"<td>{_ago(r.get('finished_at') or r.get('started_at'), now)}</td>"
            f'<td class="st {st}">{st}</td>'
            f"<td>{_e(r.get('reason'))}</td>"
            f"<td>{_e(r.get('exit_code'))}</td>"
            f"<td>{_commit_cell(r)}</td>"
            f'<td><button hx-get="/-/ui/run/{rid}/output" hx-target="#out-{rid}" '
            'hx-swap="innerHTML">Output</button> '
            f'<button hx-delete="/-/ui/schedule/{s}/run/{rid}" hx-target="#detail" '
            'hx-swap="outerHTML" hx-confirm="Lauf-Record löschen?">Löschen</button></td>'
            "</tr>"
            f'<tr><td colspan="6"><div id="out-{rid}"></div></td></tr>'
        )
    return "".join(rows)


#: §5.6-Verben, die der Controller als Buttons anbietet (Durchsetzung/Scope: 4.6).
_VERBS = ("start", "reset", "kill")


def _action_bar(slug: str, job: dict | None) -> str:
    if not job or not job.get("id"):
        return ""  # kein Live-Job (z. B. nie gelaufener/entfernter Schedule)
    s = _e(slug)
    btns = "".join(
        f'<button hx-post="/-/ui/schedule/{s}/{v}" hx-target="#detail" '
        f'hx-swap="outerHTML">{v.upper()}</button> '
        for v in _VERBS
    )
    return f'<div class="actions">{btns}</div>'


def schedule_detail_inner(
    schedule: dict | None, runs: list[dict], job: dict | None,
    slug: str = "", now: float | None = None,
) -> str:
    """Der austauschbare Detail-Kern (``#detail``): MD-Anker + Aktions-Leiste
    (START/RESET/KILL) + Lauf-Liste (Output-Toggle + Löschen je Lauf)."""
    now = time.time() if now is None else now
    s = schedule or {}
    name = _e(s.get("slug") or slug)
    kind = _e(s.get("kind") or (runs[0].get("kind") if runs else ""))
    trigger = _e(s.get("trigger"))
    last = _e(s.get("last_status"))
    nxt = _until(s.get("next_fire_at"), now)
    meta = (f"Typ <b>{kind}</b> · Trigger <code>{trigger}</code> · "
            f"letzter Status <b>{last}</b> · nächster Lauf {nxt}")
    runs_html = (
        '<table><thead><tr><th>Zeit</th><th>Status</th><th>Grund</th>'
        '<th>exit</th><th>Commit</th><th>Aktion</th></tr></thead>'
        f"<tbody>{_run_rows(runs, slug, now)}</tbody></table>"
        if runs else '<p class="out-empty">— noch keine Läufe —</p>'
    )
    return (
        '<div id="detail">'
        f"<h1>{name}</h1>"
        f'<div class="meta">{meta}</div>'
        f"{_action_bar(slug, job)}"
        "<h2>Läufe</h2>"
        f"{runs_html}"
        "</div>"
    )


def schedule_detail_page(
    schedule: dict | None, runs: list[dict], job: dict | None = None,
    slug: str = "", now: float | None = None,
) -> str:
    """Schedule-zentrierte Detail-Sicht (§3 Ebene 3) als volle Seite."""
    name = _e((schedule or {}).get("slug") or slug)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {name}</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        '<a class="back" href="/-/">← zurück</a>'
        f"{schedule_detail_inner(schedule, runs, job, slug, now)}"
        "</body></html>"
    )
