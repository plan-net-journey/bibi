"""HTML-Rendering der Controller-App (PLAN-4 §4.1 ff.) — **pure** Funktionen:
Daten-dict (aus den ``/-/``-JSON-Endpunkten) → HTML. Kein HTTP, kein DB-Zugriff,
damit voll unit-testbar. Look: Terminal/Konsole-nah, minimal (§2.5)."""

from __future__ import annotations

import html
import re
import time

_HTMX = "https://unpkg.com/htmx.org@1.9.12"

#: Poll-Trigger der self-aktualisierenden Fragmente — 2s, gated durch FOLLOW
#: (``window.bibiFollow``). Zentral, damit das Intervall an einer Stelle hängt.
_POLL = "every 2s [window.bibiFollow]"

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
.st.complete { color: #5fb37a; }
.st.running { color: #5a9fe0; }
.st.pending, .st.deferred { color: #888; }
.st.failed, .st.error, .st.killed, .st.zombie { color: #e06c5a; }
.st.overdue { color: #d6a23e; }
.kind { font-family: ui-monospace, monospace; font-size: .82rem; color: #999; }
.handles { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center;
           margin: 1rem 0 .25rem; }
.handles a, .handles button { font: inherit; font-size: .82rem; text-decoration: none;
           color: inherit; background: #8882; border: 1px solid #8884;
           border-radius: .35rem; padding: .25rem .7rem; cursor: pointer; }
.handles .handle.on { background: #1a7f3733; border-color: #1a7f3766; }
.handles .handle.warn { background: #d6a23e33; border-color: #d6a23e88; }
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
.live { margin: .6rem 0 1rem; padding: .5rem .8rem; border: 1px solid #5a9fe066;
        border-radius: .4rem; background: #5a9fe014; }
.live-head { display: flex; gap: .6rem; align-items: baseline; }
.live .st { font-weight: 600; }
.liveout { margin-top: .5rem; }
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
.feed { height: 72vh; overflow-y: auto; background: #0008; border: 1px solid #8883;
        border-radius: .4rem; padding: .6rem .8rem; font-family: ui-monospace, monospace;
        font-size: .85rem; line-height: 1.7; }
.feed-row { white-space: pre-wrap; }
.feed-row .t  { color: #888; }
.feed-row .ex { color: #888; }
.feed-row a.run  { color: inherit; text-decoration: none; opacity: .75; }
.feed-row a.run:hover { text-decoration: underline; opacity: 1; }
.feed-row .st.complete { font-weight: 600; }
.bandbar { display: flex; gap: .5rem; margin: .7rem 0 .35rem; }
.bandtog { font: inherit; font-size: .82rem; background: #8882; border: 1px solid #8884;
           border-radius: .35rem; padding: .2rem .7rem; cursor: pointer; color: inherit; }
.bandtog.open { background: #5a9fe022; border-color: #5a9fe066; }
.band { border: 1px solid #8883; border-radius: .4rem; padding: .35rem .6rem;
        margin-bottom: .4rem; font-family: ui-monospace, monospace; font-size: .85rem; }
.band.collapsed { display: none; }
.band-row { padding: .15rem 0; }
.outscroll { max-height: 72vh; overflow-y: auto; }
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
        hint = ' <span class="muted">(letzter Lauf)</span>' if d.get("last_run") else ""
        rows.append(
            "<tr>"
            f"<td>{_slug_link(d.get('slug'))}</td>"
            f'<td class="st {_e(d.get("status"))}">{_e(d.get("status"))}{hint}</td>'
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
    attrs = (f'id="verdict" hx-get="/-/ui/verdict" hx-trigger="{_POLL}" hx-swap="outerHTML"')
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
    kind = _e(s.get("kind"))
    nxt = _until(s.get("next_fire_at"), now)
    return (
        "<tr>"
        f'<td><a class="slug" href="/-/ui/schedule/{slug}">{slug}</a></td>'
        f'<td class="kind">{kind}</td>'
        f'<td class="st {st}">{st}</td>'
        f"<td>{_ago(s.get('last_run_at'), now)}</td>"
        f"<td>{nxt}</td>"
        "</tr>"
    )


def _sched_table(items: list[dict], now: float) -> str:
    rows = "".join(_sched_row(s, now) for s in items)
    return ('<table><thead><tr><th>Schedule</th><th>Art</th><th>Status</th>'
            f'<th>letzter / seit</th><th>nächster</th></tr></thead><tbody>{rows}'
            "</tbody></table>")


def schedule_list(schedules: list[dict], now: float | None = None) -> str:
    """Die volle Liste — **flach + immer sichtbar** (kein Top-Level-Klapp mehr, so
    überleben keine Expands den 5s-Poll, und es deckt sich mit der bibi-v3-Sicht):
    aktive Schedules als Tabelle + ein eingeklapptes Archiv abgelaufener One-shots
    (MD bleibt — A15, §4.4)."""
    now = time.time() if now is None else now
    head = f'<h2>Schedules ({len(schedules)})</h2>'
    if not schedules:
        return head + '<p class="out-empty">— keine Schedules —</p>'
    archived = [s for s in schedules if _is_archived(s)]
    active = [s for s in schedules if not _is_archived(s)]
    body = (_sched_table(active, now) if active
            else '<p class="out-empty">— keine aktiven Schedules —</p>')
    if archived:
        body += (f'<details class="archive"><summary>Archiv ({len(archived)})'
                 f"</summary>{_sched_table(archived, now)}</details>")
    return head + body


def schedules_fragment(schedules: list[dict], now: float | None = None,
                       *, typ: str | None = None, status: str | None = None) -> str:
    """Self-pollender Wrapper um die (bereits gefilterte) Schedule-Liste. Der
    Self-Poll trägt den aktiven Filter in der URL, damit er ihn über den 2s-Tick
    bewahrt. Ziel = ``/-/ui/schedules/list`` (das Fragment; die Seite liegt auf
    ``/-/ui/schedules``)."""
    now = time.time() if now is None else now
    qs = "&".join(f"{k}={v}" for k, v in (("typ", typ), ("status", status))
                  if v and v != "alle")
    url = "/-/ui/schedules/list" + (f"?{qs}" if qs else "")
    attrs = (f'id="schedules" hx-get="{url}" hx-trigger="{_POLL}" hx-swap="outerHTML"')
    return f"<div {attrs}>{schedule_list(schedules, now)}</div>"


# ── Schedules-Screen mit Filter (Frontend-Plan §C.3) ─────────────────────────

#: Filter-Optionen. „problem" ist eine **Gruppe** (Abweichungen als Filter statt
#: eigenem Block): failed/error/killed/zombie + überfällig (pending, fällig verpasst).
_SCHED_TYPES = ("job", "claude", "app")
_SCHED_STATUSES = ("running", "pending", "complete", "failed", "deferred", "problem")
_SCHED_PROBLEM = {"failed", "error", "killed", "zombie"}


def _sched_is_problem(s: dict, now: float) -> bool:
    if s.get("last_status") in _SCHED_PROBLEM:
        return True
    nf = s.get("next_fire_at")  # überfällig: pending, dessen Trigger in der Vergangenheit liegt
    return s.get("row_status") == "pending" and nf is not None and nf < now


def filter_schedules(schedules: list[dict], *, typ: str | None = None,
                     status: str | None = None, now: float | None = None) -> list[dict]:
    """Schedules nach Typ und Status filtern (rein). ``alle``/leer = kein Filter;
    ``status='problem'`` matcht die Abweichungs-Gruppe (inkl. überfällig)."""
    now = time.time() if now is None else now
    out = list(schedules)
    if typ and typ != "alle":
        out = [s for s in out if s.get("kind") == typ]
    if status and status != "alle":
        if status == "problem":
            out = [s for s in out if _sched_is_problem(s, now)]
        else:
            out = [s for s in out if s.get("last_status") == status]
    return out


def _filter_bar(typ: str | None, status: str | None) -> str:
    def _opts(values: tuple, cur: str | None) -> str:
        cur = cur or "alle"
        parts = [f'<option value="alle"{" selected" if cur == "alle" else ""}>alle</option>']
        for v in values:
            parts.append(f'<option value="{v}"{" selected" if cur == v else ""}>{v}</option>')
        return "".join(parts)

    common = ('hx-get="/-/ui/schedules/list" hx-target="#schedules" hx-swap="outerHTML" '
              'hx-include="[name=\'typ\'],[name=\'status\']"')
    return (
        '<div class="logbar">'
        f'<label>Typ <select name="typ" {common}>{_opts(_SCHED_TYPES, typ)}</select></label>'
        f'<label>Status <select name="status" {common}>{_opts(_SCHED_STATUSES, status)}</select></label>'
        '</div>'
    )


def _screen_nav(active: str) -> str:
    """Screen-Tabs (Feed · Schedules · Live-Log · API-Docs); der aktive ohne Link."""
    tabs = [("Feed", "/-/ui/feed"), ("Schedules", "/-/ui/schedules"),
            ("Live-Log", "/-/ui/logs"), ("API-Docs", "/-/docs")]
    items = [t if t == active else f'<a class="back" href="{h}">{t}</a>' for t, h in tabs]
    return '<span class="muted">' + " · ".join(items) + "</span>"


def schedules_page(schedules: list[dict], typ: str | None = None,
                   status: str | None = None, now: float | None = None) -> str:
    """Der Schedules-Screen: Nav + Filterleiste + (gefilterte) self-pollende Liste.
    ``schedules`` ist bereits gefiltert; ``typ``/``status`` spiegeln die Auswahl."""
    now = time.time() if now is None else now
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Schedules</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f'<header><h1>bibi</h1>{_screen_nav("Schedules")}</header>'
        f"{_filter_bar(typ, status)}"
        f"{schedules_fragment(schedules, now, typ=typ, status=status)}"
        "</body></html>"
    )


def maint_handle(status: dict) -> str:
    """Maintenance-Toggle, der seinen Zustand spiegelt (``/-/status.maintenance``)."""
    on = bool(status.get("maintenance"))
    label = "MAINT: AN" if on else "MAINT: aus"
    cls = "handle warn" if on else "handle"
    return (f'<button id="maint" class="{cls}" hx-post="/-/ui/maintenance" '
            f'hx-target="#maint" hx-swap="outerHTML">{label}</button>')


def _handles(status: dict) -> str:
    """Die Handle-Leiste: Rescan, Docs, Live-Log, FOLLOW-Toggle, Maintenance."""
    return (
        '<nav class="handles">'
        '<button hx-post="/-/ui/rescan" hx-target="#schedules" '
        'hx-swap="outerHTML">RESCAN</button>'
        '<button id="follow" class="handle on" onclick="bibiToggleFollow()">FOLLOW: AN</button>'
        f"{maint_handle(status)}"
        '<a href="/-/ui/logs">Live-Log</a>'
        '<a href="/-/docs">API-Docs</a>'
        '<a href="/-/redoc">ReDoc</a>'
        "</nav>"
    )


#: FOLLOW-Toggle: steuert ``window.bibiFollow`` (Trigger-Filter der Poll-Fragmente).
#: Vor htmx-Init gesetzt (im <head>), damit die Trigger den Startzustand sehen.
_FOLLOW_JS = """
window.bibiFollow = (localStorage.getItem('bibiFollow') ?? '1') === '1';
function bibiToggleFollow(){
  window.bibiFollow = !window.bibiFollow;
  localStorage.setItem('bibiFollow', window.bibiFollow ? '1' : '0');
  const b = document.getElementById('follow');
  b.textContent = 'FOLLOW: ' + (window.bibiFollow ? 'AN' : 'aus');
  b.className = 'handle ' + (window.bibiFollow ? 'on' : '');
}
document.addEventListener('DOMContentLoaded', () => {
  const b = document.getElementById('follow');
  if (b && !window.bibiFollow){ b.textContent = 'FOLLOW: aus'; b.className = 'handle'; }
});
"""


def dashboard_page(
    status: dict, schedules: list[dict] | None = None, now: float | None = None
) -> str:
    """Die App-Wurzel ``/-/`` (Browser): Server-Render — Handle-Leiste, dann Verdikt
    + Abweichungen (Ebene 0/1), darunter die flache, self-pollende Liste (Ebene 2)."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        '<header><h1>bibi</h1><span class="muted">Health- &amp; Anomalie-Sicht'
        "</span></header>"
        f"{_handles(status)}"
        f"{verdict_fragment(status, now)}"
        f"{schedules_fragment(schedules or [], now)}"
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


# ── Feed (Home) — Journal als zeitsortierte Strömung (Frontend-Plan §C.1) ────

def _hms(ts: float | None) -> str:
    """Absolute Uhrzeit ``HH:MM:SS`` (lokal) — der Feed zeigt *wann*, nicht *vor wie lang*."""
    if ts is None:
        return "--:--:--"
    return time.strftime("%H:%M:%S", time.localtime(ts))


def feed_row(r: dict, now: float | None = None) -> str:
    """Eine Journal-Zeile als Feed-Eintrag (rein). ``status`` trägt die CSS-Klasse
    (complete prominent); ``run_id`` verlinkt zum Execution-Detail (Stufe 4),
    ``slug`` zum Schedule-Detail. Kein inline-Output (Entscheidung #4 — Link genügt)."""
    rid = _e(r.get("run_id"))
    slug = _e(r.get("slug"))
    st = _e(r.get("status"))
    jid = r.get("id")
    t = _hms(r.get("finished_at") or r.get("started_at"))
    exit_code = r.get("exit_code")
    ex = f'<span class="ex">exit {_e(exit_code)}</span> ' if exit_code is not None else ""
    sha = r.get("commit_sha")
    commit = f'<span class="commit">⎘ {_e(sha[:7])}</span>' if sha else ""
    run = (f'<a class="run" href="/-/ui/run/{_e(jid)}">{rid}</a>'
           if jid is not None else rid)
    return (
        f'<div class="feed-row" data-jid="{_e(jid)}">'
        f'<span class="t">{t}</span> '
        f'<span class="st {st}">{st}</span> '
        f'<a class="slug" href="/-/ui/schedule/{slug}">{slug}</a> '
        f"{run} {ex}{commit}"
        "</div>"
    )


def feed_list(rows: list[dict], now: float | None = None) -> str:
    """Die Journal-Zeilen als Feed (rein). Eingabe = ``/-/journal`` (archived_at
    **DESC**, neueste zuerst); der Feed zeigt neueste **unten** → umgedreht gerendert."""
    now = time.time() if now is None else now
    body = "".join(feed_row(r, now) for r in reversed(rows))
    return f'<div id="feed" class="feed">{body}</div>'


#: Live-Feed: an ``/-/feed/stream`` (Stufe 0) hängen; jedes Journal-Event als Zeile
#: **unten** anhängen (Tail), Autoscroll wenn am Ende. Backfill-Dedup über data-jid
#: (der Server hat die Backfill-Zeilen schon gerendert) — der Stream re-sendet sie,
#: wir überspringen bereits bekannte IDs und hängen nur wirklich Neues an.
_FEED_JS = """
(function(){
  const feed = document.getElementById('feed');
  if (!feed) return;
  const seen = new Set([...feed.querySelectorAll('.feed-row')].map(e => e.dataset.jid));
  feed.scrollTop = feed.scrollHeight;
  function el(tag, cls, txt){ const e=document.createElement(tag);
    if(cls) e.className=cls; if(txt!=null) e.textContent=txt; return e; }
  function feedRow(o){
    const row = el('div','feed-row'); row.dataset.jid = o.id;
    const t = o.finished_at ? new Date(o.finished_at*1000).toLocaleTimeString() : '';
    row.appendChild(el('span','t',t)); row.append(' ');
    row.appendChild(el('span','st '+(o.status||''), o.status||'')); row.append(' ');
    const s = el('a','slug',o.slug||''); s.href='/-/ui/schedule/'+encodeURIComponent(o.slug||'');
    row.appendChild(s); row.append(' ');
    const r = el('a','run',o.run_id||''); r.href='/-/ui/run/'+encodeURIComponent(o.id);
    row.appendChild(r); row.append(' ');
    if (o.exit_code!=null){ row.appendChild(el('span','ex','exit '+o.exit_code)); row.append(' '); }
    if (o.commit_sha) row.appendChild(el('span','commit','\\u2398 '+String(o.commit_sha).slice(0,7)));
    return row;
  }
  const atBottom = () => feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 24;
  const es = new EventSource('/-/feed/stream');
  es.onmessage = (e) => {
    let o; try { o = JSON.parse(e.data); } catch(_) { return; }
    if (o.id==null || seen.has(String(o.id))) return;
    seen.add(String(o.id));
    const stick = atBottom();
    feed.appendChild(feedRow(o));
    if (stick) feed.scrollTop = feed.scrollHeight;
  };
})();
"""


#: Band-Zugehörigkeit (Frontend-Plan §C.2, Achse #2 „nicht im Journal"): nur
#: **nicht-terminale** jobs-Zustände. Terminale (complete/error/killed/zombie/
#: inactive) stehen im Feed/Journal, nicht in den Bändern.
_ACTIVE_STATES = ("running", "failed", "deferred")


def _aktiv_row(j: dict, now: float) -> str:
    slug = _e(j.get("slug"))
    st = _e(j.get("status"))
    bits: list[str] = []
    if j.get("status") == "running" and j.get("started_at"):
        bits.append(f"seit {_ago(j.get('started_at'), now)}")
    if j.get("status") == "failed" and j.get("next_fire_at"):
        bits.append(f"retry {_until(j.get('next_fire_at'), now)}")
    if j.get("reason"):
        bits.append(_e(j.get("reason")))
    tail = " · ".join(bits)
    return (f'<div class="band-row"><span class="st {st}">{st}</span> '
            f'<a class="slug" href="/-/ui/schedule/{slug}">{slug}</a>'
            f'{" · " + tail if tail else ""}</div>')


def _wartet_row(j: dict, now: float) -> str:
    slug = _e(j.get("slug"))
    nxt = _until(j.get("next_fire_at"), now)
    return (f'<div class="band-row"><span class="st pending">○</span> '
            f'<a class="slug" href="/-/ui/schedule/{slug}">{slug}</a> '
            f'<span class="muted">{nxt}</span></div>')


def bands_fragment(jobs: list[dict], now: float | None = None) -> str:
    """Die zwei Bänder (rein): aktiv (running/failed/deferred) + wartet (pending),
    in der Kopfzeile gezählt. Klapp-Zustand client-seitig (``_BANDS_JS`` / localStorage),
    darum hier nur das Markup mit ``data-band``; die Zähler sind serverseitig frisch."""
    now = time.time() if now is None else now
    active = [j for j in jobs if j.get("status") in _ACTIVE_STATES]
    waiting = [j for j in jobs if j.get("status") == "pending"]
    a_body = ("".join(_aktiv_row(j, now) for j in active)
              or '<div class="out-empty">— nichts aktiv —</div>')
    w_body = ("".join(_wartet_row(j, now) for j in waiting)
              or '<div class="out-empty">— nichts wartend —</div>')
    return (
        '<div id="bands">'
        '<div class="bandbar">'
        f'<button class="bandtog" data-band="aktiv" onclick="bibiToggleBand(\'aktiv\')">'
        f'▶ {len(active)} aktiv</button>'
        f'<button class="bandtog" data-band="wartet" onclick="bibiToggleBand(\'wartet\')">'
        f'○ {len(waiting)} wartet</button>'
        '</div>'
        f'<div class="band" data-band="aktiv">{a_body}</div>'
        f'<div class="band" data-band="wartet">{w_body}</div>'
        '</div>'
    )


#: Bänder: Klapp-Zustand aus localStorage (Default aktiv **auf**, wartet **zu** —
#: Entscheidung #6) bei Load + nach jedem Refresh anwenden; alle 2 s ``/-/ui/feed/bands``
#: nachladen (Live-State der jobs-Tabelle). Der stdout-Live-Stream im aktiv-Band
#: folgt in Stufe 5.
_BANDS_JS = """
(function(){
  function applyBands(){
    document.querySelectorAll('.band').forEach(b => {
      const k=b.dataset.band, def=(k==='aktiv'?'1':'0');
      const open=(localStorage.getItem('bibiBand.'+k) ?? def)==='1';
      b.classList.toggle('collapsed', !open);
      const t=document.querySelector('.bandtog[data-band="'+k+'"]');
      if(t) t.classList.toggle('open', open);
    });
  }
  window.bibiToggleBand=function(k){
    const def=(k==='aktiv'?'1':'0');
    const cur=(localStorage.getItem('bibiBand.'+k) ?? def)==='1';
    localStorage.setItem('bibiBand.'+k, cur?'0':'1');
    applyBands();
  };
  applyBands();
  setInterval(async () => {
    try{
      const r=await fetch('/-/ui/feed/bands'); if(!r.ok) return;
      const html=await r.text();
      const wrap=document.getElementById('bands');
      if(wrap){ wrap.outerHTML=html; applyBands(); }
    }catch(_){}
  }, 2000);
})();
"""


def _feed_nav() -> str:
    """Screen-Navigation des Feed (Home) — gemeinsame Tab-Leiste."""
    return _screen_nav("Feed")


def feed_page(rows: list[dict], jobs: list[dict] | None = None,
              now: float | None = None) -> str:
    """Der Feed-Screen (Home): Server-Backfill (neueste unten) + Live-Push per SSE,
    darunter die Bänder „aktiv"/„wartet" (gezählt, klappbar). Der Daemon liefert
    JSON; das FE rendert — analog zum Live-Log-Panel."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Feed</title>"
        f"<style>{_CSS}</style></head><body>"
        f'<header><h1>bibi</h1>{_feed_nav()}</header>'
        f"{feed_list(rows, now)}"
        f"{bands_fragment(jobs or [], now)}"
        f"<script>{_FEED_JS}</script>"
        f"<script>{_BANDS_JS}</script>"
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


def _run_rows(runs: list[dict], slug: str, now: float,
              top_output: dict | None = None) -> str:
    s = _e(slug)
    rows = []
    for i, r in enumerate(runs):
        rid = r.get("id")
        st = _e(r.get("status"))
        # Der jüngste Lauf bekommt den Output **default expanded** (server-gerendert,
        # überlebt den Poll); ältere behalten den Lazy-Toggle (sonst N Fetches/Poll).
        if i == 0 and top_output is not None:
            out_cell = '<span class="muted">Output ↓</span>'
            out_html = output_block(top_output.get("events", []),
                                    top_output.get("kind", "job"))
        else:
            out_cell = (f'<button hx-get="/-/ui/run/{rid}/output" hx-target="#out-{rid}" '
                        'hx-swap="innerHTML">Output</button>')
            out_html = ""
        rows.append(
            "<tr>"
            f"<td>{_ago(r.get('finished_at') or r.get('started_at'), now)}</td>"
            f'<td class="st {st}">{st}</td>'
            f"<td>{_e(r.get('reason'))}</td>"
            f"<td>{_e(r.get('exit_code'))}</td>"
            f"<td>{_commit_cell(r)}</td>"
            f"<td>{out_cell} "
            f'<a class="back" href="/-/ui/run/{rid}">→ Detail</a> '
            f'<button hx-delete="/-/ui/schedule/{s}/run/{rid}" hx-target="#detail" '
            'hx-swap="outerHTML" hx-confirm="Lauf-Record löschen?">Löschen</button></td>'
            "</tr>"
            f'<tr><td colspan="6"><div id="out-{rid}">{out_html}</div></td></tr>'
        )
    return "".join(rows)


def _live_panel(job: dict | None, now: float, live_output: dict | None = None) -> str:
    """Eigener Block für den **aktiven/anstehenden** Lauf, nahe am Header — getrennt
    vom Journal (das erst beim Terminal-Übergang eine Zeile bekommt). So ist der
    laufende Job sofort sichtbar (pending → running → …); wird er terminal,
    verschwindet der Block und der echte Journal-Eintrag erscheint unten. Der
    Live-Output wird **default expanded** mitgerendert (server-seitig, überlebt Poll)."""
    if not job or job.get("status") in _TERMINAL_VIEW:
        return ""
    st = _e(job.get("status"))
    started = job.get("started_at")
    bits = []
    if started:
        bits.append(f"seit {_ago(started, now)}")
    if job.get("status") == "pending" and job.get("next_fire_at"):
        bits.append(f"nächster Lauf {_until(job.get('next_fire_at'), now)}")
    if job.get("reason"):
        bits.append(_e(job.get("reason")))
    tail = (" · " + " · ".join(bits)) if bits else ""
    out = ""
    if live_output and live_output.get("events"):
        out = ('<div class="liveout">'
               + output_block(live_output["events"], live_output.get("kind", "job"))
               + "</div>")
    return (f'<div class="live"><div class="live-head">'
            f'<span class="st {st}">{st}</span>'
            f'<span class="muted">aktiver Lauf{tail}</span></div>{out}</div>')


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
    *, top_output: dict | None = None, live_output: dict | None = None,
) -> str:
    """Der austauschbare Detail-Kern (``#detail``): Meta + Aktions-Leiste
    (START/RESET/KILL) + Live-Block (aktiver Lauf, Output default expanded) +
    Journal (jüngster Lauf-Output default expanded, ältere per Toggle)."""
    now = time.time() if now is None else now
    s = schedule or {}
    name = _e(s.get("slug") or slug)
    kind = _e(s.get("kind") or (runs[0].get("kind") if runs else ""))
    trigger = _e(s.get("trigger"))
    last_run = _e(runs[0]["status"]) if runs else "—"   # Ergebnis des letzten Laufs (Journal)
    nxt = _until(s.get("next_fire_at"), now)
    meta = (f"Typ <b>{kind}</b> · Trigger <code>{trigger}</code> · "
            f"letzter Lauf <b>{last_run}</b> · nächster Lauf {nxt}")
    runs_html = (
        '<table><thead><tr><th>Zeit</th><th>Status</th><th>Grund</th>'
        '<th>exit</th><th>Commit</th><th>Aktion</th></tr></thead>'
        f"<tbody>{_run_rows(runs, slug, now, top_output)}</tbody></table>"
        if runs else '<p class="out-empty">— noch keine Läufe —</p>'
    )
    # #detail self-pollt (2s, FOLLOW-gated) → Live-Block wechselt pending→running→…
    attrs = (f'id="detail" hx-get="/-/ui/schedule/{_e(slug)}/detail" '
             f'hx-trigger="{_POLL}" hx-swap="outerHTML"')
    return (
        f"<div {attrs}>"
        f"<h1>{name}</h1>"
        f'<div class="meta">{meta}</div>'
        f"{_action_bar(slug, job)}"
        f"{_live_panel(job, now, live_output)}"
        "<h2>Journal</h2>"
        f"{runs_html}"
        "</div>"
    )


def schedule_detail_page(
    schedule: dict | None, runs: list[dict], job: dict | None = None,
    slug: str = "", now: float | None = None,
    *, top_output: dict | None = None, live_output: dict | None = None,
) -> str:
    """Schedule-zentrierte Detail-Sicht (§3 Ebene 3) als volle Seite."""
    name = _e((schedule or {}).get("slug") or slug)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {name}</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        '<a class="back" href="/-/">← zurück</a>'
        f"{schedule_detail_inner(schedule, runs, job, slug, now, top_output=top_output, live_output=live_output)}"
        "</body></html>"
    )


# ── Execution-Detail (Ebene 4, lauf-zentriert; Frontend-Plan §C.4) ───────────


def execution_detail_page(entry: dict | None, events: list[dict], kind: str,
                          now: float | None = None) -> str:
    """Ein **Lauf** (``run_id``): Meta (Status/exit/Dauer/host/worker/Commit) + voller
    per-Run-Output aus ``output_ref``. Ziel der Feed-/Journal-/Schedule-Links."""
    now = time.time() if now is None else now
    e = entry or {}
    run_id = _e(e.get("run_id"))
    slug = _e(e.get("slug"))
    st = _e(e.get("status"))
    k = _e(e.get("kind") or kind)
    meta = [f'<span class="st {st}">{st}</span>', k]
    if e.get("exit_code") is not None:
        meta.append(f"exit {_e(e.get('exit_code'))}")
    dur = e.get("exec_runtime")
    if dur is None and e.get("started_at") is not None and e.get("finished_at") is not None:
        dur = e["finished_at"] - e["started_at"]
    if dur is not None:
        meta.append(f"Dauer {int(dur)} s")
    if e.get("reason"):
        meta.append(_e(e.get("reason")))
    times = []
    if e.get("started_at"):
        times.append(f"gestartet {_hms(e.get('started_at'))}")
    if e.get("finished_at"):
        times.append(f"beendet {_hms(e.get('finished_at'))}")
    if e.get("host"):
        times.append(f"host {_e(e.get('host'))}")
    if e.get("worker"):
        times.append(f"worker {_e(e.get('worker'))}")
    commit = ""
    if e.get("commit_sha"):
        sha = e["commit_sha"]
        branch = _e(e.get("branch") or "")
        commit = (f'<div class="meta">Ergebnis-Commit '
                  f'<span class="commit" title="{_e(sha)} {branch}">⎘ {_e(sha[:7])}</span></div>')
    out = output_block(events, e.get("kind") or kind)
    back = (f'<a class="back" href="/-/ui/schedule/{slug}">← {slug}</a> · '
            f'<a class="back" href="/-/ui/feed">Feed</a>')
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {run_id}</title>"
        f"<style>{_CSS}</style></head><body>"
        f'<header><h1>bibi · {run_id}</h1><span class="muted">{back}</span></header>'
        f'<div class="meta">{" · ".join(meta)}</div>'
        f'<div class="meta">{" · ".join(times)}</div>'
        f"{commit}"
        "<h2>Output</h2>"
        f'<div class="outscroll">{out}</div>'
        "</body></html>"
    )
