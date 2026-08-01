"""Grundpalette und Token-Sätze (m.rau/bibi#68).

Bis hierher hatte die UI **keine Grundpalette**: ``color-scheme: light dark``
überließ Grund und Textfarbe dem Browser, kein einziger ``--bg``-Token
existierte, und alles Neutrale lief über den Alpha-Grau-Trick (``#8881`` …
``#888``), der theme-blind funktioniert. Genau deshalb musste bibi bisher kein
zweites Theme pflegen.

Echte Token-Sätze beenden das: ab jetzt sind es **zwei Themes, dauerhaft**. Das
ist der Preis, der in die Entscheidung gehörte, und diese Datei ist der Nachweis,
dass er auch bezahlt wird — ein Theme, das nur zur Hälfte gezeichnet ist, fällt
sonst erst dem auf, der es benutzt.

Die Werte stammen aus Teil 5 der Design-Studie
(``vault/case/20260621.Bibi4/20260729.bibi4DesignStudie-77178146``), der einzigen
Palette, an der eine Gestaltungsentscheidung tatsächlich getroffen wurde
(01 Kontenblatt, 2026-07-31): warmes Papier gegen warmes Anthrazit, Terracotta
als einziger Marken-Akzent, gedimmtes Grau als Hauptträger, Semantikfarben nur
an Zustandsstellen.
"""

from __future__ import annotations

import re

from bibi.controller import render


def _block(selector: str) -> str:
    """Der Inhalt genau eines Token-Blocks aus ``_CSS``.

    Über den Selektor statt über Zeilennummern: die Blöcke wandern beim
    Umbauen, ihre Selektoren nicht.
    """
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", render._CSS, re.S)
    assert m, f"kein Block für {selector!r}"
    return m.group(1)


def _tokens(block: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);",
                                                block)}


# ── Beide Themes sind gezeichnet, keines abgeleitet ─────────────────────────


def test_light_defines_a_full_token_set():
    """Der Grund, aus dem alles andere folgt: es gibt eine Grundpalette."""
    t = _tokens(_block(":root"))
    for name in ("--bg", "--text", "--dim", "--faint", "--line", "--brand",
                 "--green", "--blue", "--amber", "--red"):
        assert name in t, f"{name} fehlt im LIGHT-Satz"
    assert t["--bg"] == "#faf9f5"        # warmes Papier
    assert t["--brand"] == "#c25f3c"     # Terracotta


def test_dark_is_drawn_not_derived():
    """LIGHT und DARK tragen **dieselbe** Token-Menge.

    Das ist die Anforderung des Plans wörtlich: *„LIGHT und DARK sind beide
    durchgezeichnet, nicht eines davon abgeleitet."* Ein DARK-Satz, der nur
    einzelne Werte überschreibt, erbt den Rest aus LIGHT — und dann ist genau
    das nicht gestaltet, was niemand nachgesehen hat.
    """
    light = set(_tokens(_block(":root")))
    dark = set(_tokens(_block(':root[data-theme="dark"]')))
    assert light == dark, f"nur in einem Satz: {light ^ dark}"


def test_dark_ground_is_warm_anthracite():
    t = _tokens(_block(':root[data-theme="dark"]'))
    assert t["--bg"] == "#1c1b18"
    assert t["--brand"] == "#d97757"


def test_brand_and_error_keep_their_hue_distance():
    """Terracotta kann nur **eine** Bedeutung tragen, und sie braucht Abstand
    zum Fehlerton.

    Der Befund stammt aus der Studie: im ersten Entwurf lagen Marke (17°) und
    Fehler (9°) nur 8° auseinander, und im gerenderten Bild las sich die halbe
    Tabelle als Fehlerzustand. Korrigiert wurde der Fehlerton, nicht die Marke.
    Ein Test, weil eine Farbe, die man später „nur ein bisschen" nachzieht,
    genau hier wieder hineinläuft.
    """
    import colorsys

    def hue(hex6: str) -> float:
        r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return colorsys.rgb_to_hls(r, g, b)[0] * 360

    for sel in (":root", ':root[data-theme="dark"]'):
        t = _tokens(_block(sel))
        d = abs(hue(t["--brand"]) - hue(t["--red"]))
        assert d >= 9.0, f"{sel}: Marke und Fehlerton nur {d:.1f}° auseinander"


# ── Der Toggle schaltet Token, nicht nur color-scheme ───────────────────────


def test_theme_toggle_switches_token_sets():
    """Der ``data-theme``-Toggle schrieb bisher nur ``color-scheme`` um. Mit
    Token muss er Token-Sätze umschalten — sonst ändert ein Klick die Farben
    des Browsers und nicht die der Seite."""
    assert ':root[data-theme="dark"]' in render._CSS
    assert ':root[data-theme="light"]' in render._CSS


def test_a_dark_browser_gets_dark_without_a_stored_choice():
    """Wer nichts gewählt hat, bekommt, was sein System sagt — und eine
    ausdrückliche Wahl schlägt das."""
    assert "prefers-color-scheme: dark" in render._CSS
    css = render._CSS
    # Die ausdrückliche Wahl steht NACH der Media-Query, sonst verliert sie.
    assert css.index("prefers-color-scheme: dark") < css.index(
        ':root[data-theme="light"]')


def test_body_carries_the_ground_itself():
    """Grund und Textfarbe kommen aus der Palette, nicht mehr vom Browser."""
    body = _block("body")
    assert "var(--bg)" in body
    assert "var(--text)" in body


def test_body_is_monospace():
    """Die Monospace-Frage ist in der Studie **gemessen** beantwortet: voll
    Monospace passt bei 14 px in die bestehenden 64 rem (breiteste Zeile der
    UI: 851 px von 1024). Der Preis ist 14 px Grundgröße statt 15 px."""
    body = _block("body")
    assert "monospace" in body
    assert "14px" in body


# ── Der Alpha-Grau-Trick ist abgelöst ───────────────────────────────────────


def test_no_bare_colour_literals_outside_the_token_sets():
    """Kein Hex-Literal mehr außerhalb der Token-Blöcke.

    Das ist der eigentliche Nachweis, dass die Palette *wirkt* statt nur
    dazustehen: solange irgendwo ``#888`` steht, hat diese Stelle kein Theme,
    sondern den Alpha-Grau-Trick — und der ist der Grund, warum es bisher keine
    zwei Themes gab. Ein einzelnes übersehenes Literal fällt sonst genau in dem
    Modus auf, in dem niemand nachsieht.
    """
    # Kommentare sind kein CSS. Sie zu durchsuchen hieße, den Text zu prüfen
    # statt die Wirkung — und ausgerechnet die Begründung, warum der
    # Alpha-Grau-Trick abgelöst wurde, darf ihn dann nicht mehr beim Namen
    # nennen.
    css = re.sub(r"/\*.*?\*/", "", render._CSS, flags=re.S)
    # Token-Definitionen selbst dürfen Literale tragen — sie sind ihr Ort.
    for sel in (":root", ':root[data-theme="dark"]', ':root[data-theme="light"]'):
        for m in re.finditer(re.escape(sel) + r"\s*\{.*?\n\}", css, re.S):
            css = css.replace(m.group(0), "")
    css = re.sub(r"@media\s*\(prefers-color-scheme:[^)]*\)\s*\{.*?\n\}", "",
                 css, flags=re.S)
    leftover = re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not leftover, f"nicht auf Token umgestellt: {sorted(set(leftover))}"
