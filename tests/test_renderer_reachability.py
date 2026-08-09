"""Renderer ohne Produktions-Aufrufer (m.rau/bibi#100).

**Warum es diesen Test gibt.** `#95`, `#96`, `#99`, `#102` und `#106` sind
Ausprägungen einer Fehlerform: eine Fähigkeit wird beim Umbau nicht mitgenommen,
**während ihre Tests am alten Pfad hängen bleiben und grün weiterlaufen**. Die
Abdeckung bewacht dann den toten Pfad und lässt den lebenden unbewacht —
`_jobs_type_cell()` war über `_jobs_table` 15-fach getestet und durchgehend
grün, während die Fähigkeit, die sie prüft, im FE monatelang fehlte.

**Ein Test am toten Pfad ist schlimmer als kein Test**, weil er ein Signal
erzeugt, das wie Abdeckung aussieht.

Dieser Test misst die Menge und friert sie ein. Er findet nichts von selbst —
er verhindert, dass die Zahl **wächst**, ohne dass jemand es merkt. Wer einen
Renderer verwaisen lässt, muss ihn hier eintragen und damit begründen.
"""

from __future__ import annotations

import ast
import collections
import pathlib

#: Der Bestand am 2026-08-09, nach dem Entfernen der drei aufrufer-losen
#: Funktionen ohne Testabdeckung (`_plural`, `_effective_sched_type`,
#: `_load_more` — letztere meine eigene Hinterlassenschaft aus `#96`).
#:
#: **Was hier steht, ist Schuld, keine Erlaubnis.** Alle sechs gehören zu
#: abgelösten bibi4-Screens: `_jobs_table`/`_jobs_row` zum Jobs-Screen
#: (PLAN-17), die vier Kacheln und `daemon_page` zum Dashboard (PLAN-19/20/21).
#: Sie werden von 51 Test-Aufrufen am Leben gehalten. Der Abbau steht in
#: `#100`; er braucht eine Entscheidung je Test — prüft er eine Fähigkeit, die
#: es im v5-FE noch gibt (dann zieht er um), oder eine, die ersatzlos entfallen
#: ist (dann geht er mit). Diese Entscheidung 78-mal zu treffen war der Grund,
#: `#100` in `v0.7.12` nicht abzuschließen.
_BEKANNT_OHNE_AUFRUFER = frozenset({
    "_jobs_table",
    "_host_card",
    "_git_segment_card",
    "_mode_card",
    "_client_job_status_card",
    "daemon_page",
})


def _aufrufe(pfade) -> collections.Counter:
    """Echte Aufrufe per AST — Docstrings und Kommentare zählen nicht mit.

    Die erste Fassung dieser Erhebung war ein `grep` und zählte
    `` `_jobs_table()` `` in einem Kommentar als Aufruf; `_jobs_table` fehlte
    dadurch im Ergebnis. Der Unterschied ist der ganze Befund.

    **Gemessen werden direkte Aufrufer, nicht Erreichbarkeit.** `_jobs_row()`
    hat einen Aufrufer — `_jobs_table()`, das selbst tot ist. Es ist damit
    transitiv unerreichbar, aber nicht aufrufer-los, und taucht hier nicht auf.
    Das ist Absicht: eine transitive Analyse müsste einen Einstiegspunkt
    definieren (welche Route zählt?), und genau darüber gibt es bei
    `/-/ui/jobs/detail/…` gerade keine Einigkeit. Wer die Wurzel entfernt,
    bekommt das Blatt beim nächsten Lauf gemeldet.
    """
    c: collections.Counter = collections.Counter()
    for p in pfade:
        try:
            baum = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Call):
                name = (getattr(knoten.func, "id", None)
                        or getattr(knoten.func, "attr", None))
                if name:
                    c[name] += 1
    return c


def _ohne_aufrufer() -> set[str]:
    wurzel = pathlib.Path(__file__).resolve().parent.parent
    render = wurzel / "bibi" / "controller" / "render.py"
    defs = [n.name for n in ast.walk(ast.parse(render.read_text(encoding="utf-8")))
            if isinstance(n, ast.FunctionDef)]
    prod = _aufrufe((wurzel / "bibi").rglob("*.py"))
    return {n for n in defs if prod[n] == 0}


def test_no_new_renderer_loses_its_caller():
    """Ein Umbau, der einen Renderer verwaisen lässt, meldet sich hier."""
    neu = _ohne_aufrufer() - _BEKANNT_OHNE_AUFRUFER
    assert not neu, (
        f"{len(neu)} Renderer ohne Produktions-Aufrufer sind dazugekommen: "
        f"{sorted(neu)}. Entweder wieder aufrufen, entfernen — oder bewusst in "
        f"_BEKANNT_OHNE_AUFRUFER eintragen und im Commit begründen. Ein Test, "
        f"der nur noch den toten Pfad prüft, sieht aus wie Abdeckung und ist "
        f"keine (#100).")


def test_the_known_backlog_does_not_quietly_heal():
    """Die Gegenrichtung: wer einen der bekannten wieder anschließt oder
    entfernt, trägt ihn hier aus.

    Sonst wächst die Liste zu einem Friedhof, den niemand mehr liest — und der
    Test oben verlöre seine Schärfe, weil die Ausnahme jede neue Leiche deckt.
    """
    verschwunden = _BEKANNT_OHNE_AUFRUFER - _ohne_aufrufer()
    assert not verschwunden, (
        f"{sorted(verschwunden)} hat/haben wieder einen Aufrufer oder sind "
        f"entfernt — bitte aus _BEKANNT_OHNE_AUFRUFER austragen (#100)")
