"""HTML-Rendering der Controller-App (PLAN-4 §4.1 ff.) — **pure** Funktionen:
Daten-dict (aus den ``/-/``-JSON-Endpunkten) → HTML. Kein HTTP, kein DB-Zugriff,
damit voll unit-testbar. Look: Terminal/Konsole-nah, minimal (§2.5)."""

from __future__ import annotations

import datetime
import html
import json
import re
import time

from bibi.controller.jobs_view import Segment
from bibi.schedule import models

# PLAN-36 Stufe 36.0: htmx lokal statt unpkg.com-CDN (Tailnet-only-Setup —
# die CDN-URL war die einzige externe Abhängigkeit jeder Seite; offline starb
# damit das komplette FE). Route: controller/__init__.py::htmx_asset(),
# Datei: controller/static/htmx.min.js, versionierter Pfad = Cache-Busting.
_HTMX = "/-/static/htmx-1.9.12.min.js"

# PLAN-36 Stufe 36.3: die Poll-Trigger-Konstanten (_POLL 2s, _POLL_NET 30s,
# _CHART_POLL 20s, _CLIENTS_POLL 10s) sind vollständig zurückgebaut — JEDE
# Region aktualisiert sich jetzt ausschließlich über den Event-Bus
# (data-bus/data-bus-refetch, s. _EVENTS_JS); den Still-gestorbener-Strom-Fall
# fängt der Ping-Watchdog in _EVENTS_JS ab, nicht mehr ein Poll-Netz.

_CSS = """
/* ── Grundpalette (m.rau/bibi#68) ───────────────────────────────────────────
   Bis v0.5.3 hatte die UI keine. `color-scheme: light dark` ueberliess Grund
   und Textfarbe dem Browser, und alles Neutrale lief ueber den Alpha-Grau-
   Trick (#8881 … #888), der theme-blind funktioniert — deshalb musste bibi
   nie ein zweites Theme pflegen. Das ist ab hier vorbei: es sind zwei Themes,
   dauerhaft, und beide sind vollstaendig gezeichnet. Der Preis stand so in
   #68 und wird bewusst bezahlt.

   Die Werte kommen aus Teil 5 der Design-Studie (bibi-notes,
   20260729.bibi4DesignStudie-77178146) — die einzige Palette, an der eine
   Gestaltungsentscheidung tatsaechlich getroffen wurde (Layout 01
   Kontenblatt, 2026-07-31). Abgeleitet aus dem interaktiven Claude-Code-CLI:
   warmes Papier gegen warmes Anthrazit, Terracotta als einziger
   Marken-Akzent, gedimmtes Grau als Haupttraeger, Semantikfarben nur an
   Zustandsstellen.

   Terracotta traegt genau EINE Bedeutung — Interaktion. Sie an Marke und
   Job-Typ zugleich zu geben liess im Studien-Mockup die halbe Tabelle orange
   werden und die Slugs sich als Fehlerzustand lesen. Der Fehlerton haelt
   deshalb Farbwinkel-Abstand (10,9° statt der 6,6°, die naheliegend gewesen
   waeren); korrigiert wurde der Fehlerton, nicht die Marke. */
:root {
  color-scheme: light;
  --bg: #faf9f5; --text: #1f1e1b; --dim: #6f695e; --faint: #9c9689;
  --line: #00000012; --line-hard: #00000024; --hover: #00000008;
  --brand: #c25f3c;
  --green: #3f7d52; --blue: #3a6f9e; --amber: #a3762a; --red: #b0342b;
  /* Beschriftungen im Header: erkennbar farbig, aber ruhig —
     sie sind das Geruest, an dem das Auge die Zeile findet, nicht der Wert. */
  --hdr-key: #5d7f9d;
  --btnbg: #00000008; --btnline: #00000022;
  --greensoft: #3f7d5222; --bluesoft: #3a6f9e1a; --blueline: #3a6f9e55;
  --ambersoft: #a3762a22; --amberline: #a3762a55;
  --redsoft: #b0342b14; --redline: #b0342b44;
  --cell0: #00000009; --cell1: #3a6f9e33; --cell2: #3a6f9e66;
  --cell3: #3a6f9ea6; --cell4: #3a6f9e;
  --term-bg: #1c1b18; --term-text: #e8e5dc; --term-link: #d97757;
}
/* Wer nichts gewaehlt hat, bekommt was sein System sagt. Die ausdrueckliche
   Wahl steht weiter unten und schlaegt das — die Reihenfolge ist hier die
   ganze Kaskade. */
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #1c1b18; --text: #e8e5dc; --dim: #948e81; --faint: #6d675c;
    --line: #ffffff12; --line-hard: #ffffff26; --hover: #ffffff08;
    --brand: #d97757;
    --green: #6aa87e; --blue: #6b9fd0; --amber: #cb9a4a; --red: #d4534a;
  /* Beschriftungen im Header: erkennbar farbig, aber ruhig —
     sie sind das Geruest, an dem das Auge die Zeile findet, nicht der Wert. */
  --hdr-key: #8aa7c2;
    --btnbg: #ffffff0d; --btnline: #ffffff26;
    --greensoft: #6aa87e26; --bluesoft: #6b9fd022; --blueline: #6b9fd055;
    --ambersoft: #cb9a4a26; --amberline: #cb9a4a55;
    --redsoft: #d4534a1a; --redline: #d4534a44;
    --cell0: #ffffff0d; --cell1: #6b9fd033; --cell2: #6b9fd066;
    --cell3: #6b9fd0a6; --cell4: #6b9fd0;
    --term-bg: #1c1b18; --term-text: #e8e5dc; --term-link: #d97757;
  }
}
/* Der data-theme-Toggle schrieb bis #68 nur `color-scheme` um. Mit Token muss
   er Token-Saetze umschalten, sonst aendert ein Klick die Farben des Browsers
   und nicht die der Seite. Beide Saetze stehen vollstaendig da und keiner ist
   vom anderen abgeleitet: was nur zur Haelfte gezeichnet ist, faellt erst dem
   auf, der genau diesen Modus benutzt. */
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #faf9f5; --text: #1f1e1b; --dim: #6f695e; --faint: #9c9689;
  --line: #00000012; --line-hard: #00000024; --hover: #00000008;
  --brand: #c25f3c;
  --green: #3f7d52; --blue: #3a6f9e; --amber: #a3762a; --red: #b0342b;
  /* Beschriftungen im Header: erkennbar farbig, aber ruhig —
     sie sind das Geruest, an dem das Auge die Zeile findet, nicht der Wert. */
  --hdr-key: #5d7f9d;
  --btnbg: #00000008; --btnline: #00000022;
  --greensoft: #3f7d5222; --bluesoft: #3a6f9e1a; --blueline: #3a6f9e55;
  --ambersoft: #a3762a22; --amberline: #a3762a55;
  --redsoft: #b0342b14; --redline: #b0342b44;
  --cell0: #00000009; --cell1: #3a6f9e33; --cell2: #3a6f9e66;
  --cell3: #3a6f9ea6; --cell4: #3a6f9e;
  --term-bg: #1c1b18; --term-text: #e8e5dc; --term-link: #d97757;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #1c1b18; --text: #e8e5dc; --dim: #948e81; --faint: #6d675c;
  --line: #ffffff12; --line-hard: #ffffff26; --hover: #ffffff08;
  --brand: #d97757;
  --green: #6aa87e; --blue: #6b9fd0; --amber: #cb9a4a; --red: #d4534a;
  /* Beschriftungen im Header: erkennbar farbig, aber ruhig —
     sie sind das Geruest, an dem das Auge die Zeile findet, nicht der Wert. */
  --hdr-key: #8aa7c2;
  --btnbg: #ffffff0d; --btnline: #ffffff26;
  --greensoft: #6aa87e26; --bluesoft: #6b9fd022; --blueline: #6b9fd055;
  --ambersoft: #cb9a4a26; --amberline: #cb9a4a55;
  --redsoft: #d4534a1a; --redline: #d4534a44;
  --cell0: #ffffff0d; --cell1: #6b9fd033; --cell2: #6b9fd066;
  --cell3: #6b9fd0a6; --cell4: #6b9fd0;
  --term-bg: #1c1b18; --term-text: #e8e5dc; --term-link: #d97757;
}
/* Monospace ist gemessen, nicht geschaetzt (Canvas-measureText an den echten
   Zeilen der UI): die breiteste Zeile — Nodes mit 13 Spalten — braucht in
   Mono 14px 851 px, verfuegbar sind 1024. Der Preis ist 14px Grundgroesse
   statt 15px, und dafuer bleibt die max-width unangetastet. */
body { font: 14px/1.55 ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
       background: var(--bg); color: var(--text);
       margin: 0; padding: 1.5rem;
       max-width: 64rem; margin-inline: auto; }
/* Rahmen um die ganze Nav-Leiste, von "bibi" links bis Theme-Toggle rechts
   (Bibi4-Iteration, User-Fund) — derselbe Stil wie .panel-card/.card. */
header { display: flex; align-items: baseline; justify-content: space-between;
         gap: .75rem; flex-wrap: wrap; border: 1px solid var(--line); border-radius: .4rem;
         padding: .5rem .9rem; margin-bottom: .6rem; }
.nav-left, .nav-right { display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap; }
header .handles { margin: 0; }
h1 { font-size: 1.4rem; margin: 0; }
.muted { color: var(--dim); font-size: .85rem; }
.banner { margin: 0; padding: .35rem .75rem; border-radius: .35rem;
          border: 1px solid var(--btnline); font-size: .82rem; font-weight: 500;
          display: inline-block; }
.banner.ok  { background: var(--greensoft); }
.banner.bad { background: var(--redsoft); }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th { text-align: left; color: var(--faint); font-weight: 500; padding: .35rem .5rem;
     border-bottom: 1px solid var(--line); }
td { padding: .4rem .5rem; border-bottom: 1px solid var(--line); }
.st { font-family: ui-monospace, monospace; }
.st.complete { color: var(--green); }
/* starting (#38): Live-Farbe wie running, aber gedimmt — der Job ist aktiv, sein
   Wrapper aber noch nicht gestartet. Ohne eigene Regel bliebe der Status
   ungefärbt und wäre optisch nicht von "pending" zu unterscheiden, obwohl er
   das Gegenteil bedeutet. */
.st.starting { color: var(--blue); opacity: .7; }
.st.running { color: var(--blue); }
.st.awaiting { color: var(--amber); }
.st.pending, .st.deferred { color: var(--dim); }
.st.failed, .st.error, .st.killed, .st.zombie { color: var(--red); }
.st.overdue { color: var(--amber); }
.kind { font-family: ui-monospace, monospace; font-size: .82rem; color: var(--faint); }
.handles { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center;
           margin: 1rem 0 .25rem; }
/* Toggles (FOLLOW/THEME/RESCAN/MAINT) wie Nav-Text-Links, keine Buttons mehr
   (PLAN-19 Befund 7, User-Fund: "nicht Buttons und Text Links gemischt") —
   überschreibt das globale button{...} gezielt nur für diese Klasse. */
.toggle { font: inherit; font-size: 1.3rem; line-height: 1; text-decoration: none;
          color: var(--dim); background: none; border: none; padding: 0; cursor: pointer; }
.toggle:hover { text-decoration: underline; }
.toggle.on { color: var(--green); }
.toggle.warn { color: var(--amber); }
.toggle.bad { color: var(--red); }
/* Disabled-aber-sichtbar (Bibi4-Iteration, User-Fund "eine App") — Host/
   Client zeigen dieselbe Toggle-Menge, nicht verfügbare Funktionen (z.B.
   MAINT auf dem Client) bleiben an Ort und Stelle, statt zu verschwinden. */
.toggle:disabled { opacity: .35; cursor: default; text-decoration: none; }
/* Rollen-Matrix (Bibi4-Iteration, User-Fund: "Rollen etwas schoener
   visualisieren, vielleicht als Spalten mit leerem oder gefuelltem
   Rechteck") — ersetzt die alte Komma-Text-Spalte im Clients-Screen. */
.role-box { font-size: 1rem; }
.role-box.on { color: var(--green); }
.role-box.off { color: var(--faint); opacity: .45; }
/* Time-Toggle (Bibi4-Iteration, User-Fund: "Time: abs./rel./both" für die
   last/since- und next-Spalten) — alle drei Varianten stehen serverseitig
   immer im Markup, data-timeformat auf <html> blendet per CSS genau eine
   ein, kein Re-Render pro Klick nötig. Default (per _TIME_JS) ist "both". */
:root[data-timeformat="abs"] .tt-relonly,
:root[data-timeformat="abs"] .tt-relboth,
:root[data-timeformat="both"] .tt-relonly,
:root[data-timeformat="rel"] .tt-abs,
:root[data-timeformat="rel"] .tt-relboth { display: none; }
/* Links tragen die Marke — bis #68 hatten sie ueberhaupt keine eigene Farbe
   und erbten den Browser-Standard (im Light-Mode ein dunkles Lila). Das war
   schon einmal ein Lesbarkeitsproblem und wurde in der Logbox punktuell
   uebermalt; mit einer eigenen Farbe erledigt sich der Sonderfall. Terracotta
   heisst hier Interaktion und nur das — sie ist an keiner Typ- oder
   Zustandsstelle vergeben. */
a.slug { font-weight: 600; text-decoration: none; color: var(--brand); }
a.slug:hover { text-decoration: underline; }
.sched a { text-decoration: none; }
.sched a:hover { text-decoration: underline; }
a.rowlink { color: inherit; text-decoration: none; }
a.rowlink:hover { text-decoration: underline; }
h2 { font-size: .95rem; color: var(--dim); margin: 1.5rem 0 .4rem; font-weight: 600; }
.back { color: var(--dim); text-decoration: none; font-size: .85rem; }
.tab-active { font-weight: 600; border-bottom: 2px solid currentColor; }
.meta { color: var(--dim); font-size: .9rem; margin: .2rem 0 1rem; }
/* Terminal-Boxen bleiben theme-unabhängig dunkel (PLAN-19 Befund 3, User-Fund:
   Light-Mode unleserlich) — vorher halbtransparentes Schwarz über dem
   wechselnden Seitenhintergrund, im Light-Mode nur mittelgrau statt dunkel;
   Text ohne eigene Farbe erbte zudem die Body-Textfarbe (im Light-Mode dunkel
   auf jetzt dunklem Grund). Fester Hintergrund + feste helle Standardfarbe. */
.term { background: var(--term-bg); color: var(--term-text); border: 1px solid var(--line); border-radius: .4rem;
        padding: .6rem .8rem; overflow-x: auto; font-family: ui-monospace, monospace;
        font-size: .82rem; line-height: 1.45; white-space: pre-wrap; }
.term .err { color: var(--red); }
.term .thinking { color: var(--dim); font-style: italic; }
.term .phase { color: var(--blue); font-style: italic; }
.md { font-size: .92rem; }
.md pre { background: var(--term-bg); color: var(--term-text); border: 1px solid var(--line); border-radius: .4rem;
          padding: .6rem .8rem; overflow-x: auto; }
.md code { font-family: ui-monospace, monospace; font-size: .85em; }
.out-empty { color: var(--dim); font-size: .85rem; font-style: italic; }
button { font: inherit; background: var(--btnbg); border: 1px solid var(--btnline);
         border-radius: .35rem; padding: .15rem .5rem; cursor: pointer; color: inherit; }
.commit { font-family: ui-monospace, monospace; font-size: .8rem; color: var(--dim); }
.live { margin: .6rem 0 1rem; padding: .5rem .8rem; border: 1px solid var(--blueline);
        border-radius: .4rem; background: var(--bluesoft); }
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
          background: var(--btnbg); border: 1px solid var(--btnline); border-radius: .3rem; }
.logbar input { flex: 1; min-width: 8rem; }
.logbox { height: 72vh; overflow-y: auto; background: var(--term-bg); color: var(--term-text);
          border: 1px solid var(--line); border-radius: .4rem; padding: .6rem .8rem;
          font-family: ui-monospace, monospace;
          font-size: .82rem; line-height: 1.5; white-space: pre-wrap; }
.logbox .ln.warning { color: var(--amber); }
.logbox .ln.error   { color: var(--red); }
.logbox .ln.debug   { color: var(--dim); }
/* Die Logbox ist theme-unabhaengig dunkel, ihre Links brauchen deshalb den
   Dark-Ton der Marke statt des Theme-Tons — im Light-Mode saesse sonst ein
   dunkles Terracotta auf Anthrazit. Der Anlass war urspruenglich ein anderer
   (Chromes Standard-Linkfarbe, im Light-Mode dunkles Lila, User-Fund "im
   Light Mode ist die Schriftfarbe lila schwer zu lesen"); den erledigt die
   eigene Linkfarbe aus #68 mit, der Kontrast-Grund bleibt. */
.logbox a.slug { color: var(--term-link); }
.feed { height: 45vh; overflow-y: auto; background: var(--term-bg); border: 1px solid var(--line);
        border-radius: .4rem; padding: .6rem .8rem; font-family: ui-monospace, monospace;
        font-size: .85rem; line-height: 1.7; }
.feed-row { white-space: pre-wrap; }
.feed-row .t  { color: var(--dim); }
.feed-row .ex { color: var(--dim); }
.feed-row a.run  { color: inherit; text-decoration: none; opacity: .75; }
.feed-row a.run:hover { text-decoration: underline; opacity: 1; }
.feed-row .st.complete { font-weight: 600; }
#bands h3 { margin: .7rem 0 .3rem; font-size: .95rem; }
.bandscroll { max-height: 30vh; overflow-y: auto; border: 1px solid var(--line);
              border-radius: .4rem; padding: .35rem .6rem; margin-bottom: .4rem;
              font-family: ui-monospace, monospace; font-size: .85rem; }
.band-row { padding: .15rem 0; }
.outscroll { max-height: 72vh; overflow-y: auto; }
.hitl { margin: .5rem 0 0; padding: .5rem .75rem; border: 1px solid var(--amberline);
        border-radius: .35rem; background: var(--ambersoft); }
.hitl-label { font-weight: 600; font-size: .9rem; margin-bottom: .45rem; }
.hitl a { color: var(--amber); word-break: break-all; }
.liveterm { max-height: 24rem; overflow-y: auto; }
.liveterm .lts { color: var(--dim); user-select: none; }
.liveclock { color: var(--green); font-size: .8rem; font-family: ui-monospace, monospace; }
/* m.rau/bibi#62: drei Stufen statt auto-fit — breit 1x4, schmal 2x2, ganz
   schmal 4x1. Mit `repeat(auto-fit, minmax(9rem, 1fr))` entschied der Browser,
   wie viele Spalten es gibt, und ergab je nach Fensterbreite auch 3+1 — genau
   die Anordnung, die die Anforderung ausschliesst. */
.statuscards { display: grid; grid-template-columns: repeat(4, 1fr);
               gap: .6rem; margin-bottom: 1.2rem; }
@media (max-width: 60rem) { .statuscards { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 32rem) { .statuscards { grid-template-columns: 1fr; } }
/* Header, zwei Bloecke nach Herkunft (bibi5, FE-Spezifikation §2). Kein
   Kasten, nur eine Haarlinie darunter — Layout "Kontenblatt": der Header ist
   Rahmen, kein Inhalt, und soll sich nicht wie eine Karte anfuehlen. */
.hdr { display: grid; grid-template-columns: 1fr 1fr; gap: .4rem 2.5rem;
       padding: .5rem 0 .7rem; border-bottom: 1px solid var(--line);
       margin-bottom: 1.1rem; font-size: .92rem; }
/* Jobs-Screen (bibi5). Kontenblatt: keine Rahmen, nur Haarlinien — die
   Bandkopfzeilen gliedern, nicht Kaesten. */
table.jobs { width: 100%; border-collapse: collapse; font-size: .92rem; }
table.jobs th { text-align: left; font-weight: 500; color: var(--hdr-key);
                padding: .3rem .6rem .3rem 0; }
table.jobs td { padding: .22rem .6rem .22rem 0; white-space: nowrap; }
table.jobs tbody tr:hover { background: var(--hover); }
/* Die Gruppenzeile ueber den zwei Zustandsbloecken. Gepunktet, weil sie eine
   Zusammenfassung ist und keine Spalte. */
table.jobs .gruppen th.grp { color: var(--dim); font-size: .82rem;
                             letter-spacing: .06em; border-bottom: 1px dotted var(--line-hard); }
/* Bandkopf: eine Zeile, die keine Daten traegt — deshalb ohne Hover. */
table.jobs tr.band td { padding-top: .9rem; font-weight: 600;
                        letter-spacing: .04em; border-bottom: 1px solid var(--line); }
table.jobs tr.band:hover { background: none; }
table.jobs tr.leer-band td { color: var(--dim); font-style: normal;
                             padding: .45rem 0 .3rem; white-space: normal; }
table.jobs td.slug a { color: var(--text); text-decoration: none; }
table.jobs td.slug a:hover { text-decoration: underline; }
/* Filterleiste. Textknoepfe, keine Kaesten — sie stehen ueber einer Tabelle
   und sollen sie nicht optisch erschlagen. */
.fltr-bar { display: flex; align-items: baseline; gap: .35rem;
            padding: .4rem 0 .7rem; flex-wrap: wrap; font-size: .9rem; }
.fltr-grp { color: var(--hdr-key); margin-right: .2rem; margin-left: .9rem;
            letter-spacing: .04em; font-size: .82rem; }
.fltr-grp:first-child { margin-left: 0; }
.fltr { background: none; border: 1px solid transparent; color: var(--dim);
        padding: .1rem .45rem; border-radius: 3px; cursor: pointer;
        font: inherit; }
.fltr:hover { color: var(--text); background: var(--hover); }
/* Der gewaehlte Zustand traegt Rahmen UND Farbe: Farbe allein geht in hellen
   Themes und auf schlechten Monitoren verloren. */
.fltr.on { color: var(--text); border-color: var(--btnline); background: var(--btnbg); }
.fltr-zahl { margin-left: auto; color: var(--dim); }
table.jobs th[data-sort] { cursor: pointer; user-select: none; }
table.jobs th[data-sort]:hover { color: var(--text); }
table.jobs th.sortiert { color: var(--text); }
/* Leerer Screen: kein Kasten, kein Ausrufezeichen — ein Satz, der sagt, was
   fehlt und was man tun kann. */
.leer { padding: 2.2rem 0; max-width: 42rem; }
.leer p { margin: 0 0 .5rem; }
.leer code { background: var(--btnbg); padding: .05rem .3rem; border-radius: 3px; }
.conn-dot { font-size: 1.05rem; line-height: 1; padding: 0 .1rem; cursor: default; }
.conn-dot.ok { color: var(--green); }
.conn-dot.warn { color: var(--amber); }
.conn-dot.bad { color: var(--red); }
.hdr-title { font-weight: 600; letter-spacing: .04em; margin-bottom: .25rem; }
.hdr-host { font-weight: 400; margin-left: .9rem; }
/* Der Bezugspunkt der absoluten Zeiten. Rechtsbuendig im Block, damit er die
   Werte darunter nicht verdraengt, aber im selben Blickfeld bleibt. */
.hdr-row { display: flex; gap: .8rem; line-height: 1.5; }
.hdr-label { color: var(--hdr-key); min-width: 5.5rem; flex: 0 0 auto; }
.hdr-row .hdr-value { color: var(--fg); }
/* Ausfall: der Block behaelt seine Werte und wird gedimmt. Ein alter Wert mit
   Datum sagt mehr als acht Platzhalter. */
.hdr-block.dimmed .hdr-value,
.hdr-block.dimmed .hdr-label { opacity: .45; }
/* Unterhalb 64rem brechen die zwei Bloecke untereinander statt zu zerschneiden. */
@media (max-width: 64rem) { .hdr { grid-template-columns: 1fr; gap: .9rem; } }
/* m.rau/bibi#66: Sortier-Koepfe. Der Link erbt die Kopf-Farbe — er soll wie
   eine Spalte aussehen, nicht wie ein Verweis; erst der Zeiger verraet, dass
   man klicken kann. Die aktive Spalte traegt zusaetzlich den Pfeil. */
th a { color: inherit; text-decoration: none; cursor: pointer; }
th a:hover { text-decoration: underline; }
th.sorted { font-weight: 700; }
.card { border: 1px solid var(--line); border-radius: .4rem; padding: .55rem .7rem; }
.card .label { font-size: .72rem; color: var(--faint); text-transform: uppercase; letter-spacing: .03em; }
.card .value { font-size: 1.05rem; font-weight: 600; margin-top: .1rem; }
.card .value.ok { color: var(--green); }
.card .value.bad { color: var(--red); }
.card .sub { font-size: .75rem; color: var(--dim); margin-top: .15rem; }
/* Mehrzeilige Karten (PLAN-19 Befund 4, User-Entscheidung: Git UND Mode im
   selben 3-Zeilen-Stil, kein Trenner-Punkt mehr) — Host/Mode/Git ersetzen die
   bisherigen 6 Kacheln des Feed-Headers. */
.card .cardline { font-size: 1.05rem; font-weight: 600; margin-top: .1rem; }
.card .cardline.ok, .card a.ok { color: var(--green); }
.card .cardline.bad, .card a.bad { color: var(--red); }
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
.card .kvgrid .k { font-size: .72rem; font-weight: 400; color: var(--faint);
                   text-transform: uppercase; letter-spacing: .03em; align-self: center; }
.card .kvgrid .v { font-size: 1.05rem; font-weight: 600; }
.card .kvgrid .v.ok { color: var(--green); }
.card .kvgrid .v.bad { color: var(--red); }
/* Job-Status-Matrix (Bibi4-Iteration, User-Fund: "Apps enden nicht" — eigene
   Spalte je Kind statt der bisherigen 2x2-Aggregation ohne Kind-Aufschlüsselung,
   löst .kvgrid2 ab). 4 Spalten: Label + job/claude/app, row-major befüllt
   (Header-Zeile, dann Waiting/Running/Stopped). */
.card .jobstatus-grid { display: grid; grid-template-columns: auto repeat(3, minmax(2.2rem, auto));
                        row-gap: .2rem; column-gap: .6em; margin-top: .15rem; }
.card .jobstatus-grid .jsg-h { font-size: .68rem; font-weight: 400; color: var(--faint);
                               text-transform: uppercase; letter-spacing: .03em; text-align: right; }
.card .jobstatus-grid .jsg-k { font-size: .72rem; font-weight: 400; color: var(--faint);
                               text-transform: uppercase; letter-spacing: .03em; align-self: center; }
.card .jobstatus-grid .jsg-v { font-size: 1.0rem; font-weight: 600; text-align: right; }
.side-empty { color: var(--dim); font-size: .82rem; }
.chip { font-family: ui-monospace, monospace; font-size: .7rem; font-weight: 700;
        padding: .1rem .45rem; border-radius: .3rem; display: inline-block; white-space: nowrap; }
/* Git-Status je Job-MD (PLAN-21 Befund 10) — löst die vorherige Lokal/Remote-
   Abgleich-Chips (same/diff/local_only/remote_only) ab. */
.chip.clean { background: var(--greensoft); color: var(--green); }
.chip.modified { background: var(--ambersoft); color: var(--amber); }
.chip.new { background: var(--bluesoft); color: var(--blue); }
.chip.conflict { background: var(--redsoft); color: var(--red); }
/* Nodes-Screen Git-Status-Chips (Batch 9 Punkt 3) — dieselben Farben wie
   .tree-*/.sync-* (Feed-Git-Kachel), hier als Chip statt Klartext. */
.chip.synced { background: var(--greensoft); color: var(--green); }
.chip.ahead { background: var(--ambersoft); color: var(--amber); }
.chip.behind, .chip.diverged { background: var(--redsoft); color: var(--red); }
.startbtn { font: inherit; font-size: .78rem; background: var(--bluesoft); border: 1px solid var(--blueline);
        border-radius: .35rem; padding: .2rem .55rem; cursor: pointer; color: inherit; font-weight: 600;
        white-space: nowrap; }
.killbtn { font: inherit; font-size: .78rem; background: var(--redsoft); border: 1px solid var(--redline);
        border-radius: .35rem; padding: .2rem .55rem; cursor: pointer; color: inherit; font-weight: 600;
        white-space: nowrap; }
.startbtn:disabled, .killbtn:disabled { opacity: .4; cursor: default; }
.runhist { font-size: .86rem; }
.runhist .row { display: flex; gap: .8rem; padding: .35rem 0; border-bottom: 1px solid var(--line);
                align-items: baseline; }
.runhist a.row:hover { background: var(--hover); }
.runhist .t { color: var(--dim); font-family: ui-monospace, monospace; font-size: .78rem; flex: 0 0 4.4rem; }
.gitsegment { font-family: ui-monospace, monospace; font-size: .95rem; }
.gitsegment .sep { color: var(--faint); }
.tree-clean, .sync-synced { color: var(--green); }
.tree-modified, .sync-ahead { color: var(--amber); }
.sync-behind, .sync-conflict { color: var(--red); }
/* .panel-card: generischer Rahmen um eine Liste. */
.panel-card { border: 1px solid var(--line); border-radius: .4rem; padding: .7rem 1rem .6rem;
              margin: .5rem 0 1rem; }
.panel-card > h2:first-child { margin-top: 0; }
/* Feed: eine Zeile je Einheit — Zeit · Einheit · Umfang · Wer · Commit. */
.feedlist { display: flex; flex-direction: column; gap: 0; font-size: .88rem; }
.frow { display: flex; gap: .6rem; align-items: baseline; padding: .38rem 0;
        border-bottom: 1px solid var(--line); }
.frow .t { color: var(--dim); font-family: ui-monospace, monospace; font-size: .78rem;
           flex: 0 0 3.4rem; }
/* Ohne min-width:0 hat ein Flex-Item eine implizite Mindestbreite gleich seinem
   Inhalt — ein langer Slug und die Urheberliste liefen sonst über den Rand. */
/* Der Slug bricht **nie** mitten im Wort (Befund m.rau: `20260531.Continuou` /
   `sCollection-` / `a0bc0dcc`). `overflow-wrap: anywhere` stammt aus bibi4 und
   war dort fuer die Autorenliste gedacht — auf einem Namen ist es falsch. */
.frow .msg { flex: 1; min-width: 0; overflow-wrap: normal; word-break: keep-all; }
.frow .cnt { color: var(--dim); font-family: ui-monospace, monospace; font-size: .78rem;
             flex: 0 0 auto; white-space: nowrap; }
.frow .who { color: var(--dim); font-size: .78rem; flex: 0 1 auto; min-width: 0;
             overflow-wrap: anywhere; }
.frow a.commit { text-decoration: none; }
.frow a.commit:hover { text-decoration: underline; color: var(--blue); }
/* Tagestrennlinie, dasselbe Idiom wie in der Lauf-Liste von Job Detail. */
.fday { display: flex; align-items: center; gap: .6rem; margin: .9rem 0 .2rem;
        font-family: ui-monospace, monospace; font-size: .72rem; color: var(--faint); }
.fday::after { content: ""; flex: 1; border-top: 1px solid var(--line); }
.freach { font-size: .78rem; color: var(--faint); margin: 0 0 .3rem; }
/* m.rau/bibi#63: in der Karte, an ihrem unteren linken Rand. Der obere
   Abstand trennt vom Inhalt darueber, der untere entfaellt — die Karte
   bringt ihr eigenes Padding mit. */
.loadmore { display: flex; justify-content: flex-start; gap: .5rem;
            margin: .8rem 0 0; }
/* ══ Job Detail und Attributes ═════════════════════════════════════════════
   Diese Screens entstanden in Schritt 2 und wurden **ohne eine einzige
   CSS-Regel** ausgeliefert: 27 Klassen gab es nur im Markup. Ohne Regel sind
   Spans inline ohne Abstand, weshalb die Kopfzeile als
   `jobsgmail-billingjob` und die Attributseite als `attempts3` erschien.
   `test_every_markup_class_has_a_css_rule` faengt diesen Fall jetzt ab.

   Kein Cron-Ausdruck in diesem Kommentar: ein `*` gefolgt von `/` schliesst
   ihn vorzeitig, und der Parser verwirft dann die erste Regel dahinter. Genau
   das ist hier passiert — `.jd-head` blieb `display: block`, waehrend alles
   andere wirkte.

   Die Formen folgen dem, was Feed und Jobs schon benutzen — Beschriftungen in
   `--hdr-key`, Trennlinien in `--line`, Monospace fuer alles Zaehlbare. Neu
   ist nur die Slot-Kachel, und sie ist bewusst eine Kachel: sie traegt
   Steuerung, waehrend die Liste darunter Historie traegt. */

/* Seitenkopf: `◂ back`, Slug, Beziehung, Meta rechts. */
.jd-head { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap;
           padding: .5rem 0 .55rem; border-bottom: 1px solid var(--line); }
.jd-slug { font-weight: 700; font-size: 1.05rem; }
.jd-meta { color: var(--dim); font-size: .8rem; margin-left: auto;
           font-family: ui-monospace, monospace; }
.back { color: var(--dim); text-decoration: none; font-size: .85rem; }
.back:hover { color: inherit; text-decoration: underline; }
.rel { font-size: .72rem; color: var(--dim); }
.subhead { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap;
           margin: .9rem 0 .35rem; font-size: .8rem; color: var(--dim); }

/* Slot-Kacheln: Zustand und Verben. Steuerung, sonst nichts (FE §5.1.1).
   **Nebeneinander**, weil sie gleichrangig sind und staendig verglichen werden
   („laeuft es beim Scheduler, aber lokal nicht?"). `1fr` je Kachel statt
   `auto`: sonst waere die mit dem laengeren Zustand breiter, und die beiden
   Seiten saehen ungleich gewichtet aus, obwohl sie es nicht sind. */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
         gap: .6rem; margin: .7rem 0 .9rem; }
.tile { display: flex; flex-direction: column; gap: .3rem;
        padding: .55rem .7rem;
        border: 1px solid var(--line-hard); border-radius: .4rem; }
.tile-head { font-weight: 700; font-size: .78rem; letter-spacing: .03em;
             color: var(--hdr-key); }
.tile-state { font-family: ui-monospace, monospace; font-size: .85rem; }
.slot-none { color: var(--faint); font-size: .8rem; font-style: italic; }

/* Kopfzeile der Lauf-Liste: Herkunft mit Zaehlung, Zustaende, Reichweite.
   Die Zaehlung ist der Ersatz fuer die frueher faltbaren Quell-Gruppen — sie
   zeigt, *dass* es die andere Seite gibt, ohne dass man sie aufklappen muss. */
.runs-head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap;
             margin: .2rem 0 .35rem; font-size: .8rem; }
.runs-title { font-weight: 700; letter-spacing: .03em; color: var(--hdr-key); }
.runs-src, .runs-states { display: inline-flex; align-items: baseline; gap: .3rem;
                          flex-wrap: wrap; }
/* Die Filter-Chips brauchen einen **sichtbaren** An-Zustand: ein Toggle, dem
   man nicht ansieht, ob er greift, ist keiner. Terracotta traegt in dieser UI
   genau eine Bedeutung — Interaktion —, gefuellt heisst „wirkt gerade". */
.runs-head .chip { text-decoration: none; color: var(--faint);
                   border: 1px solid transparent; font-weight: 600; }
.runs-head .chip:hover { color: inherit; border-color: var(--btnline); }
.runs-head .chip-on { color: var(--bg); background: var(--brand);
                      border-color: var(--brand); }
/* Die Reichweite ganz rechts: sie beantwortet „ist da noch mehr?", und diese
   Frage stellt sich am Ende des Lesens, nicht am Anfang. */
.runs-reach { margin-left: auto; color: var(--faint);
              font-family: ui-monospace, monospace; font-size: .78rem; }
/* Die Marke „steht im Slot": schmal, damit sie nicht als Spalte gelesen wird —
   sie gehoert zur Zeile, nicht zu den Werten. */
.mark { width: 1.1rem; color: var(--brand); text-align: center; }
.src { color: var(--faint); font-family: ui-monospace, monospace; }
/* Ein Lauf, der noch im Slot steht, ist der einzige, den man beeinflussen
   kann — deshalb hebt ihn ein Rand hervor und keine Flaeche: eine gefaerbte
   Zeile laese sich als Fehler lesen.

   Nur die **erste** Zelle traegt die Linie. Auf allen `td` gesetzt zeichnet sie
   an jeder Zellgrenze und die Zeile sieht aus wie ein Gitter — live gesehen. */
.run-in-slot td:first-child { box-shadow: inset 2px 0 0 var(--brand); }
/* Die Leiste haelt Abstand zum Zustand links — sonst klebt `[START]` am Wort. */
.slot-bar { display: inline-flex; align-items: center; gap: .45rem; }
/* Ein verfuegbares Verb muss sich vom ausgegrauten unterscheiden — sonst
   sieht die ganze Leiste tot aus, auch wo sie es nicht ist (live gesehen:
   zwei klickbare Knoepfe, die wie deaktiviert wirkten). Terracotta traegt in
   dieser UI genau eine Bedeutung: Interaktion. Hier ist sie richtig. */
.slot-do { font: inherit; font-size: .78rem; letter-spacing: .03em;
           font-weight: 600; padding: .12rem .6rem; cursor: pointer;
           color: var(--brand); background: var(--btnbg);
           border: 1px solid var(--brand); border-radius: .3rem; }
.slot-do:hover { background: var(--brand); color: var(--bg); }
.slot-do:disabled { opacity: .4; cursor: default; border-color: var(--btnline);
                    color: inherit; }
/* Nicht verfuegbare Verben bleiben sichtbar und ausgegraut (FE §5.2) — sonst
   springt das Layout und die Aussage „das geht hier nicht" geht verloren. */
.slot-off { font-size: .78rem; letter-spacing: .03em; color: var(--faint);
            padding: .12rem .35rem; }

/* Die Lauf-Liste (FE-Spezifikation §5.3). */
.runs { width: 100%; border-collapse: collapse; font-size: .85rem; }
.runs th { text-align: left; font-weight: 600; font-size: .72rem;
           letter-spacing: .03em; color: var(--faint); text-transform: uppercase;
           padding: .3rem .5rem .3rem 0; border-bottom: 1px solid var(--line); }
.runs td { padding: .28rem .5rem .28rem 0; border-bottom: 1px solid var(--line);
           vertical-align: baseline; }
.runs td:first-child, .runs th:first-child { padding-left: .2rem; }
.runs tr:hover td { background: var(--hover); }
/* Die Tagestrennzeile ist kein Datensatz — ohne eigene Form las sie sich als
   leere Zeile mit einem Datum links (Befund m.rau: „warum die leeren Zeilen"). */
.runs tr.day td { font-family: ui-monospace, monospace; font-size: .72rem;
                  color: var(--faint); padding-top: .8rem;
                  border-bottom: 1px solid var(--line-hard); }
.runs .t { font-family: ui-monospace, monospace; white-space: nowrap; }
.cta { color: var(--dim); text-decoration: none; font-size: .8rem;
       white-space: nowrap; cursor: pointer; background: none; border: 0;
       font-family: inherit; padding: 0; }
.cta:hover { color: var(--brand); text-decoration: underline; }
.run-show { text-align: right; }

/* Ausgeklappter Output: feste Hoehe, eigener Scrollbereich. */
.out { padding: 0 !important; }
.out-body { max-height: 20lh; overflow: auto; margin: .3rem 0 .5rem;
            padding: .5rem .7rem; border: 1px solid var(--line-hard);
            border-radius: .35rem; background: var(--hover);
            font-family: ui-monospace, monospace; font-size: .78rem;
            line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
.fold { cursor: pointer; user-select: none; }

/* Attribute: Beschriftung links, Wert rechts, Defaults gedimmt und geklammert. */
.attrs { margin: .6rem 0 1rem; }
.attrtable { border-collapse: collapse; font-size: .85rem; }
.attr-row { display: grid; grid-template-columns: 12rem 1fr; gap: .4rem;
            padding: .18rem 0; }
.attr-key { color: var(--hdr-key); font-family: ui-monospace, monospace;
            font-size: .8rem; }
/* Zwei Signale fuer „Default", nicht eins: die Dimmung geht in hellen Themes
   und auf schlechten Monitoren verloren, die Klammer nicht. */
.ts-dim { color: var(--faint); }

/* Leerer Zustand und Nachladen. */
.empty { color: var(--dim); font-size: .85rem; font-style: italic;
         padding: .8rem .2rem; }
.more { display: flex; justify-content: flex-start; gap: .5rem; margin: .8rem 0 0; }

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
    if d < 10:
        # Eine Nachkommastelle, solange sie etwas unterscheidet: die meisten
        # Laeufe dauern zwei bis acht Sekunden, und als ganze Zahl sehen sie
        # alle gleich aus. Ab zehn Sekunden traegt die Stelle nichts mehr.
        return f"{max(0.0, float(seconds)):.1f}s"
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


#: Terminale Sicht-Zustände — ein One-shot in einem davon gilt als „abgelaufen".
#:
#: Der Abschnitt „Volle Schedule-Liste + Archiv", der hier stand, ist leer:
#: seine Renderer sind mit m.rau/bibi#130 entfallen. Die Konstante bleibt, sie
#: hat drei Aufrufer im Live- und Jobs-Teil.
_TERMINAL_VIEW = {"complete", "error", "inactive", "zombie", "killed"}


# ── Connected-Clients-Screen (Host, Bibi4-Iteration) ─────────────────────────
# Backend existierte schon lange vor diesem Screen (WorkerRegistry, /-/worker,
# in /-/status.workers exponiert) — hier nur die erste Darstellung dafür.
# node_id/git_user (Bibi4-Iteration, User-Fund: "wir brauchen unbedingt den
# hinterlegten gitea/git Nutzernamen") reisen seit dem Heartbeat-Ausbau mit.
# role (zweite Bibi4-Iteration, User-Fund: "Client Übersicht braucht die
# Rollen je Client") denselben Weg: Heartbeat -> WorkerRegistry -> hier.

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


#: Aktualitäts-Chip der Engine-Zelle. Die Wörter sind bewusst die der
#: Repo-Zelle (``synced``/``behind``) statt der internen Verdict-Namen aus
#: ``deploy.update_state()`` — nebeneinander gelesen sollen beide Zeilen
#: dieselbe Sprache sprechen (m.rau/bibi#67).
_NODE_ENGINE_VERDICT: dict[str, tuple[str, str]] = {
    "current": ("chip clean", "läuft auf dem erwarteten Tag"),
    "behind": ("chip conflict", "ein neuerer Stand ist gepinnt"),
    "branch": ("chip modified",
               "an einen Branch gepinnt — ob er weitergewandert ist, "
               "weiß dieser Knoten nicht"),
    "unknown": ("chip", "Soll- oder Ist-Stand fehlt"),
}


def _node_engine_cell(engine: str | None, expected: str | None = None,
                      tree: str | None = None) -> str:
    """Installierter Engine-Stand je Knoten (m.rau/bibi#19, erweitert #43).

    ``engine`` ist die fertige Bezeichnung aus ``engine_info.EngineInfo.label()``
    — hier wird nur dekoriert, nicht interpretiert. Ein Tag ("v0.2.0") steht
    neutral; ein editable install bekommt einen Warn-Chip, denn er ist die
    stille Falle, um die es in dem Issue eigentlich geht: ein Knoten, der gegen
    ein Arbeits-Checkout läuft statt gegen den gepinnten Stand, sah bisher aus
    wie jeder andere.

    ``expected`` (m.rau/bibi#43) trägt die zweite Hälfte nach: der Screen zeigte
    bisher, *was läuft*, nicht *was laufen sollte*. Weichen beide ab, kommt ein
    NEED-UPDATE-Chip dazu. Dass der Soll-Stand für alle Knoten derselbe ist, ist
    keine Annahme, sondern folgt aus der geteilten ``uv.lock`` — ein Knotennetz
    fährt ein Release."""
    if not engine:
        return "—"

    def _tree_chip() -> str:
        """Arbeitsbaum-Chip — **nur wo es einen Arbeitsbaum gibt.**

        Ein VCS-Pin hat keinen. Dort ``clean`` zu zeigen wäre eine Aussage, die
        niemand geprüft hat; der Chip entfällt stattdessen ganz. Dieselbe
        Zurückhaltung, mit der ``update_state()`` bei einem Branch-Pin lieber
        „unbestimmt" sagt als zu raten.
        """
        if not tree:
            return ""
        cls = _NODE_TREE_CHIP_CLASS.get(tree, "chip")
        return f' <span class="{cls}">{_e(tree)}</span>'

    for marker, title in (
            ("(editable)", "laeuft gegen ein Arbeits-Checkout, nicht gegen den "
                           "gepinnten Stand"),
            # m.rau/bibi#58: eine Kopie eines Verzeichnisses sieht aus wie ein
            # Release und ist keins — derselbe Unterschied zum gepinnten Stand
            # wie beim editable install, nur schlechter zu bemerken.
            ("(local)", "aus einem lokalen Verzeichnis installiert, nicht aus "
                        "dem gepinnten Tag")):
        if marker in engine:
            base = engine.replace(marker, "").strip()
            return (f'{_e(base)}{_tree_chip()} '
                    f'<span class="chip conflict" title="{title}">'
                    f'{marker.strip("()")}</span>')

    from bibi.daemon import deploy as deploy_mod
    verdict = deploy_mod.label_verdict(expected, engine)
    cls, title = _NODE_ENGINE_VERDICT.get(
        verdict, ("chip", "Stand nicht bestimmbar"))
    cell = f'{_e(engine)}{_tree_chip()}'
    cell += f' <span class="{cls}" title="{_e(title)}">{_e(verdict)}</span>'
    if verdict == "behind":
        cell += (f' <span class="chip conflict" title="expected {_e(expected or "")}">'
                 "NEED UPDATE</span>")
    return cell


def _node_restart_cell(node_id: str | None, port: int | None,
                       session: bool | None = None) -> str:
    """Neustart-Knöpfe je Knoten (m.rau/bibi#39).

    Zwei getrennte Verben statt eines mit Häkchen: **Restart** beendet nur den
    Prozess, **Deploy** pullt vorher. Der Unterschied ist bedeutsam genug, ihn
    nicht hinter einer Option zu verstecken — der eine holt einen neuen Stand,
    der andere nicht.

    Ohne ``port`` (älterer Client, oder erster Heartbeat noch nicht durch) gibt
    es keine Adresse zum Aufrufen; dann bleibt die Zelle leer statt einen Knopf
    anzubieten, der ins Leere liefe.

    ``hx-confirm`` bei beiden: ein Klick, der einen laufenden Knoten beendet,
    darf nicht versehentlich passieren. Der Drain (#38) macht ihn verantwortbar,
    nicht folgenlos.

    ``session=True`` ändert Chip, Verb und Rückfrage (m.rau/bibi#44). Der
    Endpunkt beendet den Prozess und verlässt sich auf einen Supervisor — den
    ein Sitzungs-Daemon nicht hat. Das erst im Ergebnis zu erwähnen wäre zu
    spät: wer den Knopf für seinen eigenen Knoten drückt, hat dann bereits
    seine Sitzung abgeschossen. Deshalb steht es **vor** dem Klick da, und
    zwar zweimal — als Chip für den, der die Tabelle überfliegt, und in der
    Rückfrage für den, der schon klickt.

    ``session=None`` heißt *unbekannt* (Client älter als diese Änderung) und
    verhält sich unverändert wie bisher: eine Behauptung über die Herkunft
    wäre hier schlechter als keine.
    """
    if not node_id or not port:
        return "—"
    base = 'hx-target="#clientsboard" hx-swap="outerHTML" hx-disabled-elt="this"'
    nid = _e(node_id)
    if session:
        warn = ("This node runs inside a session — it will stop and nobody "
                "brings it back. Start it again with: bibi")
        return (
            f'<span class="chip modified" title="no supervisor">session</span> '
            f'<button class="killbtn" hx-post="/-/ui/clients/{nid}/restart" '
            f'hx-confirm="{_e(warn)}" {base}>Stop{_BTN_SPINNER}</button> '
            f'<button class="killbtn" hx-post="/-/ui/clients/{nid}/deploy" '
            f'hx-confirm="{_e("Pull the new state, then stop. " + warn)}" {base}>'
            f'Deploy + stop{_BTN_SPINNER}</button>'
        )
    return (
        f'<button class="startbtn" hx-post="/-/ui/clients/{nid}/restart" '
        f'hx-confirm="Restart this node?" {base}>Restart{_BTN_SPINNER}</button> '
        f'<button class="startbtn" hx-post="/-/ui/clients/{nid}/deploy" '
        f'hx-confirm="Pull the new state and restart?" {base}>'
        f'Deploy{_BTN_SPINNER}</button>'
    )


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


def _expected_ref() -> str | None:
    """Der Soll-Stand aus ``pyproject.toml`` — defensiv (§2.7), er ist Beiwerk
    und darf den Screen nie kosten."""
    try:
        from bibi.daemon import deploy as deploy_mod
        return deploy_mod.current_ref()
    except Exception:  # noqa: BLE001
        return None


def _clients_table(workers: list[dict], now: float,
                   expected: str | None = None) -> str:
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
            f"<td>{_node_engine_cell(w.get('engine'), expected, w.get('engine_tree'))}</td>"
            f"<td>{_e(w.get('git_user') or '—')}</td>"
            f"<td>{_node_git_status_chips(w.get('git_status'))}"
            + (f' <code>{_e(w["git_commit"])}</code>' if w.get("git_commit") else "")
            + "</td>"
            f"<td>{status_html}</td>"
            f"<td>{_node_approval_cell(w.get('node_id'), w.get('approval_status', 'pending'))}</td>"
            f"<td>{_node_restart_cell(w.get('node_id'), w.get('port'), w.get('session'))}</td>"
            f"<td>{_abs_datetime(w.get('connected_at'), now)}</td>"
            f"<td>{_ago(w.get('last_heartbeat'), now)}</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Name</th>'
        f"{_role_matrix_header()}"
        '<th>Engine</th><th>Git-User</th>'
        '<th>Git-Status</th><th>Status</th><th>Freigabe</th><th>Neustart</th>'
        '<th>Connected seit</th>'
        '<th>Letzter Heartbeat</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _expected_version_form(deploy_result: dict | None) -> str:
    """Feld für die erwartete Engine-Version (m.rau/bibi#39).

    Der aktuelle Ref kommt aus ``pyproject.toml`` — also aus der Absicht, nicht
    aus der Lock. Genau der Unterschied ist der Punkt: hier wird die Absicht
    gesetzt, das Ergebnis erzeugt ``uv lock``.

    Zwei Knöpfe: **Setzen** schreibt und pusht, **Setzen + Ausrollen** stößt
    zusätzlich den Neustart aller Knoten an. Getrennt, weil man den Lock-Diff
    sehen wollen kann, bevor drei Daemons durchstarten.

    Die **Auswahlliste** der verfügbaren Releases kam auf Wunsch von m.rau dazu
    (2026-07-31, zusammen mit dem Fehlerbericht zum leeren Ref). Bewusst ein
    ``datalist`` und kein ``select``: das Feld bleibt ein freies Textfeld, sonst
    ginge das Branch-Pinning (``dev``) verloren — und genau das unterscheidet
    ``update_status()``s Urteil „branch" von „outdated". Eine Version von Hand
    einzutippen heißt, sie vorher woanders nachgeschlagen zu haben; ein
    Tippfehler kostet einen ``uv lock``-Fehlschlag, bis er auffällt.
    """
    cur = _expected_ref() or "?"
    try:
        from bibi.daemon import deploy as deploy_mod
        refs = deploy_mod.available_refs()
    except Exception:  # noqa: BLE001 — defensiv (§2.7); die Liste ist Komfort
        refs = []
    datalist = ""
    list_attr = ""
    if refs:
        list_attr = ' list="engine-refs"'
        datalist = ('<datalist id="engine-refs">'
                    + "".join(f'<option value="{_e(r)}">' for r in refs)
                    + "</datalist>")
    msg = ""
    if deploy_result:
        if deploy_result.get("ok") and deploy_result.get("changed"):
            msg = (f'<span class="chip clean">gesetzt: {_e(deploy_result.get("ref",""))}</span>'
                   f' <span class="ts-dim">(war {_e(deploy_result.get("was",""))}'
                   f'{", gepusht" if deploy_result.get("pushed") else ", NICHT gepusht"})</span>')
        elif deploy_result.get("ok"):
            msg = f'<span class="ts-dim">{_e(deploy_result.get("note",""))}</span>'
        else:
            # Der Fehlerfall ist der wichtigere: uv lock scheitert, wenn der Tag
            # nicht existiert — dann wurde zurückgerollt und nichts committet.
            msg = (f'<span class="chip conflict">{_e(deploy_result.get("error",""))}</span>'
                   f' <span class="ts-dim">{_e(deploy_result.get("detail",""))}</span>')
    return (
        '<p class="handles">'
        '<label>Erwartete Engine-Version '
        f'<input name="version" value="{_e(cur)}" size="14"{list_attr}></label>'
        # Der Stand, den DIESE Seite beim Rendern gesehen hat (m.rau/bibi#57).
        # Ein Tab, der vor einem Release geöffnet wurde, trägt im Feld oben den
        # alten Ref; ein Klick auf „Setzen" schriebe ihn zurück und stufte beim
        # nächsten Neustart jeden Knoten herab. Genau das ist am 2026-07-31 um
        # 16:20 passiert. Der Server vergleicht dieses Feld gegen den
        # tatsächlichen Stand und schreibt bei Abweichung nicht.
        f'<input type="hidden" name="seen" value="{_e(cur)}">'
        f"{datalist} "
        '<button class="startbtn" hx-post="/-/ui/clients/expected-version" '
        'hx-include="closest p" hx-target="#clientsboard" hx-swap="outerHTML" '
        f'hx-disabled-elt="this">Setzen{_BTN_SPINNER}</button> '
        '<button class="startbtn" hx-post="/-/ui/clients/expected-version?deploy=true" '
        'hx-include="closest p" hx-confirm="Version setzen UND alle Knoten neu starten?" '
        'hx-target="#clientsboard" hx-swap="outerHTML" hx-disabled-elt="this">'
        f'Setzen + Ausrollen{_BTN_SPINNER}</button> {msg}'
        '</p>'
    )


def clients_fragment(workers: list[dict], now: float | None = None, *,
                     deploy_result: dict | None = None) -> str:
    now = time.time() if now is None else now
    return (
        '<div id="clientsboard" data-bus="nodes" data-bus-refetch="/-/ui/clients/board">'
        '<div class="panel-card"><h2>Nodes</h2>'
        f"{_expected_version_form(deploy_result)}"
        # „Restart all" (m.rau/bibi#39) im Panel-Kopf, nicht je Zeile: es ist
        # eine Aktion auf die Föderation, nicht auf einen Knoten. Rollierend
        # ausgeführt (Clients zuerst, Host zuletzt) — siehe clients_restart_all().
        '<p class="handles">'
        '<button class="startbtn" hx-post="/-/ui/clients/restart-all" '
        'hx-confirm="ALLE Knoten neu starten?" hx-target="#clientsboard" '
        f'hx-swap="outerHTML" hx-disabled-elt="this">Restart all{_BTN_SPINNER}</button> '
        '<button class="startbtn" hx-post="/-/ui/clients/restart-all?deploy=true" '
        'hx-confirm="Auf ALLEN Knoten den neuen Stand holen und neu starten?" '
        'hx-target="#clientsboard" hx-swap="outerHTML" hx-disabled-elt="this">'
        f'Deploy all{_BTN_SPINNER}</button>'
        '</p>'
        f"{_clients_table(workers, now, _expected_ref())}</div>"
        "</div>"
    )


def clients_page(workers: list[dict], now: float | None = None, *,
                 daemon_status: dict | None = None, git_status: dict | None = None,
                 host_url: str | None = None,
        scheduler: dict | None = None,
        scheduler_stale_since: float | None = None,) -> str:
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
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Nodes', daemon_status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{clients_fragment(workers, now)}"
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_JS}</script>"
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

#: UND Zustands-Chips im Chart-Kopf — dieselbe Farbe an beiden Stellen macht
#: eine separate Legende redundant, User-Fund 2026-07-08). Sechs klar
#: unterscheidbare Töne — vorher teilten sich error/zombie sowie killed/
#: _WAITING_COLOR versehentlich denselben Hex-Wert.
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
_RESOLUTION_LABEL = {1440: "24h/1m", 480: "8h/1w", 180: "3h/3d", 120: "2h/2d",
                     15: "15min/24h", 5: "5min/8h", 1: "1min/2h"}



#: Filter-Optionen. „problem" ist eine **Gruppe** (Abweichungen als Filter statt
#: eigenem Block): failed/error/killed/zombie + überfällig (pending, fällig verpasst).
_SCHED_TYPES = ("job", "claude")
_SCHED_STATUSES = ("starting", "running", "pending", "complete", "failed", "deferred", "problem")
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


#: Sortierschlüssel → wie der Wert aus einer Zeile kommt (m.rau/bibi#66).
#: Die Schlüssel sind bewusst die Spalten**bedeutungen**, nicht Feldnamen: Host-
#: und Client-Zeilen tragen dieselbe Information unter teils anderen Namen, und
#: der Nutzer sortiert nach dem, was in der Spalte steht.
_SORT_KEYS: dict[str, "callable"] = {
    "slug": lambda r: (r.get("slug") or "").lower(),
    # Nach dem *angezeigten* Typ, nicht nach dem rohen Payload — sonst
    # sortierte die Spalte nach etwas anderem, als in ihr steht.
    "type": lambda r: _effective_sched_type(r),
    "status": lambda r: (r.get("last_status") or r.get("row_status") or ""),
    "last": lambda r: r.get("finished_at") if r.get("finished_at") is not None
                      else r.get("started_at"),
    "runtime": lambda r: r.get("runtime"),
    "next": lambda r: r.get("next_fire_at"),
}


def sort_rows(rows: list[dict], sort: str | None,
              direction: str | None = "asc") -> list[dict]:
    """Zeilen serverseitig sortieren (m.rau/bibi#66).

    **Serverseitig und nicht in JS**, weil der Event-Bus die Region neu rendert:
    eine clientseitige Sortierung wäre beim nächsten Refetch weg. Das Issue
    nennt genau diesen Grund, und er wiegt schwerer als der Geschwindigkeits-
    vorteil — die Tabellen haben Dutzende Zeilen, nicht Tausende.

    Ein unbekannter Schlüssel lässt die Reihenfolge unangetastet, statt zu
    werfen oder zu leeren: er kann aus einem alten Cookie oder einer von Hand
    zusammengesetzten URL kommen, und beides darf keinen Screen kosten.

    ``None``-Werte landen **immer am Ende**, in beide Richtungen. „Kein Wert"
    heisst *gibt es nicht*, nicht *ganz früh* — beim Umdrehen füllten sie sonst
    den Anfang und verdrängten genau das, wonach jemand gerade sucht.
    """
    fn = _SORT_KEYS.get(sort or "")
    if fn is None:
        return rows
    rev = (direction or "asc") == "desc"

    def _key(r: dict):
        v = fn(r)
        if v is None:
            # Erstes Tupelglied: fehlende Werte hinten, unabhängig von rev.
            return (1, 0) if not rev else (-1, 0)
        return (0, v) if not rev else (0, v)

    missing = [r for r in rows if fn(r) is None]
    present = [r for r in rows if fn(r) is not None]
    present.sort(key=fn, reverse=rev)
    return present + missing


def _sortable_head(columns: list[tuple[str, str | None]], *, sort: str | None,
                   direction: str | None, url: str, target: str) -> str:
    """``<thead>`` mit klickbaren Spalten (m.rau/bibi#66).

    Ein Klick auf die aktive Spalte dreht die Richtung um, ein Klick auf eine
    andere startet aufsteigend — das ist die Erwartung, die jede Tabelle
    irgendeiner Oberfläche bedient, und eine Abweichung davon müsste man
    erklären.

    Spalten ohne Schlüssel (``None``) bleiben gewöhnliche Köpfe — ein
    Sortier-Link auf etwas Unsortierbares wäre ein Angebot, das nichts
    einlöst.
    """
    cells = []
    for label, key in columns:
        if key is None:
            cells.append(f"<th>{_e(label)}</th>")
            continue
        active = key == sort
        nxt = "desc" if active and (direction or "asc") == "asc" else "asc"
        arrow = "" if not active else (" ▾" if (direction or "asc") == "desc" else " ▴")
        cls = ' class="sorted"' if active else ""
        href = f"{url}{'&' if '?' in url else '?'}sort={key}&dir={nxt}"
        cells.append(
            f'<th{cls}><a href="#" hx-get="{_e(href)}" hx-target="{_e(target)}" '
            f'hx-swap="outerHTML" hx-include="[name=\'typ\'],[name=\'status\']">'
            f"{_e(label)}{arrow}</a></th>")
    return "<thead><tr>" + "".join(cells) + "</tr></thead>"


def client_row_status(row: dict, local_runs: dict[str, dict]) -> str | None:
    """Der Status, den eine Client-Zeile **anzeigt** (m.rau/bibi#65).

    Dieselbe dreistufige Ermittlung wie in ``_jobs_row()``, nur an einer Stelle
    statt an zweien: ein laufender Job schlägt den letzten Lauf, sonst gilt der
    letzte Lauf, sonst gibt es keinen Status ("noch nie lokal gelaufen").

    **Warum das eine eigene Funktion ist und keine Kopie:** Anzeige und Filter
    müssen denselben Wert benutzen. Täten sie es nicht, filterte der Nutzer
    nach etwas anderem, als er sieht — und das fiele erst auf, wenn eine Zeile
    unerklärlich verschwindet. Genau deshalb ruft ``_jobs_row()`` sie ebenfalls
    auf, statt die Logik ein zweites Mal zu schreiben.
    """
    live = row.get("live")
    if live:
        # Ein Live-Eintrag ohne eigenen Status heisst laufend — dieselbe
        # Auslegung wie in der Statuszelle.
        return live.get("status") or "running"
    lr = local_runs.get(row.get("slug"))
    return lr.get("status") if lr else None


def enrich_client_rows(rows: list[dict], local_runs: dict[str, dict]) -> list[dict]:
    """Client-Zeilen um ``last_status`` ergänzen, damit ``filter_schedules()``
    unverändert auf ihnen arbeitet (m.rau/bibi#65).

    Die Host-Zeile trägt diesen Schlüssel aus der Scheduler-DB; die Client-Zeile
    kommt aus der lokalen MD-Discovery und trägt ihn nicht. Ohne die Angleichung
    liesse sich der gemeinsame Filter zwar aufrufen, er griffe aber nie — ein
    Bedienelement, das sichtbar da ist und nichts tut.

    Die Alternative wäre eine zweite Filterfunktion für die Client-Seite
    gewesen. Dagegen spricht, dass die Statuswerte dieselben sind und sich nur
    ihr Ablageort unterscheidet: das ist kein fachlicher Grund für zwei
    Wahrheiten, die auseinanderlaufen können.
    """
    out = []
    for row in rows:
        st = client_row_status(row, local_runs)
        out.append({**row, "last_status": st} if st else dict(row))
    return out


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


def _cookie_filter_value(cookie: str | None, valid: tuple[str, ...]) -> str | None:
    """Persistenter Filter-Wert aus Cookie (User-Fund: "die ausgewählte
    Auswahl in /-/ui/schedules sollte erhalten bleiben"). Nur übernehmen,
    wenn der Wert noch zu den aktuell gültigen Optionen gehört (oder der
    ``alle``-Sentinel ist) — schützt gegen veraltete Cookies nach
    Options-Änderungen (z. B. den entfernten ``app``-Typ, PLAN-25 Befund 7)."""
    if cookie and (cookie == "alle" or cookie in valid):
        return cookie
    return None


def _filter_bar(typ: str | None, status: str | None, *,
                url: str = "/-/ui/schedules/list",
                target: str = "#schedules") -> str:
    def _opts(values: tuple, cur: str | None) -> str:
        cur = cur or "alle"
        # value bleibt "alle" (interner Sentinel, s. filter_schedules()), nur
        # der sichtbare Text ist englisch (User-Fund 2026-07-08, 5. Runde).
        parts = [f'<option value="alle"{" selected" if cur == "alle" else ""}>all</option>']
        for v in values:
            parts.append(f'<option value="{v}"{" selected" if cur == v else ""}>{v}</option>')
        return "".join(parts)

    # Ziel und Target sind Parameter, seit die Leiste auch auf der Client-Seite
    # steht (m.rau/bibi#65). Fest verdrahtet wuerde ein Klick dort ein Fragment
    # austauschen, das es auf dem Client gar nicht gibt.
    common = (f'hx-get="{url}" hx-target="{target}" hx-swap="outerHTML" '
              'hx-include="[name=\'typ\'],[name=\'status\']"')
    return (
        '<div class="logbar">'
        f'<label>Type <select name="typ" {common}>{_opts(_SCHED_TYPES, typ)}</select></label>'
        f'<label>Status <select name="status" {common}>{_opts(_SCHED_STATUSES, status)}</select></label>'
        '</div>'
    )


#: Die fünf Screens, in der Reihenfolge der App-Bar. Feed und Jobs sind die
#: täglichen, Nodes ist Betrieb, Live und Log sind Diagnose.
#:
#: **Archive ist gestrichen** (m.rau/bibi#130, FE-Spezifikation §1). Die Frage
#: „was lief" beantwortet die `RELIABILITY`-Spalte im Jobs-Screen in einer Zahl;
#: ein Screen, der Läufe nach Zeit auflistet, beantwortet dieselbe Frage
#: langsamer. Der frühere Einwand — er sei der einzige Weg zu einem heimatlosen
#: Lauf — trägt nicht mehr: das JOURNAL-Segment führt auch den Job ohne MD.
SCREENS: tuple[tuple[str, str], ...] = (
    ("Feed", "/-/"),
    ("Jobs", "/-/jobs"),
    ("Nodes", "/-/nodes"),
    ("Live", "/-/live"),
    ("Log", "/-/log"),
)


#: Zustände, die im Header als „stopped" zählen — terminal, aber nicht
#: `complete`. Ein `complete` mit `next` wartet, es ist nicht angehalten.
_STOPPED_STATES = ("error", "inactive", "zombie", "killed")


#: Ein Zeitformat fuer den ganzen Header. Datum nur, wo es noetig ist —
#: alles unter 24 Stunden traegt die Uhrzeit allein, sonst waere jede Zeile
#: doppelt so lang fuer eine Information, die fast immer "heute" lautet.
def _uhrzeit(ts: float | None, now: float) -> str:
    """Absoluter Zeitpunkt, lesbar und dauerhaft wahr.

    **Entscheidung m.rau, 2026-08-03**, gegen die Relativzeiten der
    FE-Spezifikation §2 (``21s ago``, ``in 2 min``). Drei Gruende, in der
    Reihenfolge ihres Gewichts:

    1. **Ein absoluter Zeitpunkt bleibt nach einem Screenshot wahr.** ``21s
       ago`` ist ohne den Aufnahmezeitpunkt wertlos — und in diesem System
       lesen Mensch und Agent dieselbe Oberflaeche.
    2. **Nichts kann einfrieren.** Eine Relativzeit ist nur im Moment des
       Renderns richtig; sie aktuell zu halten verlangt entweder einen
       sekuendlichen Refetch (haengt an einem git-Subprozess) oder einen
       Ticker im Browser — und damit dieselbe Zeitlogik in zwei Sprachen.
    3. **Ein Format ueber die ganze Kette.** Epoch in der DB, Epoch im
       Transport, Uhrzeit erst beim Anzeigen.

    Der Preis ist eine Subtraktion im Kopf. Er ist klein, weil die Uhr in der
    App-Bar unmittelbar daneben steht.
    """
    if ts is None:
        return "—"
    import datetime as _dt
    t = _dt.datetime.fromtimestamp(ts)
    # Aelter (oder ferner) als ein Tag: ohne Datum waere die Uhrzeit mehrdeutig.
    if abs(now - ts) >= 86400:
        return t.strftime("%d/%m %H:%M")
    return t.strftime("%H:%M:%S")


def _hdr_row(label: str, wert: str, *, klasse: str = "") -> str:
    """Eine Beschriftungs-/Wert-Zeile eines Header-Blocks."""
    c = f' class="{klasse}"' if klasse else ""
    return (f'<div class="hdr-row"><span class="hdr-label">{label}</span>'
            f'<span class="hdr-value"{c}>{wert}</span></div>')


def status_header(
    status: dict | None, git_status: dict | None, *,
    scheduler: dict | None = None, now: float,
    scheduler_host: str | None = None, scheduler_stale_since: float | None = None,
) -> str:
    """Der Header: links dieser Knoten, rechts der Scheduler.

    Die Zweiteilung folgt dem Ausfall. Fällt der Host weg, verlieren genau die
    rechten Werte gleichzeitig ihre Gültigkeit — und nur sie. Vier Kacheln
    nebeneinander konnten das nicht zeigen: sie mischten, was dieser Knoten
    weiß, mit dem, was ihm jemand gesagt hat.

    ``scheduler_stale_since`` schaltet die Offline-Darstellung: der rechte
    Block behält seine letzten Werte, wird gedimmt und datiert. Kein
    achtfaches „offline" — ein alter Wert mit Datum sagt mehr als acht
    Platzhalter.

    Der Scheduler-Hostname kommt aus der Konfiguration dieses Knotens
    (``scheduler_host``), nicht aus der Antwort des Hosts. Deshalb steht er
    auch dann da, wenn nichts mehr antwortet — er ist der Anker, an dem der
    leere Block hängt, und trägt den Ausfall in Rot.
    """
    status = status or {}
    git_status = git_status or {}
    stale = scheduler_stale_since is not None

    # ── links: was dieser Knoten selbst weiß ────────────────────────────────
    eigener = status.get("hostname") or "—"
    hb = (status.get("connect") or {}).get("last_at")
    hb_alt = now - hb if hb else None
    # Rot mit steigendem Alter: ein Heartbeat, der zwei Minuten aussetzt, ist
    # kein Schönheitsfehler — der Knoten gilt dem Host nach 60 s als stale.
    hb_klasse = "bad" if hb_alt is not None and hb_alt > 60 else ""
    auto = "on" if status.get("auto_sync") else "off"
    heartbeat = f'{_uhrzeit(hb, now)}, auto-sync: {auto}'

    zweig = git_status.get("branch") or "—"
    baum = git_status.get("tree") or "—"
    sync = git_status.get("sync") or "—"
    # `oid` ist der volle Hash aus `working_tree_status()`; sieben Zeichen
    # genuegen und sind das, was man weitergibt. `commit` bleibt als Alias
    # zugelassen, weil aeltere Aufrufer ihn kurz uebergeben.
    oid = git_status.get("oid") or git_status.get("commit")
    projekt = f'{zweig} · {baum} · {sync}' + (f': {oid[:7]}' if oid else "")
    # Eskalierte Merge-Quarantäne (`stuck`) gehört in diese Zeile, obwohl die
    # FE-Spezifikation §2 sie nicht nennt: bisher hatte sie eine eigene Zahl in
    # der Git-Kachel, und ein Branch, der dreimal nicht mergen konnte, wartet
    # auf einen Menschen. Ersatzlos wegzulassen hieße, eine Eskalation still zu
    # verlieren — der eine Fehler, den dieser Header nicht machen darf.
    stuck = git_status.get("stuck") or 0
    if stuck:
        mehrzahl = "s" if stuck != 1 else ""
        projekt += f' · <span class="sync-conflict">{stuck} conflict{mehrzahl}</span>'
    # Konflikt ist der einzige Git-Zustand, der Handeln verlangt.
    projekt_klasse = "bad" if git_status.get("conflict") or sync == "conflict" else ""

    engine = status.get("engine") or {}
    version = engine.get("running") or "—"
    if engine.get("needs_update"):
        version += ' <span class="bad">requires upgrade</span>'

    # Derselbe Punkt wie rechts, mit derselben Bedeutung fuer diese Seite:
    # kommt der eigene Heartbeat durch? Zwei Titelzeilen, die dasselbe sind,
    # muessen auch gleich aussehen (m.rau, 2026-08-03).
    hb_ok = (status.get("connect") or {}).get("ok")
    eigen_klasse = "ok" if hb_ok is not False else "bad"
    links = (
        f'<div class="hdr-block"><div class="hdr-title">'
        f'<span class="{eigen_klasse}">●</span> CLIENT'
        f'<span class="hdr-host">{eigener}</span></div>'
        + _hdr_row("heartbeat", heartbeat, klasse=hb_klasse)
        + _hdr_row("project", projekt, klasse=projekt_klasse)
        + _hdr_row("bibi", version)
        + "</div>"
    )

    # ── rechts: was der Scheduler sagt ──────────────────────────────────────
    sched = scheduler or {}
    host = scheduler_host or sched.get("hostname") or "—"
    punkt = "○" if stale else "●"
    host_klasse = "bad" if stale else "ok"
    titel_zusatz = f' — no contact for {_human_duration(now - scheduler_stale_since)}' if stale else ""

    clients = len(sched.get("workers") or [])
    counts_quelle = sched.get("job_stats") or {}
    counts = counts_quelle.get("counts") or {}
    gestoppt = sum(counts.get(z, 0) for z in _STOPPED_STATES)
    fertig = counts.get("complete", 0)
    # `next_due_at` liegt in `job_stats`, nicht auf oberster Ebene — live stand
    # hier ein Strich, weil ich es eine Ebene zu hoch gesucht hatte.
    naechster = _until(counts_quelle.get("next_due_at") or sched.get("next_fire_at"), now)
    faellig = counts_quelle.get("next_due_at") or sched.get("next_fire_at")
    next_job = f'{_uhrzeit(faellig, now)}, {gestoppt} stopped, {fertig} finished'

    hoch = sched.get("started_at")
    verbunden = (status.get("connect") or {}).get("since") or status.get("started_at")
    # Knapp, weil beide Zeilen sonst umbrechen: das "since" ist aus "up"
    # ohnehin zu lesen, und "connected" ist zur clients-Zeile gewandert — dort
    # gehoert es hin, weil es diese Verbindung meint (m.rau, 2026-08-03).
    uptime = f'up {_uhrzeit(hoch, now)}'

    dim = " dimmed" if stale else ""
    rechts = (
        f'<div class="hdr-block{dim}"><div class="hdr-title">'
        f'<span class="{host_klasse}">{punkt}</span> SCHEDULER'
        f'<span class="hdr-host {host_klasse}">{host}</span>{titel_zusatz}</div>'
        + _hdr_row("clients", f"{clients}, connected {_uhrzeit(verbunden, now)}")
        + _hdr_row("next job", next_job)
        + _hdr_row("uptime", uptime)
        + "</div>"
    )
    return f'<div class="hdr">{links}{rechts}</div>'


def _screen_nav(active: str, roles: list[str] | None = None) -> str:
    """Die App-Bar: sechs Screens, der aktive ohne Link.

    Auf **jedem** Knoten dieselben sechs — es gibt nur noch einen Client, und
    der Scheduler ist Backend ohne eigenes Frontend (FE-Spezifikation §1). Die
    Leiste verzweigt deshalb nicht mehr nach Rolle; ``roles`` bleibt in der
    Signatur, weil die Aufrufer es durchreichen, und wird hier nicht gelesen.

    Vorher zeigte ein Scheduler-Knoten ``/-/ui/schedules`` und ein Client
    ``/-/ui/jobs``, beide beschriftet „Jobs" — zwei Screens unter einem Namen,
    weil es zwei Frontends gab. Ein Screenshot war ohne Kenntnis der Rolle
    nicht einzuordnen.

    ``Live`` und ``Log`` sind getrennt, was vorher ein Tab war: der Unterschied
    ist das Gedächtnis. Live hat keines und erzählt, was gerade geschieht; Log
    hat Historie und ist zum Nachschlagen da (FE-Spezifikation §7). ``API
    Docs`` ist aus der Leiste raus — die Route bleibt, aber eine generierte
    Schema-Seite ist kein Screen dieser App.
    """
    def _tab(t: str, h: str) -> str:
        if t == active:
            return f'<span class="tab-active">{t}</span>'
        return f'<a class="back" href="{h}">{t}</a>'
    items = [_tab(t, h) for t, h in SCREENS]
    return '<span class="muted">' + " · ".join(items) + "</span>"


def _live_clock(scheduler_now: float | None = None, now: float | None = None) -> str:
    """Die **eine** Uhr des UI, oben rechts — und sie zeigt die Zeit des
    Schedulers.

    Entscheidung m.rau (2026-08-03): *„Am liebsten hätte ich die scheduler
    Uhrzeit! ... rechts oben mit Ticker, und sonst nirgends."* Die lokale Zeit
    hat jeder in seiner Menüleiste; die des Hosts steht sonst an keiner Stelle.
    In einem verteilten System ist sie die interessantere — sie ist der
    Bezugspunkt für alles, was der rechte Header-Block zeigt, und ein
    Auseinanderlaufen der Uhren wird genau hier sichtbar.

    ``data-offset`` ist der Versatz in Sekunden (Scheduler minus eigene Uhr).
    Der Ticker im Browser zählt die eigene Zeit hoch und addiert ihn — sonst
    zeigte er die lokale Zeit unter fremdem Namen. Ohne erreichbaren Host ist
    der Versatz 0: dann läuft die eigene Uhr weiter, statt stehenzubleiben.
    Eine stehende Uhr sieht aus wie eine Zeit und ist keine.
    """
    versatz = 0.0
    if scheduler_now is not None and now is not None:
        versatz = round(scheduler_now - now, 1)
    return (f'<span class="liveclock" id="liveclock" data-offset="{versatz}">'
            f'--.--.---- --:--:--</span>')


#: Setzt die Uhr sekündlich (rein client-seitig) — „wir leben noch".
_CLOCK_JS = """
(function(){
  const c = document.getElementById('liveclock');
  if (!c) return;
  // Versatz zur Scheduler-Uhr in Sekunden; 0 = kein Host erreichbar, dann
  // laeuft die eigene Zeit weiter (eine stehende Uhr waere schlimmer).
  const versatz = parseFloat(c.dataset.offset || '0') * 1000;
  const tick = () => {
    const t = new Date(Date.now() + versatz);
    c.textContent = t.toLocaleDateString('en-GB') + ' ' + t.toLocaleTimeString('en-GB');
  };
  tick(); setInterval(tick, 1000);
})();
"""


# PLAN-36 Stufe 36.3 (E8): FOLLOW-Toggle + _FOLLOW_JS komplett entfernt.
# Der Toggle existierte, weil die frueheren 2s-Volltausch-Polls jede manuelle
# Scroll-Position zerstoerten ("die ersten Logzeilen auch LESEN MUESSEN") —
# unter dem Bus stickt appendLine() nur, wenn die Box unten steht, und
# _SCROLL_JS restauriert Positionen ueber Swaps; ein globaler Pausenschalter
# schuetzt nichts mehr und hielt nur veraltete Zustaende auf dem Schirm.


def _theme_toggle() -> str:
    """DARK/LIGHT-Button — Teil der rechten Nav-Gruppe (PLAN-21 Befund 1,
    User-Fund: "Theme als Symbol LIGHT/DARK" statt Textlabel). Startsymbol per
    ``_THEME_JS`` gesetzt (Default = System-Präferenz), damit hier kein
    Server-seitiger Theme-State nötig ist. Als Text-Link gestylt, kein
    Button-Look (PLAN-19 Befund 7)."""
    return '<button id="theme" class="toggle" onclick="bibiToggleTheme()">☾</button>'


#: DARK/LIGHT-Toggle: überschreibt ``color-scheme`` explizit via ``data-theme``
#: auf <html> (s. _CSS), Default = System-Präferenz (``prefers-color-scheme``),
#: persistiert in localStorage — analog zu _TIME_JS. Symbol statt Text
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


def _header(active: str, status: dict | None = None, *,
            scheduler: dict | None = None,
            scheduler_now: float | None = None, now: float | None = None) -> str:
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
    # Die Uhr zeigt die Zeit des Schedulers, nicht die eigene — deshalb reisen
    # sein `now` und der Renderzeitpunkt bis hierher durch.
    right = (f'{_ops_handles(status, scheduler=scheduler)}{_time_toggle()}'
            f'{_live_clock(scheduler_now, now)}{_theme_toggle()}')
    return (f'<header><div class="nav-left">{left}</div>'
            f'<div class="nav-right">{right}</div></header>')


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
    dasselbe Verhalten (Filter, positionsbasiertes Autoscroll) ohne Duplikat-Pflege haben."""
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
             client_rows: list[dict] | None = None,
        scheduler: dict | None = None,
        scheduler_stale_since: float | None = None,) -> str:
    """Live-Log-Panel (§5.4 Slice C): EventSource gegen ``/-/log/stream``, mit
    Level- + Text-Filter (Rolle/Event/slug/msg). Reines FE; der Daemon liefert
    die Events als SSE (der Log-Stream bleibt bewusst ein eigener Strom neben
    ``/-/events`` — Log-Zeilen sind Diagnose-Volumen, kein UI-Zustand).
    Autoscroll pausiert rein ueber die Scroll-Position (``paused`` in
    ``_LOG_JS``) — der globale FOLLOW-Toggle ist seit PLAN-36 Stufe 36.3
    entfernt (E8). Status-Kacheln (Host/Mode/Git/Job-Status,
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
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Live Log', status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"<script>{_CLOCK_JS}</script>"
        f"{feed_status_fragment(status, git_status, host_url, now, client_rows=client_rows, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{_log_panel()}"
        f"<script>{_EVENTS_JS}</script>"
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


def _engine_update_line(status: dict) -> str:
    """NEED-UPDATE-Zeile für den **eigenen** Knoten (m.rau/bibi#43).

    Beantwortet die Frage „muss ich hier noch etwas tun?", die man sonst nur
    durch Vergleichen zweier Dateien beantwortet.

    **Wie laut?** Ein Chip in der Kopf-Kachel, kein durchgehender roter Balken —
    und nur, wenn Soll und Ist wirklich auseinanderlaufen. Der Mismatch ist nach
    jedem Deploy-Push der Normalzustand, bis der Neustart kommt; ein Alarm dafür
    wäre Lärm, und Lärm wird weggeklickt. Dass die Kachel auf jedem Screen
    steht, macht die dezente Form ausreichend.

    Der Knopf löst den Neustart **lokal über 127.0.0.1** aus — also unabhängig
    davon, ob dieser Knoten von außen erreichbar ist. Genau daran scheiterte der
    Restart-Knopf im Nodes-Screen beim Mac (Schwester-Issue): die Bind-Adresse.
    """
    eng = status.get("engine") or {}
    if not eng.get("needs_update"):
        return ""
    running = eng.get("running") or "?"
    expected = eng.get("expected") or "?"
    return (
        f'<span class="chip conflict">NEED UPDATE</span> '
        f'<span class="ts-dim">{_e(running)} → {_e(expected)}</span> '
        f'<button class="startbtn" hx-post="/-/ui/self/update" '
        f'hx-confirm="Neuen Stand holen und diesen Knoten neu starten?" '
        f'hx-target="#feedstatus" hx-swap="outerHTML" hx-disabled-elt="this">'
        f'Update{_BTN_SPINNER}</button>'
    )


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
        lines = [_e(own)] if own else ["—"]
        update = _engine_update_line(status)
        if update:
            lines.append(update)
        return _lines_card("Host", lines, sub=subs)
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
    lines = [link]
    # Auf einem Client wiegt die Anzeige schwerer als auf dem Host: er wird
    # nicht bedient, er läuft mit — und ein hostloser Knoten hat gar keinen
    # Nodes-Screen, auf dem der Rückstand sonst auffiele (m.rau/bibi#43).
    update = _engine_update_line(status)
    if update:
        lines.append(update)
    return _lines_card("Client", lines, sub=sub)


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
_JOB_STATUS_RUNNING = ("starting", "running", "awaiting")
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


def job_status_fragment(job_stats: dict | None, now: float) -> str:
    """Eigenständige Job-Status-Kachel (Bibi4-Iteration, User-Fund: "Job
    Status ändert sich oft, und eine häufigere Abfrage wäre gut") — eigenes
    Element neben dem ``feed_status_fragment()``-Bundle, seit PLAN-36 Stufe
    36.3 bus-getrieben statt 2s-Poll: das Target ``jobs`` feuert bei jeder
    Job-Zustandsänderung, die Kachel refetcht dann ``/-/ui/feed/jobstatus``
    (dieselbe billige ``job_db``-SQLite-Abfrage wie vorher, nur ereignisgenau
    statt taktweise). Nur gerendert, wenn ``job_stats`` vorhanden ist
    (``scheduler``-Rolle, wie bisher PLAN-26 Befund 3) — sonst leerer String,
    kein leerer Bus-Container."""
    if job_stats is None:
        return ""
    attrs = ('id="jobstatuscard" data-bus="jobs" '
             'data-bus-refetch="/-/ui/feed/jobstatus"')
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
    *, client_rows: list[dict] | None = None,
    scheduler: dict | None = None, scheduler_stale_since: float | None = None,
) -> str:
    """Die Feed-Header-Kacheln (PLAN-19 Befund 4: Host-Connection, Mode,
    Git — löst die bisherigen 6 Kacheln von PLAN-18 Stufe 18.3 ab, u. a. fällt
    die Rollen-Kachel weg, deckungsgleich mit der ursprünglichen Umbau-Vorgabe
    „Rollen sind eh klar"). Baut **nicht** mehr auf ``_status_card_list()``
    auf (die bleibt unverändert für ``_status_cards()``/``daemon_page()`` als
    Baustein bestehen, auch ohne eigene Route seit PLAN-18 Stufe 18.4).

    Optionale 4. Kachel (PLAN-26 Befund 3) lebt seit der Bibi4-Iteration in
    ``job_status_fragment()`` — eigenes Bus-Target ``jobs`` statt Teil dieses
    Bundles, s. dortiger Docstring. Hier nur noch als verschachtelter
    Baustein eingehängt (``.statuscards`` bleibt das Grid, der Job-Status-
    Container ist einfach ein weiteres Grid-Kind).

    ``client_rows`` (Bibi4-Iteration, User-Brainstorm): Gegenstück für
    Knoten ohne ``scheduler``-Rolle — dieselbe Discovery-Liste wie
    ``_jobs_table()``, hier nur als Zähl-Grundlage für
    ``_client_job_status_card()``. Anders als der Host-Job-Status (eigener
    2s-Poll, DB-Query) bleibt das Teil dieses 30s-Bundles: die zugrunde
    liegende Discovery+Git-Status-Abfrage ist dieselbe Kostenklasse wie die
    Git-Karte selbst, ändert sich zudem selten (Repo-Struktur, nicht Live-
    Scheduling). Wenn weder ``job_stats`` noch ``client_rows`` vorhanden sind
    (z. B. Job-/Run-Detailseiten), bleibt die 4. Kachel schlicht weg, wie
    bisher.

    Aktualisierung seit PLAN-36 Stufe 36.3 über das Bus-Target ``feedstatus``
    (Collector: Flag-Diff auto_sync/sync_conflict/maintenance + jede Job-
    Zustandsänderung) statt des früheren 30s-Polls — der existierte, weil die
    Git-Karte an einem ``git status``-Subprozess hängt, der für Sekundentakt
    zu teuer war; jetzt läuft er nur noch, wenn sich tatsächlich etwas
    geändert hat (bzw. beim MAINT-Klick, s. u.)."""
    # Seit bibi5 rendert dieses Fragment den **Header** (zwei Blöcke nach
    # Herkunft, ``status_header()``) statt der vier Status-Kacheln. Name,
    # Signatur und Bus-Verdrahtung bleiben, weil daran das Nachladen hängt —
    # was sich ändert, ist die Darstellung, und die ändert sich damit auf jedem
    # Screen zugleich.
    #
    # ``host_url`` ist der Scheduler-URL aus der Konfiguration *dieses* Knotens.
    # Daraus kommt der Hostname im rechten Block — deshalb steht er auch dann
    # da, wenn der Host nicht antwortet.
    host = None
    if host_url:
        from urllib.parse import urlparse
        host = urlparse(host_url).hostname or host_url
    body = status_header(status, git_status, scheduler=scheduler, now=now,
                         scheduler_host=host,
                         scheduler_stale_since=scheduler_stale_since)
    # "bibiMaintChanged from:body" (User-Fund: "ein Klick auf Maintenance muss
    # ein Update nach sich ziehen") — der MAINT-Toggle lebt im gemeinsamen
    # Header, unabhängig davon, ob dieses Fragment auf der Seite existiert;
    # ohne Treffer im DOM ist das Event ein No-op.
    attrs = ('id="feedstatus" data-bus="feedstatus" '
             'data-bus-refetch="/-/ui/feed/status" '
             'hx-get="/-/ui/feed/status" '
             'hx-trigger="bibiMaintChanged from:body" hx-swap="outerHTML"')
    return f'<div {attrs}>{body}</div>'


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
              *, public_host: str = "localhost", index: int = 0) -> str:
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

    # m.rau/bibi#65: derselbe Wert, nach dem die Filterleiste filtert —
    # client_row_status() ist die eine Quelle fuer beide.
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
        st = client_row_status(row, local_runs) or "running"
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

    return (f"<tr><td>{slug_cell}</td><td>{type_cell}</td><td>{status_cell}</td>"
            f"<td>{last_cell}</td><td>{runtime_cell}</td><td></td></tr>")


def _jobs_table(rows: list[dict], local_runs: dict[str, dict], now: float,
                *, public_host: str = "localhost",
                sort: str | None = None, direction: str | None = None,
                sort_url: str = "/-/ui/jobs/board",
                sort_target: str = "#jobsboard") -> str:
    if not rows:
        return '<p class="out-empty">— keine Job-MDs im Repository gefunden —</p>'
    body = "".join(_jobs_row(r, local_runs, now, public_host=public_host, index=i)
                  for i, r in enumerate(rows))
    head = _sortable_head(
        [("Slug", "slug"), ("Type", "type"), ("Status", "status"),
         ("last / since", "last"), ("Runtime", "runtime"), ("Activity", None)],
        sort=sort, direction=direction, url=sort_url, target=sort_target)
    return f"<table>{head}<tbody>{body}</tbody></table>"


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


#: (PLAN-36 Stufe 36.2: das frühere ``_JOBS_LIVE_AUTOREFRESH_JS`` — der
#: running→terminal-Fingerprint-Vergleich, der ``#journal`` nachlud — ist
#: durch die ``journal:``-Zustands-Events des Bus ersetzt, s. ``_EVENTS_JS``.
#: ``data-running``/``data-journal-url`` an ``#jobsdetail-live`` bleiben als
#: Diagnose-Attribute erhalten.)


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
    """Bus-getriebene Region (``#jobsdetail-live``): Meta-Zeile + Aktions-
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
    # PLAN-36 Stufe 36.3: einziger Update-Weg ist der Bus (data-bus/-refetch)
    # — das 36.2er-Sicherheitsnetz-Poll und der awaiting-2s-Sonderfall sind
    # zurückgebaut (jeder awaiting-Übergang ist eine Job-Statusänderung, der
    # Collector feuert das live:-Event ereignisgenau; den Still-gestorbener-
    # Strom-Fall deckt der Ping-Watchdog in _EVENTS_JS).
    attrs = (f'id="jobsdetail-live" data-running="{running_flag}" '
            f'data-journal-url="{journal_url}" '
            f'data-bus="live:{s}" '
            f'data-bus-refetch="/-/ui/jobs/detail/{s}/live"')
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
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_SCROLL_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Feed-Screen (PLAN-18 Stufe 18.3) — jetzt Home (``/-/``) ──────────────────

def _feed_commit_cell(sha: str | None, commit_base_url: str | None) -> str:
    if not sha:
        return ""
    short = _e(sha[:7])
    if commit_base_url:
        href = _e(f"{commit_base_url}/commit/{sha}")
        return f'<a class="commit" href="{href}" target="_blank" rel="noopener">{short}</a>'
    return f'<span class="commit">{short}</span>'


def _feed_row(entry: dict, *, commit_base_url: str | None = None) -> str:
    """Eine Einheit: Uhrzeit, Name, Umfang, Urheber, Commit.

    Nur die Uhrzeit — das Datum steht in der Tagestrennlinie darüber und
    stünde sonst in jeder Zeile ein zweites Mal.
    """
    ts = entry.get("last_changed")
    zeit = _abs_time(ts)
    n = int(entry.get("changes") or 0)
    umfang = f"{n} change" if n == 1 else f"{n} changes"
    wer = ", ".join(entry.get("authors") or []) or "—"
    commit = _feed_commit_cell(entry.get("last_commit_sha"), commit_base_url)
    return ('<div class="frow">'
           f'<span class="t">{_e(zeit)}</span>'
           f'<span class="msg">{_e(entry.get("unit") or "—")}</span>'
           f'<span class="cnt">{_e(umfang)}</span>'
           f'<span class="who">{_e(wer)}</span>'
           f"{commit}"
           "</div>")


def _feed_empty(days: int | None) -> str:
    """Leerer Zustand als Einstiegsdokumentation (Umbauplan §4): was fehlt, und
    was man tun kann. ``— keine Änderungen —`` sagt beides nicht.

    Der Hinweis auf LOAD MORE steht nur, wo der Knopf auch steht. Ohne
    ``days`` gibt es kein Fenster zum Erweitern, und ein Rat, der auf einen
    fehlenden Knopf zeigt, ist schlimmer als keiner.
    """
    if not days or days < 1:
        # Ohne Fenster gibt es nichts zu erweitern, und „the last 0 days" ist
        # keine Auskunft. Beides derselbe Fall: kein Rat auf einen Knopf, der
        # nicht da ist, und keine Zahl, die niemand gemeint hat.
        return ('<p class="out-empty">No changes found. Every commit to a '
               "Markdown file under <code>vault/</code> shows up here.</p>")
    fenster = "the last day" if days == 1 else f"the last {days} days"
    return ('<p class="out-empty">No changes in '
           f"{_e(fenster)}. Every commit to a Markdown file under "
           "<code>vault/</code> shows up here — widen the window with LOAD MORE.</p>")


def _feed_list(entries: list[dict], *, days: int | None = None,
              commit_base_url: str | None = None) -> str:
    """Tageweise gruppiert, jüngster Tag zuerst — dasselbe Idiom wie die
    Lauf-Liste in Job Detail, damit beide Listen gleich gelesen werden."""
    if not entries:
        return _feed_empty(days)
    from bibi.controller import jobs_view
    teile = []
    for tag, zeilen in jobs_view.by_day(entries, ts_key="last_changed"):
        teile.append(f'<div class="fday">{_e(tag)}</div>')
        teile.append('<div class="feedlist">' + "".join(
            _feed_row(e, commit_base_url=commit_base_url) for e in zeilen) + "</div>")
    return "".join(teile)


def _feed_reach(entries: list[dict], days: int | None) -> str:
    """Reichweite und Umfang im Bild, nicht nur im Knopf — sonst ist ein
    LOAD MORE, das nichts mehr lädt, von „da war nichts" nicht zu
    unterscheiden."""
    einheiten = len(entries)
    aenderungen = sum(int(e.get("changes") or 0) for e in entries)
    e_wort = "unit" if einheiten == 1 else "units"
    a_wort = "change" if aenderungen == 1 else "changes"
    umfang = f"{einheiten} {e_wort}, {aenderungen} {a_wort}"
    if not days or days < 1:
        # Ohne Fenster keine Fensterangabe — „showing None days" stand hier
        # vorher wortwoertlich.
        return f'<p class="freach">{umfang}</p>'
    fenster = "1 day" if days == 1 else f"{days} days"
    return f'<p class="freach">showing {_e(fenster)} · {umfang}</p>'


def _feed_board_url(days: int | None) -> str:
    return "/-/ui/feed/board" + (f"?days={days}" if days is not None else "")


def feed_fragment(feed_data: dict, *, days: int | None = None,
                  now: float | None = None) -> str:
    """Der austauschbare Feed-Kern (``#feedboard``): Reichweite, Liste,
    LOAD MORE. Ein Klick erweitert das Fenster um einen Tag."""
    entries = feed_data.get("entries") or []
    commit_base_url = feed_data.get("commit_base_url")
    load_more = ""
    if days and days >= 1:
        url = _feed_board_url(days + 1)
        load_more = (
            '<div class="loadmore">'
            f'<button hx-get="{url}" hx-target="#feedboard" '
            f'hx-swap="outerHTML">LOAD MORE ({days + 1} days)</button>'
            "</div>"
        )
    return (
        '<div id="feedboard"><div class="panel-card">'
        f"{_feed_reach(entries, days)}"
        f"{_feed_list(entries, days=days, commit_base_url=commit_base_url)}"
        f"{load_more}"
        "</div></div>"
    )


def feed_page(
    feed_data: dict, *, git_status: dict | None = None, host_url: str | None = None,
    days: int | None = None,
    daemon_status: dict | None = None, now: float | None = None,
    client_rows: list[dict] | None = None,
        scheduler: dict | None = None,
        scheduler_stale_since: float | None = None,) -> str:
    """Feed-Screen (Home, ``/-/``): Hülle, Header, eine Zeile je geänderter
    Einheit. Zeigt die Wirkung der Arbeit, wo die anderen Screens ihre
    Ausführung zeigen."""
    now = time.time() if now is None else now
    status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Feed</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Feed', status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"<script>{_CLOCK_JS}</script>"
        f"{feed_status_fragment(status, git_status, host_url, now, client_rows=client_rows, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{feed_fragment(feed_data, days=days, now=now)}"
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


def _ops_handles(status: dict | None = None, *, scheduler: dict | None = None) -> str:
    """Die drei Ops-Handles der App-Bar: ``⟳`` Rescan, ``◐`` Maintenance,
    ``●`` Verbindung.

    **Der Verbindungspunkt trägt drei Zustände**, nicht zwei: grün verbunden,
    orange Maintenance aktiv, rot getrennt. Das geht auf einen Befund zurück,
    der mehrfach Zeit gekostet hat — *„wir haben uns gefragt, warum der Job
    nicht startet. Wir hatten den Maintenance Mode übersehen."* Die Antwort
    darauf ist keine zweite Ampel, sondern eine dritte Farbe an der
    vorhandenen: ein Modus, der die Automatik anhält, gehört dorthin, wo man
    ohnehin nachsieht, ob die Verbindung steht.

    **Der Maintenance-Zustand kommt vom Scheduler**, nicht von diesem Knoten.
    Ein Client hat keinen eigenen; zeigte der Punkt den lokalen Wert, stünde
    dort dauerhaft „aus" und der Befund wäre verkleidet statt behoben.

    ``◐`` für Maintenance statt ``⚙``/``⚠``: ein halb gefüllter Kreis ist ein
    Zustand zwischen an und aus, und genau das ist dieser Modus. Ein Zahnrad
    ist eine Einstellung, ein Warndreieck ein Fehler — beides trifft es nicht.
    Der Knopf bleibt ohne ``scheduler``-Rolle sichtbar, aber ``disabled``:
    dieselbe Toggle-Menge auf jedem Knoten, nicht verfügbare Funktionen
    ausgegraut statt ausgeblendet.
    """
    roles = (status or {}).get("roles") or []
    ist_host = "scheduler" in roles
    # Auf einem Scheduler-Knoten *ist* der eigene Status der des Schedulers —
    # dieselbe Regel wie in `_scheduler_status()`. Ohne sie zeigte der Host
    # seinen eigenen Maintenance-Modus nicht an, weil er sich selbst nicht
    # ueber HTTP befragt.
    quelle = scheduler if scheduler is not None else (status if ist_host else None)
    maint = bool((quelle or {}).get("maintenance"))

    # Verbunden? Der Host ist es mit sich selbst — dort gibt es keinen
    # Heartbeat, und ein roter Punkt waere schlicht falsch.
    verbunden = True if ist_host else ((status or {}).get("connect") or {}).get("ok") is not False

    if not verbunden:
        # Getrennt schlaegt Maintenance: wer nicht verbunden ist, weiss ueber
        # den Modus des Hosts ohnehin nichts Aktuelles.
        dot_cls, dot_titel = "bad", "disconnected"
    elif maint:
        dot_cls, dot_titel = "warn", "maintenance active — nothing is dispatched"
    else:
        dot_cls, dot_titel = "ok", "connected"

    if ist_host:
        mcls = "toggle warn" if maint else "toggle"
        mtitle = "maintenance: on" if maint else "maintenance: off"
        maint_btn = f'<button id="maint" class="{mcls}" title="{mtitle}">◐</button>'
    else:
        maint_btn = ('<button id="maint" class="toggle" disabled '
                    'title="maintenance: host only">◐</button>')
    return (
        '<nav class="handles">'
        '<button id="rescan" class="toggle" title="rescan the vault">⟳</button>'
        f"{maint_btn}"
        f'<span id="conn-dot" class="conn-dot {dot_cls}" title="{dot_titel}">●</span>'
        "</nav>"
    )


#: RESCAN + MAINT als plain-JS-Buttons gegen die JSON-API (§1.1). RESCAN → POST
#: /-/rescan (kurze Quittung). MAINT → POST/DELETE /-/maintenance; der Button **und
#: ein Banner** spiegeln die **echte Server-Antwort** (kein optimistisches Toggle —
#: bei Fehler bleibt der Zustand).
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
    maint.title = on ? 'maintenance: on' : 'maintenance: off';
    // Der Verbindungspunkt traegt den Modus mit — er ist die Stelle, auf die
    // man ohnehin schaut. Rot (getrennt) bleibt unangetastet: es schlaegt
    // Maintenance, weil ein getrennter Knoten ueber den Modus nichts weiss.
    const dot = document.getElementById('conn-dot');
    if (dot && !dot.classList.contains('bad')) {
      dot.classList.toggle('warn', on);
      dot.classList.toggle('ok', !on);
      dot.title = on ? 'maintenance active — nothing is dispatched' : 'connected';
    }
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
    """Die Live-Output-Box eines laufenden Jobs. Server-seitig mit dem
    aktuellen (bereits formatierten) Output geseedet (no-JS-Paint); ab
    ``data-from`` hängen die ``append``-Events des globalen Event-Stroms an
    (``_EVENTS_JS``, PLAN-36 Stufe 36.2 — vorher eine eigene EventSource pro
    Box gegen ``/-/job/{id}/output/stream``, die es auf Client-Knoten gar
    nicht gab, s. FE-Live-Update-Briefing Befund 1). Offsets zählen in
    denselben formatierten Einheiten wie der Seed — kein Offset-Mismatch.
    Kein ``hx-preserve`` mehr: die Box wird nur noch bei echten Zustands-
    Refetches ersetzt (frischer Seed + neuer data-from = Resync-Heilung),
    nicht mehr pro Poll-Tick — das Attribut schützte zuletzt nichts und
    blockierte auf Client-Seiten den einzigen Update-Weg."""
    evs = events or []
    seed = "\n".join(_event_line(e) for e in _merge_deltas(evs))
    jid = _e(job_id)
    return (f'<pre class="term liveterm" id="livebox-{jid}" data-job="{jid}" '
            f'data-from="{len(evs)}">{seed}</pre>')


#: PLAN-36 Stufe 36.2: EIN globaler Event-Strom pro Seite statt per-Box-
#: EventSources und Fingerprint-Poll-Vergleichen. Zwei Event-Klassen (E2):
#: **state** — leere Dirty-Meldung, das Ziel-Element (``[data-bus=…]``)
#: refetcht seine eigene Fragment-Route (``data-bus-refetch``) per htmx-Swap;
#: **append** — eine formatierte Output-Zeile fuer ``.liveterm[data-job]``,
#: dedupliziert ueber ``data-from`` (derselbe Offset-Zaehler, mit dem der
#: server-seitige Seed die Box vorbefuellt — ein Bus-Refetch traegt frischen
#: Seed + neuen data-from, nachlaufende Appends mit off <= data-from sind
#: dadurch harmlos). Kein ``onerror`` (2026-07-20-Lektion: Abriss und
#: Serverende sind clientseitig ununterscheidbar) — EventSource reconnected
#: selbst, und der Server schickt beim (Re-)Connect den Resync aller aktiven
#: Elemente (E5), der jede Luecke heilt. Seit Stufe 36.3 zusaetzlich ein
#: Ping-Watchdog: der Server sendet alle ``EVENTS_PING_S`` ein
#: ``{"t":"ping"}``-data-Event; bleibt der Strom >45s stumm (still gestorbene
#: Verbindung, die der Browser-Reconnect nie bemerkt), wird er verworfen und
#: neu aufgebaut. Damit ersetzt der Watchdog die frueheren Sicherheitsnetz-
#: Polls vollstaendig. Das FOLLOW-Gate ist weg (E8): Events werden immer
#: angewendet, Lesbarkeit sichert allein die Scroll-Logik (appendLine stickt
#: nur, wenn die Box unten steht; _SCROLL_JS erhaelt Positionen ueber Swaps).
_EVENTS_JS = """
(function(){
  if (!window.EventSource) return;
  function initBoxes(){
    document.querySelectorAll('.liveterm[data-job]').forEach(box => {
      if (box._bibiInit) return;
      box._bibiInit = true;
      // Seed ans Ende (2026-07-06-Lektion: sonst liefert atBottom() ab dem
      // allerersten Check false und FOLLOW bleibt dauerhaft wirkungslos).
      box.scrollTop = box.scrollHeight;
    });
  }
  document.addEventListener('DOMContentLoaded', initBoxes);
  document.body.addEventListener('htmx:afterSettle', initBoxes);

  function appendLine(box, o){
    const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
    // Token-Delta (PLAN-14): an die zuletzt gerenderte Zeile anhaengen statt
    // eine neue Timestamp-Zeile zu erzeugen.
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
  }

  let es = null;
  let lastSeen = Date.now();
  function handle(e){
    lastSeen = Date.now();
    let ev; try { ev = JSON.parse(e.data); } catch(_) { return; }
    if (ev.t === 'state') {
      // querySelectorAll statt querySelector (Stufe 36.3): kollektive
      // Targets ("jobs", "nodes", ...) treffen auf einer Seite legitim
      // mehrere Regionen (z.B. Board + Archiv-Liste).
      const sel = '[data-bus="' + (window.CSS && CSS.escape ? CSS.escape(ev.target) : ev.target) + '"]';
      document.querySelectorAll(sel).forEach((el) => {
        const url = el.getAttribute('data-bus-refetch');
        // source: el ist essenziell (User-Fund 2026-07-27, "Liste aktualisiert
        // erst nach Reload"): ohne source ordnet htmx JEDEN ajax-Request
        // document.body zu, und dessen Sync-Queue haelt nur EINEN wartenden
        // Request ("last") — bei einem Event-Batch (jobs+feedstatus+chart im
        // selben Collector-Tick) verdraengte jeder weitere Call den gequeuten
        // Refetch der Schedules-Liste, sie verhungerte still. Mit source: el
        // hat jede Region ihre eigene Queue, alle Refetches laufen parallel.
        if (url && window.htmx) htmx.ajax('GET', url, {source: el, target: el, swap: 'outerHTML'});
      });
    } else if (ev.t === 'append') {
      const jid = (ev.target || '').slice(4);  // "out:<job_id>"
      const box = document.querySelector('.liveterm[data-job="' + jid + '"]');
      if (!box || !ev.e) return;
      const from = parseInt(box.dataset.from || '0', 10);
      if (ev.off <= from) return;  // Dedup gegen Seed/Refetch
      box.dataset.from = String(ev.off);
      appendLine(box, ev.e);
    }
    // ev.t === 'ping': nichts zu tun — lastSeen ist schon aufgefrischt.
  }
  function connect(){
    es = new EventSource('/-/events');
    window._bibiEvents = es;
    lastSeen = Date.now();
    es.onmessage = handle;
  }
  connect();
  // Watchdog (Stufe 36.3, ersetzt das Sicherheitsnetz-Poll-Netz komplett):
  // der Server pingt alle 15s als data-Event; bleibt der Strom >45s stumm
  // (3 verpasste Pings — Laptop-Sleep, Proxy-Abriss ohne TCP-RST, den die
  // EventSource-Reconnect-Logik nie bemerkt), Strom verwerfen und neu
  // aufbauen — der Connect-Resync des Servers heilt dann alle Regionen.
  // Bewusst kein eigener onerror-Handler: echte Verbindungsfehler
  // reconnected der Browser selbst, mit demselben Resync.
  setInterval(() => {
    if (Date.now() - lastSeen > 45000) { es.close(); connect(); }
  }, 10000);
})();
"""

#: Scroll-Erhalt fuer die Live-Region-Swaps (extrahiert aus dem frueheren
#: _LIVE_JS, PLAN-36 Stufe 36.2) — Swaps kommen jetzt vom Bus-Refetch und dem
#: gestreckten Sicherheitsnetz-Poll, das Problem bleibt dasselbe: ein frisch
#: eingehaengtes Element hat scrollTop=0. Beide Regionen abgedeckt (#live Host,
#: #jobsdetail-live Client — letzterer bekommt durch den Bus erstmals lebende
#: Output-Boxen, s. FE-Live-Update-Briefing Befund 1).
_SCROLL_JS = """
(function(){
  const isLiveRegion = (t) => t && (t.id === 'live' || t.id === 'jobsdetail-live');
  const region = () => document.getElementById('live') || document.getElementById('jobsdetail-live');
  // .liveclamp (awaiting/terminal): absolute Position wiederherstellen.
  let saved = null;
  document.body.addEventListener('htmx:beforeSwap', (ev) => {
    const t = ev.detail && ev.detail.target;
    if (isLiveRegion(t)) {
      const box = t.querySelector('.liveclamp');
      saved = box ? box.scrollTop : null;
    }
  });
  document.body.addEventListener('htmx:afterSettle', () => {
    if (saved == null) return;
    const r = region();
    const box = r && r.querySelector('.liveclamp');
    if (box) box.scrollTop = saved;
    saved = null;
  });
})();
// .liveterm (running): zwei Faelle (PLAN-36 Stufe 36.0, Befund 5 — live doppelt
// reproduziert): war die Box unten (FOLLOW), folgt sie dem NEUEN Ende; war sie
// hochgescrollt (User liest alte Zeilen), wird die absolute Position (savedTop)
// restauriert — vorher fehlte dieser else-Zweig, der browserseitige Reset auf 0
// beim Wiedereinhaengen blieb stehen.
(function(){
  const isLiveRegion = (t) => t && (t.id === 'live' || t.id === 'jobsdetail-live');
  const region = () => document.getElementById('live') || document.getElementById('jobsdetail-live');
  let wasAtBottom = null, savedTop = null;
  document.body.addEventListener('htmx:beforeSwap', (ev) => {
    const t = ev.detail && ev.detail.target;
    if (isLiveRegion(t)) {
      const box = t.querySelector('.liveterm[data-job]');
      wasAtBottom = box ? (box.scrollTop + box.clientHeight >= box.scrollHeight - 24) : null;
      savedTop = box ? box.scrollTop : null;
    }
  });
  document.body.addEventListener('htmx:afterSettle', () => {
    if (wasAtBottom == null) { savedTop = null; return; }
    const r = region();
    const box = r && r.querySelector('.liveterm[data-job]');
    if (box) box.scrollTop = wasAtBottom ? box.scrollHeight : (savedTop ?? box.scrollTop);
    wasAtBottom = null; savedTop = null;
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
    if status not in ("starting", "running", "awaiting", "deferred"):
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
    wenn keiner läuft/wartet. Seit PLAN-36 Stufe 36.2 reichen AUCH die
    ``.../journal``-Refresh-Routen ihn durch (nicht mehr nur der initiale
    Seitenaufbau): der Bus meldet ``journal:``-dirty jetzt bei jedem
    Statuswechsel (Collector), nicht erst beim Terminal-Übergang — ein
    Refetch während des Laufs muss die Platzhalterzeile also erhalten,
    sonst verschwände sie beim ersten Bus-Refresh.

    ``data-bus``/``data-bus-refetch`` (PLAN-36 Stufe 36.2): ein
    ``journal:<slug>``-Zustands-Event des globalen Stroms refetcht diese
    Region über ihre eigene Refresh-Route — ersetzt beide früheren
    Fingerprint-Autorefresh-Skripte."""
    oob_attr = ' hx-swap-oob="true"' if oob else ""
    live_row = _live_placeholder_row(live_job, now, anchor=live_anchor)
    s = _e(slug)
    return (
        f'<div id="journal"{oob_attr} class="panel-card" '
        f'data-bus="journal:{s}" data-bus-refetch="{base}/{s}/journal">'
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
    "starting": ("kill",),
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
    Bus-getrieben (``live:``-Target) — bleibt getrennt vom Journal
    (``#journal``), das sonst durch nachgeladene Infinite-Scroll-Zeilen bei
    jedem Swap wieder plattgemacht würde (Journal Infinite Scroll, §6)."""
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
    # PLAN-36 Stufe 36.3: einziger Update-Weg ist der Bus (data-bus/-refetch,
    # s. _EVENTS_JS — ein live:-Zustands-Event refetcht diese Region). Das
    # 36.2er-Sicherheitsnetz-Poll und der awaiting-2s-Sonderfall sind
    # zurückgebaut: jeder awaiting-Übergang ist eine Job-Statusänderung, der
    # Collector feuert das live:-Event ereignisgenau; den Still-gestorbener-
    # Strom-Fall deckt der Ping-Watchdog in _EVENTS_JS.
    # data-finished-at: früher der Fingerabdruck für den Journal-Autorefresh
    # (jetzt Bus-Events) — bleibt als Diagnose-Attribut erhalten.
    _finished = _e(job.get("finished_at")) if job and job.get("finished_at") else ""
    attrs = (f'id="live" data-slug="{_e(slug)}" data-finished-at="{_finished}" '
             f'data-bus="live:{_e(slug)}" '
             f'data-bus-refetch="/-/ui/schedule/{_e(slug)}/live"')
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
    """Voller Detail-Kern für den initialen Seitenaufbau: ``#live`` (bus-
    getrieben) + ``#journal`` (einmalig, wächst nur per Infinite Scroll)."""
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
    ``#live``/``#journal``, damit ein ``#live``-Swap sie nicht neu rendert.
    Kein "← zurück"-Link mehr (Bibi4-Iteration, User-Fund) — die Nav-Leiste
    trägt schon einen Jobs-Tab dorthin zurück, der Link war redundant."""
    name = _e((schedule or {}).get("slug") or slug)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {name}</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('', daemon_status)}"
        f'<div style="display:flex;gap:.75rem;align-items:baseline">'
        f'<a class="back" href="/-/ui/schedule/{_e(name)}/attrs">Attribute →</a>'
        f'</div>'
        f"{schedule_detail_inner(schedule, runs, job, slug, now, live_output=live_output, public_host=public_host)}"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_SCROLL_JS}</script>"
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
        back = '<a class="back" href="/-/jobs">← Jobs</a>'
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
        f"<script>{_JOBS_JS}</script>"
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
        f'<a class="back" href="/-/jobs">← Jobs</a>'
        f'<a class="back" href="/-/ui/jobs/detail/{name}">← Detail</a>'
        f'</div>'
        f'<h1>{name} · Attribute</h1>'
        f"{config_html}"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


# ── Jobs-Screen (bibi5, FE-Spezifikation §4) ─────────────────────────────────

#: Die Slug-Spalte trägt 28 Zeichen; der längste Slug im Bestand hat 50.
_SLUG_BREITE = 28


def _slug_kurz(slug: str) -> str:
    """In der Mitte kürzen, nicht hinten.

    Vorne steht das Datum, hinten der Zweck (``20260609.dr-stage3-…-activation``)
    — hinten abzuschneiden verlöre beides. Der volle Slug bleibt im ``title``.
    """
    if len(slug) <= _SLUG_BREITE:
        return slug
    kopf = (_SLUG_BREITE - 1) // 2
    schwanz = _SLUG_BREITE - 1 - kopf
    return f"{slug[:kopf]}…{slug[-schwanz:]}"


#: Ein Satz je leerem Band. Er sagt, was fehlt **und** was man tun kann — das
#: ist die eigentliche Einstiegsdokumentation dieses Screens (Umbauplan §4).
_LEER = {
    Segment.SCHEDULE: ("no scheduled jobs — add <code>schedule:</code> to a "
                       "markdown file in your vault, or <code>at:</code> for a one-off"),
    Segment.ADHOC: ("none — a job with <code>schedule: adhoc</code> waits here "
                    "until you start it"),
    Segment.JOURNAL: "nothing archived in this window",
}


def _jobs_zeile(row, now: float) -> str:
    """Eine Zeile: ein Slug, zwei Zustandsblöcke."""
    from bibi.schedule.models import job_uid

    beziehung = ""
    if row.relation:
        # `duplicate` ist das einzige rote Label: es meldet ein Problem im
        # Vault, kein Verhältnis zwischen zwei Speichern, und verlangt eine
        # Umbenennung statt eines Syncs.
        klasse = "bad" if row.relation == "duplicate" else "muted"
        titel = f' title="{" · ".join(row.paths)}"' if row.relation == "duplicate" else ""
        beziehung = f' <span class="{klasse}"{titel}>({row.relation})</span>'

    s, l = row.scheduler, row.local
    return (
        "<tr>"
        f'<td class="slug"><a href="/-/jobs/{job_uid(row.slug)}" title="{row.slug}">'
        f"{_slug_kurz(row.slug)}</a>{beziehung}</td>"
        f'<td>{models.display_kind(row.spec.get("payload"), row.spec.get("app_port"))}</td>'
        # `row_status` ist der Slot-Zustand der Scheduler-DB; `status` heisst
        # dieses Feld nur in der lokalen Job-DB (live abgenommen 2026-08-03).
        f'<td>{s.get("row_status") or s.get("status") or "—"}</td>'
        f'<td>{_uhrzeit(s.get("last_run_at"), now)}</td>'
        f'<td>{_uhrzeit(s.get("next_fire_at"), now)}</td>'
        f'<td>{l.get("status") or "—"}</td>'
        f'<td>{_human_duration(l.get("exec_runtime")) if l.get("exec_runtime") else "—"}</td>'
        f'<td>{row.quote or "—"}</td>'
        "</tr>"
    )


#: Die Filtergruppen der Kopfleiste. `TYPE` und `STATUS` wirken auf alle
#: Bänder, die drei Journal-Filter nur auf das dritte — deshalb stehen sie
#: dort und nicht hier oben.
_FILTER_OBEN = (("TYPE", ("job", "claude", "app")),
                ("STATUS", ("waiting", "running", "stopped")))
_FILTER_JOURNAL = ("dropped", "oneshot", "local")

#: Klickbare Spalten. Der Schlüssel ist zugleich der Query-Parameter.
_SORTIERBAR = (("slug", "SLUG"), ("type", "TYPE"), ("status", "STATUS"),
               ("last", "LAST"), ("next", "NEXT"), ("24h", "24H"))


def _filter_knopf(wert: str, aktiv: list[str]) -> str:
    an = " on" if wert in aktiv else ""
    return f'<button class="fltr{an}" data-filter="{wert}">{wert}</button>'


def _sort_kopf(schluessel: str, label: str, sort: str | None, richtung: str) -> str:
    """Ein Spaltenkopf, der seinen Zustand zeigt.

    Ohne Pfeil weiß niemand, ob gerade auf- oder absteigend sortiert ist — und
    ein zweiter Klick fühlt sich dann folgenlos an.
    """
    if sort == schluessel:
        pfeil = " ↓" if richtung == "desc" else " ↑"
        return (f'<th class="sortiert" data-sort="{schluessel}" '
                f'data-dir="{richtung}">{label}{pfeil}</th>')
    return f'<th data-sort="{schluessel}">{label}</th>'


def jobs_screen(rows: list, now: float, *, typ: list[str] | None = None,
                status: list[str] | None = None, journal: list[str] | None = None,
                sort: str | None = None, direction: str = "asc") -> str:
    """Die drei Bänder mit ihren Zeilen.

    Alle drei stehen immer da, auch leer: sonst verschöbe sich das Layout je
    nachdem, was gerade existiert, und man suchte ein Band, das nur gerade
    nichts enthält.

    Die Bänder sind eine Klassifikation, keine Sortierordnung — sortiert wird
    innerhalb eines Bandes. Der Grund für Bänder statt einer flachen Liste ist
    die gestaffelte Filtermenge: `TYPE` und `STATUS` wirken überall, die drei
    Journal-Filter nur im dritten Band, und eine gestaffelte Filtermenge
    braucht einen Ort je Staffel.
    """
    if not rows:
        return (
            '<div class="leer">'
            "<p>No jobs yet.</p>"
            "<p class=\"muted\">bibi finds its work in your vault: add "
            "<code>schedule:</code> to the frontmatter of a markdown file for a "
            "recurring job, or <code>at:</code> for a one-off. "
            "Then press <span class=\"mono\">⟳</span> to rescan.</p>"
            "</div>"
        )

    from bibi.controller import jobs_view

    typ, status, journal = typ or [], status or [], journal or []
    rows = [r for r in rows
            if jobs_view.trifft_filter(r, typ=typ, status=status, journal=journal)]
    if sort:
        rows = jobs_view.sortiere(rows, nach=sort, richtung=direction)

    gruppen = "".join(
        f'<span class="fltr-grp">{name}</span>'
        + "".join(_filter_knopf(w, typ if name == "TYPE" else status) for w in werte)
        for name, werte in _FILTER_OBEN)
    leiste = (f'<div class="fltr-bar">{gruppen}'
              f'<span class="fltr-zahl">{len(rows)} jobs</span></div>')

    kopf = (
        "<thead>"
        '<tr class="gruppen"><th></th><th></th>'
        '<th colspan="3" class="grp">SCHEDULER</th>'
        '<th colspan="2" class="grp">LOCAL</th><th></th></tr>'
        "<tr>"
        + _sort_kopf("slug", "SLUG", sort, direction)
        + _sort_kopf("type", "TYPE", sort, direction)
        + _sort_kopf("status", "STATUS", sort, direction)
        + _sort_kopf("last", "LAST", sort, direction)
        + _sort_kopf("next", "NEXT", sort, direction)
        + "<th>STATUS</th><th>RUNTIME</th>"
        + _sort_kopf("24h", "24H", sort, direction)
        + "</tr></thead>"
    )

    teile = []
    for seg in (Segment.SCHEDULE, Segment.ADHOC, Segment.JOURNAL):
        drin = [r for r in rows if r.segment is seg]
        eigene = ""
        if seg is Segment.JOURNAL:
            # Hier, nicht oben: diese drei wirken nur in diesem Band, und eine
            # gestaffelte Filtermenge braucht einen Ort je Staffel.
            eigene = " " + "".join(_filter_knopf(w, journal) for w in _FILTER_JOURNAL)
        teile.append(
            f'<tr class="band"><td colspan="8">{seg.value.upper()} '
            f'<span class="muted">{len(drin)}</span>{eigene}</td></tr>'
        )
        if drin:
            teile.extend(_jobs_zeile(r, now) for r in drin)
        else:
            teile.append(f'<tr class="leer-band"><td colspan="8">— {_LEER[seg]}</td></tr>')

    return f'{leiste}<table class="jobs">{kopf}<tbody>{"".join(teile)}</tbody></table>'


_JOBS_JS = """
(function(){
  // Filter und Sortierung leben in der URL, nicht im Speicher der Seite:
  // damit ist jede Ansicht teilbar, ueberlebt ein Neuladen und laesst sich
  // zurueckblaettern. Die Auswertung passiert am Server -- dieselbe
  // Klassifikation wie beim ersten Aufbau, kein zweiter Filter im Browser.
  const url = new URL(window.location.href);
  const mehrfach = (name, wert) => {
    const da = url.searchParams.getAll(name);
    url.searchParams.delete(name);
    // Toggle: was schon drin ist, faellt raus.
    const neu = da.includes(wert) ? da.filter(v => v !== wert) : da.concat([wert]);
    neu.forEach(v => url.searchParams.append(name, v));
    window.location.href = url.toString();
  };
  const gruppe = (wert) => {
    if (['job','claude','app'].includes(wert)) return 'typ';
    if (['waiting','running','stopped'].includes(wert)) return 'status';
    return 'journal';
  };
  document.querySelectorAll('.fltr').forEach(b => {
    b.addEventListener('click', () => mehrfach(gruppe(b.dataset.filter), b.dataset.filter));
  });
  document.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const jetzt = url.searchParams.get('sort');
      const richtung = url.searchParams.get('dir') || 'asc';
      url.searchParams.set('sort', th.dataset.sort);
      // Zweiter Klick auf dieselbe Spalte dreht die Richtung um.
      url.searchParams.set('dir', jetzt === th.dataset.sort && richtung === 'asc'
                                   ? 'desc' : 'asc');
      window.location.href = url.toString();
    });
  });
})();
"""


def jobs_page_v5(rows: list, *, now: float, daemon_status: dict | None = None,
                 git_status: dict | None = None, host_url: str | None = None,
                 scheduler: dict | None = None,
                 scheduler_stale_since: float | None = None,
                 typ: list[str] | None = None, status: list[str] | None = None,
                 journal: list[str] | None = None,
                 sort: str | None = None, direction: str = "asc") -> str:
    """Die Jobs-Seite: Hülle plus die drei Bänder.

    Getrennt von :func:`jobs_screen`, weil die Bänder als Fragment nachgeladen
    werden — die Hülle bleibt dabei stehen. Denselben Schnitt hat der Feed
    zwischen Seite und Liste.
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Jobs</title>"
        f"<style>{_CSS}</style>"
        f'<script src="/-/static/htmx-1.9.12.min.js"></script>'
        "</head><body>"
        f"{_header('Jobs', daemon_status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        # Am Bus angemeldet: `jobs` traegt jede Job-Zustandsaenderung, und der
        # Scheduler-Diff des Collectors meldet ueber `feedstatus`, was sich
        # drueben getan hat -- eine geloeschte MD faellt erst beim Rescan auf
        # und ist kein Job-Ereignis. Nachgeladen wird die Liste, nicht die
        # Seite: sonst ginge bei jedem Ereignis Scroll-Position und Fokus
        # verloren (Befund m.rau, 2026-08-03).
        f'<div id="jobs" data-bus="jobs" data-bus-refetch="/-/jobs/list" '
        f'hx-get="/-/jobs/list" hx-trigger="bibiJobsChanged from:body" '
        f'hx-swap="innerHTML">'
        f'{jobs_screen(rows, now, typ=typ, status=status, journal=journal, sort=sort, direction=direction)}</div>'
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


#: Ausklappen einer Lauf-Ausgabe.
#:
#: Reine Anzeige und deshalb im Browser: der Server weiss nicht, was jemand
#: gerade aufgeklappt hat, und soll es auch nicht wissen — sonst waere jeder
#: Klick ein Roundtrip.
#:
#: **Die Faltung der Quell-Gruppen ist weg** (m.rau/bibi#131): es gibt nur noch
#: eine Liste, und was die Faltung leistete — die wenigen lokalen Laeufe neben
#: 1064 Scheduler-Laeufen auffindbar zu halten —, leistet der Herkunftsfilter
#: mit seiner Zaehlung besser.
#:
#: **Zwei Wege zum Output, weil es zwei Speicher gibt:** ein archivierter Lauf
#: hat eine Journal-ID, ein im Slot stehender hat keine (unter A2 entsteht sie
#: erst auf START/RESET). Welcher Weg gilt, entscheidet der Server beim
#: Rendern und legt es in die Zeile — der Browser raet nicht.
_JOB_DETAIL_JS = """
(() => {
  const SEITE = {S: 'scheduler', C: 'client'};
  document.addEventListener('click', async (ev) => {
    const show = ev.target.closest('.run-show');
    if (!show) return;
    const zeile = document.getElementById('run-' + show.dataset.run);
    if (!zeile) return;
    if (!zeile.hidden) { zeile.hidden = true; show.textContent = '[show]'; return; }
    zeile.hidden = false;
    show.textContent = '[hide]';
    const feld = zeile.querySelector('.out-body');
    if (feld.dataset.geladen) return;
    feld.textContent = 'loading …';
    // Der Pfad ohne Query: `?days=90` gehoert zur Liste, nicht zum Lauf.
    const basis = location.pathname;
    const ziel = show.dataset.slot
      ? `${basis}/slot/${SEITE[show.dataset.src] || 'client'}/${show.dataset.slot}/output`
      : `${basis}/runs/${show.dataset.jid}/output`;
    try {
      const r = await fetch(ziel);
      feld.textContent = r.ok ? await r.text() : 'output unavailable';
      // Ein laufender Lauf ist noch nicht fertig — sein Output darf beim
      // naechsten Aufklappen nicht aus dem Cache kommen.
      if (r.ok && !show.dataset.slot) feld.dataset.geladen = '1';
    } catch (e) { feld.textContent = 'output unavailable'; }
  });
  // Deep-Link: `#run=<run_id>` oeffnet genau diese Zeile. Er ueberlebt die
  // Archivierung, weil der Bereich am Lauf haengt und nicht an der Position.
  const m = location.hash.match(/^#run=(.+)$/);
  if (m) {
    const b = document.querySelector('.run-show[data-run="' + m[1] + '"]');
    if (b) { b.click(); b.scrollIntoView({block: 'start'}); }
  }
})();
"""


def _slot_leiste(aktionen, *, job_id: str | None = None,
                 ziel: str | None = None, rebuild: bool = False) -> str:
    """Die Knopfleiste eines Slots (FE-Spezifikation §5.2).

    Nicht verfügbare Verben bleiben **sichtbar und ausgegraut**, nicht
    ausgeblendet: sonst springt das Layout, und die Information „das geht hier
    nicht" geht verloren. ``done`` ist die einzige Ausnahme — ein verbrauchter
    Slot hat keinen Ausgang, deshalb zeigt er auch keine toten Knöpfe, und das
    Fehlen der Leiste ist selbst die Aussage.

    ``rebuild`` ist der **vierte** Knopf und folgt einer anderen Regel als die
    drei (PLAN-24 Befund 5, unverändert übernommen): er ist entweder da oder
    gar nicht da, nie ausgegraut. Die drei Verben hängen am *Zustand* — dort
    heißt grau „jetzt gerade nicht". REBUILD hängt am *Job*: ein Host-Job hat
    kein per-Job-Image, das zu verwerfen wäre, und ein grauer Knopf behauptete,
    es gäbe eins.
    """
    from bibi.schedule.slot import Verb
    if not aktionen and not rebuild:
        return ""
    teile = []
    for verb, label in ((Verb.START, "START"), (Verb.RESET, "RESET"), (Verb.KILL, "KILL")):
        if verb in aktionen and job_id:
            # Drei Angaben, sonst wirkt der Knopf nicht: das Verb, die Job-ID
            # und die Seite. Der Scheduler-Slot liegt auf dem Host — ein POST
            # an den eigenen Daemon traefe den falschen Job.
            teile.append(
                f'<button class="slot-do" data-verb="{verb.value}" '
                f'data-id="{_e(job_id)}" data-ziel="{_e(ziel or "client")}">{label}</button>')
        else:
            teile.append(f'<span class="slot-off">{label}</span>')
    if rebuild and job_id:
        teile.append(
            f'<button class="slot-do" data-verb="rebuild" data-id="{_e(job_id)}" '
            f'data-ziel="{_e(ziel or "client")}" title="Verwirft das per-Job-Image, '
            f'der naechste Lauf startet vom Default-Image">REBUILD</button>')
    return f'<span class="slot-bar">{" ".join(teile)}</span>'


#: Die drei Verben. Ein Klick postet an den Controller, der an die richtige
#: Seite weiterleitet — der Scheduler-Slot liegt auf dem Host, der Client-Slot
#: hier. Danach laedt die Seite neu; der Bus meldet die Aenderung ohnehin,
#: aber der Klickende soll seine Wirkung sofort sehen und nicht auf den
#: naechsten Tick warten.
_SLOT_JS = """
(function(){
  document.querySelectorAll('button.slot-do').forEach(b => {
    b.addEventListener('click', async () => {
      const {verb, id, ziel} = b.dataset;
      if (!verb || !id) return;
      b.disabled = true;
      try {
        const r = await fetch(`/-/ui/jobs/verb/${ziel}/${encodeURIComponent(id)}/${verb}`,
                              {method: 'POST'});
        if (!r.ok) {
          const t = await r.text();
          b.disabled = false;
          alert(`${verb.toUpperCase()} failed (${r.status}): ${t.slice(0, 200)}`);
          return;
        }
      } catch (e) {
        b.disabled = false;
        alert(`${verb.toUpperCase()} failed: ${e}`);
        return;
      }
      window.location.reload();
    });
  });
})();
"""


def _slot_kachel(kachel, *, now: float) -> str:
    """Eine Slot-Kachel: Zustand und die drei Verben, sonst nichts (FE §5.1.1).

    Was *geschehen* ist, steht unten in der Liste. Diese Trennung nach Aufgabe
    ist der Grund, warum es für den Output nur noch einen Ort gibt — vorher hing
    der laufende an der Slot-Kopfzeile und der beendete in der Liste, und beim
    Terminalwerden rutschte er von einem zum anderen.
    """
    ziel = "scheduler" if kachel.quelle == "SCHEDULER" else "client"
    client = ziel == "client"
    titel = kachel.quelle + (f" &middot; {_e(kachel.host)}" if kachel.host else "")
    if not kachel.status:
        # Kein Rateschritt: fehlt jeder Zustand, sagt die Kachel das, statt
        # `pending` zu behaupten (Befund bei der Abnahme, 2026-08-03).
        zustand = '<span class="slot-none">no state reported</span>'
    else:
        # `idle` statt `pending` auf der Client-Seite (Entscheidung m.rau,
        # 2026-08-04): `pending` verspricht „reserviert, wartet" — dort wartet
        # aber niemand. Der Client-Slot entsteht **nur** durch `/run`
        # (Zustandsmodell §1), es gibt keinen Dispatcher, der ihn aufgreift.
        # Live gefunden: `daily-digest` trug `pending · next 17:20`, und der
        # Termin stammte aus der Zeit, als dieser Mac selbst Scheduler war.
        teile = ["idle" if (client and kachel.status == "pending") else _e(kachel.status)]
        if kachel.slot.get("reason"):
            teile.append(_e(kachel.slot["reason"]))
        begonnen, beendet = kachel.slot.get("started_at"), kachel.slot.get("finished_at")
        if client and kachel.status in jobs_view_ohne_lauf():
            # **Der Client-Slot zeigt zurück, der Scheduler-Slot nach vorn.**
            # Ein `next` wäre hier eine Behauptung über die Zukunft, die
            # niemand einlöst; `last` ist eine über die Vergangenheit, die
            # nachprüfbar ist. Genau an der Stelle, an der drüben `next` steht:
            # wo der Slot keinen eigenen Lauf trägt. Trägt er einen, sagt
            # dessen Dauer darunter schon alles. Ohne lokalen Lauf steht gar
            # nichts — ein leeres `last —` sähe aus wie ein Fehler.
            if kachel.last_at is not None:
                teile.append(f"last {_abs_time(kachel.last_at)}")
        elif kachel.status == "pending" and kachel.slot.get("next_fire_at"):
            # `pending · next 12:00` — ein reservierter Platz mit Termin. Ohne
            # `next` bleibt es beim blossen `pending`: das ist `adhoc`, ein
            # freier Platz ohne Verabredung.
            teile.append(f'next {_abs_time(kachel.slot["next_fire_at"])}')
        if begonnen is not None and kachel.status not in jobs_view_ohne_lauf():
            # Eine Dauer, kein Zeitpunkt — FE §2 verlangt absolute *Zeitpunkte*
            # und lässt Dauern ausdrücklich zu (`no contact for 4m`). Gemessen
            # gegen das Ende, wo es eines gibt: ein blockierter Lauf steht unter
            # A2 tagelang, und seine Laufzeit darf dabei nicht mitwachsen.
            teile.append(_human_duration((beendet if beendet is not None else now) - begonnen))
        zustand = " &middot; ".join(teile)
    return (
        f'<div class="tile"><div class="tile-head">{titel}</div>'
        f'<div class="tile-state">{zustand}</div>'
        f'{_slot_leiste(kachel.aktionen, job_id=kachel.slot.get("id"), ziel=ziel, rebuild=_ist_container(kachel.slot))}'
        "</div>"
    )


def _ist_container(slot: dict) -> bool:
    """Läuft dieser Job im Container? Entscheidet über den REBUILD-Knopf.

    Der Job-eigene ``exec_mode`` schlägt den Knoten-Default — dieselbe
    Reihenfolge wie ``worker._job_is_container()``, wo ihr Fehlen 2026-07-12
    dazu führte, dass ein Container-Job auf einem Host-Default-Knoten beim KILL
    nie sein ``docker stop`` bekam. Fehlt der Wert ganz (die Scheduler-Zeile
    führt ihn mit, die lokale auch), ist ``host`` die richtige Annahme: dann
    erscheint der Knopf nicht, statt einen anzubieten, der `409` antwortet.
    """
    return (slot.get("exec_mode") or "host").strip().lower() == "container"


def jobs_view_ohne_lauf() -> frozenset:
    """Die Slot-Zustände ohne eigenen Lauf — eine Quelle, nicht zwei."""
    from bibi.controller import jobs_view
    return jobs_view.OHNE_EIGENEN_LAUF


def job_tiles_fragment(tiles: list, *, now: float) -> str:
    """Die Kacheln nebeneinander (FE §5.1).

    **Nebeneinander, weil sie gleichrangig sind** und man sie ständig
    vergleicht („läuft es beim Scheduler, aber lokal nicht?"). Eine Kachel
    fehlt genau dann, wenn es dort keinen Slot gibt — nicht, wenn er leer ist.
    """
    if not tiles:
        return ""
    return ('<div class="tiles">'
            + "".join(_slot_kachel(k, now=now) for k in tiles)
            + "</div>")


def job_runs_fragment(liste, *, now: float, job_uid: str | None = None,
                      days: int | None = None, reach: dict | None = None,
                      aktiv: dict | None = None, weiter: int | None = None) -> str:
    """Die **eine** Lauf-Liste über beide Quellen (FE §5.3, m.rau/bibi#131).

    Sie führt jeden Lauf — die archivierten aus dem Journal **und** den, der
    noch im Slot steht, mit einer Marke. Damit gibt es für den Output genau
    einen Ort, und beim Terminalwerden bewegt sich nichts: die Zeile bleibt, wo
    sie ist, nur ihr Zustand ändert sich.

    Die frühere Faltung je Quelle ist ersatzlos weg. Sie entstand gegen ein
    echtes Problem (``gmail-transfer``: 1064 Scheduler-Läufe gegen 9 lokale),
    aber der Herkunftsfilter mit Zählung löst dasselbe und leistet mehr — er
    zeigt, *dass* es lokale gibt, ohne dass man eine Gruppe finden muss.
    """
    from bibi.controller import jobs_view

    if not liste.tiles and not liste.runs:
        # Kein Verweis mehr auf den Archive-Screen (m.rau/bibi#130): der ist
        # gestrichen, und ein Text, der auf einen Screen zeigt, den es nicht
        # gibt, ist derselbe tote Weg wie ein toter Link — nur ohne href, also
        # ohne dass ein Routen-Test ihn faende.
        return ('<div class="empty">This job is unknown on both sides — no slot, '
                'no runs. It may have been renamed.</div>')
    basis = f"/-/jobs/{job_uid}" if job_uid else ""
    aus = [_runs_filterzeile(liste, basis=basis, aktiv=aktiv, reach=reach)]
    if not liste.runs:
        return "".join([*aus, '<div class="empty">No runs yet — trigger one with '
                        "START, or wait for the schedule.</div>"])
    aus.append('<table class="runs"><thead><tr>'
               '<th class="mark"></th><th>TIME</th><th>SRC</th><th>STATUS</th>'
               "<th>EXIT</th><th>RUNTIME</th><th>COMMIT</th><th></th>"
               "</tr></thead><tbody>")
    for tag, laeufe in jobs_view.by_day(liste.runs, ts_key="sort_at"):
        aus.append(f'<tr class="day"><td colspan="8">{_e(tag)}</td></tr>')
        for r in laeufe:
            aus.append(_run_zeile(r))
    aus.append("</tbody></table>")
    # Der Knopf erscheint **nur**, wenn es wirklich mehr gibt, und er erweitert
    # um eine Menge statt um einen Tag — `weiter` ist das Fenster, das die
    # nächsten zehn Einträge trägt (§5.3, `jobs_view.naechstes_fenster()`).
    if job_uid and weiter and weiter != days:
        aus.append(_mehr_tage(basis, aktiv or {}, weiter))
    return "".join(aus)


def _mehr_tage(basis: str, aktiv: dict, tage: int) -> str:
    """``LOAD MORE`` als Fenster-Erweiterung, nicht als Offset.

    Die Liste ist tageweise gruppiert, und ein Nachladen „um eine Seite"
    schnitte mitten in einen Tag. Der Knopf verbreitert deshalb das Zeitfenster
    — und zwar so weit, dass wirklich etwas dazukommt: sonst verspricht er
    „mehr" und liefert an einem ruhigen Tag eine einzige Zeile.
    """
    teile = [f"days={tage}"]
    for a in ("status", "src"):
        if aktiv.get(a):
            teile.append(f"{a}={','.join(aktiv[a])}")
    return (f'<div class="more"><a class="cta" href="{basis}?{"&".join(teile)}">'
            "[ LOAD MORE ]</a></div>")


#: Die terminalen Zustände, nach denen die Lauf-Liste filtert (FE §5.3). Es
#: sind genau die, die ein *Lauf* erreichen kann — `done` fehlt, weil das ein
#: Slot-Zustand ist und nie im Journal steht (Zustandsmodell §1).
RUN_FILTER = ("complete", "error", "killed", "zombie", "inactive")


def _filter_link(basis: str, aktiv: dict, achse: str, wert: str,
                 beschriftung: str | None = None) -> str:
    """Ein Filter-Knopf, der sich selbst umschaltet.

    Serverseitig statt im Browser, weil die Tagestrennlinien sonst nicht mehr
    stimmen: ein ausgeblendeter Lauf hinterlässt einen leeren Tag, und der
    behauptet „an diesem Tag lief nichts".
    """
    gewaehlt = list(aktiv.get(achse) or [])
    neu = [w for w in gewaehlt if w != wert] if wert in gewaehlt else [*gewaehlt, wert]
    teile = []
    for a in ("status", "src"):
        werte = neu if a == achse else list(aktiv.get(a) or [])
        if werte:
            teile.append(f"{a}={','.join(werte)}")
    if aktiv.get("days"):
        teile.append(f"days={aktiv['days']}")
    ziel = basis + ("?" + "&".join(teile) if teile else "")
    klasse = "chip chip-on" if wert in gewaehlt else "chip"
    return f'<a class="{klasse}" href="{ziel}">{_e(beschriftung or wert)}</a>'


def _runs_filterzeile(liste, *, basis: str = "", aktiv: dict | None = None,
                      reach: dict | None = None) -> str:
    """Kopfzeile der Lauf-Liste: Herkunft mit Zählung, Zustände, Reichweite.

    **Die Zählung ist der Ersatz für die frühere Faltung** (§5.3). Die entstand
    gegen ein echtes Problem — ``gmail-transfer`` hat 1064 Scheduler-Läufe
    gegen wenige lokale, der erste lokale stünde unerreichbar weit unten. Der
    Filter löst dasselbe und leistet mehr: er zeigt, *dass* es lokale gibt.
    Eine zugeklappte Gruppe sagte das auch, aber nur, wenn man sie fand.
    """
    aktiv = aktiv or {}
    s, c = liste.counts.get("S", 0), liste.counts.get("C", 0)
    # Beschriftung **und** Zahl im Link, nicht der Buchstabe daneben: `S` als
    # Klickziel neben einem toten `scheduler 500` liest sich wie vier Elemente
    # statt zwei — und die Zahl ist genau das, was die Faltung ersetzt.
    herkunft = (f'{_filter_link(basis, aktiv, "src", "S", f"scheduler {s}")}'
                f'{_filter_link(basis, aktiv, "src", "C", f"client {c}")}')
    zustaende = "".join(_filter_link(basis, aktiv, "status", w) for w in RUN_FILTER)
    return ('<div class="runs-head">'
            '<span class="runs-title">RUNS</span>'
            f'<span class="runs-src">{herkunft}</span>'
            f'<span class="runs-states">{zustaende}</span>'
            f'{_runs_reichweite(reach)}'
            "</div>")


def _runs_reichweite(reach: dict | None) -> str:
    """Wie weit die Liste zurückreicht — und wo ihre echte Grenze liegt.

    Sie kommt vom gestrichenen Archive-Screen (§6) und muss bleiben: **ein
    ``LOAD MORE``, das nichts mehr lädt, muss sich von „da war nichts"
    unterscheiden lassen.**

    Dort stand ``showing 1 month · pruned after 3 months`` — und das war eine
    Falschaussage im UI: ein zeitbasiertes Pruning gibt es nicht, das einzige
    ``DELETE FROM journal`` löscht eine Zeile per ID. Die echte Grenze ist das
    Abfragelimit von :data:`RUN_LIMIT` Läufen **je Quelle**. Sie wird deshalb
    nur genannt, wenn die Liste tatsächlich daran anstößt — sonst behauptete
    der Screen eine Schranke, die für diesen Job nie greift.
    """
    if not reach:
        return ""
    teile = [f'{reach.get("total", 0)}']
    if reach.get("days"):
        tage = reach["days"]
        teile.append(f'showing {tage} day{"" if tage == 1 else "s"}')
    if reach.get("capped"):
        teile.append(f'capped at {reach["capped"]} per source')
    return f'<span class="runs-reach">{" &middot; ".join(teile)}</span>'


def _run_zeile(r: dict) -> str:
    """Eine Zeile der Lauf-Liste plus ihr (zugeklappter) Ausklappbereich."""
    st = r.get("status") or ""
    rs = r.get("reason")
    im_slot = r.get("in_slot") is True
    # Die Marke bedeutet „steht im Slot", nicht „läuft" — sie trägt beide
    # Fälle, die ein Slot kennen kann: den laufenden Lauf und den blockierten
    # terminalen, der nach A2 auf einen Menschen wartet. Sie ist der Bezug
    # zwischen oben und unten: die Kachel gehört zu der Zeile, die sie trägt.
    marke = '&#9656;' if im_slot else ""
    # Woher der Output kommt, entscheidet sich hier und nicht im Browser: ein
    # archivierter Lauf hat eine Journal-ID, ein im Slot stehender hat keine —
    # ihn trägt nur die Job-Zeile, und die liegt je nach Herkunft hier oder
    # beim Scheduler.
    if im_slot:
        holen = (f'data-slot="{_e(r.get("job_id"))}" '
                 f'data-src="{_e(r.get("src"))}"')
    else:
        holen = f'data-jid="{_e(r.get("id"))}"'
    return (
        f'<tr class="{"run run-in-slot" if im_slot else "run"}">'
        f'<td class="mark">{marke}</td>'
        f'<td class="t" data-ts="{r.get("sort_at") or ""}">'
        f'{_abs_time(r.get("sort_at"))}</td>'
        f'<td class="src">{_e(r.get("src"))}</td>'
        f'<td>{_e(st)}{" &middot; " + _e(rs) if rs else ""}</td>'
        f'<td>{_e(r.get("exit_code"))}</td>'
        f'<td>{_human_duration(r.get("exec_runtime"))}</td>'
        f'<td>{_e((r.get("commit_sha") or "")[:7])}</td>'
        f'<td><button class="cta run-show" {holen} '
        f'data-run="{_e(r.get("run_id"))}">[show]</button></td>'
        "</tr>"
        # Der Ausklappbereich gehoert zum **Lauf**, nicht zur Zeilenposition —
        # deshalb ueberlebt der Deep-Link `#run=` die Archivierung (§5.4).
        f'<tr class="out" id="run-{_e(r.get("run_id"))}" hidden>'
        f'<td colspan="8"><pre class="out-body"></pre></td></tr>')


def job_detail_page_v5(*, slug: str, spec: dict, now: float, liste=None,
                       daemon_status: dict | None = None,
                       git_status: dict | None = None, host_url: str | None = None,
                       scheduler: dict | None = None,
                       scheduler_stale_since: float | None = None,
                       beziehung: str | None = None,
                       days: int | None = None, reach: dict | None = None,
                       aktiv: dict | None = None, weiter: int | None = None) -> str:
    """Job Detail (FE-Spezifikation §5) — Hülle, Kopfzeile, Kacheln, Liste.

    **Oben die Kacheln: was ich tun kann. Unten die Liste: was geschehen ist**
    (m.rau/bibi#131). Die frühere Fassung hatte je Quelle eine faltbare Gruppe,
    den Slot in ihrer Kopfzeile und den laufenden Output daran hängend — zwei
    Orte für dieselbe Sache, zwischen denen ein Lauf beim Terminalwerden hin-
    und herrutschte. Jetzt führt **eine** Liste jeden Lauf, den im Slot
    stehenden mit einer Marke, und der Output klappt überall an derselben
    Stelle auf.
    """
    from bibi.schedule.models import job_uid as _uid

    trigger = spec.get("schedule") or spec.get("at_iso") or "—"
    typ = spec.get("kind") or "job"
    rel = f' <span class="rel">({_e(beziehung)})</span>' if beziehung else ""
    kopf = (
        '<div class="jd-head">'
        '<a class="back" href="/-/jobs">&#9666; jobs</a>'
        f'<span class="jd-slug">{_e(slug)}</span>{rel}'
        f'<span class="jd-meta">{_e(typ)} &middot; {_e(str(trigger))}</span>'
        f'<a class="cta" href="/-/jobs/{_uid(slug)}/attrs">[ATTRS]</a>'
        "</div>"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi &middot; {_e(slug)}</title>"
        f"<style>{_CSS}</style>"
        f'<script src="/-/static/htmx-1.9.12.min.js"></script>'
        "</head><body>"
        f"{_header('Jobs', daemon_status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{kopf}"
        # Am Bus: `archived` meldet, dass ein Lauf ins Journal gewandert ist —
        # die einzige Verbindung zwischen Strom und Liste (m.rau/bibi#108).
        # Nachgeladen wird die Liste, nicht die Seite: sonst ginge bei jedem
        # Lauf Scroll-Position und Faltzustand verloren.
        # Die Kacheln stehen **ausserhalb** des nachladenden Bereichs: sie
        # tragen die Knoepfe, und ein Nachladen mitten im Klick nimmt sie unter
        # der Hand weg. Die Liste darunter darf sich jederzeit erneuern.
        f'{job_tiles_fragment(getattr(liste, "tiles", []), now=now)}'
        f'<div id="runs" data-bus="archived" data-bus-refetch="/-/jobs/{_uid(slug)}/runs">'
        f'{job_runs_fragment(liste, now=now, job_uid=_uid(slug), days=days, reach=reach, aktiv=aktiv, weiter=weiter) if liste is not None else ""}'
        "</div>"
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOB_DETAIL_JS}</script>"
        f"<script>{_SLOT_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


#: Scheduling-Werte der Attribut-Seite in der Reihenfolge, in der sie dort
#: stehen — Trigger zuerst, dann Retry-Verhalten, dann die Fristen.
_ATTR_FELDER = ("schedule", "at", "attempts", "backoff",
                "defer_time", "defer_max", "error_time", "silence_timeout",
                "wall_time", "hitl_timeout")


def _load_more(ziel: str, offset: int, limit: int) -> str:
    """Der Nachlade-Knopf. Er erscheint **nur**, wenn es wirklich mehr gibt.

    Ein Knopf, der nichts mehr lädt, ist schlimmer als keiner — er sieht aus
    wie ein Weg. Genau die Unterscheidung, die §6 auch für die Reichweite
    verlangt: was fehlt, ist weggepruned und nicht bloß ungeladen.
    """
    trenn = "&" if "?" in ziel else "?"
    return (f'<div class="more"><a class="cta" '
            f'href="{ziel}{trenn}limit={limit}&offset={offset}">[ LOAD MORE ]</a></div>')


def job_attrs_page_v5(*, slug: str, spec: dict, defaults: dict, now: float,
                      daemon_status: dict | None = None,
                      git_status: dict | None = None, host_url: str | None = None,
                      scheduler: dict | None = None,
                      scheduler_stale_since: float | None = None) -> str:
    """Job Attributes (FE-Spezifikation §5.5) — alle Konfigurationswerte.

    **Gesetzte Werte sind von Default-Werten unterscheidbar, und zwar an zwei
    Signalen:** ein gesetzter Wert steht in Normalfarbe, ein geerbter gedimmt
    **und in Klammern**. Zwei statt einem, weil Dimmung allein in hellen Themes
    und auf schlechten Monitoren verlorengeht — dann sähe ein geerbter Wert aus
    wie eine Entscheidung.

    **Wie „geerbt" erkannt wird:** durch Vergleich mit dem Default. Das ist eine
    Näherung — wer einen Wert explizit auf genau den Default setzt, erscheint
    hier als Erbe. Der Preis ist bewusst: die Alternative wäre, das rohe
    Frontmatter bis hierher durchzureichen, und damit eine zweite Wahrheit
    neben der geparsten Spec zu führen. Für die Frage, die diese Seite
    beantwortet — *warum verhält sich der Job so* —, ist der Wert entscheidend
    und nicht, wer ihn hingeschrieben hat.
    """
    from bibi.schedule.models import job_uid as _uid

    zeilen = []
    for feld in _ATTR_FELDER:
        wert = spec.get(feld)
        if wert is None:
            continue
        geerbt = feld in defaults and wert == defaults[feld]
        gezeigt = f"({_e(wert)})" if geerbt else _e(wert)
        klasse = "attr-default" if geerbt else "attr-set"
        zeilen.append(
            f'<div class="attr-row"><span class="attr-key">{_e(feld)}</span>'
            f'<span class="{klasse}">{gezeigt}</span></div>')
    if not zeilen:
        # Leerer Zustand mit Handlungsanweisung (Umbauplan §4): sagen, was
        # fehlt und was man tun kann — nicht bloss, dass nichts da ist.
        zeilen.append('<div class="empty">No attributes — this job has no '
                      'configuration beyond its trigger and payload.</div>')
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi &middot; {_e(slug)} &middot; attributes</title>"
        f"<style>{_CSS}</style>"
        f'<script src="/-/static/htmx-1.9.12.min.js"></script>'
        "</head><body>"
        f"{_header('Jobs', daemon_status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        '<div class="jd-head">'
        f'<a class="back" href="/-/jobs/{_uid(slug)}">&#9666; back to job</a>'
        f'<span class="jd-slug">{_e(slug)}</span>'
        '<span class="jd-meta">attributes</span>'
        "</div>"
        f'<div class="attrs">{"".join(zeilen)}</div>'
        f"<script>{_CLOCK_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_TIME_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )
