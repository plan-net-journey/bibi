"""Maintenance wird am Verbindungspunkt geschaltet (`#161`).

**Anzeige und Bedienung fallen zusammen.** Der Verbindungspunkt zeigt den
Maintenance-Modus bereits — seit `#70`, und aus einem benannten Grund: *„wir
haben uns gefragt, warum der Job nicht startet. Wir hatten den Maintenance Mode
übersehen."* Er wird jetzt zu dem Element, an dem man ihn auch schaltet.

> *„Maintenance Mode soll durch Klick auf ● ein-/ausgeschaltet werden."* — auf
> Rückfrage präzisiert: *„ich meine nur das Icon, das schon rot, grün, orange
> ist. Da klickt man drauf. Und bei Maintenance Mode erscheint dann orange.
> Offline ist rot. Und connected + nicht maintenance ist grün. So wie jetzt
> auch."* (m.rau)

**Die drei Farben bleiben unverändert** — ausdrücklich bestätigt. Was fällt,
ist der eigene Knopf: `◐` geht ersatzlos, und damit erledigt sich die in `#33`
festgehaltene Doppelvergabe von selbst. Die Handles gehen von vier auf drei
zurück: Rescan, Zeitformat, Verbindung/Maintenance.

**Dazu der Modus als Wort in der Kopf-Karte** — `up 41 min ago, maintenance
mode`, wortwörtlich der Bau aus `#156`: zwei Felder in einer Zeile, jedes mit
eigener Regel, wie `18:11:33, auto-sync off`. `#33` trägt den Zusatz bereits:
*„Maintenance als Wort — aber nur solange er anliegt."*
"""

from __future__ import annotations

import re

from bibi.controller import render

#: Ein Client mit Scheduler — er darf schalten (`#69`).
_CLIENT = {"roles": ["synchronizer", "controller", "connect"],
           "connect": {"ok": True, "last_at": 1.0}}
#: Ein Knoten ohne jeden Scheduler — es gibt nichts zu schalten.
_ALLEIN = {"roles": ["synchronizer", "controller"]}


def test_the_bar_carries_three_handles_not_four():
    """**Der erste Rot-Schritt**: heute findet der Test vier Handles.

    `◐` fällt ersatzlos weg, weil der Schalter an den Verbindungspunkt wandert.
    Damit stehen nicht mehr zwei halbgefüllte Kreise in derselben Leiste — die
    Kollision, die `#33` seit dem Board-Umzug führt.
    """
    html = render._ops_handles(_CLIENT)
    assert 'id="maint"' not in html, html
    for eid in ("rescan", "tfmt", "conn-dot"):
        assert f'id="{eid}"' in html, eid


def test_the_connection_dot_is_a_control():
    """**Der zweite Rot-Schritt**: der `conn-dot` ist heute ein ``span``.

    Ein Element, auf das man klicken soll, muss ein Bedienelement sein — sonst
    erreicht es keine Tastatur und kein Screenreader nennt es als solches.
    """
    html = render._ops_handles(_CLIENT)
    punkt = re.search(r"<(\w+)[^>]*id=\"conn-dot\"", html)
    assert punkt, html
    assert punkt.group(1) == "button", f"conn-dot ist ein <{punkt.group(1)}>"


def test_the_three_colours_stay_what_they_were():
    """Ausdrücklich bestätigt: rot getrennt, orange Maintenance, grün verbunden.

    Die Frage nach einem zweifarbigen Icon-Paar ist damit gegenstandslos — und
    diese Prüfung hält fest, dass der Umbau am Verhalten nichts ändert, nur am
    Element.
    """
    assert 'class="conn-dot ok"' in render._ops_handles(
        _CLIENT, scheduler={"maintenance": False})
    assert 'class="conn-dot warn"' in render._ops_handles(
        _CLIENT, scheduler={"maintenance": True})
    aus = dict(_CLIENT, connect={"ok": False, "last_at": 1.0})
    assert 'class="conn-dot bad"' in render._ops_handles(aus)


def test_without_a_scheduler_the_dot_is_not_a_switch():
    """**Die Gegenprobe, und sie ist der Grund für den Test.**

    Ein Klick auf einen roten Punkt darf keine Anfrage auslösen, die niemand
    beantworten kann. Ohne diese Prüfung wäre ein Fix grün, der jedem Knoten
    einen Schalter gibt, der ins Leere greift.

    Die Erreichbarkeitsregel bleibt die von `#69`: schaltbar, wenn dieser
    Knoten *einen* Scheduler hat — konfiguriert, oder als der er selbst läuft.
    """
    html = render._ops_handles(_ALLEIN)
    punkt = re.search(r'<button[^>]*id="conn-dot"[^>]*>', html)
    assert punkt, html
    assert "disabled" in punkt.group(0), punkt.group(0)


def test_the_switch_talks_to_the_scheduler_not_to_itself():
    """Der Zustand kommt aus der **echten Server-Antwort**, nicht optimistisch.

    Das war schon die Bauart des alten Knopfes und bleibt es: bei einem Fehler
    steht danach der Zustand, den der Server nennt, und nicht der, den man sich
    gewünscht hat.
    """
    js = render._OPS_HANDLES_JS
    assert "/-/ui/ops/maintenance" in js
    assert "d.maintenance" in js, "der Zustand kommt nicht aus der Antwort"
    assert "conn-dot" in js


def test_the_scheduler_card_says_the_word_while_it_lasts():
    """**Der dritte Rot-Schritt**: der Text steht heute nicht in der Karte.

    `up 41 min ago, maintenance mode` — wortwörtlich der Bau aus `#156`: zwei
    Felder in einer Zeile, jedes mit eigener Regel, wie `18:11:33, auto-sync
    off`.
    """
    html = render.status_header(
        {"roles": ["controller"], "connect": {"ok": True, "last_at": 1.0}}, {},
        scheduler={"maintenance": True, "started_at": 100.0}, now=200.0)
    assert "maintenance mode" in html, html


def test_the_word_goes_away_with_the_mode():
    """*„Maintenance als Wort — aber nur solange er anliegt"* (`#33`).

    **Die Gegenprobe zur vorigen, und ohne sie wäre ein Fix grün, der das Wort
    dauerhaft hinschreibt.** Ein Hinweis, der immer dasteht, ist keiner — und
    dieser hier soll gerade die Frage beantworten, warum nichts startet.
    """
    html = render.status_header(
        {"roles": ["controller"], "connect": {"ok": True, "last_at": 1.0}}, {},
        scheduler={"maintenance": False, "started_at": 100.0}, now=200.0)
    assert "maintenance" not in html.lower(), html
