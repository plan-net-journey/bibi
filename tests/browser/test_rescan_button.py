"""Der Rescan-Knopf behält sein Zeichen (`#159`).

**Dieser Test ist beim Bau des Icon-Satzes entstanden, nicht davor** — und er
hält einen Fehler fest, den der Icon-Satz selbst erzeugt hätte.

``_OPS_HANDLES_JS`` quittiert einen Rescan, indem es das Zeichen des Knopfes
gegen ``✓`` bzw. ``✕`` tauscht und nach ein paar Sekunden zurückschreibt. Das
lief über ``textContent`` und war richtig, solange dort eine Glyphe stand:

    const idleIcon = rescan.textContent;   // "⟳"
    ...
    rescan.textContent = ok ? '✓' : '✕';
    setTimeout(() => { rescan.textContent = idleIcon; }, 1200);

**Mit einem SVG ist ``textContent`` leer.** ``idleIcon`` wäre ``""``, das
Setzen von ``textContent`` löschte das SVG — und nach der Quittung stünde ein
**leerer Knopf** da, dauerhaft, bis zum nächsten Neuladen.

**Der Test gehört hierher und nicht zu ``test_icon_set.py``**: dort wird das
Markup geprüft, das der Server ausliefert, und das wäre in beiden Fassungen
gleich. Der Fehler entsteht erst, wenn jemand klickt.

**Und die Quittungszeichen kommen aus demselben Satz.** ``✓`` und ``✕`` wären
sonst zwei weitere Glyphen aus der Systemschrift, neben drei Icons — also
wörtlich der Befund, gegen den `#159` gebaut ist, nur eine Sekunde später
sichtbar.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from bibi.controller import render  # noqa: E402

pytestmark = pytest.mark.browser


#: Irgendeine Adresse — sie wird nie wirklich aufgerufen, beide Routen unten
#: fangen sie ab. Gebraucht wird sie trotzdem: ``set_content`` lässt die Seite
#: auf ``about:blank`` stehen, und ein relatives ``fetch('/-/ui/ops/rescan')``
#: löst dort gegen nichts auf und scheitert mit einem ``TypeError``. Der erste
#: Entwurf dieses Tests ist genau daran hängengeblieben — und er sah aus wie ein
#: Befund am Knopf, war aber einer am Prüfstand.
_ORT = "http://bibi.test/-/jobs"


def _seite(page, *, antwort: str, status: int = 200) -> None:
    """Die App-Bar mit ihrem JS, gegen eine gestellte Rescan-Antwort.

    Kein Daemon: die eine Route, um die es geht, wird abgefangen. Was hier
    geprüft wird, ist die Reaktion des Knopfes auf eine Antwort — nicht, dass
    der Controller eine liefert.
    """
    page.route("**/-/ui/ops/rescan", lambda r: r.fulfill(
        status=status, content_type="application/json", body=antwort))
    page.route(_ORT, lambda r: r.fulfill(
        # `charset` ausdruecklich: ohne ihn liest Chromium das Markup als
        # Latin-1, und die Quittungszeichen kaemen als `âœ“` an. Ein Pruefstand,
        # der falsch dekodiert, meldet einen Befund, den es nicht gibt.
        status=200, content_type="text/html; charset=utf-8",
        body="<html><body>"
             f'{render._ops_handles({"maintenance": False, "roles": ["scheduler"]})}'
             f"<style>{render._CSS}</style>"
             f"<script>{render._OPS_HANDLES_JS}</script>"
             "</body></html>"))
    page.goto(_ORT)


def _sichtbares_icon(page) -> str:
    """Welches der drei Zeichen im Rescan-Knopf man **sieht**.

    Gemessen an der tatsächlichen Fläche, nicht am Markup: die drei Icons
    stehen alle im DOM, das CSS zeigt eins. Ein Test am ``innerHTML`` wäre über
    jede Fassung grün, in der die Zeichen zwar da sind, aber alle drei zugleich
    sichtbar — oder keins.

    **Nicht ``offsetParent``, und das hat einen Anlauf gekostet:** die
    Eigenschaft gibt es nur an ``HTMLElement``. An einem ``SVGElement`` ist sie
    ``undefined`` — also nie ``null``, und die Prüfung hielt alle drei Icons für
    sichtbar. Ein Prüfstand, der immer dasselbe sagt, sagt nichts.
    """
    return page.eval_on_selector(
        "#rescan",
        """el => {
            const sichtbar = [...el.querySelectorAll('svg')]
                .filter(s => s.getBoundingClientRect().width > 0);
            if (sichtbar.length !== 1) return 'ANZAHL=' + sichtbar.length;
            return sichtbar[0].classList.contains('ico-idle') ? 'idle'
                 : sichtbar[0].classList.contains('ico-ok') ? 'ok'
                 : sichtbar[0].classList.contains('ico-bad') ? 'bad' : 'FREMD';
        }""")


#: **Der Rekorder läuft vor dem Auslöser** — Handgriff 7 des Verfahrens, und er
#: hat dort drei Läufe gekostet, bevor die Bauart stand.
#:
#: Die Quittung steht 1,2 s. Wer klickt und danach nachsieht, misst mal den
#: Zwischenzustand und mal den Endzustand, je nachdem, wie schnell der nächste
#: Werkzeugaufruf ankommt — ein Test, der so gebaut ist, ist nicht falsch,
#: sondern unzuverlässig, und das ist schlimmer. Aufgezeichnet wird deshalb
#: über den Klick hinweg, und geprüft wird das Protokoll.
#:
#: **Ein ``MutationObserver`` und ausdrücklich kein ``setInterval``.** Die erste
#: Fassung tastete alle 20 ms ab und war im Einzellauf grün — im vollen
#: Browser-Lauf fiel sie um, und zwar reproduzierbar. Der Grund ist die dritte
#: Falle aus ``Iterationen.md``: **ein Tab im Hintergrund misst nichts.** Chrome
#: drosselt ``setInterval`` dort auf eine Sekunde, und die Quittung steht 1,2 s
#: — unter ``xdist`` ist höchstens einer der Browser im Vordergrund. Das
#: Protokoll war dann nicht leer, sondern **falsch**: es zeigte einen Knopf, der
#: sich nie geändert hat.
#:
#: Ein ``MutationObserver`` hängt am DOM statt an einer Uhr und wird nicht
#: gedrosselt. Er sieht jede Änderung, auch die in einem unsichtbaren Tab.
_REKORDER = """
(function(){
  const el = document.getElementById('rescan');
  const sichtbar = function(){
    const v = [...el.querySelectorAll('svg')].filter(s => s.getBoundingClientRect().width > 0);
    if (v.length !== 1) return 'ANZAHL=' + v.length;
    return v[0].classList.contains('ico-idle') ? 'idle'
         : v[0].classList.contains('ico-ok') ? 'ok'
         : v[0].classList.contains('ico-bad') ? 'bad' : 'FREMD';
  };
  const nimm = function(){
    const s = {i: sichtbar(), t: el.title};
    const l = window.__rec[window.__rec.length - 1];
    if (!l || l.i !== s.i || l.t !== s.t) window.__rec.push(s);
  };
  window.__rec = [];
  nimm();
  new MutationObserver(nimm).observe(el, {attributes: true});
})();
"""


#: Warten in zwei Schritten, und das ist keine Umständlichkeit.
#:
#: **Der Endzustand des Knopfes ist sein Anfangszustand** — `title` lautet
#: vorher wie nachher ``rescan the vault``. Ein einzelnes
#: ``wait_for_function`` darauf ist deshalb **sofort wahr** und kehrt zurück,
#: bevor irgendetwas passiert ist; das Protokoll enthält dann genau einen
#: Eintrag und der Test liest ihn als „der Knopf hat sich nie geändert".
#:
#: Im Einzellauf ging das eine Weile gut, weil der abgefangene ``fetch`` schnell
#: genug antwortete, um die Quittung noch vor dem ersten Poll zu setzen. Im
#: vollen Lauf nicht mehr. **Ein Test, der von der Reihenfolge zweier Ereignisse
#: lebt, muss auf beide warten** — erst darauf, dass sich etwas ändert, dann
#: darauf, dass es zurückkommt.
def _quittung_und_zurueck(page) -> None:
    page.wait_for_function(
        "() => document.getElementById('rescan').title !== 'rescan the vault'",
        timeout=5000)
    page.wait_for_function(
        "() => document.getElementById('rescan').title === 'rescan the vault'",
        timeout=9000)


def test_the_rescan_button_gets_its_icon_back(seite):
    """**Der Rot-Schritt.** Nach der Quittung steht das Zeichen wieder da.

    Gemessen wird nach dem Rückschreiben, also jenseits der 1200 ms — der
    interessante Moment ist der *nach* dem Ende, nicht das Ende selbst
    (Handgriff 3 des Verfahrens).
    """
    _seite(seite, antwort='{"antwort": {"inserted": 1, "updated": 0, "removed": 0}}')
    assert _sichtbares_icon(seite) == "idle"
    seite.click("#rescan")
    _quittung_und_zurueck(seite)
    assert _sichtbares_icon(seite) == "idle", \
        f"Knopf nach der Quittung: {_sichtbares_icon(seite)!r}"


def test_the_receipt_is_an_icon_too(seite):
    """Die Quittung selbst kommt aus dem Satz — kein ``✓`` aus der Systemschrift.

    Ohne diese Prüfung wäre ein Fix grün, der das Icon zwar zurückholt, für die
    Sekunde dazwischen aber wieder eine fremde Glyphe zeigt. Genau eine Sekunde
    Unruhe ist immer noch Unruhe, und sie träfe den Moment, in dem alle
    hinsehen.
    """
    _seite(seite, antwort='{"antwort": {"inserted": 0, "updated": 0, "removed": 0}}')
    seite.evaluate(_REKORDER)
    seite.click("#rescan")
    _quittung_und_zurueck(seite)
    protokoll = seite.evaluate("() => window.__rec")
    quittungen = [s for s in protokoll if s["t"].startswith("rescanned")]
    assert quittungen, f"keine Quittung im Protokoll: {protokoll}"
    for s in quittungen:
        assert s["i"] == "ok", s


def test_a_failed_rescan_says_so_and_still_recovers(seite):
    """Die Gegenprobe: der Fehlerweg schreibt dasselbe Icon zurück.

    Er hat eine eigene, längere Frist (4 s statt 1,2 s — *„ein Fehler darf
    laenger stehen bleiben"*) und damit einen eigenen Pfad durch dieselbe
    Funktion. Ein Test nur auf den Erfolgsfall ließe die Hälfte offen.
    """
    _seite(seite, antwort='{"error": "kaputt"}', status=500)
    seite.evaluate(_REKORDER)
    seite.click("#rescan")
    _quittung_und_zurueck(seite)
    protokoll = seite.evaluate("() => window.__rec")
    fehler = [s for s in protokoll if s["t"].startswith("rescan failed")]
    assert fehler, f"keine Fehlerquittung im Protokoll: {protokoll}"
    for s in fehler:
        assert s["i"] == "bad", s
    assert _sichtbares_icon(seite) == "idle"
