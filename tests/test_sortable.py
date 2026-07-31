"""Sortierbare Spalten in Jobs und Archive (m.rau/bibi#66).

Sortierung gab es im FE nirgends. Die Entscheidung, die das Issue offen liess:
server- oder clientseitig. **Serverseitig**, weil der Event-Bus die Region neu
rendert — eine clientseitige Sortierung in JS wäre bei jedem Refetch weg, und
das Issue nennt genau das als Grund.

Aus demselben Grund braucht es Persistenz: ein Sortierzustand, der bei jedem
Bus-Refetch zurückspringt, wäre ärgerlicher als keiner.
"""

from __future__ import annotations

from bibi.controller import render


# --- Die Sortierung selbst ---------------------------------------------------

def _rows():
    return [
        {"slug": "beta", "payload": "echo", "last_status": "complete",
         "finished_at": 300.0, "next_fire_at": 900.0},
        {"slug": "alpha", "payload": "claude: x", "last_status": "failed",
         "finished_at": 100.0, "next_fire_at": 700.0},
        {"slug": "gamma", "payload": "echo", "last_status": "running",
         "finished_at": 200.0, "next_fire_at": None},
    ]


def test_sort_by_slug_both_directions():
    asc = [r["slug"] for r in render.sort_rows(_rows(), "slug", "asc")]
    desc = [r["slug"] for r in render.sort_rows(_rows(), "slug", "desc")]
    assert asc == ["alpha", "beta", "gamma"]
    assert desc == ["gamma", "beta", "alpha"]


def test_sort_by_type_uses_the_effective_kind():
    """Nach dem angezeigten Typ, nicht nach dem rohen Payload — sonst sortierte
    die Spalte nach etwas anderem, als in ihr steht."""
    asc = [r["slug"] for r in render.sort_rows(_rows(), "type", "asc")]
    assert asc[0] == "alpha"        # claude < job


def test_sort_by_status():
    asc = [r["slug"] for r in render.sort_rows(_rows(), "status", "asc")]
    assert asc == ["beta", "alpha", "gamma"]   # complete < failed < running


def test_sort_by_time_puts_missing_values_last_in_both_directions():
    """``None`` heisst „gibt es nicht", nicht „ganz früh". Eine Zeile ohne Wert
    gehört ans Ende — in beide Richtungen, sonst füllt sie beim Umdrehen den
    Anfang und verdrängt das, wonach jemand gerade sucht."""
    asc = [r["slug"] for r in render.sort_rows(_rows(), "next", "asc")]
    desc = [r["slug"] for r in render.sort_rows(_rows(), "next", "desc")]
    assert asc[-1] == "gamma" and desc[-1] == "gamma"


def test_unknown_key_leaves_the_order_alone():
    """Ein unbekannter Sortierschlüssel (alter Cookie, manipulierte URL) darf
    die Liste nicht leeren oder werfen — er tut schlicht nichts."""
    assert render.sort_rows(_rows(), "quatsch", "asc") == _rows()
    assert render.sort_rows(_rows(), None, None) == _rows()


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

def test_sort_survives_a_bus_refetch():
    """Der Refetch-URL des Fragments trägt die Sortierung — sonst wäre sie beim
    nächsten Bus-Ereignis weg, und genau das macht sie im Issue erwähnenswert."""
    html = render.jobs_fragment([], {}, now=0.0, sort="status", direction="desc")
    assert "sort=status" in html and "dir=desc" in html


def test_host_route_sorts_and_remembers(team_repo):
    """Die Host-Tabelle bekam klickbare Köpfe — ohne Route-Anschluss wären sie
    ein Angebot, das nichts einlöst. Genau der Zustand, der bei #65 als
    schlechter eingestuft wurde als gar kein Bedienelement."""
    from fastapi.testclient import TestClient
    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    class _C:
        def status(self): return {}
        def schedules(self):
            return [{"slug": "b", "payload": "echo", "last_status": "complete"},
                    {"slug": "a", "payload": "echo", "last_status": "failed"}]

    app = create_app(roles.resolve({"controller"}), controller_client=_C())
    with TestClient(app) as c:
        r = c.get("/-/ui/schedules/list?sort=slug&dir=desc")
        assert r.status_code == 200
        assert "sort=slug" in r.text and "dir=asc" in r.text  # naechster Klick dreht um
        assert r.cookies.get("bibi_sort") == "slug"
        assert r.cookies.get("bibi_dir") == "desc"

        # Alter Cookie / von Hand gebaute URL darf keinen Screen kosten.
        assert c.get("/-/ui/schedules/list?sort=quatsch&dir=hoch").status_code == 200
