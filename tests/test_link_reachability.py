"""Kein Link ins Leere — die Klasse hinter #104/#145/#117 und #118.

**Dreimal derselbe Fehler an drei Stellen.** ``m.rau/bibi#104`` und ``#145``
haben ihn für den Kopf des Job-Detail-Screens behoben, ``#117`` hat ihn an der
Kachel wieder eingeführt: eine Adresse, die nur auf der Maschine gilt, die sie
ausspricht, landet in einem Link für jemanden, der woanders sitzt. ``#118`` ist
seine Schwester — kein falsches Ziel, sondern gar keins.

Diese Datei prüft deshalb **nicht die Fundstellen, sondern die Zusage darüber**:
was als Link gerendert wird, muss von einem anderen Rechner aus etwas meinen,
sonst ist es kein Link, sondern Text. Ein Test je Fundstelle hätte das vierte
Auftreten wieder nicht gefunden — Welle 3 baut genau diesen Code um.

**Wo die Zusage ausdrücklich *nicht* gilt, steht als eigener Test darunter.**
Die Selbstverlinkung eines Knotens im Nodes-Screen zeigt bewusst die URL, unter
der der Knoten sich selbst kennt, ``localhost`` eingeschlossen (User-Entschei-
dung, s. ``render._node_link_cell``). Ohne diesen Gegen-Test hätte der Wächter
oben eine getroffene Entscheidung als Fehler gemeldet.
"""

from __future__ import annotations

from bibi.controller import jobs_view, render


# ── #117: die Kachel nimmt den Host, der den Lauf wirklich ausgeführt hat ───


def _kacheln(**kwargs):
    grund = dict(scheduler_slot=None, client_slot=None, scheduler_runs=[],
                 client_runs=[], now=1000.0)
    return jobs_view.build_run_list(**{**grund, **kwargs}).tiles


def test_the_scheduler_tile_takes_the_host_that_actually_ran_the_job():
    # Genau die sarasate-Topologie: Scheduler und Client auf einer Maschine,
    # verbunden über Loopback — der Verbindungs-String ist absichtlich
    # `127.0.0.1`, die Zeile weiß trotzdem, wer gelaufen ist.
    kacheln = _kacheln(
        scheduler_slot={"id": 7, "slug": "burndown-app", "status": "running",
                        "host": "sarasate", "worker": "sarasate"},
        scheduler_host="127.0.0.1", app_port=9110)
    sched = [k for k in kacheln if k.quelle == "SCHEDULER"]
    assert sched, "die Scheduler-Kachel fehlt"
    assert sched[0].host == "sarasate"


def test_a_tile_never_carries_a_loopback_host():
    # Keine Zeile, also kein besserer Kandidat: dann lieber gar kein Host als
    # einer, der beim Betrachter ins Leere zeigt.
    kacheln = _kacheln(
        scheduler_slot={"id": 7, "slug": "burndown-app", "status": "running"},
        scheduler_host="127.0.0.1", app_port=9110)
    sched = [k for k in kacheln if k.quelle == "SCHEDULER"]
    assert sched and sched[0].host is None


def test_a_locked_tile_never_carries_a_loopback_host_either():
    # Der gesperrte Zweig hat seine eigene `Tile(...)`-Stelle — #96 hat gezeigt,
    # dass genau solche Zweitstellen beim Reparieren übersehen werden.
    kacheln = _kacheln(scheduler_slot=None, scheduler_host="127.0.0.1",
                       scheduler_offline=True, app_port=9110)
    sched = [k for k in kacheln if k.quelle == "SCHEDULER"]
    assert sched and sched[0].host is None


def test_a_rendered_tile_never_links_a_loopback_address():
    # Die Zusage am gerenderten Ergebnis, nicht am Zwischenwert: hier hätte
    # auch eine vierte, noch unbekannte Fundstelle ihren Wächter.
    kacheln = _kacheln(
        scheduler_slot={"id": 7, "slug": "burndown-app", "status": "running"},
        scheduler_host="127.0.0.1", app_port=9110)
    html = "".join(render._slot_kachel(k, now=1000.0) for k in kacheln)
    assert "127.0.0.1" not in html
    assert "tile-app" not in html, "ohne erreichbaren Host darf kein App-Link entstehen"


def test_a_reachable_host_still_produces_the_app_link():
    # Gegenprobe — der Wächter darf den Normalfall nicht mitnehmen.
    kacheln = _kacheln(
        scheduler_slot={"id": 7, "slug": "burndown-app", "status": "running",
                        "host": "sarasate"},
        scheduler_host="127.0.0.1", app_port=9110)
    html = "".join(render._slot_kachel(k, now=1000.0) for k in kacheln)
    assert 'href="http://sarasate:9110/"' in html


# ── Die vierte Fundstelle, die #117 nicht genannt hat ──────────────────────
#
# Gefunden beim Absuchen nach weiteren Link-Erzeugern (die `#96`-Lehre: eine
# Fähigkeit an drei von vier Stellen eingesetzt, die vierte lebt unbemerkt
# weiter) und **live belegt**, nicht abgeleitet: das FE des Macs lieferte für
# `burndown-app` `href="http://localhost:9110/"`, während die App auf sarasate
# lief. `live_fragment()` bekommt `public_host` — den Knoten des BETRACHTERS —
# und baut daraus zwei Links: die Typ-Zelle und „Open app →".
#
# Das ist wörtlich der Fehler, den `#145` behoben hatte, an einer Stelle, die
# `#145` nicht angefasst hat.


def _fragment(**kwargs):
    grund = dict(schedule={"slug": "burndown-app", "trigger": "on_demand",
                           "app_port": 9110, "payload": {}},
                 runs=[], job=None, slug="burndown-app", now=1000.0,
                 public_host="air2024")
    return render.live_fragment(**{**grund, **kwargs})


def test_the_live_fragment_does_not_link_the_viewers_own_host():
    # Der Betrachter sitzt auf `air2024`, der Job lief auf `sarasate`.
    html = _fragment(schedule={"slug": "burndown-app", "trigger": "on_demand",
                               "app_port": 9110, "payload": {}, "host": "sarasate"})
    assert "http://air2024:9110/" not in html
    assert 'href="http://sarasate:9110/"' in html


def test_the_live_fragment_shows_the_port_without_a_link_when_the_node_is_unknown():
    # Kein bekannter ausführender Knoten: der Port ist eine Job-Eigenschaft und
    # darf stehen, die Adresse ist keine. Genau der `link=False`-Pfad, den
    # `_jobs_type_cell()` seit m.rau/bibi#104 für diesen Fall schon führt.
    html = _fragment()
    assert "app :9110" in html
    assert ":9110/" not in html, "ohne bekannten Knoten darf keine Adresse entstehen"


# ── #118: ein Knoten ohne Controller-Rolle hat kein Frontend ────────────────


def _zeile(**kwargs):
    grund = {"worker": "sarasate", "host": "sarasate", "port": 8780,
             "git_user": "m.rau", "stale": False, "connected_at": 0,
             "last_heartbeat": 990}
    return [{**grund, **kwargs}]


def test_a_node_without_the_controller_role_is_not_linked():
    # Der reine Scheduler-Knoten: `/-/` liefert dort planmäßig 404 (Entscheidung
    # 2026-08-04). Ein Link dorthin ist nicht gelegentlich, sondern strukturell
    # tot.
    html = render._clients_table(_zeile(role="scheduler,synchronizer,worker"),
                                 now=1000)
    assert "sarasate" in html
    assert "<a href=" not in html.split("</td>")[0], \
        "ohne Controller-Rolle darf der Name kein Link sein"


def test_a_node_with_the_controller_role_is_still_linked():
    html = render._clients_table(_zeile(role="controller,synchronizer"), now=1000)
    assert '<a href="http://sarasate:8780/-/"' in html


def test_a_node_that_reports_no_role_at_all_is_not_linked():
    # Kein Rollen-Feld heißt „weiß ich nicht", und aus Unwissen wird kein Link.
    # Der Fall ist real: ein älterer Client heartbeatet ohne `role`.
    html = render._clients_table(_zeile(), now=1000)
    assert "<a href=" not in html.split("</td>")[0]


# ── Die Grenze der Zusage, als Test festgehalten ────────────────────────────


def test_a_node_links_itself_by_loopback_on_purpose():
    """Die Loopback-Regel oben gilt für Kacheln, **nicht** für die Selbst-
    verlinkung im Nodes-Screen.

    Dort ist `localhost` eine getroffene Entscheidung: der Link zeigt die URL,
    unter der der Knoten sich selbst kennt (sein eigener ``BIBI_DAEMON_PORT``),
    nicht die, unter der ein anderer ihn erreichen würde. Ohne diesen Test
    meldete der Wächter eine Entscheidung als Fehler — und der nächste, der ihn
    liest, hielte sie für ein Versehen.
    """
    html = render._clients_table(
        _zeile(worker="air2024", host="localhost", port=8769, role="controller"),
        now=1000)
    assert '<a href="http://localhost:8769/-/"' in html
