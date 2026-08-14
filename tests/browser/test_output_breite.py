"""Ein aufgeklappter Output scrollt in seiner Zelle (`#207`).

**Befund m.rau, 2026-08-14, mit Screenshot:** *„Ein Output, der lange Spalten
hat, verbreitet die Anzeige. Es wird kein Scrollbar angezeigt!"*

Gesehen auf der Job-Detailseite von `EngineCI`, aufgeklappter Lauf mit
Coverage-Report: der Header endete bei ~710 px, die RUNS-Tabelle lief bis
~1120 px. **Die Tabelle sprengt den Seitencontainer, statt dass der Output
scrollt.**

## Warum `overflow: auto` dort nie greifen konnte

`.out-body` trägt die Regel, die es bräuchte — `max-height`, `overflow: auto`,
`white-space: pre`. Sie sitzt aber in einer `<td class="out">` einer Tabelle
mit `table-layout: auto`. Dort bestimmt der **Inhalt** die Spaltenbreite: die
Zelle nimmt die bevorzugte Breite ihres Kindes an, das Kind bekommt genau diese
Breite zugeteilt — und dann gibt es nichts mehr, worüber es überlaufen könnte.
`overflow: auto` sieht einen Inhalt, der exakt passt.

## Warum der Nachweis in den Browser gehört

**Die Frage ist eine Layout-Berechnung, und die macht nur ein Browser.** Ein
Test am Markup sähe eine `.out-body` mit `overflow: auto` und wäre grün — die
Regel steht ja da. Was fehlt, ist die Wirkung, und die hängt an der Tabelle
darüber. Derselbe Grund, aus dem `#82` seinen serverseitigen Zähler-Test
verloren hat: *„Wer ein Browser-Problem serverseitig nachstellen will, baut den
Browser nach."*

**Die lange Zeile kommt aus dem Lauf selbst, nicht aus dem Test.** Das ist die
Regel aus `v0.8.13` — ein Fixture ist eine Behauptung über die Wirklichkeit.
Hier behauptet es nichts: der Job gibt 400 Zeichen aus, und die Seite bekommt
sie auf demselben Weg wie im Betrieb.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from .browserlib import job_md, warte_bis  # noqa: E402

pytestmark = pytest.mark.browser

_CLIENT = "--synchronizer --controller"

#: 400 Zeichen ohne Leerzeichen — genau die Länge aus der Messung am laufenden
#: System. Ohne Leerzeichen, weil `white-space: pre` sonst umbrechen dürfte und
#: der Fall gar nicht entstünde.
_LANGE_ZEILE = "job: python3 -c \"print('LANG' * 100)\""


def _uid(slug: str) -> str:
    from bibi.schedule.models import job_uid
    return job_uid(slug)


def _breiten(seite) -> dict:
    """Was das Ticket gemessen hat — aber am **scrollenden** Element.

    **Das ist nicht immer die `.out-body` selbst**, und der Unterschied hat
    beim Bau einen Anlauf gekostet. Ihr CSS-Kommentar sagt es: *„Der Bereich
    traegt in aller Regel ein `<pre class="term">` in sich; dessen Regel
    entscheidet dann, dieser hier gilt fuer rohen Text."* Bei einem Lauf mit
    formatiertem Output uebernimmt das Kind den Ueberlauf, und die `.out-body`
    darueber meldet `scrollWidth == clientWidth` — **richtig, und trotzdem als
    Messwert wertlos**, weil sie nichts mehr zu verbergen hat.

    Gemessen wird deshalb der innerste Kasten, der tatsaechlich Text traegt.
    """
    return seite.evaluate("""() => {
      const t = document.querySelector('table.runs');
      const b = document.querySelector('.out-body');
      const kasten = (b && b.querySelector('pre, code')) || b;
      return {
        tabelle: t ? Math.round(t.getBoundingClientRect().width) : null,
        seite: Math.round(document.documentElement.scrollWidth),
        fenster: Math.round(document.documentElement.clientWidth),
        box_scroll: kasten ? kasten.scrollWidth : null,
        box_sicht: kasten ? kasten.clientWidth : null,
      };
    }""")


def _klappe_den_lauf_auf(seite) -> None:
    seite.eval_on_selector_all(
        ".runs .cta", "els => { if (els.length) els[0].click(); }")


@pytest.fixture
def seite_mit_langem_output(fabrik, seite):
    root = fabrik.repo("knoten")
    job_md(root, "breit", payload=_LANGE_ZEILE)
    k = fabrik.starte(root, rollen=_CLIENT)
    k.post("/-/rescan")

    code, body = k.post_json("/-/run", {"slug": "breit"})
    assert code == 200, f"der Lauf startete nicht: {code} {body}"

    seite.goto(k.url + "/-/jobs/" + _uid("breit"))
    seite.wait_for_selector("#runs", timeout=10_000)
    warte_bis(lambda: seite.query_selector_all(".runs .cta") or None,
              frist=30, was="die Lauf-Liste bekam nie eine Zeile")
    _klappe_den_lauf_auf(seite)
    warte_bis(lambda: seite.evaluate(
        "() => { const b = document.querySelector('.out-body');"
        " return b && b.textContent.includes('LANG') ? 1 : null; }"),
        frist=20, was="der Output erschien nie im aufgeklappten Kasten")
    return seite


def test_der_output_scrollt_statt_die_tabelle_zu_dehnen(seite_mit_langem_output):
    """**Der Rot-Schritt zu `#207`.**

    Heute wächst die Box mit ihrer längsten Zeile, und `table.runs` wächst mit:
    gemessen am laufenden System 1024 → 3039 px, `documentElement` 1960 → 3507.
    `scrollWidth == clientWidth` — die Box scrollt nicht, weil sie nichts zu
    verbergen hat.
    """
    m = _breiten(seite_mit_langem_output)
    assert m["box_scroll"] > m["box_sicht"], (
        f"die Box scrollt nicht, sie waechst: {m}")
    assert m["tabelle"] <= m["fenster"], (
        f"die Runs-Tabelle laeuft aus dem Fenster: {m}")


def test_die_seite_selbst_laeuft_nicht_ueber(seite_mit_langem_output):
    """**Die Gegenprobe, und sie prüft das, was m.rau gesehen hat.**

    Der eigentliche Befund war nicht die Box, sondern die Seite: der Header
    endete bei ~710 px, die Tabelle lief bis ~1120. Ein Fix, der nur die Box
    einfängt und die Tabelle weiter wachsen ließe, wäre beim Test darüber grün
    und am Bildschirm unverändert falsch.
    """
    m = _breiten(seite_mit_langem_output)
    assert m["seite"] <= m["fenster"] + 1, (
        f"die Seite laeuft horizontal ueber: {m}")
