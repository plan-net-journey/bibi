"""Ein Swap darf die Zeitanzeige nicht umwerfen (`#160`).

**Der Befund ist ein Rückschritt aus `v0.8.7`, und er ist eine Woche alt**
(m.rau zum ausgelieferten Stand): *„Die Anzeige wackelt immer mal wieder …
dabei wird kurz absolute Zeit dargestellt und dann wieder relative Zeit bei
eingeschalteter relativer Zeit. … Dieses Flackern bezieht übrigens auch die
`-d` Diff Effekte mit sich."*

Zwei Ursachen, eine Wurzel — beide sitzen im Übergang, nicht im Ergebnis:

* ``_DURATION_JS`` lief als ``tick(); setInterval(tick, 1000)``, **ohne**
  Aufruf an ``htmx:afterSettle``. Jedes frisch eingetauschte Fragment trägt die
  absoluten Zeiten des Servers, bis der nächste Sekundentakt kommt.
* ``_DIFF_JS`` verglich ``td.textContent``. In relativer Anzeige steht vorher
  ``39s ago`` und nachher ``17:16:39`` — verschieden. **Also blitzte jede
  Zeitpunkt-Zelle bei jedem Swap, ohne dass sich ein Wert geändert hatte.**

## Warum das hier steht und nicht bei ``test_cell_diff.py``

Jene Datei prüft den **Vertrag** — welche Zelle sich abmeldet, woran der
Vergleich eine Zeile wiedererkennt. Sie kann nicht prüfen, was ein Swap *tut*,
und genau dort saß der Fehler. Der Docstring dort sagt es selbst: was in der
Bewegung passiert, gehört auf eine Ebene, die Bewegung sehen kann.

**Ohne Daemon und ohne htmx.** Die Seite wird mit ``set_content`` gestellt und
die beiden htmx-Ereignisse werden von Hand gefeuert — geprüft wird die Reaktion
der beiden Skripte auf einen Swap, nicht htmx' Fähigkeit, einen auszulösen.
Das hält den Test bei rund einer Sekunde statt bei einer halben Minute.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from bibi.controller import render  # noqa: E402

pytestmark = pytest.mark.browser

#: Ein fester Zeitpunkt, damit die absolute Form vorhersagbar ist.
_AT = 1_754_000_000.0


def _seite(page, *, tfmt: str, zeilen: str) -> None:
    """Eine Tabelle mit Zeitpunkt-Zellen, plus die beiden Skripte."""
    page.set_content(
        f'<html data-tfmt="{tfmt}"><body>'
        f'<div id="ziel"><table><tbody>{zeilen}</tbody></table></div>'
        f"<script>{render._DIFF_JS}</script>"
        f"<script>{render._DURATION_JS}</script>"
        f"</body></html>")


def _zeile(*, at: float, abs_text: str, andere: str = "ruhig") -> str:
    """Eine Zeile mit einer Zeitpunkt-Zelle und einer gewöhnlichen daneben."""
    return (f'<tr data-row="job-a">'
            f'<td><span data-tp="{at}" data-abs="{abs_text}">{abs_text}</span></td>'
            f"<td>{andere}</td>"
            f"</tr>")


def _swap(page, neue_zeilen: str) -> None:
    """Ein Swap, wie htmx ihn fährt: ``beforeSwap`` → Markup tauschen →
    ``afterSettle``, beide mit ``detail.target`` auf dem getauschten Bereich.

    **Der Rekorder muss vor dem Auslöser laufen** (Handgriff 7 des Verfahrens):
    der Schnappschuss entsteht in ``beforeSwap``, also bevor das neue Markup
    im Dokument steht. Wer erst tauscht und dann meldet, misst nichts.
    """
    page.evaluate(
        """(html) => {
            const t = document.getElementById('ziel');
            document.body.dispatchEvent(new CustomEvent(
                'htmx:beforeSwap', {detail: {target: t}}));
            t.querySelector('tbody').innerHTML = html;
            document.body.dispatchEvent(new CustomEvent(
                'htmx:afterSettle', {detail: {target: t}}));
        }""", neue_zeilen)


def _flash_auf_zeitzellen(page) -> int:
    return page.evaluate(
        "() => document.querySelectorAll('td.cellflash:has([data-tp])').length")


def test_a_swap_never_shows_an_absolute_time_while_relative_is_on(seite):
    """Die erste Hälfte von `#160`: das frische Fragment trägt absolute Zeiten,
    und bis zum nächsten Sekundentakt standen sie da.

    **Gemessen unmittelbar nach dem Settle, nicht nach einer Sekunde.** Wer
    wartet, misst das Ergebnis und nennt es einen Erfolg — genau so ist der
    Fehler im `v0.8.7`-Durchgang als *Erfolgsnachweis* ins Release-Memo
    gekommen (*„der Ticker hat sie binnen eines Intervalls wieder relativ
    geschrieben"*). Der Weg dorthin war der Fehler, nicht das Ziel.
    """
    _seite(seite, tfmt="rel", zeilen=_zeile(at=_AT, abs_text="17:16:39"))
    _swap(seite, _zeile(at=_AT, abs_text="17:16:39"))
    text = seite.eval_on_selector("[data-tp]", "el => el.textContent")
    assert "17:16:39" not in text, f"absolute Zeit nach dem Swap sichtbar: {text!r}"
    assert "ago" in text or "in " in text or text == "asap", text


def test_a_swap_does_not_flash_a_timestamp_that_did_not_change(seite):
    """Die zweite und schwerere Hälfte: der Diff verglich gerenderten Text.

    ``39s ago`` gegen ``17:16:39`` ist verschieden, also blitzte **jede**
    Zeitpunkt-Zelle bei jedem Swap. Das Aufflammen sagt *„hier hat sich etwas
    geändert"* — und sagte damit die Unwahrheit, bei jedem Swap und in jeder
    Zeitspalte.
    """
    _seite(seite, tfmt="rel", zeilen=_zeile(at=_AT, abs_text="17:16:39"))
    _swap(seite, _zeile(at=_AT, abs_text="17:16:39"))
    assert _flash_auf_zeitzellen(seite) == 0


def test_a_timestamp_that_really_changed_still_flashes(seite):
    """**Die Gegenprobe, und sie ist zwingend.**

    Ohne sie wäre auch ein Fix grün, der den Diff für Zeitpunkte schlicht
    abschaltet — und der nähme dem Aufflammen ausgerechnet die Spalte, in der
    sich am häufigsten etwas ändert.
    """
    _seite(seite, tfmt="rel", zeilen=_zeile(at=_AT, abs_text="17:16:39"))
    _swap(seite, _zeile(at=_AT + 3600, abs_text="18:16:39"))
    assert _flash_auf_zeitzellen(seite) == 1


def test_the_flash_survives_the_absolute_setting_too(seite):
    """Dieselbe Zusage in der anderen Anzeigeform.

    Der Diff vergleicht ab jetzt den Wert aus ``data-tp``. Damit ist er gegen
    **jedes** künftige Anzeigeformat immun und nicht nur gegen dieses — das ist
    der Grund, warum der Weg über den Wert und nicht über einen Sonderfall für
    die relative Form geht.
    """
    _seite(seite, tfmt="abs", zeilen=_zeile(at=_AT, abs_text="17:16:39"))
    _swap(seite, _zeile(at=_AT, abs_text="17:16:39"))
    assert _flash_auf_zeitzellen(seite) == 0
    _swap(seite, _zeile(at=_AT + 60, abs_text="17:17:39"))
    assert _flash_auf_zeitzellen(seite) == 1


def test_an_ordinary_cell_still_flashes_when_its_text_changes(seite):
    """Die zweite Gegenprobe: der Diff bleibt für alles andere ein Textvergleich.

    Ohne sie wäre ein Fix grün, der auf **jeder** Zelle nur noch Attribute
    vergleicht — und der sähe keine einzige gewöhnliche Änderung mehr.
    """
    _seite(seite, tfmt="rel",
           zeilen=_zeile(at=_AT, abs_text="17:16:39", andere="ruhig"))
    _swap(seite, _zeile(at=_AT, abs_text="17:16:39", andere="laut"))
    n = seite.evaluate(
        "() => document.querySelectorAll('td.cellflash').length")
    assert n == 1
