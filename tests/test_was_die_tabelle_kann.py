"""Was die Tabelle kann (`v0.8.12`) — was verspricht ein Spaltenkopf?

Drei Posten aus dem `v0.8.9`-Akzeptanz-Durchgang (m.rau, 2026-08-13). Zwei
davon sind Fälle, in denen der Code seiner eigenen, geschriebenen Zusage
widerspricht — bei der Sortierung stehen Docstring und Implementierung fünf
Zeilen auseinander und behaupten Verschiedenes.

**Ein Test, der aus derselben Annahme geschrieben wird wie der Code, teilt
seinen Fehler.** Deshalb prüft hier keiner die Verdrahtung (steht `sort=x` in
der URL?), sondern durchweg die Wirkung: welche Zeile steht hinterher wo.
"""

from __future__ import annotations

import pytest

from bibi.controller import jobs_view
from bibi.controller.jobs_view import JobRow, Segment


def _row(slug: str, segment: Segment, **kwargs) -> JobRow:
    return JobRow(slug=slug, segment=segment,
                  scheduler=kwargs.pop("scheduler", {}),
                  local=kwargs.pop("local", {}),
                  spec=kwargs.pop("spec", {}), **kwargs)


# ── #178: ohne GROUP eine Liste, eine Ordnung ──────────────────────────────


def test_ohne_group_sortiert_die_ganze_liste():
    """*„die Sortierung ist Schrott, wenn die GROUP blind im Hintergrund
    2 Sortierungen produziert"* (m.rau, 2026-08-13).

    Der Rot-Schritt braucht **zwei Zeilen aus verschiedenen Bändern, deren
    Sortierschlüssel die Bandreihenfolge umkehrt** — sonst ist „nach Band, dann
    nach Spalte" von „nach Spalte" nicht zu unterscheiden. Hier steht der
    ADHOC-Job alphabetisch vorn und im Band hinten.

    Festlegung m.rau: *„ohne Gruppierung bleiben die Gruppen nicht erkennbar,
    und das ist gewollt so. Kombiniert mit Sortierung sind keine zwei Gruppen
    gebraucht!"*
    """
    rows = [_row("zeta", Segment.SCHEDULE), _row("alpha", Segment.ADHOC)]
    aus = jobs_view.sortiere(rows, nach="slug", richtung="asc", group=False)
    assert [r.slug for r in aus] == ["alpha", "zeta"], (
        "ohne GROUP gilt die Spalten-Ordnung, nicht die Band-Ordnung")


def test_mit_group_bleibt_die_bandordnung():
    """Die Gegenprobe, und ohne sie wäre der Fix eine Abschaffung der Bänder.

    Mit `GROUP` sind die Bänder eine Klassifikation *und* eine Reihenfolge —
    eine Sortierung über sie hinweg zerstörte genau die Aussage, für die es sie
    gibt.
    """
    rows = [_row("zeta", Segment.SCHEDULE), _row("alpha", Segment.ADHOC)]
    aus = jobs_view.sortiere(rows, nach="slug", richtung="asc", group=True)
    assert [r.slug for r in aus] == ["zeta", "alpha"]


def test_ohne_group_stehen_leere_werte_weiterhin_hinten():
    """Auch ohne Bänder gilt: ein Strich ist keine Zahl.

    Zeilen ohne Wert sollen nicht die erste Bildschirmhöhe füllen — die Regel
    galt bisher je Band und muss den Umbau auf **eine** Liste überleben.
    """
    rows = [
        _row("ohne", Segment.SCHEDULE, scheduler={}),
        _row("mit", Segment.ADHOC, scheduler={"last_run_at": 100.0}),
    ]
    aus = jobs_view.sortiere(rows, nach="last", richtung="asc", group=False)
    assert [r.slug for r in aus] == ["mit", "ohne"]


# ── #179: drei Spalten, nach denen sich nicht sortieren ließ ───────────────


def test_sortieren_nach_scheduler_runtime():
    """`runtime_p90` ist eine stabile Zahl — man sortiert nach dem, was dasteht.

    Im Ticket stand zunächst, diese Spalte müsse nach der Brutto-Laufzeit
    sortiert werden, weil die Zelle im Browser tickt. **Sie tickt nicht:** die
    Spalte zeigt das Perzentil der letzten abgeschlossenen Läufe (#132), und
    das rechnet der Scheduler. Der Irrtum ist mit #190 aufgeflogen.
    """
    rows = [
        _row("lang", Segment.SCHEDULE, scheduler={"runtime_p90": 90.0}),
        _row("kurz", Segment.SCHEDULE, scheduler={"runtime_p90": 3.0}),
    ]
    aus = jobs_view.sortiere(rows, nach="runtime", richtung="asc", group=True)
    assert [r.slug for r in aus] == ["kurz", "lang"]


def test_sortieren_nach_client_status():
    """*„aber die Daten existieren, oder? Können wir sie dazu holen?"* (m.rau)

    Es muss nichts geholt werden: `JobRow.local` trägt denselben Satz wie die
    Scheduler-Seite und wird bereits gerendert. **Die Zellen zeigten also
    Werte, nach denen sich nicht sortieren ließ, obwohl der Sortierer dieselbe
    Zeile in der Hand hielt.**
    """
    rows = [
        _row("b", Segment.SCHEDULE, local={"status": "running"}),
        _row("a", Segment.SCHEDULE, local={"status": "complete"}),
    ]
    aus = jobs_view.sortiere(rows, nach="client_status", richtung="asc", group=True)
    assert [r.slug for r in aus] == ["a", "b"]


def test_sortieren_nach_client_last_run():
    """Die dritte der drei Spalten — dieselbe Quelle, dieselbe Zeile."""
    rows = [
        _row("neu", Segment.SCHEDULE, local={"finished_at": 200.0}),
        _row("alt", Segment.SCHEDULE, local={"finished_at": 100.0}),
    ]
    aus = jobs_view.sortiere(rows, nach="client_last", richtung="desc", group=True)
    assert [r.slug for r in aus] == ["neu", "alt"]


def test_die_drei_koepfe_sind_klickbar():
    """Der Kopf muss die Sortierung auch anbieten — sonst ist sie unerreichbar.

    Alle drei standen als nackte `<th>` ohne `data-sort`, während die sechs
    anderen durch `_sort_kopf()` liefen.
    """
    from bibi.controller import render

    kopf = render._jobs_kopf(None, "asc")
    for schluessel in ("runtime", "client_status", "client_last"):
        assert f'data-sort="{schluessel}"' in kopf or f"sort={schluessel}" in kopf, (
            f"die Spalte {schluessel} bietet keine Sortierung an")


# ── #180: die Achse local/1shot/gone fällt ─────────────────────────────────


def test_die_journal_achse_ist_weg():
    """*„weg mit der Achse, auch auf dem Job Screen."* (m.rau, 2026-08-13)

    Ersatzlos: `local` bekommt keinen Nachfolger. Die einzige Einbuße ist
    benannt und abgenickt — *„Verstehe, aber diese Frage ist nachrangig."*
    """
    from bibi.controller import render

    assert not hasattr(render, "_FILTER_JOURNAL"), (
        "die Achse fällt samt ihrer Wertliste")


def test_trifft_filter_kennt_die_achse_nicht_mehr():
    """Der dritte Zweig geht mit — sonst bliebe toter Code mit einer URL davor.

    Eine Zeile, die früher an `journal=local` gescheitert wäre, kommt jetzt
    durch: der Filter existiert nicht mehr, und ein Parameter ohne Wirkung ist
    besser als einer, der stillschweigend etwas anderes tut.
    """
    zeile = _row("ohne-lokale-laeufe", Segment.SCHEDULE, local={})
    assert jobs_view.trifft_filter(zeile, typ=[], status=[]) is True
    with pytest.raises(TypeError):
        jobs_view.trifft_filter(zeile, typ=[], status=[], journal=["local"])
