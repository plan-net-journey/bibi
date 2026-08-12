"""Der Umschalter zwischen absolut und relativ (#139).

**Der Ort ist im `v0.8.6`-Durchgang gefunden worden, und der Fund lautete: es
gibt ihn nicht.** `_ops_handles()` rendert drei Bedienelemente — Rescan,
Maintenance, Verbindungspunkt. Das `◐`, auf das m.rau klickt, *ist*
Maintenance. Ein zweites Element war nie dahintergelegt worden.

**Der Docstring von `_header()` behauptete das Gegenteil** und zählte die
Toggles als *„FOLLOW/RESCAN/MAINT/Datum-Uhrzeit/THEME"* auf — eine Aufzählung
aus der bibi4-Zeit, die den v5-Umbau überlebt hat, während die Funktion es
nicht tat. **Deshalb ist die Suche zweimal ergebnislos geblieben und trotzdem
plausibel gewesen.**

Die Vorgabe aus `#30` bleibt und grenzt das ein: *„ein absoluter Zeitpunkt
bleibt nach einem Screenshot wahr."* Die Entscheidung vom 2026-08-03 galt der
**Vorgabe** — absolut ist der Vorgabewert und bleibt es. Sie galt nicht gegen
einen Umschalter.
"""

from __future__ import annotations

import re

from bibi.controller import render

NOW = 1_000_000.0


def test_a_point_in_time_carries_its_anchor():
    """Ohne Anker kann der Browser nichts umrechnen — und `_uhrzeit()` gab bis
    `v0.8.6` nackten Text zurück, an zwölf Aufrufstellen."""
    html = render._uhrzeit(NOW - 90, NOW)
    assert 'data-tp="' in html, html
    assert re.search(r'data-abs="\d{2}:\d{2}:\d{2}"', html), html
    assert re.search(r">\d{2}:\d{2}:\d{2}<", html), html


def test_the_absolute_form_is_what_the_server_renders():
    """**Der Vorgabewert bleibt absolut** (#30). Der Umschalter ist eine
    Zugabe, keine Umkehr — ohne JavaScript steht die Uhrzeit da."""
    html = render._uhrzeit(NOW - 90, NOW)
    sichtbar = re.sub(r"<[^>]+>", "", html)
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", sichtbar), sichtbar


def test_a_missing_time_stays_a_bare_dash():
    """**Die Gegenprobe, und sie ist die gefährlichste Stelle des Umbaus.**

    `_sched()` ersetzt einen Strich durch `offline` und prüft dafür auf
    Gleichheit mit `—`. Käme der Strich in einer Hülle, wäre er nie mehr
    gleich, und `#147` fiele stillschweigend aus — an einer Stelle, die kein
    Test dieses Umbaus ansieht.
    """
    assert render._uhrzeit(None, NOW) == "—"


def test_the_offline_word_still_replaces_the_dash():
    """Dieselbe Zusage eine Ebene höher, am echten Screen — weil die Gefahr
    oben nicht in `_uhrzeit()` sichtbar wird, sondern erst hier."""
    from bibi.controller.jobs_view import build_rows

    zeilen = build_rows(local=[{"slug": "a", "schedule": "0 * * * *",
                                "payload": "echo hi", "repo_path": "case/x/a.md"}],
                        scheduler=[], journal=[{"slug": "a"}], now=NOW,
                        scheduler_offline=True)
    html = render.jobs_screen(zeilen, now=NOW, scheduler_offline=True)
    zeile = re.search(r'<tr data-row=.*?</tr>', html, re.S).group(0)
    assert zeile.count("offline") >= 3, zeile


def test_the_ops_bar_carries_a_fourth_handle():
    """Drei waren es, und keiner davon war der Zeitumschalter."""
    html = render._ops_handles({'roles': ['controller'], 'connect': {'ok': True}})
    assert 'id="tfmt"' in html, html
    assert html.count('<button') == 3, 'Rescan, Maintenance und der Umschalter'


def test_the_ticker_rewrites_points_in_time():
    """**Der Zustand ueberlebt den Swap, ohne dass jemand ihn wiederherstellt.**

    Header und Tabelle werden vom Bus ausgetauscht; ein Umschalter, der nach
    jedem Job-Wechsel zurueckspringt, ist schlimmer als keiner. Der vorhandene
    Ticker scannt das DOM in **jedem** Intervall neu — die umgeschalteten
    Zeitpunkte laufen deshalb ueber denselben Weg wie die Dauern, statt einen
    eigenen Wiederherstellungspfad zu brauchen.
    """
    js = render._DURATION_JS
    assert 'data-tp' in js, 'der Ticker kennt die Zeitpunkte nicht'
    assert 'data-abs' in js, 'ohne die absolute Form gibt es keinen Rueckweg'


def test_the_choice_outlives_a_reload():
    js = render._OPS_HANDLES_JS + render._DURATION_JS
    assert 'localStorage' in js, 'die Wahl ueberlebt kein Neuladen'
