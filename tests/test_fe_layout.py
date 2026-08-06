"""Informationsstruktur des FE (m.rau/bibi#62–#65).

Diese vier Punkte legen die Struktur fest, auf der ein späteres Re-Layout
(LIGHT/DARK, `#68`) aufsetzt — erst die Struktur, dann die Farbe, so von
m.rau entschieden. Deshalb sind sie hier festgehalten und nicht dem
Augenschein überlassen: ein Chip, der aus seiner Karte rutscht, fällt beim
nächsten Umbau niemandem auf, wenn ihn kein Test hält.
"""

from __future__ import annotations

import re

from bibi.controller import render


# --- #62: Kopf-Kacheln brechen in drei Stufen um -----------------------------

def test_header_grid_has_three_breakpoints():
    """m.rau: „sollte breit 1x4, schmal 2x2 und end 4x1 Spalten sein."

    Vorher stand dort ``repeat(auto-fit, minmax(9rem, 1fr))`` — der Browser
    entschied, wie viele Spalten es gibt. Das ergibt je nach Fensterbreite auch
    drei Kacheln nebeneinander und eine darunter, was die Anforderung
    ausdrücklich nicht will.
    """
    css = render._CSS
    assert "repeat(4, 1fr)" in css
    assert "repeat(2, 1fr)" in css
    # Zwei Media-Queries, die .statuscards umstellen — die zweite und dritte Stufe.
    breakpoints = re.findall(r"@media[^{]*\{\s*\.statuscards", css)
    assert len(breakpoints) == 2, f"erwartet: zwei Umbruchstufen, gefunden: {breakpoints}"


# --- #63: „mehr laden" gehört in die Karte, links unten ----------------------

def _feed_html() -> str:
    return render.feed_fragment({"entries": []}, days=1)


def test_load_more_sits_inside_its_card():
    """Beide CTAs standen **ausserhalb** ihrer Karte — im Markup als
    ``</div><div class="loadmore">``, also direkt hinter dem Kartenende.

    Genau diese Folge darf es nicht mehr geben. Das ist die präziseste Prüfung
    ohne HTML-Parser: sie trifft die eine Stelle, an der der Unterschied
    zwischen "in der Karte" und "unter der Karte" sichtbar wird.
    """
    html = _feed_html()
    spots = [m.start() for m in re.finditer(r'<div class="loadmore">', html)]
    assert spots, "die CTAs sind ganz verschwunden"
    for pos in spots:
        card = html.rfind('<div class="panel-card">', 0, pos)
        assert card >= 0, "vor diesem loadmore beginnt gar keine Karte"
        seg = html[card:pos]
        # Innerhalb der Karte ist die div-Bilanz mindestens 1 (die Karte selbst).
        # Steht der CTA darunter, ist sie auf 0 zurueckgefallen. Ein reiner
        # Vergleich auf "</div><div class=loadmore>" reichte hier nicht: auch
        # der Karteninhalt endet auf </div> (die Heatmap-Legende).
        opens = len(re.findall(r"<div\b", seg))
        closes = seg.count("</div>")
        assert opens - closes >= 1, "loadmore steht ausserhalb seiner Karte"


def test_load_more_is_left_aligned():
    assert ".loadmore { display: flex" in render._CSS
    assert "justify-content: flex-start" in render._CSS


# --- #64: Type/Status-Filter gehört in die Schedules-Karte, links oben -------



def test_client_status_follows_the_live_run_first():
    """Dieselbe dreistufige Logik, die die Tabelle anzeigt — live schlägt den
    letzten Lauf, sonst gibt es keinen Status."""
    row = {"slug": "a", "live": {"status": "deferred"}}
    assert render.client_row_status(row, {"a": {"status": "complete"}}) == "deferred"


def test_client_status_falls_back_to_the_last_run():
    assert render.client_row_status({"slug": "a"}, {"a": {"status": "failed"}}) == "failed"


def test_client_status_is_none_when_never_run():
    assert render.client_row_status({"slug": "a"}, {}) is None


def test_client_live_without_status_reads_as_running():
    """Ein Live-Eintrag ohne eigenes Statusfeld heisst laufend — genau wie in
    der Zelle.

    Ein **leeres** Dict ist dagegen kein Live-Eintrag: `_jobs_row()` prüft mit
    `if live:`, und dort ist `{}` falsy. Die geteilte Funktion muss sich
    genauso verhalten, sonst filtert der Screen anders, als er anzeigt — beim
    Schreiben dieses Tests zunächst falsch angenommen.
    """
    assert render.client_row_status({"slug": "a", "live": {"started_at": 1.0}}, {}) == "running"
    assert render.client_row_status({"slug": "a", "live": {}}, {}) is None




