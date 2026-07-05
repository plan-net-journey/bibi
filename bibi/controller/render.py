"""HTML-Rendering der Controller-App (PLAN-4 §4.1 ff.) — **pure** Funktionen:
Daten-dict (aus den ``/-/``-JSON-Endpunkten) → HTML. Kein HTTP, kein DB-Zugriff,
damit voll unit-testbar. Look: Terminal/Konsole-nah, minimal (§2.5)."""

from __future__ import annotations

import html
import re
import time

from bibi.schedule import models

_HTMX = "https://unpkg.com/htmx.org@1.9.12"

#: Poll-Trigger der self-aktualisierenden Fragmente — 2s, gated durch FOLLOW
#: (``window.bibiFollow``). Zentral, damit das Intervall an einer Stelle hängt.
_POLL = "every 2s [window.bibiFollow]"

_CSS = """
:root { color-scheme: light dark; }
:root[data-theme="light"] { color-scheme: light; }
:root[data-theme="dark"] { color-scheme: dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem;
       max-width: 64rem; margin-inline: auto; }
header { display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap; }
header .handles { margin: 0; }
h1 { font-size: 1.4rem; margin: 0; }
.muted { color: #888; font-size: .85rem; }
.banner { margin: 0; padding: .35rem .75rem; border-radius: .35rem;
          border: 1px solid #8884; font-size: .82rem; font-weight: 500;
          display: inline-block; }
.banner.ok  { background: #1a7f3722; }
.banner.bad { background: #c0392b22; }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th { text-align: left; color: #888; font-weight: 500; padding: .35rem .5rem;
     border-bottom: 1px solid #8883; }
td { padding: .4rem .5rem; border-bottom: 1px solid #8882; }
.st { font-family: ui-monospace, monospace; }
.st.complete { color: #5fb37a; }
.st.running { color: #5a9fe0; }
.st.awaiting { color: #d6a23e; }
.st.pending, .st.deferred { color: #888; }
.st.failed, .st.error, .st.killed, .st.zombie { color: #e06c5a; }
.st.overdue { color: #d6a23e; }
.kind { font-family: ui-monospace, monospace; font-size: .82rem; color: #999; }
.handles { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center;
           margin: 1rem 0 .25rem; }
.handles a, .handles button { font: inherit; font-size: .82rem; text-decoration: none;
           color: inherit; background: #8882; border: 1px solid #8884;
           border-radius: .35rem; padding: .15rem .5rem; cursor: pointer; }
.handles .handle.on { background: #1a7f3733; border-color: #1a7f3766; }
.handles .handle.warn { background: #d6a23e33; border-color: #d6a23e88; }
a.slug { font-weight: 600; text-decoration: none; }
a.slug:hover { text-decoration: underline; }
.sched a { text-decoration: none; }
.sched a:hover { text-decoration: underline; }
.sched a.rowlink { color: inherit; }
h2 { font-size: .95rem; color: #888; margin: 1.5rem 0 .4rem; font-weight: 600; }
.back { color: #888; text-decoration: none; font-size: .85rem; }
.meta { color: #aaa; font-size: .9rem; margin: .2rem 0 1rem; }
.term { background: #0008; border: 1px solid #8883; border-radius: .4rem;
        padding: .6rem .8rem; overflow-x: auto; font-family: ui-monospace, monospace;
        font-size: .82rem; line-height: 1.45; white-space: pre-wrap; }
.term .err { color: #e06c5a; }
.term .thinking { color: #888; font-style: italic; }
.term .phase { color: #5a9fe0; font-style: italic; }
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
/* ~20 Zeilen (.term: font-size .82rem, line-height 1.45) dann scrollbar — nur
   awaiting/terminal-Output (siehe _live_panel); running hat mit .liveterm
   bereits einen eigenen Cap, kein zweiter verschachtelter nötig. */
.liveclamp { max-height: 23.8rem; overflow-y: auto; }
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
.feed { height: 45vh; overflow-y: auto; background: #0008; border: 1px solid #8883;
        border-radius: .4rem; padding: .6rem .8rem; font-family: ui-monospace, monospace;
        font-size: .85rem; line-height: 1.7; }
.feed-row { white-space: pre-wrap; }
.feed-row .t  { color: #888; }
.feed-row .ex { color: #888; }
.feed-row a.run  { color: inherit; text-decoration: none; opacity: .75; }
.feed-row a.run:hover { text-decoration: underline; opacity: 1; }
.feed-row .st.complete { font-weight: 600; }
#bands h3 { margin: .7rem 0 .3rem; font-size: .95rem; }
.bandscroll { max-height: 30vh; overflow-y: auto; border: 1px solid #8883;
              border-radius: .4rem; padding: .35rem .6rem; margin-bottom: .4rem;
              font-family: ui-monospace, monospace; font-size: .85rem; }
.band-row { padding: .15rem 0; }
.outscroll { max-height: 72vh; overflow-y: auto; }
.hitl { margin: .5rem 0 0; padding: .5rem .75rem; border: 1px solid #d6a23e55;
        border-radius: .35rem; background: #d6a23e0d; }
.hitl-label { font-weight: 600; font-size: .9rem; margin-bottom: .45rem; }
.hitl a { color: #d6a23e; word-break: break-all; }
.liveterm { max-height: 24rem; overflow-y: auto; }
.liveterm .lts { color: #888; user-select: none; }
.liveclock { color: #5fb37a; font-size: .8rem; font-family: ui-monospace, monospace; }
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


def _abs_time(ts: float | None) -> str:
    """Absoluter Zeitstempel als HH:MM (Lokalzeit). None → „—"."""
    if ts is None:
        return "—"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def _abs_datetime(ts: float | None, now: float) -> str:
    """Wie ``_abs_time``, aber mit Datum (TT.MM.) für alles außer heute — die
    Journal-Historie zeigt über Infinite Scroll oft tage-/wochenalte Läufe,
    "17:02" allein ist dann nicht mehr zuordenbar (User-Feedback)."""
    if ts is None:
        return "—"
    import datetime
    dt = datetime.datetime.fromtimestamp(ts)
    if dt.date() == datetime.datetime.fromtimestamp(now).date():
        return dt.strftime("%H:%M")
    return dt.strftime("%d.%m. %H:%M")


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


# ── Volle Schedule-Liste + Archiv (Ebene 2, §4.4) ────────────────────────────

#: Terminale Sicht-Zustände — ein One-shot in einem davon gilt als „abgelaufen".
_TERMINAL_VIEW = {"complete", "error", "inactive", "zombie", "killed"}


def _group_schedules(schedules: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Registrierungs-Drei-Gruppen (PLAN-14 Stufe 14.6, orthogonal zum Laufzeit-
    Status): aktiv (MD entdeckt) / inaktiv (DB-Zeile ohne MD) / journal (nur
    Journal-Historie, kein jobs-Eintrag mehr). Fehlt der ``active``-Key (ältere
    Fixtures), gilt der Schedule als aktiv."""
    active = [s for s in schedules if s.get("active", True) is True]
    inactive = [s for s in schedules if s.get("active", True) is False]
    journaled = [s for s in schedules if s.get("active", True) is None]
    return active, inactive, journaled


def _sched_row(s: dict, now: float) -> str:
    slug = _e(s.get("slug"))
    st = _e(s.get("last_status"))
    kind = _e(_effective_sched_type(s))
    nxt = _until(s.get("next_fire_at"), now)
    ago = _ago(s.get("last_run_at"), now)
    run_id = s.get("last_run_id")
    # Status/letzter-seit -> Lauf-Details (die konkrete Ausführung); Schedule/
    # nächster -> Job-Details (der Schedule selbst) — User-Feedback 2026-07-01.
    # Ohne abgeschlossenen Lauf (run_id None) gibt es keine Lauf-Details zum Verlinken.
    status_cell = (f'<a class="st {st}" href="/-/ui/run/{run_id}">{st}</a>'
                   if run_id is not None else f'<span class="st {st}">{st}</span>')
    ago_cell = (f'<a class="rowlink" href="/-/ui/run/{run_id}">{ago}</a>'
                if run_id is not None else ago)
    return (
        "<tr>"
        f'<td><a class="slug" href="/-/ui/schedule/{slug}">{slug}</a></td>'
        f'<td class="kind">{kind}</td>'
        f"<td>{status_cell}</td>"
        f"<td>{ago_cell}</td>"
        f'<td><a class="rowlink" href="/-/ui/schedule/{slug}">{nxt}</a></td>'
        "</tr>"
    )


def _sched_table(items: list[dict], now: float) -> str:
    rows = "".join(_sched_row(s, now) for s in items)
    return ('<table class="sched"><thead><tr><th>Schedule</th><th>Art</th><th>Status</th>'
            f'<th>letzter / seit</th><th>nächster</th></tr></thead><tbody>{rows}'
            "</tbody></table>")


def schedule_list(schedules: list[dict], now: float | None = None) -> str:
    """Die volle Liste, gruppiert nach Registrierungs-Zustand (PLAN-14 Stufe
    14.6): Aktiv (MD entdeckt) / Inaktiv (DB-Zeile ohne MD) / Journal (nur
    Journal-Historie). Flach + immer sichtbar, kein Klapp mehr — überlebt so
    den 2s-Poll ohne Expand-Verlust."""
    now = time.time() if now is None else now
    head = f'<h2>Schedules ({len(schedules)})</h2>'
    if not schedules:
        return head + '<p class="out-empty">— keine Schedules —</p>'
    active, inactive, journaled = _group_schedules(schedules)
    body = (_sched_table(active, now) if active
            else '<p class="out-empty">— keine aktiven Schedules —</p>')
    if inactive:
        body += f'<h3>Inaktiv — MD entfernt ({len(inactive)})</h3>' + _sched_table(inactive, now)
    if journaled:
        body += f'<h3>Journal — nur Historie ({len(journaled)})</h3>' + _sched_table(journaled, now)
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


def _effective_sched_type(s: dict) -> str:
    """Anzeige-/Filter-Typ ableiten — ``kind`` ist seit PLAN-10 (Unified Job
    Model) immer ``"job"`` und trägt keine Information mehr (§5.3). Delegiert an
    ``models.effective_kind`` (PLAN-12 Stufe 12.0 — einzige Quelle für alle Aufrufer)."""
    return models.effective_kind(s.get("payload"), s.get("app_port"))


def filter_schedules(schedules: list[dict], *, typ: str | None = None,
                     status: str | None = None, now: float | None = None) -> list[dict]:
    """Schedules nach Typ und Status filtern (rein). ``alle``/leer = kein Filter;
    ``status='problem'`` matcht die Abweichungs-Gruppe (inkl. überfällig)."""
    now = time.time() if now is None else now
    out = list(schedules)
    if typ and typ != "alle":
        out = [s for s in out if _effective_sched_type(s) == typ]
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
    """Screen-Tabs (Schedules · Live-Log · API-Docs); der aktive ohne Link.
    Feed ist entfernt (User-Feedback 2026-07-04); Schedules ist jetzt Home (``/-/``)."""
    tabs = [("Schedules", "/-/"), ("Live-Log", "/-/ui/logs"), ("API-Docs", "/-/docs")]
    def _tab(t: str, h: str) -> str:
        if t == active:
            return t
        extra = ' target="_blank" rel="noopener"' if t == "API-Docs" else ""
        return f'<a class="back" href="{h}"{extra}>{t}</a>'
    items = [_tab(t, h) for t, h in tabs]
    return '<span class="muted">' + " · ".join(items) + "</span>"


def _live_clock() -> str:
    """Tickende Lebendigkeits-Anzeige (Feedback Z. 2) — von ``_CLOCK_JS`` gesetzt."""
    return '<span class="liveclock" id="liveclock">● live --:--:--</span>'


#: Setzt die Uhr sekündlich (rein client-seitig) — „wir leben noch".
_CLOCK_JS = """
(function(){
  const c = document.getElementById('liveclock');
  if (!c) return;
  const tick = () => { c.textContent = '● live ' + new Date().toLocaleTimeString(); };
  tick(); setInterval(tick, 1000);
})();
"""


def _follow_toggle() -> str:
    """FOLLOW-Button (pausiert Live-Updates, ``window.bibiFollow``) — Teil des
    gemeinsamen Headers (Follow-up: war zuvor nur auf dem Feed-Screen
    sichtbar/steuerbar, jetzt auf jedem Screen)."""
    return '<button id="follow" class="handle on" onclick="bibiToggleFollow()">FOLLOW: AN</button>'


def _theme_toggle() -> str:
    """DARK/LIGHT-Button — Teil des gemeinsamen Headers, neben FOLLOW
    (User-Feedback 2026-07-04). Startlabel per ``_THEME_JS`` gesetzt (Default =
    System-Präferenz), damit hier kein Server-seitiger Theme-State nötig ist."""
    return '<button id="theme" class="handle" onclick="bibiToggleTheme()">THEME</button>'


#: DARK/LIGHT-Toggle: überschreibt ``color-scheme`` explizit via ``data-theme``
#: auf <html> (s. _CSS), Default = System-Präferenz (``prefers-color-scheme``),
#: persistiert in localStorage — analog zu _FOLLOW_JS.
_THEME_JS = """
(function(){
  const KEY = 'bibiTheme';
  const root = document.documentElement;
  function apply(theme){
    root.setAttribute('data-theme', theme);
    const b = document.getElementById('theme');
    if (b) b.textContent = 'THEME: ' + theme.toUpperCase();
  }
  window.bibiToggleTheme = function(){
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    localStorage.setItem(KEY, next);
    apply(next);
  };
  const stored = localStorage.getItem(KEY);
  apply(stored || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
})();
"""


def _header(active: str, status: dict | None = None) -> str:
    """Gemeinsame obere Navigationsleiste: Titel + Live-Uhr + Tab-Leiste + FOLLOW-
    + THEME-Toggle + Ops-Handles (RESCAN/MAINT). Ein Baustein für jeden Screen
    (User-Feedback 2026-07-04: "ziehe Rescan und Maintenance CTA auf die obere
    Navigationsleiste mit FOLLOW on/off" — dadurch auch auf Live-Log sichtbar,
    vorher pro Screen separat bzw. gar nicht eingebunden)."""
    return (f'<header><h1>bibi</h1>{_live_clock()} {_screen_nav(active)} '
            f'{_follow_toggle()}{_theme_toggle()}{_ops_handles(status)}</header>')


def schedules_page(schedules: list[dict], typ: str | None = None,
                   status: str | None = None, now: float | None = None,
                   *, daemon_status: dict | None = None) -> str:
    """Der Schedules-Screen: Nav + Ops-Handles (RESCAN/MAINT, User-Feedback
    2026-07-03) + Filterleiste + (gefilterte) self-pollende Liste. ``schedules``
    ist bereits gefiltert; ``typ``/``status`` spiegeln die Auswahl — ``status``
    ist hier der Filterwert (z. B. "error"), nicht zu verwechseln mit
    ``daemon_status`` (``/-/status``-JSON für den MAINT-Toggle)."""
    now = time.time() if now is None else now
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Schedules</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Schedules', daemon_status)}"
        f"{_filter_bar(typ, status)}"
        f"{schedules_fragment(schedules, now, typ=typ, status=status)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
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
  // Wieder-Einschalten muss sofort ans Ende springen — sonst bleibt die Box
  // bis zum nächsten Append "stick=false" (atBottom() prüft die aktuelle
  // Scroll-Position, die während FOLLOW=aus eingefroren war) und folgt nie
  // wieder, obwohl der Nutzer genau das mit dem Klick angefordert hat.
  if (window.bibiFollow){
    document.querySelectorAll('.liveterm').forEach(box => {
      box.scrollTop = box.scrollHeight;
    });
  }
}
document.addEventListener('DOMContentLoaded', () => {
  const b = document.getElementById('follow');
  if (b && !window.bibiFollow){ b.textContent = 'FOLLOW: aus'; b.className = 'handle'; }
});
"""


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
  const t = o.ts ? new Date(o.ts).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '--:--:--';
  const el = document.createElement('div');
  el.className = 'ln ' + (o.level||'').toLowerCase();
  el.appendChild(document.createTextNode(
    t+' '+(o.level||'').padEnd(5)+' '+(o.role||'')+' '+(o.event||'')
    + (o.msg ? '  '+o.msg : '')));
  if (o.slug){            // Job-ID als Link zum Schedule-Detail (Stufe 6)
    el.appendChild(document.createTextNode('  '));
    const a = document.createElement('a'); a.className = 'slug';
    a.href = '/-/ui/schedule/' + encodeURIComponent(o.slug);
    a.textContent = 'slug=' + o.slug;
    el.appendChild(a);
  }
  if (o.run_id) el.appendChild(document.createTextNode(' run=' + o.run_id));
  const extra = new Set(['ts','level','role','event','msg','slug','run_id']);
  for (const k in o){ if(!extra.has(k)) el.appendChild(document.createTextNode(' '+k+'='+o[k])); }
  return el;
}
function autoscroll(){ if(!paused) box.scrollTop = box.scrollHeight; }
function rerender(){
  box.innerHTML = '';
  for (const o of buf){ if(passes(o)) box.appendChild(line(o)); }
  autoscroll();
}
function connect(){
  const es = new EventSource('/-/log/stream?n=200');
  es.onmessage = (e) => {
    let o; try { o = JSON.parse(e.data); } catch (_) { return; }
    buf.push(o);
    if (buf.length > 2000) buf.shift();
    if (passes(o)) { box.appendChild(line(o)); autoscroll(); }
  };
  es.onerror = () => { es.close(); setTimeout(connect, 3000); };
}
connect();
box.addEventListener('scroll', () => {
  paused = box.scrollTop + box.clientHeight < box.scrollHeight - 24;
});
lvlSel.onchange = rerender;
q.oninput = rerender;
"""


def log_page(daemon_status: dict | None = None) -> str:
    """Live-Log-Panel (§5.4 Slice C): EventSource gegen ``/-/log/stream``, mit
    Level- + Text-Filter (Rolle/Event/slug/msg). Reines FE; der Daemon liefert
    die Events als SSE. ``pure`` (kein HTTP/DB) — voll testbar. Ops-Handles +
    funktionierendes FOLLOW seit User-Feedback 2026-07-04 (vorher fehlte
    ``_FOLLOW_JS`` hier komplett — der FOLLOW-Button im Header war wirkungslos)."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Live-Log</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Live-Log', daemon_status)}"
        f"<script>{_CLOCK_JS}</script>"
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
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


def _ops_handles(status: dict | None = None) -> str:
    """Ops-Bedienelemente: RESCAN, MAINT-Toggle (spiegelt ``status.maintenance``).
    Ursprünglich Feed-exklusiv, jetzt auch auf Schedules-Liste und Job-Detail
    (User-Feedback 2026-07-03: "brauchen den Rescan und Maintenance Button auf
    Schedule Screen"). FOLLOW sitzt seit einem früheren Follow-up im gemeinsamen
    ``_header()`` (jeder Screen) — hier nicht mehr doppelt. Plain-JS
    (``_OPS_HANDLES_JS``) statt htmx — funktioniert dadurch identisch auf jeder
    Seite, ohne pro Screen ein eigenes hx-target verdrahten zu müssen."""
    maint = bool((status or {}).get("maintenance"))
    mcls = "handle warn" if maint else "handle"
    mlabel = "MAINT: AN" if maint else "MAINT: aus"
    on = bool((status or {}).get("maintenance"))
    hide = "" if on else ' style="display:none"'
    return (
        '<nav class="handles">'
        '<button id="rescan" class="handle">RESCAN</button>'
        f'<button id="maint" class="{mcls}">{mlabel}</button>'
        f'<span id="maintbanner" class="banner bad"{hide}>Wartungsmodus aktiv</span>'
        "</nav>"
    )


#: RESCAN + MAINT als plain-JS-Buttons gegen die JSON-API (§1.1). RESCAN → POST
#: /-/rescan (kurze Quittung). MAINT → POST/DELETE /-/maintenance; der Button **und
#: ein Banner** spiegeln die **echte Server-Antwort** (kein optimistisches Toggle —
#: bei Fehler bleibt der Zustand). FOLLOW besorgt _FOLLOW_JS (window.bibiFollow).
_OPS_HANDLES_JS = """
(function(){
  const rescan = document.getElementById('rescan');
  if (rescan) rescan.addEventListener('click', async () => {
    rescan.disabled = true; rescan.textContent = 'RESCAN…';
    try { await fetch('/-/rescan', {method:'POST'}); } catch(_){}
    rescan.textContent = 'RESCAN ✓';
    setTimeout(() => { rescan.textContent = 'RESCAN'; rescan.disabled = false; }, 1200);
  });
  const maint = document.getElementById('maint');
  const banner = document.getElementById('maintbanner');
  function setMaint(on){
    maint.classList.toggle('warn', on);
    maint.textContent = on ? 'MAINT: AN' : 'MAINT: aus';
    if (banner) banner.style.display = on ? '' : 'none';
  }
  if (maint) maint.addEventListener('click', async () => {
    const on = maint.classList.contains('warn');
    let next = on;
    try {
      const r = await fetch('/-/maintenance', {method: on ? 'DELETE' : 'POST'});
      const d = await r.json(); next = !!d.maintenance;   // echte Server-Antwort
    } catch(_) { next = on; }                              // Fehler → Zustand unverändert
    setMaint(next);
  });
})();
"""



# ── Output-Rendering (§2.5: event-typ-fähig, nicht „alles ist eine Textzeile") ──

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def _merge_deltas(events: list[dict]) -> list[dict]:
    """Token-Deltas (Follow-up PLAN-14) zu ganzen Zeilen zusammenführen — sonst
    zerfällt eine Zeile in viele winzige Timestamp-Fragmente."""
    merged: list[dict] = []
    for e in events:
        if e.get("delta") and merged:
            merged[-1] = {**merged[-1], "line": merged[-1]["line"] + e.get("line", "")}
        else:
            merged.append(dict(e))
    return merged


def _event_line(e: dict) -> str:
    """Eine Output-Zeile: Uhrzeit-Präfix + Status-Klasse (err/thinking). **Eine**
    Formatierung für live *und* archiviert (User-Feedback 2026-07-01: der Output
    eines abgeschlossenen Laufs sah über einen zweiten, Markdown-basierten
    Renderer anders aus als während RUNNING — verwirrend, jetzt vereinheitlicht)."""
    import datetime as _dt
    try:
        ts = _dt.datetime.fromtimestamp(float(e["t"])).strftime("%H:%M:%S")
    except Exception:
        ts = "--:--:--"
    line = _e(_strip_ansi(e.get("line", "")))
    s = e.get("s")
    # "phase" (User-Feedback 2026-07-03): Worker-/Wrapper-Startup-Zeilen (Worktree,
    # Container, Prozess-Spawn) — optisch als System-Info abgesetzt, kein Job-Output.
    if s == "err":
        cls = ' class="err"'
    elif s == "thinking":
        cls = ' class="thinking"'
    elif s == "phase":
        cls = ' class="phase"'
    else:
        cls = ""
    return f'<span class="lts">{ts}</span> <span{cls}>{line}</span>'


def output_block(events: list[dict], kind: str) -> str:
    """Output eines abgeschlossenen Laufs rendern — dieselbe Zeilen-Formatierung
    wie :func:`live_output_box` (Uhrzeit-Präfix, err/thinking-Styling), nur
    eingefroren (kein SSE/JS-Hook). ``kind`` ist nur noch für die Signatur
    relevant (Aufrufer reichen ihn weiterhin durch); die Darstellung selbst
    unterscheidet nicht mehr nach Job-Typ."""
    if not events:
        return '<div class="out-empty">— kein Output —</div>'
    lines = "\n".join(_event_line(e) for e in _merge_deltas(events))
    return f'<pre class="term">{lines}</pre>'


# ── Live-Output (SSE; Frontend-Plan §C.5) ────────────────────────────────────


def live_output_box(job_id: str, events: list[dict] | None = None,
                    *, kind: str = "job") -> str:
    """Eine **streamende** stdout/stderr-Box für einen laufenden Job. Server-seitig
    mit dem aktuellen (bereits formatierten) Output geseedet (no-JS-Paint), per
    ``_LIVE_JS`` ab ``data-from`` weitergestreamt (``/-/job/{id}/output/stream?from=N``
    — formatiert, zählt in denselben Einheiten wie der Seed, kein Offset-Mismatch;
    Follow-up zu PLAN-14). ``hx-preserve`` hält die Box + EventSource über den
    2 s-``#detail``-Poll am Leben."""
    evs = events or []
    seed = "\n".join(_event_line(e) for e in _merge_deltas(evs))
    jid = _e(job_id)
    return (f'<pre class="term liveterm" id="livebox-{jid}" data-job="{jid}" '
            f'data-from="{len(evs)}" hx-preserve="true">{seed}</pre>')


#: Hängt an jede ``.liveterm[data-job]`` eine EventSource (ab ``data-from``), hängt
#: out/err-Zeilen unten an (err rot), Autoscroll am Ende. Erneut nach htmx-Swaps
#: (neue Boxen); ``hx-preserve`` sorgt dafür, dass bestehende Boxen + Streams bleiben
#: (WeakSet verhindert Doppel-Abos). Der Server schließt den Stream bei terminal →
#: ``onerror`` schließt clientseitig (kein Reconnect/Dup).
_LIVE_JS = """
(function(){
  const bound = new WeakSet();
  function attach(){
    document.querySelectorAll('.liveterm[data-job]').forEach(box => {
      if (bound.has(box)) return;
      bound.add(box);
      const id = box.dataset.job, from = box.dataset.from || '0';
      const es = new EventSource('/-/job/'+encodeURIComponent(id)+'/output/stream?from='+from);
      box._bibiEs = es;
      const atBottom = () => box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
      es.onmessage = (e) => {
        if (window.bibiFollow === false) return;
        let o; try { o = JSON.parse(e.data); } catch(_) { return; }
        const stick = atBottom();
        // Token-Delta (Follow-up PLAN-14): an die zuletzt gerenderte Zeile
        // anhängen statt eine neue Timestamp-Zeile zu erzeugen.
        if (o.delta && box._bibiLastSpan) {
          box._bibiLastSpan.textContent += (o.line || '');
          if (stick) box.scrollTop = box.scrollHeight;
          return;
        }
        if (box.childNodes.length) box.appendChild(document.createTextNode('\\n'));
        const tsSpan = document.createElement('span');
        tsSpan.className = 'lts';
        tsSpan.textContent = o.t
          ? new Date(o.t*1000).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'})
          : '--:--:--';
        box.appendChild(tsSpan);
        box.appendChild(document.createTextNode(' '));
        const span = document.createElement('span');
        if (o.s === 'err') span.className = 'err';
        else if (o.s === 'thinking') span.className = 'thinking';
        else if (o.s === 'phase') span.className = 'phase';
        span.textContent = o.line || '';
        box.appendChild(span);
        box._bibiLastSpan = span;
        if (stick) box.scrollTop = box.scrollHeight;
      };
      es.onerror = () => es.close();
    });
  }
  // EventSources schließen bevor HTMX das Element entfernt (verhindert Leak).
  document.addEventListener('htmx:beforeCleanupElement', (ev) => {
    const el = ev.detail && ev.detail.elt ? ev.detail.elt : ev.target;
    if (!el || !el.querySelectorAll) return;
    el.querySelectorAll('.liveterm[data-job]').forEach(box => {
      if (box._bibiEs) { box._bibiEs.close(); box._bibiEs = null; }
    });
    if (el.classList && el.classList.contains('liveterm') && el._bibiEs) {
      el._bibiEs.close(); el._bibiEs = null;
    }
  });
  document.addEventListener('DOMContentLoaded', attach);
  document.addEventListener('htmx:afterSwap', attach);
})();
// Scroll-Erhalt für .liveclamp (awaiting/terminal-Output — kein SSE-Append wie
// .liveterm, das über hx-preserve ohnehin nie neu gerendert wird): der 2s-
// #live-Poll ersetzt bei jedem Tick das ganze outerHTML, ein frisches Element
// hat scrollTop=0 — die Box "springt" sonst sichtbar nach oben, sobald man
// runtergescrollt hat (User-Feedback). Scroll-Position vor dem Swap merken,
// danach am neuen Element wiederherstellen.
(function(){
  let saved = null;
  document.body.addEventListener('htmx:beforeSwap', (ev) => {
    const t = ev.detail && ev.detail.target;
    if (t && t.id === 'live') {
      const box = t.querySelector('.liveclamp');
      saved = box ? box.scrollTop : null;
    }
  });
  document.body.addEventListener('htmx:afterSettle', () => {
    if (saved == null) return;
    const live = document.getElementById('live');
    const box = live && live.querySelector('.liveclamp');
    if (box) box.scrollTop = saved;
    saved = null;
  });
})();
"""

#: User-Feedback 2026-07-03: "wenn ein RUNNING Lauf terminal endet ... wird er
#: erst bei manuellem Reload in der Historie angezeigt". #journal pollt bewusst
#: nicht mit (§6, Infinite Scroll), reagiert also nicht von selbst, wenn ein Lauf
#: OHNE Button-Klick fertig wird. Fingerabdruck ist `data-finished-at` an #live
#: (ändert sich nur bei einem neuen Terminal-Übergang, egal ob complete/error/…);
#: ändert er sich zwischen zwei Polls, wird #journal einmalig auf Seite 1
#: zurückgesetzt — derselbe Effekt wie beim Button-Klick (schedule_action), nur
#: automatisch statt nutzergetriggert.
_JOURNAL_AUTOREFRESH_JS = """
(function(){
  let lastFinished = null;
  function baseline(){
    const live = document.getElementById('live');
    lastFinished = live ? (live.dataset.finishedAt || '') : null;
  }
  document.addEventListener('DOMContentLoaded', baseline);
  document.body.addEventListener('htmx:afterSettle', () => {
    const live = document.getElementById('live');
    if (!live) return;
    const finished = live.dataset.finishedAt || '';
    if (lastFinished === null) { lastFinished = finished; return; }
    if (finished && finished !== lastFinished && window.htmx) {
      const slug = live.dataset.slug;
      if (slug) {
        htmx.ajax('GET', '/-/ui/schedule/' + encodeURIComponent(slug) + '/journal',
                  {target: '#journal', swap: 'outerHTML'});
      }
    }
    lastFinished = finished;
  });
})();
"""


# ── Schedule-Detail (Ebene 3, schedule-zentriert) ────────────────────────────


def _commit_cell(run: dict) -> str:
    sha = run.get("commit_sha")
    if not sha:
        return "—"
    short = _e(sha[:7])
    branch = _e(run.get("branch") or "")
    return f'<span class="commit" title="{_e(sha)} {branch}">{short}</span>'


def _duration_cell(r: dict) -> str:
    rt = r.get("exec_runtime")
    return f"{round(rt)} s" if rt is not None else "—"


#: Läufe pro Infinite-Scroll-Nachladung (User-Entscheidung, Job Lifecycle-Diskussion).
_JOURNAL_PAGE_SIZE = 50


def _journal_sentinel_row(slug: str, offset: int) -> str:
    """Trigger-Zeile für Infinite Scroll: sichtbar (``revealed``) lädt sie die
    nächste Batch nach und ersetzt sich selbst (outerHTML) — mit neuer Batch +
    ggf. frischer Sentinel-Zeile, oder ganz ohne, wenn das Ende erreicht ist."""
    s = _e(slug)
    return (
        f'<tr id="journal-more" hx-get="/-/ui/schedule/{s}/runs?offset={offset}" '
        f'hx-trigger="revealed" hx-swap="outerHTML">'
        f'<td colspan="7" class="muted">lädt weitere Läufe…</td></tr>'
    )


def _journal_table_html(runs: list[dict], slug: str, now: float, *, offset: int = 0) -> str:
    if not runs:
        return '<p class="out-empty">— noch keine Läufe —</p>'
    rows = _run_rows(runs, slug, now)
    if len(runs) == _JOURNAL_PAGE_SIZE:
        rows += _journal_sentinel_row(slug, offset + _JOURNAL_PAGE_SIZE)
    return (
        '<table><thead><tr><th>Zeit</th><th>Status</th><th>Grund</th>'
        '<th>exit</th><th>Dauer</th><th>Commit</th><th></th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def journal_fragment(runs: list[dict], slug: str, now: float, *, oob: bool = False) -> str:
    """Eigenständige, nicht selbst-pollende Region (``#journal``) — wächst nur
    durch nutzergetriggertes Infinite-Scroll-Nachladen (kein 2s-Poll, der die
    nachgeladenen Zeilen sonst wieder plattmachen würde)."""
    oob_attr = ' hx-swap-oob="true"' if oob else ""
    return (
        f'<div id="journal"{oob_attr}>'
        "<h2>Journal</h2>"
        f"{_journal_table_html(runs, slug, now)}"
        "</div>"
    )


def journal_runs_fragment(runs: list[dict], slug: str, now: float, offset: int) -> str:
    """Nächste Batch für ``GET .../runs?offset=N`` — ersetzt die Sentinel-Zeile
    (outerHTML) durch die neuen Zeilen + ggf. eine frische Sentinel-Zeile."""
    rows = _run_rows(runs, slug, now)
    if len(runs) == _JOURNAL_PAGE_SIZE:
        rows += _journal_sentinel_row(slug, offset + _JOURNAL_PAGE_SIZE)
    return rows


def _run_rows(runs: list[dict], slug: str, now: float) -> str:
    # Follow-up (User-Feedback): "Output entfällt" für Journal-Zeilen — kein
    # Inline-Toggle mehr, nur Detail/Löschen. Der Output (formatiert + roh)
    # lebt auf der Execution-Detail-Seite ("→ Detail").
    # Kein relatives "vor Xs/min" hier (User-Feedback): #journal pollt bewusst
    # nicht mit (Infinite Scroll, §6) — ein einmal gerendertes "vor 3s" würde
    # beim nächsten Blick veraltet dastehen. Der absolute Zeitstempel bleibt
    # dagegen für immer korrekt.
    s = _e(slug)
    rows = []
    for r in runs:
        rid = r.get("id")
        st = _e(r.get("status"))
        t_abs = _abs_datetime(r.get("finished_at") or r.get("started_at"), now)
        rows.append(
            "<tr>"
            f"<td>{t_abs}</td>"
            f'<td class="st {st}">{st}</td>'
            f"<td>{_e(r.get('reason'))}</td>"
            f"<td>{_e(r.get('exit_code'))}</td>"
            f"<td>{_duration_cell(r)}</td>"
            f"<td>{_commit_cell(r)}</td>"
            f'<td><a class="back" href="/-/ui/run/{rid}">→ Detail</a> '
            f'<button hx-delete="/-/ui/schedule/{s}/run/{rid}" hx-target="#journal" '
            f'hx-swap="outerHTML" hx-confirm="Lauf-Record löschen?">Löschen</button></td>'
            "</tr>"
        )
    return "".join(rows)


def _live_panel(job: dict | None, now: float, live_output: dict | None = None,
               slug: str = "") -> str:
    """Eigener Block für den **aktuellen** Lauf (aktiv oder zuletzt beendet), nahe
    am Header. Bleibt auch nach einem Terminal-Übergang mit Status+Output stehen
    (User-Feedback 2026-07-01: "archiviert wird erst vor dem nächsten Rerun" —
    die Job-Zeile trägt den letzten Lauf ja weiter fort, bis sie ein neuer Lauf
    überschreibt; das Journal bekommt seine Zeile trotzdem sofort beim Terminal-
    Übergang, s. job_db.py::_write_journal, nur diese Anzeige hier hängt nicht
    mehr daran). Der Output wird **default expanded** mitgerendert (server-seitig,
    überlebt Poll)."""
    if not job:
        return ""
    st = _e(job.get("status"))
    # "failed" bewusst NICHT über _TERMINAL_VIEW (das behandelt live_fragment()s
    # last-run-Vorrang-Logik anders — ein Retry steht dort noch aus, das Journal
    # soll gewinnen). Für DIESE Anzeige (Meta-Zeile + Output-Box) ist "failed"
    # aber genauso ein abgeschlossener letzter Lauf wie "error" — User-Feedback
    # 2026-07-05: Output blieb sonst ausgerechnet vor dem nächsten Retry leer,
    # wenn man am ehesten nachsehen will, was schiefging.
    is_terminal = job.get("status") in _TERMINAL_VIEW or job.get("status") == "failed"
    bits = []
    if is_terminal:
        if job.get("finished_at"):
            bits.append(f"beendet {_ago(job['finished_at'], now)}")
    elif job.get("started_at"):
        bits.append(f"seit {_ago(job['started_at'], now)}")
    if job.get("status") == "pending" and job.get("next_fire_at"):
        bits.append(f"nächster Lauf {_until(job.get('next_fire_at'), now)}")
    if job.get("reason"):
        bits.append(_e(job.get("reason")))
    tail = (" · " + " · ".join(bits)) if bits else ""
    out = ""
    jid = job.get("id")
    if job.get("status") == "running" and jid:
        # Streamende Box (SSE), geseedet mit dem aktuellen Output (Offset → kein Dup).
        events = (live_output or {}).get("events", [])
        # Follow-up (User-Feedback): "Es braucht auch den Zugriff/Ansicht des
        # originalen Streams (/stream)" — die Box zeigt nur noch formatiert.
        raw_link = (f'<span class="muted">'
                    f'<a class="back" href="/-/job/{_e(jid)}/stream">roher Stream →</a>'
                    f'</span>')
        out = (f'<div class="liveout">{raw_link}'
               + live_output_box(jid, events, kind=(live_output or {}).get("kind", "job"))
               + "</div>")
    elif job.get("status") == "awaiting":
        if live_output and live_output.get("events"):
            out = ('<div class="liveout liveclamp">'
                   + output_block(live_output["events"], live_output.get("kind", "job"))
                   + "</div>")
        out += _hitl_panel(job)
    elif is_terminal and live_output and live_output.get("events"):
        out = ('<div class="liveout liveclamp">'
               + output_block(live_output["events"], live_output.get("kind", "job"))
               + "</div>")
    app_port = job.get("app_port") if job else None
    app_link = (f' <a href="http://127.0.0.1:{app_port}/" target="_blank" '
                f'style="font-size:.82rem">Zur App →</a>' if app_port else "")
    label = "letzter Lauf" if is_terminal else "aktiver Lauf"
    return (f'<div class="live"><div class="live-head">'
            f'<span class="st {st}">{st}</span>'
            f'<span class="muted">{label}{tail}</span>{app_link}</div>{out}</div>')


def _hitl_panel(job: dict) -> str:
    """HITL-Panel (§10.4/§10.5): zeigt app_url als direkten Link — FE postet nicht mehr.
    Regulärer Text-Link (Linktext = die URL selbst), kein Button — User-Feedback:
    der Button-Klick schlug fehl, ein normaler Link mit sichtbarer/kopierbarer
    URL ist eindeutiger."""
    app_url = job.get("app_url") or ""
    if app_url:
        link = f'<a href="{_e(app_url)}" target="_blank" rel="noopener">{_e(app_url)}</a>'
    else:
        link = '<span class="muted">app_url nicht verfügbar</span>'
    return f'<div class="hitl"><div class="hitl-label">Eingabe erforderlich</div>{link}</div>'


#: §5.6-Verben, die der Controller als Buttons anbietet (Durchsetzung/Scope: 4.6).
_VERBS = ("start", "reset", "kill")

# Welche Verben sind für welchen Status wirksam? (Job Lifecycle-Referenztabelle,
# §5.4). Alle drei Buttons werden IMMER gerendert (_action_bar) — hier steht nur,
# welche davon `disabled` bleiben. KILL greift auf reiner Lauf-Ebene (pending/
# running/failed/deferred/awaiting — überall, wo gerade etwas aktiv oder
# unmittelbar bevorstehend ist) und bleibt No-op auf allen echten Terminal-
# zuständen inkl. complete (User-Feedback 2026-07-03: KILL vermischte vorher
# Lauf- und Job/Schedule-Semantik). START erzwingt "sofort", RESET respektiert
# den Trigger (siehe job_db.py).
_VERBS_FOR_STATUS: dict[str, tuple[str, ...]] = {
    "pending":  ("start", "kill"),
    "running":  ("kill",),
    "awaiting": ("kill",),
    "failed":   ("start", "kill"),
    "deferred": ("start", "kill"),
    "killed":   ("start", "reset", "kill"),
    "error":    ("start", "reset"),
    "zombie":   ("start", "reset"),
    "inactive": ("start", "reset"),
    "complete": ("start",),
}


def _action_bar(slug: str, job: dict | None) -> str:
    if not job or not job.get("id"):
        return ""
    s = _e(slug)
    status = job.get("status", "")
    enabled = _VERBS_FOR_STATUS.get(status, ())
    btns = "".join(
        f'<button hx-post="/-/ui/schedule/{s}/{v}" hx-target="#live" '
        f'hx-swap="outerHTML"{"" if v in enabled else " disabled"}>{v.upper()}</button> '
        for v in _VERBS
    )
    return f'<div class="actions">{btns}</div>'


def live_fragment(
    schedule: dict | None, runs: list[dict], job: dict | None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None,
) -> str:
    """Der austauschbare Live-Kern (``#live``): Meta + Aktions-Leiste
    (START/RESET/KILL) + Live-Block (aktiver Lauf, Output default expanded).
    Self-pollt alle 2s — bleibt deshalb getrennt vom Journal (``#journal``),
    das sonst durch nachgeladene Infinite-Scroll-Zeilen bei jedem Tick wieder
    plattgemacht würde (Journal Infinite Scroll, §6)."""
    now = time.time() if now is None else now
    s = schedule or {}
    name = _e(s.get("slug") or slug)
    kind = _e(s.get("kind") or (runs[0].get("kind") if runs else ""))
    trigger = _e(s.get("trigger"))
    # schedule_view.last_status gewinnt, wenn er TERMINAL ist — dann ist er das korrekte
    # Lauf-Ergebnis auch wenn der Journal-MAX-Eintrag (Dedup-Skip) veraltet ist.
    # Nicht-terminale Werte (pending, failed, …) bedeuten Re-arm oder Retry → Journal gewinnt.
    sv_last = s.get("last_status")
    if sv_last in _TERMINAL_VIEW:
        last_run = _e(sv_last)
    elif runs:
        last_run = _e(runs[0]["status"])
    else:
        last_run = "—"
    nxt = _until(s.get("next_fire_at"), now)
    meta = (f"Typ <b>{kind}</b> · Trigger <code>{trigger}</code> · "
            f"letzter Lauf <b>{last_run}</b> · nächster Lauf {nxt}")
    # #live self-pollt: awaiting immer (unbedingt), sonst FOLLOW-gated.
    # Wenn awaiting: HITL-Formular darf nie durch bibiFollow=false einfrieren.
    _is_awaiting = job.get("status") == "awaiting" if job else False
    _poll = "every 2s" if _is_awaiting else _POLL
    # data-finished-at: Fingerabdruck des aktuellen/letzten Laufs für
    # _JOURNAL_AUTOREFRESH_JS (User-Feedback 2026-07-03) — ändert er sich
    # zwischen zwei Polls, ist gerade ein Lauf terminal geworden (egal ob
    # complete/error/…) und #journal wird automatisch neu geladen, statt nur
    # bei einem Button-Klick oder vollem Seiten-Reload.
    _finished = _e(job.get("finished_at")) if job and job.get("finished_at") else ""
    attrs = (f'id="live" data-slug="{_e(slug)}" data-finished-at="{_finished}" '
             f'hx-get="/-/ui/schedule/{_e(slug)}/live" '
             f'hx-trigger="{_poll}" hx-swap="outerHTML"')
    return (
        f"<div {attrs}>"
        f"<h1>{name}</h1>"
        f'<div class="meta">{meta}</div>'
        f"{_action_bar(slug, job)}"
        f"{_live_panel(job, now, live_output, slug=slug)}"
        "</div>"
    )


def schedule_detail_inner(
    schedule: dict | None, runs: list[dict], job: dict | None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None,
) -> str:
    """Voller Detail-Kern für den initialen Seitenaufbau: ``#live`` (self-
    pollend) + ``#journal`` (einmalig, wächst nur per Infinite Scroll)."""
    now = time.time() if now is None else now
    return (
        live_fragment(schedule, runs, job, slug, now, live_output=live_output)
        + journal_fragment(runs, slug, now)
    )


def schedule_detail_page(
    schedule: dict | None, runs: list[dict], job: dict | None = None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, daemon_status: dict | None = None,
) -> str:
    """Schedule-zentrierte Detail-Sicht (§3 Ebene 3) als volle Seite. Ops-Handles
    (RESCAN/MAINT) seit User-Feedback 2026-07-03 auch hier — außerhalb von
    ``#live``/``#journal``, damit sie nicht bei jedem 2s-Poll neu gerendert werden."""
    name = _e((schedule or {}).get("slug") or slug)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {name}</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('', daemon_status)}"
        f'<div style="display:flex;gap:.75rem;align-items:baseline">'
        f'<a class="back" href="/-/">← zurück</a>'
        f'<a class="back" href="/-/ui/schedule/{_e(name)}/attrs">Attribute →</a>'
        f'</div>'
        f"{schedule_detail_inner(schedule, runs, job, slug, now, live_output=live_output)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_LIVE_JS}</script>"
        f"<script>{_JOURNAL_AUTOREFRESH_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Execution-Detail (Ebene 4, lauf-zentriert; Frontend-Plan §C.4) ───────────


_TS_FIELDS = {"started_at", "finished_at", "archived_at"}
_ATTR_ORDER = [
    "run_id", "slug", "domain", "reason", "exit_code",
    "host", "worker", "branch", "commit_sha", "output_ref",
    "archived_at",
]
#: kind/status/exec_runtime/started_at/finished_at/schedule_ref stehen bereits
#: breit in _exec_summary() (User-Feedback 2026-07-01: wichtigste Attribute
#: breit statt in der langen vertikalen Tabelle) — hier nicht doppeln. snapshot
#: bekommt eine eigene, geparste Sektion (_run_config_section(), User-Feedback
#: 2026-07-03) statt als abgeschnittener JSON-String in dieser Tabelle zu stehen.
_ATTR_HIDDEN = {"kind", "status", "exec_runtime", "started_at", "finished_at",
                "schedule_ref", "snapshot"}


def _attr_table(e: dict) -> str:
    import datetime as _dt
    rows = []
    seen = set(_ATTR_HIDDEN)
    for key in _ATTR_ORDER:
        if key not in e:
            continue
        seen.add(key)
        val = e[key]
        if val is None:
            continue
        if key in _TS_FIELDS and isinstance(val, (int, float)):
            val = _dt.datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
        elif key == "exec_runtime" and isinstance(val, (int, float)):
            val = f"{val:.1f} s"
        elif key == "commit_sha" and isinstance(val, str) and len(val) > 7:
            branch = _e(e.get("branch") or "")
            val = f"{val[:7]} ({branch})" if branch else val[:7]
        rows.append(f"<tr><td><b>{_e(key)}</b></td><td>{_e(str(val))}</td></tr>")
    # Restliche Felder die nicht in _ATTR_ORDER stehen
    for key, val in sorted(e.items()):
        if key in seen or val is None:
            continue
        rows.append(f"<tr><td><b>{_e(key)}</b></td><td>{_e(str(val))}</td></tr>")
    return (
        '<table class="attrtable">'
        "<thead><tr><th>Attribut</th><th>Wert</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _run_config_section(e: dict) -> str:
    """Die zum Laufzeitpunkt eingefrorene Konfiguration (``journal.snapshot``,
    voll via job_full_view() seit User-Feedback 2026-07-03: "ein Schedule oder
    Attempts kann sich ändern, deshalb müssen alle Werte ... als Attribut am
    Lauf hängen"). Nur für die disponierte Domäne — lokale ``/run``-Läufe (kein
    Schedule) haben nur einen minimalen Snapshot ohne echte Konfig-Felder."""
    import json
    if e.get("domain") != "scheduled":
        return ""
    try:
        snap = json.loads(e.get("snapshot") or "{}")
    except ValueError:
        return ""
    if not snap.get("schedule_ref"):
        return ""
    return _attrs_section("Konfiguration (zu diesem Lauf)", _ATTRS_CONFIG_ORDER, snap)


def _exec_summary(e: dict) -> str:
    """Breite Kompakt-Zeile mit den wichtigsten Attributen: kind · Status ·
    exit · Start → Ende (Dauer) · host · worker · schedule_ref (User-Feedback
    2026-07-01: "eher breit als hoch die wichtigsten Attribute", statt sie nur
    in der langen vertikalen Tabelle zu verstecken)."""
    import datetime as _dt
    parts = []
    if e.get("kind"):
        parts.append(f'<span class="kind">{_e(str(e["kind"]))}</span>')
    st = e.get("status")
    if st:
        parts.append(f'<span class="st {_e(st)}">{_e(st)}</span>')
    ec = e.get("exit_code")
    if ec is not None:
        parts.append(f"exit {ec}")
    rt = e.get("exec_runtime")
    s, f = e.get("started_at"), e.get("finished_at")
    if rt is None and s is not None and f is not None:
        rt = f - s
    if s is not None and f is not None:
        s_str = _dt.datetime.fromtimestamp(s).strftime("%H:%M:%S")
        f_str = _dt.datetime.fromtimestamp(f).strftime("%H:%M:%S")
        dauer = f" (Dauer {round(rt)} s)" if rt is not None else ""
        parts.append(f"{s_str} → {f_str}{dauer}")
    elif rt is not None:
        parts.append(f"Dauer {round(rt)} s")
    if e.get("host"):
        parts.append(f"host {_e(str(e['host']))}")
    if e.get("worker"):
        parts.append(f"worker {_e(str(e['worker']))}")
    if e.get("schedule_ref"):
        parts.append(f'schedule_ref <code>{_e(str(e["schedule_ref"]))}</code>')
    return f'<p class="muted">{"  ·  ".join(parts)}</p>' if parts else ""


def execution_detail_page(entry: dict | None, events: list[dict], kind: str,
                          now: float | None = None,
                          *, daemon_status: dict | None = None) -> str:
    """Ein **Lauf** (``run_id``): alle Journal-Attribute + voller Output."""
    now = time.time() if now is None else now
    e = entry or {}
    run_id = _e(e.get("run_id") or "—")
    slug = _e(e.get("slug") or "")
    st = _e(e.get("status") or "")
    # Breadcrumb statt eigenem "bibi ·"-Header (User-Feedback 2026-07-01: doppeltes
    # "bibi" + verschachtelte Nav) — derselbe Aufbau wie schedule_detail_page().
    back = (f'<a class="back" href="/-/ui/schedule/{slug}">← {slug}</a>'
            if slug else '<a class="back" href="/-/">← zurück</a>')
    out = output_block(events, e.get("kind") or kind)
    jid = e.get("id")
    # Follow-up (User-Feedback): "auch bei archivierten Jobs im Journal eine
    # Möglichkeit, den Original Output zu sehen" — roher Zugriff neben dem
    # formatierten Output (/-/journal/{jid}/out|err|stream, PLAN-14 Stufe 14.0).
    # target=_blank (User-Feedback 2026-07-01): roher Output soll die formatierte
    # Ansicht nicht verdrängen.
    raw_links = (
        f' <span class="muted">roh: '
        f'<a class="back" href="/-/journal/{jid}/out" target="_blank" rel="noopener">out</a> · '
        f'<a class="back" href="/-/journal/{jid}/err" target="_blank" rel="noopener">err</a> · '
        f'<a class="back" href="/-/journal/{jid}/stream" target="_blank" rel="noopener">stream</a></span>'
        if jid is not None else ""
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {run_id}</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f"<style>{_CSS}"
        ".attrtable { width: auto; margin: .75rem 0; font-size: .85rem; }"
        ".attrtable td:first-child { padding-right: 1.5rem; white-space: nowrap; }"
        ".attrtable td { padding: .15rem .3rem; vertical-align: top; }"
        "</style></head><body>"
        f"{_header('', daemon_status)}"
        f'<div style="display:flex;gap:.75rem;align-items:baseline">{back}</div>'
        f'<h1><span class="st {st}">{run_id}</span></h1>'
        f"{_exec_summary(e)}"
        f"{_attr_table(e)}"
        f"{_run_config_section(e)}"
        f"<h2>Output</h2>{raw_links}"
        f'<div class="outscroll">{out}</div>'
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Schedule-Attribute (alle Konfig- + Runtime-Felder; Ebene 3b) ─────────────

_ATTRS_CONFIG_ORDER = [
    "slug", "kind", "payload", "schedule", "at_iso", "priority",
    "model", "soul", "session",
    "attempts", "backoff", "silence_timeout", "wall_time",
    "defer_time", "defer_max",
    "app_port", "app_prefix", "exec_mode", "image",
    "schedule_ref",
]
#: Nur Felder, die wirklich am Job selbst hängen (Scheduler-Bezug) — der Rest
#: (status/reason/attempt/started_at/finished_at/exit_code/host/worker/
#: output_ref/app_url/pid/…) beschreibt den *letzten Lauf*, nicht den Job als
#: Entität, und lebt bereits auf der Job-Detail- bzw. Lauf-Detail-Seite
#: (User-Feedback: "die hängen am Lauf").
_ATTRS_RUNTIME_ORDER = ["id", "next_fire_at", "fire"]
_ATTRS_TS = {"enqueued_at", "started_at", "finished_at", "next_fire_at", "deferred_at"}


def _attrs_section(title: str, keys: list[str], data: dict) -> str:
    import datetime as _dt
    rows = []
    seen: set[str] = set()
    for key in keys:
        seen.add(key)
        val = data.get(key)
        if val is None:
            cell = '<span class="muted">—</span>'
        elif key in _ATTRS_TS and isinstance(val, (int, float)):
            cell = _e(_dt.datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S"))
        elif key == "app_url" and val:
            cell = f'<a href="{_e(val)}" target="_blank" rel="noopener">{_e(val)}</a>'
        else:
            cell = f"<code>{_e(str(val))}</code>"
        rows.append(f"<tr><td><b>{_e(key)}</b></td><td>{cell}</td></tr>")
    return (
        f"<h2>{title}</h2>"
        '<table class="attrtable">'
        "<thead><tr><th>Attribut</th><th>Wert</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def schedule_attrs_page(slug: str, data: dict, now: float | None = None) -> str:
    """Alle Konfig- und Runtime-Felder eines Schedules auf einer eigenen Seite."""
    now = now or time.time()
    name = _e(slug)
    st = _e(data.get("status") or "")
    config_html = _attrs_section("Konfiguration", _ATTRS_CONFIG_ORDER, data)
    runtime_html = _attrs_section("Scheduling", _ATTRS_RUNTIME_ORDER, data)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {name} · Attribute</title>"
        f"<style>{_CSS}"
        ".attrtable { width: auto; margin: .4rem 0 1rem; font-size: .85rem; }"
        ".attrtable td:first-child { padding-right: 1.5rem; white-space: nowrap; }"
        ".attrtable td { padding: .2rem .3rem; vertical-align: top; }"
        ".attrtable code { font-family: ui-monospace, monospace; font-size: .85em; }"
        ".attrtable a { color: inherit; word-break: break-all; }"
        "</style></head><body>"
        f'<div style="display:flex;gap:.75rem;align-items:baseline">'
        f'<a class="back" href="/-/">← zurück</a>'
        f'<a class="back" href="/-/ui/schedule/{name}">← Detail</a>'
        f'</div>'
        f'<h1><span class="st {st}">{name}</span> · Attribute</h1>'
        f"{config_html}"
        f"{runtime_html}"
        "</body></html>"
    )
