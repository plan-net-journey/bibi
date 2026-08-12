"""Die beiden Aktivitäts-Marker am Zeilenanfang (#67 Schritt 2, neu gefasst in #33).

**Bis `v0.8.7` war es ein Quadrat, das pulste**, und es erbte seine Farbe aus
der `.st.<status>`-Regel der Statuszelle — *„das Quadrat erbt die Statusfarbe,
keine Farbe wechselt ihre Bedeutung"*, weshalb `running` blau war und nicht
grün. Zustände, die nicht in `_AKTIVITAET` standen, bekamen **gar kein**
Element.

**Mit `#33` sind es zwei Quadrate, und sie stehen in jeder Zeile.** Die
Änderung ist keine Verzierung, sie folgt aus der Aufteilung der Bedeutung auf
drei Träger:

| | sagt |
|---|---|
| linkes Quadrat | läuft eine **Uhr** |
| rechtes Quadrat | läuft ein **Prozess** |
| Chip | was bisher **herausgekommen** ist |

Das Blinken sagt, wie dringlich; das Abwechseln (`alt`) sagt, dass die beiden
zusammengehören. **Die Farben kommen aus `_ZUSTAND_VOKABULAR`** und nicht mehr
aus der Statuszelle — dieselbe Tabelle, aus der auch der Chip liest.

**Warum jede Zeile beide Plätze trägt, auch wo nichts blinkt:** ohne Element
rutschte der Slug jeder ruhenden Zeile um einen Em nach links. Die alte
Begründung — *„ein Zeichen, das immer da ist, trägt keine Information mehr"* —
stimmte für **ein** Zeichen. Bei zweien trägt die Farbe die Information, und
der feste Platz ist die Voraussetzung dafür, dass die Spalten daneben
übereinander stehen.

**Die vollständige Tabelle steht in `tests/test_status_vocabulary.py`**, dort
aus dem Ticket abgeschrieben und Zustand für Zustand geprüft. Hier stehen die
Fälle, die einen eigenen Anlass haben.
"""

from __future__ import annotations

from bibi.controller import render
from bibi.controller.jobs_view import JobRow, Segment

NOW = 1_000_000.0


def _zeile(status: str) -> str:
    row = JobRow(slug="x", segment=Segment.SCHEDULE,
                 scheduler={"row_status": status, "started_at": NOW - 5},
                 spec={"payload": "echo hi"})
    return render._jobs_zeile(row, NOW)


def test_a_running_job_blinks_fast_on_both_squares():
    """`running` ist der einzige Zustand, in dem **beide** Quadrate schnell
    blinken — links läuft eine Uhr, rechts läuft ein Prozess, und das Abwechseln
    sagt, dass es dasselbe Ereignis ist."""
    html = _zeile("running")
    assert html.count("blink-fast") == 2, html
    assert "alt" in html


def test_failed_and_deferred_keep_the_slow_blink_on_the_left():
    """Beide haben eine laufende **Uhr** und keinen laufenden Prozess: der
    nächste Versuch steht an, gearbeitet wird gerade nicht.

    Bis `v0.8.7` teilten sie sich einen Ruhepuls und eine Farbe. Sie teilen
    sich weiterhin den langsamen Takt links — **rechts gehen sie auseinander**,
    und das ist der Gewinn: `failed` ist orange (etwas ist schiefgegangen),
    `deferred` grün (es kommt von selbst zurück)."""
    for zustand in ("failed", "deferred"):
        assert "blink-slow" in _zeile(zustand), zustand
    assert "mk-orange" in _zeile("failed")
    assert "mk-green" in _zeile("deferred")


def test_awaiting_blinks_fastest_because_it_wants_something_from_you():
    """**Hier kehrt `#33` eine spätere Entscheidung zurück.**

    Das Ticket verlangt seit jeher *dynamisch*; die Design-Studie hat `awaiting`
    danach auf *nicht animiert* gesetzt, mit der Begründung *„Bewegung heißt, es
    passiert etwas ohne dich"*, und `_AKTIVITAET` trug bis `v0.8.7` `act-still`.

    Die neue Tabelle stellt die ältere Festlegung wieder her, und sie hat recht:
    **Blinken heißt hier nicht „es passiert etwas ohne dich", sondern „diese
    Zeile will etwas von dir"** — und `awaiting` will am meisten."""
    html = _zeile("awaiting")
    assert html.count("blink-fast") == 2, html
    assert "mk-yellow" in html


def test_terminal_states_get_two_markers_too():
    """**Die Umkehr der alten Regel, und der Rot-Schritt von `#33`.**

    Hier stand *„terminale Zustände bekommen gar keinen Marker, nicht etwa einen
    stillen"*. Die Folge war sichtbar und nie benannt: der Slug jeder ruhenden
    Zeile rutschte um einen Em nach links, weil das Element davor fehlte."""
    for zustand in ("complete", "error", "killed", "zombie", "inactive"):
        html = _zeile(zustand)
        assert html.count('<i class="mk') == 2, f"{zustand}: {html!r}"
        assert "blink" not in html, f"{zustand} soll still sein"


def test_pending_gets_two_markers_but_no_motion():
    """`pending` ist kein aktiver Zustand — `_live_placeholder_row()` zählt es
    ausdrücklich nicht zu den laufenden. Es bekommt trotzdem seine zwei Plätze,
    und **links Gelb**: eine Uhr läuft, ein Prozess nicht."""
    html = _zeile("pending")
    assert html.count('<i class="mk') == 2
    assert "blink" not in html
    assert "mk-yellow" in html and "mk-grey" in html


def test_the_marker_reads_from_the_one_table():
    """Die Farbe kommt aus `_ZUSTAND_VOKABULAR`, nicht aus der Statuszelle.

    **Das ist die Umkehr der alten Bauart und ihr Zweck.** Vorher trug der
    Marker die `.st.<status>`-Klasse mit und nahm seine Fläche aus
    `currentColor` — eine Quelle, zwei Orte. Das trug, solange **eine** Farbe
    alles sagte. Seit die Bedeutung auf drei Träger verteilt ist, kann der
    Marker die Chip-Farbe nicht mehr erben: `starting` ist die Zeile, an der
    beide auseinandergehen."""
    html = _zeile("starting")
    assert "mk-yellow" in html, html
    assert "st starting" not in html.split("</span>")[0], \
        "der Marker erbt noch die Statusklasse"


def test_both_blinks_survive_reduced_motion_as_a_distinction():
    """**Erhalten, nicht abschalten** — die Regel überlebt ihren Anlass.

    Sie stand hier für `act-run`/`act-rest` (gefülltes gegen hohles Quadrat) und
    gilt jetzt für drei Fälle statt zwei: still, schnell, langsam. Der
    konkrete Fall dahinter steht in
    `tests/test_status_vocabulary.py::test_starting_and_awaiting_stay_apart_without_motion` —
    ohne ihn wären `starting` und `awaiting` für einen Teil der Nutzer
    dasselbe."""
    css = render._CSS
    start = css.find("@media (prefers-reduced-motion: reduce)")
    block = css[start:css.find("\n}", css.find("{", start))]
    assert ".blink-fast" in block and ".blink-slow" in block
    schnell = block[block.find(".blink-fast"):]
    langsam = block[block.find(".blink-slow"):]
    assert schnell.split("}")[0] != langsam.split("}")[0], \
        "beide Takte sehen unter reduced-motion gleich aus"


# ── #146: der Einspringer fehlte dem Balken ────────────────────────────────
#
# **Der Marker prüft zwei Quellen, seine Nachbarn prüften eine.** Sein Docstring
# benennt die Regel ausdrücklich — *„Der Scheduler führt, der Client springt
# ein"* —, und genau sie fehlte `_laufender_start()` und `_pbar()`. Die Zeile
# sagte deshalb zweierlei über denselben Lauf: der Marker pulste, die Zelle
# daneben zeigte einen Strich.
#
# **Sichtbar wurde das nur, weil derselbe Job über zwei Wege gestartet wurde.**
# Wer immer über den Scheduler startet, sieht es nie.


def _zeile_lokal(status: str) -> str:
    """Dieselbe Zeile, aber der Lauf steht im **lokalen** Datensatz.

    **Die P90 bleibt beim Scheduler**, und das ist kein Versehen des
    Testdatums, sondern der Gegenstand: der Balken misst *einen Lauf* gegen die
    *Historie des Jobs*. Wo gestartet wurde, ändert nichts daran, woran sich
    der Lauf messen lässt — zwei Maßstäbe für dieselbe Frage wären der
    schlechtere Fix. Der erste Anlauf dieses Helfers ließ sie weg, und der
    Balken blieb zu Recht aus.
    """
    row = JobRow(slug="x", segment=Segment.SCHEDULE,
                 scheduler={"row_status": "complete", "next_fire_at": NOW + 3600,
                            "runtime_p90": 16.0},
                 local={"status": status, "started_at": NOW - 5},
                 spec={"payload": "echo hi"})
    return render._jobs_zeile(row, NOW)


def test_a_locally_started_run_gets_its_elapsed_time():
    """Was der Marker sagt, muss die Zelle daneben auch sagen."""
    zeile = _zeile_lokal("running")
    assert "blink-fast" in zeile, "der Marker blinkt nicht — der Testfall traegt nicht"
    assert 'data-dur' in zeile, "die laufende Zeit fehlt dem lokalen Lauf"


def test_a_locally_started_run_gets_its_progress_bar():
    assert "data-pbar" in _zeile_lokal("running"), (
        "der Fortschrittsbalken fehlt dem lokalen Lauf")


def test_a_job_running_nowhere_still_gets_no_bar():
    """Die Gegenprobe, und sie ist der Grund für den Test: ein Fix, der den
    lokalen Datensatz ungeprüft übernimmt, zeichnete überall einen Balken."""
    row = JobRow(slug="x", segment=Segment.SCHEDULE,
                 scheduler={"row_status": "complete", "next_fire_at": NOW + 3600},
                 local={"status": "complete"},
                 spec={"payload": "echo hi"})
    zeile = render._jobs_zeile(row, NOW)
    assert "data-pbar" not in zeile
    assert "blink" not in zeile
