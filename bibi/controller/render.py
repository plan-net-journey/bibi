"""HTML-Rendering der Controller-App (PLAN-4 §4.1 ff.) — **pure** Funktionen:
Daten-dict (aus den ``/-/``-JSON-Endpunkten) → HTML. Kein HTTP, kein DB-Zugriff,
damit voll unit-testbar. Look: Terminal/Konsole-nah, minimal (§2.5)."""

from __future__ import annotations

import datetime
import html
import json
import re
import time

from bibi.controller.jobs_view import Segment, erreichbarer_host, status_gruppe
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
  /* Der Blitz einer geaenderten Zelle (#67). Bewusst **ohne Farbton**: die
     Palette traegt Semantikfarben nur an Zustandsstellen und Terracotta genau
     eine Bedeutung. Eine Wertaenderung ist keins von beidem — blitzte sie in
     Amber, saehe die Zelle drei Sekunden lang aus wie ein Zustand, den sie
     nicht hat. Sie blitzt deshalb ueber Helligkeit. */
  --flashbg: #1f1e1b14; --flashfg: #1f1e1b;
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
  /* s. o. (#67) */
  --flashbg: #e8e5dc1a; --flashfg: #e8e5dc;
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
  /* s. o. (#67) */
  --flashbg: #1f1e1b14; --flashfg: #1f1e1b;
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
  /* s. o. (#67) */
  --flashbg: #e8e5dc1a; --flashfg: #e8e5dc;
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
/* Die Gruppen folgen dem Zustandsmodell, nicht dem ersten Eindruck (#68).

   `failed` stand bis v0.8.3 bei den roten Endzuständen und ist keiner: es hat
   Backoff, ein gesetztes `next_fire_at` und den Übergang RETRY → starting, und
   `lifecycle.TERMINAL` führt es nicht. Wer die Zeile las, hielt den Job für
   erledigt, während er auf seinen nächsten Versuch wartete.

   `deferred` stand bei `pending` im Grau und gilt als **aktiv**:
   `_live_placeholder_row()` zählt es zu den laufenden, `pending` ausdrücklich
   nicht. Die Farbe gruppierte damit genau gegen die Logik.

   Beide tragen jetzt Amber — zusammen mit `awaiting`, weil #33 alle drei auf
   der hohen Aufmerksamkeitsstufe führt. **Was sie unterscheidet, ist Bewegung
   und nicht Farbe:** `awaiting` steht still (es passiert nichts, bis jemand
   handelt), `failed`/`deferred` tragen den Ruhepuls. Die Farbe sagt „hier ist
   Aufmerksamkeit nötig", der Marker sagt „wer als nächstes handelt". */
.st.awaiting, .st.failed, .st.deferred { color: var(--amber); }
.st.pending { color: var(--dim); }
.st.error, .st.killed, .st.zombie { color: var(--red); }
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
/* Links tragen die Marke — bis #68 hatten sie ueberhaupt keine eigene Farbe
   und erbten den Browser-Standard (im Light-Mode ein dunkles Lila). Das war
   schon einmal ein Lesbarkeitsproblem und wurde in der Logbox punktuell
   uebermalt; mit einer eigenen Farbe erledigt sich der Sonderfall. Terracotta
   heisst hier Interaktion und nur das — sie ist an keiner Typ- oder
   Zustandsstelle vergeben. */
a.slug { font-weight: 600; text-decoration: none; color: var(--brand); }
a.slug:hover { text-decoration: underline; }
h2 { font-size: .95rem; color: var(--dim); margin: 1.5rem 0 .4rem; font-weight: 600; }
.back { color: var(--dim); text-decoration: none; font-size: .85rem; }
.tab-active { font-weight: 600; border-bottom: 2px solid currentColor; }
/* Auf einer Unterseite ist derselbe Tab ein Link (m.rau/bibi#148) und muss
   trotzdem aussehen wie der Nicht-Link auf dem Screen selbst — sonst wechselt
   die Hervorhebung ihre Farbe, je nachdem wie tief man steht. */
a.tab-active { color: inherit; text-decoration: none; }
a.tab-active:hover { text-decoration: none; }
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
/* Der Denkabschnitt eines claude-Laufs, einklappbar (m.rau/bibi#99). Die
   Zusammenfassung tritt noch weiter zurueck als der Inhalt: sie ist eine
   Handhabe, keine Aussage. */
.term .think { display: block; }
.term .think > summary { color: var(--faint); font-style: italic;
                         cursor: pointer; list-style: none; }
.term .think > summary::-webkit-details-marker { display: none; }
.term .think > summary::before { content: "▾ "; }
.term .think:not([open]) > summary::before { content: "▸ "; }
.term .phase { color: var(--blue); font-style: italic; }
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

/* Bewegung ist ab v0.8.3 Information, also braucht sie einen Weg ohne Bewegung
   (#68 Punkt 4). Bis dahin gab es in dieser Datei kein einziges
   `prefers-reduced-motion` — die eine vorhandene Animation lief ungefragt.

   **Erhalten, nicht abschalten.** Ein Block, der nur `animation: none` setzt,
   nimmt die Aussage mit weg: der Spinner bedeutet „die Anfrage läuft", und ein
   unsichtbarer Spinner bedeutet nichts. Er steht deshalb still und sichtbar da,
   statt zu pulsieren. Dieselbe Regel gilt für jeden Marker, der hier
   dazukommt. */
/* Der Wertwechsel (#67 Schritt 1): Eingang schnell, Ausgang langsam.

   150 ms rein und 3 s raus — das Verhaeltnis ist die Aussage. Ein Blitz, der so
   schnell verschwindet, wie er kommt, wird uebersehen; einer, der gleich
   schnell ein- und ausblendet, sieht aus wie ein Flackern. Der schnelle Eingang
   holt den Blick, der lange Ausgang laesst ihn ankommen.

   4,76 % von 3150 ms sind die 150 ms des Eingangs. */
@keyframes bibi-cellflash {
    0%    { background: transparent;    color: inherit; }
    4.76% { background: var(--flashbg); color: var(--flashfg); }
  100%    { background: transparent;    color: inherit; }
}
td.cellflash { animation: bibi-cellflash 3.15s ease-out 1; }

@media (prefers-reduced-motion: reduce) {
  .htmx-request .btn-spinner { animation: none; opacity: 1; transform: none; }
  /* **Erhalten, nicht abschalten.** Der Blitz sagt „hier hat sich etwas
     geaendert"; ohne ihn waere die Aenderung unsichtbar. Statt der Animation
     bleibt die Markierung deshalb stehen — dieselbe Aussage ohne Bewegung.
     `_DIFF_JS` nimmt sie beim naechsten Swap wieder weg. */
  td.cellflash { animation: none; background: var(--flashbg); color: var(--flashfg); }
}
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
.logbox .ln.idle    { color: var(--dim); font-style: italic; }
/* Die Logbox ist theme-unabhaengig dunkel, ihre Links brauchen deshalb den
   Dark-Ton der Marke statt des Theme-Tons — im Light-Mode saesse sonst ein
   dunkles Terracotta auf Anthrazit. Der Anlass war urspruenglich ein anderer
   (Chromes Standard-Linkfarbe, im Light-Mode dunkles Lila, User-Fund "im
   Light Mode ist die Schriftfarbe lila schwer zu lesen"); den erledigt die
   eigene Linkfarbe aus #68 mit, der Kontrast-Grund bleibt. */
.logbox a.slug { color: var(--term-link); }
#bands h3 { margin: .7rem 0 .3rem; font-size: .95rem; }
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
/* Die Filterwerte sitzen seit #31 unter ihrer Spalte, nicht mehr in der Leiste.
   Hier standen bis dahin `.fltr-grp`-Regeln fuer die Gruppenlabels `TYPE` und
   `STATUS` — sie sind mit dem Markup entfallen, weil der Spaltenkopf einen
   Zentimeter tiefer dasselbe Wort traegt.

   Die Zeile ist bewusst leiser als die Koepfe darueber: sie ist ein Handle,
   keine Ueberschrift, und ohne die duennere Schrift konkurriert sie mit der
   Spaltenbeschriftung um dieselbe Aufmerksamkeit. */
tr.fltr-kopf th { border-bottom: 1px solid var(--line); padding-bottom: .3rem;
                  font-weight: normal; }
th.fltr-zelle { white-space: nowrap; }
th.fltr-zelle .fltr { font-size: .82rem; padding: .05rem .35rem; }
.fltr { background: none; border: 1px solid transparent; color: var(--dim);
        padding: .1rem .45rem; border-radius: 3px; cursor: pointer;
        font: inherit; }
.fltr:hover { color: var(--text); background: var(--hover); }
/* Der gewaehlte Zustand traegt Rahmen UND Farbe: Farbe allein geht in hellen
   Themes und auf schlechten Monitoren verloren. */
.fltr.on { color: var(--text); border-color: var(--btnline); background: var(--btnbg); }
/* `gone` trifft nichts, solange keine abgelegte Zeile sichtbar ist (#31).
   Ausgegraut statt versteckt: ein Knopf, der verschwindet, laesst die Achse
   springen, und wer ihn sucht, weiss nicht, ob es ihn nie gab oder ob er
   gerade nur leer ist. Der Cursor sagt dasselbe noch einmal fuer die Maus. */
.fltr.tot { opacity: .4; cursor: default; }
.fltr.tot:hover { color: var(--dim); background: none; }
.fltr-zahl { margin-left: auto; color: var(--dim); }
table.jobs th[data-sort] { cursor: pointer; user-select: none; }
table.jobs th[data-sort]:hover { color: var(--text); }
table.jobs th.sortiert { color: var(--text); }
/* Mindestbreiten fuer LAST und NEXT (#31). Die Zellen tragen `11/01 14:46`,
   beim Jahreswechsel aber auch `01/01/2027 00:05` — ohne feste Breite
   springen die Spalten dann, und zwar genau in dem Moment, in dem jemand
   hinsieht. `nowrap` allein reicht nicht: es verhindert den Umbruch, nicht
   das Wandern der Nachbarspalten.

   Die Breite haengt an der Spaltenposition, nicht an einer Klasse — die
   Zellen tragen bis auf `slug` keine, und eine einzufuehren, damit das CSS
   huebscher wird, waere eine Aenderung am Markup fuer eine Frage der
   Darstellung. */
table.jobs td:nth-child(5), table.jobs td:nth-child(6),
table.jobs th:nth-child(5), table.jobs th:nth-child(6) { min-width: 6.5rem; }
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
/* Eine tickende Dauer darf nicht zappeln. Die Design-Studie hat es gemessen:
   die Ziffernbreiten der System-Sans schwanken um 39 % (5,77 bis 8,00 px), und
   eine sekuendlich fortgeschriebene Zelle wechselt dabei sichtbar ihre Breite.
   `tabular-nums` haelt sie konstant (7,83 px). Vor #122 fiel das nicht auf,
   weil keine dieser Zellen sich je von selbst geaendert hat. */
.dur { font-variant-numeric: tabular-nums; }
.chip { font-family: ui-monospace, monospace; font-size: .7rem; font-weight: 700;
        padding: .1rem .45rem; border-radius: .3rem; display: inline-block; white-space: nowrap; }
/* Git-Status je Job-MD (PLAN-21 Befund 10) — löst die vorherige Lokal/Remote-
   Abgleich-Chips (same/diff/local_only/remote_only) ab. */
.chip.clean { background: var(--greensoft); color: var(--green); }
.chip.modified { background: var(--ambersoft); color: var(--amber); }
.chip.new { background: var(--bluesoft); color: var(--blue); }
.chip.conflict { background: var(--redsoft); color: var(--red); }
/* Beziehungslabels der Jobs-Zeile (#31, Vorschlag 1). Zwei Lautstaerken, und
   die Zweiteilung ist die eigentliche Aussage: `leise` traegt ein Verhaeltnis
   zwischen zwei Speichern (new/modified/deleted/dropped) und verlangt
   Kenntnis; `bad` meldet einen Fehler im Vault und verlangt Handeln. Sind
   alle gleich laut, ist keiner mehr laut. */
.chip.leise { background: var(--hover); color: var(--dim); font-weight: 500; }
.chip.bad { background: var(--redsoft); color: var(--red); }
/* Faelligkeitskennzeichen der NEXT-Spalte (#11). Es steht NEBEN dem
   Zeitpunkt, nicht an seiner Stelle: der Zeitpunkt sagt, wie lange etwas
   ueberfaellig ist, das Kennzeichen, dass es das ueberhaupt ist. Leiser als
   ein Chip, weil eine Verspaetung kein Fehler ist — der Scheduler holt sie
   beim naechsten Tick. */
.due { color: var(--amber); font-size: .78rem; letter-spacing: .03em; }
/* Der Weg von der Kachel zur Lauf-Liste (#39). Leiser als die Verben daneben:
   er ist eine Navigation, keine Handlung, und darf mit START/KILL/RESET nicht
   um dieselbe Aufmerksamkeit konkurrieren. */
.tile-weg { color: var(--faint); text-decoration: none; }
.tile-weg:hover { color: var(--text); text-decoration: underline; }
/* Nodes-Screen Git-Status-Chips (Batch 9 Punkt 3) — dieselben Farben wie die
   tree- und sync-Klassen der Feed-Git-Kachel, hier als Chip statt Klartext.
   Die Klassennamen stehen bewusst ohne Stern: ".tree-" plus Stern ergibt die
   Folge, die einen CSS-Kommentar schliesst — der Rest dieses Satzes landete
   dadurch als ungueltiges CSS im Stylesheet, wo ihn nur der Parser sah. */
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
/* Der Weg zum Dienst, an der Kachel des Knotens, der ihn faehrt
   (m.rau/bibi#104). Normal gewichtet neben der fetten Kopfzeile: die Kachel
   sagt zuerst, WO sie steht — der Link ist ein Angebot, keine Ueberschrift. */
.tile-app { font-weight: 400; margin-left: .4rem; }
.tile-state { font-family: ui-monospace, monospace; font-size: .85rem; }
/* Gesperrte Kachel (m.rau/bibi#146): sichtbar, aber erkennbar nicht bedienbar
   — dieselbe Behandlung wie der offline-Header, der gedimmt wird und seine
   Werte behält, statt zu verschwinden (FE §2). Gestrichelter Rand, weil die
   Dimmung allein im Light-Mode zu schwach trägt. */
.tile-off { opacity: .55; border-style: dashed; }
.tile-off .tile-state { font-style: italic; }
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
/* Lauf-Attribute (#40): dieselbe Zeile, um eine Herkunftsspalte erweitert.
   Die Spaltenbreiten stehen an `.attrs-head`, damit Kopf und Zeilen nicht
   auseinanderlaufen koennen — sie erben beide dasselbe Raster. */
.attrs-head, .attrs .attr-row { grid-template-columns: 12rem 1fr 5rem; }
.attrs-head { display: grid; gap: .4rem; padding: .18rem 0;
              border-bottom: 1px solid var(--line); color: var(--hdr-key);
              font-family: ui-monospace, monospace; font-size: .72rem;
              letter-spacing: .04em; }
.attr-src { color: var(--faint); font-family: ui-monospace, monospace;
            font-size: .72rem; text-align: right; }
.attrs-note { font-size: .78rem; max-width: 60rem; margin: .2rem 0 1rem; }

/* Leerer Zustand und Nachladen. */
.empty { color: var(--dim); font-size: .85rem; font-style: italic;
         padding: .8rem .2rem; }
.more { display: flex; justify-content: flex-start; gap: .5rem; margin: .8rem 0 0; }

"""


def _dauer_span(text: str, art: str, anker: float) -> str:
    """Eine Dauer, die im Browser weiterzählt (Thema A, #122).

    **Der Server liefert den Anker, der Browser zählt.** Eine Dauer ist keine
    Nachricht — sie ist eine reine Funktion aus einem Zeitstempel und *jetzt*,
    und ein Roundtrip pro Sekunde wäre für einen Wert, den jedes Gerät selbst
    ausrechnen kann, der Firehose in Reinform. Genau so arbeitet die
    Kopfzeilen-Uhr seit jeher (``_CLOCK_JS``).

    **Der Text bleibt trotzdem im HTML.** Ohne JS steht dann eine
    stehengebliebene Zahl da statt einer leeren Zelle — dieselbe Entscheidung
    wie beim Erstbild: eine stehengebliebene Seite kann man lesen.
    """
    return (f'<span class="dur" data-dur="{art}" data-at="{anker}">'
            f'{text}</span>')


def _ago_text(d: int) -> str:
    """Nur die Regel, ohne Huelle — damit der Test sie mit dem Browser
    vergleichen kann, ohne HTML abziehen zu muessen."""
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60} min ago"
    if d < 86400:
        return f"{d // 3600} h ago"
    return f"{d // 86400} d ago"


def _ago(ts: float | None, now: float) -> str:
    """Vergangenheits-Distanz. **Traegt immer einen Anker**, weil sie per
    Definition eine Distanz zu *jetzt* ist — sie steht keine Sekunde still,
    ausser der Browser laesst sie."""
    if ts is None:
        return "—"
    return _dauer_span(_ago_text(max(0, int(now - ts))), "ago", ts)


def _human_duration(seconds: float | None, *, seit: float | None = None) -> str:
    """Dauer (kein Zeitpunkt) als angepasstes Delta — Bibi4-Iteration, User-
    Fund: "die Spalte Laufzeit soll human-readable sein und nicht nur die
    Sekunden zeigen, sondern je nach Dauer ein angepasstes Delta". Analog zu
    ``_ago()``/``_until()``, aber ohne "vor"/"in"-Präfix (reine Dauer, keine
    Distanz zu ``now``) und mit zwei Einheiten je Stufe (z.B. "3m 12s") statt
    einer, damit eine 90-Minuten-Laufzeit nicht auf "1h" abgerundet wird."""
    if seconds is None:
        return "—"
    text = _dauer_text(seconds)
    # **Ohne Anker kein Ticken, und das ist die wichtigere Haelfte.** Eine
    # abgeschlossene Laufzeit ist ein Ergebnis, kein Zeitraum, der gerade
    # vergeht; ein P90 ist eine Kennzahl ueber viele Laeufe. Beide duerfen
    # nicht weiterzaehlen. Wer die Dauer eines LAUFENDEN Vorgangs zeigt, gibt
    # `seit` mit — die Entscheidung gehoert zum Aufrufer, weil nur er weiss,
    # ob der Vorgang noch laeuft.
    return text if seit is None else _dauer_span(text, "since", seit)


def _dauer_text(seconds: float) -> str:
    """Nur die Regel, ohne Huelle (s. ``_ago_text``)."""
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
    return _dauer_span(_until_text(int(ts - now)), "until", ts)


def _until_text(d: int) -> str:
    """Nur die Regel, ohne Huelle (s. ``_ago_text``)."""
    if d <= 0:
        return "asap"
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


def _node_link_cell(worker: str | None, host: str | None, port: int | None,
                    role: str | None = None) -> str:
    """Name+Host zu einem Link kombiniert (Batch 9 Punkt 3, User-Fund:
    ``[{name} :{port}](http://{host}:{port}/-/)``) — die URL, wie der Knoten
    sich selbst kennt (sein eigener ``BIBI_DAEMON_PORT``), nicht wie ein
    anderer Knoten ihn erreichen würde; bewusst so, auch wenn das bei
    ``localhost`` verwirrend aussieht (User-Entscheidung, s. „## Clients
    Screen"). Ohne ``port`` (älterer Client vor dieser Änderung, oder erster
    Heartbeat noch nicht durch) bleibt es reiner Text statt totem Link.

    **Ohne Controller-Rolle gibt es kein ``/-/``, und damit keinen Link**
    (#118). Ein reiner Scheduler-Knoten ist seit der Entscheidung vom
    2026-08-04 nur Backend — sein ``/-/`` antwortet planmäßig mit ``404``, und
    ein Link dorthin ist nicht gelegentlich tot, sondern strukturell. Die Rolle
    steht in derselben Zeile, die auch die ``CRON``/``CTRL``/``SYNC``/``WORK``-
    Spalten füllt; die Auskunft war da, sie wurde nur nicht gelesen.

    **Fehlt die Rolle, entsteht ebenfalls kein Link.** Ein älterer Client
    heartbeatet ohne ``role``, und aus „weiß ich nicht" ein „hat ein Frontend"
    zu machen wäre genau die Sorte Annahme, die diesen Befund erzeugt hat.
    Derselbe Text-statt-Link-Pfad wie beim fehlenden Port darüber."""
    name = _e(worker or "—")
    if not host or not port:
        return name
    if "controller" not in {r.strip() for r in (role or "").split(",")}:
        return name
    href = _e(f"http://{host}:{port}/-/")
    return f'<a href="{href}" target="_blank" rel="noopener">{name} :{port}</a>'


_NODE_TREE_CHIP_CLASS = {"clean": "chip clean", "modified": "chip modified",
                         "conflict": "chip conflict"}
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


def _node_sync_state_chips(w: dict) -> str:
    """Ob dieser Knoten seine Arbeit **loswird** (m.rau/bibi#74).

    Die Git-Status-Chips daneben sagen, wie der Baum *aussieht*. Diese hier
    sagen, ob er sich überhaupt noch bewegt — und das ist die Angabe, die 43
    Stunden lang niemand hatte: ``sarasate-client`` hing in einem Sync-Konflikt
    und meldete es 102-mal an eine Oberfläche, die im Normalbetrieb niemand
    öffnet. Aufgefallen ist es erst, weil ein Rollout zufällig danach fragte.

    **Stille ist der Normalfall.** Ein gesunder Knoten bekommt hier nichts —
    eine Warnung, die immer leuchtet, wird nach dem zweiten Mal überlesen.
    ``None`` (der Knoten sendet die Angabe nicht) ist ebenfalls still: das ist
    *unbekannt*, und eine Behauptung wäre schlimmer als eine Lücke.
    """
    teile = []
    if w.get("sync_conflict"):
        teile.append('<span class="chip conflict" title="this node cannot push '
                     'its work — resolve with /sync">sync blocked</span>')
    if w.get("auto_sync") is False:
        teile.append('<span class="chip modified" title="auto-sync is off — '
                     'work stays local until someone syncs by hand">'
                     'sync off</span>')
    # m.rau/bibi#111: die zweite Konflikt-Sorte — nicht "dieser Knoten kommt
    # mit origin nicht klar" (oben), sondern "die Arbeit eines Jobs kommt
    # nicht nach trunk". Branch-Namen mit in den Chip, nicht nur eine Zahl:
    # der Abnahmefall ist genau die Frage "welcher Branch", und eine Zahl
    # allein hätte agent/Witz nicht benannt.
    stuck = w.get("merge_stuck") or []
    if stuck:
        namen = html.escape(", ".join(stuck))
        teile.append(f'<span class="chip conflict" title="merge-back cannot '
                     f'land this work — resolve with /sync">merge stuck: '
                     f'{namen}</span>')
    return (" " + " ".join(teile)) if teile else ""


#: Aktualitäts-Chip der Engine-Zelle. Die Wörter sind bewusst die der
#: Repo-Zelle (``synced``/``behind``) statt der internen Verdict-Namen aus
#: ``deploy.update_state()`` — nebeneinander gelesen sollen beide Zeilen
#: dieselbe Sprache sprechen (m.rau/bibi#67).
_NODE_ENGINE_VERDICT: dict[str, tuple[str, str]] = {
    "current": ("chip clean", "running the expected tag"),
    # m.rau/bibi#127: die Lock ist angekommen, der Prozess ist alt. Eigene
    # Stufe zwischen `current` und `behind`, weil hier eine ANDERE Handlung
    # hilft — ein Neustart, kein Sync. Bis dahin sahen beide Lagen gleich aus.
    "restart pending": ("chip modified",
                        "the expected revision is installed — this node is "
                        "still running the previous one"),
    "behind": ("chip conflict", "a newer revision is pinned"),
    "branch": ("chip modified",
               "pinned to a branch — this node cannot tell whether it "
               "has moved on"),
    "unknown": ("chip", "expected or actual revision missing"),
}


def _node_engine_cell(engine: str | None, expected: str | None = None,
                      tree: str | None = None,
                      installed: str | None = None) -> str:
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
            ("(editable)", "running against a working checkout, not the "
                           "pinned revision"),
            # m.rau/bibi#58: eine Kopie eines Verzeichnisses sieht aus wie ein
            # Release und ist keins — derselbe Unterschied zum gepinnten Stand
            # wie beim editable install, nur schlechter zu bemerken.
            ("(local)", "installed from a local directory, not from the "
                        "pinned tag")):
        if marker in engine:
            base = engine.replace(marker, "").strip()
            return (f'{_e(base)}{_tree_chip()} '
                    f'<span class="chip conflict" title="{title}">'
                    f'{marker.strip("()")}</span>')

    from bibi.daemon import deploy as deploy_mod
    verdict = deploy_mod.node_verdict(expected, engine, installed)
    cls, title = _NODE_ENGINE_VERDICT.get(
        verdict, ("chip", "revision cannot be determined"))
    cell = f'{_e(engine)}{_tree_chip()}'
    cell += f' <span class="{cls}" title="{_e(title)}">{_e(verdict)}</span>'
    if verdict == "behind":
        # **Nur hier, nicht bei ``restart pending``** (m.rau/bibi#127): dort
        # liegt die erwartete Version schon auf der Platte. Ein „NEED UPDATE"
        # daneben schickte jemanden den Sync prüfen, während in Wahrheit nur
        # ein Neustart fehlt — der Verdict-Chip nennt die Handlung bereits.
        cell += (f' <span class="chip conflict" title="expected {_e(expected or "")}">'
                 "NEED UPDATE</span>")
    return cell


def _node_session_chip(session: bool | None) -> str:
    """„Dieser Knoten läuft in einer Sitzung und hat keinen Supervisor" (#44).

    **Der Chip hat den Rückbau aus #103 überlebt, die Knöpfe daneben nicht** —
    und das ist eine Entscheidung, keine Nachlässigkeit. Er saß in der
    Restart-Zelle, weil er vor einem Klick warnte, der die eigene Sitzung
    abschießt; mit der Zelle wäre er verschwunden. Er sagt aber etwas über den
    **Knoten** aus, nicht über den Knopf, und diese Aussage wird mit dem
    automatischen Rollout *wichtiger* statt überflüssig: ein Sitzungs-Knoten,
    der sich selbst neu startet, kommt von allein nicht zurück.

    **Er steht jetzt an der Namenszelle.** Sie benennt den Knoten, und wie er
    betrieben wird, gehört dazu. Die Engine-Spalte wäre die andere Wahl
    gewesen — dort steht die Version, um die es beim Rollout geht —, aber sie
    trägt bereits Version, Baum-Chip, Verdict und `NEED UPDATE`; ein fünftes
    Zeichen hätte die Zelle unlesbar gemacht.

    ``session=None`` heißt *unbekannt* (Client älter als #44) und schweigt:
    eine Behauptung über die Herkunft wäre schlechter als keine.
    """
    if not session:
        return ""
    return (' <span class="chip modified" title="no supervisor — it will not '
            'come back on its own">session</span>')


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
        return '<p class="out-empty">— no nodes —</p>'
    rows = []
    for w in sorted(workers, key=lambda w: w.get("worker") or ""):
        stale = w.get("stale", False)
        status_html = ('<span class="chip conflict">disconnected</span>' if stale
                       else '<span class="chip clean">connected</span>')
        rows.append(
            "<tr>"
            f"<td>{_node_link_cell(w.get('worker'), w.get('host'), w.get('port'), w.get('role'))}"
            f"{_node_session_chip(w.get('session'))}</td>"
            f"{_role_matrix_cells(w.get('role'))}"
            f"<td>{_node_engine_cell(w.get('engine'), expected, w.get('engine_tree'), w.get('engine_installed'))}</td>"
            f"<td>{_e(w.get('git_user') or '—')}</td>"
            f"<td>{_node_git_status_chips(w.get('git_status'))}"
            + (f' <code>{_e(w["git_commit"])}</code>' if w.get("git_commit") else "")
            # m.rau/bibi#74: neben „wie sieht der Baum aus" jetzt auch „bewegt
            # er sich noch". Dieselbe Spalte, weil es dieselbe Frage ist.
            + _node_sync_state_chips(w)
            + "</td>"
            f"<td>{status_html}</td>"
            f"<td>{_node_approval_cell(w.get('node_id'), w.get('approval_status', 'pending'))}</td>"
            f"<td>{_abs_datetime(w.get('connected_at'), now)}</td>"
            f"<td>{_ago(w.get('last_heartbeat'), now)}</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Name</th>'
        f"{_role_matrix_header()}"
        '<th>Engine</th><th>Git user</th>'
        # Keine `Restart`-Spalte mehr (#103): ein Knoten wird nicht gestoppt,
        # gestartet, restartet oder deployed — er holt sich den geforderten
        # Stand selbst, wenn Ist und Soll auseinanderlaufen.
        '<th>Git status</th><th>Status</th><th>Approval</th>'
        '<th>Connected since</th>'
        '<th>Last heartbeat</th></tr></thead>'
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
            msg = (f'<span class="chip clean">set: {_e(deploy_result.get("ref",""))}</span>'
                   f' <span class="ts-dim">(was {_e(deploy_result.get("was",""))}'
                   f'{", pushed" if deploy_result.get("pushed") else ", NOT pushed"})</span>')
        elif deploy_result.get("ok"):
            msg = f'<span class="ts-dim">{_e(deploy_result.get("note",""))}</span>'
        else:
            # Der Fehlerfall ist der wichtigere: uv lock scheitert, wenn der Tag
            # nicht existiert — dann wurde zurückgerollt und nichts committet.
            msg = (f'<span class="chip conflict">{_e(deploy_result.get("error",""))}</span>'
                   f' <span class="ts-dim">{_e(deploy_result.get("detail",""))}</span>')
    return (
        '<p class="handles">'
        '<label>Expected engine version '
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
        f'hx-disabled-elt="this">Set{_BTN_SPINNER}</button> {msg}'
        '</p>'
    )


def clients_fragment(workers: list[dict], now: float | None = None, *,
                     deploy_result: dict | None = None) -> str:
    now = time.time() if now is None else now
    return (
        '<div id="clientsboard" data-bus="nodes" data-bus-refetch="/-/ui/clients/board">'
        '<div class="panel-card"><h2>Nodes</h2>'
        f"{_expected_version_form(deploy_result)}"
        # Hier standen „Restart all" und „Deploy all" (m.rau/bibi#39). Sie sind
        # mit #103 gefallen, zusammen mit „Set + deploy" und der Restart-Spalte
        # je Zeile. Der Bauplan sagt es wörtlich: *„Ein Scheduler/Worker/Client
        # kann nicht gestoppt, gestartet, restartet oder deployed werden. Das
        # gilt ebenso für Restart all und Deploy all."* Stattdessen der moderne
        # Weg — jeder Knoten holt sich den geforderten Stand selbst, wenn Ist
        # und Soll auseinanderlaufen.
        #
        # **Die Routen bleiben**, nach m.raus Auftrennung der Reihenfolge:
        # *„Die Endpunkte können bestehen bleiben. Das FE kann die Buttons
        # trotzdem schon zurück bauen."* Sie fallen erst, wenn der Auslöser
        # gebaut ist — heute vermittelt der Heartbeat keinen Restart.
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
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Nodes</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Nodes', daemon_status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{clients_fragment(workers, now)}"
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_DIFF_JS}</script>"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_JS}</script>"
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



# Hier stand `_effective_sched_type()` — ein Ein-Zeilen-Delegator an
# `models.effective_kind()`. Sein letzter Aufrufer war `_local_job_meta_line()`,
# und der ist mit `#96` auf `_jobs_type_cell()` umgestellt worden, weil er den
# `app_port` mitlesen muss. Damit war die Funktion aufrufer-los — eigene
# Hinterlassenschaft desselben Tages, entfernt mit `#100`. Wer den Typ braucht,
# ruft `models.effective_kind()` direkt; wer ihn anzeigen will,
# `models.display_kind()` (mit `app_port`) oder `_jobs_type_cell()`.


# Hier standen `_SORT_KEYS` und `sort_rows()` (m.rau/bibi#66) — der
# serverseitige Sortierpfad des bibi4-Screens. Der Jobs-Screen sortiert seit
# dem v5-Umbau über `jobs_view.sortiere()`; `sort_rows()` hatte danach keinen
# Aufrufer mehr, `_SORT_KEYS` blieb als Whitelist für den `sort`-Parameter
# zurück (#95). Sie ist mit den Spalten nicht mitgewachsen: `24h` fehlte,
# `runtime` stand darin, obwohl es nie ein klickbarer Kopf war. Die Whitelist
# leitet sich jetzt aus `_SORTIERBAR` ab — siehe `sortierbare_schluessel()`.


#: Die sechs Screens, in der Reihenfolge der App-Bar. Feed und Jobs sind die
#: täglichen, Journal steht neben Jobs (es war dessen drittes Segment), Nodes
#: ist Betrieb, Live und Log sind Diagnose.
#:
#: **Archive bleibt gestrichen** (m.rau/bibi#130, FE-Spezifikation §1 und §6).
#: `Journal` ist nicht seine Rücknahme, und der Unterschied muss sichtbar
#: bleiben, sonst wird der alte Screen versehentlich wiederbelebt: der alte Tab
#: führte **Läufe** aller Jobs nach Zeit und beantwortete „was lief heute
#: Nacht?" — die Frage, die die `24H`-Spalte des Jobs-Screens in einer Zahl
#: beantwortet und die deshalb gestrichen wurde. Dieser hier führt **Jobs**, je
#: Slug aggregiert, und beantwortet „welche Jobs haben nur noch Historie?".
#:
#: Er heißt `Journal` und nicht `Archive` (#38): so heißt die Sache in der
#: Engine und in FE §4.1 auch, und `Archive` hat in `OneClient.md` bereits zwei
#: verschiedene Dinge bezeichnet und beide Seiten in die Irre geführt.
#:
#: **Seine Adresse liegt unter `/-/jobs/`, und das ist keine Marotte:**
#: `/-/journal` ist seit jeher die Journal-**API** des Schedulers
#: (``daemon/app.py``), die der Controller-Client selbst aufruft. Ein Knoten
#: mit beiden Rollen — sarasate ist einer — führt beide Routen in derselben
#: App; die zweite wäre stumm verdeckt, und je nach Registrierungsreihenfolge
#: bekäme entweder der Browser JSON oder der Client HTML. Der Pfad sagt
#: außerdem, was der Screen ist: das dritte Segment von Jobs, umgezogen.
SCREENS: tuple[tuple[str, str], ...] = (
    ("Feed", "/-/"),
    ("Jobs", "/-/jobs"),
    ("Journal", "/-/jobs/journal"),
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
    # **Diese Dauer tickt, und das ist der Unterschied zur Runtime** (m.rau in
    # #67): beide zaehlen hoch, aber ein wachsender Kontaktverlust ist
    # unerwartet und verdient Aufmerksamkeit, eine wachsende Laufzeit ist
    # erwartet. Vom Diff ausgenommen wird, was erwartet ist — nicht, was tickt.
    titel_zusatz = (f' — no contact for '
                    f'{_human_duration(now - scheduler_stale_since, seit=scheduler_stale_since)}'
                    if stale else "")

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


def _screen_nav(active: str, roles: list[str] | None = None, *,
                sub: bool = False) -> str:
    """Die App-Bar: sechs Screens, der aktive ohne Link — außer man steht darunter.

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

    ``sub`` sagt, dass die zeigende Seite **unterhalb** von ``active`` liegt
    (m.rau/bibi#148). Dann bleibt der Tab hervorgehoben und wird zusätzlich
    zum Rückweg. Der Unterschied ist nicht *aktiv gegen inaktiv* — das war die
    alte Lesart und sie machte den Tab auf jeder Unterseite tot — sondern *auf
    dem Screen gegen unterhalb davon*: ein Link auf die Seite, auf der man
    steht, ist eine Sackgasse; einer auf den Screen darüber ist der Weg zurück.
    """
    def _tab(t: str, h: str) -> str:
        if t == active:
            if sub:
                return f'<a class="tab-active" href="{h}">{t}</a>'
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


#: Schreibt jede Dauer sekündlich fort — rein client-seitig, ohne ein einziges
#: Server-Ereignis (Thema A der FE-Ereignisarchitektur, #122).
#:
#: **Die drei Regeln stehen hier zwangsläufig ein zweites Mal.** Der Server
#: rendert das Erstbild, der Browser schreibt es fort; beide müssen dieselbe
#: Zahl gleich schreiben, sonst springt der Text beim ersten Tick sichtbar um.
#: ``tests/test_live_durations.py`` vergleicht deshalb beide Seiten Wert für
#: Wert gegen einen Node-Harnisch — ohne ihn fiele ein Auseinanderlaufen erst
#: im Betrieb auf, und dann als Zucken, das niemand einem Commit zuordnet.
_DURATION_JS = """
(function(){
  function dauer(sek){
    const d = Math.max(0, Math.trunc(sek));
    if (d < 10) return Math.max(0, sek).toFixed(1) + 's';
    if (d < 60) return d + 's';
    if (d < 3600) return Math.trunc(d/60) + 'm ' + (d%60) + 's';
    if (d < 86400) return Math.trunc(d/3600) + 'h ' + Math.trunc((d%3600)/60) + 'm';
    return Math.trunc(d/86400) + 'd ' + Math.trunc((d%86400)/3600) + 'h';
  }
  function ago(d){
    if (d < 60) return d + 's ago';
    if (d < 3600) return Math.trunc(d/60) + ' min ago';
    if (d < 86400) return Math.trunc(d/3600) + ' h ago';
    return Math.trunc(d/86400) + ' d ago';
  }
  function until(d){
    if (d <= 0) return 'asap';
    if (d < 60) return 'in ' + d + 's';
    if (d < 3600) return 'in ' + Math.trunc(d/60) + ' min';
    if (d < 86400) return 'in ' + Math.trunc(d/3600) + ' h';
    return 'in ' + Math.trunc(d/86400) + ' d';
  }
  window.__bibiDauer = { dauer: dauer, ago: ago, until: until };
  function tick(){
    const jetzt = Date.now()/1000;
    document.querySelectorAll('[data-dur]').forEach(function(el){
      const at = parseFloat(el.getAttribute('data-at'));
      if (!isFinite(at)) return;
      const art = el.getAttribute('data-dur');
      let text;
      if (art === 'since') text = dauer(jetzt - at);
      else if (art === 'ago') text = ago(Math.max(0, Math.trunc(jetzt - at)));
      else if (art === 'until') text = until(Math.trunc(at - jetzt));
      else return;
      // Nur schreiben, wenn sich etwas aendert: ein unveraendertes
      // `textContent` zu setzen macht eine laufende CSS-Animation kaputt —
      // und genau die baut Welle 3 auf diese Zellen.
      if (el.textContent !== text) el.textContent = text;
    });
  }
  tick(); setInterval(tick, 1000);
})();
"""


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
#: persistiert in localStorage. Symbol statt Text
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


def _header(active: str, status: dict | None = None, *,
            scheduler: dict | None = None, sub: bool = False,
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
    left = f'<h1>bibi</h1>{_screen_nav(active, roles, sub=sub)}'
    # Die Uhr zeigt die Zeit des Schedulers, nicht die eigene — deshalb reisen
    # sein `now` und der Renderzeitpunkt bis hierher durch.
    right = (f'{_ops_handles(status, scheduler=scheduler)}'
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
  idleEl = null;                          // die alte Referenz ist mit box.innerHTML weg
  for (const o of buf){ if(passes(o)) box.appendChild(line(o)); }
  autoscroll();
  refreshIdle();
}
// #112: leer sagte bisher nicht "ruhig", sondern nichts -- ununterscheidbar
// von einem abgerissenen Strom oder einem Filter, der zu viel wegnimmt.
// lastActivityAt zaehlt JEDES Ereignis, nicht nur das erste -- eine lange
// Stille NACH einer Aktivitaet ist derselbe Fall wie eine Stille direkt nach
// dem Laden.
let lastActivityAt = Date.now();
let idleEl = null;
const IDLE_AFTER_MS = 20000;              // etwas ueber dem 15s-Ping-Rhythmus von _EVENTS_JS
function fmtClock(ts){
  return new Date(ts).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function refreshIdle(){
  const quietFor = Date.now() - lastActivityAt;
  if (quietFor < IDLE_AFTER_MS){
    if (idleEl){ idleEl.remove(); idleEl = null; }
    return;
  }
  const txt = 'Connected — nothing has happened since ' + fmtClock(lastActivityAt) + '.';
  if (!idleEl){
    idleEl = document.createElement('div');
    idleEl.className = 'ln idle';
    box.appendChild(idleEl);
    autoscroll();
  }
  if (idleEl.textContent !== txt) idleEl.textContent = txt;
}
setInterval(refreshIdle, 5000);
function connect(){
  const es = new EventSource('/-/log/stream?n=200');
  es.onmessage = (e) => {
    let o; try { o = JSON.parse(e.data); } catch (_) { return; }
    lastActivityAt = Date.now();
    if (idleEl){ idleEl.remove(); idleEl = null; }
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
    seit demselben `feed_status_fragment()` wie auf ``/-/``
    — braucht dafür jetzt auch das htmx-Script-Tag (vorher unnötig, da der
    Log-Stream reines SSE/Plain-JS ist)."""
    now = time.time() if now is None else now
    status = daemon_status or {}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Live-Log</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Live Log', status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"{feed_status_fragment(status, git_status, host_url, now, client_rows=client_rows, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{_log_panel()}"
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_DIFF_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


_TREE_LABEL_CLASS = {"clean": "tree-clean", "modified": "tree-modified"}
_SYNC_LABEL_CLASS = {"synced": "sync-synced", "ahead": "sync-ahead",
                     "behind": "sync-behind", "diverged": "sync-conflict"}


_JOB_STATUS_WAITING = ("pending", "deferred", "failed")
_JOB_STATUS_RUNNING = ("starting", "running", "awaiting")
_JOB_STATUS_STOPPED = ("inactive", "zombie", "error", "killed")
_JOB_STATUS_ROWS = (("Waiting", _JOB_STATUS_WAITING), ("Running", _JOB_STATUS_RUNNING),
                    ("Stopped", _JOB_STATUS_STOPPED))
#: Spaltenreihenfolge der Matrix — dieselben drei Werte wie ``models.display_kind()``.
_JOB_STATUS_KINDS = (("job", "Job"), ("claude", "Claude"), ("app", "App"))


#: Zeilen der Client-Matrix — dieselbe Form wie _JOB_STATUS_ROWS (Label,
#: Menge matchender git_status-Werte), nur git-Gesundheit statt Lifecycle.
#: "clean" bewusst keine eigene Zeile (etablierte Konvention: der stille
#: Normalzustand bleibt unsichtbar, s. _jobs_row()-Docstring) — anders als
#: bei _JOB_STATUS_ROWS sind diese drei Zeilen NICHT erschöpfend für "alle
#: Jobs", sondern zeigen nur die vom Normalzustand abweichenden.
_CLIENT_STATUS_ROWS = (("New", ("new",)), ("Modified", ("modified",)), ("Conflict", ("conflict",)))


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


# ── Jobs-Screen (PLAN-17 Stufe 17.1/17.2, PLAN-21 Befund 10) ─────────────────

#: Git-Status je lokaler Job-MD (PLAN-21 Befund 10, User-Fund: "die Jobs im
#: Repository plus ihr git Status (neu, geändert, etc.) anzeigen"; gelöschte
#: MDs brauchen keinen eigenen Status — sie verschwinden von selbst aus der
#: Liste, da discovery.discover() sie nicht mehr findet).
_GIT_STATUS_LABEL = {
    "new": ("chip new", "new"),
    "modified": ("chip modified", "modified"),
    # Bibi4-Iteration, User-Fund: "sind sie lokal modifiziert, konfliktär,
    # fehlen?" — konfliktär war zuvor nicht von modified unterschieden
    # (local_files_status(), git_status.py).
    "conflict": ("chip conflict", "conflicted"),
    "clean": ("chip clean", "unchanged"),
}

_SPARK_W, _SPARK_H = 72, 20


def _jobs_type_cell(row: dict, public_host: str, *, link: bool = True) -> str:
    """Type-Zelle: ``job``/``claude``, bei Apps ``app :PORT`` (PLAN-29 Befund 2,
    User-Fund: "Type, bei Apps mit Port").

    Bewusst **eigenständig** von ``_effective_sched_type()``/
    ``models.effective_kind()`` — PLAN-25 Befund 7 entfernte "app" dort
    absichtlich aus Schedules-Übersicht/Filter (User-Entscheidung: "Jobs mit
    Port und Prefix sollen einfach als Jobs erscheinen"). Hier soll ein
    App-Job weiterhin als "app" erkennbar sein — rührt an der
    PLAN-25-Vereinfachung nicht.

    **``link=False`` gibt denselben Text ohne Adresse** (m.rau/bibi#104). Der
    Port ist eine Job-Eigenschaft und darf überall stehen; die **Adresse** ist
    es nicht — sie braucht den Knoten, der die App fährt, und ``public_host``
    ist der des Betrachters. Wo der ausführende Knoten nicht bekannt ist (die
    Jobs-Tabelle führt zwei Seiten in einer Zeile), gehört deshalb der Port
    hin und der Link nicht. Verlinkt wird in den Slot-Kacheln, die ihren
    Knoten über ``Tile.host`` kennen."""
    app_port = row.get("app_port")
    kind = models.display_kind(row.get("payload"), app_port)
    if kind != "app":
        return kind
    if not link:
        return f"app :{_e(str(app_port))}"
    href = _e(f"http://{public_host}:{app_port}/")
    return f'<a href="{href}" target="_blank" rel="noopener">app :{app_port}</a>'


#: (PLAN-36 Stufe 36.2: das frühere ``_JOBS_LIVE_AUTOREFRESH_JS`` — der
#: running→terminal-Fingerprint-Vergleich, der ``#journal`` nachlud — ist
#: durch die ``journal:``-Zustands-Events des Bus ersetzt, s. ``_EVENTS_JS``.
#: ``data-running``/``data-journal-url`` bleiben als Diagnose-Attribute
#: erhalten.)


# ── Feed-Screen (PLAN-18 Stufe 18.3) — jetzt Home (``/-/``) ──────────────────

def _feed_commit_cell(sha: str | None, commit_base_url: str | None) -> str:
    if not sha:
        return ""
    short = _e(sha[:7])
    if commit_base_url:
        href = _e(f"{commit_base_url}/commit/{sha}")
        return f'<a class="commit" href="{href}" target="_blank" rel="noopener">{short}</a>'
    return f'<span class="commit">{short}</span>'


def _urheber(authors: list[str]) -> tuple[str, str]:
    """Höchstens zwei Namen, dann ``+n`` — und die volle Liste im ``title`` (#34).

    **Anlass (Fall m.rau):** bei zehn gleich häufigen Urhebern nannte „die
    häufigsten" alle zehn. Die Spalte lief über und sagte dabei weniger, als
    zwei Namen es täten.

    **Sortiert nach Häufigkeit, bei Gleichstand alphabetisch — und das zweite
    ist der eigentliche Inhalt der Regel.** Ohne den Tiebreak hängt die
    Reihenfolge daran, wie die Daten ankamen; die Anzeige springt dann zwischen
    zwei Ladevorgängen, ohne dass sich etwas geändert hätte. Eine Liste, die
    ohne Anlass die Reihenfolge wechselt, liest sich wie eine Nachricht.

    Gibt ``(text, title_attribut)`` zurück; das Attribut ist leer, wenn nichts
    gekürzt wurde — **gekürzt heißt nicht weggeworfen**, aber ein ``title``,
    der dasselbe wiederholt, ist nur ein Tooltip ohne Auskunft.
    """
    if not authors:
        return "—", ""
    haeufig: dict[str, int] = {}
    for a in authors:
        haeufig[a] = haeufig.get(a, 0) + 1
    geordnet = sorted(haeufig, key=lambda a: (-haeufig[a], a))
    if len(geordnet) <= 2:
        return ", ".join(geordnet), ""
    return (f'{", ".join(geordnet[:2])} +{len(geordnet) - 2}',
            f' title="{_e(", ".join(geordnet))}"')


def _feed_row(entry: dict, *, commit_base_url: str | None = None) -> str:
    """Eine Einheit: Uhrzeit, Name, Umfang, Urheber, Commit.

    Nur die Uhrzeit — das Datum steht in der Tagestrennlinie darüber und
    stünde sonst in jeder Zeile ein zweites Mal.
    """
    ts = entry.get("last_changed")
    zeit = _abs_time(ts)
    n = int(entry.get("changes") or 0)
    umfang = f"{n} change" if n == 1 else f"{n} changes"
    wer, alle = _urheber(entry.get("authors") or [])
    commit = _feed_commit_cell(entry.get("last_commit_sha"), commit_base_url)
    return ('<div class="frow">'
           f'<span class="t">{_e(zeit)}</span>'
           f'<span class="msg">{_e(entry.get("unit") or "—")}</span>'
           f'<span class="cnt">{_e(umfang)}</span>'
           f'<span class="who"{alle}>{_e(wer)}</span>'
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


def _uncommitted_row(entry: dict) -> str:
    """Eine offene Änderung — dieselben Spalten wie eine committete Zeile.

    Zwei Zellen sagen etwas anderes: die Zeit ist eine Datei-Mtime und darf
    fehlen, und statt des Commits stehen die Zustände (``modified`` ·
    ``deleted`` · ``new``). Genau diese beiden Unterschiede sind der Grund für
    einen eigenen Block statt einer einsortierten Zeile.
    """
    ts = entry.get("last_changed")
    n = int(entry.get("changes") or 0)
    umfang = f"{n} change" if n == 1 else f"{n} changes"
    chips = "".join(f'<span class="chip {_e(z)}">{_e(z)}</span> '
                    for z in (entry.get("states") or []))
    return ('<div class="frow">'
           f'<span class="t">{_e(_abs_time(ts)) if ts else "—"}</span>'
           f'<span class="msg">{_e(entry.get("unit") or "—")}</span>'
           f'<span class="cnt">{_e(umfang)}</span>'
           f'<span class="who">{_e(entry.get("author") or "—")}</span>'
           f"{chips}"
           "</div>")


def _feed_list(entries: list[dict], *, days: int | None = None,
              commit_base_url: str | None = None,
              uncommitted: list[dict] | None = None) -> str:
    """Tageweise gruppiert, jüngster Tag zuerst — dasselbe Idiom wie die
    Lauf-Liste in Job Detail, damit beide Listen gleich gelesen werden.

    **Über der ersten Tagestrennlinie steht, was noch nicht committet ist**
    (m.rau/bibi#133). Der Ort folgt aus der Sache: ungespeicherte Arbeit ist
    jünger als jeder Commit, hat aber keinen Tag, unter den sie gehörte.
    """
    uncommitted = uncommitted or []
    kopf = ""
    if uncommitted:
        kopf = ('<div class="fday">UNCOMMITTED</div>'
                '<div class="feedlist">'
                + "".join(_uncommitted_row(e) for e in uncommitted) + "</div>")
    if not entries:
        # Ein Vault, in dem gearbeitet und noch nichts committet wurde, hat sehr
        # wohl etwas zu zeigen — die Leermeldung wäre dort eine Falschaussage.
        return kopf or _feed_empty(days)
    from bibi.controller import jobs_view
    teile = [kopf]
    for tag, zeilen in jobs_view.by_day(entries, ts_key="last_changed"):
        teile.append(f'<div class="fday">{_e(tag)}</div>')
        teile.append('<div class="feedlist">' + "".join(
            _feed_row(e, commit_base_url=commit_base_url) for e in zeilen) + "</div>")
    return "".join(teile)


def _feed_reach(days: int | None) -> str:
    """Die Reichweite **am Knopf**, der sie ändert (#34).

    **Hier stand bis dahin zusätzlich der Umfang** — `128 units, 2533 changes`
    — mit der Begründung, ein LOAD MORE, das nichts mehr lädt, sei sonst von
    „da war nichts" nicht zu unterscheiden. Die Sorge war berechtigt und die
    Antwort darauf falsch: sie beantwortet die Frage *nach* dem Klick, indem
    sie *vor* jedem Klick zwei Zahlen hinstellt, die niemand braucht. Wie viele
    Einheiten im Fenster liegen, sieht man an der Liste; die Summe der
    Änderungen ist eine Zahl ohne Handlung. Befund m.rau: *„nimm die folgende
    Anzeige komplett aus dem Feed Screen raus."*

    **Die Reichweite bleibt** — sie beantwortet „warum sehe ich nicht mehr?" —
    und steht jetzt dort, wo die Frage entsteht.
    """
    if not days or days < 1:
        # Ohne Fenster keine Fensterangabe — „showing None days" stand hier
        # vorher wortwoertlich.
        return ""
    fenster = "1 day" if days == 1 else f"{days} days"
    return f"showing {_e(fenster)}"


#: Wie viele neue Einheiten ein Klick auf LOAD MORE mindestens bringen soll,
#: und wie viele ertraglose Tage er dafür höchstens durchschreitet (#34).
_LOAD_MORE_ZIEL = 10
_LOAD_MORE_GRENZE = 30


def naechstes_fenster(entries: list[dict], *, aktuell: int, now: float) -> int:
    """Auf welches Fenster LOAD MORE öffnet — eine **Menge**, kein Tag (#34).

    **Befund m.rau:** an einem ruhigen Tag kommt genau eine Zeile dazu; der
    Knopf verspricht „mehr" und liefert „einen Tag weiter". Er wandert deshalb
    so weit, bis ``_LOAD_MORE_ZIEL`` neue Einheiten zusammenkommen — und hört
    nach ``_LOAD_MORE_GRENZE`` ertraglosen Tagen auf.

    **Die Obergrenze ist kein Sicherheitsnetz, sondern die Bedingung dafür,
    dass der Knopf ehrlich bleiben kann.** Ohne sie liefe die Erweiterung bei
    einem stillen Vault ins Leere, und niemand könnte „hier ist nichts mehr"
    von „ich suche noch" unterscheiden.

    **Und nicht weiter als nötig:** wo genug liegt, wird das Fenster nur bis
    dorthin geöffnet. Ein Sprung um immer dieselben 30 Tage wäre derselbe
    Fehler in die andere Richtung — er überschüttet den ruhigen Fall und
    überspringt im lebhaften alles, was dazwischen lag.

    ``entries`` sind die Einträge eines Fensters, das mindestens
    ``aktuell + _LOAD_MORE_GRENZE`` Tage umfasst; ohne sie kann die Funktion
    nur raten, und Raten ist hier ein zweiter Netzaufruf.
    """
    grenze = aktuell + _LOAD_MORE_GRENZE
    neue = sorted(
        alter
        for e in entries
        if (ts := e.get("last_changed")) is not None
        and aktuell < (alter := (now - ts) / 86400) <= grenze
    )
    if len(neue) < _LOAD_MORE_ZIEL:
        return grenze
    # Aufgerundet: der Tag, an dem die zehnte Einheit liegt, muss **im**
    # Fenster sein — ein abgerundetes Fenster schnitte sie wieder ab.
    import math
    return min(grenze, max(aktuell + 1, math.ceil(neue[_LOAD_MORE_ZIEL - 1])))


def _feed_board_url(days: int | None) -> str:
    return "/-/ui/feed/board" + (f"?days={days}" if days is not None else "")


def feed_fragment(feed_data: dict, *, days: int | None = None,
                  now: float | None = None) -> str:
    """Der austauschbare Feed-Kern (``#feedboard``): Reichweite, Liste,
    LOAD MORE. Ein Klick erweitert das Fenster um einen Tag.

    **Haengt am Bus wie jede andere Live-Region** (#80). Bis dahin war dies die
    einzige, die es nicht tat — ``#feedstatus``, ``#jobstatuscard``,
    ``#clientsboard``, ``#jobs``, ``#tiles``, ``#runs`` und die Journal-Liste
    alle schon. Der Feed aktualisierte deshalb nur beim Seitenaufbau und beim
    Klick auf LOAD MORE; blieb ein Tab offen, waehrend jemand eine Vault-Datei
    speicherte, stand er still.

    **Die Reichweite steckt in der Refetch-URL**, und das ist kein Detail
    (dieselbe Klasse wie #44): das ausgetauschte Fragment traegt sein eigenes
    ``days`` mit, damit ein Bus-Refetch ein per LOAD MORE geoeffnetes Fenster
    nicht wieder zudreht. Wer sich dreissig Tage aufgeklappt hat, will nicht
    beim naechsten Commit wieder bei sieben stehen."""
    entries = feed_data.get("entries") or []
    commit_base_url = feed_data.get("commit_base_url")
    load_more = ""
    if days and days >= 1:
        # **Das Ziel des Knopfes rechnet der Aufrufer aus** (#34) — er hat die
        # Einträge eines größeren Fensters, aus denen sich bestimmen lässt, wie
        # weit es sich zu öffnen lohnt. Fehlt die Angabe, bleibt es beim alten
        # Verhalten (ein Tag weiter): ein Fragment, das ohne diese Zahl gar
        # keinen Knopf mehr zeigte, wäre schlechter als eines, das einen
        # bescheideneren anbietet.
        ziel = feed_data.get("next_days") or days + 1
        url = _feed_board_url(ziel)
        load_more = (
            '<div class="loadmore">'
            f'<button hx-get="{url}" hx-target="#feedboard" '
            f'hx-swap="outerHTML">LOAD MORE · {_feed_reach(days)}</button>'
            "</div>"
        )
    return (
        f'<div id="feedboard" data-bus="feed" '
        f'data-bus-refetch="{_feed_board_url(days)}"><div class="panel-card">'
        f"{_feed_list(entries, days=days, commit_base_url=commit_base_url, uncommitted=feed_data.get('uncommitted') or [])}"
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
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Feed</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('Feed', status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"{feed_status_fragment(status, git_status, host_url, now, client_rows=client_rows, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{feed_fragment(feed_data, days=days, now=now)}"
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_DIFF_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
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

    # Verbunden? Drei Faelle, nicht zwei (#70). Der Punkt kannte bis v0.7.5 nur
    # "verbunden" und "ausdruecklich getrennt" — geschrieben als „nur ein
    # ausdrueckliches ``False`` heisst getrennt":
    #
    #     ((status or {}).get("connect") or {}).get("ok") is not False
    #
    # Der dritte Fall fiel damit auf die *gruene* Seite: ein Knoten ohne
    # Scheduler-URL hat gar kein ``connect``-Dict (``app.py`` setzt es nur
    # ``if heartbeat is not None``), ``{}.get("ok")`` ist ``None``, und
    # ``None is not False`` ist ``True``. Befund m.rau, 2026-08-07: „wie kann
    # bei *disconnected* das Signal im Tab rechts **gruen** sein?"
    #
    # ``_host_card()`` unterscheidet dieselben drei Faelle laengst ueber
    # ``conn is None`` — hier fehlte nur der Gleichklang.
    conn = (status or {}).get("connect")
    if ist_host:
        # Der Host ist mit sich selbst verbunden — dort gibt es keinen
        # Heartbeat, und ein roter Punkt waere schlicht falsch. Ihm fehlt
        # ``connect`` aus demselben Grund wie dem Client ohne Scheduler,
        # deshalb steht diese Abfrage *vor* der auf ``None``.
        verbunden, ohne_gegenueber = True, False
    elif conn is None:
        verbunden, ohne_gegenueber = False, True
    else:
        verbunden, ohne_gegenueber = conn.get("ok") is not False, False

    if ohne_gegenueber:
        # „disconnected" waere hier irrefuehrend: es gab nie eine Verbindung,
        # die abreissen konnte. Wer das liest, sucht den Fehler sonst beim
        # Scheduler statt in der eigenen Konfiguration.
        dot_cls, dot_titel = "bad", "no scheduler configured — nothing to connect to"
    elif not verbunden:
        # Getrennt schlaegt Maintenance: wer nicht verbunden ist, weiss ueber
        # den Modus des Hosts ohnehin nichts Aktuelles.
        dot_cls, dot_titel = "bad", "disconnected"
    elif maint:
        dot_cls, dot_titel = "warn", "maintenance active — nothing is dispatched"
    else:
        dot_cls, dot_titel = "ok", "connected"

    # Wer den Schalter erreicht (#69). Die Bedingung fragte bis v0.7.5 „bin ich
    # der Scheduler" — und sperrte damit genau die Knoten, die eine Oberflaeche
    # haben. Seit dem 2026-08-06 traegt das Profil ``scheduler`` ausdruecklich
    # **kein** ``controller`` mehr (``roles.py``: „der Scheduler ist Backend");
    # es gab danach keinen Knoten, auf dem beides zugleich wahr war. Maintenance
    # war eine vollstaendig gebaute Funktion, die niemand ausloesen konnte.
    # Befund m.rau, 2026-08-07: „es **muss** vom Client aus schaltbar sein."
    #
    # Die richtige Frage ist die, die ``_ops_ziel()`` im Controller laengst
    # beantwortet: **habe ich einen Scheduler** — konfiguriert, oder als der ich
    # selbst laufe. Genau die Frage steht schon oben, nur unter anderem Namen:
    # ``ohne_gegenueber``. Ein Knoten ohne Scheduler behaelt die Sperre, und
    # dort ist sie richtig — es gibt nichts zu schalten.
    #
    # Der Bootstrap-Fall bleibt damit heil: ``init --profile scheduler
    # --with-ui`` haengt einem Scheduler doch einen ``controller`` an, und
    # ``ist_host`` traegt ihn ueber den lokalen Zweig von ``_ops_ziel()``.
    if not ohne_gegenueber:
        mcls = "toggle warn" if maint else "toggle"
        mtitle = "maintenance: on" if maint else "maintenance: off"
        maint_btn = f'<button id="maint" class="{mcls}" title="{mtitle}">◐</button>'
    else:
        maint_btn = ('<button id="maint" class="toggle" disabled '
                    'title="maintenance: no scheduler to switch">◐</button>')
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
      // Ueber den Controller, nicht relativ an diesen Knoten (m.rau/bibi#142):
      // relativ traf der Aufruf den eigenen Client, wo es die Route gar nicht
      // gibt. Und der Haken haengt jetzt an der Antwort statt an nichts — ein
      // Knopf, der Erfolg behauptet, den es nicht gab, laedt nicht zum
      // Nachsehen ein und bleibt deshalb lange unbemerkt.
      let ok = false, titel = '';
      try {
        const r = await fetch('/-/ui/ops/rescan', {method:'POST'});
        ok = r.ok;
        const d = await r.json().catch(() => ({}));
        const a = d.antwort || {};
        titel = ok
          ? ('rescanned: ' + (a.inserted ?? 0) + ' new, ' + (a.updated ?? 0)
             + ' updated, ' + (a.removed ?? 0) + ' removed')
          : ('rescan failed: ' + (d.error || r.status));
      } catch(e) { titel = 'rescan failed: ' + e; }
      rescan.textContent = ok ? '✓' : '✕';
      rescan.title = titel;
      setTimeout(() => {
        rescan.textContent = idleIcon;
        rescan.title = 'rescan the vault';
        rescan.disabled = false;
      }, ok ? 1200 : 4000);   // ein Fehler darf laenger stehen bleiben
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
      // Ebenfalls ueber den Controller (m.rau/bibi#142). Dieser Handle ist der
      // wirksamere der beiden Faelle: relativ schaltete er den *lokalen*
      // Maintenance-Modus eines Clients, der gar keine Jobs verteilt — er tat
      // also etwas, nur am falschen Knoten.
      const r = await fetch('/-/ui/ops/maintenance', {method: on ? 'DELETE' : 'POST'});
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
    # `line` **oder** `text`: die Formatter-Ausgabe nennt das Feld `line`, die
    # Host-Antwort aus `/-/journal/{id}/output` `text`. Die abgeloeste
    # Roh-Ausgabe der v5-Route las beide (`e.get("text") or e.get("line")`) —
    # beim Anschluss an den Formatter (m.rau/bibi#99) waere die zweite Form
    # sonst still auf leere Zeilen gefallen.
    line = _e(_strip_ansi(e.get("line") or e.get("text") or ""))
    # Ein Ereignis ohne Inhalt bekommt keine Uhrzeit (m.rau/bibi#107). Der
    # Formatter liefert Events mit leerem `line`; im Rohtext war eine leere
    # Zeile eine leere Zeile, mit Präfix wird daraus eine, die aussieht, als
    # hätte zu diesem Zeitpunkt etwas stattgefunden. Die Leerzeile selbst
    # bleibt — sie trennt Absätze und ist Inhalt; was verschwindet, ist die
    # Behauptung eines Ereignisses.
    if not line.strip():
        return "<span></span>"
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
        return '<div class="out-empty">— no output —</div>'
    return f'<pre class="term">{_falte_thinking(_merge_deltas(events))}</pre>'


def _falte_thinking(events: list[dict]) -> str:
    """Zusammenhängendes ``thinking`` in ein ``<details>``, alles andere flach.

    Vorgabe m.rau zu ``#104``s Nachbarn ``#99``: *„Streaming, zurück gesetzt,
    **etwas** im Hintergrund · einklappbar"*. Die ersten beiden Punkte trägt
    ``.term .thinking`` (gedimmt, kursiv) und tat es schon — sie kamen nur nie
    an, weil die v5-Route den Formatter umging. Der dritte braucht diese
    Klammer.

    **Zusammenhängend, nicht je Zeile:** ein Denkabschnitt ist ein Gedanke und
    keine Sammlung von Zeilen; je Zeile ein Aufklapper wäre unbedienbar. Ein
    Block endet, sobald etwas anderes kommt — echte Ausgabe, eine Phase, ein
    Fehler.

    **Aufgeklappt (``open``), nicht zugeklappt.** Der Live-Fall und der
    Archiv-Fall sind verschieden: während ein Lauf läuft, ist ``thinking`` oft
    das Einzige, was sich bewegt, und eine zugeklappte Box zeigte dann nichts.
    Für den Archiv-Fall wäre zu ohne weiteres besser — dafür müsste diese
    Funktion aber wissen, ob der Lauf noch läuft, und das weiß sie nicht. Lieber
    sichtbar und einklappbar als versteckt und vergessen; ``<details>`` merkt
    sich den Zustand ohnehin nicht über einen Swap hinweg.
    """
    teile: list[str] = []
    block: list[dict] = []

    def _schliessen() -> None:
        if not block:
            return
        # Gezählt werden **Zeilen, nicht Events** (m.rau/bibi#107). Live stand
        # dort `thinking (1 line)` über fünfzehn sichtbaren Zeilen: die
        # Token-Deltas eines Denkabschnitts sind nach `_merge_deltas()` **ein**
        # Event, dessen `line` die Umbrüche trägt. Die Angabe war damit für
        # jeden zusammenhängenden Abschnitt dieselbe und widersprach sichtbar
        # dem, was darunter stand — genau das darf eine Zusammenfassung nicht.
        n = sum(1 + (e.get("line") or e.get("text") or "").count("\n") for e in block)
        teile.append(
            f'<details class="think" open><summary>thinking '
            f'({n} {"line" if n == 1 else "lines"})</summary>'
            + "\n".join(_event_line(e) for e in block) + "</details>")
        block.clear()

    for e in events:
        if e.get("s") == "thinking":
            block.append(e)
            continue
        _schliessen()
        teile.append(_event_line(e))
    _schliessen()
    return "\n".join(teile)


# ── Live-Output (SSE; Frontend-Plan §C.5) ────────────────────────────────────


def live_output_box(job_id: str, events: list[dict] | None = None,
                    *, kind: str = "job", stream_url: str | None = None) -> str:
    """Die Live-Output-Box eines laufenden Jobs. Server-seitig mit dem
    aktuellen (bereits formatierten) Output geseedet (no-JS-Paint); ab
    ``data-from`` hängen die ``append``-Events des globalen Event-Stroms an
    (``_EVENTS_JS``, PLAN-36 Stufe 36.2 — vorher eine eigene EventSource pro
    Box gegen ``/-/job/{id}/output/stream``, die es auf Client-Knoten gar
    nicht gab, s. FE-Live-Update-Briefing Befund 1).

    **Für einen Lauf auf dem Scheduler kommt genau diese EventSource zurück**
    (#78, ``stream_url``), und der damalige Schluss ist dabei zu berichtigen:
    die Route fehlte nicht, sie war auf den *eigenen* Knoten gerichtet. Der
    globale Bus kann diesen Fall nicht bedienen — seine ``append``-Ereignisse
    entstehen, indem der Collector eine lokale Datei tailt, und die gibt es
    hier nicht. Der Unterschied zu damals ist ``event: done``: seither lässt
    sich ein beabsichtigtes Ende von einem Abriss unterscheiden.

    Offsets zählen in
    denselben formatierten Einheiten wie der Seed — kein Offset-Mismatch.
    Kein ``hx-preserve`` mehr: die Box wird nur noch bei echten Zustands-
    Refetches ersetzt (frischer Seed + neuer data-from = Resync-Heilung),
    nicht mehr pro Poll-Tick — das Attribut schützte zuletzt nichts und
    blockierte auf Client-Seiten den einzigen Update-Weg."""
    evs = events or []
    seed = "\n".join(_event_line(e) for e in _merge_deltas(evs))
    jid = _e(job_id)
    # ``data-stream`` (#78): dieser Lauf laeuft beim **Scheduler**, seine
    # ``output.jsonl`` liegt hier nicht. Der globale Bus kann ihn deshalb nicht
    # speisen — seine ``append``-Ereignisse entstehen aus einer lokalen Datei.
    # Steht das Attribut, oeffnet ``_EVENTS_JS`` fuer diese Box eine eigene
    # EventSource auf den Durchreicher. Rein additiv: ohne das Attribut bleibt
    # alles beim globalen Strom.
    strom = f' data-stream="{_e(stream_url)}"' if stream_url else ""
    return (f'<pre class="term liveterm" id="livebox-{jid}" data-job="{jid}"'
            f'{strom} data-from="{len(evs)}">{seed}</pre>')


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
      attachRemote(box);
    });
  }

  // #78: eine Box mit data-stream waechst NICHT ueber den globalen Bus — ihr
  // Lauf liegt beim Scheduler, seine output.jsonl gibt es hier nicht. Sie
  // bekommt deshalb eine eigene EventSource auf den Durchreicher dieses
  // Knotens, so wie es sie vor PLAN-36 Stufe 36.2 fuer lokale Laeufe gab.
  //
  // Kein onerror-Schliessen (2026-07-20-Lektion: Abriss und Serverende sehen
  // clientseitig gleich aus) -- geschlossen wird auf 'done', das der
  // Durchreicher vom Scheduler mitbringt. Danach ist der Lauf terminal, und
  // ein terminaler Lauf braucht keinen Strom.
  function attachRemote(box){
    const url = box.getAttribute('data-stream');
    if (!url || box._bibiRemote) return;
    const src = new EventSource(url + (url.indexOf('?') < 0 ? '?' : '&')
                                + 'from=' + (box.getAttribute('data-from') || 0));
    box._bibiRemote = src;
    src.addEventListener('done', () => { src.close(); box._bibiRemote = null; });
    src.onmessage = (e) => {
      // Offset-Dedup wie beim globalen Strom: ein Refetch traegt frischen Seed
      // und neues data-from, nachlaufende Zeilen mit kleinerem Offset sind
      // dadurch harmlos.
      const off = parseInt(e.lastEventId || '0', 10);
      const from = parseInt(box.getAttribute('data-from') || '0', 10);
      if (off && off <= from) return;
      try { appendLine(box, JSON.parse(e.data)); } catch (err) { return; }
      if (off) box.setAttribute('data-from', String(off));
    };
  }
  document.addEventListener('DOMContentLoaded', initBoxes);
  document.body.addEventListener('htmx:afterSettle', initBoxes);
  // #124: eine Box, die per `innerHTML` entsteht, faellt zwischen beide
  // Momente — sie waere da, und der Strom faende sie nicht. Wer eine
  // einsetzt, sagt es hier an. **Ausdruecklich statt ueber die Reihenfolge
  // zweier afterSettle-Zuhoerer**, die an der Skript-Reihenfolge der Seite
  // haengt und niemandem auffaellt, wenn sie kippt.
  window.__bibiInitBoxes = initBoxes;

  // #82: eine Box, die htmx gleich entfernt, nimmt ihren Strom mit.
  //
  // `attachRemote()` schuetzt gegen doppeltes Anhaengen an DIESELBE Box —
  // nicht gegen den Fall, der im Betrieb eintritt: ein Bus-Refetch *ersetzt*
  // die Box (outerHTML-Swap). Die neue ist ein neues Element ohne
  // `_bibiRemote` und oeffnet eine eigene EventSource; die alte war aus dem
  // DOM, ihre Verbindung lief aber weiter. Jeder Refetch hinterliess eine
  // Leiche, und nach `_MAX_OUTPUT_PROXIES` antwortete der Durchreicher 429 —
  // die Box wuchs nicht mehr mit. Ein laufender Job erzeugt Statuswechsel,
  // jeder Wechsel einen Refetch: es traf genau den Fall, fuer den #78 gebaut
  // wurde. (Befund m.rau bei der Abnahme von v0.7.7.)
  //
  // `beforeCleanupElement` statt `beforeSwap`: htmx feuert es fuer **jedes**
  // Element, das es aus dem DOM nimmt — auch fuer Kinder des getauschten
  // Knotens, und die Box ist eins. `beforeSwap` traegt nur das Ziel selbst.
  document.body.addEventListener('htmx:beforeCleanupElement', (ev) => {
    const box = ev.target;
    if (!box || !box._bibiRemote) return;
    try { box._bibiRemote.close(); } catch (e) { /* schon zu */ }
    box._bibiRemote = null;
  });

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
#: — die Region bekommt durch den Bus erstmals lebende
#: Output-Boxen, s. FE-Live-Update-Briefing Befund 1).
_DIFF_JS = """
(function(){
  // Der Zell-Diff (#67 Schritt 1) — `watch -d` fuer die Jobs-Tabelle.
  //
  // **Der Vergleich lebt im Browser, weil nur er weiss, was dieser Betrachter
  // zuletzt gesehen hat.** Der Server kennt den neuen Stand, nicht den alten
  // dieses einen Fensters; zwei Tabs mit verschiedenem Scrollstand und
  // verschiedenem Refetch-Zeitpunkt haetten sonst dieselbe Markierung.
  //
  // Der Schluessel ist `data-row` plus Zellindex *innerhalb der Zeile*, nicht
  // die Position in der Tabelle: Sortierung und Filter verschieben Zeilen, und
  // ein positionsbasierter Vergleich blitzte dann die halbe Tabelle.
  let vorher = null;

  function schnappschuss(wurzel){
    const m = new Map();
    wurzel.querySelectorAll('tr[data-row]').forEach(function(tr){
      const key = tr.getAttribute('data-row');
      let i = 0;
      tr.querySelectorAll('td').forEach(function(td){
        const n = i++;
        if (td.hasAttribute('data-nodiff')) return;
        m.set(key + '\u0000' + n, td.textContent.trim());
      });
    });
    return m;
  }

  document.body.addEventListener('htmx:beforeSwap', function(ev){
    const t = ev.detail && ev.detail.target;
    vorher = (t && t.querySelector && t.querySelector('tr[data-row]'))
      ? schnappschuss(t) : null;
  });

  document.body.addEventListener('htmx:afterSettle', function(ev){
    if (!vorher) return;
    const t = ev.detail && ev.detail.target;
    // Bei einem outerHTML-Swap ist das alte Ziel nicht mehr im Dokument — dann
    // ist die neue Tabelle ueber `document` zu finden, nicht ueber die Leiche.
    const wurzel = (t && t.isConnected) ? t : document;
    const jetzt = schnappschuss(wurzel);
    // Erst raeumen: unter `prefers-reduced-motion` laeuft keine Animation, die
    // Markierung bliebe sonst fuer immer stehen. Und ein Neustart derselben
    // Animation braucht ohnehin ein Entfernen dazwischen.
    wurzel.querySelectorAll('td.cellflash').forEach(function(td){
      td.classList.remove('cellflash');
    });
    wurzel.querySelectorAll('tr[data-row]').forEach(function(tr){
      const key = tr.getAttribute('data-row');
      let i = 0;
      tr.querySelectorAll('td').forEach(function(td){
        const n = i++;
        if (td.hasAttribute('data-nodiff')) return;
        const k = key + '\u0000' + n;
        // **Eine neue Zeile ist keine Aenderung, sondern ein Zugang.** Ohne
        // diese Zeile blitzte beim ersten Refetch die ganze Tabelle auf.
        if (!vorher.has(k)) return;
        if (vorher.get(k) === jetzt.get(k)) return;
        void td.offsetWidth;   // Reflow erzwingen, sonst greift der Neustart nicht
        td.classList.add('cellflash');
      });
    });
    vorher = null;
  });
})();
"""


_SCROLL_JS = """
(function(){
  const isLiveRegion = (t) => t && t.id === 'live';
  const region = () => document.getElementById('live');
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
  const isLiveRegion = (t) => t && t.id === 'live';
  const region = () => document.getElementById('live');
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
    """Die Laufzeit einer Journal-Zeile — tickend, solange der Lauf laeuft.

    `exec_runtime` kommt fertig gerechnet herein und weiss nicht mehr, ob es
    ein Ergebnis oder ein Zwischenstand ist. **Diese Zeile weiss es**, an
    `finished_at`, und nur deshalb steht die Entscheidung hier und nicht im
    Formatierer."""
    laeuft = r.get("started_at") is not None and r.get("finished_at") is None
    return _human_duration(r.get("exec_runtime"),
                           seit=r.get("started_at") if laeuft else None)


#: Läufe pro Infinite-Scroll-Nachladung (User-Entscheidung, Job Lifecycle-Diskussion).
_JOURNAL_PAGE_SIZE = 50


#: Basis-Pfad der Journal-Bausteine. Bis `#100` war er ein Parameter, weil
#: die abgelöste Client-Detailseite dieselben Bausteine gegen ihre eigenen
#: Routen verdrahtete. Mit ihr ist der zweite Wert entfallen — und ein
#: Parameter, der überall denselben Wert trägt, ist keine Wahl mehr, sondern
#: eine Stelle, an der jemand eine vermutet.
_JOURNAL_BASE = "/-/ui/schedule"


def _journal_sentinel_row(slug: str, offset: int) -> str:
    """Trigger-Zeile für Infinite Scroll: sichtbar (``revealed``) lädt sie die
    nächste Batch nach und ersetzt sich selbst (outerHTML) — mit neuer Batch +
    ggf. frischer Sentinel-Zeile, oder ganz ohne, wenn das Ende erreicht ist."""
    s = _e(slug)
    return (
        f'<tr id="journal-more" hx-get="{_JOURNAL_BASE}/{s}/runs?offset={offset}" '
        f'hx-trigger="revealed" hx-swap="outerHTML">'
        f'<td colspan="7" class="muted">loading more runs…</td></tr>'
    )


def _live_placeholder_row(job: dict | None, now: float) -> str:
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
    Lauf zeigt. Der Rückverweis zeigt auf die Live-Region ``#live`` — ein
    reiner In-Page-Sprung, keine neue Route nötig. Bis `#100` war er wählbar,
    weil die abgelöste Client-Detailseite ``#jobsdetail-live`` hieß; mit ihr
    ist die zweite Region entfallen und mit ihr die Wahl.

    ``status or "running"`` (nicht bloß ``status``): das Client-``live``-Dict
    (``worker.local_run_live()``) trug historisch nicht immer ein explizites
    ``status``-Feld. Ohne diesen
    Default hier könnte diese Zeile ausbleiben, während die Kachel oben
    trotzdem "running" zeigt — der Host-``job``-Dict hat ``status`` dagegen
    immer gesetzt (NOT NULL-Spalte), dort ist der Default ein No-op."""
    if not job:
        return ""
    status = job.get("status") or "running"
    if status not in ("starting", "running", "awaiting", "deferred"):
        return ""
    st = _e(status)
    begonnen = job.get("started_at")
    t_abs = _abs_datetime(begonnen, now)
    # **RUNTIME statt eines vierten Gedankenstrichs** (#123). Hier standen vier,
    # und der dritte war die Laufzeit — sie war nie da, weder stehend noch
    # tickend. Seit `v0.7.16` tickt die Dauer auf der Kachel darueber, und der
    # Unterschied machte die Luecke sichtbar; der Docstring dieser Funktion
    # verlangt ohnehin, dass Kachel und Zeile nie auseinanderlaufen.
    #
    # Die drei verbleibenden Striche bleiben: REASON, EXIT und COMMIT sind zur
    # Laufzeit wirklich unbekannt, und sie zu fuellen waere eine Behauptung.
    laufzeit = ("—" if begonnen is None
                else _human_duration(now - begonnen, seit=begonnen))
    return (
        "<tr>"
        f"<td>{t_abs}</td>"
        f'<td class="st {st}">{st}</td>'
        f"<td>—</td><td>—</td><td>{laufzeit}</td><td>—</td>"
        f'<td><a class="back" href="#live">↑ live</a></td>'
        "</tr>"
    )


def _journal_table_html(runs: list[dict], slug: str, now: float, *, offset: int = 0,
                        live_row: str = "") -> str:
    if not runs and not live_row:
        return '<p class="out-empty">— no runs yet —</p>'
    rows = live_row + _run_rows(runs, slug, now)
    if len(runs) == _JOURNAL_PAGE_SIZE:
        rows += _journal_sentinel_row(slug, offset + _JOURNAL_PAGE_SIZE)
    return (
        '<table><thead><tr><th>TIME</th><th>STATUS</th><th>REASON</th>'
        '<th>EXIT</th><th>RUNTIME</th><th>COMMIT</th><th></th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def journal_fragment(runs: list[dict], slug: str, now: float, *, oob: bool = False,
                     live_job: dict | None = None) -> str:
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
    live_row = _live_placeholder_row(live_job, now)
    s = _e(slug)
    return (
        f'<div id="journal"{oob_attr} class="panel-card" '
        f'data-bus="journal:{s}" data-bus-refetch="{_JOURNAL_BASE}/{s}/journal">'
        "<h2>Journal</h2>"
        f"{_journal_table_html(runs, slug, now, live_row=live_row)}"
        "</div>"
    )


def journal_runs_fragment(runs: list[dict], slug: str, now: float, offset: int,
                          ) -> str:
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
            f'<button hx-delete="{_JOURNAL_BASE}/{s}/run/{rid}" hx-target="#journal" '
            f'hx-swap="outerHTML" hx-confirm="Delete this run record?">Delete</button></td>'
            "</tr>"
        )
    return "".join(rows)


def _live_panel(job: dict | None, now: float, live_output: dict | None = None,
               slug: str = "", *, public_host: str = "localhost",
               raw_stream_base: str | None = "/-/job",
               output_stream_url: str | None = None) -> str:
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
        # `output_stream_url` nur hier, im running-Zweig (#78): ein terminaler
        # Lauf braucht keinen Strom, dort bleibt der einmalige Abruf richtig.
        out = (f'<div class="liveout">{raw_link}'
               + live_output_box(jid, events, kind=(live_output or {}).get("kind", "job"),
                                 stream_url=output_stream_url)
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
    # Dieselbe Zusage wie in `live_fragment()` darueber: kein Link ohne
    # erreichbaren Knoten. `public_host` kann hier `None` sein, und das ist
    # die Aussage „ich weiss nicht, wo die App laeuft" — sie gehoert nicht in
    # ein `href`.
    if not app_port:
        app_link = ""
    elif public_host:
        app_link = (f' <a href="http://{public_host}:{app_port}/" target="_blank" '
                    f'style="font-size:.82rem">Open app →</a>')
    else:
        # **Der Port bleibt, die Adresse geht** — dieselbe Aufteilung, die
        # `_jobs_type_cell(link=False)` seit m.rau/bibi#104 fuehrt. Ohne diesen
        # Zweig haette der #117-Fix eine Auskunft mitgenommen, die er gar nicht
        # meint: dass dieser Job ueberhaupt eine App ist.
        app_link = f' <span style="font-size:.82rem">app :{app_port}</span>' 
    # PLAN-22 Befund 1: pending hat weder started_at noch Output — "aktiver
    # Lauf" suggerierte fälschlich, dass gerade schon etwas läuft.
    if is_terminal:
        label = "last run"
    elif job.get("status") == "pending":
        label = "waiting"
    elif job.get("status") == "deferred":
        label = "waiting for retry"
    else:
        label = "active run"
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
        link = '<span class="muted">app_url unavailable</span>'
    return f'<div class="hitl"><div class="hitl-label">Input required</div>{link}</div>'


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
                ) -> str:
    """Die Aktions-Leiste: ``/-/ui/schedule/{slug}/{verb}`` → ``#live``.

    Ziel und Basis waren bis `#100` Parameter (PLAN-29 Befund 3+5,
    Vereinheitlichung statt Parallel-Renderer) — die zweite Gegenstelle war
    die Client-Detailseite und ist mit ihr entfallen."""
    if not job or not job.get("id"):
        return ""
    s = _e(slug)
    status = job.get("status", "")
    enabled = _VERBS_FOR_STATUS.get(status, ())
    btns = "".join(
        f'<button hx-post="{_JOURNAL_BASE}/{s}/{v}" hx-target="#live" '
        f'hx-swap="outerHTML" hx-disabled-elt="this"'
        f'{"" if v in enabled else " disabled"}>{v.upper()}{_BTN_SPINNER}</button> '
        for v in _VERBS
    )
    if (exec_mode or "host").strip().lower() == "container":
        btns += (f'<button hx-post="{_JOURNAL_BASE}/{s}/rebuild" hx-target="#live" '
                 f'hx-swap="outerHTML" hx-disabled-elt="this" '
                 f'title="Discards the per-job image; the next run starts '
                 f'from the default image">REBUILD{_BTN_SPINNER}</button> ')
    return f'<div class="actions">{btns}</div>'


def live_fragment(
    schedule: dict | None, runs: list[dict], job: dict | None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, public_host: str = "localhost",
    output_stream_url: str | None = None,
) -> str:
    """Der austauschbare Live-Kern (``#live``): Meta + Aktions-Leiste
    (START/RESET/KILL) + Live-Block (aktiver Lauf, Output default expanded).
    Bus-getrieben (``live:``-Target) — bleibt getrennt vom Journal
    (``#journal``), das sonst durch nachgeladene Infinite-Scroll-Zeilen bei
    jedem Swap wieder plattgemacht würde (Journal Infinite Scroll, §6)."""
    now = time.time() if now is None else now
    s = schedule or {}
    name = _e(s.get("slug") or slug)
    # **Die Adresse gehoert dem Knoten, der die App faehrt** — nicht dem, der
    # die Seite rendert. `public_host` ist der des BETRACHTERS, und damit ist
    # das hier wortgleich der Fehler aus m.rau/bibi#145, an einer Stelle, die
    # #145 nicht angefasst hat: gefunden beim Absuchen nach weiteren
    # Link-Erzeugern zu #117, live belegt (das Mac-FE verlinkte
    # `localhost:9110`, waehrend die App auf sarasate lief).
    #
    # Ohne bekannten ausfuehrenden Knoten kein Link, nur der Port — genau der
    # `link=False`-Pfad, den `_jobs_type_cell()` seit #104 dafuer schon fuehrt.
    app_host = erreichbarer_host((job or {}).get("host"), s.get("host"))
    kind = _jobs_type_cell(s, app_host or "", link=bool(app_host))
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
    meta = (f"Type <b>{kind}</b> · Trigger <code>{trigger}</code> · "
            f"last run <b>{last_run}</b> · next run {nxt}")
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
        f"{_live_panel(job, now, live_output, slug=slug, public_host=app_host, output_stream_url=output_stream_url)}"
        "</div>"
    )


def schedule_detail_inner(
    schedule: dict | None, runs: list[dict], job: dict | None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, public_host: str = "localhost",
    output_stream_url: str | None = None,
) -> str:
    """Voller Detail-Kern für den initialen Seitenaufbau: ``#live`` (bus-
    getrieben) + ``#journal`` (einmalig, wächst nur per Infinite Scroll)."""
    now = time.time() if now is None else now
    return (
        live_fragment(schedule, runs, job, slug, now, live_output=live_output,
                      public_host=public_host, output_stream_url=output_stream_url)
        + journal_fragment(runs, slug, now, live_job=job)
    )


def schedule_detail_page(
    schedule: dict | None, runs: list[dict], job: dict | None = None,
    slug: str = "", now: float | None = None,
    *, live_output: dict | None = None, daemon_status: dict | None = None,
    public_host: str = "localhost", output_stream_url: str | None = None,
) -> str:
    """Schedule-zentrierte Detail-Sicht (§3 Ebene 3) als volle Seite. Ops-Handles
    (RESCAN/MAINT) seit User-Feedback 2026-07-03 auch hier — außerhalb von
    ``#live``/``#journal``, damit ein ``#live``-Swap sie nicht neu rendert.
    Kein "← zurück"-Link mehr (Bibi4-Iteration, User-Fund) — die Nav-Leiste
    trägt schon einen Jobs-Tab dorthin zurück, der Link war redundant."""
    name = _e((schedule or {}).get("slug") or slug)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi · {name}</title>"
        f'<script src="{_HTMX}" crossorigin="anonymous"></script>'
        f"<style>{_CSS}</style></head><body>"
        f"{_header('', daemon_status)}"
        f'<div style="display:flex;gap:.75rem;align-items:baseline">'
        f'<a class="back" href="/-/ui/schedule/{_e(name)}/attrs">Attribute →</a>'
        f'</div>'
        f"{schedule_detail_inner(schedule, runs, job, slug, now, live_output=live_output, public_host=public_host, output_stream_url=output_stream_url)}"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_DIFF_JS}</script>"
        f"<script>{_SCROLL_JS}</script>"
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
        dauer = f" (runtime {round(rt)} s)" if rt is not None else ""
        rows.append(f'<tr><td><b>Run</b></td><td>{_e(s_str)} → {_e(f_str)}{dauer}</td></tr>')
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
                if slug else '<a class="back" href="/-/">← back</a>')
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
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
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_JS}</script>"
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
        '<html lang="en"><head><meta charset="utf-8">'
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
        f'<a class="back" href="/-/">← back</a>'
        f'<a class="back" href="/-/ui/schedule/{name}">← Detail</a>'
        f'</div>'
        f'<h1><span class="st {st}">{name}</span> · Attribute</h1>'
        f"{config_html}"
        f"{runtime_html}"
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


#: Zustände, in denen ein Lauf im Slot **in Arbeit** ist: er hat begonnen und
#: ist nicht terminal. `failed` und `deferred` gehören ausdrücklich nicht dazu —
#: dort wartet der Slot auf einen Termin, und genau den soll `NEXT` dann nennen.
_IN_ARBEIT = frozenset({"starting", "running", "awaiting"})


def _laufender_start(s: dict) -> float | None:
    """Der Startzeitpunkt des Laufs, der gerade im Slot steht (#136).

    ``None`` heißt „hier läuft nichts" — dann sagen ``LAST`` und ``NEXT``
    weiterhin, was sie immer gesagt haben.

    **Ein Zustand allein macht keinen Lauf.** ``RESET`` räumt ``started_at``
    ausdrücklich; ohne die zweite Prüfung zählte danach eine Zelle die Laufzeit
    eines Laufs hoch, den es nie gab — derselbe Fall, den ``slot_run()`` an
    seiner Stelle schon abfängt.
    """
    if (s.get("row_status") or s.get("status")) not in _IN_ARBEIT:
        return None
    start = s.get("started_at")
    return start if isinstance(start, (int, float)) else None


def _next_zelle(row, s: dict, now: float, ohne_zukunft: bool) -> str:
    """Die NEXT-Zelle — mit Fälligkeitskennzeichen, wenn der Termin hinter uns
    liegt und der Job noch wartet (#11).

    **Ergänzen, nicht ersetzen.** ``_until()`` gibt für genau diesen Fall
    ``asap`` zurück und wird in der Jobs-Tabelle seit dem v5-Umbau nicht mehr
    gerufen; ``asap`` **statt** des Zeitpunkts wirft aber weg, *wie lange*
    etwas überfällig ist — bei zwei Sekunden egal, bei 38 Tagen nicht. Vor
    allem wäre es wieder eine Relativangabe und verlöre die Eigenschaft, um
    derentwillen die Entscheidung vom 2026-08-03 gegen Relativzeiten fiel:
    **ein absoluter Zeitpunkt bleibt nach einem Screenshot wahr.**

    **Nur für wartende Jobs.** Ein terminaler Job mit stehengebliebenem Termin
    ist `#97` — ein Datenfehler, der nicht dadurch heilt, dass man ihn hübscher
    rendert. Ihn als fällig zu markieren hieße zu behaupten, er komme noch.
    """
    if ohne_zukunft:
        return "—"
    # **Solange ein Lauf läuft, ist die Laufzeit die Zukunft** (#136). Ein
    # Termin, der neben einem laufenden Lauf steht, beantwortet eine Frage, die
    # in diesem Moment niemand stellt — und er ist zugleich die eine Angabe
    # dieser Zeile, die ein Rescan mitten im Lauf neu berechnet.
    #
    # Der Server liefert den Anker, der Browser zählt (`_DURATION_JS`). **Was
    # hier tickt, muss auch refetchen** — die Zeile hängt am Sammel-Target
    # `jobs`, sonst entstünde genau die Anzeige aus #131: eine, die sich
    # bewegt und dabei den falschen Zustand zeigt.
    laeuft = _laufender_start(s)
    if laeuft is not None:
        return _dauer_span(_human_duration(now - laeuft), "since", laeuft)
    ts = s.get("next_fire_at")
    zeit = _uhrzeit(ts, now)
    if ts is None or ts >= now:
        return zeit
    gruppe = status_gruppe(s.get("row_status") or s.get("status"),
                           next_fire_at=ts)
    if gruppe != "waiting":
        return zeit
    return f'{zeit} <span class="due" title="overdue — the scheduler will pick it up on its next tick">due</span>'


def _jobs_zeile(row, now: float, *, public_host: str = "localhost") -> str:
    """Eine Zeile: ein Slug, zwei Zustandsblöcke."""
    from bibi.schedule.models import job_uid

    beziehung = ""
    if row.relation:
        # `duplicate` ist das einzige rote Label: es meldet ein Problem im
        # Vault, kein Verhältnis zwischen zwei Speichern, und verlangt eine
        # Umbenennung statt eines Syncs.
        # **Chip statt Klammertext** (#31, Vorschlag 1 der Design-Studie).
        # Befund m.rau: *„Aktuell ist die Visualisierung in `(...)`. Das folgt
        # dem Terminal-Ansatz. Aber gerade hier wollen wir Aufmerksamkeit
        # lenken."* Die Klammern waren ein Wireframe-Zeichen für „hier steht
        # eine Nebenangabe" und wurden wörtlich gebaut; im Browser trägt die
        # Form das schon.
        #
        # **Die Abstufung ist der Inhalt, nicht die Form.** `new`, `modified`,
        # `deleted` und `dropped` beschreiben ein Verhältnis zwischen zwei
        # Speichern und verlangen Kenntnis — vier ruhige Chips in der
        # Beschriftungsfarbe. `duplicate` meldet einen Fehler im Vault und
        # verlangt Handeln; es ist als einziges rot. **Sind alle fünf gleich
        # laut, ist keiner mehr laut.**
        klasse = "bad" if row.relation == "duplicate" else "leise"
        titel = f' title="{" · ".join(row.paths)}"' if row.relation == "duplicate" else ""
        beziehung = f' <span class="chip {klasse}"{titel}>{row.relation}</span>'

    s, l = row.scheduler, row.local
    # `NEXT` und `24H` sind für eine Journal-Zeile ohne Aussage (#130).
    #
    # Beide Spalten waren dort schon immer so gefüllt: `next_fire_at` ist der
    # zuletzt berechnete Termin eines Jobs, den der Scheduler auf `active=0`
    # führt — er liegt zwangsläufig hinter uns —, und die Quote rechnet gegen
    # eine Erwartung, die für einen abgelegten Job nicht mehr gilt. `0/288+0 0%`
    # liest sich als schlechtester möglicher Wert; gemeint ist „keiner".
    #
    # **Ein Umzug ändert nichts an den Daten und alles an ihrem Gewicht.** Im
    # dritten Band standen diese Zeilen zwischen den aktiven und fielen nicht
    # auf. Seit #38 führt ein eigener Screen ausschliesslich solche Zeilen —
    # dann behauptet jede Zeile dasselbe Falsche, und es ist nicht mehr die
    # Ausnahme, sondern der Screen.
    #
    # **Am Segment der Zeile, nicht am Screen**, obwohl heute nur der
    # Journal-Screen solche Zeilen führt: „dieser Job hat keine Zukunft mehr"
    # ist eine Aussage über den Job, nicht über den Ort, an dem er gerade
    # steht. Die Spalten bleiben dabei stehen — sie wegzulassen hiesse einen
    # zweiten Tabellenkopf zu führen, und das ist für die Filterzeile aus #31,
    # die an genau diesen Köpfen hängen soll, der falsche Weg.
    ohne_zukunft = row.segment is Segment.JOURNAL
    return (
        # `data-row` ist der Wiedererkennungsschlüssel des Zell-Diffs (#67).
        # **Die Position taugt dafür nicht** — Sortierung und Filter verschieben
        # sie, und dann vergliche der Diff die Zelle eines Jobs mit der eines
        # anderen und blitzte die halbe Tabelle. Der Job-uid ist über beide
        # Screens und beide Knoten derselbe.
        f'<tr data-row="{job_uid(row.slug)}">'
        f'<td class="slug"><a href="/-/jobs/{job_uid(row.slug)}" title="{row.slug}">'
        f"{_slug_kurz(row.slug)}</a>{beziehung}</td>"
        # Das `@` traegt die Gruppenzugehoerigkeit an der Zeile (m.rau/bibi#134):
        # `@` = Oneshot, ein `next` daneben = Rhythmus, keins von beidem =
        # adhoc. Genau daran haengt der Unterschied zwischen „Gruppierung
        # entfernen" und „Gruppierung ausblenden".
        # Typ samt Port, aber **ohne Link** (m.rau/bibi#104). `#96` hatte hier
        # `_jobs_type_cell()` eingesetzt und damit korrekt verlinkt — mit
        # `public_host()`, also dem Knoten des BETRACHTERS. Im Mac-FE standen
        # danach fuenf Links auf `localhost:91xx`, wo nichts laeuft, waehrend
        # die Apps auf sarasate liefen: ein bestehender Fehler, vervielfacht
        # und dadurch sichtbar geworden. Der Typ war der unstrittige Teil und
        # bleibt; die Adresse braucht den ausfuehrenden Knoten und lebt in den
        # Slot-Kacheln, die ihn ueber `Tile.host` kennen.
        f'<td>{"@" if row.oneshot else ""}'
        f'{_jobs_type_cell(row.spec, public_host, link=False)}</td>'
        # Client zuerst (m.rau/bibi#147) — dieselbe Ordnung wie im Header und in
        # den Kacheln des Job-Details. `status` heisst dieses Feld in der
        # lokalen Job-DB; in den Scheduler-Zeilen heisst es `row_status` (live
        # abgenommen 2026-08-03).
        f'<td>{l.get("status") or "—"}</td>'
        f'<td>{s.get("row_status") or s.get("status") or "—"}</td>'
        # **`LAST` nennt den Lauf, der gerade läuft** (#136) — seine Startzeit,
        # nicht die des vorigen. Wer auf die Zeile sieht, während der Job
        # arbeitet, sucht diesen Lauf und nicht seinen Vorgänger; der steht
        # ohnehin im Journal.
        f'<td>{_uhrzeit(_laufender_start(s) or s.get("last_run_at"), now)}</td>'
        f"<td>{_next_zelle(row, s, now, ohne_zukunft)}</td>"
        # RUNTIME gehört auf diese Seite und ist ein Perzentil (m.rau/bibi#132):
        # P90 der letzten 30 Läufe, nicht die Dauer des letzten. Die sprang —
        # derselbe Job zeigte mal `2.8s`, mal `4m 34s`, je nachdem was zuletzt
        # geschah. Unter fünf Läufen liefert der Scheduler bewusst nichts.
        # `data-nodiff`: die einzige Zelle, die sich vom Aufflammen abmeldet
        # (#67). Sie zählt im Sekundentakt hoch, und gegen 3 s Ausklingzeit wäre
        # ihre Markierung dauerhaft an. **Dass sie hochzählt, ist erwartet und
        # damit keine Nachricht.** Die Abmeldung steht im Markup und nicht als
        # Spaltenindex im JavaScript — eine Ausnahme, die an einer Position
        # hängt, bricht beim ersten Spaltenumbau, und #135 baut sie um.
        f'<td data-nodiff>{_human_duration(s.get("runtime_p90"))}</td>'
        f'<td>{"—" if ohne_zukunft else (row.quote or "—")}</td>'
        "</tr>"
    )


#: Die Filtergruppen der Kopfleiste des Jobs-Screens. `TYPE` und `STATUS`
#: wirken auf beide Bänder.
#:
#: Die drei Journal-Filter standen bis #38 am dritten Band. Sie sind mit ihm
#: auf den Journal-Screen gezogen — ein Filter, der nur auf Zeilen wirkt, die
#: hier nicht mehr stehen, wäre ein toter Knopf, und ein toter Knopf ist
#: schlimmer als ein fehlender.
_FILTER_OBEN = (("TYPE", ("job", "claude", "app")),
                ("STATUS", ("waiting", "running", "stopped")))
#: Die dritte Achse (#31). Umbenannt gegenüber `dropped`/`oneshot`: die
#: Werte stehen seit dem Umzug in den Kopf neben kurzen Spaltennamen, und
#: `1shot` sagt dasselbe auf halber Breite. `gone` ist zudem das Wort, das
#: der Vorgang beschreibt — `dropped` klang nach einem Fehler beim Ablegen.
_FILTER_JOURNAL = ("local", "1shot", "gone")

#: Klickbare Spalten. Der Schlüssel ist zugleich der Query-Parameter.
_SORTIERBAR = (("slug", "SLUG"), ("type", "TYPE"), ("status", "STATUS"),
               ("last", "LAST"), ("next", "NEXT"), ("24h", "24H"))


def sortierbare_schluessel() -> frozenset[str]:
    """Welche ``sort``-Werte die Route annehmen darf (#95).

    **Abgeleitet statt aufgezählt.** Die Vorgängerin war eine zweite, von Hand
    gepflegte Liste (``_SORT_KEYS``), und sie lief auseinander: ``24h`` war
    klickbar, kam aber nicht durch die Prüfung — der Kopf setzte den Parameter,
    die Route warf ihn weg, und niemand sah einen Fehler, nur eine Spalte, die
    auf Klicks nicht reagierte.

    Eine neue klickbare Spalte kann diesen Fehler nicht wiederholen: sie steht
    in ``_SORTIERBAR`` und ist damit hier drin.
    """
    return frozenset(k for k, _ in _SORTIERBAR)


def _filter_knopf(wert: str, aktiv: list[str], *, tot: bool = False) -> str:
    """Ein Filterknopf. ``tot`` graut ihn aus, statt ihn wegzulassen (#31).

    Ein Knopf, der verschwindet, sobald er nichts trifft, lässt die Achse
    springen — und wer ihn sucht, weiss nicht, ob es ihn nie gab oder ob er
    gerade leer ist. Ausgegraut sagt beides: es gibt ihn, und hier trifft er
    nichts."""
    an = " on" if wert in aktiv else ""
    tot_klasse = " tot" if tot else ""
    disabled = " disabled" if tot else ""
    return (f'<button class="fltr{an}{tot_klasse}" data-filter="{wert}"'
            f"{disabled}>{wert}</button>")


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


def _jobs_kopf(sort: str | None, direction: str, *,
               typ: list[str] | None = None,
               status: list[str] | None = None,
               status_filter: bool = True) -> str:
    """Der Tabellenkopf — **eine** Quelle für Jobs- und Journal-Screen.

    Beide führen dieselbe Einheit (ein Slug = eine Zeile) und dieselben acht
    Spalten; sie unterscheiden sich in der Auswahl der Zeilen und in ihrer
    Filterleiste, nicht in der Tabelle. Zwei Köpfe nebeneinander liefen
    auseinander, sobald eine Spalte dazukäme — und genau das steht mit `#39`
    an.
    """
    return (
        "<thead>"
        '<tr class="gruppen"><th></th><th></th>'
        # **Client links, Scheduler rechts** (m.rau/bibi#147): links steht, was
        # dieser Knoten selbst weiss, rechts, was der Scheduler sagt — mit dem
        # Ausfall als Argument (FE §2): faellt der Host weg, verlieren genau die
        # rechten Werte ihre Gueltigkeit. Die Tabelle drehte das bisher um und
        # machte aus der Regel eine Ausnahme.
        #
        # `CLIENT`, nicht `LOCAL`: ein Wort fuer eine Sache. `LOCAL` ist nur aus
        # Sicht des Betrachters lokal, `CLIENT` benennt die Herkunft — und der
        # Header sagte ohnehin schon `CLIENT`.
        #
        # RUNTIME ist seit m.rau/bibi#132 eine Scheduler-Eigenschaft: er weiss,
        # wann es *wieder* laeuft, und wie lange es *dauert*. Beide Angaben sind
        # gefragt, beide gehoeren ihm — der Client bleibt der reine Zustand.
        '<th colspan="1" class="grp">CLIENT</th>'
        '<th colspan="4" class="grp">SCHEDULER</th><th></th></tr>'
        "<tr>"
        + _sort_kopf("slug", "SLUG", sort, direction)
        + _sort_kopf("type", "TYPE", sort, direction)
        # Die Client-Spalte ist nicht sortierbar — `status` sortiert nach dem
        # Scheduler-Zustand und tat das immer schon.
        + "<th>STATUS</th>"
        + _sort_kopf("status", "STATUS", sort, direction)
        + _sort_kopf("last", "LAST", sort, direction)
        + _sort_kopf("next", "NEXT", sort, direction)
        # Nicht sortierbar: FE §4.6 fuehrt sechs klickbare Spalten, RUNTIME ist
        # keine davon. Dass die Zahl jetzt tragfaehig ist, macht das Sortieren
        # naheliegend — aber das ist eine Erweiterung der Vorgabe, keine Folge
        # aus ihr.
        + "<th>RUNTIME</th>"
        + _sort_kopf("24h", "24H", sort, direction)
        + "</tr>"
        # **Die Filterwerte hängen unter der Spalte, die sie einschränken (#31).**
        #
        # Befund m.rau: *„Der Filter nimmt sehr viel Platz ein. Unnötig viel
        # Platz."* Der Platz ging nicht für die Knöpfe drauf, sondern für eine
        # Doppelung: `TYPE` und `STATUS` standen als Spaltenkopf **und** als
        # Gruppenlabel der Leiste darüber — dasselbe Wort zweimal, einen
        # Zentimeter auseinander, einmal als Überschrift und einmal als
        # Beschriftung.
        #
        # **Der Kopf trägt danach zwei Bedeutungen**, und beide sind alt: der
        # Klick sortiert (wie bisher), die Werte darunter filtern (wie bisher,
        # nur woanders). Kein neues Konzept — zwei Dinge an einem Ort, die
        # schon immer über dieselbe Spalte sprachen.
        #
        # `STATUS` hängt unter der **Scheduler**-Spalte, nicht unter der des
        # Clients: der Filter wirkt ausschliesslich auf den Scheduler-Zustand
        # (Klarstellung m.rau), der Client-Zustand ist Anzeige. Die leere Zelle
        # dazwischen sagt das deutlicher als jeder Kommentar es könnte.
        + _filter_zeile(typ or [], status or [], status_filter)
        + "</thead>"
    )


def _filter_zeile(typ: list[str], status: list[str],
                  status_filter: bool = True) -> str:
    """Die dritte Kopfzeile: Filterwerte unter ihren Spalten (#31).

    Acht Zellen, damit die Zeile zur Tabelle passt — gefüllt sind zwei. Die
    leeren stehen ausdrücklich da, statt die Zeile per ``colspan`` zu
    verkürzen: eine Spalte ohne Filter ist eine Aussage (*hier gibt es nichts
    zu filtern*), und sie geht verloren, sobald die Zellen verrutschen dürfen.

    ``status_filter=False`` lässt die STATUS-Zelle leer — für den
    Journal-Screen. Dort steht Historie, die keinen laufenden Zustand hat;
    ``trifft_filter()`` überspringt den Filter für dieses Segment ohnehin, und
    **ein toter Knopf ist schlimmer als ein fehlender**. Die Spalte selbst
    bleibt, weil beide Screens einen Tabellenkopf teilen.

    Diese Unterscheidung stand schon als Test da, bevor die Filter umzogen
    (``test_the_journal_screen_has_no_status_filter``) — sie hat den Umbau
    beim ersten Lauf gefangen. Genau dafür ist sie da.
    """
    def zellen(werte, aktiv):
        return ('<th class="fltr-zelle">'
                + "".join(_filter_knopf(w, aktiv) for w in werte)
                + "</th>")

    return (
        '<tr class="fltr-kopf">'
        "<th></th>"
        + zellen(_FILTER_OBEN[0][1], typ)      # TYPE
        + "<th></th>"                          # CLIENT — Anzeige, kein Filter
        + (zellen(_FILTER_OBEN[1][1], status) if status_filter else "<th></th>")
        + "<th></th><th></th><th></th><th></th>"
        + "</tr>"
    )


#: Die leere Seite, bevor überhaupt ein Job existiert. Sie sagt, was fehlt
#: **und** was man tun kann — das ist die eigentliche Einstiegsdokumentation
#: dieses Screens (Umbauplan §4).
_KEINE_JOBS = (
    '<div class="leer">'
    "<p>No jobs yet.</p>"
    "<p class=\"muted\">bibi finds its work in your vault: add "
    "<code>schedule:</code> to the frontmatter of a markdown file for a "
    "recurring job, or <code>at:</code> for a one-off. "
    # `<code>` statt einer Monospace-Klasse ohne CSS-Regel: dieselbe Auszeichnung
    # wie bei `schedule:` und `at:` eine Zeile darüber, und eine, die es gibt.
    "Then press <code>⟳</code> to rescan.</p>"
    "</div>"
)


def _sichtbar(rows: list, *, typ: list[str], status: list[str],
              journal: list[str], sort: str | None, direction: str) -> list:
    """Filtern und sortieren — der gemeinsame Vorlauf beider Screens."""
    from bibi.controller import jobs_view

    rows = [r for r in rows
            if jobs_view.trifft_filter(r, typ=typ, status=status, journal=journal)]
    if sort:
        rows = jobs_view.sortiere(rows, nach=sort, richtung=direction)
    return rows


def jobs_screen(rows: list, now: float, *, typ: list[str] | None = None,
                status: list[str] | None = None, journal: list[str] | None = None,
                sort: str | None = None, direction: str = "asc",
                group: bool = True, public_host: str = "localhost") -> str:
    """Die **zwei** Bänder mit ihren Zeilen — oder eine Liste ohne Unterteilung.

    Beide stehen immer da, auch leer: sonst verschöbe sich das Layout je
    nachdem, was gerade existiert, und man suchte ein Band, das nur gerade
    nichts enthält.

    **Es waren drei, bis #38 das dritte umzog.** Der Befund war gemessen und
    nicht gefühlt: 37 Jobs im Screen, davon 23 im JOURNAL-Segment — knapp zwei
    Drittel der Zeilen gehörten Jobs, die es nicht mehr gibt, und standen
    zwischen den 14, um die es täglich geht. Der Screen war nicht zu voll, er
    war **falsch gewichtet**: das Seltene verdrängte das Häufige. Das dritte
    Segment steht seither unter :func:`journal_screen`.

    Die Bänder sind eine Klassifikation, keine Sortierordnung — sortiert wird
    innerhalb eines Bandes.

    **``group=False`` blendet sie aus** (m.rau/bibi#134). Das ist kein
    Widerspruch zur Klassifikation, sondern ihre Folge: seit die Zeile ihre
    Gruppe selbst trägt (``@`` beim Oneshot, ein ``next`` beim Rhythmus,
    keins von beidem bei ``adhoc``), ist die Bänderung nur noch eine
    Darstellungsform. Die Sortierung wirkt dann über die ganze Liste — genau
    das ist der Zweck des Schalters.

    ``journal`` bleibt in der Signatur und wird durchgereicht: die Auswahl
    lebt in der URL, und ein Wechsel zwischen den beiden Screens soll sie
    nicht unterwegs verlieren. Gefiltert wird damit hier nichts mehr — die
    Zeilen, auf die sie wirkt, stehen drüben.
    """
    if not rows:
        return _KEINE_JOBS

    typ, status, journal = typ or [], status or [], journal or []
    rows = [r for r in _sichtbar(rows, typ=typ, status=status, journal=journal,
                                 sort=sort, direction=direction)
            if r.segment is not Segment.JOURNAL]

    # **Die Toolbar-Zeile trägt nur noch, was keiner Spalte gehört** (#31,
    # Vorschlag 1 der Design-Studie): links die Schalter, rechts die Kennzahl.
    # `TYPE` und `STATUS` sind mit ihren Werten in den Tabellenkopf gezogen —
    # dorthin, wo die Spalte steht, die sie einschränken.
    #
    # `group` bleibt hier, und das ist kein Rest: er schaltet die **Bänder**,
    # also die Gliederung der ganzen Tabelle, und hat deshalb keine Spalte,
    # unter die er ziehen könnte.
    # Die dritte Achse steht in der Toolbar, nicht im Kopf: ihre Werte
    # beschreiben die **Herkunft** einer Zeile (lokal gelaufen, einmalig,
    # abgelegt) und gehören zu keiner Spalte. `gone` trifft ohne sichtbares
    # Journal-Band nichts — es wird ausgegraut statt versteckt, damit die Achse
    # ihre Form behält und niemand einen Knopf sucht, der nur gerade leer ist.
    hat_gone = any(r.relation in ("dropped", "deleted") for r in rows)
    achse = "".join(
        _filter_knopf(w, journal, tot=(w == "gone" and not hat_gone))
        for w in _FILTER_JOURNAL)
    leiste = (f'<div class="fltr-bar">{achse}'
              f'<button class="fltr{" on" if group else ""}" data-group='
              f'"{"off" if group else "on"}">group</button>'
              f'<span class="fltr-zahl">{len(rows)} jobs</span></div>')

    kopf = _jobs_kopf(sort, direction, typ=typ, status=status)

    if not group:
        # Eine Liste ohne Unterteilung. Die Sortierung wirkt damit über alles,
        # statt innerhalb jedes Bandes — genau der Zweck des Schalters.
        zeilen = "".join(_jobs_zeile(r, now, public_host=public_host) for r in rows)
        return f'{leiste}<table class="jobs">{kopf}<tbody>{zeilen}</tbody></table>'

    teile = []
    for seg in (Segment.SCHEDULE, Segment.ADHOC):
        drin = [r for r in rows if r.segment is seg]
        teile.append(
            f'<tr class="band"><td colspan="8">{seg.value.upper()} '
            f'<span class="muted">{len(drin)}</span></td></tr>'
        )
        if drin:
            teile.extend(_jobs_zeile(r, now, public_host=public_host) for r in drin)
        else:
            teile.append(f'<tr class="leer-band"><td colspan="8">— {_LEER[seg]}</td></tr>')

    return f'{leiste}<table class="jobs">{kopf}<tbody>{"".join(teile)}</tbody></table>'


def journal_screen(rows: list, now: float, *, typ: list[str] | None = None,
                   status: list[str] | None = None,
                   journal: list[str] | None = None,
                   sort: str | None = None, direction: str = "asc",
                   public_host: str = "localhost") -> str:
    """Das dritte Segment, jetzt auf eigenem Screen (#38).

    **Ein Umzug, kein neuer Screen** — und der Unterschied zum gestrichenen
    Archive-Tab muss im Code sichtbar bleiben, sonst wird der alte versehentlich
    wiederbelebt: dort war eine Zeile ein **Lauf**, hier ist sie ein **Job**.
    Die Frage „was lief heute Nacht?" bleibt gestrichen; diese hier beantwortet
    „welche Jobs haben nur noch Historie?".

    Kein ``group``-Handle: es gibt nur eine Sektion, und ein Schalter, der
    zwischen einer Gruppe und keiner umschaltet, schaltet nichts.

    Kein ``STATUS``-Filter: im Journal steht Historie, die keinen laufenden
    Zustand hat — ``trifft_filter()`` überspringt ihn für dieses Segment schon
    immer, und ein Knopf ohne Wirkung ist schlimmer als keiner. ``status``
    bleibt trotzdem in der Signatur und wird durchgereicht, damit ein Wechsel
    zurück zu Jobs die dortige Wahl wiederfindet.
    """
    typ, status, journal = typ or [], status or [], journal or []
    rows = [r for r in _sichtbar(rows, typ=typ, status=status, journal=journal,
                                 sort=sort, direction=direction)
            if r.segment is Segment.JOURNAL]

    # Wie beim Jobs-Screen (#31): `TYPE` zieht in den Kopf, die Toolbar behält,
    # was keiner Spalte gehört. Die Journal-Achse ist genau so ein Fall — sie
    # beschreibt die **Herkunft** einer Zeile (abgelegt, einmalig, lokal
    # gelaufen) und nicht den Inhalt einer Spalte.
    leiste = (f'<div class="fltr-bar">'
              + "".join(_filter_knopf(w, journal) for w in _FILTER_JOURNAL)
              + f'<span class="fltr-zahl">{len(rows)} jobs</span></div>')

    if not rows:
        return f'{leiste}<div class="leer"><p class="muted">— {_LEER[Segment.JOURNAL]}</p></div>'

    zeilen = "".join(_jobs_zeile(r, now, public_host=public_host) for r in rows)
    kopf = _jobs_kopf(sort, direction, typ=typ, status=status,
                      status_filter=False)
    return (f'{leiste}<table class="jobs">{kopf}'
            f"<tbody>{zeilen}</tbody></table>")


_JOBS_JS = """
(function(){
  // Filter und Sortierung leben in der URL, nicht im Speicher der Seite:
  // damit ist jede Ansicht teilbar, ueberlebt ein Neuladen und laesst sich
  // zurueckblaettern. Die Auswertung passiert am Server -- dieselbe
  // Klassifikation wie beim ersten Aufbau, kein zweiter Filter im Browser.
  //
  // #85: **ein Zuhoerer an `document.body`, nicht einer je Knopf.** Die
  // Knoepfe und die Spaltenkoepfe stehen IN `#jobs`, und der Bus ersetzt genau
  // diese Region per `outerHTML`-Swap. Direkt gebundene Handler hingen danach
  // an Elementen, die nicht mehr im DOM waren -- ab dem ersten Refetch war
  // jeder Klick folgenlos, bis jemand neu lud. Und ein Refetch passiert bei
  // jedem Job-Zustandswechsel: auf einem Knoten mit laufenden Jobs war "tot"
  // der Normalfall und "klickbar" das kurze Fenster nach dem Laden.
  //
  // `_EVENTS_JS` loest dasselbe Problem ueber `htmx:afterSettle` und
  // Neuverdrahtung; hier ist Delegation der einfachere Weg, weil es nichts
  // aufzubauen gibt -- nur zuzuhoeren. Der Zuhoerer haengt an `body`, das kein
  // Swap dieser Seite je austauscht.
  //
  // Die URL wird beim Klick gelesen und nicht beim Laden gemerkt: ein
  // gemerktes Objekt waere nach einer History-Aenderung veraltet, und
  // "veraltet" hiesse hier "faellt auf eine fruehere Ansicht zurueck".
  const gruppe = (wert) => {
    if (['job','claude','app'].includes(wert)) return 'typ';
    if (['waiting','running','stopped'].includes(wert)) return 'status';
    return 'journal';
  };
  // Jede von hier gebaute URL traegt `f=1` (m.rau/bibi#156): sie sagt damit
  // "diese Query ist die Antwort, auch wo sie schweigt". Ohne das Zeichen ist
  // eine URL ohne `typ` von einer nie gesetzten nicht zu unterscheiden -- der
  // Server faellt dann auf den Cookie zurueck und bringt genau den Filter
  // wieder, den man eben abgewaehlt hat.
  const gehe = (url) => {
    url.searchParams.set('f', '1');
    window.location.href = url.toString();
  };
  const mehrfach = (url, name, wert) => {
    const da = url.searchParams.getAll(name);
    url.searchParams.delete(name);
    // Toggle: was schon drin ist, faellt raus.
    const neu = da.includes(wert) ? da.filter(v => v !== wert) : da.concat([wert]);
    neu.forEach(v => url.searchParams.append(name, v));
    gehe(url);
  };
  document.body.addEventListener('click', (ev) => {
    const ziel = ev.target;
    if (!ziel || !ziel.closest) return;
    // `closest`, nicht `ev.target` selbst: ein Klick kann ein Kind treffen
    // (der Pfeil im sortierten Spaltenkopf ist Text im `th`).
    const knopf = ziel.closest('.fltr[data-filter], .fltr[data-group]');
    const kopf = knopf ? null : ziel.closest('th[data-sort]');
    if (!knopf && !kopf) return;
    const url = new URL(window.location.href);
    if (knopf && knopf.dataset.filter !== undefined) {
      mehrfach(url, gruppe(knopf.dataset.filter), knopf.dataset.filter);
    } else if (knopf) {
      // Das group-Handle ist kein Mehrfach-Toggle, sondern ein Schalter mit
      // zwei Stellungen -- der Knopf traegt die Stellung, die er herstellt.
      if (knopf.dataset.group === 'off') url.searchParams.set('group', 'off');
      else url.searchParams.delete('group');
      gehe(url);
    } else {
      const jetzt = url.searchParams.get('sort');
      const richtung = url.searchParams.get('dir') || 'asc';
      url.searchParams.set('sort', kopf.dataset.sort);
      // Zweiter Klick auf dieselbe Spalte dreht die Richtung um.
      url.searchParams.set('dir', jetzt === kopf.dataset.sort && richtung === 'asc'
                                   ? 'desc' : 'asc');
      gehe(url);
    }
  });
})();
"""


#: Der Marker, den jede vom Skript gebaute Ansichts-URL trägt (m.rau/bibi#156).
#: Er beantwortet die eine Frage, die eine leere Query sonst offen lässt: *ist
#: hier nichts gewählt, oder wurde alles abgewählt?* Ohne ihn brächte der
#: Cookie den eben gelöschten Filter zurück, und der Knopf wäre tot.
VIEW_MARKER = "f"


def _jobs_view_query(*, typ: list[str] | None, status: list[str] | None,
                     journal: list[str] | None, sort: str | None,
                     direction: str, group: bool) -> str:
    """Die aktuelle Ansicht als Query-String — für die Refetch-URL.

    Der Bus lud bis #156 ``/-/jobs/list`` **ohne** Query nach. Damit kam bei
    jedem Job-Statuswechsel die ungefilterte Liste zurück und ersetzte die
    gefilterte: jede Filterwahl galt nur bis zum nächsten Ereignis, und das ist
    der Normalbetrieb. Der Cookie allein reicht dagegen nicht — zwei Browser-
    Tabs teilen sich einen, und der zweite überschriebe dem ersten die Sicht.
    Die Query gehört deshalb an die URL, der Cookie ist nur für die Wiederkehr.
    """
    from urllib.parse import urlencode
    paare: list[tuple[str, str]] = [(VIEW_MARKER, "1")]
    for name, werte in (("typ", typ), ("status", status), ("journal", journal)):
        paare += [(name, w) for w in (werte or [])]
    if sort:
        paare += [("sort", sort), ("dir", direction or "asc")]
    if not group:
        paare.append(("group", "off"))
    return urlencode(paare)


def jobs_list_fragment(rows: list, now: float, *, typ: list[str] | None = None,
                       status: list[str] | None = None,
                       journal: list[str] | None = None,
                       sort: str | None = None, direction: str = "asc",
                       group: bool = True, public_host: str = "localhost") -> str:
    """Die Bänder **samt ihrem Bus-Wrapper** — das Nachlade-Ziel des Bus.

    Der Wrapper gehört ins Fragment, nicht nur in die Seite: ``_EVENTS_JS``
    swappt mit ``outerHTML``, ersetzt das Ziel-Element also vollständig. Käme
    die Antwort ohne ``data-bus``, wäre die Region nach dem ersten Update
    abgemeldet und bewegte sich bis zum nächsten Reload nicht mehr.

    Dieselbe Form wie ``feed_status_fragment()`` und ``clients_board_
    fragment()`` — die Konvention stand, dieses eine Fragment hielt sie nicht
    (Befund m.rau, 2026-08-05). Genau **eine** Quelle für den Wrapper, damit
    Seite und Fragment nicht auseinanderlaufen können.
    """
    return (
        # Am Bus angemeldet: `jobs` traegt jede Job-Zustandsaenderung, und der
        # Scheduler-Diff des Collectors meldet ueber `feedstatus`, was sich
        # drueben getan hat -- eine geloeschte MD faellt erst beim Rescan auf
        # und ist kein Job-Ereignis. Nachgeladen wird die Liste, nicht die
        # Seite: sonst ginge bei jedem Ereignis Scroll-Position und Fokus
        # verloren (Befund m.rau, 2026-08-03).
        # Die Query gehört an die Refetch-URL, sonst kommt die Liste roh
        # zurück und macht jede Filterwahl beim ersten Ereignis zunichte
        # (m.rau/bibi#156).
        f'<div id="jobs" data-bus="jobs" data-bus-refetch="/-/jobs/list?'
        + _jobs_view_query(typ=typ, status=status, journal=journal, sort=sort,
                           direction=direction, group=group)
        + '">'
        + jobs_screen(rows, now, typ=typ, status=status, journal=journal,
                      sort=sort, direction=direction, group=group,
                      public_host=public_host)
        + "</div>"
    )


def journal_list_fragment(rows: list, now: float, *, typ: list[str] | None = None,
                          status: list[str] | None = None,
                          journal: list[str] | None = None,
                          sort: str | None = None, direction: str = "asc",
                          group: bool = True,
                          public_host: str = "localhost") -> str:
    """Die Journal-Liste samt Bus-Wrapper — dieselbe Form wie bei Jobs.

    ``data-bus="jobs"``, weil es dieselben Ereignisse sind: ein Job, der
    aufhört zu existieren, wandert von dort nach hier, und beide Screens
    müssen das mitbekommen. Der Bus swappt per ``querySelectorAll`` — mehrere
    Regionen unter einem Ziel sind vorgesehen.
    """
    return (
        f'<div id="journal-jobs" data-bus="jobs" data-bus-refetch="/-/jobs/journal/list?'
        + _jobs_view_query(typ=typ, status=status, journal=journal, sort=sort,
                           direction=direction, group=group)
        + '">'
        + journal_screen(rows, now, typ=typ, status=status, journal=journal,
                         sort=sort, direction=direction, public_host=public_host)
        + "</div>"
    )


def jobs_page_v5(rows: list, *, now: float, daemon_status: dict | None = None,
                 git_status: dict | None = None, host_url: str | None = None,
                 scheduler: dict | None = None,
                 scheduler_stale_since: float | None = None,
                 typ: list[str] | None = None, status: list[str] | None = None,
                 journal: list[str] | None = None,
                 sort: str | None = None, direction: str = "asc",
                 group: bool = True, public_host: str = "localhost") -> str:
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
        # Wrapper + Bänder kommen aus `jobs_list_fragment()` — derselben
        # Funktion, die die Refetch-Route bedient. Der frühere `hx-get` mit
        # `hx-trigger="bibiJobsChanged"` ist entfallen: das Ereignis hat nie
        # jemand gefeuert (einzige Fundstelle im Repo war der Trigger selbst),
        # und mit `hx-swap="innerHTML"` widersprach es dem `outerHTML` des Bus.
        f'{jobs_list_fragment(rows, now, typ=typ, status=status, journal=journal, sort=sort, direction=direction, group=group, public_host=public_host)}'
        # Der Empfaenger zur Anmeldung darueber (m.rau/bibi#153): `data-bus`
        # allein bewirkt nichts, den Strom baut ausschliesslich `_EVENTS_JS`
        # auf. Beim Neubau der v5-Seiten blieb es aus — als einzige Screens.
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_DIFF_JS}</script>"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


def journal_page_v5(rows: list, *, now: float, daemon_status: dict | None = None,
                    git_status: dict | None = None, host_url: str | None = None,
                    scheduler: dict | None = None,
                    scheduler_stale_since: float | None = None,
                    typ: list[str] | None = None, status: list[str] | None = None,
                    journal: list[str] | None = None,
                    sort: str | None = None, direction: str = "asc",
                    group: bool = True, public_host: str = "localhost") -> str:
    """Die Journal-Seite: dieselbe Hülle wie Jobs, andere Liste.

    ``_JOBS_JS`` liegt auch hier: Filter- und Sortier-Klicks werden über einen
    Zuhörer an ``body`` ausgewertet und schreiben in die URL der Seite, auf der
    sie passieren — der Screen muss dafür nicht bekannt sein.
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>bibi · Journal</title>"
        f"<style>{_CSS}</style>"
        f'<script src="/-/static/htmx-1.9.12.min.js"></script>'
        "</head><body>"
        f"{_header('Journal', daemon_status, scheduler=scheduler, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f'{journal_list_fragment(rows, now, typ=typ, status=status, journal=journal, sort=sort, direction=direction, group=group, public_host=public_host)}'
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_DIFF_JS}</script>"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOBS_JS}</script>"
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
  // Der Ladevorgang als eigene Funktion, weil ihn zwei Stellen brauchen: der
  // Klick und der Retter nach einem Swap (m.rau/bibi#105).
  const ladeOutput = async (zeile, show) => {
    const feld = zeile.querySelector('.out-body');
    if (!feld || feld.dataset.geladen) return;
    feld.textContent = 'loading …';
    // Der Pfad ohne Query: `?days=90` gehoert zur Liste, nicht zum Lauf.
    const basis = location.pathname;
    const ziel = show.dataset.slot
      ? `${basis}/slot/${SEITE[show.dataset.src] || 'client'}/${show.dataset.slot}/output`
      : `${basis}/runs/${show.dataset.jid}/output`;
    try {
      const r = await fetch(ziel);
      // `innerHTML`, nicht `textContent` (m.rau/bibi#99): die Antwort ist seit
      // dem Formatter-Anschluss ausgezeichnetes Markup — Uhrzeit-Praefix,
      // err/thinking-Klassen, der einklappbare Denkabschnitt. Als Text
      // eingesetzt staende das Markup lesbar in der Box. Der Server escapt
      // jede Zeile ueber `_e()`, bevor er sie einsetzt.
      if (r.ok) {
        feld.innerHTML = await r.text();
        // Steckt eine Live-Box darin, braucht sie ihren Strom (#124). Ohne
        // diesen Aufruf waere sie gebaut und nicht angeschlossen — genau die
        // Verwechslung, die dieses Ticket erzeugt hat.
        if (window.__bibiInitBoxes) window.__bibiInitBoxes();
      }
      else feld.textContent = 'output unavailable';
      // Ein laufender Lauf ist noch nicht fertig — sein Output darf beim
      // naechsten Aufklappen nicht aus dem Cache kommen.
      if (r.ok && !show.dataset.slot) feld.dataset.geladen = '1';
    } catch (e) { feld.textContent = 'output unavailable'; }
  };
  document.addEventListener('click', async (ev) => {
    const show = ev.target.closest('.run-show');
    if (!show) return;
    const zeile = document.getElementById('run-' + show.dataset.run);
    if (!zeile) return;
    if (!zeile.hidden) { zeile.hidden = true; show.textContent = '[show]'; return; }
    zeile.hidden = false;
    show.textContent = '[hide]';
    await ladeOutput(zeile, show);
  });
  // Der Faltzustand ueberlebt einen Bus-Refetch (#44).
  //
  // Er lebt ausschliesslich im DOM: `hidden` an der Ausklappzeile, der
  // Knopftext und der geladene Text im `.out-body`. Ein Refetch tauscht
  // `#runs` komplett aus — und damit klappt der Bereich genau dann zu, wenn
  // jemand einem laufenden Job zusieht. Solange die Liste selten neu lud, war
  // das ein Aergernis; mit #43 refetcht sie bei jedem Slot-Zustandswechsel,
  // und daraus wird ein Ausschlusskriterium. Deshalb gehoeren beide zusammen.
  //
  // Dasselbe Muster wie `_SCROLL_JS` fuer die Scroll-Position: sichern auf
  // `htmx:beforeSwap`, wiederherstellen auf `htmx:afterSettle`.
  //
  // Gemerkt wird nach `run_id`, **nicht** nach Zeilenposition — nach einem
  // Refetch kann oben ein neuer Lauf stehen. Es ist derselbe Bezug, aus dem
  // schon der Deep-Link unten die Archivierung ueberlebt.
  const istRuns = (t) => t && t.id === 'runs';
  let offen = null;
  document.body.addEventListener('htmx:beforeSwap', (ev) => {
    const t = ev.detail && ev.detail.target;
    if (!istRuns(t)) return;      // nicht bei jedem Swap irgendwo aufklappen
    offen = [];
    t.querySelectorAll('tr.out:not([hidden])').forEach((z) => {
      const feld = z.querySelector('.out-body');
      offen.push({run: z.id.slice(4),
                  // `innerHTML`, nicht `textContent`: die Antwort ist seit
                  // #99 ausgezeichnetes Markup (Uhrzeit, thinking-Klassen,
                  // der einklappbare Denkabschnitt). Als Text gesichert kaeme
                  // er als Text zurueck und das Markup staende lesbar da.
                  html: feld ? feld.innerHTML : '',
                  geladen: !!(feld && feld.dataset.geladen)});
    });
  });
  document.body.addEventListener('htmx:afterSettle', () => {
    if (!offen) return;
    for (const s of offen) {
      const z = document.getElementById('run-' + s.run);
      if (!z) continue;           // der Lauf ist aus dem Zeitfenster gefallen
      z.hidden = false;
      const feld = z.querySelector('.out-body');
      const b = document.querySelector('.run-show[data-run="' + s.run + '"]');
      if (feld) {
        // **Zwei Faelle, und bis #105 wurden sie gleich behandelt.**
        //
        // Ist der Lauf archiviert (`geladen`), ist sein Output vollstaendig
        // und unveraenderlich — der gerettete Stand ist der richtige, und ihn
        // neu zu holen waere ein Roundtrip je Refetch ohne Gewinn. Das war
        // die urspruengliche Begruendung und sie gilt hier weiter.
        //
        // Ist er es **nicht**, laeuft er noch — und dann ist der gerettete
        // Stand per Definition unfertig. Genau hier lag der Fehler: wird der
        // Lauf terminal, feuert der Bus, `#runs` wird getauscht, und der alte
        // Text kam zurueck. Danach korrigiert ihn nichts mehr, weil kein
        // weiteres Ereignis folgt. Sichtbar als: Status `complete`, letzte
        // Zeile fehlt, erst ein Reload holt sie (Befund m.rau, 2026-08-09).
        feld.innerHTML = s.html;
        // **Hier entscheidet sich, ob es ruckelt** (#124). Traegt der gerettete
        // Stand eine Live-Box, setzt sie ueber ihr `data-from` exakt dort auf,
        // wo sie stand — Doppelte verwirft der Offset-Dedup. Ihn stattdessen
        // vollstaendig neu zu holen waere ein Roundtrip je Swap und ein
        // sichtbarer Neuaufbau statt eines Weiterlaufens.
        const lebt = feld.querySelector('.liveterm');
        if (s.geladen) { feld.dataset.geladen = '1'; }
        else if (lebt) { if (window.__bibiInitBoxes) window.__bibiInitBoxes(); }
        else if (b) { ladeOutput(z, b); }
      }
      if (b) b.textContent = '[hide]';
    }
    offen = null;
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
            f'data-ziel="{_e(ziel or "client")}" title="Discards the per-job image; '
            f'the next run starts from the default image">REBUILD</button>')
    return f'<span class="slot-bar">{" ".join(teile)}</span>'


#: Die drei Verben. Ein Klick postet an den Controller, der an die richtige
#: Seite weiterleitet — der Scheduler-Slot liegt auf dem Host, der Client-Slot
#: hier. Danach laedt die Seite neu; der Bus meldet die Aenderung ohnehin,
#: aber der Klickende soll seine Wirkung sofort sehen und nicht auf den
#: naechsten Tick warten.
#:
#: **Delegiert am `document`, nicht je Knopf** (m.rau/bibi#152). Die fruehere
#: Fassung band beim Seitenaufbau einen Listener an jeden `button.slot-do` —
#: damit ueberlebte kein Knopf einen `outerHTML`-Swap, und genau deshalb
#: standen die Kacheln ausserhalb der Bus-Region: ein Nachladen haette sie in
#: Attrappen verwandelt, die aussehen wie Knoepfe und nichts tun. Der Listener
#: gehoert jetzt der Seite, nicht dem Element; ein ausgetauschter Knopf wirkt
#: sofort. `_JOB_DETAIL_JS` macht es fuer die Output-Faltung laengst so.
_SLOT_JS = """
(function(){
  document.addEventListener('click', async (ev) => {
    const b = ev.target.closest('button.slot-do');
    if (!b || b.disabled) return;
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
    # Der Weg zum Dienst gehoert an die Kachel (m.rau/bibi#104, Entscheidung
    # 2026-08-09) und **kehrt #145 um**, das ihn ausdruecklich in den Kopf
    # gelegt hatte. Die Begruendung dort war richtig und ist es noch: `app_port`
    # gilt fuer den Job, nicht fuer einen Lauf. Sie uebersah nur, dass die
    # ADRESSE nicht dem Job gehoert — derselbe Port meint auf zwei Knoten zwei
    # Dienste, und welcher gemeint ist, weiss ausschliesslich die Kachel ueber
    # ihren `host`. Der Kopf hatte dafuer nur `config.public_host()`, also den
    # Knoten des BETRACHTERS: im Mac-FE zeigte der Link auf `localhost:9110`,
    # wo nichts laeuft, waehrend die App auf sarasate lief.
    app = (f' <a class="tile-app" href="http://{_e(str(kachel.host))}:'
           f'{_e(str(kachel.app_port))}/" target="_blank" rel="noopener">'
           f'app :{_e(str(kachel.app_port))}</a>'
           if getattr(kachel, "app_port", None) and kachel.host else "")
    if kachel.disabled:
        # Gesperrt, nicht verborgen (m.rau/bibi#146). Der Grund steht **im**
        # Text und nicht im `title`: auf einem Touch-Gerät gibt es kein Hover,
        # und eine graue Kachel ohne Begründung ist nur eine andere Art, nichts
        # zu sagen. Keine Verbleiste — ausgegraut heisst nicht bedienbar, und
        # ein Knopf, der nur grau aussieht und trotzdem postet, verspricht eine
        # Wirkung, die es nicht gibt.
        return (
            f'<div class="tile tile-off"><div class="tile-head">{titel}{app}</div>'
            f'<div class="tile-state">{_e(kachel.disabled)}</div>'
            "</div>"
        )
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
                # **Dieselbe 24-Stunden-Regel wie im Header (#39).**
                # `_abs_time()` liefert nur `HH:MM`; bei einem Lauf von
                # vorgestern stand dort `last 14:03` — eine Angabe, die falsch
                # gelesen wird, weil sie „heute" suggeriert. `_uhrzeit()` nimmt
                # unter 24 Stunden die Uhrzeit allein und darüber Datum plus
                # Uhrzeit (FE §2); der Header macht es an dieser Stelle längst
                # richtig, die Kachel benutzte die Regel nur nicht.
                #
                # Ein Funktionstausch, kein neues Konzept — und deshalb der
                # erste der vier Punkte des Tickets: er behebt eine echte
                # Fehllesung, während die anderen drei etwas hinzufügen.
                teile.append(f"last {_uhrzeit(kachel.last_at, now)}")
                # **Runtime, Commit und der Weg zum Lauf (#39, Punkte 2-4).**
                #
                # Befund m.rau: *„Warum nicht die Runtime wenn verfügbar? Warum
                # nicht der commit? Warum kein Link runter zu den Details, wo
                # ich auch den Output öffnen kann!?"* — die Kachel war eine
                # Sackgasse: sie nennt einen Lauf und bot keinen Weg dorthin.
                #
                # **Jede Angabe nur, wenn es sie gibt.** Ein leeres `commit —`
                # sähe aus wie ein Fehler, wo nur nichts zu sagen ist —
                # dieselbe Erwägung, aus der `last` ohne lokalen Lauf ganz
                # entfällt. Der Commit ist heute in rund 7 % der Läufe belegt;
                # **seine Leere ist selbst eine Auskunft**, nämlich dass dieser
                # Lauf im Vault nichts bewegt hat.
                laufzeit = kachel.slot.get("exec_runtime")
                if laufzeit:
                    teile.append(_human_duration(laufzeit))
                sha = kachel.slot.get("commit_sha")
                if sha:
                    teile.append(f"commit {_e(str(sha)[:7])}")
                # Der Weg zum Lauf, und bewusst ein Anker statt einer Route:
                # die Lauf-Liste steht auf **dieser** Seite. Ein Link, der die
                # Seite neu lädt, um zwei Bildschirmhöhen tiefer zu landen,
                # verlöre Faltzustand und Scroll-Position — genau das, was
                # `#44` an der Region eigens rettet.
                teile.append('<a href="#runs" class="tile-weg" '
                             'title="jump to the runs of this job">runs ↓</a>')
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
            teile.append(_human_duration(
                (beendet if beendet is not None else now) - begonnen,
                seit=begonnen if beendet is None else None))
        zustand = " &middot; ".join(teile)
    return (
        f'<div class="tile"><div class="tile-head">{titel}{app}</div>'
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


def job_tiles_fragment(tiles: list, *, now: float, slug: str,
                       job_uid: str) -> str:
    """Die Kacheln nebeneinander (FE §5.1), als eigene Bus-Region.

    **Nebeneinander, weil sie gleichrangig sind** und man sie ständig
    vergleicht („läuft es beim Scheduler, aber lokal nicht?"). Eine Kachel
    fehlt genau dann, wenn es dort keinen Slot gibt — nicht, wenn er leer ist.

    **Am Bus unter `live:<slug>`** (m.rau/bibi#152): der Kachel-Zustand ist
    genau das, was sich nach einem START ändert, und er blieb bisher stehen.
    Dass die Kacheln dabei ihre Knopfleiste mittauschen, ist seit der
    Umstellung von `_SLOT_JS` auf Delegation folgenlos — vorher hätte es die
    Listener gekostet, und genau deshalb standen sie außerhalb.

    **Der Wrapper gehört ins Fragment, nicht nur in die Seite** (die Lehre aus
    #151): `_EVENTS_JS` swappt mit `outerHTML`, eine Antwort ohne eigenes
    `data-bus` meldete die Region beim ersten Update ab. Und er steht auch
    dann, wenn es gerade keine Kachel gibt — sonst könnte die Region den
    Übergang „Slot entsteht" nie empfangen.
    """
    innen = ('<div class="tiles">'
             + "".join(_slot_kachel(k, now=now) for k in tiles)
             + "</div>") if tiles else ""
    return (f'<div id="tiles" data-bus="live:{_e(slug)}" '
            f'data-bus-refetch="/-/jobs/{_e(job_uid)}/tiles">{innen}</div>')


def job_runs_fragment(liste, *, now: float, slug: str | None = None,
                      job_uid: str | None = None,
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

    basis = f"/-/jobs/{job_uid}" if job_uid else ""

    def _region(inneres: str) -> str:
        """Der Bus-Wrapper — im Fragment, nicht nur in der Seite.

        Dieselbe Lücke wie bei der Jobs-Liste in #151, hier nur nie
        aufgefallen: solange die Detail-Seite überhaupt keinen Bus-Client
        auslieferte (#153), fand nie ein Swap statt, der die Region hätte
        abmelden können. **Ein Fehler hat wieder den anderen verdeckt.**

        **Das Ziel ist ``journal:<slug>``, nicht mehr ``archived`` (#43).**
        ``archived`` wird an genau einer Stelle publiziert: beim
        Journal-INSERT. Ein Slot, der von ``starting`` auf ``running`` geht,
        archiviert nichts — die Kachel sprang also auf den neuen Zustand,
        während die Zeile darunter den alten weiterzeigte. ``journal:<slug>``
        feuert auf **beiden** Wegen: bei jedem Slot-Zustandswechsel
        (``bus.py``, "Journal bei JEDEM Statuswechsel mit-dirty") und bei
        jedem Journal-INSERT. Damit deckt das Ziel genau das ab, was diese
        Liste zeigt — beide Quellen —, und es brauchte dafür keinen einzigen
        neuen ``publish_state()``-Aufruf.

        Entscheidung m.rau, 2026-08-07, gegen die beiden Wege aus dem Ticket:
        ``archived`` zusätzlich aus ``_publish_live()`` zu senden hiesse, ein
        Ziel zu feuern, wenn nichts archiviert wurde — genau der Namensverfall,
        vor dem ``bus.py`` an der ``archived``-Stelle selbst warnt.

        **Ohne ``slug`` bleibt die Region stumm** statt sich am falschen Ziel
        anzumelden: ein Fragment ohne Job-Bezug hat keinen Lauf, dessen
        Journal es beobachten könnte.
        """
        nachlader = f' data-bus-refetch="{basis}/runs"' if basis else ""
        ziel = f' data-bus="journal:{_e(slug)}"' if slug else ""
        return f'<div id="runs"{ziel}{nachlader}>{inneres}</div>'

    if not liste.tiles and not liste.runs:
        # Kein Verweis mehr auf den Archive-Screen (m.rau/bibi#130): der ist
        # gestrichen, und ein Text, der auf einen Screen zeigt, den es nicht
        # gibt, ist derselbe tote Weg wie ein toter Link — nur ohne href, also
        # ohne dass ein Routen-Test ihn faende.
        return _region('<div class="empty">This job is unknown on both sides — '
                       'no slot, no runs. It may have been renamed.</div>')
    aus = [_runs_filterzeile(liste, basis=basis, aktiv=aktiv, reach=reach)]
    if not liste.runs:
        return _region("".join([*aus, '<div class="empty">No runs yet — trigger '
                                "one with START, or wait for the schedule.</div>"]))
    aus.append('<table class="runs"><thead><tr>'
               '<th class="mark"></th><th>TIME</th><th>SRC</th><th>STATUS</th>'
               "<th>EXIT</th><th>RUNTIME</th><th>COMMIT</th><th></th>"
               "</tr></thead><tbody>")
    for tag, laeufe in jobs_view.by_day(liste.runs, ts_key="sort_at"):
        aus.append(f'<tr class="day"><td colspan="8">{_e(tag)}</td></tr>')
        for r in laeufe:
            aus.append(_run_zeile(r, basis=basis))
    aus.append("</tbody></table>")
    # Der Knopf erscheint **nur**, wenn es wirklich mehr gibt, und er erweitert
    # um eine Menge statt um einen Tag — `weiter` ist das Fenster, das die
    # nächsten zehn Einträge trägt (§5.3, `jobs_view.naechstes_fenster()`).
    if job_uid and weiter and weiter != days:
        aus.append(_mehr_tage(basis, aktiv or {}, weiter))
    return _region("".join(aus))


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


def _run_zeile(r: dict, *, basis: str = "") -> str:
    """Eine Zeile der Lauf-Liste plus ihr (zugeklappter) Ausklappbereich.

    ``basis`` ist ``/-/jobs/<job_uid>`` und trägt den Weg zu den Attributen
    dieses Laufs (#40). Er steht **nur an archivierten Läufen**: ein Lauf im
    Slot hat keine Journal-Zeile und damit keinen Snapshot — ein Link dorthin
    wäre ein toter Knopf. Dass die Ansicht erst ab der Archivierung existiert,
    ist eine Lücke der Ablage und nicht der Anzeige; sie ist als eigenes
    Ticket festgehalten statt hier stillschweigend überbrückt.
    """
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
    attrs = ""
    if basis and not im_slot and r.get("id") is not None:
        attrs = (f' <a class="cta" href="{basis}/runs/{_e(r.get("id"))}/attrs">'
                 "[attrs]</a>")
    return (
        f'<tr class="{"run run-in-slot" if im_slot else "run"}">'
        f'<td class="mark">{marke}</td>'
        f'<td class="t" data-ts="{r.get("sort_at") or ""}">'
        f'{_abs_time(r.get("sort_at"))}</td>'
        f'<td class="src">{_e(r.get("src"))}</td>'
        f'<td>{_e(st)}{" &middot; " + _e(rs) if rs else ""}</td>'
        f'<td>{_e(r.get("exit_code"))}</td>'
        # **Ueber `_duration_cell()`, nicht an ihm vorbei** (#123). Die
        # Entscheidung "tickt oder tickt nicht" haengt an `finished_at`, und
        # sie steht dort schon. Hier stand ein direkter Aufruf ohne Anker — es
        # gibt zwei Zeilenbauer fuer Laeufe, und der erste Anlauf hat nur den
        # anderen erreicht. Dieselbe Fehlerform wie #96.
        f'<td>{_duration_cell(r)}</td>'
        f'<td>{_e((r.get("commit_sha") or "")[:7])}</td>'
        f'<td><button class="cta run-show" {holen} '
        f'data-run="{_e(r.get("run_id"))}">[show]</button>{attrs}</td>'
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
                       aktiv: dict | None = None, weiter: int | None = None,
                       public_host: str = "localhost") -> str:
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
    # `display_kind()`, nicht `spec["kind"]` (#96, vierte Fundstelle): `kind`
    # ist seit PLAN-10 (Unified Job Model) **immer** `"job"` und traegt keine
    # Information mehr — s. `_effective_sched_type()`. Die Seite schrieb
    # deshalb `job` direkt neben den `[APP]`-Link, den sie aus demselben Spec
    # gerade gebaut hatte. Der Port bleibt dem CTA vorbehalten: zwei Links auf
    # dieselbe Adresse waeren eine Doppelung, kein Gewinn.
    typ = models.display_kind(spec.get("payload"), spec.get("app_port"))
    rel = f' <span class="rel">({_e(beziehung)})</span>' if beziehung else ""
    # Der Weg zum Dienst gehört in den Kopf und nicht in eine Kachel
    # (m.rau/bibi#145): `app_port` steht im MD-Frontmatter und gilt für den
    # Job, nicht für einen Lauf — eine Kachel beschreibt einen Slot und wäre
    # der falsche Ort für etwas, das auch ohne jeden Lauf gilt. Der Screen
    # führte den Link bisher überhaupt nicht; die Bedingung, die das Ticket
    # verdächtigte, sass in der alten Client-Detailseite (entfallen mit #100).
    # **Kein `[APP]`-CTA mehr im Kopf** (m.rau/bibi#104). `#145` hatte ihn
    # hierher gelegt, mit der richtigen Begruendung, dass `app_port` fuer den
    # Job gilt und nicht fuer einen Lauf. Uebersehen wurde, dass das fuer den
    # PORT stimmt und fuer die ADRESSE nicht: derselbe Port meint auf zwei
    # Knoten zwei Dienste. Der Kopf hatte dafuer nur `config.public_host()`,
    # den Knoten des Betrachters — im Mac-FE zeigte der Link auf
    # `localhost:9110`, wo nichts lief. Die Slot-Kacheln kennen ihren Knoten
    # ueber `Tile.host` und tragen den Link jetzt; ein zweiter, falscher
    # daneben waere schlechter als keiner.
    app_cta = ""
    kopf = (
        '<div class="jd-head">'
        '<a class="back" href="/-/jobs">&#9666; jobs</a>'
        f'<span class="jd-slug">{_e(slug)}</span>{rel}'
        f'<span class="jd-meta">{_e(typ)} &middot; {_e(str(trigger))}</span>'
        f'{app_cta}'
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
        f"{_header('Jobs', daemon_status, scheduler=scheduler, sub=True, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        f"{kopf}"
        # **Zwei angemeldete Regionen, beide mit ihrem Wrapper aus einer
        # Quelle** — derselben Funktion, die auch die Refetch-Route bedient
        # (der Schnitt aus m.rau/bibi#151).
        #
        # `live:<slug>` traegt die Kacheln: den Slot-Zustand und die Verben,
        # die an ihm haengen. Sie standen frueher ausserhalb, weil ein Swap die
        # Knopf-Listener gekostet haette — seit `_SLOT_JS` delegiert hoert, ist
        # das gegenstandslos (m.rau/bibi#152). Nur den Statustext nachzuladen
        # waere zu wenig gewesen: welche Verben moeglich sind, haengt am
        # Zustand, eine Leiste im alten Stand boete START zu einem laufenden
        # Job an.
        #
        # `journal:<slug>` traegt die Lauf-Liste (#43). Es hiess bis v0.7.5
        # `archived` und feuerte damit nur beim Journal-INSERT — ein Slot, der
        # von `starting` auf `running` ging, archivierte nichts, und die Zeile
        # widersprach der Kachel ueber ihr. Nachgeladen wird die Liste, nicht
        # die Seite: sonst ginge bei jedem Lauf Scroll-Position und
        # Faltzustand verloren. Den Faltzustand rettet seit #44 zusaetzlich
        # `_JOB_DETAIL_JS` ueber den Swap — der Refetch ist jetzt haeufig
        # genug, dass er sonst waehrend des Mitlesens zuklappte.
        f'{job_tiles_fragment(getattr(liste, "tiles", []), now=now, slug=slug, job_uid=_uid(slug))}'
        f'{job_runs_fragment(liste, now=now, slug=slug, job_uid=_uid(slug), days=days, reach=reach, aktiv=aktiv, weiter=weiter) if liste is not None else ""}'
        # Wie auf der Jobs-Seite (m.rau/bibi#153): ohne `_EVENTS_JS` gibt es
        # keinen Strom, an dem sich die Regionen anmelden koennten — `#tiles`
        # an `live:<slug>`, `#runs` seit #43 an `journal:<slug>`.
        f"<script>{_EVENTS_JS}</script>"
        f"<script>{_DIFF_JS}</script>"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_JOB_DETAIL_JS}</script>"
        f"<script>{_SLOT_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


#: Scheduling-Werte der Attribut-Seite in der Reihenfolge, in der sie dort
#: stehen — Trigger zuerst, dann Retry-Verhalten, dann die Fristen.
#: `hitl_timeout` stand hier bis `v0.8.2` und ist mit #129 gefallen: der Parser
#: hat es am 2026-07-04 mit `silence_timeout` zusammengelegt, `_spec_columns()`
#: kennt es seither nicht — die Seite führte ein Feld, das es fachlich nicht
#: mehr gibt und das nur noch sein eigener DEFAULT füllte.
#: **Alle** Konfigurationswerte, in der Reihenfolge, in der man sie liest (#141):
#: erst wer (`slug`) und was (`kind`/`payload`), dann wann, dann wie oft und wie
#: lange, dann womit — und ganz am Ende, woher der Job kommt.
#:
#: `slug` steht vorn, weil er die **Identität** ist und die einzige Angabe, die
#: man sonst nirgends verlässlich abliest: in der Tabelle gekürzt, in der URL
#: als `md5(slug)`. Wer ihn sicher wissen wollte, musste die MD öffnen.
#: `schedule_ref` ist die zweite Hälfte derselben Auskunft — aus welcher Datei
#: dieser Job kommt. Zusammen beantworten sie *„wie heißt der Job wirklich, und
#: warum heißt er so?"*, und das wiegt schwerer, seit der Dateiname allein die
#: Identität trägt (#143).
_ATTR_FELDER = ("slug", "kind", "payload",
                "schedule", "at",
                "attempts", "backoff",
                "defer_time", "defer_max", "error_time", "silence_timeout",
                "wall_time",
                "model", "soul", "session", "priority",
                "app_port", "app_prefix", "exec_mode", "image",
                "schedule_ref")

#: Die Konfigurationswerte eines **Laufs** (#40) sind dieselben (#141). Hier
#: stand bis `v0.8.2` eine eigene, längere Liste — die Job-Seite trug nur den
#: Zeitplan, und die Lauf-Seite musste ergänzen. Jetzt zeigen beide alles, und
#: **eine Liste statt zweier** ist der Punkt: `_ATTR_FELDER` und `_LAUF_KONFIG`
#: überschnitten sich fast vollständig, und die Warnung dazu stand im Ticket —
#: kombiniert ein Renderer die eine Liste mit der anderen Datenquelle, ist die
#: Vermischung wieder da, und sie sieht plausibel aus.
_LAUF_KONFIG = _ATTR_FELDER

#: Felder, die nur für einen bestimmten Typ etwas bedeuten (#132). Ein
#: Shell-Job trägt ein `model` aus der Job-Zeile, das für seinen Lauf keine
#: Rolle spielt — für die Frage *„warum ging dieser Lauf anders aus als jener"*
#: ist es Rauschen. Der Typ steht in `display_kind()` und muss dafür nicht neu
#: erfunden werden.
_TYP_GEBUNDEN = {
    "model": ("claude",), "soul": ("claude",), "session": ("claude",),
    "app_port": ("app",), "app_prefix": ("app",),
}


def _gilt_fuer(feld: str, snap: dict) -> bool:
    """Ob ``feld`` für einen Lauf dieses Typs überhaupt etwas aussagt (#132)."""
    from bibi.schedule.models import display_kind
    erlaubt = _TYP_GEBUNDEN.get(feld)
    if erlaubt is None:
        return True
    return display_kind(snap.get("payload"), snap.get("app_port")) in erlaubt

#: Die Laufzeit-Werte, in der Reihenfolge, in der man sie liest: erst wer und
#: wo, dann wie lange und mit welchem Ausgang, dann woran (Commit) und wohin
#: (Output). `run_id` steht zuerst, weil er den Lauf benennt.
_LAUF_LAUFZEIT = ("run_id", "status", "reason", "host", "worker", "domain",
                  "pinned_host", "started_at", "finished_at", "archived_at",
                  "exec_runtime", "exit_code", "commit_sha", "branch",
                  "output_ref")


# Hier stand `_load_more()` — der Nachlade-Knopf der bibi4-Listen. Die
# v5-Lauf-Liste laedt ueber `reach`/`weiter` nach (FE §5.3) und rendert ihren
# Knopf selbst; dieser hier hatte seit dem Umbau keinen Aufrufer mehr und auch
# keinen Test. Entfernt mit `#100`.


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
        f"{_header('Jobs', daemon_status, scheduler=scheduler, sub=True, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        '<div class="jd-head">'
        f'<a class="back" href="/-/jobs/{_uid(slug)}">&#9666; back to job</a>'
        f'<span class="jd-slug">{_e(slug)}</span>'
        '<span class="jd-meta">attributes</span>'
        "</div>"
        f'<div class="attrs">{"".join(zeilen)}</div>'
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )


def _attr_zeile(feld: str, wert, herkunft: str, *, geerbt: bool = False) -> str:
    """Eine Zeile der Lauf-Attribute: Feld, Wert, Herkunft.

    Dieselbe Form wie auf der Job-Attribut-Seite, um eine dritte Spalte
    erweitert. Zwei Signale wie dort: die Herkunft steht als Wort da **und**
    färbt den Wert — Dimmung allein geht in hellen Themes verloren.

    ``geerbt`` trägt die **zweite** Unterscheidung (#132): ein Vorgabewert wird
    gedimmt und in Klammern gesetzt, genau wie auf der Job-Seite. Sie ist von
    der Herkunft unabhängig — ein geerbter Wert kann trotzdem vom heutigen
    Job-Wert abweichen, und dann sagen beide Signale verschiedenes über
    dieselbe Zeile. Genau dafür sind es zwei.
    """
    klasse = {"job": "attr-default", "run": "attr-set", "runtime": "attr-set"}[herkunft]
    if geerbt:
        klasse = "attr-default"
    return (f'<div class="attr-row"><span class="attr-key">{_e(feld)}</span>'
            f'<span class="{klasse}">{_e(wert)}</span>'
            f'<span class="attr-src muted">{herkunft}</span></div>')


def run_attrs_page_v5(*, slug: str, lauf: dict, job_spec: dict, now: float,
                      defaults: dict | None = None,
                      daemon_status: dict | None = None,
                      git_status: dict | None = None, host_url: str | None = None,
                      scheduler: dict | None = None,
                      scheduler_stale_since: float | None = None) -> str:
    """Die Attribute **eines Laufs** (#40) — drei Schichten in einer Tabelle.

    **Warum eine Tabelle und nicht zwei Ansichten:** die Frage, die hierher
    führt, lautet *warum ging dieser Lauf anders aus als jener*. Getrennte
    Ansichten beantworten sie in zwei Blicken; eine Spalte „woher" beantwortet
    sie in einem. Das liegt außerdem näher an dem, was ``/attrs`` für den Job
    schon tut.

    **Standbild, nicht Bus-gebunden.** Bei einem laufenden Lauf wüchsen die
    Laufzeitwerte — ob die Ansicht daran hängt, entscheidet Welle 4 und soll
    hier nicht vorweggenommen werden. Die Frage stellt sich ohnehin erst dann:
    es gibt diese Seite nur für archivierte Läufe (siehe unten).

    **Was die Herkunftsspalte nicht wissen kann, behauptet sie auch nicht.**
    ``journal.snapshot`` friert die Job-Konfiguration beim **Archivieren** ein,
    nicht beim Start — und nach Archivierungsregel A2 bleibt ein terminaler
    Lauf im Slot stehen, bis jemand START oder RESET auslöst, der Abstand
    wächst also beliebig. Ein Unterschied zwischen Snapshot und heutiger
    Job-Konfiguration heißt deshalb *„dieser Lauf hatte einen anderen Wert"*
    und nicht *„der Lauf hat ihn gesetzt"*: ob der Lauf abwich oder der Job
    sich danach änderte, ist aus der Ablage nicht zu entscheiden. Der Satz
    steht auf der Seite, nicht nur hier — dieselbe Ehrlichkeit, mit der die
    Job-Seite ihr „geerbt" als Näherung ausweist.

    Die volle Erhebung, auf der das beruht, liegt im Bibi5-Case unter
    ``20260811.Lauf-Attribute.md``.
    """
    import json as _json

    from bibi.schedule.models import job_uid as _uid

    try:
        schnapp = _json.loads(lauf.get("snapshot") or "{}")
    except (ValueError, TypeError):
        # Ein unlesbarer Snapshot ist kein Grund, die Laufzeitwerte
        # zurückzuhalten — sie stehen in eigenen Spalten und sind unberührt.
        schnapp = {}

    zeilen = []
    for feld in _LAUF_LAUFZEIT:
        wert = lauf.get(feld)
        if wert in (None, ""):
            continue
        if feld in ("started_at", "finished_at", "archived_at"):
            wert = _uhrzeit(wert, now)
        elif feld == "exec_runtime":
            wert = _human_duration(wert)
        zeilen.append(_attr_zeile(feld, wert, "runtime"))

    vorgabe = defaults or {}
    for feld in _LAUF_KONFIG:
        wert = schnapp.get(feld)
        if wert is None:
            continue
        # Felder, die für den Typ dieses Laufs nichts bedeuten, stehen nicht in
        # der Tabelle (#132) — das Modell eines Shell-Jobs beantwortet keine
        # Frage, die hierher führt.
        if not _gilt_fuer(feld, schnapp):
            continue
        # `run`, wo der Lauf einen anderen Wert trug als der Job heute.
        # `job`, wo beide dasselbe sagen.
        abweichend = feld in job_spec and job_spec.get(feld) != wert
        # **Zwei Fragen, zwei Signale, nebeneinander statt ineinander** (#132).
        # `SOURCE` beantwortet *Lauf gegen Job*; die Klammern beantworten
        # *gesetzt gegen Vorgabe*. Die Job-Seite kann das seit jeher, die
        # Lauf-Seite hatte es nicht übernommen — wer von der einen zur anderen
        # wechselte, verlor eine Auskunft, die einen Schritt vorher noch da war.
        geerbt = feld in vorgabe and wert == vorgabe[feld]
        zeilen.append(_attr_zeile(feld, f"({wert})" if geerbt else wert,
                                  "run" if abweichend else "job",
                                  geerbt=geerbt))

    if not zeilen:
        zeilen.append('<div class="empty">Nothing recorded for this run — its '
                      "journal row carries no snapshot.</div>")

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bibi &middot; {_e(slug)} &middot; run attributes</title>"
        f"<style>{_CSS}</style>"
        f'<script src="/-/static/htmx-1.9.12.min.js"></script>'
        "</head><body>"
        f"{_header('Jobs', daemon_status, scheduler=scheduler, sub=True, scheduler_now=(scheduler or {}).get('now'), now=now)}"
        f"{feed_status_fragment(daemon_status, git_status, host_url, now, scheduler=scheduler, scheduler_stale_since=scheduler_stale_since)}"
        '<div class="jd-head">'
        f'<a class="back" href="/-/jobs/{_uid(slug)}">&#9666; back to job</a>'
        f'<span class="jd-slug">{_e(slug)}</span>'
        f'<span class="jd-meta">run attributes &middot; {_e(lauf.get("run_id") or "")}</span>'
        "</div>"
        '<div class="attrs-head"><span class="attr-key">FIELD</span>'
        '<span>VALUE</span><span class="attr-src">SOURCE</span></div>'
        f'<div class="attrs">{"".join(zeilen)}</div>'
        # Hier stand bis `v0.8.2` ein Vorbehalt: der Snapshot entstehe beim
        # Archivieren und nicht beim Start, ein `run` heisse deshalb nur
        # „anderer Wert als heute" und nicht „der Lauf setzte ihn". Mit #129
        # friert der Snapshot bei START ein — der Satz ist gegenstandslos
        # geworden und faellt, statt als beruhigende Fussnote stehenzubleiben.
        # Die frühere Monospace-Klasse steht hier nicht mehr: sie hatte nie eine
        # CSS-Regel und blieb nur deshalb unbemerkt, weil ihre Escaping-Form sie
        # vor dem Klassen-Wächter aus #94 verbarg. Die Seite ist ohnehin
        # durchgehend Monospace.
        '<p class="muted attrs-note">A <b>run</b> means this '
        "run held a different value than the job holds today: its attributes "
        "were frozen when it started. Values in parentheses are defaults the "
        "job never set.</p>"
        f"<script>{_CLOCK_JS}</script><script>{_DURATION_JS}</script>"
        f"<script>{_OPS_HANDLES_JS}</script>"
        f"<script>{_THEME_JS}</script>"
        "</body></html>"
    )
