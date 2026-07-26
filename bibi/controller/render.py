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
/* Rahmen um die ganze Nav-Leiste, von "bibi" links bis Theme-Toggle rechts
   (Bibi4-Iteration, User-Fund) — derselbe Stil wie .panel-card/.card. */
header { display: flex; align-items: baseline; justify-content: space-between;
         gap: .75rem; flex-wrap: wrap; border: 1px solid #8883; border-radius: .4rem;
         padding: .5rem .9rem; margin-bottom: .6rem; }
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
.toggle { font: inherit; font-size: 1.3rem; line-height: 1; text-decoration: none;
          color: #888; background: none; border: none; padding: 0; cursor: pointer; }
.toggle:hover { text-decoration: underline; }
.toggle.on { color: #5fb37a; }
.toggle.warn { color: #d6a23e; }
.toggle.bad { color: #e06c5a; }
/* Disabled-aber-sichtbar (Bibi4-Iteration, User-Fund "eine App") — Host/
   Client zeigen dieselbe Toggle-Menge, nicht verfügbare Funktionen (z.B.
   MAINT auf dem Client) bleiben an Ort und Stelle, statt zu verschwinden. */
.toggle:disabled { opacity: .35; cursor: default; text-decoration: none; }
/* Rollen-Matrix (Bibi4-Iteration, User-Fund: "Rollen etwas schoener
   visualisieren, vielleicht als Spalten mit leerem oder gefuelltem
   Rechteck") — ersetzt die alte Komma-Text-Spalte im Clients-Screen. */
.role-box { font-size: 1rem; }
.role-box.on { color: #5fb37a; }
.role-box.off { color: #666; opacity: .45; }
/* Time-Toggle (Bibi4-Iteration, User-Fund: "Time: abs./rel./both" für die
   last/since- und next-Spalten) — alle drei Varianten stehen serverseitig
   immer im Markup, data-timeformat auf <html> blendet per CSS genau eine
   ein, kein Re-Render pro Klick nötig. Default (per _TIME_JS) ist "both". */
:root[data-timeformat="abs"] .tt-relonly,
:root[data-timeformat="abs"] .tt-relboth,
:root[data-timeformat="both"] .tt-relonly,
:root[data-timeformat="rel"] .tt-abs,
:root[data-timeformat="rel"] .tt-relboth { display: none; }
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
.actions button:disabled { opacity: .5; cursor: default; }
/* Aktivitätsanzeige (Bibi4 Batch 6) — .btn-spinner sitzt im Button (s.
   _BTN_SPINNER), unsichtbar per Default, htmx setzt die htmx-request-Klasse
   automatisch aufs auslösende Element für die Dauer des Requests, kein
   eigenes hx-indicator nötig. */
.btn-spinner { display: inline-block; width: .55em; height: .55em; margin-left: .45em;
               border-radius: 50%; background: currentColor; opacity: 0;
               vertical-align: middle; }
.htmx-request .btn-spinner { opacity: 1; animation: bibi-pulse .9s ease-in-out infinite; }
@keyframes bibi-pulse { 0%, 100% { opacity: .25; transform: scale(.7); }
                         50% { opacity: 1; transform: scale(1); } }
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
/* a.slug hat sonst keine eigene Farbe (Jobs-/Schedule-Tabelle: soll dem Theme
   folgen) — im .logbox hier aber erbt sie sonst Chromes color-scheme-abhängige
   Standard-Linkfarbe (Light: dunkles #0000EE), obwohl der Hintergrund immer
   dunkel bleibt (Bibi4-Iteration, User-Fund: "im Light Mode ist die
   Schriftfarbe lila schwer zu lesen" — live gemessen, Root Cause). Fest statt
   theme-abhängig, analog zu .ln.warning/.error oben. */
.logbox a.slug { color: #9e9eff; }
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
/* Job-Status-Matrix (Bibi4-Iteration, User-Fund: "Apps enden nicht" — eigene
   Spalte je Kind statt der bisherigen 2x2-Aggregation ohne Kind-Aufschlüsselung,
   löst .kvgrid2 ab). 4 Spalten: Label + job/claude/app, row-major befüllt
   (Header-Zeile, dann Waiting/Running/Stopped). */
.card .jobstatus-grid { display: grid; grid-template-columns: auto repeat(3, minmax(2.2rem, auto));
                        row-gap: .2rem; column-gap: .6em; margin-top: .15rem; }
.card .jobstatus-grid .jsg-h { font-size: .68rem; font-weight: 400; color: #888;
                               text-transform: uppercase; letter-spacing: .03em; text-align: right; }
.card .jobstatus-grid .jsg-k { font-size: .72rem; font-weight: 400; color: #888;
                               text-transform: uppercase; letter-spacing: .03em; align-self: center; }
.card .jobstatus-grid .jsg-v { font-size: 1.0rem; font-weight: 600; text-align: right; }
.side-empty { color: #888; font-size: .82rem; }
.chip { font-family: ui-monospace, monospace; font-size: .7rem; font-weight: 700;
        padding: .1rem .45rem; border-radius: .3rem; display: inline-block; white-space: nowrap; }
/* Git-Status je Job-MD (PLAN-21 Befund 10) — löst die vorherige Lokal/Remote-
   Abgleich-Chips (same/diff/local_only/remote_only) ab. */
.chip.clean { background: #5fb37a2e; color: #5fb37a; }
.chip.modified { background: #d6a23e2e; color: #d6a23e; }
.chip.new { background: #5a9fe02e; color: #5a9fe0; }
.chip.conflict { background: #e06c5a2e; color: #e06c5a; }
/* Nodes-Screen Git-Status-Chips (Batch 9 Punkt 3) — dieselben Farben wie
   .tree-*/.sync-* (Feed-Git-Kachel), hier als Chip statt Klartext. */
.chip.synced { background: #5fb37a2e; color: #5fb37a; }
.chip.ahead { background: #d6a23e2e; color: #d6a23e; }
.chip.behind, .chip.diverged { background: #e06c5a2e; color: #e06c5a; }
.sparkline { display: block; vertical-align: middle; }
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
           flex: 0 0 11rem; overflow-wrap: anywhere; }
.lvl { font-family: ui-monospace, monospace; font-size: .68rem; font-weight: 700;
       padding: .05rem .4rem; border-radius: .25rem; flex: 0 0 auto;
       text-transform: uppercase; letter-spacing: .02em; }
.lvl.case { background: #5fb37a33; color: #5fb37a; }
.lvl.vault { background: #5a9fe033; color: #5a9fe0; }
.lvl.system { background: #d6a23e33; color: #d6a23e; }
/* Bibi4-Iteration, User-Fund: langer Slug (Bindestrich-Umbruch mitten im Wort)
   und die kommagetrennte Autorenliste liefen über den Rand — Flex-Items haben
   ohne min-width:0 eine implizite Mindestbreite gleich ihrem Inhalt, egal wie
   die Zeile eigentlich schrumpfen könnte. */
.frow .msg { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.frow .who { color: #888; font-size: .78rem; flex: 0 1 auto; min-width: 0;
             overflow-wrap: anywhere; }
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


def _human_duration(seconds: float | None) -> str:
    """Dauer (kein Zeitpunkt) als angepasstes Delta — Bibi4-Iteration, User-
    Fund: "die Spalte Laufzeit soll human-readable sein und nicht nur die
    Sekunden zeigen, sondern je nach Dauer ein angepasstes Delta". Analog zu
    ``_ago()``/``_until()``, aber ohne "vor"/"in"-Präfix (reine Dauer, keine
    Distanz zu ``now``) und mit zwei Einheiten je Stufe (z.B. "3m 12s") statt
    einer, damit eine 90-Minuten-Laufzeit nicht auf "1h" abgerundet wird."""
    if seconds is None:
        return "—"
    d = max(0, int(seconds))
    if d < 60:
        return f"{d}s"
    if d < 3600:
        m, s = divmod(d, 60)
        return f"{m}m {s}s"
    if d < 86400:
        h, rem = divmod(d, 3600)
        return f"{h}h {rem // 60}m"
    days, rem = divmod(d, 86400)
    return f"{days}d {rem // 3600}h"


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


def _time_abs_full(ts: float | None) -> str:
    """Volles absolutes Format für den Time-Toggle (Bibi4-Iteration, User-
    Beispiel ``2026-07-18 23:18``) — mit Jahr, anders als das knappere
    ``_abs_datetime()`` (nur TT.MM., für die kompakte Journal-Spalte gedacht,
    wo das Jahr praktisch nie mehrdeutig ist)."""
    if ts is None:
        return "—"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _time_toggle_cell(ts: float | None, now: float, *, rel_fn=_ago) -> str:
    """Rendert alle drei Time-Toggle-Varianten (abs/rel/beides) auf einmal vor
    — CSS blendet über ``data-timeformat`` auf ``<html>`` genau eine Variante
    ein (Bibi4-Iteration, User-Fund: Toggle "Time: abs./rel./both" für die
    last/since- und next-Spalten). Kein Re-Render bei einem Toggle-Klick
    nötig, kein eigener Client-Tick für die relative Anzeige — die betroffenen
    Tabellen pollen ohnehin schon (2s Schedules/Jobs), das reicht für die
    Aktualität. ``rel_fn`` ist ``_ago`` (Vergangenheit, "last/since") oder
    ``_until`` (Zukunft, "next", trägt schon den "asap"-Sonderfall)."""
    if ts is None:
        return "—"
    abs_s = _e(_time_abs_full(ts))
    rel_s = _e(rel_fn(ts, now))
    return (f'<span class="tt-abs">{abs_s}</span>'
           f'<span class="tt-relonly">{rel_s}</span>'
           f'<span class="tt-relboth"> ({rel_s})</span>')


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


def _sched_row(s: dict, now: float, *, public_host: str = "localhost",
              sparklines: dict[str, list[int]] | None = None) -> str:
    raw_slug = s.get("slug")
    slug = _e(raw_slug)
    st = _e(s.get("last_status"))
    # Bibi4-Iteration, User-Fund: "Type (beim Host wird app noch nicht
    # angezeigt, soll es aber, auch mit Port!)" — dieselbe Zellen-Ableitung
    # wie die Client-Jobs-Tabelle (_jobs_type_cell()), nicht mehr
    # _effective_sched_type()/models.effective_kind(). Bewusst NUR die
    # Anzeige betroffen: filter_schedules()/_effective_sched_type() bleiben
    # unverändert (PLAN-25 Befund 7 galt fürs Filtern, User-Entscheidung
    # "Jobs mit Port und Prefix sollen einfach als Jobs erscheinen" — dieser
    # Fund hier reversiert nur die Anzeige, nicht die Filter-Semantik, die
    # wurde nicht neu angefragt).
    kind = _jobs_type_cell(s, public_host)
    nxt = _time_toggle_cell(s.get("next_fire_at"), now, rel_fn=_until)
    ago = _time_toggle_cell(s.get("last_run_at"), now, rel_fn=_ago)
    run_id = s.get("last_run_id")
    # Status/letzter-seit -> Lauf-Details (die konkrete Ausführung); Schedule/
    # nächster -> Job-Details (der Schedule selbst) — User-Feedback 2026-07-01.
    # Ohne abgeschlossenen Lauf (run_id None) gibt es keine Lauf-Details zum Verlinken.
    status_cell = (f'<a class="st {st}" href="/-/ui/run/{run_id}">{st}</a>'
                   if run_id is not None else f'<span class="st {st}">{st}</span>')
    ago_cell = (f'<a class="rowlink" href="/-/ui/run/{run_id}">{ago}</a>'
                if run_id is not None else ago)
    # Batch 9 Punkt 1 (Host-Sparkline-Spalte): dieselbe hx-preserve-Zelle wie
    # die Jobs-Tabelle (_jobs_row()) — sparklines kommt nur vom initialen
    # Seitenaufbau (schedules_screen()/archive_screen()), der 2s-Self-Poll
    # übergibt None (s. _sparkline_cell()-Docstring).
    spark_cell = _sparkline_cell(raw_slug, sparklines)
    return (
        "<tr>"
        f'<td><a class="slug" href="/-/ui/schedule/{slug}">{slug}</a></td>'
        f'<td class="kind">{kind}</td>'
        f"<td>{status_cell}</td>"
        f"<td>{ago_cell}</td>"
        f'<td><a class="rowlink" href="/-/ui/schedule/{slug}">{nxt}</a></td>'
        f"<td>{spark_cell}</td>"
        "</tr>"
    )


def _sched_table(items: list[dict], now: float, *, public_host: str = "localhost",
                 sparklines: dict[str, list[int]] | None = None) -> str:
    # Bibi4-Iteration, Seitenabgleich (User-Fund): Spaltenkopf war "Schedule",
    # der Client sagt für dieselbe Spalte schon "Slug" (_jobs_table()) — auch
    # dein ursprünglicher Batch-1-Spaltenplan für den Host wollte "Slug" als
    # erste Spalte, das war nie nachgezogen worden.
    rows = "".join(_sched_row(s, now, public_host=public_host, sparklines=sparklines)
                  for s in items)
    return ('<table class="sched"><thead><tr><th>Slug</th><th>Type</th><th>Status</th>'
            '<th>last / since</th><th>next</th><th>Activity</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>")


def _schedule_active_block(schedules: list[dict], now: float,
                           *, public_host: str = "localhost",
                           sparklines: dict[str, list[int]] | None = None) -> str:
    head = f'<h2>Schedules ({len(schedules)})</h2>'
    if not schedules:
        return head + '<p class="out-empty">— no schedules —</p>'
    active, _archive, _journaled = _group_schedules(schedules)
    body = (_sched_table(active, now, public_host=public_host, sparklines=sparklines) if active
            else '<p class="out-empty">— no active schedules —</p>')
    return head + body


def _schedule_archive_block(schedules: list[dict], now: float,
                            *, public_host: str = "localhost",
                            sparklines: dict[str, list[int]] | None = None) -> str:
    if not schedules:
        return ""
    _active, archive, journaled = _group_schedules(schedules)
    body = ""
    if archive:
        body += (f'<h3>Archive ({len(archive)})</h3>'
                + _sched_table(archive, now, public_host=public_host, sparklines=sparklines))
    if journaled:
        body += (f'<h3>Journal — history only ({len(journaled)})</h3>'
                + _sched_table(journaled, now, public_host=public_host, sparklines=sparklines))
    return body


def schedule_list(schedules: list[dict], now: float | None = None,
                  *, public_host: str = "localhost",
                  sparklines: dict[str, list[int]] | None = None) -> str:
    """Die volle Liste, gruppiert nach Registrierungs-Zustand (PLAN-14 Stufe
    14.6, erweitert PLAN-23 Befund 2): Aktiv (MD entdeckt) / Archive (MD
    entfernt ODER abgeschlossener oneshot) / Journal (nur Journal-Historie).
    Flach + immer sichtbar, kein Klapp mehr — überlebt so den 2s-Poll ohne
    Expand-Verlust."""
    now = time.time() if now is None else now
    return (_schedule_active_block(schedules, now, public_host=public_host, sparklines=sparklines)
           + _schedule_archive_block(schedules, now, public_host=public_host, sparklines=sparklines))


def schedules_fragment(schedules: list[dict], now: float | None = None,
                       *, typ: str | None = None, status: str | None = None,
                       public_host: str = "localhost",
                       sparklines: dict[str, list[int]] | None = None) -> str:
    """Self-pollender Wrapper um die (bereits gefilterte) aktive Schedule-
    Liste. Der Self-Poll trägt den aktiven Filter in der URL, damit er ihn
    über den 2s-Tick bewahrt. Ziel = ``/-/ui/schedules/list`` (das Fragment;
    die Seite liegt auf ``/-/ui/schedules``). Archive/Journal sitzen seit der
    Bibi4-Iteration nicht mehr hier, sondern auf einem eigenen Screen
    (``archive_fragment()``/``archive_page()``, User-Fund: "Archive wird
    verschoben auf einen eigenen Screen") — löst PLAN-25 Befund 6 (3 Rahmen
    Chart/Schedules/Archive auf einer Seite) ab. ``sparklines`` (Batch 9
    Punkt 1) kommt nur vom initialen Seitenaufbau (``schedules_page()``); der
    2s-Self-Poll (``schedules_list_fragment()``) übergibt ``None``, analog zu
    ``jobs_fragment()``/``jobs_board()``."""
    now = time.time() if now is None else now
    qs = "&".join(f"{k}={v}" for k, v in (("typ", typ), ("status", status))
                  if v and v != "alle")
    url = "/-/ui/schedules/list" + (f"?{qs}" if qs else "")
    attrs = (f'id="schedules" hx-get="{url}" '
            f'hx-trigger="{_POLL}" hx-swap="outerHTML"')
    active_html = (f'<div class="panel-card">'
                  f'{_schedule_active_block(schedules, now, public_host=public_host, sparklines=sparklines)}</div>')
    return f"<div {attrs}>{active_html}</div>"


def archive_fragment(schedules: list[dict], now: float | None = None,
                     *, public_host: str = "localhost",
                     sparklines: dict[str, list[int]] | None = None) -> str:
    """Self-pollender Archive-Screen-Kern (Host) — Bibi4-Iteration, User-Fund:
    "Archive wird verschoben auf einen eigenen Screen". Zeigt dieselben
    Archive-/Journal-Gruppen wie zuvor der untere Teil von ``/-/ui/schedules``
    (``_schedule_archive_block()``), jetzt eigenständig unter ``/-/ui/archive``.
    Ziel = ``/-/ui/archive/list``. ``sparklines`` (Batch 9 Punkt 1) nur vom
    initialen Seitenaufbau (``archive_page()``), der 2s-Self-Poll
    (``archive_list_fragment()``) übergibt ``None``."""
    now = time.time() if now is None else now
    body = _schedule_archive_block(schedules, now, public_host=public_host, sparklines=sparklines)
    if not body:
        body = '<p class="out-empty">— kein Archiv —</p>'
    attrs = f'id="archive" hx-get="/-/ui/archive/list" hx-trigger="{_POLL}" hx-swap="outerHTML"'
    return f'<div {attrs}><div class="panel-card">{body}</div></div>'


def archive_page(schedules: list[dict], now: float | None = None,
                 *, daemon_status: dict | None = None, git_status: dict | None = None,
                 host_url: str | None = None, status_poll_interval_s: int = 30,
                 job_status_poll_interval_s: int = 2, public_host: str = "localhost",
                 sparklines: dict[str, list[int]] | None = None) -> str:
    """Archive-Screen (Host, Bibi4-Iteration) — eigene Seite für Archive/
    Journal, abgetrennt von der aktiven Schedule-Liste auf ``/-/ui/schedules``.
    Dieselben Nav/Ops-Bausteine wie jede andere Seite (``_header()``).

    Die Status-Kacheln (Host/Mode/Git/Job-Status, ``feed_status_fragment()``)
    fehlten hier bisher komplett — die Archive-Extraktion (Bibi4-Iteration)
    hat sie schlicht nicht mitgenommen, obwohl Feed/Jobs/Live-Log sie alle
    haben (User-Fund: "Header ist in Feed, Jobs, Archive (!), Live-Log
    sichtbar" — das "(!)" war berechtigt, echter Bug, kein Bildausschnitt)."""
    now = time.time() if now is None else now
    daemon_status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Archive</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Archive', daemon_status)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, poll_interval_s=status_poll_interval_s, job_status_poll_interval_s=job_status_poll_interval_s)}"
        f"{archive_fragment(schedules, now, public_host=public_host, sparklines=sparklines)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Connected-Clients-Screen (Host, Bibi4-Iteration) ─────────────────────────
# Backend existierte schon lange vor diesem Screen (WorkerRegistry, /-/worker,
# in /-/status.workers exponiert) — hier nur die erste Darstellung dafür.
# node_id/git_user (Bibi4-Iteration, User-Fund: "wir brauchen unbedingt den
# hinterlegten gitea/git Nutzernamen") reisen seit dem Heartbeat-Ausbau mit.
# role (zweite Bibi4-Iteration, User-Fund: "Client Übersicht braucht die
# Rollen je Client") denselben Weg: Heartbeat -> WorkerRegistry -> hier.

_CLIENTS_POLL = "every 10s [window.bibiFollow]"

# Reihenfolge wie vom User vorgegeben: "Mit Scheduler, Controller,
# Synchronizer, Connected, Worker." role-Werte (roher komma-getrennter
# String, s. roles.Roles.active_names()) tragen "connect", nicht
# "connected" — nur das Spalten-Label war "Connected".
#
# Batch 9 Punkt 3 (Nodes-Screen), User-Fund: CONNECT-Spalte entfällt —
# WorkerRegistry-Einträge sind per Konstruktion immer verbunden (sie
# existieren nur, weil ein Heartbeat ankam), die Spalte trug also nie
# Information, die nicht schon die Status-Spalte (connected/disconnected)
# zeigt. Labels CRON/CTRL/SYNC/WORK (User-Fund, „## Clients Screen",
# explizit in Großbuchstaben — löst die zuerst gebaute ≤3-Zeichen-Kürzung
# SCH/CTL/SYN/WRK ab, "cron" statt "sch" für Scheduler) — Spaltenanzahl
# bewusst bei vier belassen (User-Frage "ctrl weglassen?" beantwortet:
# behalten, sonst verschwindet die Information, dass ein reiner Client
# wirklich kein Worker/Scheduler ist).
_ROLE_COLUMNS = (
    ("scheduler", "CRON", "Scheduler"),
    ("controller", "CTRL", "Controller"),
    ("synchronizer", "SYNC", "Synchronizer"),
    ("worker", "WORK", "Worker"),
)


def _role_matrix_header() -> str:
    return "".join(f'<th><abbr title="{full}">{short}</abbr></th>'
                    for _name, short, full in _ROLE_COLUMNS)


def _role_matrix_cells(role: str | None) -> str:
    active = {r.strip() for r in (role or "").split(",") if r.strip()}
    cells = []
    for name, _short, full in _ROLE_COLUMNS:
        on = name in active
        cls = "role-box on" if on else "role-box off"
        mark = "■" if on else "□"
        cells.append(f'<td><span class="{cls}" title="{full}">{mark}</span></td>')
    return "".join(cells)


def _node_link_cell(worker: str | None, host: str | None, port: int | None) -> str:
    """Name+Host zu einem Link kombiniert (Batch 9 Punkt 3, User-Fund:
    ``[{name} :{port}](http://{host}:{port}/-/)``) — die URL, wie der Knoten
    sich selbst kennt (sein eigener ``BIBI_DAEMON_PORT``), nicht wie ein
    anderer Knoten ihn erreichen würde; bewusst so, auch wenn das bei
    ``localhost`` verwirrend aussieht (User-Entscheidung, s. „## Clients
    Screen"). Ohne ``port`` (älterer Client vor dieser Änderung, oder erster
    Heartbeat noch nicht durch) bleibt es reiner Text statt totem Link."""
    name = _e(worker or "—")
    if not host or not port:
        return name
    href = _e(f"http://{host}:{port}/-/")
    return f'<a href="{href}" target="_blank" rel="noopener">{name} :{port}</a>'


_NODE_TREE_CHIP_CLASS = {"clean": "chip clean", "modified": "chip modified"}
_NODE_SYNC_CHIP_CLASS = {"synced": "chip synced", "ahead": "chip ahead",
                         "behind": "chip behind", "diverged": "chip diverged"}


def _node_git_status_chips(git_status: str | None) -> str:
    """Batch 9 Punkt 3, User-Fund: "alle git Status Elemente ebenfalls als
    Chip (wie Status selbst) darstellen" — dieselbe ``.chip``-Optik wie die
    Status-Spalte, hier für Tree+Sync aus dem Heartbeat-String (``"<branch>
    · <tree> · <sync>"``, ``Heartbeat._git_status()``). Branch bleibt
    Klartext (kein Status, keine Farbsemantik, kein Präzedenzfall dafür
    irgendwo sonst im FE). Unerwartetes Format (z. B. "n/a" ohne lokales
    Repo, ältere Clients ohne Sync-Feld) fällt auf reinen Text zurück."""
    if not git_status:
        return "—"
    parts = git_status.split(" · ")
    if len(parts) != 3:
        return _e(git_status)
    branch, tree, sync = parts
    tree_cls = _NODE_TREE_CHIP_CLASS.get(tree, "chip")
    sync_cls = _NODE_SYNC_CHIP_CLASS.get(sync, "chip")
    return (f'{_e(branch)} <span class="{tree_cls}">{_e(tree)}</span> '
            f'<span class="{sync_cls}">{_e(sync)}</span>')


_APPROVAL_CHIP_CLASS = {"pending": "chip modified", "approved": "chip clean",
                        "blocked": "chip conflict"}


def _node_approval_cell(node_id: str | None, approval_status: str) -> str:
    """PLAN-32 Stufe 32.1 (Open-Trust-Connect-Gate): Freischalt-Status-Chip +
    Approve-/Block-Button je Zeile, wirkt sofort auf ``#clientsboard`` (analog
    ``_action_bar()``s Job-Verben). Ohne ``node_id`` (älterer Client vor
    dieser Änderung, oder die synthetische Host-Zeile) kein Button — serverseitig
    nicht individuell adressierbar, gilt implizit als "approved"
    (s. ``app.py::worker_heartbeat()``)."""
    chip = f'<span class="{_APPROVAL_CHIP_CLASS.get(approval_status, "chip")}">{_e(approval_status)}</span>'
    if not node_id:
        return chip
    nid = _e(node_id)
    if approval_status == "approved":
        btn = (f'<button class="killbtn" hx-post="/-/ui/clients/{nid}/block" '
               f'hx-target="#clientsboard" hx-swap="outerHTML" hx-disabled-elt="this">'
               f'Block{_BTN_SPINNER}</button>')
    else:
        # pending oder blocked: "Approve" ist die primäre Aktion — ein
        # pending-Knoten soll nicht erst einen Umweg über "Block" nehmen, um
        # dann wieder "Approve" angeboten zu bekommen.
        btn = (f'<button class="startbtn" hx-post="/-/ui/clients/{nid}/approve" '
               f'hx-target="#clientsboard" hx-swap="outerHTML" hx-disabled-elt="this">'
               f'Approve{_BTN_SPINNER}</button>')
    return f'{chip} {btn}'


def _clients_table(workers: list[dict], now: float) -> str:
    if not workers:
        return '<p class="out-empty">— keine Knoten —</p>'
    rows = []
    for w in sorted(workers, key=lambda w: w.get("worker") or ""):
        stale = w.get("stale", False)
        status_html = ('<span class="chip conflict">disconnected</span>' if stale
                       else '<span class="chip clean">connected</span>')
        rows.append(
            "<tr>"
            f"<td>{_node_link_cell(w.get('worker'), w.get('host'), w.get('port'))}</td>"
            f"{_role_matrix_cells(w.get('role'))}"
            f"<td>{_e(w.get('git_user') or '—')}</td>"
            f"<td>{_node_git_status_chips(w.get('git_status'))}</td>"
            f"<td>{status_html}</td>"
            f"<td>{_node_approval_cell(w.get('node_id'), w.get('approval_status', 'pending'))}</td>"
            f"<td>{_abs_datetime(w.get('connected_at'), now)}</td>"
            f"<td>{_ago(w.get('last_heartbeat'), now)}</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Name</th>'
        f"{_role_matrix_header()}"
        '<th>Git-User</th>'
        '<th>Git-Status</th><th>Status</th><th>Freigabe</th><th>Connected seit</th>'
        '<th>Letzter Heartbeat</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def clients_fragment(workers: list[dict], now: float | None = None) -> str:
    now = time.time() if now is None else now
    return (
        f'<div id="clientsboard" hx-get="/-/ui/clients/board" '
        f'hx-trigger="{_CLIENTS_POLL}" hx-swap="outerHTML">'
        '<div class="panel-card"><h2>Nodes</h2>'
        f"{_clients_table(workers, now)}</div>"
        "</div>"
    )


def clients_page(workers: list[dict], now: float | None = None, *,
                 daemon_status: dict | None = None, git_status: dict | None = None,
                 host_url: str | None = None, status_poll_interval_s: int = 30,
                 job_status_poll_interval_s: int = 2) -> str:
    """Nodes-Screen (Batch 9 Punkt 3: umbenannt von "Clients" — Nav-Label +
    Tabellen-Überschrift, Route/interne Namen bewusst unverändert, analog zur
    Host/Client-Jobs-Umbenennung weiter oben) — nur für die ``scheduler``-
    Rolle im Nav verlinkt (``_screen_nav()``), die Route selbst ist trotzdem
    rollenfrei erreichbar (analog zu Archive/Jobs — ein direkter Aufruf 404t
    nicht). ``workers`` trägt seit Batch 9 Punkt 3 zusätzlich eine synthetische
    Zeile für den Host selbst (``controller._host_worker_entry()``, ``__init__.py``)
    — ``WorkerRegistry`` kennt nur Knoten, die sich per Heartbeat gemeldet
    haben, der Host meldet sich nie bei sich selbst."""
    now = time.time() if now is None else now
    daemon_status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Nodes</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Nodes', daemon_status)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, poll_interval_s=status_poll_interval_s, job_status_poll_interval_s=job_status_poll_interval_s)}"
        f"{clients_fragment(workers, now)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


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
    beim Hover trotzdem den Status-Namen.

    X-Achsen-Labels tragen das Datum (``TT.MM HH:MM``) statt nur ``HH:MM``,
    sobald das Fenster mehr als einen Tag umfasst (Bibi4-Iteration, User-Fund:
    "bei ein oder mehreren Tagen muss in der X-Achse das Datum ... angezeigt
    werden" — reine Uhrzeit war über mehrere Tage sonst mehrdeutig, z. B. bei
    den groben Auflösungen 8h/1w oder 24h/1m). Bei ≤1 Tag bleibt die knappe
    ``HH:MM``-Form (unaufdringlich, keine Mehrdeutigkeit innerhalb eines
    Tages) — Spannweite kommt direkt aus den Labels selbst, kein zusätzlicher
    Parameter nötig."""
    if not labels:
        return '<div class="chart-wrap"><p class="out-empty">— noch keine Daten —</p></div>'
    spans_multiple_days = len(labels) > 1 and (labels[-1] - labels[0]) > 86400
    fmt = "%d.%m %H:%M" if spans_multiple_days else "%H:%M"
    tick_labels = [datetime.datetime.fromtimestamp(t).strftime(fmt) for t in labels]
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
    """Screen-Tabs (Feed · [Jobs] · Live-Log · API-Docs); der aktive ohne
    Link. **Home ist jetzt Feed** (PLAN-18 Stufe 18.3, löst die
    2026-07-04-Entscheidung „Home = Schedules" bewusst ab) — Schedules bleibt
    unter seiner eigenen Route erreichbar, ist nur nicht mehr ``/-/`` selbst.
    Jobs (PLAN-17 Stufe 17.2) zeigt den Lokal/Remote-Abgleich + Start-Button für
    /run. Daemon-Tab entfernt (PLAN-18 Stufe 18.4) — sein Inhalt (Status-
    Kacheln) lebt jetzt im Feed-Header, ``daemon_page()``/``_status_cards()``
    bleiben als Bausteine bestehen, nur die eigene Seite/der Tab fallen weg.

    Host (``/-/ui/schedules``) und Client (``/-/ui/jobs``) heißen im Tab jetzt
    beide "Jobs" (Bibi4-Iteration, User-Fund: "eine App", Host/Client sollen
    dieselbe Screen-Menge zeigen) — Routen/interne Namen bleiben unverändert,
    nur das Label vereinheitlicht sich. Kein Kollisionsrisiko: ``scheduler``
    und ``connect`` schließen sich gegenseitig aus (``roles.py``), es kann also
    nie beide gleichzeitig geben.

    Rollenabhängig ausgeblendet (PLAN-20 Befund 6): der Host-Jobs-Tab nur mit
    ``scheduler``-Rolle (die zugrundeliegenden ``/-/schedule``-Routen existieren
    serverseitig nur dann, s. ``app.py::_add_scheduler_routes`` — ohne Rolle
    wäre die Seite ohnehin nur ein 404). Der Client-Jobs-Tab nur mit
    ``connect``-Rolle (User-Entscheidung trotz Rückfrage: bewusst NICHT
    zusätzlich für reine Scheduler-Knoten wie sarasate — auch wenn der Screen
    dort technisch funktionieren würde).

    Archive-Tab (Bibi4-Iteration, User-Fund: Archive/Journal bzw. lokale Läufe
    auf einen eigenen Screen auslagern) für Host (``scheduler``-Rolle,
    ``archive_page()``/``/-/ui/archive``) UND Client (``connect``-Rolle,
    ``jobs_archive_page()``/``/-/ui/jobs/archive``) — dieselbe Beschriftung,
    unterschiedliche Routen/Inhalte, exakt wie beim Jobs-Tab. Kein
    Kollisionsrisiko aus demselben Grund (``scheduler``⊥``connect``)."""
    roles = roles or []
    tabs = [("Feed", "/-/")]
    if "scheduler" in roles:
        tabs.append(("Jobs", "/-/ui/schedules"))
        tabs.append(("Archive", "/-/ui/archive"))
        tabs.append(("Nodes", "/-/ui/clients"))
    if "connect" in roles:
        tabs.append(("Jobs", "/-/ui/jobs"))
        tabs.append(("Archive", "/-/ui/jobs/archive"))
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
    rechten Nav-Gruppe (Bibi4-Iteration: Toggles rechts, revidiert PLAN-21
    Befund 1). Als Text-Link gestylt, kein Button-Look (PLAN-19 Befund 7).
    Icon statt Text (Bibi4-Iteration, User-Fund: "Toggles über Icons") — ⏵
    (an, folgt live) / ⏸ (aus, pausiert), analog zum ☾/☀-Symbolwechsel von
    ``_theme_toggle()``. ``title`` trägt die textuelle Erklärung fürs Hover,
    da der Button sonst kein sichtbares Label mehr hat."""
    return ('<button id="follow" class="toggle on" onclick="bibiToggleFollow()" '
           'title="Follow: an (live folgen)">⏵</button>')


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


def _time_toggle() -> str:
    """Time-Toggle (Bibi4-Iteration, User-Fund: "Time: abs./rel./both" für
    die last/since- und next-Spalten) — 3-State-Zyklus abs → rel → both → abs,
    analog zum ☾/☀-Symbolwechsel von ``_theme_toggle()``. Startsymbol/-titel
    per ``_TIME_JS`` gesetzt (Default "both"), damit hier kein serverseitiger
    State nötig ist. Die drei Icons sind bewusst plain-Unicode (Geometric
    Shapes, wie ☾/☀ kein Emoji-Rendering) statt Uhr-Symbolen aus dem Emoji-
    Bereich, die auf den meisten Systemen farbig statt monochrom rendern."""
    return '<button id="time" class="toggle" onclick="bibiToggleTime()">◒</button>'


#: Time-Toggle: schaltet data-timeformat auf <html> zwischen "abs"/"rel"/"both"
#: um (s. _CSS für die .tt-abs/.tt-relonly/.tt-relboth-Sichtbarkeitsregeln),
#: persistiert in localStorage — analog zu _THEME_JS. Icons: ◐ (abs) / ◑ (rel)
#: / ◒ (both, Default).
_TIME_JS = """
(function(){
  const KEY = 'bibiTimeFormat';
  const ORDER = ['abs', 'rel', 'both'];
  const ICON = {abs: '◐', rel: '◑', both: '◒'};
  const TITLE = {abs: 'Zeit: absolut', rel: 'Zeit: relativ', both: 'Zeit: absolut + relativ'};
  const root = document.documentElement;
  function apply(mode){
    root.setAttribute('data-timeformat', mode);
    const b = document.getElementById('time');
    if (b) { b.textContent = ICON[mode]; b.title = TITLE[mode]; }
  }
  window.bibiToggleTime = function(){
    const cur = root.getAttribute('data-timeformat') || 'both';
    const next = ORDER[(ORDER.indexOf(cur) + 1) % ORDER.length];
    localStorage.setItem(KEY, next);
    apply(next);
  };
  apply(localStorage.getItem(KEY) || 'both');
})();
"""


def _header(active: str, status: dict | None = None) -> str:
    """Gemeinsame obere Navigationsleiste: links Titel + reine Tab-Leiste,
    rechts alle Toggles (FOLLOW/RESCAN/MAINT/Datum-Uhrzeit/THEME) — Bibi4-
    Iteration, User-Fund: "Tabs links, Toggles rechts" (löst die PLAN-21-
    Aufteilung ab, in der FOLLOW/RESCAN/MAINT noch links neben den Tabs
    standen). ``git_status`` fällt hier weg (PLAN-21 Befund 2, Sync-Dopplung:
    der Sync-Zustand steht jetzt nur noch in der Git-Karte, RESCAN zeigt
    wieder die generische Beschriftung). Rollen für ``_screen_nav()``
    (PLAN-20 Befund 6) kommen aus ``status["roles"]`` — schon vorhanden
    (``/-/status``), keine neue Datenquelle nötig."""
    roles = (status or {}).get("roles")
    left = f'<h1>bibi</h1>{_screen_nav(active, roles)}'
    right = (f'{_follow_toggle()}{_ops_handles(status)}{_time_toggle()}'
            f'{_live_clock()}{_theme_toggle()}')
    return (f'<header><div class="nav-left">{left}</div>'
            f'<div class="nav-right">{right}</div></header>')


def schedules_page(schedules: list[dict], typ: str | None = None,
                   status: str | None = None, now: float | None = None,
                   *, daemon_status: dict | None = None,
                   landings: list[dict] | None = None,
                   git_status: dict | None = None, host_url: str | None = None,
                   status_poll_interval_s: int = 30, job_status_poll_interval_s: int = 2,
                   bucket_minutes: int = _DEFAULT_RESOLUTION_MINUTES,
                   public_host: str = "localhost",
                   sparklines: dict[str, list[int]] | None = None) -> str:
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
        f"{_header('Jobs', daemon_status)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, poll_interval_s=status_poll_interval_s, job_status_poll_interval_s=job_status_poll_interval_s)}"
        f"{timeseries_fragment(landings or [], daemon_status.get('job_stats'), now, bucket_minutes=bucket_minutes)}"
        f"{_filter_bar(typ, status)}"
        f"{schedules_fragment(schedules, now, typ=typ, status=status, public_host=public_host, sparklines=sparklines)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


#: FOLLOW-Toggle: steuert ``window.bibiFollow`` (Trigger-Filter der Poll-Fragmente).
#: Vor htmx-Init gesetzt (im <head>), damit die Trigger den Startzustand sehen.
_FOLLOW_JS = """
window.bibiFollow = (localStorage.getItem('bibiFollow') ?? '1') === '1';
function bibiFollowIcon(on){ return on ? '⏵' : '⏸'; }
function bibiFollowTitle(on){ return on ? 'Follow: an (live folgen)' : 'Follow: aus (pausiert)'; }
function bibiToggleFollow(){
  window.bibiFollow = !window.bibiFollow;
  localStorage.setItem('bibiFollow', window.bibiFollow ? '1' : '0');
  const b = document.getElementById('follow');
  b.textContent = bibiFollowIcon(window.bibiFollow);
  b.title = bibiFollowTitle(window.bibiFollow);
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
  if (b && !window.bibiFollow){
    b.textContent = bibiFollowIcon(false); b.title = bibiFollowTitle(false); b.className = 'toggle';
  }
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
             status_poll_interval_s: int = 30, job_status_poll_interval_s: int = 2,
             client_rows: list[dict] | None = None) -> str:
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
        f"{feed_status_fragment(status, git_status, host_url, now, poll_interval_s=status_poll_interval_s, job_status_poll_interval_s=job_status_poll_interval_s, client_rows=client_rows)}"
        f"{_log_panel()}"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
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
                     "behind": "sync-behind", "diverged": "sync-conflict"}


def _lines_card(label: str, lines: list[str], sub: str | list[str] = "") -> str:
    """Karte mit mehreren linksbündigen Zeilen statt einem einzelnen Wert
    (PLAN-19 Befund 4) — ``lines`` sind bereits fertiges HTML (Farb-Spans/
    Links), werden hier nicht mehr escaped. Baustein für Host/Mode/Git im
    neuen 3-Karten-Feed-Header.

    ``sub`` ist entweder eine einzelne Zeile (str, wie bisher) oder mehrere
    (list[str], Bibi4-Iteration — Host-Kachel braucht jetzt drei eigene
    Zeilen statt einer " · "-verketteten) — jede wird als eigenes
    ``.sub``-Div gerendert, leere Einträge fallen weg."""
    subs = [sub] if isinstance(sub, str) else sub
    sub_html = "".join(f'<div class="sub">{_e(s)}</div>' for s in subs if s)
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
        # Bibi4-Iteration, User-Fund: "next in ..." wandert von der Job-
        # Status-Kachel hierher, plus Anzahl verbundener Clients (Connected-
        # Clients-Screen, Stufe 2), später ergänzt um den Complete-Zähler
        # (zweite Iteration, User-Fund: "nur beim Host gehört 785 complete
        # ebenfalls nach HOST, sarasate, next in 2 min - 2 clients
        # connected") — alle drei ausschließlich eine Host-Perspektive,
        # deshalb nur in diesem Zweig, nicht bei "Client" unten. Zwei Zeilen
        # (dritte Iteration, User-Fund: "schreib '2 clients connected' in
        # der ersten Zeile und 'next Job in 1 min, 11 complete' in der
        # zweiten Zeile" — next/complete auf eine Zeile zusammengelegt).
        job_stats = status.get("job_stats") or {}
        workers = status.get("workers") or []
        n_clients = sum(1 for w in workers if not w.get("stale"))
        complete = job_stats.get("complete_since_uptime", 0)
        subs = [
            f"{n_clients} clients connected",
            f"next Job {_until(job_stats.get('next_due_at'), now)}, {complete} complete",
        ]
        return _lines_card("Host", [_e(own)] if own else ["—"], sub=subs)
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
    ``ahead: <hash> (N)``, ``diverged: <hash> (+N, -M)`` — ahead UND behind
    zugleich > 0, kein unaufgelöster Merge-Konflikt mit ``<<<<<<<``-Markern
    (``git_status.working_tree_status()``). Hieß bis Batch 7 Stufe 3
    ``"conflict"`` — umbenannt (User-Fund: "ich verstehe die Bedeutung von
    conflict und sync: !conflict nicht"), derselbe Wert reist unverändert bis
    in die CLI-Statuszeile (``statusline_cmd.py``s ``_SYNC_COLOR``) — beide
    Oberflächen lesen denselben ``WorkingTreeStatus.sync``-Wert, keine
    getrennte Semantik. Ohne ``oid`` (ältere Aufrufer/Tests) bleibt es beim
    reinen Zustandswort."""
    if not oid:
        return sync
    short = oid[:7]
    if sync == "behind":
        return f"{sync}: {short} ({behind})"
    if sync == "ahead":
        return f"{sync}: {short} ({ahead})"
    if sync == "diverged":
        return f"{sync}: {short} (+{ahead}, -{behind})"
    return f"{sync}: {short}"


def _git_segment_card(git_status: dict | None) -> str:
    """Git-Kachel: Tree + Sync als Key/Value-Grid, Branch als Sub-Zeile
    (PLAN-19 Befund 4, verfeinert PLAN-21 Befund 7: Grid-Optik statt
    gestapelter Zeilen). ``git_status`` ist bereits ein Dict (``{"tree",
    "sync", "branch", "oid", "ahead", "behind"}``, aus
    ``bibi.git_status.working_tree_status()`` — rein lokal, kein Heartbeat/
    Netzwerk nötig). ``None`` (kein Git-Repo) → leere Kachel mit „—".

    Dritte Zeile "Konflikte" (PLAN-30 Ebene 3), nur wenn ``git_status["stuck"]``
    > 0 — die Anzahl der Job-Branches, die nach 3 Fehlschlägen aus dem
    automatischen Merge-back-Retry eskaliert wurden (``merge_quarantine.py``).
    Happy Path bleibt unverändert zweizeilig (+ Branch-Sub-Zeile)."""
    if git_status is None:
        return _lines_card("Git", ["—"])
    tree, sync = git_status["tree"], git_status["sync"]
    sync_value = _format_sync_value(
        sync, git_status.get("oid"), git_status.get("ahead", 0), git_status.get("behind", 0))
    rows = [
        ("Tree", tree, _TREE_LABEL_CLASS[tree]),
        ("Sync", sync_value, _SYNC_LABEL_CLASS[sync]),
    ]
    stuck = git_status.get("stuck", 0)
    if stuck:
        rows.append(("Konflikte", str(stuck), "sync-conflict"))
    branch = git_status.get("branch")
    return _kv_card("Git", rows, sub=f"Branch {branch}" if branch else "")


_JOB_STATUS_WAITING = ("pending", "deferred", "failed")
_JOB_STATUS_RUNNING = ("running", "awaiting")
_JOB_STATUS_STOPPED = ("inactive", "zombie", "error", "killed")
_JOB_STATUS_ROWS = (("Waiting", _JOB_STATUS_WAITING), ("Running", _JOB_STATUS_RUNNING),
                    ("Stopped", _JOB_STATUS_STOPPED))
#: Spaltenreihenfolge der Matrix — dieselben drei Werte wie ``models.display_kind()``.
_JOB_STATUS_KINDS = (("job", "Job"), ("claude", "Claude"), ("app", "App"))


def _job_status_card(job_stats: dict, now: float) -> str:
    """Job-Status-Kachel (PLAN-26 Befund 3) — 4. Kachel neben Host/Mode/Git,
    nur gerendert wenn ``job_stats`` vorhanden ist (``scheduler``-Rolle, wie
    ``job_stats`` selbst — Client-Darstellung laut User bewusst "später").

    Matrix statt der bisherigen 2x2-Aggregation (Bibi4-Iteration, User-Fund:
    "Apps enden nicht" — fachlich eigene Kategorie, siehe ``models.display_kind()``):
    3 Zeilen Waiting/Running/Stopped x 3 Spalten job/claude/app, aus
    ``job_stats["counts_by_kind"]`` (``job_db.status_counts_by_kind()``).

    Keine Fußzeile mehr (zweite Bibi4-Iteration, User-Fund: "nur beim Host
    gehört 785 complete ebenfalls nach HOST, sarasate, next in 2 min - 2
    clients connected") — der Complete-Zähler ist zur Host-Kachel gewandert,
    zusammen mit dem schon vorher dorthin verschobenen "next ..." (s.
    ``_host_card()``). Damit ist diese Kachel jetzt reine Matrix ohne Sub-
    Zeile, symmetrisch zu ``_client_job_status_card()``.

    Kein eigener Titel mehr (Bibi4-Iteration, User-Fund: "entferne die
    Überschrift Job Status und beginne ganz oben mit JOB CLAUDE APP") — die
    Kopfzeile der Matrix trägt die Beschriftung jetzt selbst, spart eine Zeile
    Höhe gegenüber Mode/Git."""
    by_kind = job_stats.get("counts_by_kind") or {}

    def cell(kind: str, statuses: tuple[str, ...]) -> int:
        counts = by_kind.get(kind) or {}
        return sum(counts.get(s, 0) for s in statuses)

    header = '<div class="jsg-h"></div>' + "".join(
        f'<div class="jsg-h">{label}</div>' for _, label in _JOB_STATUS_KINDS)
    rows = "".join(
        f'<div class="jsg-k">{row_label}</div>' + "".join(
            f'<div class="jsg-v">{cell(kind, statuses)}</div>' for kind, _ in _JOB_STATUS_KINDS)
        for row_label, statuses in _JOB_STATUS_ROWS)
    return f'<div class="card"><div class="jobstatus-grid">{header}{rows}</div></div>'


def job_status_fragment(job_stats: dict | None, now: float, *, poll_interval_s: int = 2) -> str:
    """Eigenständig pollende Job-Status-Kachel (Bibi4-Iteration, User-Fund:
    "Job Status ändert sich oft, und eine häufigere Abfrage wäre gut ... da es
    sich um eine sqlite db Abfrage handelt, sollte eine 1-2 Sekunden Abfrage
    aber möglich sein") — löst sich aus dem bisherigen 30s-Bundle von
    ``feed_status_fragment()``: anders als Host/Mode/Git (die am
    ``git status``-Subprozess hängen) ist Job Status eine reine ``job_db``-
    SQLite-Abfrage, dieselbe Kosten-Klasse wie die 2s-Polls der Schedules-/
    Jobs-Tabelle (``_POLL``). Nur gerendert, wenn ``job_stats`` vorhanden ist
    (``scheduler``-Rolle, wie bisher PLAN-26 Befund 3) — sonst leerer String,
    kein leerer Poll-Container. ``poll_interval_s`` kommt vom Aufrufer
    (Default 2s, konfigurierbar über ``config.job_status_poll_interval()``/
    ``BIBI_JOB_STATUS_POLL_INTERVAL``)."""
    if job_stats is None:
        return ""
    attrs = (f'id="jobstatuscard" hx-get="/-/ui/feed/jobstatus" '
            f'hx-trigger="every {poll_interval_s}s [window.bibiFollow]" hx-swap="outerHTML"')
    return f'<div {attrs}>{_job_status_card(job_stats, now)}</div>'


#: Zeilen der Client-Matrix — dieselbe Form wie _JOB_STATUS_ROWS (Label,
#: Menge matchender git_status-Werte), nur git-Gesundheit statt Lifecycle.
#: "clean" bewusst keine eigene Zeile (etablierte Konvention: der stille
#: Normalzustand bleibt unsichtbar, s. _jobs_row()-Docstring) — anders als
#: bei _JOB_STATUS_ROWS sind diese drei Zeilen NICHT erschöpfend für "alle
#: Jobs", sondern zeigen nur die vom Normalzustand abweichenden.
_CLIENT_STATUS_ROWS = (("New", ("new",)), ("Modified", ("modified",)), ("Conflict", ("conflict",)))


def _client_job_status_card(rows: list[dict]) -> str:
    """4. Stat-Karte für den Client (Bibi4-Iteration, User-Brainstorm: "was
    zeigen wir an Stelle der Host Job Status Card beim Client?") — Gegenstück
    zu ``_job_status_card()``, aber mit Repo-Struktur- statt Live-Scheduling-
    Daten: ``job_stats``/``counts_by_kind`` existiert nur für die
    ``scheduler``-Rolle (job_db-gestützt); der Client hat stattdessen die
    ohnehin schon geladene Discovery-Liste (dieselbe wie ``_jobs_table()``).

    Echte Matrix seit der Bibi4-Iteration (User-Fund: "mir gefällt die
    schnöde Zusammenfassung nicht, ich hätte gerne die Matrix immer wie beim
    Host") — löst die vorherige, auf eine Fließtext-Subline reduzierte
    Fassung ab: 3 Zeilen (New/Modified/Conflict, **immer alle drei gezeigt,
    auch bei 0** — explizite User-Entscheidung, analog zu ``_JOB_STATUS_ROWS``,
    das WAITING/RUNNING/STOPPED ebenfalls unbedingt zeigt) x 3 Spalten
    (Job/Claude/App). Keine Fußzeile mehr — "next in ..."/Client-Count
    wanderten in die Host-Kachel (``_host_card()``)."""
    def cell(kind: str, statuses: tuple[str, ...]) -> int:
        return sum(
            1 for r in rows
            if models.display_kind(r.get("payload"), r.get("app_port")) == kind
            and r.get("git_status", "clean") in statuses
        )
    header = '<div class="jsg-h"></div>' + "".join(
        f'<div class="jsg-h">{label}</div>' for _, label in _JOB_STATUS_KINDS)
    body = "".join(
        f'<div class="jsg-k">{row_label}</div>' + "".join(
            f'<div class="jsg-v">{cell(kind, statuses)}</div>' for kind, _ in _JOB_STATUS_KINDS)
        for row_label, statuses in _CLIENT_STATUS_ROWS)
    return f'<div class="card"><div class="jobstatus-grid">{header}{body}</div></div>'


def feed_status_fragment(
    status: dict, git_status: dict | None, host_url: str | None, now: float,
    *, poll_interval_s: int = 30, job_status_poll_interval_s: int = 2,
    client_rows: list[dict] | None = None,
) -> str:
    """Die Feed-Header-Kacheln (PLAN-19 Befund 4: Host-Connection, Mode,
    Git — löst die bisherigen 6 Kacheln von PLAN-18 Stufe 18.3 ab, u. a. fällt
    die Rollen-Kachel weg, deckungsgleich mit der ursprünglichen Umbau-Vorgabe
    „Rollen sind eh klar"). Baut **nicht** mehr auf ``_status_card_list()``
    auf (die bleibt unverändert für ``_status_cards()``/``daemon_page()`` als
    Baustein bestehen, auch ohne eigene Route seit PLAN-18 Stufe 18.4).

    Optionale 4. Kachel (PLAN-26 Befund 3) lebt seit der Bibi4-Iteration in
    ``job_status_fragment()`` — eigener, schnellerer Self-Poll statt Teil
    dieses 30s-Bundles, s. dortiger Docstring. Hier nur noch als verschachtelter
    Baustein eingehängt (``.statuscards`` bleibt das Grid, der Job-Status-
    Poll-Container ist einfach ein weiteres Grid-Kind).

    ``client_rows`` (Bibi4-Iteration, User-Brainstorm): Gegenstück für
    Knoten ohne ``scheduler``-Rolle — dieselbe Discovery-Liste wie
    ``_jobs_table()``, hier nur als Zähl-Grundlage für
    ``_client_job_status_card()``. Anders als der Host-Job-Status (eigener
    2s-Poll, DB-Query) bleibt das Teil dieses 30s-Bundles: die zugrunde
    liegende Discovery+Git-Status-Abfrage ist dieselbe Kostenklasse wie die
    Git-Karte selbst, ändert sich zudem selten (Repo-Struktur, nicht Live-
    Scheduling) — kein eigener schneller Poll nötig. Wenn weder ``job_stats``
    noch ``client_rows`` vorhanden sind (z. B. Job-/Run-Detailseiten), bleibt
    die 4. Kachel schlicht weg, wie bisher.

    Self-pollend seit PLAN-25 Befund 4 (User-Fund: "Header kontinuierlich
    aktualisieren") — vorher nur beim initialen Seitenaufbau gerendert.
    Bewusst **kein** festes 2s-Intervall wie ``#schedules`` für Host/Mode/Git:
    die Git-Karte hängt an einem ``git status``-Subprozess, nicht billig genug
    für Sekundentakt. ``poll_interval_s`` kommt vom Aufrufer (Default 30s,
    konfigurierbar über ``config.status_poll_interval()``/
    ``BIBI_STATUS_POLL_INTERVAL``), damit diese Funktion config-frei bleibt."""
    cards = [_host_card(status, host_url, now), _mode_card(status, now),
             _git_segment_card(git_status)]
    if status.get("job_stats") is not None:
        job_card = job_status_fragment(status.get("job_stats"), now,
                                       poll_interval_s=job_status_poll_interval_s)
    elif client_rows is not None:
        job_card = _client_job_status_card(client_rows)
    else:
        job_card = ""
    # "bibiMaintChanged from:body" (Bibi4-Iteration, User-Fund: "ein Klick auf
    # Maintenance muss ein Update der Mode Card nach sich ziehen") — der MAINT-
    # Toggle (_OPS_HANDLES_JS) lebt im gemeinsamen Header, unabhängig davon, ob
    # diese Kachel auf der aktuellen Seite überhaupt existiert (z. B. Job-
    # Detail hat keine); ohne Treffer im DOM ist das Event einfach ein No-op.
    attrs = (f'id="feedstatus" hx-get="/-/ui/feed/status" '
            f'hx-trigger="every {poll_interval_s}s [window.bibiFollow], '
            f'bibiMaintChanged from:body" hx-swap="outerHTML"')
    return f'<div {attrs}><div class="statuscards">{"".join(cards)}{job_card}</div></div>'


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
        f"<script>{_TIME_JS}</script>"
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
    # Bibi4-Iteration, User-Fund: "sind sie lokal modifiziert, konfliktär,
    # fehlen?" — konfliktär war zuvor nicht von modified unterschieden
    # (local_files_status(), git_status.py).
    "conflict": ("chip conflict", "konfliktär"),
    "clean": ("chip clean", "unverändert"),
}

_SPARK_W, _SPARK_H = 72, 20


def _sparkline_svg(counts: list[int]) -> str:
    """Kleines Inline-SVG für die Jobs-Sparkline (Bibi4-Iteration, User-Fund:
    "eine Sparkline, die die durch den Agenten verursachten git Änderungen
    repräsentiert"). Reine Darstellung — die Zähl-Buckets kommen von
    ``feed.activity_series_by_prefix()``. Leerer String, wenn nirgends
    Aktivität war (kein Bild statt einer flachen Nulllinie)."""
    if not counts or not any(counts):
        return ""
    peak = max(counts)
    n = len(counts)
    step = _SPARK_W / max(n - 1, 1)
    points = " ".join(
        f"{i * step:.1f},{_SPARK_H - 1 - (c / peak) * (_SPARK_H - 2):.1f}"
        for i, c in enumerate(counts)
    )
    return (f'<svg class="sparkline" viewBox="0 0 {_SPARK_W} {_SPARK_H}" '
           f'width="{_SPARK_W}" height="{_SPARK_H}" preserveAspectRatio="none">'
           f'<polyline points="{points}" fill="none" stroke="#5fb37a" '
           f'stroke-width="1.5"/></svg>')


def _sparkline_cell(slug: str, series_by_slug: dict[str, list[int]] | None) -> str:
    """``hx-preserve``-Zelle (Bibi4-Iteration) — dieselbe Technik wie
    ``.liveterm``-Boxen (render.py, ``_LIVE_JS``-Kommentar: "hx-preserve hält
    die Box ... über den 2s-Poll am Leben"): die zugrunde liegende Aggregation
    (``feed.collect_commits()`` + ``agent_commit_shas()`` über 30 Tage) ist
    Git-Subprozess-lastig, dieselbe Kostenklasse wie die Git-Karte — zu teuer
    für den 2s-Tabellen-Poll. ``series_by_slug`` kommt deshalb nur vom
    initialen Seitenaufbau (``jobs_page()``); der 2s-Self-Poll
    (``jobs_board()``) übergibt ``None`` und rendert eine leere Zelle mit
    derselben ``id`` — htmx behält dank ``hx-preserve`` das schon vorhandene
    Sparkline-Element, statt es durch die leere Variante zu ersetzen.
    Aktualisiert sich dadurch bei Seitenaufruf/Reload, nicht bei jedem
    Tabellen-Tick."""
    svg = _sparkline_svg((series_by_slug or {}).get(slug, []))
    return f'<span id="spark-{_e(slug)}" hx-preserve="true">{svg}</span>'


def _sparkline_cell_lazy(slug: str, index: int = 0) -> str:
    """Entkoppelter Platzhalter (Bibi4-Iteration, User-Fund: "Sparklines
    dauern beim Reload immer") — löst ``jobs_screen()``s bisherige, den
    kompletten Seitenaufbau blockierende ``_job_sparkline_series()``-
    Berechnung ab. Statt die Serie eager mitzuliefern, feuert die Zelle
    selbst einen ``hx-get`` gegen eine eigene Pro-Slug-Route
    (``/-/ui/jobs/{slug}/sparkline``), sobald sie ins DOM eingehängt wird.
    Analog zum bestehenden ``hx-trigger=\"revealed\"``-Muster in
    ``_journal_sentinel_row()``, hier ``load`` statt ``revealed`` — eine
    normale Jobs-Tabelle braucht kein Scroll-Gating, alle Zeilen sind eh
    sichtbar.

    ``delay:{index*120}ms`` (Bugfix, User-Fund 2026-07-22: "zieht meinen
    ganzen Rechner in die Knie. Immer noch!", eskaliert bis zum Browser-Tab-
    Crash) — ohne Staffelung feuern bei N Zeilen alle N ``hx-get``s im
    selben Tick (das war die ursprüngliche, im Case-Dokument selbst
    gewünschte "eine nach der anderen"-Ladefolge, die hier zuvor NICHT
    umgesetzt war). Jede Anfrage bringt unabhängig von
    ``_job_sparkline_series()``s Cache/Lock ihre eigene
    ``jobs_sparkline()``-Route-Kosten mit (Discovery-Scan über
    ``_local_schedules()``) — N gleichzeitige Anfragen multiplizieren das,
    N gestaffelte über ~120ms Abstand nicht. Reiner Anzeige-Effekt (die
    Zellen füllen sich sichtbar nacheinander statt schlagartig), keine
    Server-Änderung nötig.

    ``hx-preserve=\"true\"`` **hier auch schon im unaufgelösten Zustand**
    (Regression, User-Fund nach Deploy: "Sparklines erscheinen jetzt gar
    nicht mehr") — ohne das riss der 2s-Self-Poll (``#jobsboard``,
    ``jobs_board()``, rendert jede Zeile mit ``sparklines=None`` neu) diesen
    Platzhalter samt seines noch laufenden ``hx-get``s aus dem DOM, sobald
    der Poll vor dessen Auflösung feuerte (praktisch immer bei mehreren
    Zeilen: der Poll tickt alle 2s, mehrere gleichzeitige Pro-Slug-Requests
    konkurrieren um Browser-Verbindungen + den Cache-Lock). Die dadurch neu
    eingesetzte, leere ``_sparkline_cell(slug, None)``-Zelle hat zwar selbst
    ``hx-preserve``, aber das schützt nur VOR zukünftigen Swaps, nicht
    rückwirkend — die Zeile blieb ab dann für immer leer, ohne dass je
    wieder ein Ladeversuch angestoßen wurde. Jetzt tragen beide Zustände
    (unaufgelöst hier, aufgelöst in ``_sparkline_cell()``) dieselbe ``id``
    UND ``hx-preserve`` — welcher Zustand auch gerade im DOM steht, ein
    Ancestor-Poll lässt ihn unangetastet, bis der eigene ``hx-get`` (falls
    noch unaufgelöst) durch ist und sich selbst per ``hx-swap=\"outerHTML\"``
    ersetzt."""
    s = _e(slug)
    return (f'<span id="spark-{s}" hx-preserve="true" '
           f'hx-get="/-/ui/jobs/{s}/sparkline" hx-trigger="load" hx-swap="outerHTML"></span>')


def _jobs_type_cell(row: dict, public_host: str) -> str:
    """Type-Zelle nur für die Jobs-Tabelle (PLAN-29 Befund 2, User-Fund:
    "Type, bei Apps mit Port und als Link (auch wenn die App down ist)").
    Bewusst **eigenständig** von ``_effective_sched_type()``/
    ``models.effective_kind()`` — PLAN-25 Befund 7 entfernte "app" dort
    absichtlich aus Schedules-Übersicht/Filter (User-Entscheidung: "Jobs mit
    Port und Prefix sollen einfach als Jobs erscheinen"). Hier, in der
    separaten Jobs-Tabelle, soll ein App-Job weiterhin als "app" + Link
    erkennbar sein — rührt an der PLAN-25-Vereinfachung nicht. Der Link steht
    unbedingt (kein Live-Check), auch wenn die App gerade nicht läuft."""
    app_port = row.get("app_port")
    kind = models.display_kind(row.get("payload"), app_port)
    if kind == "app":
        href = _e(f"http://{public_host}:{app_port}/")
        return f'<a href="{href}" target="_blank" rel="noopener">app :{app_port}</a>'
    return kind


def _jobs_row(row: dict, local_runs: dict[str, dict], now: float,
              *, public_host: str = "localhost", sparklines: dict[str, list[int]] | None = None,
              lazy_sparklines: bool = False, index: int = 0) -> str:
    """Eine Zeile: Slug(+Git-Chip)/Type/Status/last-since/Runtime (Bibi4-
    Iteration, User-Fund: "Slug/Type/Status/last-since/Runtime" — löst die
    vorherige 7-Spalten-Form (eigene Git-Spalte, getrennte Start/Ende-Spalten)
    ab, analog zur Host-Schedules-Tabelle. Slug verlinkt auf die lokale Job-
    Detailseite; Status verlinkt auf den konkreten letzten Lauf
    (/-/ui/run/{jid}), sofern schon mal gelaufen. ``row["live"]`` (PLAN-21
    Befund 10, 2. Nachtrag): läuft der Job gerade, geht der Status-Link auf
    die Detailseite (dort lebt der Live-Output), last/Runtime zeigen den
    laufenden Versuch (Laufzeit bis ``now``) statt des letzten
    ABGESCHLOSSENEN Laufs.

    last/since ist EINE Spalte (nicht mehr getrennt Start/Ende), analog zu
    ``_sched_row()``s ``last_run_at``: laufend → Start des aktuellen Versuchs,
    sonst → Ende des letzten abgeschlossenen Laufs — dieselbe „ein Zeitpunkt,
    zwei Bedeutungen je nach Status"-Logik, die die Host-Tabelle längst hat.

    Git-Status (Bibi4-Iteration, User-Fund: "sind sie lokal modifiziert,
    konfliktär, fehlen?", präzisiert: "es genügt new/modified/clean, wobei
    clean als Chip gar nicht angezeigt wird" + "plus konfliktär") sitzt jetzt
    als Chip direkt am Slug statt in einer eigenen Spalte — "clean" (der
    Normalzustand, laut User bewusst der leise Default) zeigt gar keinen
    Chip, "fehlen" bleibt bewusst außen vor (frühere Entscheidung, s.
    ``local_files_status()``-Docstring, git_status.py)."""
    slug = row["slug"]
    s = _e(slug)
    live = row.get("live")
    lr = local_runs.get(slug)
    jid = lr.get("id") if lr else None

    slug_cell = f'<a class="slug" href="/-/ui/jobs/detail/{s}">{s}</a>'
    git_status = row.get("git_status", "clean")
    if git_status != "clean":
        cls, label = _GIT_STATUS_LABEL.get(git_status, ("chip", _e(str(git_status))))
        slug_cell += f' <span class="{cls}">{label}</span>'

    if live:
        # PLAN-27 Befund 4, User-Fund: "der Status awaiting wird in /ui/jobs
        # nicht angezeigt" — live["status"] kommt jetzt aus local_runs_live()
        # (worker.py), analog zu _local_job_meta()s Fallunterscheidung.
        #
        # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED
        # nie im Dashboard gesehen"): dieselbe Kollabierung wie in
        # _local_job_view() — jeder Live-Status außer awaiting wurde hart zu
        # "running", obwohl live["status"] den echten Wert (deferred/failed,
        # seit dem _PINNED_LIVE_STATUSES-Fix hier überhaupt erst sichtbar)
        # längst trägt.
        st = live.get("status") or "running"
        status_cell = (f'<a class="rowlink" href="/-/ui/jobs/detail/{s}">'
                       f'<span class="st {st}">{st}</span></a>')
        started_at = live.get("started_at")
        last_cell = _time_toggle_cell(started_at, now, rel_fn=_ago)
        runtime_cell = _human_duration(now - started_at) if started_at is not None else "—"
    elif jid is not None:
        status_cell = (f'<a class="rowlink" href="/-/ui/run/{jid}">'
                       f'<span class="st {_e(lr["status"])}">{_e(lr["status"])}</span></a>')
        last_cell = _time_toggle_cell(lr.get("finished_at"), now, rel_fn=_ago)
        runtime_cell = _duration_cell(lr)
    else:
        status_cell = '<span class="side-empty">noch nie lokal gelaufen</span>'
        last_cell = runtime_cell = "—"

    type_cell = _jobs_type_cell(row, public_host)
    spark_cell = (_sparkline_cell_lazy(slug, index) if lazy_sparklines
                  else _sparkline_cell(slug, sparklines))

    return (f"<tr><td>{slug_cell}</td><td>{type_cell}</td><td>{status_cell}</td>"
            f"<td>{last_cell}</td><td>{runtime_cell}</td><td>{spark_cell}</td></tr>")


def _jobs_table(rows: list[dict], local_runs: dict[str, dict], now: float,
                *, public_host: str = "localhost", sparklines: dict[str, list[int]] | None = None,
                lazy_sparklines: bool = False) -> str:
    if not rows:
        return '<p class="out-empty">— keine Job-MDs im Repository gefunden —</p>'
    body = "".join(_jobs_row(r, local_runs, now, public_host=public_host, sparklines=sparklines,
                            lazy_sparklines=lazy_sparklines, index=i)
                  for i, r in enumerate(rows))
    return (
        '<table><thead><tr><th>Slug</th><th>Type</th><th>Status</th>'
        '<th>last / since</th><th>Runtime</th><th>Activity</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def jobs_fragment(
    rows: list[dict], local_runs: dict[str, dict],
    *, now: float | None = None, public_host: str = "localhost",
    sparklines: dict[str, list[int]] | None = None,
    lazy_sparklines: bool = False,
) -> str:
    """Der austauschbare Jobs-Kern (``#jobsboard``): lokale Job-MDs + Git-
    Status + letzter Start/Ende/Laufzeit je Zeile (PLAN-21 Befund 10 — löst
    die vorherige Lokal/Remote-Abgleich-Tabelle ab, kein Netzaufruf/Remote-
    Bezug mehr, dient ausschließlich dem Review der lokalen Repository-
    Realität; PLAN-28 User-Feedback: kein Start-CTA mehr hier, Start gibt es
    nur noch auf der Job-Detailseite). Self-pollt wie die anderen Screens
    (PLAN-17 Stufe 17.2), damit ein anderswo (Detailseite, CLI) gestarteter
    Lauf ohne Warten sichtbar wird.

    "Lokale Läufe" (die frühere zweite Sektion hier) lebt seit der Bibi4-
    Iteration auf einem eigenen Screen (``jobs_archive_fragment()``/
    ``jobs_archive_page()``, User-Fund: "der untere Abschnitt lokale Läufe
    wandert in den eigenen Screen Archive") — löst PLAN-29 Befund 1 (2
    Panel-Cards hier) auf 1 Panel-Card ab, analog zu ``schedules_fragment()``
    beim Host.

    ``sparklines`` (Bibi4-Iteration, User-Fund: "eine Sparkline ... git
    Änderungen") kommt nur vom initialen Seitenaufbau (``jobs_page()``) — der
    2s-Self-Poll hier übergibt bewusst ``None``, s. ``_sparkline_cell()``-
    Docstring (hx-preserve, zu teuer für den Sekundentakt).

    ``lazy_sparklines`` (zweite Bibi4-Iteration, User-Fund: "Sparklines dauern
    beim Reload immer") — der initiale Seitenaufbau selbst rechnet die Serie
    nicht mehr, sondern rendert nur noch Platzhalter, s. ``_sparkline_cell_lazy()``."""
    now = time.time() if now is None else now
    return (
        f'<div id="jobsboard" hx-get="/-/ui/jobs/board" hx-trigger="{_POLL}" hx-swap="outerHTML">'
        '<div class="panel-card"><h2>Jobs</h2>'
        f"{_jobs_table(rows, local_runs, now, public_host=public_host, sparklines=sparklines, lazy_sparklines=lazy_sparklines)}</div>"
        "</div>"
    )


def _client_archive_row(r: dict, now: float) -> str:
    """Eine Archive-Zeile (Client) — Slug/Type/Status/last-since/Runtime/next,
    dieselbe Spaltenstruktur wie ``_sched_row()`` (Host), aus einem Journal-
    Run-Dict statt einem Schedule-Dict. "Type" nutzt ``models.effective_kind()``
    statt ``display_kind()`` — die journal-Tabelle trägt kein ``app_port`` (nur
    ``payload``, s. schema.sql), genau wie beim Host-Archiv/Journal
    (``_sched_row()`` selbst nutzt ebenfalls nur ``_effective_sched_type()``).

    "last/since" (Bibi4-Iteration, User-Fund: fehlendes Datum/Uhrzeit im
    Client-Archive) nutzt ``finished_at`` über denselben ``_time_toggle_cell()``
    wie der Host — für einen archivierten Lauf ist "wann fertig" das Analogon
    zu "letzter Lauf".

    "next" ist beim Client immer "—" (User-Fund: einmalige /run-Läufe kennen
    keinen künftigen Termin) — bewusst sichtbar mit "—" statt ausgeblendet
    (Bibi4-Iteration, User-Fund "eine App": nicht verfügbare Spalten disabled/
    leer statt zu verschwinden, dieselbe Haltung wie beim MAINT-Toggle)."""
    slug = _e(r.get("slug"))
    kind = _e(models.effective_kind(r.get("payload")))
    status = _e(r.get("status"))
    when = _time_toggle_cell(r.get("finished_at"), now, rel_fn=_ago)
    runtime = _duration_cell(r)
    jid = r.get("id")
    slug_cell = (f'<a class="slug" href="/-/ui/run/{jid}">{slug}</a>'
                if jid is not None else slug)
    status_cell = (f'<a class="st {status}" href="/-/ui/run/{jid}">{status}</a>'
                  if jid is not None else f'<span class="st {status}">{status}</span>')
    when_cell = (f'<a class="rowlink" href="/-/ui/run/{jid}">{when}</a>'
                if jid is not None else when)
    return (
        "<tr>"
        f"<td>{slug_cell}</td>"
        f'<td class="kind">{kind}</td>'
        f"<td>{status_cell}</td>"
        f"<td>{when_cell}</td>"
        f"<td>{runtime}</td>"
        "<td>—</td>"
        "</tr>"
    )


def _client_archive_table(runs: list[dict], now: float) -> str:
    if not runs:
        return '<p class="out-empty">— keine lokalen Läufe —</p>'
    rows = "".join(_client_archive_row(r, now) for r in runs)
    return ('<table class="sched"><thead><tr><th>Slug</th><th>Type</th><th>Status</th>'
            f'<th>last/since</th><th>runtime</th><th>next</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def jobs_archive_fragment(runs: list[dict], now: float | None = None) -> str:
    """Self-pollender Archive-Screen-Kern (Client) — Bibi4-Iteration, User-Fund:
    "der untere Abschnitt lokale Läufe wandert in den eigenen Screen Archive".
    ``runs`` ist dieselbe flache Journal-Liste wie zuvor für "Lokale Läufe"
    (``client.run_journal()``), jetzt ohne die frühere 20er-Deckelung und in
    Tabellenform (Slug/Type/Status/Runtime/next) statt der alten Zeilen-
    Ansicht — dieselbe Spaltensprache wie das Host-Archiv. Ziel =
    ``/-/ui/jobs/archive/list``."""
    now = time.time() if now is None else now
    body = f'<h2>Archive ({len(runs)})</h2>' + _client_archive_table(runs, now)
    attrs = (f'id="archive" hx-get="/-/ui/jobs/archive/list" '
            f'hx-trigger="{_POLL}" hx-swap="outerHTML"')
    return f'<div {attrs}><div class="panel-card">{body}</div></div>'


def jobs_archive_page(runs: list[dict], now: float | None = None,
                      *, daemon_status: dict | None = None, git_status: dict | None = None,
                      host_url: str | None = None, status_poll_interval_s: int = 30,
                      job_status_poll_interval_s: int = 2,
                      client_rows: list[dict] | None = None) -> str:
    """Archive-Screen (Client, Bibi4-Iteration) — eigene Seite für die lokale
    Lauf-Historie, abgetrennt von der Jobs-Liste auf ``/-/ui/jobs``. Dieselben
    Nav/Ops-Bausteine wie jede andere Seite (``_header()``), analog zu
    ``archive_page()`` (Host) — inklusive der Status-Kacheln, die hier
    ebenfalls fehlten (s. ``archive_page()``-Docstring)."""
    now = time.time() if now is None else now
    daemon_status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Archive</title>"
        f"<script>{_FOLLOW_JS}</script>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Archive', daemon_status)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, poll_interval_s=status_poll_interval_s, job_status_poll_interval_s=job_status_poll_interval_s, client_rows=client_rows)}"
        f"{jobs_archive_fragment(runs, now)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


def jobs_page(
    rows: list[dict], local_runs: dict[str, dict],
    *, daemon_status: dict | None = None, git_status: dict | None = None,
    host_url: str | None = None, status_poll_interval_s: int = 30,
    job_status_poll_interval_s: int = 2,
    now: float | None = None, public_host: str = "localhost",
    sparklines: dict[str, list[int]] | None = None,
    lazy_sparklines: bool = False,
) -> str:
    """Jobs-Screen (PLAN-17 Stufe 17.2, umgebaut PLAN-21 Befund 10): lokale
    Repository-Realität + Git-Status + letzter Start/Ende/Laufzeit je Zeile.
    Rein lokal — funktioniert auch auf einem reinen Client (kein Scheduler/
    Worker im Ruhezustand), ohne je den Scheduler zu kontaktieren. Status-
    Kacheln (Host/Mode/Git/Job-Status) seit demselben ``feed_status_fragment()``
    wie ``/-/``/``/-/ui/schedules``/Live-Log (PLAN-28 User-Feedback: "Der
    Header soll auch auf der Client Job Seite angezeigt werden" — PLAN-27
    Befund 2 hatte das nur fürs Live-Log erledigt). Lokale Lauf-Historie lebt
    seit der Bibi4-Iteration auf ``jobs_archive_page()``, s. dortiger
    Docstring."""
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
        f"{feed_status_fragment(status, git_status, host_url, now, poll_interval_s=status_poll_interval_s, job_status_poll_interval_s=job_status_poll_interval_s, client_rows=rows)}"
        f"{jobs_fragment(rows, local_runs, now=now, public_host=public_host, sparklines=sparklines, lazy_sparklines=lazy_sparklines)}"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Lokale Job-Detailseite (PLAN-21 Befund 10-Nachtrag; PLAN-29 Befund 3+5) ──
#
# User-Fund 2026-07-09: der Jobs-Screen (Client) verlinkte bisher direkt von
# der Liste auf den letzten EINZELNEN Lauf, ohne Zwischenseite "dieser Job,
# alle lokalen Läufe" — anders als der Host, wo /-/ui/schedule/{slug} genau
# das leistet. Diese Seite ist das Gegenstück.
#
# PLAN-29 Befund 3+5 (User-Entscheidung: "(2) sehr gerne", Vereinheitlichung
# statt Parallel-Renderer): war bis 2026-07-14 ein bewusster Nicht-Clone von
# schedule_detail_page() (eigene Buttons/Icons, keine Attribute-Seite) — vor
# PLAN-28 auch nötig, weil lokale /run-Läufe keine echte jobs-Zeile hatten.
# Seit PLAN-28 sind gepinnte Läufe echte jobs-Zeilen (nur in der eigenen
# lokalen jobs.sqlite statt einer geteilten DB, s. dort), die Darstellung
# nutzt jetzt deshalb dieselben Bausteine wie der Host: _action_bar()/
# _live_panel() (mit base="/-/ui/jobs/detail", target="#jobsdetail-live",
# raw_stream_base=None — der rohe SSE-Link existiert auf einem reinen Client
# nicht, s. _live_panel()-Docstring) statt eigener Icon-Buttons/Output-Box.
# _local_job_view() unten ist der Adapter: baut aus der lokalen MD-Discovery
# + eigener /run-Journal-Historie + Live-Status dasselbe job-shaped Dict, das
# _action_bar()/_live_panel() erwarten (Gegenstück zu _detail_data()s
# Scheduler-DB-Lookup auf dem Host — die einzige echte Quelle des
# Unterschieds ist WO die Zeile lebt, nicht WIE sie aussieht, exakt wie
# gegen execute_reservation() als gemeinsamen Ausführungskern verifiziert).
# Die Journal-Tabelle (Historie, Löschen, Detail-Link) war schon vorher
# **derselbe** Baustein wie beim Host (journal_fragment() mit
# base="/-/ui/jobs/detail").


def _local_job_view(local: dict, last_run: dict | None, live: dict | None) -> dict | None:
    """Adapter (PLAN-29 Befund 3+5): normalisiert die lokal ohnehin schon
    vorhandenen Datenquellen auf dasselbe job-shaped Dict, das
    _action_bar()/_live_panel() erwarten — ``id``/``status`` (Button-
    Aktivierung), ``started_at``/``finished_at``/``reason`` (Meta-Zeile),
    ``app_port`` (Link, aus der MD-Discovery — job-Attribut, nicht
    laufabhängig) und im Live-Fall ``app_url`` (HITL-Signal). Gibt ``None``
    zurück, wenn der Job auf diesem Knoten noch nie lief UND gerade nichts
    live ist — _action_bar() rendert dafür trotzdem eine Start-only-Leiste
    (s. dortiger Aufrufer), _live_panel() bleibt dann bewusst leer (kein
    "letzter Lauf"/"aktiver Lauf"-Kasten ohne jeden Lauf)."""
    app_port = local.get("app_port")
    if live:
        # Ausbau User-Fund 2026-07-10: lokale App-Jobs melden jetzt auch
        # awaiting über den Signal-Kanal (worker.local_run_signal_state()) —
        # ohne explizites Signal gilt ein live-Eintrag als "running" (dieselbe
        # Fallunterscheidung wie _jobs_row()/vormals _local_job_meta()).
        #
        # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED
        # nie im Dashboard gesehen"): die alte "awaiting sonst running"-Regel
        # kollabierte JEDEN anderen Live-Status (deferred, failed — beide seit
        # dem _PINNED_LIVE_STATUSES-Fix hier überhaupt erst ankommend) hart auf
        # "running". live["status"] (worker.local_run_live(), DB-Spalte direkt)
        # trägt den echten Wert längst mit — der wurde hier nur nie benutzt.
        status = live.get("status") or "running"
        return {"id": live.get("id"), "status": status,
                "started_at": live.get("started_at"), "app_port": app_port,
                "app_url": live.get("app_url")}
    if last_run:
        return {"id": last_run.get("id"), "status": last_run.get("status"),
                "started_at": last_run.get("started_at"),
                "finished_at": last_run.get("finished_at"),
                "reason": last_run.get("reason"), "app_port": app_port}
    return None


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


def _local_job_meta_line(local: dict, *, public_host: str = "localhost",
                         include_app_link: bool = True) -> str:
    """Typ/Trigger/Git-Zeile der Client-Job-Detailseite — reine MD-Discovery-
    Info ohne 1:1-Host-Entsprechung (dessen Meta-Zeile, ``live_fragment()``,
    zeigt Kind/Trigger/letzten-Status/nächsten-Lauf aus der Scheduler-DB,
    kennt aber keinen Git-Status je Datei). Status selbst steht bewusst NICHT
    hier (anders als beim Host), sondern ausschließlich im Status-Badge von
    ``_live_panel()`` direkt darunter — keine doppelte Anzeige.

    Wrapper seit dem Seitenabgleich (Batch 8) ``<div class="meta">`` statt
    ``<p class="muted">`` — dasselbe Markup wie ``live_fragment()`` (Host) für
    dieselbe Rolle im Layout, auch wenn der Inhalt rollenspezifisch bleibt
    (Host: letzter/nächster Lauf aus der Scheduler-DB; Client: Git-Status +
    App-Link, die der Host so nicht kennt).

    App-Link hier als Fallback zu ``_live_panel()`` (Bibi4-Iteration, User-Fund:
    "der fehlt"): ``_local_job_view()`` gibt ``None`` zurück, wenn der Job auf
    diesem Knoten noch nie lief UND nichts live ist — dann bleibt
    ``_live_panel()`` komplett leer, obwohl ``local["app_port"]`` (statische
    MD-Frontmatter) längst bekannt ist. Auf dem Host gibt es diese Lücke nicht
    (jede Schedule bekommt beim Rescan sofort eine ``job_db``-Zeile samt
    ``app_port``, unabhängig vom ersten Lauf). ``include_app_link=False``
    (vom Aufrufer gesetzt, sobald ``job`` nicht ``None`` ist) verhindert die
    doppelte Anzeige, wenn ``_live_panel()`` den Link ohnehin schon zeigt."""
    kind = _e(_effective_sched_type(local))
    trigger = _e(local.get("schedule") or local.get("at") or "—")
    cls, git_label = _GIT_STATUS_LABEL.get(local.get("git_status", "clean"),
                                           ("chip", _e(str(local.get("git_status", "—")))))
    app_port = local.get("app_port") if include_app_link else None
    app_link = (f' · <a href="http://{public_host}:{app_port}/" target="_blank" '
               f'rel="noopener">Zur App →</a>' if app_port else "")
    return (f'<div class="meta">Typ <b>{kind}</b> · '
            f'Trigger <code>{trigger}</code> · Git <span class="{cls}">{git_label}</span>'
            f'{app_link}</div>')


def jobs_detail_live_fragment(slug: str, live: dict | None, local: dict | None,
                              last_run: dict | None, *,
                              last_run_output: dict | None = None,
                              public_host: str = "localhost",
                              now: float | None = None) -> str:
    """Self-pollende Region (``#jobsdetail-live``): Meta-Zeile + Aktions-
    Leiste + Live-/letzter-Lauf-Block — seit PLAN-29 Befund 3+5 dieselben
    Bausteine wie beim Host (``_action_bar()``/``_live_panel()``, s. Modul-
    Kommentar), nur mit lokal gespeisten Daten. Ziel = ``/-/ui/jobs/detail/
    {slug}/live``. ``last_run_output`` (PLAN-28 User-Feedback): Fallback auf
    den archivierten Output des letzten Laufs, solange nichts live ist."""
    now = time.time() if now is None else now
    local = local or {}
    s = _e(slug)
    running_flag = "1" if live else "0"
    journal_url = f"/-/ui/jobs/detail/{s}/journal"
    job = _local_job_view(local, last_run, live)
    # _action_bar() rendert nichts ohne job["id"] — ein Client-Job kann aber
    # echt noch nie gelaufen sein (anders als beim Host, s. _VERBS_FOR_STATUS
    # "": Sentinel statt echtem Lauf, nur für die Button-Leiste).
    action_job = job or {"id": "-", "status": ""}
    live_output = live if live else last_run_output
    body = (
        # include_app_link=(job is None): sobald job existiert, zeigt
        # _live_panel() den App-Link bereits selbst — sonst stünde er doppelt.
        _local_job_meta_line(local, public_host=public_host, include_app_link=job is None)
        + _action_bar(slug, action_job, local.get("exec_mode"),
                     base="/-/ui/jobs/detail", target="#jobsdetail-live")
        + _live_panel(job, now, live_output, slug=slug, public_host=public_host,
                     raw_stream_base=None)
    )
    attrs = (f'id="jobsdetail-live" data-running="{running_flag}" '
            f'data-journal-url="{journal_url}" '
            f'hx-get="/-/ui/jobs/detail/{s}/live" hx-trigger="{_POLL}" hx-swap="outerHTML"')
    return f"<div {attrs}>{body}</div>"


def jobs_detail_inner(slug: str, local: dict, last_run: dict | None,
                      runs: list[dict], now: float | None = None,
                      *, live: dict | None = None,
                      last_run_output: dict | None = None,
                      public_host: str = "localhost") -> str:
    now = time.time() if now is None else now
    return (
        jobs_detail_live_fragment(slug, live, local, last_run,
                                  last_run_output=last_run_output,
                                  public_host=public_host, now=now)
        + journal_fragment(runs, slug, now, base="/-/ui/jobs/detail",
                          live_job=live, live_anchor="#jobsdetail-live")
    )


def jobs_detail_page(slug: str, local: dict | None, last_run: dict | None,
                     runs: list[dict], now: float | None = None,
                     *, daemon_status: dict | None = None, live: dict | None = None,
                     last_run_output: dict | None = None,
                     public_host: str = "localhost") -> str:
    """Lokale Job-Detailseite (ein Slug, nur lokale /run-Läufe dieses Knotens)
    — seit PLAN-29 Befund 3+5 dieselben Bausteine wie schedule_detail_page()
    (Host), s. Modul-Kommentar. "Attribute →" verlinkt auf die neue, lokal
    gespeiste jobs_detail_attrs_page() (Gegenstück zu schedule_attrs_page(),
    die auf einem Client mangels Scheduler-Rolle nur leere Platzhalter
    zeigen würde, s. dortiger PLAN-29-Befund).

    Kein "← Jobs"-Link mehr (zweite Bibi4-Iteration, User-Fund: derselbe
    Seitenabgleich-Wunsch, der schedule_detail_page() den "← zurück"-Link
    genommen hat, gilt explizit auch hier) — die Nav-Leiste trägt schon
    einen Jobs-Tab dorthin zurück, der Link war redundant."""
    now = time.time() if now is None else now
    local = local or {}
    s = _e(slug)
    inner = jobs_detail_inner(slug, local, last_run, runs, now, live=live,
                              last_run_output=last_run_output, public_host=public_host)
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
        f'<a class="back" href="/-/ui/jobs/detail/{s}/attrs">Attribute →</a>'
        f'</div>'
        f'<h1>{s}</h1>'
        f"{inner}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_LIVE_AUTOREFRESH_JS}</script>"
        f"<script>{_TIME_JS}</script>"
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
    # Bibi4-Iteration, User-Fund: der Time-Toggle (abs/rel/beides) schaltete
    # überall außer im Feed — hier bisher fest absolut, unabhängig vom Toggle.
    t = _time_toggle_cell(e.get("last_changed"), now, rel_fn=_ago)
    authors = ", ".join(e.get("authors") or []) or "—"
    # Bibi4-Iteration, User-Fund: "warum erscheint hier mein Name" — all_agent
    # (Merge-Herkunft, feed.py::group_entities()) wurde bisher nur fürs
    # "Wer"-Filtern genutzt (_FEED_FILTER_JS), nicht in der Autor-Spalte selbst
    # sichtbar gemacht. Der rohe Git-Autor bleibt "m.rau" auch für automatisierte
    # Läufe (git_ops.stage_and_commit() committet /save & Co. immer unter der
    # ambienten Identität) — ohne diesen Zusatz sah eine als "nur Agents"
    # gefilterte Zeile trotzdem aus wie ein manueller m.rau-Commit.
    who = f"{authors} · automatisiert" if is_agent and authors != "—" else authors
    commit = _feed_commit_cell(e.get("last_commit_sha"), commit_base_url)
    return (f'<div class="{cls}" data-kind="{_e(kind)}" data-agent="{"1" if is_agent else "0"}">'
           f'<span class="t">{t}</span>'
           f'<span class="lvl {_e(kind)}">{_e(kind)}</span>'
           f'<span class="msg">{_e(name)}</span>'
           f"{commit}"
           f'<span class="who">{_e(who)}</span>'
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
    status_poll_interval_s: int = 30, job_status_poll_interval_s: int = 2,
    client_rows: list[dict] | None = None,
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
        f"{feed_status_fragment(status, git_status, host_url, now, poll_interval_s=status_poll_interval_s, job_status_poll_interval_s=job_status_poll_interval_s, client_rows=client_rows)}"
        f"{feed_fragment(feed_data, days=days, weeks=weeks, now=now)}"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
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

    MAINT funktional weiterhin nur mit ``scheduler``-Rolle (PLAN-25 Befund 1)
    — der Client kennt gar keinen eigenen Maintenance-Mode, ein Klick hätte
    dort nie etwas pausiert. Seit der Bibi4-Iteration (User-Fund "eine App":
    Host und Client sollen dieselbe Toggle-Menge zeigen, nicht verfügbare
    Funktionen disabled statt ausgeblendet) bleibt der Button ohne
    ``scheduler``-Rolle sichtbar, aber ``disabled`` — rein visuelle
    Vereinheitlichung, keine neue Funktionalität (User-Entscheidung: kein
    read-only Host-Maintenance-Status auf dem Client). RESCAN bleibt
    unbedingt aktiv, das ist auf jedem Knoten sinnvoll.

    Icons statt Text (Bibi4-Iteration, User-Fund: "Toggles über Icons") — ⟳
    für RESCAN (reine Aktion, kein eigener Zustand), ⚙/⚠ für MAINT aus/an
    (analog FOLLOW/THEME: Glyph trägt den Zustand, ``title`` das Hover-Label)."""
    roles = (status or {}).get("roles") or []
    maint = bool((status or {}).get("maintenance"))
    if "scheduler" in roles:
        mcls = "toggle warn" if maint else "toggle"
        micon = "⚠" if maint else "⚙"
        mtitle = "Wartungsmodus: an" if maint else "Wartungsmodus: aus"
        maint_btn = f'<button id="maint" class="{mcls}" title="{mtitle}">{micon}</button>'
    else:
        maint_btn = ('<button id="maint" class="toggle" disabled '
                    'title="Wartungsmodus: nur auf dem Host verfügbar">⚙</button>')
    return (
        '<nav class="handles">'
        '<button id="rescan" class="toggle" title="Rescan auslösen">⟳</button>'
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
    const idleIcon = rescan.textContent;   // "⟳"
    rescan.addEventListener('click', async () => {
      rescan.disabled = true;
      try { await fetch('/-/rescan', {method:'POST'}); } catch(_){}
      rescan.textContent = '✓';
      setTimeout(() => { rescan.textContent = idleIcon; rescan.disabled = false; }, 1200);
    });
  }
  const maint = document.getElementById('maint');
  function setMaint(on){
    maint.classList.toggle('warn', on);
    maint.textContent = on ? '⚠' : '⚙';
    maint.title = on ? 'Wartungsmodus: an' : 'Wartungsmodus: aus';
    // Bibi4-Iteration, User-Fund: "ein Klick auf Maintenance muss ein Update
    // der Mode Card nach sich ziehen" — die Mode-Kachel hängt sonst im
    // separat gepollten #feedstatus-Bundle (bis zu 30s Verzögerung).
    document.body.dispatchEvent(new Event('bibiMaintChanged'));
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
#: (WeakSet verhindert Doppel-Abos). Der Server schickt kurz vor dem beabsichtigten
#: Schließen (Job terminal) ein explizites ``event: done`` — NUR darauf schließt der
#: Client selbst (User-Fund 2026-07-20: ``onerror`` feuert identisch bei einem
#: einfachen Verbindungsabriss UND beim beabsichtigten Server-Ende, ein früheres
#: ``es.close()`` in ``onerror`` fror deshalb auch noch laufende Jobs nach jedem
#: Netzwerk-Hänger dauerhaft ein). ``onerror`` schließt jetzt nicht mehr — der
#: automatische Browser-Reconnect greift, inkl. selbstständig mitgeschicktem
#: ``Last-Event-ID`` (aus der ``id:``-Zeile jedes Events, s. ``_formatted_sse()``
#: in ``app.py``), kein Duplikat-Risiko.
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
      // User-Fund 2026-07-20: 'done' (server schickt es explizit kurz vor dem
      // beabsichtigten Schliessen, s. _formatted_sse()) ist jetzt das einzige
      // Signal fuer "Job wirklich fertig" -- der Client schliesst SELBST,
      // bevor die Verbindung natuerlich endet.
      es.addEventListener('done', () => {
        if (box._bibiEs) { box._bibiEs.close(); box._bibiEs = null; }
      });
      // onerror schliesst absichtlich NICHT mehr (frueher: es.close() fuer
      // JEDEN Verbindungsabriss, ununterscheidbar vom obigen 'done'-Fall --
      // ein Job, der noch lief, aber dessen Verbindung kurz haengt, fror in
      // der Box fuer immer ein, "Erst ein manuelles Reload gibt mehr Output").
      // Ohne eigenen Handler reconnectet EventSource automatisch (Browser-
      // Standardverhalten) und schickt dabei von selbst Last-Event-ID mit --
      // kein Duplikat-Risiko, kein eigenes Zaehl-JS noetig.
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
    return _human_duration(r.get("exec_runtime"))


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


def _live_placeholder_row(job: dict | None, now: float, *, anchor: str) -> str:
    """Rein zur Anzeige eingeblendete oberste Journal-Zeile für einen noch
    nicht abgeschlossenen Lauf — kein echter Journal-Eintrag (``_write_journal()``
    schreibt unverändert erst beim Terminal-Übergang, Job-Lifecycle-Redesign,
    leichte Variante statt [[PLAN-35]], Case 20260621.Bibi4-870bd9db, 2026-07-27:
    dort die volle Herleitung, warum die schwerere Variante — Journal-Zeile
    schon bei Dispatch anlegen — zurückgestellt wurde).

    Dasselbe "zeig einen aktiven Lauf"-Kriterium wie ``_live_panel()``s
    eigener ``out``-Zweig weiter unten (NICHT dessen breiteres
    ``is_terminal``, das dort einem anderen Zweck dient — der Meta-Zeilen-
    Formatierung, nicht der Frage "aktiver Lauf ja/nein"): nur
    ``running``/``awaiting``/``deferred`` gelten als aktiv, ``pending``
    explizit NICHT (wartet nur auf den Start, kein Lauf im Gange) — sonst
    zeigte diese Zeile "läuft" für einen Job, der noch gar nicht losgelaufen
    ist. Kachel und diese Zeile dürfen nie auseinanderlaufen, sonst zeigt die
    eine "läuft noch", während die andere schon den letzten abgeschlossenen
    Lauf zeigt. ``anchor`` verlinkt zurück auf die Live-Region (``#live``
    Host, ``#jobsdetail-live`` Client) — ein reiner In-Page-Sprung, keine neue
    Route nötig.

    ``status or "running"`` (nicht bloß ``status``): das Client-``live``-Dict
    (``worker.local_run_live()``) trug historisch nicht immer ein explizites
    ``status``-Feld — ``_local_job_view()`` (die Quelle von Block 1 auf der
    Client-Seite) fängt genau das mit demselben Default ab. Ohne diesen
    Default hier könnte diese Zeile ausbleiben, während die Kachel oben
    trotzdem "running" zeigt — der Host-``job``-Dict hat ``status`` dagegen
    immer gesetzt (NOT NULL-Spalte), dort ist der Default ein No-op."""
    if not job:
        return ""
    status = job.get("status") or "running"
    if status not in ("running", "awaiting", "deferred"):
        return ""
    st = _e(status)
    t_abs = _abs_datetime(job.get("started_at"), now)
    return (
        "<tr>"
        f"<td>{t_abs}</td>"
        f'<td class="st {st}">{st}</td>'
        "<td>—</td><td>—</td><td>—</td><td>—</td>"
        f'<td><a class="back" href="{anchor}">↑ live</a></td>'
        "</tr>"
    )


def _journal_table_html(runs: list[dict], slug: str, now: float, *, offset: int = 0,
                        base: str = _JOURNAL_BASE, live_row: str = "") -> str:
    if not runs and not live_row:
        return '<p class="out-empty">— noch keine Läufe —</p>'
    rows = live_row + _run_rows(runs, slug, now, base=base)
    if len(runs) == _JOURNAL_PAGE_SIZE:
        rows += _journal_sentinel_row(slug, offset + _JOURNAL_PAGE_SIZE, base=base)
    return (
        '<table><thead><tr><th>Zeit</th><th>Status</th><th>Grund</th>'
        '<th>exit</th><th>Dauer</th><th>Commit</th><th></th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def journal_fragment(runs: list[dict], slug: str, now: float, *, oob: bool = False,
                     base: str = _JOURNAL_BASE, live_job: dict | None = None,
                     live_anchor: str = "#live") -> str:
    """Eigenständige, nicht selbst-pollende Region (``#journal``) — wächst nur
    durch nutzergetriggertes Infinite-Scroll-Nachladen (kein 2s-Poll, der die
    nachgeladenen Zeilen sonst wieder plattmachen würde). ``.panel-card``-Rahmen
    (Bibi4-Iteration, User-Fund) sitzt auf diesem äußeren Div, nicht auf der
    Tabelle selbst — wächst automatisch mit, weil hier normale Dokumentfluss-
    Höhe gilt (kein fixer/scrollender Innenbereich, das Nachladen hängt neue
    Zeilen einfach ans bestehende ``<tbody>``).

    ``live_job`` (Job-Lifecycle-Redesign, leichte Variante, 2026-07-27):
    Host ``job``-Dict oder Client ``live``-Dict des aktuellen Laufs, ``None``
    wenn keiner läuft/wartet — bewusst nur beim initialen Seitenaufbau
    durchgereicht (``schedule_detail_inner()``/``jobs_detail_inner()``), NICHT
    bei den ``.../journal``-Refresh-Routen: die feuern laut
    ``_JOURNAL_AUTOREFRESH_JS``/``_JOBS_LIVE_AUTOREFRESH_JS`` erst, wenn
    ``finished_at`` sich ändert — in genau dem Moment ist der Lauf schon
    terminal, kein Platzhalter mehr nötig, der echte Journal-Eintrag existiert
    dann bereits in ``runs``."""
    oob_attr = ' hx-swap-oob="true"' if oob else ""
    live_row = _live_placeholder_row(live_job, now, anchor=live_anchor)
    return (
        f'<div id="journal"{oob_attr} class="panel-card">'
        "<h2>Journal</h2>"
        f"{_journal_table_html(runs, slug, now, base=base, live_row=live_row)}"
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
               slug: str = "", *, public_host: str = "localhost",
               raw_stream_base: str | None = "/-/job") -> str:
    """Eigener Block für den **aktuellen** Lauf (aktiv oder zuletzt beendet), nahe
    am Header. Bleibt auch nach einem Terminal-Übergang mit Status+Output stehen
    (User-Feedback 2026-07-01: "archiviert wird erst vor dem nächsten Rerun" —
    die Job-Zeile trägt den letzten Lauf ja weiter fort, bis sie ein neuer Lauf
    überschreibt; das Journal bekommt seine Zeile trotzdem sofort beim Terminal-
    Übergang, s. job_db.py::_write_journal, nur diese Anzeige hier hängt nicht
    mehr daran). Der Output wird **default expanded** mitgerendert (server-seitig,
    überlebt Poll).

    ``raw_stream_base`` (PLAN-29 Befund 3+5): der rohe SSE-Stream-Link
    (``{base}/{jid}/stream``) existiert nur über ``_add_worker_routes()``
    (``roles.worker``) — auf einem reinen Client (Rolle ``connect``, kein
    ``worker``) wäre das ein toter Link (501, gefrorener v3.0-Contract-Stub,
    live gegen den Mac-Client geprüft). ``None`` lässt den Link komplett weg,
    ohne die formatierte Live-Output-Box selbst anzutasten."""
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
    # Bugfix (User-Fund): "next run" fehlte bisher bei deferred/failed komplett
    # — die Box zeigte während der gesamten Warte-/Retry-Phase weder wann noch
    # dass überhaupt ein Retry ansteht (dasselbe next_fire_at-Feld wie bei pending).
    if job.get("status") in ("pending", "deferred", "failed") and job.get("next_fire_at"):
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
                    f'<a class="back" href="{raw_stream_base}/{_e(jid)}/stream">roher Stream →</a>'
                    f'</span>') if raw_stream_base else ""
        out = (f'<div class="liveout">{raw_link}'
               + live_output_box(jid, events, kind=(live_output or {}).get("kind", "job"))
               + "</div>")
    elif job.get("status") == "awaiting":
        if live_output and live_output.get("events"):
            out = ('<div class="liveout liveclamp">'
                   + output_block(live_output["events"], live_output.get("kind", "job"))
                   + "</div>")
        out += _hitl_panel(job)
    elif job.get("status") == "deferred" and live_output and live_output.get("events"):
        # Bugfix (User-Fund, "von der defer habe ich nie etwas im FE gesehen"):
        # deferred hatte hier gar keinen Zweig — out blieb leer, obwohl der
        # Wrapper vor dem Deferred(...)-Signal meist schon etwas ausgegeben hat.
        out = ('<div class="liveout liveclamp">'
               + output_block(live_output["events"], live_output.get("kind", "job"))
               + "</div>")
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
    elif job.get("status") == "deferred":
        label = "wartet auf Retry"
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
# unmittelbar bevorstehend ist) und bleibt No-op auf den übrigen echten
# Terminalzuständen (error/inactive/zombie/killed). complete ist seit
# 2026-07-20 die explizite Ausnahme (User-Redesign, widerruft den Teil von
# 2026-07-03, der complete mit einschloss): ohne KILL konnte Lazy Rearm einen
# wiederkehrenden complete-Job nie wirklich anhalten, ohne die MD zu editieren
# — KILL archiviert den Lauf jetzt wie RESET, landet aber direkt auf killed
# (s. lifecycle.py/report_status()). START erzwingt "sofort", RESET respektiert
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
    "complete": ("start", "kill"),
    # PLAN-29 Befund 3+5: ein Client-Job kann echt noch nie gelaufen sein
    # (keine jobs-Zeile existiert, anders als beim Host, wo jede entdeckte
    # Schedule sofort eine bekommt) — "" markiert genau diesen Fall, START
    # bleibt trotzdem nutzbar, KILL/RESET bleiben deaktiviert (nichts da).
    "":         ("start",),
}


#: PLAN-24 Befund 5: REBUILD ist bewusst NICHT Teil von _VERBS/_VERBS_FOR_STATUS
#: — anders als START/RESET/KILL (immer sichtbar, nur je nach Status
#: aktiviert) taucht REBUILD gar nicht erst auf, wenn der Job nicht im
#: Container-Modus läuft (User-Klärung: "sichtbar nur bei exec_mode:
#: container", nicht sichtbar-aber-deaktiviert). Host-Mode-Jobs haben kein
#: per-Job-Image, das ein Reset bräuchte (uv run --script ist selbst schon
#: reproduzierbar).
_CONTAINER_VERBS = ("rebuild",)

#: Aktivitätsanzeige an den Aktions-Buttons (Bibi4 Batch 6, User-Fund: "Man
#: ist geneigt, den Button mehrfach zu klicken"). Zwei htmx-Bordmittel, kein
#: eigenes JS: ``hx-disabled-elt="this"`` sperrt den Klick strukturell für
#: die Request-Dauer, dieser Spinner-Span sitzt IM Button (Default-Ziel der
#: htmx-request-Klasse ohne explizites hx-indicator) und wird per CSS nur
#: während ``.htmx-request`` sichtbar (s. ``.btn-spinner``/``@keyframes
#: bibi-pulse`` in _CSS).
_BTN_SPINNER = '<span class="btn-spinner" aria-hidden="true"></span>'


def _action_bar(slug: str, job: dict | None, exec_mode: str | None = None,
                *, base: str = "/-/ui/schedule", target: str = "#live") -> str:
    """``base``/``target`` (PLAN-29 Befund 3+5, User-Entscheidung: Vereinheitlichung
    statt Parallel-Renderer) — Default reproduziert exakt das bisherige Host-
    Verhalten (``/-/ui/schedule/{slug}/{verb}`` → ``#live``); die Client-
    Job-Detailseite ruft mit ``base="/-/ui/jobs/detail"``, ``target="#jobsdetail-live"``."""
    if not job or not job.get("id"):
        return ""
    s = _e(slug)
    status = job.get("status", "")
    enabled = _VERBS_FOR_STATUS.get(status, ())
    btns = "".join(
        f'<button hx-post="{base}/{s}/{v}" hx-target="{target}" '
        f'hx-swap="outerHTML" hx-disabled-elt="this"'
        f'{"" if v in enabled else " disabled"}>{v.upper()}{_BTN_SPINNER}</button> '
        for v in _VERBS
    )
    if (exec_mode or "host").strip().lower() == "container":
        btns += (f'<button hx-post="{base}/{s}/rebuild" hx-target="{target}" '
                 f'hx-swap="outerHTML" hx-disabled-elt="this" '
                 f'title="Verwirft das per-Job-Image, nächster Lauf startet vom '
                 f'Default-Image">REBUILD{_BTN_SPINNER}</button> ')
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
    kind = _jobs_type_cell(s, public_host)
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
        + journal_fragment(runs, slug, now, live_job=job)
    )


def schedule_detail_page(
    schedule: dict | None, runs: list[dict], job: dict | None = None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, daemon_status: dict | None = None,
    public_host: str = "localhost",
) -> str:
    """Schedule-zentrierte Detail-Sicht (§3 Ebene 3) als volle Seite. Ops-Handles
    (RESCAN/MAINT) seit User-Feedback 2026-07-03 auch hier — außerhalb von
    ``#live``/``#journal``, damit sie nicht bei jedem 2s-Poll neu gerendert werden.
    Kein "← zurück"-Link mehr (Bibi4-Iteration, User-Fund) — die Nav-Leiste
    trägt schon einen Jobs-Tab dorthin zurück, der Link war redundant."""
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
        f'<a class="back" href="/-/ui/schedule/{_e(name)}/attrs">Attribute →</a>'
        f'</div>'
        f"{schedule_detail_inner(schedule, runs, job, slug, now, live_output=live_output, public_host=public_host)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_LIVE_JS}</script>"
        f"<script>{_JOURNAL_AUTOREFRESH_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
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
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Schedule-Attribute (alle Konfig- + Runtime-Felder; Ebene 3b) ─────────────

_ATTRS_CONFIG_ORDER = [
    "slug", "kind", "payload", "schedule", "at_iso", "priority",
    "model", "soul", "session",
    "attempts", "backoff", "silence_timeout", "wall_time",
    "defer_time", "defer_max", "error_time",
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
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


def jobs_detail_attrs_page(slug: str, local: dict | None) -> str:
    """Attribute-Seite für einen lokal entdeckten Job (PLAN-29 Befund 3+5) —
    Gegenstück zu ``schedule_attrs_page()`` (Host), aber aus der lokalen MD-
    Discovery gespeist (``_local_schedules()``) statt der Scheduler-DB, die
    ein reiner Client strukturell nicht hat (live geprüft: ``client.
    schedule_config()`` liefert dort still ``{}`` statt eines Fehlers, s.
    PLAN-29 Befund 3+5 — dieselbe Seite mit denselben Scheduler-Daten würde
    auf einem Client also nur Platzhalter zeigen). Nur "Konfiguration"
    (``_ATTRS_CONFIG_ORDER``) — "Scheduling" (``id``/``next_fire_at``/
    ``fire``) ist Scheduler-Laufzeitstand, den es für einen rein lokal
    entdeckten Job nicht gibt."""
    name = _e(slug)
    data = {**(local or {}), "slug": slug, "kind": "job"}
    config_html = _attrs_section("Konfiguration", _ATTRS_CONFIG_ORDER, data)
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
        f'<a class="back" href="/-/ui/jobs">← Jobs</a>'
        f'<a class="back" href="/-/ui/jobs/detail/{name}">← Detail</a>'
        f'</div>'
        f'<h1>{name} · Attribute</h1>'
        f"{config_html}"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )
