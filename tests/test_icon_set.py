"""Die App-Bar hat einen Icon-Satz (`#159`).

**Befund m.rau zum ausgelieferten `v0.8.7`**, mit `P1` von ihm selbst vergeben:
*„die verwendeten Icons sind unterschiedlich gross, und dadurch unruhig und
unschön"*.

**Woran es lag: es gab keinen Satz.** ``_ops_handles()`` rendert die Zeichen als
**Textknoten** aus der Systemschrift — ``⟳`` (U+27F3), ``◐`` (U+25D0), ``◷``
(U+25F7) und ``●`` (U+25CF). Vier Unicode-Blöcke, vier Zeichnungen von vier
Schriftgestaltern, verschiedene Grundlinien und verschiedene optische Größen.

**Keine CSS-Regel kann das einfangen**, weil es nichts Gemeinsames gibt, an dem
man sie ausrichten könnte: Glyphenbreite, Strichstärke und Höhe kommen aus der
Schrift, nicht aus dem Dokument. Was m.rau sieht, ist keine Nachlässigkeit bei
der Auswahl, sondern das Fehlen eines Satzes.

## Warum das vor dem Layout-Case liegt

Der nächste Case spielt jeden Screen in Layout-Varianten durch. **Varianten sind
nicht vergleichbar, solange die Zeichen darin ihre Größe wechseln** — man
beurteilt dann die Schrift und hält es für das Layout.

## Was diese Datei prüft

Die vier Teile der Zusage, jeder einzeln: **eine Quelle**, **dieselbe
Kantenlänge**, **dieselbe Strichstärke**, **``currentColor``**. Dazu die
Gegenprobe, dass kein Zeichen über eine externe Adresse kommt — sonst wäre ein
Fix grün, der die Unruhe gegen eine Netzabhängigkeit tauscht.
"""

from __future__ import annotations

import re

from bibi.controller import render

#: Ein Knoten mit Scheduler — sonst ist der Maintenance-Knopf `disabled` und
#: der Verbindungspunkt zeigt „kein Gegenüber". Beide sollen ihr Zeichen auch
#: dann tragen; geprüft wird das in `test_every_handle_keeps_its_icon_when_disabled`.
_MIT_SCHEDULER = {"maintenance": False, "roles": ["scheduler"]}


def _svgs(html: str) -> list[str]:
    return re.findall(r"<svg\b.*?</svg>", html, re.S)


def test_every_handle_in_the_app_bar_carries_an_svg():
    """**Der Rot-Schritt**: für jedes Bedienelement ein ``svg`` mit gemeinsamer
    Klasse — heute findet der Test Textknoten."""
    html = render._ops_handles(_MIT_SCHEDULER)
    for eid in ("rescan", "maint", "tfmt", "conn-dot"):
        m = re.search(rf'id="{eid}".*?</(?:button|span)>', html, re.S)
        assert m, f"{eid} fehlt in der App-Bar"
        assert "<svg" in m.group(0), f"{eid} traegt kein svg: {m.group(0)!r}"
        # `class="ico ..."`: der Rescan-Knopf traegt drei Zeichen, die sich
        # einen Platz teilen, und jedes fuehrt neben `ico` noch seine eigene
        # Klasse. Die gemeinsame steht in jedem davon zuerst.
        assert re.search(r'class="ico(?: [\w-]+)?"', m.group(0)), \
            f"{eid} traegt nicht die gemeinsame Klasse: {m.group(0)!r}"


def test_no_handle_is_a_glyph_from_the_system_font():
    """Die andere Hälfte desselben Satzes: die vier Zeichen sind **weg**.

    Ohne diese Prüfung wäre ein Markup grün, das ein ``svg`` *neben* die alte
    Glyphe setzt — und dann stünde beides da.
    """
    html = render._ops_handles(_MIT_SCHEDULER)
    for zeichen, name in (("⟳", "U+27F3 ⟳"), ("◐", "U+25D0 ◐"),
                          ("◷", "U+25F7 ◷"), ("●", "U+25CF ●")):
        assert zeichen not in html, f"{name} steht noch als Textzeichen in der App-Bar"


def test_all_icons_share_one_edge_length():
    """*„trägt dieselbe Kantenlänge"* — wörtlich aus der Zusage.

    Gemessen an der ``viewBox``: sie ist das Koordinatensystem, in dem die Pfade
    gezeichnet sind. Zwei Icons mit verschiedener ``viewBox`` sehen bei gleicher
    CSS-Größe verschieden groß aus, und genau das war der Befund.
    """
    boxen = {re.search(r'viewBox="([^"]+)"', s).group(1) for s in _svgs(
        render._ops_handles(_MIT_SCHEDULER))}
    assert len(boxen) == 1, f"verschiedene viewBox in einer Leiste: {boxen}"


def test_all_stroked_icons_share_one_stroke_width():
    """*„und dieselbe Strichstärke"* — die zweite Hälfte derselben Zusage.

    Ausgenommen ist, was gar keinen Strich hat: der Verbindungspunkt ist
    **gefüllt** und trägt deshalb keine Strichstärke, die abweichen könnte.
    """
    staerken = {m.group(1) for s in _svgs(render._ops_handles(_MIT_SCHEDULER))
                if (m := re.search(r'stroke-width="([^"]+)"', s))}
    assert len(staerken) == 1, f"verschiedene Strichstaerken: {staerken}"


def test_every_icon_follows_the_current_colour():
    """*„und seine Farbe folgt ``currentColor``"*.

    Das ist die Bedingung dafür, dass der Verbindungspunkt weiterhin rot,
    orange und grün werden kann, ohne dass das Icon davon weiß: die Farbe kommt
    aus der Klasse am Element, nicht aus dem SVG.
    """
    svgs = _svgs(render._ops_handles(_MIT_SCHEDULER))
    assert svgs, "keine Icons — der Test waere sonst ueber eine leere Liste gruen"
    for s in svgs:
        assert "currentColor" in s, s[:160]
        assert not re.search(r'(?:stroke|fill)="#', s), f"feste Farbe im Icon: {s[:160]}"


def test_the_icon_size_comes_from_the_stylesheet_in_em():
    """Die Größe steht im CSS und in ``em`` — nicht als Attribut am SVG.

    **Das ist der Unterschied zwischen „gleich groß" und „mitwachsend".** Ein
    ``width="24"`` am Element wäre auch überall gleich und würde bei jeder
    Schriftgröße daneben stehen; ``em`` bindet das Zeichen an die Zeile, in der
    es steht.
    """
    for s in _svgs(render._ops_handles(_MIT_SCHEDULER)):
        # Nur die Attribute am `<svg>` selbst, und `stroke-width` ist keins
        # davon — der Bindestrich ist eine Wortgrenze, ein `\b(?:width|height)`
        # trifft ihn mit. Die erste Fassung dieses Tests tat genau das.
        kopf = s[:s.index(">") + 1]
        assert not re.search(r'(?<![-\w])(?:width|height)=', kopf), \
            f"feste Groesse am svg: {kopf}"
    css = render._CSS
    m = re.search(r"\.ico\s*\{[^}]*\}", css)
    assert m, ".ico fehlt im Stylesheet"
    assert "em" in m.group(0), m.group(0)


def test_no_icon_is_loaded_over_the_network():
    """**Die Gegenprobe.** Ohne sie wäre ein Fix grün, der die Unruhe gegen eine
    Netzabhängigkeit tauscht.

    Das FE liefert sich seit jeher selbst aus; ein CDN-Verweis in der App-Bar
    hieße, dass die Leiste ohne Netz keine Zeichen mehr hat — auf einem Knoten,
    dessen Zweck es ist, über Netzprobleme Auskunft zu geben.
    """
    html = render._ops_handles(_MIT_SCHEDULER)
    assert "http" not in html, html
    assert not re.search(r'\b(?:src|href)=', html), html


def test_every_handle_keeps_its_icon_when_disabled():
    """Ein Knoten ohne Scheduler trägt dieselben Zeichen, nur ausgegraut.

    Dieselbe Toggle-Menge auf jedem Knoten — nicht verfügbare Funktionen
    ausgegraut statt ausgeblendet. Ohne diese Prüfung könnte der Icon-Satz an
    genau dem Knoten fehlen, an dem am ehesten jemand nachsieht, warum nichts
    läuft.
    """
    html = render._ops_handles({"roles": ["synchronizer", "controller"]})
    assert len(_svgs(html)) >= 3, html
    assert "disabled" in html
