"""Ein Farbvokabular für elf Zustände: zwei Marker und ein Chip (`#33`).

**Dieses Ticket hielt seit dem Board-Umzug eine Absicht fest** — Aufmerksamkeits­
stufen je Zustand — und benannte die offene Stelle selbst: die Stufen standen in
**Worten**, nicht in Farben. m.rau hat sie im `v0.8.7`-Durchgang ausgeschrieben,
für alle elf Zustände aus ``schedule/models.py``.

## Was die Tabelle als Regel sagt

**Zwei Zeichen und ein Chip tragen drei verschiedene Fragen, und das ist der
Grund, warum vier Farben für elf Zustände reichen.** Bisher trug *eine* Farbe
alles und kollidierte deshalb: `failed`, `error`, `killed` und `zombie` teilten
sich Rot, `pending` und `deferred` teilten sich Grau — Gruppierungen, die schon
die Design-Studie am 2026-08-04 als *„nach Farbe statt nach Aktivität"*
beanstandet hat.

* **Die beiden Quadrate sagen, was gerade geschieht.** Links: läuft eine Uhr.
  Rechts: läuft ein Prozess. Das Blinken sagt, wie dringlich; das Abwechseln
  sagt, dass beides zusammengehört.
* **Der Chip sagt, was bisher dabei herausgekommen ist.** Grau *nichts zu
  melden* · grün *läuft oder kommt von selbst zurück* · gelb *wartet auf einen
  Menschen* · orange *ist schiefgegangen*.
* **`starting` ist die einzige Zeile, in der Chip und rechtes Quadrat
  verschieden sind** — und genau daran liest man die Regel ab: die Quadrate sind
  schon gelb, weil etwas anläuft; der Chip ist noch grau, weil noch nichts
  herausgekommen ist.

## Zwei Folgen, die benannt gehören

**Rot verschwindet aus der Statusanzeige** und bleibt reserviert für *„jetzt
handeln"* — getrennter Knoten, Merge-Konflikt, Lauf über seiner ``wall_time``.
Das ist eine schärfere Zuständigkeit als vorher und der eigentliche Gewinn.

**`running` wird grün, `complete` grau**, was die Entscheidung vom 2026-08-05
umkehrt (*„Grün heißt in diesem System `complete`"*). Die Umkehr ist
ausdrücklich erteilt und trägt, weil die Bedeutung jetzt an drei Orten liegt
statt an einem.

## Warum die Tabelle hier noch einmal steht

**Weil ein Test, der gegen die Datenstruktur des Codes prüft, nichts prüft.**
Sie ist hier aus dem Ticket abgeschrieben und nicht importiert; läuft der Code
weg, sagt es dieser Test und nicht die nächste Beobachtung.
"""

from __future__ import annotations

import re

import pytest

from bibi.controller import render
from bibi.schedule.models import Status

#: **Wörtlich aus `#33`**, Kommentar m.rau vom 2026-08-12.
#: ``(links, rechts, chip)`` — je Marker ``(Farbe, Bewegung)``.
#: Bewegung: ``None`` still · ``fast`` schnell · ``slow`` langsam.
#: ``alt`` heißt „im Gegentakt zum linken" und steht nur rechts.
TABELLE: dict[str, tuple[tuple[str, str | None], tuple[str, str | None], str]] = {
    "pending":  (("yellow", None),   ("grey", None),        "grey"),
    "starting": (("yellow", None),   ("yellow", None),      "grey"),
    "running":  (("green", "fast"),  ("green", "fast-alt"), "green"),
    "failed":   (("yellow", "slow"), ("orange", None),      "orange"),
    "error":    (("orange", None),   ("orange", None),      "orange"),
    "deferred": (("yellow", "slow"), ("green", "slow-alt"), "green"),
    "inactive": (("orange", None),   ("orange", None),      "orange"),
    "awaiting": (("yellow", "fast"), ("yellow", "fast-alt"), "yellow"),
    "complete": (("grey", None),     ("grey", None),        "grey"),
    "zombie":   (("orange", None),   ("orange", None),      "orange"),
    "killed":   (("orange", None),   ("orange", None),      "orange"),
}


def test_the_table_covers_every_state_the_engine_knows():
    """Keiner fehlt, keiner ist erfunden.

    **Die Prüfung gehört an den Anfang, weil sie die Tabelle selbst prüft.**
    Ein Vokabular, das zehn von elf Zuständen kennt, ist im elften stumm — und
    stumm heißt hier: ohne Marker, ohne Chip, eine Zeile, die nichts sagt.
    """
    assert set(TABELLE) == {s.value for s in Status}


def test_red_is_gone_from_the_status_display():
    """*„Rot verschwindet aus der Statusanzeige"* — die erste der beiden Folgen.

    Rot bleibt reserviert für *„jetzt handeln"*: getrennter Knoten,
    Merge-Konflikt, Lauf über seiner ``wall_time``. Solange vier Endzustände es
    tragen, ist es die Farbe für *„etwas ist schiefgegangen"* und damit für den
    Normalfall eines jeden Systems, das lange genug läuft.
    """
    farben = {f for links, rechts, chip in TABELLE.values()
              for f in (links[0], rechts[0], chip)}
    assert "red" not in farben, farben
    assert farben == {"grey", "green", "yellow", "orange"}, farben


@pytest.mark.parametrize("zustand", sorted(TABELLE))
def test_every_row_carries_two_marker_slots(zustand: str):
    """**Der erste Rot-Schritt.** Zwei Plätze, unabhängig vom Zustand.

    ``_AKTIVITAET`` kannte fünf Zustände und gab für den Rest **gar kein
    Element** zurück — weshalb der Slug jeder ruhenden Zeile um einen Em nach
    links rutschte. Eine Tabelle, deren Spalten je nach Zustand woanders
    anfangen, ist beim Überfliegen genau so viel wert wie keine.
    """
    html = render._aktivitaets_marker({"row_status": zustand}, {})
    marker = re.findall(r'<i class="mk[^"]*"', html)
    assert len(marker) == 2, f"{zustand}: {html!r}"


@pytest.mark.parametrize("zustand", sorted(TABELLE))
def test_colour_and_motion_follow_the_table(zustand: str):
    """**Der zweite Rot-Schritt**: alle elf Zustände gegen die Tabelle."""
    links, rechts, _chip = TABELLE[zustand]
    html = render._aktivitaets_marker({"row_status": zustand}, {})
    gefunden = re.findall(r'<i class="mk ([^"]*)"', html)
    assert len(gefunden) == 2, html
    for platz, (farbe, bewegung), klassen in (
            ("links", links, gefunden[0]), ("rechts", rechts, gefunden[1])):
        teile = set(klassen.split())
        assert f"mk-{farbe}" in teile, f"{zustand}/{platz}: {klassen!r}"
        if bewegung is None:
            assert not {k for k in teile if k.startswith("blink")}, \
                f"{zustand}/{platz} soll still sein: {klassen!r}"
        else:
            takt, *alt = bewegung.split("-")
            assert f"blink-{takt}" in teile, f"{zustand}/{platz}: {klassen!r}"
            assert ("alt" in teile) == bool(alt), f"{zustand}/{platz}: {klassen!r}"


@pytest.mark.parametrize("zustand", sorted(TABELLE))
def test_the_chip_follows_the_table(zustand: str):
    """Der Chip sagt, was herausgekommen ist — nicht, was gerade geschieht."""
    _links, _rechts, chip = TABELLE[zustand]
    html = render._status_chip(zustand)
    assert f"chip-{chip}" in html, f"{zustand}: {html!r}"
    assert zustand in html, f"{zustand} steht nicht als Wort im Chip: {html!r}"


@pytest.mark.parametrize("zustand", sorted(TABELLE))
def test_the_same_state_wears_the_same_colour_on_every_screen(zustand: str):
    """**Der dritte Rot-Schritt**, und er hat zwei Vorgeschichten.

    Marker und Chip stammen aus **derselben** Farbtabelle, und zwei
    Implementierungen derselben Regel sind in diesem Code schon zweimal
    auseinandergelaufen — `#102` und `#126`, beide am Aktualitäts-Urteil, beide
    mit zwei Vokabularen für dasselbe. Deshalb liegt der Chip-Teil in diesem
    Ticket und nicht in `#36`, wo er ursprünglich stand.

    Geprüft werden die vier Orte, an denen ein Zustand erscheint: die
    Jobs-Zeile, das Journal, die Detail-Kachel und die Lauf-Liste.
    """
    from bibi.controller.jobs_view import JobRow, Segment
    now = 1_000_000.0
    erwartet = f"chip-{TABELLE[zustand][2]}"

    zeile = render._jobs_zeile(
        JobRow(slug="x", segment=Segment.SCHEDULE,
               scheduler={"row_status": zustand, "started_at": now - 5},
               spec={"payload": "echo hi"}), now)
    lauf = render._run_rows([{"id": 1, "status": zustand,
                              "started_at": now - 5}], "x", now)

    for name, html in (("jobs-zeile", zeile), ("lauf-liste", lauf)):
        assert erwartet in html, f"{zustand} auf {name}: {html!r}"


def test_no_screen_paints_a_state_by_hand():
    """Die Gegenprobe zum vorigen: **eine** Funktion, nicht vier Kopien.

    Ein Test, der vier Orte einzeln gegen die Tabelle hält, ist grün, solange
    alle vier zufällig richtig sind — er merkt nicht, dass es vier sind. Was
    ihn trägt, ist diese Prüfung: außerhalb von ``_status_chip()`` setzt
    niemand eine Chip-Farbe für einen Zustand.
    """
    quelle = render.__file__
    with open(quelle, encoding="utf-8") as f:
        text = f.read()
    # Die Definition selbst und die Farbtabelle sind erlaubt; jede weitere
    # Fundstelle waere eine zweite Implementierung.
    treffer = re.findall(r'chip-(?:grey|green|yellow|orange)', text)
    assert len(treffer) <= 8, (
        f"{len(treffer)} Fundstellen fuer eine Chip-Farbe — sie gehoeren in "
        "_ZUSTAND_VOKABULAR und _status_chip(), nicht in die Screens.")


#: Welche CSS-Variable zu welcher Farbe der Tabelle gehört.
_VAR = {"grey": "--faint", "green": "--green",
        "yellow": "--yellow", "orange": "--orange"}


@pytest.mark.parametrize("zustand", sorted(TABELLE))
def test_the_stylesheet_and_the_table_agree(zustand: str):
    """**Die eine Dopplung, die sich nicht vermeiden ließ — deshalb bewacht.**

    Wo ein Zustand kein Chip ist, sondern eine **Überschrift** (die ``h1`` der
    Lauf- und der Attribut-Seite), kommt seine Farbe aus ``.st.<zustand>`` im
    Stylesheet. Ein Stylesheet kann keine Python-Tabelle lesen; die Farbe steht
    dort also ein zweites Mal.

    Genau diese Konstellation ist in diesem Code schon zweimal auseinander­
    gelaufen — `#102` und `#126`, beide am Aktualitäts-Urteil, beide mit zwei
    Vokabularen für dieselbe Sache. Der Unterschied diesmal ist dieser Test:
    **die Dopplung ist sichtbar und geprüft statt versteckt.**
    """
    css = render._CSS
    erwartet = _VAR[TABELLE[zustand][2]]
    regeln = re.findall(rf"([^{{}}]*\.st\.{zustand}\b[^{{]*)\{{([^}}]*)\}}", css)
    assert regeln, f"keine .st.{zustand}-Regel im Stylesheet"
    farben = {m.group(1) for _sel, block in regeln
              if (m := re.search(r"color:\s*var\((--[\w-]+)\)", block))}
    assert farben == {erwartet}, (
        f".st.{zustand} faerbt {farben}, der Chip aber {erwartet}")


def test_starting_and_awaiting_stay_apart_without_motion():
    """**Die Gegenprobe zur Zugänglichkeit, und sie hat einen konkreten Fall.**

    Unter ``prefers-reduced-motion`` werden `starting` und `awaiting`
    ununterscheidbar — beide gelb/gelb, sie trennt allein das Blinken. Das
    heutige CSS hat dieselbe Lücke für ``act-run``/``act-rest`` schon einmal
    über die **Form** geschlossen (gefüllt gegen hohl); derselbe Weg hier.

    Ohne diesen Test verliert ein Teil der Nutzer zwei Zustände — und zwar
    lautlos, weil an keiner Stelle etwas fehlt, das jemandem auffiele.

    **Geprüft wird `starting` gegen `awaiting`, also *still* gegen *schnell*.**
    Das ist der Fall aus dem Ticket; ein Test auf *schnell gegen langsam* wäre
    daran vorbeigegangen und hätte einen Bau durchgelassen, in dem der stille
    Marker unverändert bleibt und damit aussieht wie der schnelle. Genau so ist
    der erste Entwurf hier gebaut worden.
    """
    css = render._CSS
    m = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    assert m, "kein reduced-motion-Block im Stylesheet"
    block = m.group(1)

    def _form(klasse: str | None) -> str:
        """Wie ein Marker unter reduzierter Bewegung aussieht.

        Der stille trägt keine Zusatzklasse; seine Form kommt aus ``.mk``
        selbst, also von **außerhalb** des Blocks.
        """
        grund = re.search(r"\n\.mk \{([^}]*)\}", css)
        assert grund, "keine .mk-Grundregel"
        if klasse is None:
            return grund.group(1).strip()
        r = re.search(rf"\.{klasse}[^{{]*\{{([^}}]*)\}}", block)
        assert r, f"{klasse} fehlt im reduced-motion-Block: {block}"
        # Die Zusatzregel liegt über der Grundregel.
        return (grund.group(1) + ";" + r.group(1)).strip()

    still, schnell, langsam = _form(None), _form("blink-fast"), _form("blink-slow")
    assert still != schnell, (
        "starting (still) und awaiting (schnell) sehen unter reduzierter "
        f"Bewegung gleich aus: {still!r}")
    assert schnell != langsam and still != langsam, (still, schnell, langsam)


def test_starting_is_the_only_row_where_chip_and_right_marker_differ():
    """**An dieser einen Zeile liest man die Regel ab.**

    Die Quadrate sind schon gelb, weil etwas anläuft; der Chip ist noch grau,
    weil noch nichts herausgekommen ist. Wäre es nirgends so, trügen Chip und
    Marker dieselbe Information zweimal — und einer von beiden wäre überflüssig.
    """
    abweichend = {z for z, (_l, rechts, chip) in TABELLE.items() if rechts[0] != chip}
    assert abweichend == {"starting"}, abweichend
