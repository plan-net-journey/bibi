"""Ein blockierter Knoten kann es sagen (m.rau/bibi#74).

``sync_conflict`` wurde ausschliesslich **lokal** gelesen: von der Oberflaeche
desselben Knotens, von ``bibi-ctrl status``, von der Statusleiste. Ein Knoten,
dessen Synchronizer haengt, konnte seinen Zustand also niemandem mitteilen.

**Gemessen, nicht angenommen:** ``sarasate-client`` hing vom 2026-08-05 bis zum
2026-08-07 in einem Sync-Konflikt und meldete es 102-mal alle drei Minuten an
niemanden — in eine Weboberflaeche auf Port 8781, die im Normalbetrieb niemand
oeffnet. Aufgefallen ist es, weil ein Rollout zufaellig den Stand abfragte.
**Ein Release ist kein Ueberwachungswerkzeug und soll keins werden muessen.**

Die Arbeit auf einem blockierten Knoten verlaesst ihn nicht. Hier war es ein
Job-Ergebnis in einem Test-Case; auf dem Knoten eines Menschen waere es dessen
Vault-Arbeit, tagelang lokal, waehrend jede Oberflaeche gruen zeigt.

``auto_sync`` reist mit, weil es dieselbe Frage beantwortet: ist dieser Knoten
im Normalbetrieb?
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.daemon.worker_registry import WorkerRegistry


@pytest.fixture
def host_app(team_repo):  # noqa: ARG001 — parkt cwd im Test-Repo
    return create_app(roles.resolve({"scheduler", "controller"}))


def _beat(c: TestClient, **extra):
    nutzlast = {"worker": "client-1", "host": "Mac.fritz.box",
                "node_id": "n-1", **extra}
    r = c.post("/-/worker", json=nutzlast)
    assert r.status_code == 200, r.text
    return r


def test_the_heartbeat_carries_the_conflict_flag(host_app):
    """**Der Rot-Schritt von #74:** das Feld gab es nicht.

    Geprueft wird die Registry und nicht die Antwort — dort landet, was der
    Host ueber seine Knoten weiss, und von dort liest ihn jeder Screen."""
    with TestClient(host_app) as c:
        _beat(c, sync_conflict=True)
        eintraege = c.get("/-/worker").json()
    zeile = next(w for w in eintraege if w.get("node_id") == "n-1")
    assert zeile.get("sync_conflict") is True, (
        "ein blockierter Knoten kann seinen Zustand niemandem mitteilen (#74)")


def test_a_healthy_node_reports_no_conflict(host_app):
    """Die Gegenrichtung, und sie ist der halbe Wert der Meldung.

    Ohne sie waere das Feld auch dann gesetzt, wenn nichts los ist — eine
    Warnung, die immer leuchtet, wird nach dem zweiten Mal ueberlesen."""
    with TestClient(host_app) as c:
        _beat(c, sync_conflict=False)
        eintraege = c.get("/-/worker").json()
    zeile = next(w for w in eintraege if w.get("node_id") == "n-1")
    assert not zeile.get("sync_conflict")


def test_auto_sync_travels_along(host_app):
    """``auto_sync`` beantwortet dieselbe Frage: ist dieser Knoten im
    Normalbetrieb? Ein Knoten mit abgeschaltetem Sync ist nicht kaputt, aber
    seine Arbeit bleibt genauso liegen."""
    with TestClient(host_app) as c:
        _beat(c, auto_sync=False)
        eintraege = c.get("/-/worker").json()
    zeile = next(w for w in eintraege if w.get("node_id") == "n-1")
    assert zeile.get("auto_sync") is False


def test_the_registry_keeps_both_fields():
    """Eine Ebene tiefer, ohne HTTP: die Registry ist der Speicher, und ein
    Feld, das sie nicht kennt, verschwindet lautlos zwischen Route und Screen."""
    r = WorkerRegistry()
    r.heartbeat("w", "h", node_id="n-1", sync_conflict=True, auto_sync=False)
    eintrag = r.list()[0]
    assert eintrag["sync_conflict"] is True
    assert eintrag["auto_sync"] is False


def test_the_sender_reads_its_own_state(team_repo, monkeypatch):
    """Und der Knoten muss das Feld auch **fuellen**.

    Ohne diesen Test bliebe die ganze Kette gruen, waehrend jeder Heartbeat
    ``None`` traegt — gebaut waere dann ein Meldeweg ohne Meldung. Genau die
    Fehlerform, um die es in `#74` geht."""
    from bibi import state
    from bibi.daemon.heartbeat import Heartbeat

    state.set_sync_conflict(True)
    gesendet: dict = {}

    class _Client:
        def register(self, worker, host, git_status=None, **kw):
            gesendet.update(kw)
            return None

    hb = Heartbeat(client=_Client(), worker_name="w", role="synchronizer")
    hb._beat()
    assert gesendet.get("sync_conflict") is True, (
        "der Knoten liest sein eigenes Flag nicht — der Meldeweg traegt nichts")


# ── Die Anzeige (m.rau/bibi#74) ─────────────────────────────────────────────
#
# Ein Meldeweg ohne Anzeige ist kein Meldeweg. Das Flag reiste 43 Stunden lang
# korrekt in eine lokale Oberflaeche — gefehlt hat nicht die Uebertragung,
# sondern dass jemand hinsah.


def test_a_blocked_node_is_visible_in_the_nodes_screen():
    from bibi.controller import render
    html = render._clients_table(
        [{"worker": "client-1", "host": "h", "git_status": "trunk · clean · synced",
          "sync_conflict": True}], now=0.0)
    assert "sync blocked" in html, (
        "der blockierte Knoten meldet seinen Zustand, und der Screen zeigt ihn "
        "trotzdem nicht (#74)")


def test_a_healthy_node_stays_quiet_in_the_screen():
    """**Stille ist der Normalfall**, und das ist der halbe Wert der Anzeige.

    Eine Warnung, die immer leuchtet, wird nach dem zweiten Mal ueberlesen —
    dieselbe Erwaegung wie bei jeder anderen Meldung dieses Screens."""
    from bibi.controller import render
    html = render._clients_table(
        [{"worker": "client-1", "host": "h", "git_status": "trunk · clean · synced",
          "sync_conflict": False, "auto_sync": True}], now=0.0)
    assert "sync blocked" not in html
    assert "sync off" not in html


def test_a_node_that_says_nothing_is_not_declared_healthy():
    """``None`` heisst *unbekannt*, nicht *in Ordnung* — und bleibt still.
    Eine Behauptung waere hier schlimmer als eine Luecke."""
    from bibi.controller import render
    html = render._clients_table(
        [{"worker": "alt", "host": "h", "git_status": "trunk · clean · synced"}],
        now=0.0)
    assert "sync blocked" not in html
    assert "sync off" not in html


def test_auto_sync_off_is_shown_as_its_own_state():
    """Ein Knoten mit abgeschaltetem Sync ist nicht kaputt — aber seine Arbeit
    bleibt genauso liegen. Deshalb ein eigener Chip, nicht derselbe."""
    from bibi.controller import render
    html = render._clients_table(
        [{"worker": "client-1", "host": "h", "git_status": "trunk · clean · synced",
          "auto_sync": False}], now=0.0)
    assert "sync off" in html
    assert "sync blocked" not in html
