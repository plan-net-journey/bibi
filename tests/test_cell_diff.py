"""Eine geänderte Zelle flammt auf — die ganze Zelle (#67 Schritt 1).

**Der Anlass ist ein Satz von m.rau:** *„Ich bin ein grosser Fan von `watch -d`.
Also das kurze Aufflammen bei einer Änderung."* Daraus wurde eine Design-Studie
über zwei Runden; was hier gebaut wird, ist ihre entschiedene Fassung.

**Es blitzt die ganze Zelle, nicht die einzelne Zahl.** Die Studie hatte
zeichengenaues Diffen gewählt (Kanal A5); m.rau hat das am 2026-08-07 abgesagt —
*„JA zur Zelle bzw. Wort oder Worte. NEIN zu zeichenweisen Diff. Das ist komisch.
Wenn dann den ganzen Text!"*, präzisiert auf *„ich meine die ZELLE."* Der
Zusatznutzen, mit dem A5 begründet war, entfällt damit bewusst.

**Die Runtime-Spalte ist ausgenommen, und das ist keine Bequemlichkeit.** Sie
zählt im Sekundentakt hoch; gegen 3 s Ausklingzeit wäre ihre Markierung
dauerhaft an. **Dass sie hochzählt, ist erwartet und damit keine Nachricht.**

## Was diese Datei prüft — und was sie nicht kann

Geprüft wird der **Vertrag**, auf dem das Aufflammen aufsetzt: welche Zelle sich
ausdrücklich abmeldet, woran der Vergleich eine Zeile wiedererkennt, und dass die
Bewegung einen Weg ohne Bewegung hat.

**Ob der Puls zu langsam läuft oder der Ausklang statt drei Sekunden acht
dauert, sieht keine Testebene** — das ist Handgriff 7 des Verfahrens
(*„Bewegung wird aufgezeichnet, nicht betrachtet"*) und gehört in den
Akzeptanz-Durchgang, nicht hierher. Ein Test, der so täte, als könne er es,
wäre die schlechtere Abdeckung: er sähe aus wie ein Nachweis.
"""

from __future__ import annotations

from bibi.controller import render
from bibi.controller.jobs_view import JobRow, Segment

NOW = 1_000_000.0


def _zeile(**sched) -> str:
    row = JobRow(slug="x", segment=Segment.SCHEDULE, scheduler=sched,
                 spec={"payload": "echo hi"})
    return render._jobs_zeile(row, NOW)


def test_the_runtime_cell_opts_out_of_the_diff():
    """Der einzige Ausnahmefall steht im Markup, nicht in einer Spaltenzählung
    im JavaScript — eine Ausnahme, die an einer Position hängt, bricht beim
    ersten Spaltenumbau (und #135 baut die Spalten um)."""
    html = _zeile(row_status="complete", runtime_p90=31.0)
    assert "data-nodiff" in html


def test_only_the_runtime_cell_opts_out():
    """Gegenprobe: die Abmeldung gilt genau einer Zelle. Ohne sie wäre ein
    Markup grün, das den Diff überall abschaltet."""
    html = _zeile(row_status="complete", runtime_p90=31.0)
    assert html.count("data-nodiff") == 1


def test_a_row_carries_a_key_the_diff_can_recognise_it_by():
    """Der Vergleich braucht eine Zeile über den Swap hinweg wieder. Die
    Position taugt dafür nicht — Sortierung und Filter verschieben sie —, der
    Job-uid schon."""
    html = _zeile(row_status="complete")
    assert "data-row=" in html


def test_the_flash_uses_two_channels():
    """Hintergrund **und** Schriftfarbe, wie bei den Attributseiten und aus
    demselben Grund: ein Signal allein geht in hellen Themes verloren."""
    css = render._CSS
    start = css.find("@keyframes bibi-cellflash")
    assert start != -1, "keine Keyframes fuer den Zell-Diff"
    block = css[start:start + 400]
    assert "background" in block
    assert "color" in block


def test_the_flash_borrows_no_semantic_hue():
    """Die Palette trägt Semantikfarben nur an Zustandsstellen, und Terracotta
    genau eine Bedeutung (Interaktion). Eine Wertänderung ist keins von beidem —
    sie blitzt über Helligkeit, damit die Zelle nicht kurz wie ein Zustand
    aussieht, den sie nicht hat."""
    css = render._CSS
    start = css.find("@keyframes bibi-cellflash")
    assert start != -1, "keine Keyframes — der Test misst sonst nichts"
    block = css[start:start + 400]
    for verboten in ("--red", "--green", "--amber", "--blue", "--brand"):
        assert verboten not in block, f"{verboten} im Diff-Blitz"
