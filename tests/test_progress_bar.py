"""Der Fortschrittsbalken misst gegen die eigene Vergangenheit (#67 Schritt 3).

**Die Achse richtet sich an genau zwei Dingen aus:** an P90, solange der Lauf
darunter bleibt, danach am **aktuellen Wert × 1,25**. Damit steht der Balkenkopf
jenseits von P90 konstant bei 80 %, und die P90-Marke wandert nach links —
abgelesen wird der **Abstand zwischen Kopf und Marke**.

**Das löst das Skalenproblem strukturell, und das Problem ist gemessen:** `Witz`
hat P90 20,8 s und einen längsten Lauf von 3441 s, Faktor 165. Ein fester Balken
hätte die P90-Marke bei 0,6 % der Breite. Ein Balken, dessen Bezugsgröße sich
still ändert, lügt beim zweiten Hinsehen; dieser nennt sie.

**P90 statt Median**, weil der Median per Definition in der Hälfte aller Läufe
überschritten wird — als Balkenende stünde er dauerhaft im Überlauf. Gemessen an
`Runner`: 1098 Läufe, Median 11,3 s, P90 31,0 s.

## Warum das Erstbild serverseitig gerechnet wird

Der Browser zählt weiter (wie bei den Dauern), aber die **erste** Geometrie
kommt aus Python. Zwei Gründe, und der zweite ist der wichtigere: eine
stehengebliebene Seite zeigt dann einen richtigen Balken statt eines leeren —
und die Achsenrechnung wird prüfbar. Läge sie nur im JavaScript, gäbe es für die
eine Formel, an der alles hängt, keinen Test außer dem Hinsehen.
"""

from __future__ import annotations

import pytest

from bibi.controller import render
from bibi.controller.jobs_view import JobRow, Segment

NOW = 1_000_000.0


def _zeile(status: str, *, laeuft_seit: float | None = 10.0, **sched) -> str:
    s = {"row_status": status, **sched}
    if laeuft_seit is not None:
        s["started_at"] = NOW - laeuft_seit
    row = JobRow(slug="x", segment=Segment.SCHEDULE, scheduler=s,
                 spec={"payload": "echo hi"})
    return render._jobs_zeile(row, NOW)


# ── Die Achse ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("t", "ref", "kopf", "marke"), [
    # unter P90: gewoehnlicher Balken, die Marke steht am rechten Rand
    (10.0, 40.0, 25.0, 100.0),
    # genau bei P90: Kopf und Marke treffen sich
    (40.0, 40.0, 100.0, 100.0),
    # deutlich darueber: Kopf konstant bei 80 %, Marke wandert nach links
    (100.0, 40.0, 80.0, 32.0),
    (400.0, 40.0, 80.0, 8.0),
])
def test_the_axis_puts_the_head_and_the_mark_where_the_rule_says(t, ref, kopf, marke):
    assert render._pbar_geometrie(t, ref) == (pytest.approx(kopf), pytest.approx(marke))


def test_the_head_never_leaves_the_bar():
    """Die Gegenprobe zur Formel: egal wie lange der Lauf dauert, der Kopf
    bleibt im Balken. Ein Wert über 100 % wäre ein Überlauf, den kein Browser
    zeigt — und ein Balken, den man nicht mehr wachsen sieht, ist keiner."""
    for t in (0.0, 1.0, 39.9, 40.0, 40.1, 1e6):
        kopf, marke = render._pbar_geometrie(t, 40.0)
        assert 0.0 <= kopf <= 100.0, t
        assert 0.0 <= marke <= 100.0, t


# ── Wer einen Balken bekommt und wer nicht ──────────────────────────────────


def test_a_running_job_with_history_gets_a_bar():
    assert "pbar" in _zeile("running", runtime_p90=40.0)


def test_starting_gets_no_bar():
    """Per Invariante gibt es dort noch keine PID und damit nichts Messbares —
    dieselbe Begründung, mit der `#67` den Balken für `starting` ausschließt."""
    assert "pbar" not in _zeile("starting", runtime_p90=40.0)


@pytest.mark.parametrize("zustand", ["awaiting", "failed", "deferred",
                                     "complete", "pending", "error"])
def test_only_a_running_job_gets_a_bar(zustand):
    """`failed` und `deferred` bekommen **keinen** Countdown-Balken mehr: ohne
    laufenden Prozess gibt es keine Laufzeit, also keinen Fortschritt. Sie
    behalten den Ruhepuls, und der sagt bereits, was zu sagen ist."""
    assert "pbar" not in _zeile(zustand, runtime_p90=40.0)


# ── Die Kaskade ─────────────────────────────────────────────────────────────


def test_without_history_the_wall_time_carries_the_axis():
    """**Die Kaskade ist kein Sonderfall, sondern der Normalfall:** von 149
    Slugs im Journal haben 143 genau einen Lauf, nur vier kommen über zwanzig.
    Für die große Mehrheit ist ein P90 gar nicht berechenbar; `wall_time` ist
    bei den aktiven Schedules dagegen durchweg gesetzt."""
    html = _zeile("running", wall_time=900)
    assert "pbar" in html
    assert 'data-refkind="wall"' in html


def test_the_p90_wins_over_the_wall_time():
    """Gegenprobe zur Reihenfolge der Kaskade: die eigene Historie schlägt die
    Obergrenze, weil sie sagt, was üblich *ist*, und nicht, was erlaubt wäre."""
    html = _zeile("running", runtime_p90=40.0, wall_time=900)
    assert 'data-refkind="p90"' in html


def test_without_any_reference_there_is_no_bar():
    """Der dritte Ast der Kaskade: nur die Quadrate. Ein Balken ohne Bezug
    müsste eine Achse erfinden — und ein erfundener Maßstab ist schlimmer als
    keiner.

    Seit `#33` sind es zwei Quadrate statt einem, und sie blinken statt zu
    pulsen. Was der Test misst, bleibt dasselbe: die Zeile sagt weiterhin, dass
    hier gearbeitet wird — nur eben ohne Maßstab, an dem man es abliest."""
    html = _zeile("running")
    assert "pbar" not in html
    assert "blink-fast" in html


# ── Die Farbe ───────────────────────────────────────────────────────────────


def test_the_bar_turns_red_past_the_reference():
    assert "pbar over" in _zeile("running", runtime_p90=5.0, laeuft_seit=60.0)


def test_the_bar_is_blue_below_the_reference():
    html = _zeile("running", runtime_p90=400.0, laeuft_seit=10.0)
    assert "pbar" in html and "over" not in html
