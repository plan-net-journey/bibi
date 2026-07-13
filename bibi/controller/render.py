"""HTML-Rendering der Controller-App (PLAN-4 §4.1 ff.) — **pure** Funktionen:
Daten-dict (aus den ``/-/``-JSON-Endpunkten) → HTML. Kein HTTP, kein DB-Zugriff,
damit voll unit-testbar. Look: Terminal/Konsole-nah, minimal (§2.5)."""

from __future__ import annotations

import datetime
import html
import json
import re
import time

from bibi.schedule import models

_HTMX = "https://unpkg.com/htmx.org@1.9.12"

#: Poll-Trigger der self-aktualisierenden Fragmente — 2s, gated durch FOLLOW
#: (``window.bibiFollow``). Zentral, damit das Intervall an einer Stelle hängt.
_POLL = "every 2s [window.bibiFollow]"

#: Eigener, langsamerer Poll fürs Lauf-Historie-Chart (PLAN-21 Befund 11,
#: User-Fund 2026-07-08 "wackelt"): der generische 2s-Takt (für Live-Output/
#: Job-Listen gedacht) hat das Chart bei JEDEM Tick komplett neu instanziiert
#: (Canvas weg, Chart.js neu, Achsenbeschriftung neu berechnet) — sichtbares
#: Flackern, obwohl sich "wie viele Läufe sind terminal gelandet" in der
#: Praxis nicht alle 2s ändert. 20s behebt die Ursache (unnötig häufige
#: Neuerstellung), nicht nur das Symptom.
_CHART_POLL = "every 20s [window.bibiFollow]"

_CSS = """
:root { color-scheme: light dark; }
:root[data-theme="light"] { color-scheme: light; }
:root[data-theme="dark"] { color-scheme: dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem;
       max-width: 64rem; margin-inline: auto; }
header { display: flex; align-items: baseline; justify-content: space-between;
         gap: .75rem; flex-wrap: wrap; }
.nav-left, .nav-right { display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap; }
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
/* Toggles (FOLLOW/THEME/RESCAN/MAINT) wie Nav-Text-Links, keine Buttons mehr
   (PLAN-19 Befund 7, User-Fund: "nicht Buttons und Text Links gemischt") —
   überschreibt das globale button{...} gezielt nur für diese Klasse. */
.toggle { font: inherit; font-size: .85rem; text-decoration: none; color: #888;
          background: none; border: none; padding: 0; cursor: pointer; }
.toggle:hover { text-decoration: underline; }
.toggle.on { color: #5fb37a; }
.toggle.warn { color: #d6a23e; }
.toggle.bad { color: #e06c5a; }
a.slug { font-weight: 600; text-decoration: none; }
a.slug:hover { text-decoration: underline; }
.sched a { text-decoration: none; }
.sched a:hover { text-decoration: underline; }
a.rowlink { color: inherit; text-decoration: none; }
a.rowlink:hover { text-decoration: underline; }
h2 { font-size: .95rem; color: #888; margin: 1.5rem 0 .4rem; font-weight: 600; }
.back { color: #888; text-decoration: none; font-size: .85rem; }
.tab-active { font-weight: 600; border-bottom: 2px solid currentColor; }
.meta { color: #aaa; font-size: .9rem; margin: .2rem 0 1rem; }
/* Terminal-Boxen bleiben theme-unabhängig dunkel (PLAN-19 Befund 3, User-Fund:
   Light-Mode unleserlich) — vorher halbtransparentes Schwarz über dem
   wechselnden Seitenhintergrund, im Light-Mode nur mittelgrau statt dunkel;
   Text ohne eigene Farbe erbte zudem die Body-Textfarbe (im Light-Mode dunkel
   auf jetzt dunklem Grund). Fester Hintergrund + feste helle Standardfarbe. */
.term { background: #1a1a1a; color: #ddd; border: 1px solid #8883; border-radius: .4rem;
        padding: .6rem .8rem; overflow-x: auto; font-family: ui-monospace, monospace;
        font-size: .82rem; line-height: 1.45; white-space: pre-wrap; }
.term .err { color: #e06c5a; }
.term .thinking { color: #888; font-style: italic; }
.term .phase { color: #5a9fe0; font-style: italic; }
.md { font-size: .92rem; }
.md pre { background: #1a1a1a; color: #ddd; border: 1px solid #8883; border-radius: .4rem;
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
.logbox { height: 72vh; overflow-y: auto; background: #1a1a1a; color: #ddd;
          border: 1px solid #8883; border-radius: .4rem; padding: .6rem .8rem;
          font-family: ui-monospace, monospace;
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
.statuscards { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
               gap: .6rem; margin-bottom: 1.2rem; }
.card { border: 1px solid #8883; border-radius: .4rem; padding: .55rem .7rem; }
.card .label { font-size: .72rem; color: #888; text-transform: uppercase; letter-spacing: .03em; }
.card .value { font-size: 1.05rem; font-weight: 600; margin-top: .1rem; }
.card .value.ok { color: #5fb37a; }
.card .value.bad { color: #e06c5a; }
.card .sub { font-size: .75rem; color: #888; margin-top: .15rem; }
/* Mehrzeilige Karten (PLAN-19 Befund 4, User-Entscheidung: Git UND Mode im
   selben 3-Zeilen-Stil, kein Trenner-Punkt mehr) — Host/Mode/Git ersetzen die
   bisherigen 6 Kacheln des Feed-Headers. */
.card .cardline { font-size: 1.05rem; font-weight: 600; margin-top: .1rem; }
.card .cardline.ok, .card a.ok { color: #5fb37a; }
.card .cardline.bad, .card a.bad { color: #e06c5a; }
.card a { text-decoration: none; }
.card a:hover { text-decoration: underline; }
/* Key/Value als echtes 2-Spalten-Grid (PLAN-21 Befund 7, User-Fund: "als
   Grid" mit `| KEY | value |`-Codeblock-Beispiel — löst das PLAN-20-Befund-2-
   Muster ab, bei dem Label+Wert zwar beschriftet, aber nicht spaltenweise
   ausgerichtet waren). Werte einer Karte richten sich jetzt untereinander
   aus (Mode-/Git-Karte); Host/Client bleiben bei `.cardline` (freie Zeile,
   kein Key/Value-Paar). */
.card .kvgrid { display: grid; grid-template-columns: auto 1fr; row-gap: .2rem;
                column-gap: .6em; margin-top: .15rem; }
.card .kvgrid .k { font-size: .72rem; font-weight: 400; color: #888;
                   text-transform: uppercase; letter-spacing: .03em; align-self: center; }
.card .kvgrid .v { font-size: 1.05rem; font-weight: 600; }
.card .kvgrid .v.ok { color: #5fb37a; }
.card .kvgrid .v.bad { color: #e06c5a; }
/* Wie .kvgrid, aber 2 Label/Wert-Paare je Zeile (PLAN-26 Befund 3, User-Fund:
   "Job Status in 2 x 5 Zeilen") — Job-Status-Kachel, sonst identischer Stil. */
.card .kvgrid2 { display: grid; grid-template-columns: auto 1fr auto 1fr; row-gap: .2rem;
                 column-gap: .6em; margin-top: .15rem; }
.card .kvgrid2 .k { font-size: .72rem; font-weight: 400; color: #888;
                    text-transform: uppercase; letter-spacing: .03em; align-self: center; }
.card .kvgrid2 .v { font-size: 1.05rem; font-weight: 600; }
.side-empty { color: #888; font-size: .82rem; }
.chip { font-family: ui-monospace, monospace; font-size: .7rem; font-weight: 700;
        padding: .1rem .45rem; border-radius: .3rem; display: inline-block; white-space: nowrap; }
/* Git-Status je Job-MD (PLAN-21 Befund 10) — löst die vorherige Lokal/Remote-
   Abgleich-Chips (same/diff/local_only/remote_only) ab. */
.chip.clean { background: #5fb37a2e; color: #5fb37a; }
.chip.modified { background: #d6a23e2e; color: #d6a23e; }
.chip.new { background: #5a9fe02e; color: #5a9fe0; }
.startbtn { font: inherit; font-size: .78rem; background: #5a9fe033; border: 1px solid #5a9fe066;
        border-radius: .35rem; padding: .2rem .55rem; cursor: pointer; color: inherit; font-weight: 600;
        white-space: nowrap; }
.killbtn { font: inherit; font-size: .78rem; background: #e06c5a33; border: 1px solid #e06c5a66;
        border-radius: .35rem; padding: .2rem .55rem; cursor: pointer; color: inherit; font-weight: 600;
        white-space: nowrap; }
.startbtn:disabled, .killbtn:disabled { opacity: .4; cursor: default; }
.runhist { font-size: .86rem; }
.runhist .row { display: flex; gap: .8rem; padding: .35rem 0; border-bottom: 1px solid #8881;
                align-items: baseline; }
.runhist a.row:hover { background: #8881; }
.runhist .t { color: #888; font-family: ui-monospace, monospace; font-size: .78rem; flex: 0 0 4.4rem; }
.gitsegment { font-family: ui-monospace, monospace; font-size: .95rem; }
.gitsegment .sep { color: #888; }
.tree-clean, .sync-synced { color: #5fb37a; }
.tree-modified, .sync-ahead { color: #d6a23e; }
.sync-behind, .sync-conflict { color: #e06c5a; }
.filterbar { display: flex; gap: .8rem; align-items: center; flex-wrap: wrap;
             margin: .3rem 0 .8rem; font-size: .85rem; }
.filterbar select { font: inherit; padding: .2rem .45rem; color: inherit;
          background: #8881; border: 1px solid #8884; border-radius: .3rem; }
.filterbar label.chk { display: flex; align-items: center; gap: .35rem; cursor: pointer; }
/* Volle Breite, dynamisch (PLAN-19 Befund 5, User-Fund: Heatmap zog vorher
   nur eine feste Pixelbreite, viel Leerraum daneben) — Tag-Gruppen UND
   Zellen wachsen jetzt per flex mit der verfügbaren Breite statt fester px. */
.heatmap-wrap { padding-bottom: .3rem; }
.heatmap2 { display: flex; flex-direction: column; gap: 3px; width: 100%; }
.hm2-header, .hm2-subheader, .hm2-row { display: flex; align-items: center; gap: .5rem; }
.hm2-subheader { margin-bottom: .1rem; }
.hm2-wlabel { flex: 0 0 5.2rem; font-size: .72rem; color: #888; text-align: right; }
.hm2-daylabel { font-size: .68rem; color: #888; text-align: center; width: 100%; font-weight: 600; }
.hm2-day-group { display: flex; gap: 2px; padding: 0 .3rem; border-right: 1px solid #8882;
                 flex: 1; min-width: 0; box-sizing: border-box; justify-content: center; }
.hm2-day-group:last-child { border-right: none; }
.hm2-hourtick { flex: 1; font-size: .55rem; color: #888; text-align: center;
                font-family: ui-monospace, monospace; }
.hm-cell { flex: 1; height: 14px; min-width: 4px; border-radius: 2px; background: #8882; }
.hm-cell[data-lvl="1"] { background: #5a9fe044; }
.hm-cell[data-lvl="2"] { background: #5a9fe088; }
.hm-cell[data-lvl="3"] { background: #5a9fe0cc; }
.hm-cell[data-lvl="4"] { background: #5a9fe0; }
/* PLAN-21 Befund 4, User-Fund: die Legende (5 kleine Farbblöcke "wenig →
   viel") zog trotz fester width auf die volle Zeilenbreite — Root Cause: die
   geerbte Basis-Regel .hm-cell{flex:1} gewinnt in einem Flex-Container gegen
   die explizite width. `flex: none` hier überschreibt das gezielt, nur für
   die Legende (die eigentliche Heatmap bleibt bei PLAN-19s voller Breite). */
.heatmap-legend { display: flex; align-items: center; justify-content: flex-end;
                  gap: .3rem; font-size: .75rem; color: #888; margin-top: .35rem; }
.heatmap-legend .hm-cell { flex: none; width: 9px; height: 9px; }
/* Lauf-Historie-Chart (PLAN-21 Befund 11) — eine Karte über die volle Breite
   (Kopf mit Titel+Auflösung, Zustands-Chips, Chart.js-Canvas, s. render.py).
   User-Fund 2026-07-08 (2. Runde, "gefällt mir an Variante C"): kein
   separates Stat-Grid mehr, keine Chart-Legende mehr (die Chips tragen
   dieselben Farben wie die Chart-Segmente und übernehmen die Legenden-
   Funktion), Chart deutlich größer, Karte spannt dieselbe Breite wie die
   Schedule-Tabelle darunter statt eines schmalen 640px-Kastens. */
/* .panel-card: generischer Rahmen, wiederverwendet für Lauf-Historie UND die
   Schedule-Liste (User-Fund 2026-07-08, 5. Runde: "den Rahmen auch um die
   Schedules und die Inaktiven"). */
.panel-card { border: 1px solid #8883; border-radius: .4rem; padding: .7rem 1rem .6rem;
              margin: .5rem 0 1rem; }
/* PLAN-27 Befund 1, User-Fund: "Margins zwischen Chart und Heatmap
   unterschiedlich" — die generische h2-Regel (margin-top 1.5rem, für
   Überschriften ZWISCHEN Sektionen gedacht) addierte sich zusätzlich zum
   .panel-card-Padding oben, während der Chart-Kopf (.ts-head h3) schon bei
   margin:0 saß. Normalisiert Aktivität/Änderungen/Schedules auf denselben
   Stand wie der Chart. */
.panel-card > h2:first-child { margin-top: 0; }
.ts-head { display: flex; align-items: baseline; justify-content: space-between;
           flex-wrap: wrap; gap: .4rem 1rem; }
.ts-head h3 { margin: 0; font-size: .95rem; }
.ts-chips { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: baseline;
            font-family: ui-monospace, monospace; font-size: .8rem; font-weight: 700;
            color: #8886; margin: .35rem 0 .7rem; }  /* Default = gedimmt (running=0) */
.ts-chip.ts-dim { color: #888; font-weight: 400; }
.res-links { display: flex; gap: .9rem; }
.res-link { font-size: .7rem; color: #888; text-decoration: none; cursor: pointer; }
.res-link:hover { color: inherit; }
.res-link.active { color: inherit; text-decoration: underline; font-weight: 600; }
.chart-wrap { height: 148px; }  /* User-Fund 2026-07-08 (4. Runde): 74px war zu klein, doppelt so hoch */
.feedlist { display: flex; flex-direction: column; gap: 0; font-size: .88rem; }
.frow { display: flex; gap: .6rem; align-items: baseline; padding: .38rem 0;
        border-bottom: 1px solid #8881; }
.frow.is-agent { opacity: .55; }
.frow .t { color: #888; font-family: ui-monospace, monospace; font-size: .78rem;
           flex: 0 0 8.5rem; }
.lvl { font-family: ui-monospace, monospace; font-size: .68rem; font-weight: 700;
       padding: .05rem .4rem; border-radius: .25rem; flex: 0 0 auto;
       text-transform: uppercase; letter-spacing: .02em; }
.lvl.case { background: #5fb37a33; color: #5fb37a; }
.lvl.vault { background: #5a9fe033; color: #5a9fe0; }
.lvl.system { background: #d6a23e33; color: #d6a23e; }
.frow .msg { flex: 1; }
.frow .who { color: #888; font-size: .78rem; flex: 0 0 auto; }
.frow a.commit { text-decoration: none; }
.frow a.commit:hover { text-decoration: underline; color: #5a9fe0; }
.loadmore { display: flex; gap: .5rem; margin: .8rem 0; }
"""


def _plural(n: int, sing: str, plur: str) -> str:
    return sing if n == 1 else plur


def _ago(ts: float | None, now: float) -> str:
    if ts is None:
        return "—"
    d = max(0, int(now - ts))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60} min ago"
    if d < 86400:
        return f"{d // 3600} h ago"
    return f"{d // 86400} d ago"


def _until(ts: float | None, now: float) -> str:
    """Zukunfts-Distanz („in …") für „nächster Lauf". ``None`` (kein Trigger
    gesetzt) → „—"; ein gesetzter, aber bereits fälliger/überfälliger
    Zeitstempel → „asap" (PLAN-23 Befund 4 — vorher identisch zu None als
    „—" gerendert, das versteckte u. a. einen zwischenzeitlich wiederbelebten
    oneshot, s. Befund 1, hinter einer unauffälligen Anzeige)."""
    if ts is None:
        return "—"
    if ts <= now:
        return "asap"
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


def _is_archived_oneshot(s: dict) -> bool:
    """Ein abgeschlossener oneshot (`at:`-Einzellauf) gehört ins Archiv, auch
    wenn seine MD noch da ist (PLAN-23 Befund 2) — nicht erneut startbar
    (s. job_db.report_status()s PENDING-Sperre), macht als "aktiv" gelistet
    keinen Sinn mehr. Wiederkehrende Schedules (oneshot=False) bleiben bei
    complete Teil der aktiven Rotation (Lazy Rearm)."""
    return bool(s.get("oneshot")) and s.get("last_status") == "complete"


def _group_schedules(schedules: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Registrierungs-Drei-Gruppen (PLAN-14 Stufe 14.6, orthogonal zum Laufzeit-
    Status): aktiv (MD entdeckt) / archive (MD entfernt ODER abgeschlossener
    oneshot, PLAN-23 Befund 2) / journal (nur Journal-Historie, kein
    jobs-Eintrag mehr). Fehlt der ``active``-Key (ältere Fixtures), gilt der
    Schedule als aktiv."""
    active = [s for s in schedules
              if s.get("active", True) is True and not _is_archived_oneshot(s)]
    archive = [s for s in schedules
              if s.get("active", True) is False or _is_archived_oneshot(s)]
    journaled = [s for s in schedules if s.get("active", True) is None]
    return active, archive, journaled


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
    return ('<table class="sched"><thead><tr><th>Schedule</th><th>Type</th><th>Status</th>'
            f'<th>last / since</th><th>next</th></tr></thead><tbody>{rows}'
            "</tbody></table>")


def _schedule_active_block(schedules: list[dict], now: float) -> str:
    head = f'<h2>Schedules ({len(schedules)})</h2>'
    if not schedules:
        return head + '<p class="out-empty">— no schedules —</p>'
    active, _archive, _journaled = _group_schedules(schedules)
    body = (_sched_table(active, now) if active
            else '<p class="out-empty">— no active schedules —</p>')
    return head + body


def _schedule_archive_block(schedules: list[dict], now: float) -> str:
    if not schedules:
        return ""
    _active, archive, journaled = _group_schedules(schedules)
    body = ""
    if archive:
        body += f'<h3>Archive ({len(archive)})</h3>' + _sched_table(archive, now)
    if journaled:
        body += f'<h3>Journal — history only ({len(journaled)})</h3>' + _sched_table(journaled, now)
    return body


def schedule_list(schedules: list[dict], now: float | None = None) -> str:
    """Die volle Liste, gruppiert nach Registrierungs-Zustand (PLAN-14 Stufe
    14.6, erweitert PLAN-23 Befund 2): Aktiv (MD entdeckt) / Archive (MD
    entfernt ODER abgeschlossener oneshot) / Journal (nur Journal-Historie).
    Flach + immer sichtbar, kein Klapp mehr — überlebt so den 2s-Poll ohne
    Expand-Verlust."""
    now = time.time() if now is None else now
    return _schedule_active_block(schedules, now) + _schedule_archive_block(schedules, now)


def schedules_fragment(schedules: list[dict], now: float | None = None,
                       *, typ: str | None = None, status: str | None = None) -> str:
    """Self-pollender Wrapper um die (bereits gefilterte) Schedule-Liste. Der
    Self-Poll trägt den aktiven Filter in der URL, damit er ihn über den 2s-Tick
    bewahrt. Ziel = ``/-/ui/schedules/list`` (das Fragment; die Seite liegt auf
    ``/-/ui/schedules``). Aktive Liste und Archive/Journal sitzen in je einem
    eigenen ``.panel-card`` (PLAN-25 Befund 6: 3 Rahmen statt 2 — Chart /
    Schedules / Archive), nur der äußere Poll-Container trägt id/hx-Attribute."""
    now = time.time() if now is None else now
    qs = "&".join(f"{k}={v}" for k, v in (("typ", typ), ("status", status))
                  if v and v != "alle")
    url = "/-/ui/schedules/list" + (f"?{qs}" if qs else "")
    attrs = (f'id="schedules" hx-get="{url}" '
            f'hx-trigger="{_POLL}" hx-swap="outerHTML"')
    active_html = f'<div class="panel-card">{_schedule_active_block(schedules, now)}</div>'
    archive_body = _schedule_archive_block(schedules, now)
    archive_html = f'<div class="panel-card">{archive_body}</div>' if archive_body else ""
    return f"<div {attrs}>{active_html}{archive_html}</div>"


# ── Lauf-Historie-Chart (PLAN-21 Befund 11, v2 — User-Redesign 2026-07-08) ───
#
# v1 (Zeitfenster-Overlap über waiting/running/halt, s. git-Historie) wurde
# verworfen: Concurrency-Ansicht mit gemischter y-Achse, die kaum etwas über
# Systemgesundheit aussagte, und Quelle einer eigenen "Wander"-Bug-Klasse.
# v2 zählt stattdessen **Landungen in einem finalen Zustand** je Zeit-Bucket
# ("wie viele Läufe endeten womit in diesem Fenster") — ein reines
# Event-Histogramm (kein Zeitfenster-Overlap, keine Segmente nötig), exakt das
# Muster, das ``journal`` per Konstruktion schon liefert (nur Terminal-
# Übergänge, ``status``+``finished_at``). Die frühere ``transitions``-Tabelle
# wird dafür nicht mehr gebraucht (zurückgebaut, s. job_db.py) — sie war nur
# für Zwischenzustände wie eine mehrstündige awaiting-Phase nötig, die dieses
# Chart bewusst nicht mehr zeigt (dafür bleibt das Stat-Grid/live).

from bibi.schedule.lifecycle import TERMINAL as _TERMINAL_STATUSES

_WAITING_STATUSES = ("pending", "failed", "deferred", "awaiting")
_HALT_STATUSES = ("error", "inactive", "zombie", "killed")

#: Live-Farbe (running) — dieselbe wie die bestehende .st.running-Badge-
#: Konvention (Schedule-Tabelle), damit "läuft" app-weit immer blau ist.
_LIVE_COLOR = "#5a9fe0"
#: Eigener Ton für die vier nicht-terminalen "wartet noch"-Zustände (pending/
#: failed/deferred/awaiting) — die tauchen im Chart nie auf, brauchen also
#: keine Chart-Farbe, nur einen von allen Chart-Tönen unterscheidbaren.
_WAITING_COLOR = "#d6a23e"

#: Anzeige-Reihenfolge + Farbe je Terminal-Status (Chart.js-Datasets, gestapelt,
#: UND Zustands-Chips im Chart-Kopf — dieselbe Farbe an beiden Stellen macht
#: eine separate Legende redundant, User-Fund 2026-07-08). Sechs klar
#: unterscheidbare Töne — vorher teilten sich error/zombie sowie killed/
#: _WAITING_COLOR versehentlich denselben Hex-Wert.
_LANDING_ORDER = ("complete", "error", "zombie", "killed", "inactive")
_LANDING_COLOR = {
    "complete": "#5fb37a",   # grün — Erfolg
    "error": "#e06c5a",      # rot — endgültig gescheitert (Retries erschöpft)
    "zombie": "#e0567f",     # rose — Silence-Timeout, eigener Ton statt Dublette zu error
    "killed": "#e08a3e",     # orange — User-/System-Abbruch, eigener Ton statt Dublette zu waiting
    # grau — Job-STATUS "inactive" (deferred-Frist abgelaufen, s. sweep()),
    # nicht zu verwechseln mit dem gleichnamigen Registrierungs-Flag
    # active=False (MD entfernt) — zwei unabhängige Konzepte, PLAN-23 Befund 2.
    "inactive": "#8888a0",
}

#: Auflösungs-Presets (Bucket-Minuten → Fenster in Stunden) — Bucket-Zahl bleibt
#: dabei über alle Presets ähnlich groß (~96), sonst würde z. B. 1min-Auflösung
#: über 24h zu 1440 kaum noch unterscheidbaren Balken führen.
_RESOLUTION_WINDOWS = {1440: 720, 480: 168, 180: 72, 120: 48, 15: 24, 5: 8, 1: 2}
_RESOLUTION_LABEL = {1440: "24h/1m", 480: "8h/1w", 180: "3h/3d", 120: "2h/2d",
                     15: "15min/24h", 5: "5min/8h", 1: "1min/2h"}
_DEFAULT_RESOLUTION_MINUTES = 15

#: Chart.js UMD-Bundle (CDN, wie htmx per <script>-Tag — kein Build-Step nötig).
_CHARTJS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"


def _landings_buckets(landings: list[dict], *, now: float,
                      bucket_minutes: int = _DEFAULT_RESOLUTION_MINUTES,
                      hours: float | None = None) -> tuple[list[float], dict[str, list[int]]]:
    """``landings`` (``{"status", "finished_at"}``, z. B. aus
    ``job_db.journal_landings()``) → Bucket-Start-Zeitstempel + Zählung je
    Terminal-Status. Reines Event-Histogramm: ein Lauf landet in **genau
    einem** Bucket (dem, der seine ``finished_at`` enthält), keine
    Zeitfenster-Overlap-Logik nötig."""
    hours = _RESOLUTION_WINDOWS.get(bucket_minutes, 24) if hours is None else hours
    bucket_s = bucket_minutes * 60
    n = int(hours * 3600 / bucket_s)
    start = now - n * bucket_s
    counts: dict[str, list[int]] = {s: [0] * n for s in _LANDING_ORDER}
    for row in landings:
        status = row.get("status")
        ts = row.get("finished_at")
        if status not in counts or ts is None:
            continue
        idx = int((ts - start) // bucket_s)
        if 0 <= idx < n:
            counts[status][idx] += 1
    labels = [start + i * bucket_s for i in range(n)]
    return labels, counts


def _landings_chart_html(labels: list[float], counts: dict[str, list[int]],
                         chart_id: str = "landingsChart") -> str:
    """``<canvas>`` + Chart.js-Init-Script (gestapelter Bar-Chart). Wird bei
    jedem Poll-Swap des umgebenden Fragments neu instanziiert (htmx führt
    ``<script>``-Tags in geswapptem Content per Default aus) — dieselbe
    "ganzes Fragment ersetzen"-Konvention wie überall sonst im Code, kein
    Diffing/Update-in-place nötig. Kein eigenes Chart.js-Legend-Plugin mehr
    (User-Fund 2026-07-08): die Zustands-Chips im Chart-Kopf
    (``_current_state_chips``) tragen dieselben Farben und übernehmen die
    Legenden-Funktion — pro Balkensegment zeigt Chart.js' Standard-Tooltip
    beim Hover trotzdem den Status-Namen."""
    if not labels:
        return '<div class="chart-wrap"><p class="out-empty">— noch keine Daten —</p></div>'
    tick_labels = [datetime.datetime.fromtimestamp(t).strftime("%H:%M") for t in labels]
    datasets = [
        {"label": status, "data": counts[status], "backgroundColor": _LANDING_COLOR[status]}
        for status in _LANDING_ORDER
    ]
    payload = json.dumps({"labels": tick_labels, "datasets": datasets})
    return (
        f'<div class="chart-wrap"><canvas id="{chart_id}"></canvas></div>'
        "<script>(function(){"
        f"const d={payload};"
        f'const el=document.getElementById("{chart_id}");'
        "if(!el)return;"
        "new Chart(el,{type:'bar',data:d,options:{"
        "responsive:true,maintainAspectRatio:false,animation:false,"
        "scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,ticks:{precision:0}}},"
        "plugins:{legend:{display:false}}"
        "}});"
        "})();</script>"
    )


def _current_state_chips(counts: dict[str, int], running_since_uptime: int) -> str:
    """Kompakte Inline-Zeile im Chart-Kopf statt eines eigenen Stat-Grids
    (User-Fund 2026-07-08, Variante C: "kein separates Stat-Grid mehr, nur
    Inline-Zahlen neben dem Titel"). Zeigt nur, was gerade tatsächlich
    passiert — Nullen werden gar nicht erst als Chip gerendert (nicht mal
    gedimmt) statt eine Wand aus Nullen zu zeigen. Jeder Chip trägt dieselbe
    Farbe wie sein Pendant im Chart (``_LANDING_COLOR``) — das lehrt die
    Farb-Bedeutung nebenbei, eine separate Chart-Legende wird dadurch
    redundant (User-Fund: "mit der richtigen Farbgebung können wir die
    Legende weglassen")."""
    running = counts.get("running", 0)
    style = f' style="color:{_LIVE_COLOR}"' if running else ""
    chips = [f'<span class="ts-chip"{style}>{running} running</span>']
    for status in _HALT_STATUSES:  # error/inactive/zombie/killed — Chart-Farben
        n = counts.get(status, 0)
        if n:
            color = _LANDING_COLOR.get(status, _WAITING_COLOR)
            chips.append(f'<span class="ts-chip" style="color:{color}">{n} {status}</span>')
    for status in _WAITING_STATUSES:  # pending/failed/deferred/awaiting — eigener Ton
        n = counts.get(status, 0)
        if n:
            chips.append(f'<span class="ts-chip" style="color:{_WAITING_COLOR}">{n} {status}</span>')
    chips.append(f'<span class="ts-chip ts-dim">{running_since_uptime} since start</span>')
    return f'<div class="ts-chips">{"".join(chips)}</div>'


def _resolution_links(bucket_minutes: int) -> str:
    """Auflösungs-Wahl als kleine Link-Zeile (User-Fund 2026-07-08: "statt
    Drop-down einfach Links, klein, mit dem aktuellen Zeitfenster
    unterstrichen") statt Dropdown — dieselbe hx-get/Ziel-Idee wie zuvor,
    andere Optik."""
    links = "".join(
        f'<a class="res-link{" active" if m == bucket_minutes else ""}" '
        f'hx-get="/-/ui/schedules/timeseries?res={m}" '
        f'hx-target="#timeseries" hx-swap="outerHTML">{_RESOLUTION_LABEL[m]}</a>'
        for m in _RESOLUTION_WINDOWS
    )
    return f'<div class="res-links">{links}</div>'


def timeseries_fragment(landings: list[dict], job_stats: dict | None = None,
                        now: float | None = None, *,
                        bucket_minutes: int = _DEFAULT_RESOLUTION_MINUTES) -> str:
    """Self-pollender Wrapper um Chart-Kopf (Titel + Auflösung) + Zustands-
    Chips + Landungs-Histogramm, in einer Karte über die volle Breite (User-
    Fund 2026-07-08, Variante C: "vereinigt", kein separates Stat-Grid, keine
    Chart-Legende mehr, Chart deutlich größer). Ziel =
    ``/-/ui/schedules/timeseries`` — eigener Poll, getrennt von der Schedule-
    Liste (``schedules_fragment``): andere Datenquelle (``journal_landings``/
    ``job_stats`` statt ``/-/schedule``), eigener (langsamerer) Takt
    ``_CHART_POLL`` statt ``_POLL`` (s. dort — das "wackelt"-Fund 2026-07-08).
    Der Self-Poll trägt die aktuelle Auflösung in der URL, damit sie den Tick
    überlebt (dieselbe Idee wie ``schedules_fragment``s Filter-Querystring)."""
    now = time.time() if now is None else now
    job_stats = job_stats or {}
    counts = job_stats.get("counts") or {}
    running_since_uptime = job_stats.get("running_since_uptime", 0)
    labels, bucket_counts = _landings_buckets(landings, now=now, bucket_minutes=bucket_minutes)
    body = (
        f'<div class="ts-head"><h3>Run History</h3>{_resolution_links(bucket_minutes)}</div>'
        + _current_state_chips(counts, running_since_uptime)
        + _landings_chart_html(labels, bucket_counts)
    )
    url = f"/-/ui/schedules/timeseries?res={bucket_minutes}"
    attrs = (f'id="timeseries" class="panel-card" hx-get="{url}" '
            f'hx-trigger="{_CHART_POLL}" hx-swap="outerHTML"')
    return f"<div {attrs}>{body}</div>"


# ── Schedules-Screen mit Filter (Frontend-Plan §C.3) ─────────────────────────

#: Filter-Optionen. „problem" ist eine **Gruppe** (Abweichungen als Filter statt
#: eigenem Block): failed/error/killed/zombie + überfällig (pending, fällig verpasst).
_SCHED_TYPES = ("job", "claude")
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
    return models.effective_kind(s.get("payload"))


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


def _cookie_resolution_value(cookie: str | None) -> int | None:
    """Persistenter Auflösungs-Wert aus Cookie (dieselbe Systematik wie
    ``_cookie_filter_value()`` für typ/status, User-Fund: "warum wird die
    Auflösung ... nicht gespeichert?") — nur übernehmen, wenn er noch zu
    einem der aktuell gültigen Presets gehört."""
    if not cookie:
        return None
    try:
        value = int(cookie)
    except ValueError:
        return None
    return value if value in _RESOLUTION_WINDOWS else None


def _cookie_filter_value(cookie: str | None, valid: tuple[str, ...]) -> str | None:
    """Persistenter Filter-Wert aus Cookie (User-Fund: "die ausgewählte
    Auswahl in /-/ui/schedules sollte erhalten bleiben"). Nur übernehmen,
    wenn der Wert noch zu den aktuell gültigen Optionen gehört (oder der
    ``alle``-Sentinel ist) — schützt gegen veraltete Cookies nach
    Options-Änderungen (z. B. den entfernten ``app``-Typ, PLAN-25 Befund 7)."""
    if cookie and (cookie == "alle" or cookie in valid):
        return cookie
    return None


def _filter_bar(typ: str | None, status: str | None) -> str:
    def _opts(values: tuple, cur: str | None) -> str:
        cur = cur or "alle"
        # value bleibt "alle" (interner Sentinel, s. filter_schedules()), nur
        # der sichtbare Text ist englisch (User-Fund 2026-07-08, 5. Runde).
        parts = [f'<option value="alle"{" selected" if cur == "alle" else ""}>all</option>']
        for v in values:
            parts.append(f'<option value="{v}"{" selected" if cur == v else ""}>{v}</option>')
        return "".join(parts)

    common = ('hx-get="/-/ui/schedules/list" hx-target="#schedules" hx-swap="outerHTML" '
              'hx-include="[name=\'typ\'],[name=\'status\']"')
    return (
        '<div class="logbar">'
        f'<label>Type <select name="typ" {common}>{_opts(_SCHED_TYPES, typ)}</select></label>'
        f'<label>Status <select name="status" {common}>{_opts(_SCHED_STATUSES, status)}</select></label>'
        '</div>'
    )


def _screen_nav(active: str, roles: list[str] | None = None) -> str:
    """Screen-Tabs (Feed · [Schedules] · [Jobs] · Live-Log · API-Docs); der
    aktive ohne Link. **Home ist jetzt Feed** (PLAN-18 Stufe 18.3, löst die
    2026-07-04-Entscheidung „Home = Schedules" bewusst ab) — Schedules bleibt
    unter seiner eigenen Route erreichbar, ist nur nicht mehr ``/-/`` selbst.
    Jobs (PLAN-17 Stufe 17.2) zeigt den Lokal/Remote-Abgleich + Start-Button für
    /run. Daemon-Tab entfernt (PLAN-18 Stufe 18.4) — sein Inhalt (Status-
    Kacheln) lebt jetzt im Feed-Header, ``daemon_page()``/``_status_cards()``
    bleiben als Bausteine bestehen, nur die eigene Seite/der Tab fallen weg.

    Schedules/Jobs sind rollenabhängig ausgeblendet (PLAN-20 Befund 6):
    Schedules nur mit ``scheduler``-Rolle (die zugrundeliegenden ``/-/schedule``-
    Routen existieren serverseitig nur dann, s. ``app.py::_add_scheduler_routes``
    — ohne Rolle wäre die Seite ohnehin nur ein 404). Jobs nur mit ``connect``-
    Rolle (User-Entscheidung trotz Rückfrage: bewusst NICHT zusätzlich für
    reine Scheduler-Knoten wie sarasate — auch wenn der Screen dort technisch
    funktionieren würde)."""
    roles = roles or []
    tabs = [("Feed", "/-/")]
    if "scheduler" in roles:
        tabs.append(("Schedules", "/-/ui/schedules"))
    if "connect" in roles:
        tabs.append(("Jobs", "/-/ui/jobs"))
    tabs += [("Live Log", "/-/ui/logs"), ("API Docs", "/-/docs")]
    def _tab(t: str, h: str) -> str:
        if t == active:
            return f'<span class="tab-active">{t}</span>'
        extra = ' target="_blank" rel="noopener"' if t == "API Docs" else ""
        return f'<a class="back" href="{h}"{extra}>{t}</a>'
    items = [_tab(t, h) for t, h in tabs]
    return '<span class="muted">' + " · ".join(items) + "</span>"


def _live_clock() -> str:
    """Tickende Lebendigkeits-Anzeige (Feedback Z. 2) — von ``_CLOCK_JS`` gesetzt.
    Rechts-Gruppe der Nav (PLAN-21 Befund 1, User-Fund: "Datum, Uhrzeit, Theme
    hätte ich gerne abgegrenzt rechts ausgerichtet") — zeigt seither Datum +
    Uhrzeit statt nur Uhrzeit."""
    return '<span class="liveclock" id="liveclock">● live --.--.---- --:--:--</span>'


#: Setzt die Uhr sekündlich (rein client-seitig) — „wir leben noch".
_CLOCK_JS = """
(function(){
  const c = document.getElementById('liveclock');
  if (!c) return;
  const tick = () => {
    const now = new Date();
    c.textContent = '● live ' + now.toLocaleDateString('en-GB') + ' ' + now.toLocaleTimeString('en-GB');
  };
  tick(); setInterval(tick, 1000);
})();
"""


def _follow_toggle() -> str:
    """FOLLOW-Button (pausiert Live-Updates, ``window.bibiFollow``) — Teil der
    linken Nav-Gruppe (PLAN-21 Befund 1: klickbar wie bisher, optisch wie ein
    Tab neben Feed/Schedules/…). Als Text-Link gestylt, kein Button-Look mehr
    (PLAN-19 Befund 7)."""
    return '<button id="follow" class="toggle on" onclick="bibiToggleFollow()">FOLLOW: ON</button>'


def _theme_toggle() -> str:
    """DARK/LIGHT-Button — Teil der rechten Nav-Gruppe (PLAN-21 Befund 1,
    User-Fund: "Theme als Symbol LIGHT/DARK" statt Textlabel). Startsymbol per
    ``_THEME_JS`` gesetzt (Default = System-Präferenz), damit hier kein
    Server-seitiger Theme-State nötig ist. Als Text-Link gestylt, kein
    Button-Look (PLAN-19 Befund 7)."""
    return '<button id="theme" class="toggle" onclick="bibiToggleTheme()">☾</button>'


#: DARK/LIGHT-Toggle: überschreibt ``color-scheme`` explizit via ``data-theme``
#: auf <html> (s. _CSS), Default = System-Präferenz (``prefers-color-scheme``),
#: persistiert in localStorage — analog zu _FOLLOW_JS. Symbol statt Text
#: (PLAN-21 Befund 1): ☀ (hell → Klick wechselt zu dunkel) / ☾ (dunkel →
#: Klick wechselt zu hell), zeigt also das jeweils erreichbare Ziel-Theme.
_THEME_JS = """
(function(){
  const KEY = 'bibiTheme';
  const root = document.documentElement;
  function apply(theme){
    root.setAttribute('data-theme', theme);
    const b = document.getElementById('theme');
    if (b) b.textContent = theme === 'dark' ? '☀' : '☾';
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
    """Gemeinsame obere Navigationsleiste: links Titel + Tab-Leiste + FOLLOW +
    Ops-Handles (RESCAN/MAINT), rechts Datum/Uhrzeit + THEME (PLAN-21 Befund 1,
    User-Fund: "links ausgerichtet: bibi/Feed/…/Follow/Maintenance, rechts
    ausgerichtet: Datum/Uhrzeit/Theme" — löst die bisherige einzeilige
    Linksbündig-Reihe ab). ``git_status`` fällt hier weg (PLAN-21 Befund 2,
    Sync-Dopplung: der Sync-Zustand steht jetzt nur noch in der Git-Karte,
    RESCAN zeigt wieder die generische Beschriftung). Rollen für
    ``_screen_nav()`` (PLAN-20 Befund 6) kommen aus ``status["roles"]`` — schon
    vorhanden (``/-/status``), keine neue Datenquelle nötig."""
    roles = (status or {}).get("roles")
    left = (f'<h1>bibi</h1>{_screen_nav(active, roles)} '
            f'{_follow_toggle()}{_ops_handles(status)}')
    right = f'{_live_clock()}{_theme_toggle()}'
    return (f'<header><div class="nav-left">{left}</div>'
            f'<div class="nav-right">{right}</div></header>')


def schedules_page(schedules: list[dict], typ: str | None = None,
                   status: str | None = None, now: float | None = None,
                   *, daemon_status: dict | None = None,
                   landings: list[dict] | None = None,
                   git_status: dict | None = None, host_url: str | None = None,
                   status_poll_interval_s: int = 30,
                   bucket_minutes: int = _DEFAULT_RESOLUTION_MINUTES) -> str:
    """Der Schedules-Screen: Nav + Ops-Handles (RESCAN/MAINT, User-Feedback
    2026-07-03) + Status-Kacheln (Host/Mode/Git/Job-Status, User-Fund: "diesen
    Header möchte ich auch im /-/ui/schedules haben" — dieselbe
    ``feed_status_fragment()`` wie auf ``/-/``) + Stat-Grid/Landungs-
    Histogramm (PLAN-21 Befund 11) + Filterleiste + (gefilterte) self-
    pollende Liste. ``schedules`` ist bereits gefiltert; ``typ``/``status``
    spiegeln die Auswahl — ``status`` ist hier der Filterwert (z. B.
    "error"), nicht zu verwechseln mit ``daemon_status`` (``/-/status``-JSON
    für den MAINT-Toggle **und** die Stat-Grid-Zählung,
    ``daemon_status["job_stats"]``). ``bucket_minutes`` ist die initiale Chart-
    Auflösung beim ersten Laden (User-Fund: "warum wird die Auflösung ...
    nicht gespeichert?") — der Aufrufer (``controller/__init__.py``) ermittelt
    sie aus Query-Param/Cookie, bevor der Self-Poll die URL selbst trägt."""
    now = time.time() if now is None else now
    daemon_status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Schedules</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f'<script src="{_CHARTJS}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Schedules', daemon_status)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, poll_interval_s=status_poll_interval_s)}"
        f"{timeseries_fragment(landings or [], daemon_status.get('job_stats'), now, bucket_minutes=bucket_minutes)}"
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
  b.textContent = 'FOLLOW: ' + (window.bibiFollow ? 'ON' : 'OFF');
  b.className = 'toggle ' + (window.bibiFollow ? 'on' : '');
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
  if (b && !window.bibiFollow){ b.textContent = 'FOLLOW: OFF'; b.className = 'toggle'; }
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


def _log_panel() -> str:
    """Log-Filterleiste + Box + EventSource-Script — geteilter Baustein zwischen
    ``log_page()`` (Live-Log) und ``daemon_page()`` (PLAN-17 Stufe 17.0: dieselbe
    Quelle, jetzt zusätzlich neben den Status-Kacheln), damit beide Seiten
    dasselbe Verhalten (Filter, FOLLOW-Autoscroll) ohne Duplikat-Pflege haben."""
    return (
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
    )


def log_page(daemon_status: dict | None = None, *, git_status: dict | None = None,
             host_url: str | None = None, now: float | None = None,
             status_poll_interval_s: int = 30) -> str:
    """Live-Log-Panel (§5.4 Slice C): EventSource gegen ``/-/log/stream``, mit
    Level- + Text-Filter (Rolle/Event/slug/msg). Reines FE; der Daemon liefert
    die Events als SSE. Ops-Handles + funktionierendes FOLLOW seit User-Feedback
    2026-07-04 (vorher fehlte ``_FOLLOW_JS`` hier komplett — der FOLLOW-Button
    im Header war wirkungslos). Status-Kacheln (Host/Mode/Git/Job-Status,
    PLAN-27 Befund 2, User-Fund: "diesen Header auch im Live-Log anzeigen")
    seit demselben `feed_status_fragment()` wie auf ``/-/``/``/-/ui/schedules``
    — braucht dafür jetzt auch das htmx-Script-Tag (vorher unnötig, da der
    Log-Stream reines SSE/Plain-JS ist)."""
    now = time.time() if now is None else now
    status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Live-Log</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Live Log', status)}"
        f"<script>{_CLOCK_JS}</script>"
        f"{feed_status_fragment(status, git_status, host_url, now, poll_interval_s=status_poll_interval_s)}"
        f"{_log_panel()}"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


def _uptime_label(started_at: float | None, now: float) -> str:
    """Laufzeit seit Daemon-Start, grobkörnig (Tage/Stunden bzw. Stunden/Minuten)."""
    if started_at is None:
        return "—"
    d = max(0, int(now - started_at))
    days, rem = divmod(d, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} T {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{d}s"


def _card(label: str, value: str, sub: str = "", cls: str = "") -> str:
    cls_attr = f" {cls}" if cls else ""
    sub_html = f'<div class="sub">{_e(sub)}</div>' if sub else ""
    return (f'<div class="card"><div class="label">{_e(label)}</div>'
            f'<div class="value{cls_attr}">{_e(value)}</div>{sub_html}</div>')


def _status_card_list(status: dict, now: float) -> list[str]:
    """Die Rollen/Host-Verbindung/Auto-Sync/Maintenance/Uptime-Kacheln als
    Liste (statt schon im ``.statuscards``-Wrapper) — Baustein von
    ``_status_cards()`` (Daemon-Screen) UND ``_feed_status_cards()`` (PLAN-18
    Stufe 18.3, hängt eine Git-Segment-Kachel dahinter)."""
    cards = [_card("Rollen", ", ".join(status.get("roles") or []) or "—")]

    conn = status.get("connect")
    if conn is not None:
        ok = conn.get("ok")
        if ok is True:
            value, cls = "verbunden", "ok"
        elif ok is False:
            value, cls = "getrennt", "bad"
        else:
            value, cls = "wartet…", ""
        last_at = conn.get("last_at")
        sub = f"Heartbeat {_ago(last_at, now)}" if last_at is not None else ""
        cards.append(_card("Host-Verbindung", value, sub, cls))

    auto_sync = bool(status.get("auto_sync"))
    cards.append(_card("Auto-Sync", "an" if auto_sync else "aus", cls="ok" if auto_sync else ""))

    maint = bool(status.get("maintenance"))
    cards.append(_card("Maintenance", "an" if maint else "aus", cls="bad" if maint else ""))

    cards.append(_card("Uptime", _uptime_label(status.get("started_at"), now)))
    return cards


def _status_cards(status: dict, now: float) -> str:
    """Status-Kacheln (PLAN-17 Stufe 17.0): Rollen, Host-Verbindung (nur wenn
    ``connect`` im Status steckt — s. ``Heartbeat``, PLAN-17-Vorarbeit
    2026-07-05), Auto-Sync, Maintenance, Uptime."""
    return '<div class="statuscards">' + "".join(_status_card_list(status, now)) + "</div>"


_TREE_LABEL_CLASS = {"clean": "tree-clean", "modified": "tree-modified"}
_SYNC_LABEL_CLASS = {"synced": "sync-synced", "ahead": "sync-ahead",
                     "behind": "sync-behind", "conflict": "sync-conflict"}


def _lines_card(label: str, lines: list[str], sub: str = "") -> str:
    """Karte mit mehreren linksbündigen Zeilen statt einem einzelnen Wert
    (PLAN-19 Befund 4) — ``lines`` sind bereits fertiges HTML (Farb-Spans/
    Links), werden hier nicht mehr escaped. Baustein für Host/Mode/Git im
    neuen 3-Karten-Feed-Header."""
    sub_html = f'<div class="sub">{_e(sub)}</div>' if sub else ""
    body = "".join(f'<div class="cardline">{ln}</div>' for ln in lines)
    return f'<div class="card"><div class="label">{_e(label)}</div>{body}{sub_html}</div>'


def _host_card(status: dict, host_url: str | None, now: float) -> str:
    """Host- vs. Client-Karte, unterschieden nach Rolle (PLAN-21 Befund 6,
    revidiert PLAN-20 Befund 4 — User-Fund per Screenshot: der bisherige
    "lokal"-Platzhalter auf dem Host selbst war nicht gewollt, "beim Host
    anzeigen: Host > Hostname"; auf einem Client soll die Karte "Client"
    heißen statt "Host", Rendering (Hostname-Link + Heartbeat) unverändert).

    ``status["connect"]`` fehlt genau dann, wenn dieser Knoten keine
    connect-Rolle aktiv hat (``app.py``: nur gesetzt ``if heartbeat is not
    None``) — das ist die Host-Rolle, zeigt jetzt ``status["hostname"]``
    (eigener ``socket.gethostname()``, PLAN-21 neu im ``/-/status``-Payload).
    Mit ``connect`` ist es die Client-Rolle: Hostname aus der Scheduler-URL
    abgeleitet (dieselbe, die auch der Jobs-Screen-Hostlink nutzt,
    ``_scheduler_url()``, keine neue Datenquelle), grün wenn verbunden,
    verlinkt zum Host-eigenen `/-/`."""
    conn = status.get("connect")
    if conn is None:
        own = status.get("hostname")
        return _lines_card("Host", [_e(own)] if own else ["—"])
    hostname = None
    if host_url:
        from urllib.parse import urlparse
        hostname = urlparse(host_url).hostname
    if hostname is None:
        return _lines_card("Client", ["—"])
    ok = conn.get("ok")
    cls = "ok" if ok else ("bad" if ok is False else "")
    href = _e(host_url.rstrip("/") + "/-/")
    link = f'<a class="{cls}" href="{href}" target="_blank" rel="noopener">{_e(hostname)}</a>'
    sub = ""
    if conn.get("last_at") is not None:
        sub = f"Heartbeat {_ago(conn['last_at'], now)}"
    return _lines_card("Client", [link], sub=sub)


def _kv_card(label: str, rows: list[tuple[str, str, str]], sub: str = "") -> str:
    """Karte mit Key/Value-Zeilen als echtes 2-Spalten-Grid (PLAN-21 Befund 7)
    — anders als ``_lines_card()``s freien Zeilen richten sich die Werte
    mehrerer Zeilen hier untereinander aus. ``rows``: ``(key, value,
    value_css_class)``, ``value`` wird escaped (immer Klartext bei Mode/Git,
    kein HTML nötig)."""
    sub_html = f'<div class="sub">{_e(sub)}</div>' if sub else ""
    body = "".join(
        f'<div class="k">{_e(k)}</div><div class="v {cls}">{_e(v)}</div>'
        for k, v, cls in rows
    )
    return (f'<div class="card"><div class="label">{_e(label)}</div>'
            f'<div class="kvgrid">{body}</div>{sub_html}</div>')


def _mode_card(status: dict, now: float) -> str:
    """MODE-Kachel: Auto-Sync + Maintenance als Key/Value-Grid + Uptime als
    Sub-Zeile (PLAN-19 Befund 4, verfeinert PLAN-21 Befund 7: Grid-Optik statt
    gestapelter Label/Wert-Zeilen, User-Fund mit Codeblock-Beispiel)."""
    auto_sync = bool(status.get("auto_sync"))
    maint = bool(status.get("maintenance"))
    rows = [
        ("Auto-Sync", "an" if auto_sync else "aus", "ok" if auto_sync else ""),
        ("Maintenance", "an" if maint else "aus", "bad" if maint else ""),
    ]
    return _kv_card("Mode", rows,
                    sub=f"Uptime {_uptime_label(status.get('started_at'), now)}")


def _format_sync_value(sync: str, oid: str | None, ahead: int, behind: int) -> str:
    """SYNC-Zeilenwert inkl. Commit-Hash (PLAN-25 Befund 8-Nachtrag, User-Fund:
    "Release-Stand — Commit-Hash und Anzahl commits behind — reporten"),
    Format je Zustand final geklärt: ``synced: <hash>``, ``behind: <hash> (N)``,
    ``ahead: <hash> (N)``, ``conflict: <hash> (+N, -M)`` (``conflict`` heißt
    hier divergiert — ahead UND behind zugleich > 0 — nicht ein unaufgelöster
    Merge-Konflikt mit ``<<<<<<<``-Markern, s. ``git_status.working_tree_status()``).
    Ohne ``oid`` (ältere Aufrufer/Tests) bleibt es beim reinen Zustandswort."""
    if not oid:
        return sync
    short = oid[:7]
    if sync == "behind":
        return f"{sync}: {short} ({behind})"
    if sync == "ahead":
        return f"{sync}: {short} ({ahead})"
    if sync == "conflict":
        return f"{sync}: {short} (+{ahead}, -{behind})"
    return f"{sync}: {short}"


def _git_segment_card(git_status: dict | None) -> str:
    """Git-Kachel: Tree + Sync als Key/Value-Grid, Branch als Sub-Zeile
    (PLAN-19 Befund 4, verfeinert PLAN-21 Befund 7: Grid-Optik statt
    gestapelter Zeilen). ``git_status`` ist bereits ein Dict (``{"tree",
    "sync", "branch", "oid", "ahead", "behind"}``, aus
    ``bibi.git_status.working_tree_status()`` — rein lokal, kein Heartbeat/
    Netzwerk nötig). ``None`` (kein Git-Repo) → leere Kachel mit „—"."""
    if git_status is None:
        return _lines_card("Git", ["—"])
    tree, sync = git_status["tree"], git_status["sync"]
    sync_value = _format_sync_value(
        sync, git_status.get("oid"), git_status.get("ahead", 0), git_status.get("behind", 0))
    rows = [
        ("Tree", tree, _TREE_LABEL_CLASS[tree]),
        ("Sync", sync_value, _SYNC_LABEL_CLASS[sync]),
    ]
    branch = git_status.get("branch")
    return _kv_card("Git", rows, sub=f"Branch {branch}" if branch else "")


_JOB_STATUS_WAITING = ("pending", "deferred", "failed")
_JOB_STATUS_RUNNING = ("running", "awaiting")
_JOB_STATUS_STOPPED = ("inactive", "zombie", "error", "killed")


def _job_status_card(job_stats: dict, now: float) -> str:
    """Job-Status-Kachel (PLAN-26 Befund 3) — 4. Kachel neben Host/Mode/Git,
    nur gerendert wenn ``job_stats`` vorhanden ist (``scheduler``-Rolle, wie
    ``job_stats`` selbst — Client-Darstellung laut User bewusst "später").

    2 Zeilen (User-Fund direkt nach dem ersten Deploy: das ursprüngliche 2×5-
    Layout war zu hoch, hier auf dieselbe Höhe wie Host/Mode/Git verdichtet):
    Waiting (pending+deferred+failed) / Stopped (inactive+zombie+error+killed)
    in Zeile 1, Running (running+awaiting) / Complete in Zeile 2. ``complete``
    kommt NICHT aus ``counts`` (Live-Zählung aktiver Jobs — sinkt, sobald
    abgeschlossene Jobs archiviert werden), sondern aus dem kumulativen
    ``complete_since_uptime`` (``job_db.complete_count()``, analog
    ``running_since_uptime``). Sub-Zeile (kleinere Schrift, wie Mode-Karten
    "Uptime …"/Git-Karten "Branch …"): kleinster ``next_fire_at`` über alle
    aktiven Jobs (``job_db.next_due_at()``), über ``_until()`` formatiert."""
    counts = job_stats.get("counts") or {}
    waiting = sum(counts.get(s, 0) for s in _JOB_STATUS_WAITING)
    running = sum(counts.get(s, 0) for s in _JOB_STATUS_RUNNING)
    stopped = sum(counts.get(s, 0) for s in _JOB_STATUS_STOPPED)
    complete = job_stats.get("complete_since_uptime", 0)
    cells = (
        f'<div class="k">Waiting</div><div class="v">{waiting}</div>'
        f'<div class="k">Stopped</div><div class="v">{stopped}</div>'
        f'<div class="k">Running</div><div class="v">{running}</div>'
        f'<div class="k">Complete</div><div class="v">{complete}</div>'
    )
    sub = f"Nächster Job {_until(job_stats.get('next_due_at'), now)}"
    return (f'<div class="card"><div class="label">Job Status</div>'
           f'<div class="kvgrid2">{cells}</div>'
           f'<div class="sub">{_e(sub)}</div></div>')


def feed_status_fragment(
    status: dict, git_status: dict | None, host_url: str | None, now: float,
    *, poll_interval_s: int = 30,
) -> str:
    """Die Feed-Header-Kacheln (PLAN-19 Befund 4: Host-Connection, Mode,
    Git — löst die bisherigen 6 Kacheln von PLAN-18 Stufe 18.3 ab, u. a. fällt
    die Rollen-Kachel weg, deckungsgleich mit der ursprünglichen Umbau-Vorgabe
    „Rollen sind eh klar"). Baut **nicht** mehr auf ``_status_card_list()``
    auf (die bleibt unverändert für ``_status_cards()``/``daemon_page()`` als
    Baustein bestehen, auch ohne eigene Route seit PLAN-18 Stufe 18.4).

    Optionale 4. Kachel seit PLAN-26 Befund 3: ``_job_status_card()``, nur
    wenn ``status["job_stats"]`` vorhanden ist (``scheduler``-Rolle).

    Self-pollend seit PLAN-25 Befund 4 (User-Fund: "Header kontinuierlich
    aktualisieren") — vorher nur beim initialen Seitenaufbau gerendert.
    Bewusst **kein** festes 2s-Intervall wie ``#schedules``: die Karten
    hängen an ``/-/status`` (DB-Query bei Scheduler-Rolle) und einem
    ``git status``-Subprozess (Git-Karte) — beides nicht billig genug für
    Sekundentakt. ``poll_interval_s`` kommt vom Aufrufer (Default 30s,
    konfigurierbar über ``config.status_poll_interval()``/
    ``BIBI_STATUS_POLL_INTERVAL``), damit diese Funktion config-frei bleibt."""
    cards = [_host_card(status, host_url, now), _mode_card(status, now),
             _git_segment_card(git_status)]
    job_stats = status.get("job_stats")
    if job_stats is not None:
        cards.append(_job_status_card(job_stats, now))
    attrs = (f'id="feedstatus" hx-get="/-/ui/feed/status" '
            f'hx-trigger="every {poll_interval_s}s [window.bibiFollow]" hx-swap="outerHTML"')
    return f'<div {attrs}><div class="statuscards">{"".join(cards)}</div></div>'


def daemon_page(daemon_status: dict | None = None, now: float | None = None) -> str:
    """Daemon-Screen (PLAN-17 Stufe 17.0): Status-Kacheln + dasselbe Live-Log wie
    ``log_page()`` (geteilter ``_log_panel()``-Baustein) — additiv neben
    Live-Log, ersetzt es nicht (kein Migrationsrisiko für bestehende Links)."""
    now = time.time() if now is None else now
    status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Daemon</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Daemon', status)}"
        f"<script>{_CLOCK_JS}</script>"
        f"{_status_cards(status, now)}"
        f"{_log_panel()}"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Jobs-Screen (PLAN-17 Stufe 17.1/17.2, PLAN-21 Befund 10) ─────────────────

#: Git-Status je lokaler Job-MD (PLAN-21 Befund 10, User-Fund: "die Jobs im
#: Repository plus ihr git Status (neu, geändert, etc.) anzeigen"; gelöschte
#: MDs brauchen keinen eigenen Status — sie verschwinden von selbst aus der
#: Liste, da discovery.discover() sie nicht mehr findet).
_GIT_STATUS_LABEL = {
    "new": ("chip new", "neu"),
    "modified": ("chip modified", "geändert"),
    "clean": ("chip clean", "unverändert"),
}


def _jobs_row(row: dict, local_runs: dict[str, dict], now: float) -> str:
    """Eine Zeile: Slug, Git-Status, letzter Start/Ende/Laufzeit, Status
    (PLAN-21 Befund 10 — löst die vorherige Lokal/Remote/Abgleich-Zeile ab,
    kein Remote-Bezug mehr; PLAN-28 User-Feedback: kein Start-CTA mehr hier
    — Start gibt es nur noch auf der Detailseite, diese Übersicht dient
    reinem Review). Slug verlinkt auf die lokale Job-Detailseite; Status
    verlinkt auf den konkreten letzten Lauf (/-/ui/run/{jid}), sofern schon
    mal gelaufen. ``row["live"]`` (PLAN-21 Befund 10, 2. Nachtrag): läuft der
    Job gerade, geht der Status-Link auf die Detailseite (dort lebt der
    Live-Output), Start/Ende/Laufzeit zeigen den laufenden Versuch (Ende
    "—", Laufzeit bis ``now``) statt des letzten ABGESCHLOSSENEN Laufs."""
    slug = row["slug"]
    s = _e(slug)
    live = row.get("live")
    lr = local_runs.get(slug)
    jid = lr.get("id") if lr else None

    slug_cell = f'<a class="slug" href="/-/ui/jobs/detail/{s}">{s}</a>'
    if live:
        # PLAN-27 Befund 4, User-Fund: "der Status awaiting wird in /ui/jobs
        # nicht angezeigt" — live["status"] kommt jetzt aus local_runs_live()
        # (worker.py), analog zu _local_job_meta()s Fallunterscheidung.
        st = "awaiting" if live.get("status") == "awaiting" else "running"
        status_cell = (f'<a class="rowlink" href="/-/ui/jobs/detail/{s}">'
                       f'<span class="st {st}">{st}</span></a>')
        started_cell = _abs_time(live.get("started_at"))
        ended_cell = "—"
        started_at = live.get("started_at")
        runtime_cell = f"{round(now - started_at)} s" if started_at is not None else "—"
    elif jid is not None:
        status_cell = (f'<a class="rowlink" href="/-/ui/run/{jid}">'
                       f'<span class="st {_e(lr["status"])}">{_e(lr["status"])}</span></a>')
        started_cell = _abs_time(lr.get("started_at"))
        ended_cell = _abs_time(lr.get("finished_at"))
        runtime_cell = _duration_cell(lr)
    else:
        status_cell = '<span class="side-empty">noch nie lokal gelaufen</span>'
        started_cell = ended_cell = runtime_cell = "—"

    cls, label = _GIT_STATUS_LABEL.get(row.get("git_status", "clean"),
                                       ("chip", _e(str(row.get("git_status", "—")))))
    git_cell = f'<span class="{cls}">{label}</span>'

    return (f"<tr><td>{slug_cell}</td><td>{git_cell}</td>"
            f"<td>{started_cell}</td><td>{ended_cell}</td><td>{runtime_cell}</td>"
            f"<td>{status_cell}</td></tr>")


def _jobs_table(rows: list[dict], local_runs: dict[str, dict], now: float) -> str:
    if not rows:
        return '<p class="out-empty">— keine Job-MDs im Repository gefunden —</p>'
    body = "".join(_jobs_row(r, local_runs, now) for r in rows)
    return (
        '<table><thead><tr><th>Slug</th><th>Git</th><th>Letzter Start</th>'
        '<th>Letztes Ende</th><th>Laufzeit</th><th>Status</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _run_hist_row(r: dict, now: float) -> str:
    t = _abs_time(r.get("finished_at"))
    status = r.get("status", "")
    exit_code = r.get("exit_code")
    exit_txt = f" · exit {exit_code}" if exit_code is not None else ""
    dur = _duration_cell(r)
    dur_txt = f" · {dur}" if dur != "—" else ""
    body = (f'<span class="t">{_e(t)}</span>'
           f'<span class="st {_e(status)}">{_e(status)}</span>'
           f'<span>{_e(r.get("slug"))}{exit_txt}{dur_txt}</span>')
    jid = r.get("id")
    # PLAN-21 Befund 10: jeder lokale Lauf verlinkt jetzt auf seine eigene
    # Detail-Seite (/-/ui/run/{jid}, rollenunabhängig dank Fallback-Route).
    if jid is not None:
        return f'<a class="row rowlink" href="/-/ui/run/{jid}">{body}</a>'
    return f'<div class="row">{body}</div>'


def _run_history(runs: list[dict], now: float) -> str:
    if not runs:
        return '<p class="out-empty">— noch keine lokalen Läufe —</p>'
    return '<div class="runhist">' + "".join(_run_hist_row(r, now) for r in runs) + "</div>"


def jobs_fragment(
    rows: list[dict], local_runs: dict[str, dict], runs: list[dict],
    *, now: float | None = None,
) -> str:
    """Der austauschbare Jobs-Kern (``#jobsboard``): lokale Job-MDs + Git-
    Status + letzter Start/Ende/Laufzeit je Zeile + lokale Lauf-Historie
    (PLAN-21 Befund 10 — löst die vorherige Lokal/Remote-Abgleich-Tabelle ab,
    kein Netzaufruf/Remote-Bezug mehr, dient ausschließlich dem Review der
    lokalen Repository-Realität; PLAN-28 User-Feedback: kein Start-CTA mehr
    hier, Start gibt es nur noch auf der Job-Detailseite). Self-pollt wie die
    anderen Screens (PLAN-17 Stufe 17.2), damit ein anderswo (Detailseite,
    CLI) gestarteter Lauf ohne Warten sichtbar wird."""
    now = time.time() if now is None else now
    return (
        f'<div id="jobsboard" hx-get="/-/ui/jobs/board" hx-trigger="{_POLL}" hx-swap="outerHTML">'
        '<h2>Jobs im Repository</h2>'
        f"{_jobs_table(rows, local_runs, now)}"
        '<h2>Lokale Läufe</h2>'
        f"{_run_history(runs, now)}"
        "</div>"
    )


def jobs_page(
    rows: list[dict], local_runs: dict[str, dict], runs: list[dict],
    *, daemon_status: dict | None = None, git_status: dict | None = None,
    host_url: str | None = None, status_poll_interval_s: int = 30,
    now: float | None = None,
) -> str:
    """Jobs-Screen (PLAN-17 Stufe 17.2, umgebaut PLAN-21 Befund 10): lokale
    Repository-Realität + Git-Status + letzter Start/Ende/Laufzeit je Zeile +
    lokale Lauf-Historie. Rein lokal — funktioniert auch auf einem reinen
    Client (kein Scheduler/Worker im Ruhezustand), ohne je den Scheduler zu
    kontaktieren. Status-Kacheln (Host/Mode/Git/Job-Status) seit demselben
    ``feed_status_fragment()`` wie ``/-/``/``/-/ui/schedules``/Live-Log
    (PLAN-28 User-Feedback: "Der Header soll auch auf der Client Job Seite
    angezeigt werden" — PLAN-27 Befund 2 hatte das nur fürs Live-Log erledigt)."""
    now = time.time() if now is None else now
    status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Jobs</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Jobs', status)}"
        f"<script>{_CLOCK_JS}</script>"
        f"{feed_status_fragment(status, git_status, host_url, now, poll_interval_s=status_poll_interval_s)}"
        f"{jobs_fragment(rows, local_runs, runs, now=now)}"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Lokale Job-Detailseite (PLAN-21 Befund 10-Nachtrag) ──────────────────────
#
# User-Fund 2026-07-09: der Jobs-Screen (Client) verlinkte bisher direkt von
# der Liste auf den letzten EINZELNEN Lauf, ohne Zwischenseite "dieser Job,
# alle lokalen Läufe" — anders als der Host, wo /-/ui/schedule/{slug} genau
# das leistet. Diese Seite ist das Gegenstück, aber bewusst kein 1:1-Clone
# von schedule_detail_page(): die Meta-Zeile speist sich aus der lokalen MD-
# Discovery (_local_schedules()) statt der Scheduler-DB (die kennt ein reiner
# Client nicht), keine Start/Reset/Kill-Verben (Scheduler-Konzepte — nur
# "Start" bleibt, als erneuter /run). Die Journal-Tabelle (Historie,
# Löschen, Detail-Link) ist dagegen **derselbe** Baustein wie beim Host
# (journal_fragment() mit base="/-/ui/jobs/detail", s. dort) — genau die vom
# User erhoffte Vereinheitlichung, ohne Fork.


def _local_job_meta(slug: str, local: dict, last_run: dict | None,
                    *, live: dict | None = None) -> str:
    """Meta-Zeile der lokalen Job-Detailseite — Gegenstück zur Meta-Zeile in
    live_fragment() (Host), aber aus der lokalen MD-Discovery statt der
    Scheduler-DB gespeist (s. Modul-Kommentar oben). ``live`` (PLAN-21 Befund
    10, 2. Nachtrag — User-Fund 2026-07-09 "warum erscheinen keine Details
    während des Laufes?"): läuft der Job gerade, zeigt sie "running" statt
    des letzten (bereits abgeschlossenen) Laufs, und der Start-Button
    deaktiviert sich (der Server würde einen Doppelstart ohnehin mit 409
    ablehnen, s. app.py — das hier ist nur die sichtbare Konsequenz davon)."""
    kind = _e(_effective_sched_type(local))
    trigger = _e(local.get("schedule") or local.get("at") or "—")
    cls, git_label = _GIT_STATUS_LABEL.get(local.get("git_status", "clean"),
                                           ("chip", _e(str(local.get("git_status", "—")))))
    if live:
        # Ausbau User-Fund 2026-07-10: lokale App-Jobs melden jetzt auch
        # awaiting über den Signal-Kanal (s. worker.local_run_signal_state()) —
        # vorher stand hier für jeden laufenden lokalen Job unbedingt "running".
        st = "awaiting" if live.get("status") == "awaiting" else "running"
        status_html = f' · <span class="st {st}">{st}</span>'
    elif last_run:
        st = _e(last_run.get("status"))
        status_html = f' · letzter Lauf <span class="st {st}">{st}</span>'
    else:
        status_html = ""
    s = _e(slug)
    disabled = " disabled" if live else ""
    start_btn = (f'<button class="startbtn" hx-post="/-/ui/jobs/detail/{s}/start" '
                f'hx-target="#jobsdetail-live" hx-swap="outerHTML"{disabled} '
                f'title="/run {s} sofort auf diesem Rechner">▶ Start</button>')
    # KILL nur sichtbar/aktiv, solange wirklich etwas läuft (User-Fund
    # 2026-07-10: "natürlich müssen wir kill können" — ein langlebiger
    # App-Job über /run blieb sonst nur per manuellem docker kill/SIGTERM
    # von außen beendbar).
    kill_disabled = "" if live else " disabled"
    kill_btn = (f'<button class="killbtn" hx-post="/-/ui/jobs/detail/{s}/kill" '
               f'hx-target="#jobsdetail-live" hx-swap="outerHTML"{kill_disabled} '
               f'title="laufenden Prozess beenden">■ Kill</button>')
    # RESET (User-Feedback 2026-07-13: "warum nicht START, RESET und KILL wie
    # auf Host") — Not-Aus für eine hängen gebliebene Live-Anzeige (Wrapper
    # abgestürzt/Daemon neu gestartet, kein greifbarer Prozess mehr, aber die
    # Zeile steht noch auf running/awaiting und START bleibt deaktiviert).
    # Nur sichtbar/aktiv, solange überhaupt eine Live-Zeile existiert — sonst
    # gibt es nichts zurückzusetzen, START funktioniert bereits jederzeit.
    reset_disabled = "" if live else " disabled"
    reset_btn = (f'<button class="resetbtn" hx-post="/-/ui/jobs/detail/{s}/reset" '
                f'hx-target="#jobsdetail-live" hx-swap="outerHTML"{reset_disabled} '
                f'title="hängen gebliebenen Live-Status aufräumen">↺ Reset</button>')
    # REBUILD (User-Fund 2026-07-13: "REBUILD müsste doch auch beim Client
    # notwendig sein, oder?") — wie beim Host (_action_bar()) nur sichtbar bei
    # exec_mode: container, unabhängig vom Live-/Journal-Status (verwirft nur
    # das per-Job-Image, betrifft keinen laufenden Prozess).
    rebuild_btn = ""
    if (local.get("exec_mode") or "host").strip().lower() == "container":
        rebuild_btn = (f' <button class="rebuildbtn" hx-post="/-/ui/jobs/detail/{s}/rebuild" '
                      f'hx-target="#jobsdetail-live" hx-swap="outerHTML" '
                      f'title="Verwirft das per-Job-Image, nächster Lauf startet vom '
                      f'Default-Image">REBUILD</button>')
    return (
        f'<p class="muted">Typ <b>{kind}</b> · '
        f'Trigger <code>{trigger}</code> · Git <span class="{cls}">{git_label}</span>'
        f"{status_html}</p>{start_btn} {reset_btn} {kill_btn}{rebuild_btn}"
    )


def _local_live_output(live: dict | None, last_run_output: dict | None = None) -> str:
    """Live-Output-Panel (PLAN-21 Befund 10, 2. Nachtrag) — dieselbe
    Zeilen-Formatierung wie die abgeschlossene Ansicht (output_block(), Host-
    Execution-Detail), nur alle _POLL-Sekunden aus /-/run/live/{slug}
    nachgelesen statt eingefroren. Kein SSE (bewusst): der 2s-Poll, den die
    ganze Seite ohnehin schon nutzt, reicht — kein eigener Streaming-Pfad
    nötig für ein Feature, das "sichtbar während des Laufs" leisten soll,
    nicht "Zeichen-für-Zeichen-Latenz".

    ``last_run_output`` (PLAN-28 User-Feedback: "bei terminalen Status wurde
    der Output entfernt... beim Host wird der Output des letzten Laufes immer
    oben angezeigt bis RESET oder START"): ist gerade nichts live, aber ein
    letzter Lauf bekannt, zeigt dieselbe Formatierung dessen archivierten
    Output (``/-/run/journal/{id}/output``) — Analogon zu ``_live_panel()``
    (Host), das den letzten Lauf ebenfalls bis RESET/START stehen lässt."""
    if not live:
        if not last_run_output:
            return ""
        out = output_block(last_run_output.get("events", []), last_run_output.get("kind", "job"))
        return f'<h3>Output</h3><div class="outscroll">{out}</div>'
    out = output_block(live.get("events", []), live.get("kind", "job"))
    # Ausbau User-Fund 2026-07-10: dasselbe HITL-Panel wie bei Scheduler-Jobs
    # (_hitl_panel() nimmt jeden Dict mit app_url — live hat die Form seit dem
    # Signal-Kanal-Ausbau in worker.local_run_signal_state()).
    panel = _hitl_panel(live) if live.get("status") == "awaiting" else ""
    return f'<h3>Output</h3><div class="outscroll">{out}</div>{panel}'


#: Erkennt den running→(nicht mehr live)-Übergang auf der lokalen Job-
#: Detailseite und lädt #journal dann automatisch nach (PLAN-21 Befund 10,
#: 2. Nachtrag) — Analogon zu _JOURNAL_AUTOREFRESH_JS (Host), aber gegen
#: data-running statt data-finished-at, weil "läuft gerade?" hier ein
#: Boolean aus der In-Memory-Registry ist, keine Zeitstempel-Differenz.
_JOBS_LIVE_AUTOREFRESH_JS = """
(function(){
  let wasRunning = null;
  function el(){ return document.getElementById('jobsdetail-live'); }
  function baseline(){
    const e = el();
    wasRunning = e ? e.dataset.running === '1' : null;
  }
  document.addEventListener('DOMContentLoaded', baseline);
  document.body.addEventListener('htmx:afterSettle', () => {
    const e = el();
    if (!e) return;
    const running = e.dataset.running === '1';
    if (wasRunning === null) { wasRunning = running; return; }
    if (wasRunning && !running && window.htmx) {
      htmx.ajax('GET', e.dataset.journalUrl, {target: '#journal', swap: 'outerHTML'});
    }
    wasRunning = running;
  });
})();
"""


def jobs_detail_live_fragment(slug: str, live: dict | None, local: dict | None,
                              last_run: dict | None, *,
                              last_run_output: dict | None = None) -> str:
    """Self-pollende Region (``#jobsdetail-live``): Meta-Zeile + Output.
    Ziel = ``/-/ui/jobs/detail/{slug}/live``. ``last_run_output`` (PLAN-28
    User-Feedback): Fallback auf den archivierten Output des letzten Laufs,
    solange nichts live ist — s. ``_local_live_output()``."""
    s = _e(slug)
    running_flag = "1" if live else "0"
    journal_url = f"/-/ui/jobs/detail/{s}/journal"
    body = (_local_job_meta(slug, local or {}, last_run, live=live)
           + _local_live_output(live, last_run_output))
    attrs = (f'id="jobsdetail-live" data-running="{running_flag}" '
            f'data-journal-url="{journal_url}" '
            f'hx-get="/-/ui/jobs/detail/{s}/live" hx-trigger="{_POLL}" hx-swap="outerHTML"')
    return f"<div {attrs}>{body}</div>"


def jobs_detail_inner(slug: str, local: dict, last_run: dict | None,
                      runs: list[dict], now: float | None = None,
                      *, live: dict | None = None,
                      last_run_output: dict | None = None) -> str:
    now = time.time() if now is None else now
    return (
        jobs_detail_live_fragment(slug, live, local, last_run, last_run_output=last_run_output)
        + journal_fragment(runs, slug, now, base="/-/ui/jobs/detail")
    )


def jobs_detail_page(slug: str, local: dict | None, last_run: dict | None,
                     runs: list[dict], now: float | None = None,
                     *, daemon_status: dict | None = None, live: dict | None = None,
                     last_run_output: dict | None = None) -> str:
    """Lokale Job-Detailseite (ein Slug, nur lokale /run-Läufe dieses Knotens)
    — Gegenstück zu schedule_detail_page() auf dem Host, s. Modul-Kommentar."""
    now = time.time() if now is None else now
    local = local or {}
    s = _e(slug)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {s}</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('', daemon_status)}"
        f'<div style="display:flex;gap:.75rem;align-items:baseline">'
        f'<a class="back" href="/-/ui/jobs">← Jobs</a></div>'
        f'<h1>{s}</h1>'
        f"{jobs_detail_inner(slug, local, last_run, runs, now, live=live, last_run_output=last_run_output)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_LIVE_AUTOREFRESH_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Feed-Screen (PLAN-18 Stufe 18.3) — jetzt Home (``/-/``) ──────────────────

_HM_DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _heatmap_level(count: int) -> int:
    """Rohe Commit-Zahl → Farbstufe 0-4 (feste Schwellen, kein Vorgänger-
    Precedent — reicht für die erste Umsetzung, ohne Bedarf gemessen)."""
    if count <= 0:
        return 0
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    if count <= 10:
        return 3
    return 4


def _heatmap_col_labels(now: float) -> list[str]:
    """Spalten-Beschriftung relativ zu heute (PLAN-19 Befund 5, User-
    Entscheidung: rollierendes Fenster statt Mo-So-Kalenderwoche, aber
    weiterhin Wochentagsnamen statt Datum) — Spalte 6 (letzte) ist immer der
    heutige Wochentag, Spalte 0 der Wochentag sechs Tage davor."""
    import datetime
    today_weekday = datetime.datetime.fromtimestamp(now).weekday()
    return [_HM_DAYS[(today_weekday + c - 6) % 7] for c in range(7)]


def _heatmap_row_labels(now: float, weeks: int) -> list[str]:
    """Datum des Wochenstarts je Zeile (PLAN-21 Befund 5, User-Fund: "Datum
    des Wochenstarts anzeigen, also die erste in den Tagesspalten" statt
    relativer "vor N Wochen"-Angabe) — Spalte 0 jeder Zeile ist der älteste
    Tag ihrer rollierenden 7-Tage-Gruppe, exakt dieselbe Formel wie
    ``heatmap_buckets()``/``_heatmap_col_labels()`` (kein neues Datum nötig,
    nur anders formatiert)."""
    import datetime
    today = datetime.datetime.fromtimestamp(now).date()
    return [(today - datetime.timedelta(days=week_idx * 7 + 6)).strftime("%d.%m.")
            for week_idx in range(weeks)]


def _heatmap_html(grid: list[list[list[int]]], now: float | None = None) -> str:
    """5×7×8-Grid (``bibi.feed.heatmap_buckets()``) → dasselbe DOM-Layout wie
    das im Browser verifizierte Wireframe (``wireframes/feed.html``), hier
    serverseitig aus echten Zählungen statt Zufallswerten gerendert. Spalten
    sind seit PLAN-19 Befund 5 rollierend (letzte Spalte = heute) — die
    Wochentag-Labels wandern deshalb mit statt fix Mo-So zu sein."""
    now = time.time() if now is None else now
    col_labels = _heatmap_col_labels(now)
    row_labels = _heatmap_row_labels(now, len(grid))
    header = ('<div class="hm2-header"><span class="hm2-wlabel"></span>' + "".join(
        f'<div class="hm2-day-group"><span class="hm2-daylabel">{d}</span></div>'
        for d in col_labels) + "</div>")
    ticks = "".join(
        f'<span class="hm2-hourtick">{b * 3:02d}</span>' if b % 2 == 0
        else '<span class="hm2-hourtick"></span>'
        for b in range(8))
    subheader = ('<div class="hm2-subheader"><span class="hm2-wlabel"></span>'
                + f'<div class="hm2-day-group">{ticks}</div>' * len(col_labels) + "</div>")

    rows = []
    for week_idx, week in enumerate(grid):
        label = row_labels[week_idx]
        groups = []
        for col_idx, day in enumerate(week):
            cells = "".join(
                f'<span class="hm-cell" data-lvl="{_heatmap_level(n)}" '
                f'title="{_e(label)} · {col_labels[col_idx]} {b * 3:02d}–{b * 3 + 3:02d} Uhr '
                f'— {n} Änderung(en)"></span>'
                for b, n in enumerate(day))
            groups.append(f'<div class="hm2-day-group">{cells}</div>')
        rows.append(f'<div class="hm2-row"><span class="hm2-wlabel">{_e(label)}</span>'
                    f'{"".join(groups)}</div>')

    legend = ('<div class="heatmap-legend"><span>wenig</span>'
             + "".join(f'<span class="hm-cell" data-lvl="{i}"></span>' for i in range(5))
             + "<span>viel</span></div>")
    return ('<h2>Aktivität</h2>'
           f'<div class="heatmap-wrap"><div class="heatmap2">{header}{subheader}'
           f'{"".join(rows)}</div></div>{legend}')


def _feed_commit_cell(sha: str | None, commit_base_url: str | None) -> str:
    if not sha:
        return ""
    short = _e(sha[:7])
    if commit_base_url:
        href = _e(f"{commit_base_url}/commit/{sha}")
        return f'<a class="commit" href="{href}" target="_blank" rel="noopener">{short}</a>'
    return f'<span class="commit">{short}</span>'


def _feed_row(e: dict, now: float, *, commit_base_url: str | None = None) -> str:
    kind, name = e["kind"], e["name"]
    is_agent = bool(e.get("all_agent"))
    cls = "frow is-agent" if is_agent else "frow"
    # PLAN-19 Befund 6, User-Fund: absolute Zeit statt "vor 4 h" (schon
    # verfügbar, dieselbe Funktion wie die Journal-Liste andernorts nutzt).
    t = _abs_datetime(e.get("last_changed"), now)
    authors = ", ".join(e.get("authors") or []) or "—"
    commit = _feed_commit_cell(e.get("last_commit_sha"), commit_base_url)
    return (f'<div class="{cls}" data-kind="{_e(kind)}" data-agent="{"1" if is_agent else "0"}">'
           f'<span class="t">{_e(t)}</span>'
           f'<span class="lvl {_e(kind)}">{_e(kind)}</span>'
           f'<span class="msg">{_e(name)}</span>'
           f"{commit}"
           f'<span class="who">{_e(authors)}</span>'
           "</div>")


_FEED_FILTER_JS = """
function bibiApplyFeedFilters(){
  const kind = document.getElementById('feedkind').value;
  const agent = document.getElementById('feedagent').value;
  document.querySelectorAll('#feedlist .frow').forEach(row => {
    const matchKind = kind === 'alle' || row.dataset.kind === kind;
    const matchAgent = agent === 'alle'
      || (agent === 'agents' && row.dataset.agent === '1')
      || (agent === 'team' && row.dataset.agent === '0');
    row.style.display = (matchKind && matchAgent) ? '' : 'none';
  });
}
"""


def _feed_filter_bar() -> str:
    # 3-State statt Checkbox (User-Fund 2026-07-07: "Agents ausblenden" war
    # nur binär — Alle/Nur Agents/Nur Team im selben Dropdown-Stil wie "Ebene").
    return (
        '<div class="filterbar">'
        '<label>Ebene <select id="feedkind" onchange="bibiApplyFeedFilters()">'
        '<option value="alle">alle</option><option value="case">case</option>'
        '<option value="vault">vault</option><option value="system">system</option>'
        "</select></label>"
        '<label>Wer <select id="feedagent" onchange="bibiApplyFeedFilters()">'
        '<option value="alle">alle</option><option value="agents">nur Agents</option>'
        '<option value="team">nur Team</option>'
        "</select></label>"
        "</div>"
    )


def _feed_list(entities: list[dict], now: float, *, commit_base_url: str | None = None) -> str:
    if not entities:
        return '<p class="out-empty">— keine Änderungen in diesem Zeitraum —</p>'
    return '<div class="feedlist" id="feedlist">' + "".join(
        _feed_row(e, now, commit_base_url=commit_base_url) for e in entities) + "</div>"


def _feed_board_url(days: int | None, weeks: int | None) -> str:
    parts = []
    if days is not None:
        parts.append(f"days={days}")
    if weeks is not None:
        parts.append(f"weeks={weeks}")
    return "/-/ui/feed/board" + ("?" + "&".join(parts) if parts else "")


def feed_fragment(feed_data: dict, *, days: int | None = None, weeks: int | None = None,
                  now: float | None = None) -> str:
    """Der austauschbare Feed-Kern (``#feedboard``): Filterleiste + Heatmap +
    aggregierte Änderungsliste + je ein „mehr laden" für Liste (Tage) und
    Heatmap (Wochen) — **entkoppelt** (PLAN-20 Befund 3, User-Fund: „Heatmap
    immer um eine Woche nachladen"; PLAN-18 Design-Pass: einfacher wachsender
    Zähler statt fester Tier-Liste). Jeder Button hält das jeweils andere
    Fenster über ``_feed_board_url()`` konstant, damit ein Klick nicht das
    zuvor schon nachgeladene Fenster der anderen Komponente zurücksetzt."""
    now = time.time() if now is None else now
    entities = feed_data.get("entities") or []
    grid = feed_data.get("heatmap") or []
    commit_base_url = feed_data.get("commit_base_url")
    # Aktuelles Wochen-Fenster (für beide Buttons konstant zu halten): explizit
    # übergeben, sonst aus der schon abgerufenen Grid-Länge abgeleitet.
    cur_weeks = weeks if weeks is not None else (len(grid) if grid else None)
    if days is None:
        # Schon "gesamte Historie" — nichts mehr zu laden. days=0 ist das
        # explizite Sentinel dafür (siehe __init__.py::_effective_days);
        # ohne dieses Signal wäre "kein days-Query-Param" nicht von einem
        # frischen Seitenaufruf (Default 1 Tag) unterscheidbar.
        load_more = ""
    else:
        next_days = days + 1
        url = _feed_board_url(next_days, cur_weeks)
        load_more = (
            f'<div class="loadmore">'
            f'<button hx-get="{url}" hx-target="#feedboard" '
            f'hx-swap="outerHTML">mehr laden ({next_days} Tage)</button>'
            f"</div>"
        )
    heatmap_load_more = ""
    if grid:
        next_weeks = cur_weeks + 1
        hm_url = _feed_board_url(days, next_weeks)
        heatmap_load_more = (
            f'<div class="loadmore">'
            f'<button hx-get="{hm_url}" hx-target="#feedboard" '
            f'hx-swap="outerHTML">mehr laden ({next_weeks} Wochen)</button>'
            f"</div>"
        )
    return (
        '<div id="feedboard">'
        f'<div class="panel-card">{_heatmap_html(grid, now)}</div>'
        f"{heatmap_load_more}"
        '<div class="panel-card">'
        '<h2>Änderungen</h2>'
        f"{_feed_filter_bar()}"
        f"{_feed_list(entities, now, commit_base_url=commit_base_url)}"
        '</div>'
        f"{load_more}"
        f"<script>{_FEED_FILTER_JS}</script>"
        "</div>"
    )


def feed_page(
    feed_data: dict, *, git_status: dict | None = None, host_url: str | None = None,
    days: int | None = None, weeks: int | None = None,
    daemon_status: dict | None = None, now: float | None = None,
    status_poll_interval_s: int = 30,
) -> str:
    """Feed-Screen — jetzt Home (``/-/``): fixierte Status-Kacheln (Host/Mode/
    Git, PLAN-19 Befund 4) + Heatmap + aggregierte Änderungsliste. Kein
    Daemon-Log hier (User-Entscheidung, PLAN-18 Rückmeldung 11)."""
    now = time.time() if now is None else now
    status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Feed</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Feed', status)}"
        f"<script>{_CLOCK_JS}</script>"
        f"{feed_status_fragment(status, git_status, host_url, now, poll_interval_s=status_poll_interval_s)}"
        f"{feed_fragment(feed_data, days=days, weeks=weeks, now=now)}"
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
    Seite, ohne pro Screen ein eigenes hx-target verdrahten zu müssen.

    RESCAN zeigt bewusst wieder die generische Beschriftung, kein Sync-Label
    mehr (PLAN-21 Befund 2, revidiert PLAN-20 Befund 5: der Sync-Zustand stand
    dadurch gleichzeitig im Button UND in der Git-Karte — echte Dopplung, per
    Screenshot bestätigt. Bleibt jetzt nur noch in der Git-Karte). Kein
    "Wartungsmodus aktiv"-Banner mehr (PLAN-21 Befund 3: reine Redundanz zum
    längst aussagekräftigen MAINT-Toggle, keine Zusatzinfo).

    MAINT nur mit ``scheduler``-Rolle (PLAN-25 Befund 1) — der Client kennt
    gar keinen eigenen Maintenance-Mode, ein Klick hätte dort nie etwas
    pausiert. RESCAN bleibt unbedingt, das ist auf jedem Knoten sinnvoll."""
    roles = (status or {}).get("roles") or []
    maint = bool((status or {}).get("maintenance"))
    mcls = "toggle warn" if maint else "toggle"
    mlabel = "MAINT: ON" if maint else "MAINT: OFF"
    maint_btn = f'<button id="maint" class="{mcls}">{mlabel}</button>' if "scheduler" in roles else ""
    return (
        '<nav class="handles">'
        '<button id="rescan" class="toggle">RESCAN</button>'
        f"{maint_btn}"
        "</nav>"
    )


#: RESCAN + MAINT als plain-JS-Buttons gegen die JSON-API (§1.1). RESCAN → POST
#: /-/rescan (kurze Quittung). MAINT → POST/DELETE /-/maintenance; der Button **und
#: ein Banner** spiegeln die **echte Server-Antwort** (kein optimistisches Toggle —
#: bei Fehler bleibt der Zustand). FOLLOW besorgt _FOLLOW_JS (window.bibiFollow).
_OPS_HANDLES_JS = """
(function(){
  const rescan = document.getElementById('rescan');
  if (rescan) {
    const idleLabel = rescan.textContent;   // "RESCAN"
    rescan.addEventListener('click', async () => {
      rescan.disabled = true; rescan.textContent = 'RESCAN…';
      try { await fetch('/-/rescan', {method:'POST'}); } catch(_){}
      rescan.textContent = idleLabel + ' ✓';
      setTimeout(() => { rescan.textContent = idleLabel; rescan.disabled = false; }, 1200);
    });
  }
  const maint = document.getElementById('maint');
  function setMaint(on){
    maint.classList.toggle('warn', on);
    maint.textContent = on ? 'MAINT: ON' : 'MAINT: OFF';
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
#: ``hx-preserve`` hält Inhalt + EventSource über den 2s-#live-Poll am Leben, aber
#: NICHT den Scroll-Zustand — das erneute Einhängen des (unveränderten) Elements in
#: den DOM-Baum setzt ``scrollTop`` browserseitig auf 0 zurück. Live gemessen
#: 2026-07-07: derselbe Node (gleiche id, offene EventSource, wachsender Inhalt)
#: sprang trotzdem alle ~2s auf ``scrollTop=0`` zurück; die ``onmessage``-Stick-Logik
#: unten korrigiert das nur reaktiv bei der nächsten SSE-Nachricht — dazwischen bleibt
#: die Box oben hängen, obwohl FOLLOW an ist (User-Feedback: "ich muss manuell
#: herunterscrollen"). Fix analog zum ``.liveclamp``-Mechanismus weiter unten, siehe
#: dort.
_LIVE_JS = """
(function(){
  const bound = new WeakSet();
  function attach(){
    document.querySelectorAll('.liveterm[data-job]').forEach(box => {
      if (bound.has(box)) return;
      bound.add(box);
      // Seed-Inhalt (server-seitig gerendert) kann die Box bereits vor dem
      // ersten Event überfüllen — ohne dies bleibt scrollTop bei 0 und
      // atBottom() liefert ab dem allerersten Check "false" (Follow-up-Bug,
      // live reproduziert 2026-07-06: FOLLOW bleibt danach dauerhaft wirkungslos).
      box.scrollTop = box.scrollHeight;
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
// Scroll-Erhalt für .liveterm (running, SSE via hx-preserve — Inhalt + EventSource
// überleben den 2s-#live-Poll, scrollTop aber nicht, s. Kommentar oben bei _LIVE_JS).
// Anders als .liveclamp hier keine absolute Positions-Wiederherstellung: eine laufende
// Live-Box soll dem NEUEN Ende folgen (falls man vor dem Swap unten war), nicht zur
// alten Pixel-Position zurückspringen.
(function(){
  let wasAtBottom = null;
  document.body.addEventListener('htmx:beforeSwap', (ev) => {
    const t = ev.detail && ev.detail.target;
    if (t && t.id === 'live') {
      const box = t.querySelector('.liveterm[data-job]');
      wasAtBottom = box ? (box.scrollTop + box.clientHeight >= box.scrollHeight - 24) : null;
    }
  });
  document.body.addEventListener('htmx:afterSettle', () => {
    if (wasAtBottom == null) return;
    const live = document.getElementById('live');
    const box = live && live.querySelector('.liveterm[data-job]');
    if (box && wasAtBottom) box.scrollTop = box.scrollHeight;
    wasAtBottom = null;
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


#: Basis-Pfad der Journal-Bausteine — Default = Schedule-Detailseite (Host).
#: Die lokale Jobs-Detailseite (PLAN-21 Befund 10-Nachtrag) übergibt
#: "/-/ui/jobs/detail" an dieselben Funktionen, um Pagination/Löschen gegen
#: ihre eigenen (rollenunabhängigen) Routen zu verdrahten — sonst identischer
#: Baustein, kein Fork nötig.
_JOURNAL_BASE = "/-/ui/schedule"


def _journal_sentinel_row(slug: str, offset: int, *, base: str = _JOURNAL_BASE) -> str:
    """Trigger-Zeile für Infinite Scroll: sichtbar (``revealed``) lädt sie die
    nächste Batch nach und ersetzt sich selbst (outerHTML) — mit neuer Batch +
    ggf. frischer Sentinel-Zeile, oder ganz ohne, wenn das Ende erreicht ist."""
    s = _e(slug)
    return (
        f'<tr id="journal-more" hx-get="{base}/{s}/runs?offset={offset}" '
        f'hx-trigger="revealed" hx-swap="outerHTML">'
        f'<td colspan="7" class="muted">lädt weitere Läufe…</td></tr>'
    )


def _journal_table_html(runs: list[dict], slug: str, now: float, *, offset: int = 0,
                        base: str = _JOURNAL_BASE) -> str:
    if not runs:
        return '<p class="out-empty">— noch keine Läufe —</p>'
    rows = _run_rows(runs, slug, now, base=base)
    if len(runs) == _JOURNAL_PAGE_SIZE:
        rows += _journal_sentinel_row(slug, offset + _JOURNAL_PAGE_SIZE, base=base)
    return (
        '<table><thead><tr><th>Zeit</th><th>Status</th><th>Grund</th>'
        '<th>exit</th><th>Dauer</th><th>Commit</th><th></th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def journal_fragment(runs: list[dict], slug: str, now: float, *, oob: bool = False,
                     base: str = _JOURNAL_BASE) -> str:
    """Eigenständige, nicht selbst-pollende Region (``#journal``) — wächst nur
    durch nutzergetriggertes Infinite-Scroll-Nachladen (kein 2s-Poll, der die
    nachgeladenen Zeilen sonst wieder plattmachen würde)."""
    oob_attr = ' hx-swap-oob="true"' if oob else ""
    return (
        f'<div id="journal"{oob_attr}>'
        "<h2>Journal</h2>"
        f"{_journal_table_html(runs, slug, now, base=base)}"
        "</div>"
    )


def journal_runs_fragment(runs: list[dict], slug: str, now: float, offset: int,
                          *, base: str = _JOURNAL_BASE) -> str:
    """Nächste Batch für ``GET .../runs?offset=N`` — ersetzt die Sentinel-Zeile
    (outerHTML) durch die neuen Zeilen + ggf. eine frische Sentinel-Zeile."""
    rows = _run_rows(runs, slug, now, base=base)
    if len(runs) == _JOURNAL_PAGE_SIZE:
        rows += _journal_sentinel_row(slug, offset + _JOURNAL_PAGE_SIZE, base=base)
    return rows


def _run_rows(runs: list[dict], slug: str, now: float, *, base: str = _JOURNAL_BASE) -> str:
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
            f'<button hx-delete="{base}/{s}/run/{rid}" hx-target="#journal" '
            f'hx-swap="outerHTML" hx-confirm="Lauf-Record löschen?">Löschen</button></td>'
            "</tr>"
        )
    return "".join(rows)


def _live_panel(job: dict | None, now: float, live_output: dict | None = None,
               slug: str = "", *, public_host: str = "localhost") -> str:
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
            bits.append(f"finished {_ago(job['finished_at'], now)}")
    elif job.get("started_at"):
        bits.append(f"since {_ago(job['started_at'], now)}")
    if job.get("status") == "pending" and job.get("next_fire_at"):
        bits.append(f"next run {_until(job.get('next_fire_at'), now)}")
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
    app_link = (f' <a href="http://{public_host}:{app_port}/" target="_blank" '
                f'style="font-size:.82rem">Zur App →</a>' if app_port else "")
    # PLAN-22 Befund 1: pending hat weder started_at noch Output — "aktiver
    # Lauf" suggerierte fälschlich, dass gerade schon etwas läuft.
    if is_terminal:
        label = "letzter Lauf"
    elif job.get("status") == "pending":
        label = "wartet"
    else:
        label = "aktiver Lauf"
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


#: PLAN-24 Befund 5: REBUILD ist bewusst NICHT Teil von _VERBS/_VERBS_FOR_STATUS
#: — anders als START/RESET/KILL (immer sichtbar, nur je nach Status
#: aktiviert) taucht REBUILD gar nicht erst auf, wenn der Job nicht im
#: Container-Modus läuft (User-Klärung: "sichtbar nur bei exec_mode:
#: container", nicht sichtbar-aber-deaktiviert). Host-Mode-Jobs haben kein
#: per-Job-Image, das ein Reset bräuchte (uv run --script ist selbst schon
#: reproduzierbar).
_CONTAINER_VERBS = ("rebuild",)


def _action_bar(slug: str, job: dict | None, exec_mode: str | None = None) -> str:
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
    if (exec_mode or "host").strip().lower() == "container":
        btns += (f'<button hx-post="/-/ui/schedule/{s}/rebuild" hx-target="#live" '
                 f'hx-swap="outerHTML" '
                 f'title="Verwirft das per-Job-Image, nächster Lauf startet vom '
                 f'Default-Image">REBUILD</button> ')
    return f'<div class="actions">{btns}</div>'


def live_fragment(
    schedule: dict | None, runs: list[dict], job: dict | None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, public_host: str = "localhost",
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
        f"{_action_bar(slug, job, exec_mode=s.get('exec_mode'))}"
        f"{_live_panel(job, now, live_output, slug=slug, public_host=public_host)}"
        "</div>"
    )


def schedule_detail_inner(
    schedule: dict | None, runs: list[dict], job: dict | None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, public_host: str = "localhost",
) -> str:
    """Voller Detail-Kern für den initialen Seitenaufbau: ``#live`` (self-
    pollend) + ``#journal`` (einmalig, wächst nur per Infinite Scroll)."""
    now = time.time() if now is None else now
    return (
        live_fragment(schedule, runs, job, slug, now, live_output=live_output,
                      public_host=public_host)
        + journal_fragment(runs, slug, now)
    )


def schedule_detail_page(
    schedule: dict | None, runs: list[dict], job: dict | None = None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, daemon_status: dict | None = None,
    public_host: str = "localhost",
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
        f"{schedule_detail_inner(schedule, runs, job, slug, now, live_output=live_output, public_host=public_host)}"
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
#: kind/status/exit_code/started_at/finished_at/exec_runtime/schedule_ref
#: werden vorab als eigene, kompakte Zeilen gerendert (s. _attr_table())
#: statt hier nochmal generisch durchzulaufen. snapshot bekommt eigene Zeilen
#: (_run_config_rows()) statt als abgeschnittener JSON-String zu stehen.
_ATTR_HIDDEN = {"kind", "status", "exit_code", "exec_runtime", "started_at",
                "finished_at", "schedule_ref", "snapshot"}


def _attr_table(e: dict) -> str:
    """Alle Attribute dieses Laufs in **einer** Tabelle (PLAN-21 Befund 9,
    User-Entscheidung: Zusammenlegung trotz ursprünglicher Empfehlung —
    "der Snapshot besagt nur, dass diese Attribut-Werte unveränderlich
    sind"). Löst die frühere separate Kopfzeile ab (vormals ``_exec_summary()``,
    duplizierte host/worker/exit_code mit dieser Tabelle, User-Feedback
    2026-07-01 wollte sie ursprünglich breit *statt* in der Tabelle). Die zum
    Dispatch-Zeitpunkt eingefrorene Konfiguration (vormals eigene
    ``_run_config_section()``) hängt jetzt als eigener, durch eine Trennzeile
    abgesetzter Block an derselben Tabelle — "Dieser Lauf" (was passiert ist)
    bleibt dadurch weiterhin von "Konfiguration bei Start" (womit er lief,
    kann vom heutigen Schedule-Stand abweichen) unterscheidbar."""
    import datetime as _dt
    rows = []
    if e.get("kind"):
        rows.append(f'<tr><td><b>kind</b></td><td>{_e(str(e["kind"]))}</td></tr>')
    st = e.get("status")
    if st:
        rows.append(f'<tr><td><b>status</b></td>'
                    f'<td><span class="st {_e(st)}">{_e(st)}</span></td></tr>')
    if e.get("exit_code") is not None:
        rows.append(f'<tr><td><b>exit_code</b></td><td>{e["exit_code"]}</td></tr>')
    rt = e.get("exec_runtime")
    s, f = e.get("started_at"), e.get("finished_at")
    if rt is None and s is not None and f is not None:
        rt = f - s
    if s is not None and f is not None:
        s_str = _dt.datetime.fromtimestamp(s).strftime("%Y-%m-%d %H:%M:%S")
        f_str = _dt.datetime.fromtimestamp(f).strftime("%H:%M:%S")
        dauer = f" (Dauer {round(rt)} s)" if rt is not None else ""
        rows.append(f'<tr><td><b>Lauf</b></td><td>{_e(s_str)} → {_e(f_str)}{dauer}</td></tr>')
    elif rt is not None:
        rows.append(f'<tr><td><b>exec_runtime</b></td><td>{rt:.1f} s</td></tr>')

    seen = set(_ATTR_HIDDEN)
    for key in _ATTR_ORDER:
        if key not in e or key in seen:
            continue
        seen.add(key)
        val = e[key]
        if val is None:
            continue
        if key in _TS_FIELDS and isinstance(val, (int, float)):
            val = _dt.datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
        elif key == "commit_sha" and isinstance(val, str) and len(val) > 7:
            branch = _e(e.get("branch") or "")
            val = f"{val[:7]} ({branch})" if branch else val[:7]
        rows.append(f"<tr><td><b>{_e(key)}</b></td><td>{_e(str(val))}</td></tr>")
    if e.get("schedule_ref"):
        rows.append(f'<tr><td><b>schedule_ref</b></td>'
                    f'<td><code>{_e(str(e["schedule_ref"]))}</code></td></tr>')
    # Restliche Felder die nicht in _ATTR_ORDER stehen
    for key, val in sorted(e.items()):
        if key in seen or val is None:
            continue
        rows.append(f"<tr><td><b>{_e(key)}</b></td><td>{_e(str(val))}</td></tr>")

    config_rows = _run_config_rows(e)
    if config_rows:
        rows.append('<tr class="subhead"><td colspan="2">Konfiguration bei Start</td></tr>')
        rows.extend(config_rows)

    return (
        '<table class="attrtable">'
        "<thead><tr><th>Attribut</th><th>Wert</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _run_config_rows(e: dict) -> list[str]:
    """Zeilen der zum Laufzeitpunkt eingefrorenen Konfiguration
    (``journal.snapshot``, voll via job_full_view() seit User-Feedback
    2026-07-03: "ein Schedule oder Attempts kann sich ändern, deshalb müssen
    alle Werte ... als Attribut am Lauf hängen"). Nur für die disponierte
    Domäne — lokale ``/run``-Läufe (kein Schedule) haben nur einen minimalen
    Snapshot ohne echte Konfig-Felder."""
    import json
    if e.get("domain") != "scheduled":
        return []
    try:
        snap = json.loads(e.get("snapshot") or "{}")
    except ValueError:
        return []
    if not snap.get("schedule_ref"):
        return []
    return _attrs_rows(_ATTRS_CONFIG_ORDER, snap)


def _is_own_run(e: dict) -> bool:
    """Eigener, gepinnter /run-Lauf — historisch ``domain='local'`` (vor PLAN-28
    Refactor D geschrieben, auf Bestandsknoten evtl. noch vorhanden), seither
    ``domain='scheduled'`` (echte ``jobs``-Zeile) **mit** ``pinned_host``
    gesetzt. Spiegelt ``app.py``s gleichnamigen Helper — dort wie hier
    entscheidet das, ob ein Lauf zur eigenen Jobs-Historie zählt statt zu
    einem echten Team-Queue-Job."""
    return e.get("domain") == "local" or e.get("pinned_host") is not None


def execution_detail_page(entry: dict | None, events: list[dict], kind: str,
                          now: float | None = None,
                          *, daemon_status: dict | None = None) -> str:
    """Ein **Lauf** (``run_id``): alle Journal-Attribute + voller Output."""
    now = time.time() if now is None else now
    e = entry or {}
    run_id = _e(e.get("run_id") or "—")
    slug = _e(e.get("slug") or "")
    st = _e(e.get("status") or "")
    out = output_block(events, e.get("kind") or kind)
    jid = e.get("id")
    # Follow-up (User-Feedback): "auch bei archivierten Jobs im Journal eine
    # Möglichkeit, den Original Output zu sehen" — roher Zugriff neben dem
    # formatierten Output (.../out|err|stream, PLAN-14 Stufe 14.0). target=
    # _blank (User-Feedback 2026-07-01): roher Output soll die formatierte
    # Ansicht nicht verdrängen. PLAN-28 User-Feedback ("Warum nicht die
    # gleiche Ansicht? Warum nicht die gleiche Logik?"): eigene/gepinnte
    # Läufe bekommen jetzt dieselben rohen Links wie Team-Queue-Läufe, nur
    # über die rollenunabhängige /-/run/journal/{jid}/out|err|stream-Route
    # (_is_own_run()) statt der scheduler-gated /-/journal/{jid}/....
    raw_base = "/-/run/journal" if _is_own_run(e) else "/-/journal"
    raw_links = (
        f' <span class="muted">roh: '
        f'<a class="back" href="{raw_base}/{jid}/out" target="_blank" rel="noopener">out</a> · '
        f'<a class="back" href="{raw_base}/{jid}/err" target="_blank" rel="noopener">err</a> · '
        f'<a class="back" href="{raw_base}/{jid}/stream" target="_blank" rel="noopener">stream</a></span>'
        if jid is not None else ""
    )
    if _is_own_run(e):
        # Eigener /run-Lauf (PLAN-21 Befund 10; PLAN-28 Refactor D um gepinnte
        # jobs-Zeilen erweitert): "zurück zum Schedule" wäre die scheduler-
        # gated Remote-Detailseite (auf einem reinen Client 404) — zurück zum
        # Jobs-Screen stattdessen.
        back = '<a class="back" href="/-/ui/jobs">← Jobs</a>'
    else:
        # Breadcrumb statt eigenem "bibi ·"-Header (User-Feedback 2026-07-01:
        # doppeltes "bibi" + verschachtelte Nav) — derselbe Aufbau wie
        # schedule_detail_page().
        back = (f'<a class="back" href="/-/ui/schedule/{slug}">← {slug}</a>'
                if slug else '<a class="back" href="/-/">← zurück</a>')
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
        # Trennzeile "Konfiguration bei Start" (PLAN-21 Befund 9) — hebt den
        # Snapshot-Block optisch vom Lauf-Ergebnis ab, ohne eine zweite Tabelle
        # zu brauchen.
        ".attrtable tr.subhead td { padding-top: .6rem; font-size: .72rem; "
        "color: #888; text-transform: uppercase; letter-spacing: .03em; "
        "border-bottom: 1px solid #8883; }"
        "</style></head><body>"
        f"{_header('', daemon_status)}"
        f'<div style="display:flex;gap:.75rem;align-items:baseline">{back}</div>'
        f'<h1><span class="st {st}">{run_id}</span></h1>'
        f"{_attr_table(e)}"
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


def _attrs_rows(keys: list[str], data: dict) -> list[str]:
    """``<tr>``-Zeilen für ``keys`` aus ``data`` — Baustein, geteilt zwischen
    ``_attrs_section()`` (eigene Seite) und der zusammengelegten Lauf-
    Attribut-Tabelle (PLAN-21 Befund 9, ``_attr_table()``)."""
    import datetime as _dt
    rows = []
    for key in keys:
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
    return rows


def _attrs_section(title: str, keys: list[str], data: dict) -> str:
    return (
        f"<h2>{title}</h2>"
        '<table class="attrtable">'
        "<thead><tr><th>Attribut</th><th>Wert</th></tr></thead>"
        f"<tbody>{''.join(_attrs_rows(keys, data))}</tbody></table>"
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
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )
