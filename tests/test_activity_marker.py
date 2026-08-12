"""Der Aktivitäts-Marker: ein Quadrat, das pulst (#67 Schritt 2).

**Am Zeilenanfang, in der Statusfarbe.** Grün für `running` hätte mit `complete`
kollidiert; die Entscheidung lautet deshalb *„das Quadrat erbt die Statusfarbe —
keine Farbe wechselt ihre Bedeutung"*. Gebaut ist das nicht durch eine zweite
Farbtabelle, sondern indem der Marker dieselbe `.st.<status>`-Regel trägt wie
die Statuszelle und seine Fläche aus `currentColor` nimmt: **eine Quelle, zwei
Orte.**

| Zustand | Marker |
|---|---|
| `running` | schneller Puls |
| `failed`, `deferred` | Ruhepuls |
| `awaiting` | **Stillstand** |
| terminal | **gar keiner** |

**`awaiting` ist als einziger sichtbar und trotzdem unbewegt, und das ist eine
Aussage:** Bewegung heißt *„es passiert etwas ohne dich"* — bei `awaiting`
passiert nichts, bis jemand handelt. Ein Puls dort wäre eine Lüge über die
Zuständigkeit.

**Terminale Zustände bekommen gar keinen Marker**, nicht etwa einen stillen: es
gibt keine Aktivität, über die er etwas sagen könnte, und ein Zeichen, das
immer da ist, trägt keine Information mehr.
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


def test_a_running_job_pulses():
    assert "act-run" in _zeile("running")


def test_failed_and_deferred_share_the_resting_pulse():
    """Dieselbe Stufe wie in #33 (*hoch, weniger dynamisch*) — und dieselbe
    Gruppe, die #68 farblich zusammengeführt hat."""
    assert "act-rest" in _zeile("failed")
    assert "act-rest" in _zeile("deferred")


def test_awaiting_stands_still():
    html = _zeile("awaiting")
    assert "act-still" in html
    assert "act-run" not in html and "act-rest" not in html


def test_terminal_states_get_no_marker_at_all():
    for zustand in ("complete", "error", "killed", "zombie", "inactive"):
        assert 'class="act' not in _zeile(zustand), zustand


def test_pending_gets_no_marker_either():
    """`pending` ist kein aktiver Zustand — `_live_placeholder_row()` zählt es
    ausdrücklich nicht zu den laufenden. Ein Marker dort behauptete Arbeit, wo
    nur ein Termin steht."""
    assert 'class="act' not in _zeile("pending")


def test_the_marker_borrows_the_status_colour_instead_of_defining_one():
    """Der Marker trägt die `.st.<status>`-Klasse und nimmt seine Fläche aus
    `currentColor`. Eine zweite Farbtabelle wäre die Stelle, an der die beiden
    Orte später auseinanderlaufen."""
    html = _zeile("running")
    assert "st running" in html
    css = render._CSS
    start = css.find(".act {")
    assert start != -1, "keine .act-Regel"
    assert "currentColor" in css[start:start + 300]


def test_both_pulses_survive_reduced_motion_as_a_distinction():
    """**Erhalten, nicht abschalten.** Puls → gefülltes Quadrat, Ruhepuls →
    hohles: dieselbe Unterscheidung ohne Bewegung. Ein Block, der beide auf
    `animation: none` setzt und sonst nichts, machte sie ununterscheidbar."""
    css = render._CSS
    start = css.find("@media (prefers-reduced-motion: reduce)")
    block = css[start:css.find("\n}", css.find("{", start))]
    assert ".act-run" in block and ".act-rest" in block
    lauf = block[block.find(".act-run"):]
    ruhe = block[block.find(".act-rest"):]
    assert lauf.split("}")[0] != ruhe.split("}")[0], \
        "beide Zustaende sehen unter reduced-motion gleich aus"
