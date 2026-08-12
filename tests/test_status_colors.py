"""Die Statusfarben gruppieren wie das Zustandsmodell, nicht dagegen (#68, neu gefasst in #33).

**Zwei Stellen, an denen die Darstellung etwas anderes sagte als das Modell
dahinter** — beide beim Bau der Design-Mockups am Live-Stand aufgefallen.

**`failed` sah aus wie ein Endzustand und ist keiner.** Es stand in derselben
Farbe wie `error`, `killed` und `zombie` — aber es steht **nicht** in
``lifecycle.TERMINAL``: es hat Backoff, ein gesetztes ``next_fire_at`` und den
Übergang ``RETRY → starting``.

**`deferred` sah aus wie Warten und gilt als aktiv.** Es war grau wie
``pending``, während ``_live_placeholder_row()`` es zu den **aktiven** Läufen
zählt und ``pending`` ausdrücklich nicht.

## Was `#33` daran ändert — und was es bestätigt

`#68` hat beide auf **eine** Farbe gelegt (Amber) und die Unterscheidung der
Bewegung überlassen. Das war richtig, solange **eine** Farbe alles trug.

**`#33` verteilt die Bedeutung auf drei Träger, und damit trennen sich `failed`
und `deferred` wieder** — aber an einer anderen Stelle als vor `#68`:

| | `failed` | `deferred` |
|---|---|---|
| linkes Quadrat (*eine Uhr läuft*) | gelb, langsam | gelb, langsam |
| rechtes Quadrat (*ein Prozess läuft*) | orange | grün |
| Chip (*was herausgekommen ist*) | orange | grün |

**Der Befund von `#68` ist damit nicht zurückgenommen, sondern eingelöst:** was
die beiden verbindet, ist der Wiederanlauf — das steht links und ist identisch.
Was sie trennt, ist das Ergebnis — bei `failed` ist etwas schiefgegangen, bei
`deferred` nicht. Vorher musste **eine** Farbe beides sagen und konnte es nicht.

**`failed` und `error` teilen sich jetzt wieder eine Chip-Farbe**, und auch das
ist kein Rückfall: sie unterscheiden sich am linken Quadrat, das bei `failed`
blinkt (eine Uhr läuft, der nächste Versuch steht an) und bei `error` still
steht. Die Aussage von `#68` — *„`failed` ist kein Endzustand"* — wird also
weiterhin getragen, nur nicht mehr von der Farbe allein.

Die vollständige Tabelle und ihre Prüfung stehen in
``tests/test_status_vocabulary.py``.
"""

from __future__ import annotations

import re

from bibi.controller import render


def _regel(css: str, selektor: str) -> str:
    """Der Regelblock, in dem ``selektor`` steht — oder ``""``.

    **Über mehrere Zeilen**, seit die Statusregeln ihre Selektoren umbrechen
    (`#33` legt fünf Zustände auf eine Farbe). Die erste Fassung las zeilenweise
    und fand einen umbrochenen Selektor nicht — sie gab dann ``""`` zurück, und
    ein Vergleich zweier leerer Zeichenketten ist grün. **Ein Test, der seinen
    Gegenstand nicht findet, meldet Übereinstimmung.**
    """
    for block in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        if re.search(rf"{re.escape(selektor)}\b", block.group(1)):
            return block.group(0).strip()
    return ""


def _farbe(css: str, klasse: str) -> str:
    """Die ``color``-Variable, die ``.st.<klasse>`` zugewiesen bekommt."""
    zeile = _regel(css, f".st.{klasse}")
    treffer = re.search(r"color:\s*var\((--[a-z]+)\)", zeile)
    return treffer.group(1) if treffer else ""


# ── 1: failed gehört nicht zu den Endzuständen ──────────────────────────────


def test_failed_is_told_apart_from_a_terminal_error():
    """Der Befund von `#68`, getragen vom Marker statt von der Farbe (`#33`).

    Beide Chips sind orange — *etwas ist schiefgegangen* stimmt für beide. Was
    sie trennt, ist die **Uhr links**: bei `failed` blinkt sie, weil der nächste
    Versuch ansteht; bei `error` steht sie still, weil keiner mehr kommt.
    """
    from tests.test_status_vocabulary import TABELLE
    assert TABELLE["failed"][0] != TABELLE["error"][0], \
        "failed und error sind auch links nicht zu unterscheiden"


def test_deferred_is_not_painted_like_pending():
    css = render._CSS
    assert _farbe(css, "deferred") != _farbe(css, "pending")


def test_failed_and_deferred_share_what_actually_connects_them():
    """Die eigentliche Aussage von `#68`: sie gehören zusammen — **im
    Wiederanlauf**, nicht im Ergebnis.

    `#68` musste beides auf eine Farbe legen, weil es nur eine gab. `#33` legt
    den gemeinsamen Teil nach links (die laufende Uhr, identisch) und den
    unterschiedlichen nach rechts (`failed` orange, `deferred` grün). Ohne diese
    Prüfung wäre ein Fix grün, der die beiden auch links auseinanderzieht.
    """
    from tests.test_status_vocabulary import TABELLE
    assert TABELLE["failed"][0] == TABELLE["deferred"][0] == ("yellow", "slow")
    assert TABELLE["failed"][2] != TABELLE["deferred"][2]


def test_the_model_agrees_that_they_belong_together():
    """Der Beleg steht im Modell, nicht in der Meinung: beide sind
    nicht-terminal und beide gelten als aktiv."""
    from bibi.schedule import lifecycle
    from bibi.schedule.models import Status
    assert Status.FAILED not in lifecycle.TERMINAL
    assert Status.DEFERRED not in lifecycle.TERMINAL


# ── Gegenproben: was zusammengehört, bleibt zusammen ────────────────────────


def test_the_real_terminal_errors_stay_together():
    """Sie waren rot und sind jetzt orange (`#33`) — zusammen bleiben sie.

    **Rot ist seit `#33` für „jetzt handeln" reserviert**: getrennter Knoten,
    Merge-Konflikt, Lauf über seiner ``wall_time``. Ein Endzustand, der schon
    eingetreten ist, verlangt nichts mehr sofort.
    """
    css = render._CSS
    zusammen = {_farbe(css, k) for k in ("error", "killed", "zombie")}
    assert zusammen == {"--orange"}, zusammen


def test_running_and_complete_swap_their_colours():
    """**Die ausdrücklich erteilte Umkehr** (m.rau, 2026-08-12).

    Hier stand `running` blau und `complete` grün, begründet mit *„Grün heißt in
    diesem System `complete`, keine Farbe wechselt ihre Bedeutung"* (2026-08-05).
    `#33` dreht das um: `running` ist grün, `complete` grau.

    **Die Begründung von damals ist nicht widerlegt, sondern gegenstandslos
    geworden.** Sie galt, solange eine Farbe die ganze Aussage trug; jetzt sagt
    der Chip nur noch *was herausgekommen ist*, und dort heißt grün *läuft oder
    kommt von selbst zurück*. `complete` ist grau, weil es nichts zu melden gibt.
    """
    css = render._CSS
    assert _farbe(css, "running") == "--green"
    assert _farbe(css, "complete") == "--faint"


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
