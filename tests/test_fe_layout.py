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

def test_the_header_falls_to_one_column_when_it_gets_narrow():
    """Der Nachfolger der Kachel-Umbruchstufen (`#100`).

    Die alte Forderung lautete „breit 1x4, schmal 2x2 und eng 4x1 Spalten" und
    galt den vier Status-Kacheln. Der Header hat sie mit zwei Blöcken abgelöst,
    und damit braucht es nur noch **eine** Stufe: zwei Spalten nebeneinander
    oder untereinander. Die Aussage bleibt — nebeneinanderstehende Blöcke
    dürfen nicht überlaufen —, die Zahl der Stufen folgt der Anzahl der Blöcke.
    """
    css = render._CSS
    assert "grid-template-columns: 1fr 1fr" in css, "breit: die zwei Blöcke nebeneinander"
    umbrueche = re.findall(r"@media[^{]*\{\s*\.hdr\s*\{", css)
    assert len(umbrueche) == 1, f"erwartet: eine Umbruchstufe, gefunden: {umbrueche}"


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



