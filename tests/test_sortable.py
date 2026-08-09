"""Die Spaltenköpfe der sortierbaren Spalten (m.rau/bibi#66).

Sortierung gab es im FE nirgends. Die Entscheidung, die das Issue offen liess:
server- oder clientseitig. **Serverseitig**, weil der Event-Bus die Region neu
rendert — eine clientseitige Sortierung in JS wäre bei jedem Refetch weg, und
das Issue nennt genau das als Grund.

**Die Tests der Sortierfunktion selbst standen bis zum 2026-08-09 hier und sind
mit ``render.sort_rows()`` entfallen (#95).** Sie waren grün und prüften einen
Pfad, den seit dem v5-Umbau niemand mehr betrat; die lebende Sortierung
(``jobs_view.sortiere()``) hatten sie nie erreicht. Was von ihnen noch gebraucht
wird, steht jetzt dort, wo es wirkt: ``test_jobs_screen.py`` prüft über die
Route, dass jeder klickbare Kopf durchkommt.
"""

from __future__ import annotations

from bibi.controller import render


# --- Die Spaltenköpfe --------------------------------------------------------

def test_header_links_carry_key_and_direction():
    head = render._sortable_head(
        [("Slug", "slug"), ("Status", "status")], sort="slug", direction="asc",
        url="/-/ui/jobs/board", target="#jobsboard")
    assert 'sort=slug&amp;dir=desc' in head, "ein zweiter Klick muss umdrehen"
    assert 'sort=status&amp;dir=asc' in head, "eine neue Spalte startet aufsteigend"
    assert "/-/ui/jobs/board" in head and "#jobsboard" in head


def test_active_column_is_marked():
    head = render._sortable_head([("Slug", "slug")], sort="slug", direction="desc",
                                 url="/x", target="#y")
    assert "sorted" in head and "▾" in head


def test_columns_without_a_key_stay_plain():
    """Nicht jede Spalte hat einen sinnvollen Schlüssel (Activity ist eine
    Sparkline). Die bleibt ein gewöhnlicher Kopf statt eines toten Links."""
    head = render._sortable_head([("Activity", None)], sort=None, direction=None,
                                 url="/x", target="#y")
    assert "<th>Activity</th>" in head


# --- Persistenz und Bus-Refetch ---------------------------------------------


