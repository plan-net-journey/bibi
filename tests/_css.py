"""Eine winzige CSS-Kaskade über dem echten Stylesheet des Renderers.

**Warum das nötig ist, steht in `#148` und `#149`, und beide Male ist es
derselbe Satz:** ein Test, der einen Klassennamen im Markup sucht, prüft die
Verdrahtung und nicht die Wirkung. `test_offline_marks_the_scheduler_hostname_red`
hat fünf Releases lang bestätigt, dass der Hostname bei Ausfall `class="bad"`
trägt — während es im ganzen Stylesheet keine Regel für ein blankes `.bad` gab
und der Name ins Leere fiel.

Dieses Modul beantwortet stattdessen die Frage, die der Screen beantwortet:
**welchen Wert bekommt diese Eigenschaft an diesem Element tatsächlich?**

Es ist bewusst klein und kennt nur, was `render._CSS` benutzt: Typ- und
Klassenselektoren, Nachfahren-Kombinator, Vererbung für `color` und
`font-family`. Regeln mit Pseudoklassen, Attribut- oder Kind-Kombinatoren
werden übergangen — sie tragen im Renderer keine der hier gefragten
Eigenschaften, und ein halb verstandener Selektor wäre schlimmer als ein
ausgelassener: er lieferte ein Ergebnis, dem man ansieht, dass es aus einer
Rechnung kommt, ohne dass es stimmt.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

#: Eigenschaften, die sich auf Nachfahren vererben. Nur diese beiden werden
#: gebraucht; die Liste ist keine Vollständigkeitsbehauptung.
ERBT = frozenset({"color", "font-family", "font-variant-numeric"})


def stylesheet() -> str:
    """Der Inhalt von ``render._CSS`` — aus der Quelle, nicht aus dem Import.

    Dieselbe Bauart wie der Wächter aus `#94`: gelesen wird der Quelltext, weil
    ein Test über Darstellung sonst prüft, was er selbst zusammengesetzt hat.
    """
    from bibi.controller import render
    quelle = Path(render.__file__).read_text()
    return re.search(r'_CSS = """(.*?)"""', quelle, re.S).group(1)


def _regeln(css: str) -> list[tuple[str, dict[str, str]]]:
    """``(Selektor, Deklarationen)`` je Regel, in Quelltext-Reihenfolge.

    **Kommentare fallen zuerst weg, und das ist kein Detail.** Das Stylesheet
    des Renderers erklärt sich ausführlich und zitiert dabei Regeln — `#148`
    zitiert die gefallene `.hdr-row`-Zeile mitsamt ihren geschweiften Klammern.
    Ein Parser, der den Kommentar mitliest, verliert an dieser Stelle den
    Gleichlauf und findet danach keine Regel mehr. **Der Browser hat damit
    keine Mühe; nur dieses Modell hier hatte sie** — und lieferte prompt
    dasselbe `None`, das den echten Befund ausmacht. Ein Werkzeug, dessen
    Fehler wie sein Messwert aussieht, ist das gefährlichste in dieser Kiste.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    aus: list[tuple[str, dict[str, str]]] = []
    for treffer in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selektoren, koerper = treffer.group(1), treffer.group(2)
        deklarationen: dict[str, str] = {}
        for stueck in koerper.split(";"):
            if ":" not in stueck:
                continue
            name, _, wert = stueck.partition(":")
            deklarationen[name.strip().lower()] = wert.strip()
        # **Die `font:`-Kurzform zählt als Familie** — sonst meldete dieses
        # Modul für jeden Wert unter dem `body` „keine Regel", obwohl dort die
        # System-Sans steht. `keine` ist der Befund aus `#148` und muss
        # eindeutig bleiben: ein Werkzeug, dessen Lücke wie sein Messwert
        # aussieht, ist unbrauchbar.
        #
        # `font: inherit` (die Toggles, die Filterknöpfe) wird bewusst
        # übergangen statt als Wert genommen — es *ist* die Vererbung, und die
        # rechnet die Kaskade oben schon.
        kurz = deklarationen.get("font")
        if kurz and "font-family" not in deklarationen and kurz != "inherit":
            # `14px/1.55 system-ui, …` — alles hinter der Größenangabe.
            teile = kurz.split(None, 1)
            if len(teile) == 2:
                deklarationen["font-family"] = teile[1]
        if not deklarationen:
            continue
        for einzeln in selektoren.split(","):
            einzeln = einzeln.strip()
            # Was dieses Modul nicht versteht, fasst es nicht an.
            if not einzeln or any(z in einzeln for z in ":[>+~@%"):
                continue
            aus.append((einzeln, deklarationen))
    return aus


def _spezifitaet(selektor: str) -> int:
    return selektor.count("#") * 100 + selektor.count(".") * 10 + sum(
        1 for teil in selektor.split() if teil and not teil.startswith("."))


def _einzeln_passt(element: tuple[str, frozenset[str]], teil: str) -> bool:
    tag, klassen = element
    stuecke = re.findall(r"[.#]?[A-Za-z0-9_-]+", teil)
    for stueck in stuecke:
        if stueck.startswith("."):
            if stueck[1:] not in klassen:
                return False
        elif stueck.startswith("#"):
            return False        # ids kommen im Renderer nicht als Stilträger vor
        elif stueck != tag:
            return False
    return True


def _passt(kette: list[tuple[str, frozenset[str]]], selektor: str) -> bool:
    teile = selektor.split()
    if not _einzeln_passt(kette[-1], teile[-1]):
        return False
    i = len(kette) - 2
    for teil in reversed(teile[:-1]):
        while i >= 0 and not _einzeln_passt(kette[i], teil):
            i -= 1
        if i < 0:
            return False
        i -= 1
    return True


def aufgeloest(kette: list[tuple[str, frozenset[str]]], eigenschaft: str,
               css: str | None = None) -> str | None:
    """Der Wert von ``eigenschaft`` am letzten Element der ``kette``.

    ``kette`` läuft von der Wurzel zum Element. ``None`` heißt: **keine Regel
    trifft** — und genau dieser Rückgabewert ist der Befund aus `#148`.
    """
    css = stylesheet() if css is None else css
    regeln = _regeln(css)
    tiefen = range(len(kette) - 1, -1, -1) if eigenschaft in ERBT else [len(kette) - 1]
    for tiefe in tiefen:
        teilkette = kette[:tiefe + 1]
        bester: tuple[int, int] | None = None
        wert: str | None = None
        for rang, (selektor, deklarationen) in enumerate(regeln):
            if eigenschaft not in deklarationen:
                continue
            if not _passt(teilkette, selektor):
                continue
            schluessel = (_spezifitaet(selektor), rang)
            if bester is None or schluessel >= bester:
                bester, wert = schluessel, deklarationen[eigenschaft]
        if wert is not None:
            return wert
    return None


class _Baum(HTMLParser):
    """Sammelt zu jedem Element seine Ahnenkette — mehr wird nicht gebraucht."""

    #: Elemente ohne Endtag. Ohne sie verschiebt sich der ganze Stapel.
    LEER = frozenset({"br", "img", "input", "hr", "meta", "link", "col"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stapel: list[tuple[str, frozenset[str]]] = []
        self.elemente: list[tuple[list[tuple[str, frozenset[str]]], dict]] = []

    def handle_starttag(self, tag, attrs) -> None:
        merkmale = dict(attrs)
        klassen = frozenset((merkmale.get("class") or "").split())
        self.stapel.append((tag, klassen))
        self.elemente.append((list(self.stapel), merkmale))
        if tag in self.LEER:
            self.stapel.pop()

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.LEER:
            self.stapel.pop()

    def handle_endtag(self, tag) -> None:
        for i in range(len(self.stapel) - 1, -1, -1):
            if self.stapel[i][0] == tag:
                del self.stapel[i:]
                return


def ketten(html: str, wurzel: list[tuple[str, frozenset[str]]] | None = None):
    """Jedes Element des Fragments als ``(Ahnenkette, Attribute)``.

    ``wurzel`` hängt das Fragment unter seine echten Vorfahren — ein Header
    steht im `body`, eine Zeile in ihrer `table.jobs`. Ohne diesen Kontext
    prüfte man ein Markup, das so nie ausgeliefert wird.
    """
    parser = _Baum()
    parser.feed(html)
    vorne = list(wurzel or [])
    return [(vorne + kette, merkmale) for kette, merkmale in parser.elemente]


#: Der Kontext, in dem jedes Fragment dieses Renderers tatsächlich steht.
BODY: list[tuple[str, frozenset[str]]] = [("html", frozenset()),
                                          ("body", frozenset())]
