"""Eine eskalierte Merge-Quarantaene ist in keinem Frontend sichtbar (m.rau/bibi#111).

bibi fuehrt zwei Sorten Konflikt an zwei Orten. Die erste — dieser Knoten kommt
mit origin nicht klar — reist seit #74 im Heartbeat (``sync_conflict``). Die
zweite — die Arbeit EINES JOBS kommt nicht nach trunk — steht pro Branch in
``data/merge_quarantine.json`` und reiste bisher **nirgendwohin**: sichtbar nur
lokal, in ``bibi-ctrl status``/der Statusline desselben Knotens.

Live-Befund: ``agent/Witz`` scheiterte alle drei Minuten, 3 von 3 Fehlschlaegen,
eskaliert — und kein Frontend eines anderen Knotens konnte das je zeigen.
Dasselbe Rohr wie #74, ein Feld mehr in Heartbeat/Selbstauskunft, ein Chip mehr
im Nodes-Screen.
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


def test_the_heartbeat_carries_stuck_branches(host_app):
    """Der Rot-Schritt: das Feld gab es nicht."""
    with TestClient(host_app) as c:
        _beat(c, merge_stuck=["agent/Witz"])
        eintraege = c.get("/-/worker").json()
    zeile = next(w for w in eintraege if w.get("node_id") == "n-1")
    assert zeile.get("merge_stuck") == ["agent/Witz"], (
        "eine eskalierte Merge-Quarantaene kann ihren Zustand niemandem "
        "mitteilen (#111)")


def test_a_healthy_node_reports_no_stuck_branches(host_app):
    with TestClient(host_app) as c:
        _beat(c, merge_stuck=[])
        eintraege = c.get("/-/worker").json()
    zeile = next(w for w in eintraege if w.get("node_id") == "n-1")
    assert not zeile.get("merge_stuck")


def test_the_registry_keeps_the_field():
    r = WorkerRegistry()
    r.heartbeat("w", "h", node_id="n-1", merge_stuck=["agent/Witz"])
    eintrag = r.list()[0]
    assert eintrag["merge_stuck"] == ["agent/Witz"]


def test_the_sender_reads_its_own_quarantine(team_repo, monkeypatch):
    """Und der Knoten muss das Feld auch fuellen — sonst ist der Meldeweg
    gebaut, aber leer, dieselbe Fehlerform wie beim Vorbild #74."""
    from bibi.daemon import merge_quarantine
    from bibi.daemon.heartbeat import Heartbeat

    for _ in range(merge_quarantine.ESCALATE_AFTER):
        merge_quarantine.record_failure(team_repo, "agent/Witz", trunk_sha="deadbeef")
    gesendet: dict = {}

    class _Client:
        def register(self, worker, host, git_status=None, **kw):
            gesendet.update(kw)
            return None

    hb = Heartbeat(client=_Client(), worker_name="w", role="synchronizer")
    hb._beat()
    assert gesendet.get("merge_stuck") == ["agent/Witz"], (
        "der Knoten liest seine eigene Quarantaene nicht — der Meldeweg "
        "traegt nichts (#111)")


# ── Die Anzeige ──────────────────────────────────────────────────────────────


def test_a_stuck_branch_is_visible_in_the_nodes_screen():
    from bibi.controller import render
    html = render._clients_table(
        [{"worker": "sarasate", "host": "h", "git_status": "trunk · clean · synced",
          "merge_stuck": ["agent/Witz"]}], now=0.0)
    assert "agent/Witz" in html, (
        "die eskalierte Quarantaene reist jetzt, aber der Screen nennt den "
        "Branch nicht (#111)")


def test_a_healthy_node_stays_quiet_about_quarantine():
    from bibi.controller import render
    html = render._clients_table(
        [{"worker": "sarasate", "host": "h", "git_status": "trunk · clean · synced",
          "merge_stuck": []}], now=0.0)
    assert "merge stuck" not in html.lower()


def test_a_node_that_says_nothing_about_quarantine_is_not_declared_healthy():
    """``None``/fehlend heisst *unbekannt*, nicht *keine Quarantaene* — bleibt
    trotzdem still, dieselbe Regel wie bei sync_conflict: eine Behauptung waere
    hier schlimmer als eine Luecke, aber es gibt nichts Falsches zu behaupten,
    weil die Anzeige nur bei einer nicht-leeren Liste angeht."""
    from bibi.controller import render
    html = render._clients_table(
        [{"worker": "alt", "host": "h", "git_status": "trunk · clean · synced"}],
        now=0.0)
    assert "agent/" not in html
