"""Was von einem Screen aus erreichbar ist — und was nur so aussieht (`#100`).

**Warum es diesen Test gibt.** `#95`, `#96`, `#99`, `#102` und `#106` sind
Ausprägungen einer Fehlerform: eine Fähigkeit wird beim Umbau nicht
mitgenommen, **während ihre Tests am alten Pfad hängen bleiben und grün
weiterlaufen**. Die Abdeckung bewacht dann den toten Pfad und lässt den
lebenden unbewacht — `_jobs_type_cell()` war über `_jobs_table` 15-fach
getestet und durchgehend grün, während die Fähigkeit, die sie prüft, im FE
monatelang fehlte.

**Ein Test am toten Pfad ist schlimmer als kein Test**, weil er ein Signal
erzeugt, das wie Abdeckung aussieht.

**Die erste Fassung maß direkte Aufrufer und hat deshalb die Hälfte nicht
gesehen.** Sie fand `_jobs_table` (niemand ruft es), aber nicht `_jobs_row`
(`_jobs_table` ruft es) und schon gar nicht `jobs_detail_page` — dessen
Aufrufer ist eine *Route*, und eine Route hat immer einen Aufrufer: FastAPI.
Ein ganzer Screen konnte damit absterben, ohne dass eine einzige Zahl stieg.
So sind `/-/ui/jobs/detail/…` (zehn Routen), `/-/ui/feed/jobstatus` und
`/-/ui/self/update` durchgerutscht.

Gemessen wird deshalb **Erreichbarkeit ab den fünf Screens** der App-Bar
(`render.SCREENS`): ein Screen führt auf Routen, deren Handler rendern,
und was gerendert wird, nennt die nächsten Routen. Was dieser Fixpunkt nicht
erreicht, erreicht auch kein Mensch mit einem Browser.

**Der Korpus besteht aus Zeichenketten, die wirklich ausgegeben werden** —
nicht aus Quelltext. Kommentare und Docstrings zählen nicht mit, und das ist
kein Detail: der erste Entwurf hielt `/-/ui/jobs/detail` für lebendig, weil
der Docstring von `_action_bar()` die Adresse erwähnt. Dieselbe Falle hatte
schon die Vorgängerfassung, dort als `grep` gegen einen Kommentar.
"""

from __future__ import annotations

import ast
import pathlib

from bibi.controller import render

#: Renderer, die statisch nicht erreichbar sind und es trotzdem sein dürfen.
#:
#: **Was hier steht, ist Schuld, keine Erlaubnis** — und die Liste war leer,
#: seit `#100` abgeschlossen ist. Wer etwas einträgt, begründet es im Commit.
#:
#: `_ago_text` ist mit `#184` unerreichbar geworden, und die Schuld ist genau
#: benennbar: **die Regel „vor X Zeit" existiert zweimal**, einmal hier und
#: einmal in `_DURATION_JS`. Seit `_ago()` den Zeitpunkt-Anker trägt, rendert
#: sie nur noch der Browser — der Server liefert die absolute Form, und der
#: Ticker rechnet um, wer auf relativ gestellt hat.
#:
#: **Gelöscht wird sie trotzdem nicht, und das ist der Punkt:** sie ist die
#: einzige Server-Seite, gegen die
#: `test_the_browser_formats_durations_exactly_like_the_renderer` die JS-Regel
#: hält. Ohne sie prüfte der Vergleich die einzige verbliebene
#: Implementierung gegen sich selbst.
#:
#: **Sie fällt, wenn die Doppelung fällt** — wenn die drei Dauer-Regeln aus
#: einer Quelle in beide Sprachen kommen. Bis dahin steht sie hier und nicht
#: unbemerkt im Code.
_ERLAUBT_UNERREICHBAR: frozenset[str] = frozenset({"_ago_text"})

#: Dasselbe für Routen — mit **einem** Eintrag, und er ist Schuld.
#:
#: `/-/ui/clients/restart-all` hat mit `#103` seinen letzten Knopf verloren.
#: Die Route bleibt trotzdem, und zwar auf m.raus ausdrückliche Auftrennung
#: der Reihenfolge hin: *„Die Endpunkte können bestehen bleiben. Das FE kann
#: die Buttons trotzdem schon zurück bauen."* Der Rückbau des FE geht dem
#: automatischen Rollout voraus; heute vermittelt der Heartbeat keinen
#: Restart, und die Route ist bis dahin der einzige verbliebene Weg, einen
#: Neustart über die Föderation anzustoßen.
#:
#: **Sie fällt, wenn der Auslöser gebaut ist** — dann ersatzlos, samt der
#: Verben `restart`/`deploy` in `clients_node_action()`. Bis dahin steht sie
#: hier und nicht unbemerkt im Code.
_ERLAUBT_TOTE_ROUTEN: frozenset[str] = frozenset({"/-/ui/clients/restart-all"})

_WURZEL = pathlib.Path(render.__file__).resolve().parent.parent.parent


def _quelle(name: str) -> tuple[str, ast.Module]:
    p = _WURZEL / "bibi" / "controller" / name
    text = p.read_text(encoding="utf-8")
    return text, ast.parse(text)


def _funktionen(baum: ast.Module) -> dict[str, ast.AST]:
    """Alle Funktionen eines Moduls, **auch die verschachtelten**.

    Hier stand einen Anlauf lang ``baum.body`` statt ``ast.walk``, mit der
    Begründung, eine innere Funktion sei per Konstruktion nur über ihre äußere
    erreichbar. Das stimmt — **und macht den Wächter trotzdem blind**: in
    ``controller/__init__.py`` sind sämtliche Route-Handler innere Funktionen
    von ``create_app()``. Der Umbau meldete daraufhin 31 tote Routen, also
    fast alle.

    Die Lehre gehört zum Fund: eine Aussage über *Erreichbarkeit im Code* ist
    nicht dieselbe wie eine über *Erreichbarkeit im Betrieb*, und dieser
    Wächter misst die zweite.
    """
    return {n.name: n for n in ast.walk(baum)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _tabellen(baum: ast.Module) -> dict[str, set[str]]:
    """Modulweite Dicts/Tupel → die Namen, die als Werte darin stehen (#181).

    **Eine Dispatch-Tabelle ist ein Aufruf, den der AST nicht als solchen
    sieht.** ``KACHEL_VORRAT`` bildet Feldnamen auf Bauteil-Funktionen ab, und
    wer die Tabelle benutzt, ruft jede von ihnen — nur eben über einen
    Nachschlagevorgang statt über ihren Namen. Ohne diese Kante meldet der
    Wächter jedes Bauteil als unerreichbar und behauptet damit das Gegenteil
    dessen, was der Fall ist.

    Der Fund wäre also ein **falscher** gewesen, und das ist der teurere
    Fehler: ein Wächter, der bei richtigem Code anschlägt, wird abgeschaltet
    oder mit Ausnahmen zugedeckt, und dann fängt er auch die echten Fälle
    nicht mehr.
    """
    out: dict[str, set[str]] = {}
    for n in baum.body:
        # `ast.AnnAssign` mit: `KACHEL_VORRAT: dict = {...}` ist annotiert und
        # damit ein anderer Knoten als eine nackte Zuweisung. Der erste Entwurf
        # kannte nur `Assign` und fand die Tabelle deshalb nicht — sichtbar
        # daran, dass genau die Bauteile weiter als unerreichbar galten.
        if isinstance(n, ast.AnnAssign):
            ziele, wert = ([n.target], n.value)
        elif isinstance(n, ast.Assign):
            ziele, wert = (n.targets, n.value)
        else:
            continue
        if wert is None:
            continue
        namen = {k.id for k in ast.walk(wert) if isinstance(k, ast.Name)}
        if not namen:
            continue
        for ziel in ziele:
            if isinstance(ziel, ast.Name):
                out.setdefault(ziel.id, set()).update(namen)
    return out


def _string_konstanten(baum: ast.Module) -> dict[str, str]:
    """Modulweite String-Konstanten — die JS- und CSS-Blöcke.

    Sie tragen einen guten Teil der Adressen (`_OPS_HANDLES_JS` nennt Rescan
    und Maintenance, `_HTMX` den lokalen htmx-Pfad) und gehören deshalb in den
    Korpus, sobald eine erreichbare Funktion sie einbindet.
    """
    out: dict[str, str] = {}
    for n in baum.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) \
                and isinstance(n.value.value, str):
            for ziel in n.targets:
                if isinstance(ziel, ast.Name):
                    out[ziel.id] = n.value.value
    return out


def _umleitungen(baum: ast.Module, routen: dict[str, str]) -> set[str]:
    """Routen, deren Handler nichts tut als umzuleiten (`#162`).

    **Eine Umleitung ist kein Ziel und deshalb auch keine tote Route.** Sie ist
    der Rückweg für eine Adresse, die es einmal gab: `/-/live` stand ein halbes
    Jahr hinter einem sichtbaren Tab und liegt in Lesezeichen. Dass kein Screen
    auf sie zeigt, ist genau ihr Zweck — ein Verweis darauf wäre der Fehler.

    **Erkannt wird das an der Sache, nicht an einer Liste.** Ein Eintrag in
    ``_ERLAUBT_TOTE_ROUTEN`` wäre hier falsch: dort steht Schuld, und diese
    Route schuldet nichts. Die Regel gilt zudem für die nächste Umleitung
    mit, ohne dass jemand daran denkt.
    """
    out: set[str] = set()
    namen = {n: p for p, n in routen.items()}
    for n in ast.walk(baum):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if n.name not in namen:
            continue
        rueckgaben = [k for k in ast.walk(n) if isinstance(k, ast.Return)]
        if not rueckgaben:
            continue
        if all(isinstance(r.value, ast.Call)
               and getattr(r.value.func, "id", None) == "RedirectResponse"
               for r in rueckgaben):
            out.add(namen[n.name])
    return out


def _routen(baum: ast.Module) -> dict[str, str]:
    """Pfad → Name des Handlers. Nur die FE-Routen; `controller/__init__.py`
    führt keine Maschinen-API (die liegt in `daemon/`)."""
    out: dict[str, str] = {}
    for n in ast.walk(baum):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and d.args \
                    and isinstance(d.args[0], ast.Constant):
                pfad = d.args[0].value
                if isinstance(pfad, str) and pfad.startswith("/-/"):
                    out.setdefault(pfad, n.name)
    return out


def _bezeichner(knoten: ast.AST) -> set[str]:
    """Aufgerufene Funktionen und referenzierte Konstanten."""
    out: set[str] = set()
    for k in ast.walk(knoten):
        if isinstance(k, ast.Call):
            name = getattr(k.func, "id", None) or getattr(k.func, "attr", None)
            if name:
                out.add(name)
        elif isinstance(k, ast.Name):
            out.add(k.id)
    return out


def _ausgegebene_strings(knoten: ast.AST) -> str:
    """Nur, was die Funktion tatsächlich ausgibt — ohne ihren Docstring.

    Kommentare stehen ohnehin nicht im AST; der Docstring schon, und **genau
    er** hat den ersten Entwurf getäuscht.
    """
    doc = ast.get_docstring(knoten, clean=False) \
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    teile = [k.value for k in ast.walk(knoten)
             if isinstance(k, ast.Constant) and isinstance(k.value, str)
             and not (doc is not None and k.value == doc)]
    return "\n".join(teile)


def _rumpf(pfad: str) -> str:
    """Der feste Anfang einer Route — `/-/jobs/{uid}/runs` → `/-/jobs`.

    Weiter kommt man nicht: der Rest wird zur Laufzeit zusammengesetzt, und
    ein Muster, das Platzhalter rät, meldet Treffer, die es nicht gibt.
    """
    i = pfad.find("{")
    return (pfad[:i] if i > 0 else pfad).rstrip("/")


def _erreichbar() -> tuple[set[str], set[str], dict[str, str], dict[str, ast.AST]]:
    """Fixpunkt: Screens → Routen → Renderer → weitere Routen."""
    ctrl_text, ctrl_baum = _quelle("__init__.py")
    rend_text, rend_baum = _quelle("render.py")
    ctrl_f, rend_f = _funktionen(ctrl_baum), _funktionen(rend_baum)
    konst = _string_konstanten(rend_baum)
    tabellen = _tabellen(rend_baum)
    routen = _routen(ctrl_baum)
    alle = {**ctrl_f, **rend_f}

    def korpus_von(name: str) -> str:
        if name in alle:
            return _ausgegebene_strings(alle[name])
        return konst.get(name, "")

    namen: set[str] = set()
    erreichte = {p for p in routen if p in {h for _, h in render.SCREENS}}
    for _ in range(30):
        vorher = (set(namen), set(erreichte))
        namen |= {routen[p] for p in erreichte}
        grenze = True
        while grenze:
            grenze = False
            for n in list(namen):
                # Eine erreichte Dispatch-Tabelle macht ihre Einträge
                # erreichbar (#181) — wer sie benutzt, ruft jeden von ihnen.
                if n in tabellen:
                    neu = {x for x in tabellen[n]
                           if x in alle or x in konst or x in tabellen} - namen
                    if neu:
                        namen |= neu
                        grenze = True
                if n not in alle:
                    continue
                neu = {x for x in _bezeichner(alle[n])
                       if x in alle or x in konst or x in tabellen} - namen
                if neu:
                    namen |= neu
                    grenze = True
        korpus = "\n".join(korpus_von(n) for n in namen)
        for p in routen:
            if p not in erreichte and _rumpf(p) and _rumpf(p) in korpus:
                erreichte.add(p)
        if (namen, erreichte) == vorher:
            break
    return erreichte, namen, routen, rend_f


def test_every_route_can_be_reached_from_a_screen():
    """Eine Route, auf die nichts zeigt, ist ein Screen ohne Tür.

    Sie antwortet weiter mit `200`, ihre Tests bleiben grün, und niemand
    merkt, dass der Weg dorthin beim letzten Umbau verschwunden ist. Genau so
    hat `/-/ui/jobs/detail/…` einen Monat lang überlebt.
    """
    erreichte, _namen, routen, _ = _erreichbar()
    _, ctrl_baum = _quelle("__init__.py")
    tot = sorted(set(routen) - erreichte - _ERLAUBT_TOTE_ROUTEN
                 - _umleitungen(ctrl_baum, routen))
    assert not tot, (
        f"{len(tot)} Routen sind von keinem Screen aus erreichbar: {tot}. "
        f"Entweder verlinken, entfernen — oder bewusst in "
        f"_ERLAUBT_TOTE_ROUTEN eintragen und im Commit begründen (#100).")


def test_a_redirect_is_not_mistaken_for_a_screen():
    """Die Gegenprobe zur Umleitungs-Regel (`#162`).

    Ohne sie wäre die Regel ein Loch: sie müsste nur weit genug greifen, und
    jede unerreichbare Route hieße „Umleitung". Geprüft wird deshalb beides —
    dass ``/-/live`` als Umleitung erkannt wird **und** dass ein Screen, der
    rendert, es nicht ist.
    """
    _, ctrl_baum = _quelle("__init__.py")
    routen = _routen(ctrl_baum)
    um = _umleitungen(ctrl_baum, routen)
    assert "/-/live" in um
    assert "/-/log" not in um and "/-/nodes" not in um


def test_every_renderer_can_be_reached_from_a_screen():
    """Die Gegenprobe auf derselben Messung: kein Renderer ohne Weg.

    Sie schließt die Lücke der ersten Fassung, die nur direkte Aufrufer zählte
    und deshalb weder Blätter (`_jobs_row`) noch ganze Route-Wurzeln
    (`jobs_detail_page`) sah.
    """
    _erreichte, namen, _routen, rend_f = _erreichbar()
    tot = sorted(set(rend_f) - namen - _ERLAUBT_UNERREICHBAR)
    assert not tot, (
        f"{len(tot)} Renderer sind von keinem Screen aus erreichbar: {tot}. "
        f"Entweder wieder anschließen, entfernen — oder bewusst in "
        f"_ERLAUBT_UNERREICHBAR eintragen und im Commit begründen. Ein Test, "
        f"der nur noch den toten Pfad prüft, sieht aus wie Abdeckung und ist "
        f"keine (#100).")


def test_the_allowance_lists_stay_empty_unless_someone_argues():
    """Die Gegenrichtung: eine Ausnahmeliste, die wächst, deckt jede neue
    Leiche und nimmt den beiden Tests darüber ihre Schärfe.

    Sie war leer und soll es wieder werden. Wer einträgt, begründet — und
    dieser Test macht sichtbar, dass er es getan hat.

    **Ein Eintrag steht drin, seit #103.** `/-/ui/clients/restart-all` hat
    seinen letzten Knopf verloren; die Route bleibt auf m.raus ausdrückliche
    Auftrennung hin stehen (*„Die Endpunkte können bestehen bleiben. Das FE
    kann die Buttons trotzdem schon zurück bauen."*). Bis der automatische
    Rollout gebaut ist, ist sie der einzige verbliebene Weg, einen Neustart
    über die Föderation anzustoßen — heute vermittelt der Heartbeat keinen.

    **Sie fällt mit dem Auslöser**, und dieser Test ist die Erinnerung daran:
    er wird rot, sobald jemand die Route entfernt, ohne den Eintrag
    mitzunehmen — und er bleibt rot, wenn jemand einen zweiten einträgt.

    **Ein Renderer steht drin, seit #184.** `_ago_text` ist die Server-Seite
    der Regel *„vor X Zeit"*, und seit `_ago()` den Zeitpunkt-Anker trägt,
    rendert diese Regel nur noch der Browser: der Server liefert die absolute
    Form, der Ticker rechnet um, wer auf relativ gestellt hat.

    **Die Schuld ist die Doppelung, nicht die Unerreichbarkeit.** Die Regel
    existiert in Python und in JavaScript, und
    `test_the_browser_formats_durations_exactly_like_the_renderer` hält beide
    gegeneinander. Löschte man die Python-Seite, prüfte der Vergleich die
    verbliebene Implementierung gegen sich selbst — **ein Test, der nur noch
    bestätigen kann.** Der Eintrag fällt, wenn die drei Dauer-Regeln aus einer
    Quelle in beide Sprachen kommen.
    """
    assert _ERLAUBT_UNERREICHBAR == frozenset({"_ago_text"}), (
        "Ausnahmen sind zugelassen, aber nie stillschweigend: dieser Test "
        "gehört mit der Begründung angepasst, nicht die Liste allein.")
    assert _ERLAUBT_TOTE_ROUTEN == frozenset({"/-/ui/clients/restart-all"}), (
        "Ausnahmen sind zugelassen, aber nie stillschweigend: dieser Test "
        "gehört mit der Begründung angepasst, nicht die Liste allein.")
