"""Die Statusfarben gruppieren wie das Zustandsmodell, nicht dagegen (#68).

**Zwei Stellen, an denen die Darstellung etwas anderes sagte als das Modell
dahinter** — beide beim Bau der Design-Mockups am Live-Stand aufgefallen und
ausdrücklich als *„unabhängig davon verwertbar"* festgehalten.

**`failed` sah aus wie ein Endzustand und ist keiner.** Es stand in derselben
Farbe wie `error`, `killed` und `zombie` — aber es steht **nicht** in
``lifecycle.TERMINAL``: es hat Backoff, ein gesetztes ``next_fire_at`` und den
Übergang ``RETRY → starting``. Wer die Zeile las, hielt den Job für erledigt,
während er auf seinen nächsten Versuch wartete.

**`deferred` sah aus wie Warten und gilt als aktiv.** Es war grau wie
``pending`` — rund achtzehnhundert Zeilen weiter unten zählt
``_live_placeholder_row()`` es aber zu den **aktiven** Läufen und ``pending``
ausdrücklich nicht. Die Farbe gruppierte damit genau gegen die Logik.

## Warum beide dieselbe Farbe bekommen, und `awaiting` auch

Die Aufmerksamkeitsstufen aus [`#33`](https://github.com/plan-net-journey/bibi/issues/33)
führen `failed` und `deferred` auf **derselben** Stufe (*hoch, weniger
dynamisch*) und `awaiting` eine Stufe daneben (*hoch, dynamisch*). Der
Unterschied ist also keiner der Aufmerksamkeit, sondern einer der **Bewegung** —
und die trägt der Aktivitäts-Marker, nicht die Farbe: `awaiting` steht still,
`failed`/`deferred` haben einen Ruhepuls.

**Die Farbe sagt „hier ist Aufmerksamkeit nötig", die Bewegung sagt „wer als
nächstes handelt".** Zwei Fragen, zwei Kanäle — hätte die Farbe beide getragen,
bräuchte sie drei Töne für eine Unterscheidung, die eine Animation umsonst macht.
"""

from __future__ import annotations

import re

from bibi.controller import render


def _regel(css: str, selektor: str) -> str:
    """Der Regelblock, in dem ``selektor`` steht — oder ``""``."""
    for zeile in css.splitlines():
        if selektor in zeile and "{" in zeile:
            return zeile.strip()
    return ""


def _farbe(css: str, klasse: str) -> str:
    """Die ``color``-Variable, die ``.st.<klasse>`` zugewiesen bekommt."""
    zeile = _regel(css, f".st.{klasse}")
    treffer = re.search(r"color:\s*var\((--[a-z]+)\)", zeile)
    return treffer.group(1) if treffer else ""


# ── 1: failed gehört nicht zu den Endzuständen ──────────────────────────────


def test_failed_is_not_painted_like_a_terminal_error():
    css = render._CSS
    assert _farbe(css, "failed") != _farbe(css, "error")


def test_deferred_is_not_painted_like_pending():
    css = render._CSS
    assert _farbe(css, "deferred") != _farbe(css, "pending")


def test_failed_and_deferred_share_one_group():
    """Die eigentliche Aussage: sie gehören zusammen. Ohne diese Prüfung wäre
    ein Fix grün, der `failed` irgendwohin schiebt und `deferred` stehenlässt."""
    css = render._CSS
    assert _farbe(css, "failed") == _farbe(css, "deferred") != ""


def test_the_model_agrees_that_they_belong_together():
    """Der Beleg steht im Modell, nicht in der Meinung: beide sind
    nicht-terminal und beide gelten als aktiv."""
    from bibi.schedule import lifecycle
    from bibi.schedule.models import Status
    assert Status.FAILED not in lifecycle.TERMINAL
    assert Status.DEFERRED not in lifecycle.TERMINAL


# ── Gegenproben: was zusammengehört, bleibt zusammen ────────────────────────


def test_the_real_terminal_errors_stay_together():
    css = render._CSS
    rot = {_farbe(css, k) for k in ("error", "killed", "zombie")}
    assert len(rot) == 1 and rot != {""}


def test_running_and_complete_keep_their_meaning():
    css = render._CSS
    assert _farbe(css, "running") == "--blue"
    assert _farbe(css, "complete") == "--green"


# ── 4: prefers-reduced-motion gibt es überhaupt ─────────────────────────────


def test_a_reduced_motion_block_exists():
    """Die einzige Animation im Bestand — der Button-Spinner — lief ungefragt.
    Solange Bewegung Zierde ist, ist das eine Kleinigkeit; sobald sie
    Information trägt, ist es ein Zugänglichkeitsproblem."""
    assert "@media (prefers-reduced-motion: reduce)" in render._CSS


def test_reduced_motion_reaches_every_animated_selector():
    """**Ein Block, der die vorhandenen Animationen nicht erfasst, ist eine
    Behauptung.** Geprüft wird deshalb nicht sein Vorhandensein, sondern seine
    Reichweite: jeder Selektor, der eine `animation` startet, muss darin
    vorkommen. Wächst die Menge der Animationen — und genau das tut sie in
    dieser Runde —, wächst der Test von selbst mit.

    Und er prüft, dass der Block **erhält** statt abzuschalten: `animation:
    none` allein nähme die Aussage mit weg, ein unsichtbarer Spinner bedeutet
    nichts. Es muss also etwas Sichtbares danebenstehen.
    """
    css = render._CSS
    start = css.find("@media (prefers-reduced-motion: reduce)")
    assert start != -1, "kein prefers-reduced-motion-Block"
    block = css[start:css.find("\n}", css.find("{", start))]

    animiert = {
        zeile.split("{")[0].strip()
        for zeile in css[:start].splitlines()
        if "animation:" in zeile and "none" not in zeile and "{" in zeile
    }
    assert animiert, "keine Animation gefunden — der Test misst dann nichts"
    fehlend = [s for s in animiert if s.split()[-1] not in block]
    assert not fehlend, f"nicht von reduced-motion erfasst: {fehlend}"
    assert "opacity" in block or "border" in block, \
        "der Block schaltet nur ab, statt die Aussage zu erhalten"
