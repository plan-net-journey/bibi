"""Connected-Clients-Screen (Host, Bibi4-Iteration) — Backend (WorkerRegistry,
/-/worker) existierte schon lange, hier nur die erste Darstellung dafür."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── _clients_table()/clients_fragment()/clients_page() (rein) ──────────────


def test_clients_table_empty_state():
    html = render._clients_table([], now=100)
    assert "keine Knoten" in html


def test_clients_table_renders_worker_row():
    workers = [{
        "worker": "air2024", "host": "mac", "port": 8780, "git_user": "m.rau",
        "git_status": "trunk · clean · synced", "stale": False,
        "connected_at": 0, "last_heartbeat": 90,
    }]
    html = render._clients_table(workers, now=100)
    # Batch 9 Punkt 3: Name+Host+Port zu einem Link kombiniert statt zwei
    # getrennter Spalten.
    assert '<a href="http://mac:8780/-/" target="_blank" rel="noopener">air2024 :8780</a>' in html
    assert "m.rau" in html
    # Batch 9 Punkt 3: git-Status jetzt als Chips (Branch Klartext, Tree/Sync
    # als .chip-Spans), nicht mehr der rohe " · "-verkettete String.
    assert "trunk" in html and '<span class="chip clean">clean</span>' in html
    assert '<span class="chip synced">synced</span>' in html
    assert "connected" in html and "disconnected" not in html


def test_clients_table_link_cell_falls_back_to_plain_name_without_port():
    # Älterer Client (vor der port-Erweiterung) heartbeatet ohne port — darf
    # keinen toten Link (host ohne Port) erzeugen, nur den Namen zeigen.
    workers = [{"worker": "old-client", "host": "h", "stale": False,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "old-client" in html
    assert "<a href=" not in html


def test_node_git_status_chips_falls_back_to_plain_text_on_unexpected_format():
    assert render._node_git_status_chips("n/a") == "n/a"
    assert render._node_git_status_chips(None) == "—"


def test_clients_table_shows_disconnected_chip_when_stale():
    workers = [{"worker": "gone", "host": "h", "stale": True,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=1000)
    assert "disconnected" in html


def test_clients_table_handles_missing_git_user_gracefully():
    # Älterer Client (vor dem node_id/git_user-Ausbau) heartbeatet ohne
    # git_user — darf die Zeile nicht crashen lassen, nur "—" zeigen.
    workers = [{"worker": "old", "host": "h", "stale": False,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "old" in html


def test_clients_table_shows_role():
    # Bibi4-Iteration, User-Fund: "Client Übersicht braucht die Rollen je
    # Client" — derselbe Präzedenzfall wie git_user/node_id. Zweite
    # Iteration (User-Fund: "vielleicht als Spalten mit leerem oder
    # gefülltem Rechteck") ersetzt den rohen Komma-Text durch eine
    # Spalten-Matrix, ein Kästchen pro bekannter Rolle. Batch 9 Punkt 3:
    # CONNECT-Spalte entfällt, vier statt fünf Rollen-Spalten.
    workers = [{"worker": "air2024", "host": "mac", "role": "synchronizer,controller",
               "stale": False, "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert html.count('role-box on"') == 2
    assert html.count('role-box off"') == 2
    assert '<abbr title="Synchronizer">' in html
    assert '<abbr title="Connected">' not in html  # Batch 9 Punkt 3: CONNECT gestrichen


def test_clients_table_handles_missing_role_gracefully():
    workers = [{"worker": "old", "host": "h", "stale": False,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "<td>—</td>" in html
    assert html.count('role-box on"') == 0
    assert html.count('role-box off"') == 4


def test_clients_table_shows_engine_and_commit():
    # m.rau/bibi#19: ein Knoten konnte bisher nicht sagen, welche Engine er
    # fährt — ein Deploy war damit nicht überprüfbar. Der Commit steht neben
    # den Git-Chips, weil "synced" allein nicht beantwortet, ob zwei Knoten
    # denselben Stand haben.
    workers = [{
        "worker": "sarasate", "host": "sarasate", "stale": False,
        "git_status": "trunk · clean · synced", "git_commit": "a1b2c3d",
        "engine": "v0.2.0", "connected_at": 0, "last_heartbeat": 0,
    }]
    html = render._clients_table(workers, now=0)
    assert "<th>Engine</th>" in html
    assert "v0.2.0" in html
    assert "<code>a1b2c3d</code>" in html


def test_clients_table_flags_editable_engine():
    # Der eigentliche Anlass des Issues: ein Knoten gegen ein Arbeits-Checkout
    # sah bisher aus wie jeder andere. Jetzt trägt er einen Warn-Chip.
    workers = [{
        "worker": "mac", "host": "mac", "stale": False,
        "engine": "0.2.1 (editable)", "connected_at": 0, "last_heartbeat": 0,
    }]
    html = render._clients_table(workers, now=0)
    assert 'class="chip conflict"' in html
    assert ">editable<" in html
    # Die Version bleibt daneben lesbar, der Chip ersetzt sie nicht.
    assert "0.2.1" in html


def test_clients_table_handles_missing_engine_gracefully():
    # Ein älterer Client sendet die Felder nicht — die Zeile bleibt leer statt
    # zu brechen.
    workers = [{"worker": "old", "host": "h", "stale": False,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "<th>Engine</th>" in html
    assert "editable" not in html


def test_clients_fragment_is_bus_driven():
    # PLAN-36 Stufe 36.3: kein 10s-Self-Poll mehr — die Region haengt am
    # kollektiven Bus-Target "nodes" (Collector: WorkerRegistry-Fingerprint).
    html = render.clients_fragment([], now=0)
    assert 'id="clientsboard"' in html
    assert 'data-bus="nodes"' in html
    assert 'data-bus-refetch="/-/ui/clients/board"' in html
    assert "hx-trigger" not in html


def test_clients_page_includes_header_and_table():
    html = render.clients_page([{"worker": "w1", "host": "h1", "stale": False,
                                "connected_at": 0, "last_heartbeat": 0}], now=0)
    assert "<header>" in html
    assert "w1" in html
    assert "bibi · Nodes" in html  # Batch 9 Punkt 3: umbenannt von "Clients"


# ── Controller-Route /-/ui/clients (+/board) ────────────────────────────────
# Ein fake ControllerClient statt der echten /-/worker-Registry-Anmeldung —
# _status() macht sonst einen echten HTTP-Selbstaufruf gegen daemon_port(),
# den TestClient (ASGI-Transport, kein echter Socket) nicht bedienen kann.
# Die Registry selbst (node_id-Rekeying etc.) ist schon in test_connect.py
# unit-getestet — hier geht es nur um die Rendering-Verdrahtung der Route.


class _FakeClient:
    def __init__(self, *, workers: list[dict] | None = None) -> None:
        self._workers = workers or []
        self.node_actions: list[tuple[str, str]] = []

    def status(self) -> dict:
        return {"roles": ["scheduler", "controller"], "workers": self._workers}

    def schedules(self) -> list[dict]:
        return []

    def landings(self, *, since: float | None = None) -> list[dict]:
        return []

    def node_action(self, node_id: str, verb: str) -> dict:
        self.node_actions.append((node_id, verb))
        return {"node_id": node_id, "status": "approved" if verb == "approve" else "blocked"}

    # m.rau/bibi#39: der Restart geht direkt an den Knoten, nicht über den
    # Scheduler — hier nur aufgezeichnet.
    restarts: list[tuple] = []

    def restart_node(self, host, port, *, deployment=False, reset=False, timeout=90.0):
        type(self).restarts.append((host, port, deployment))
        return {"restarting": True, "pulled": deployment}


def test_clients_screen_route_renders_registered_worker(team_repo: Path):
    client = _FakeClient(workers=[{
        "worker": "air2024", "host": "mac", "git_user": "m.rau",
        "git_status": "trunk", "stale": False,
        "connected_at": 0, "last_heartbeat": 0,
    }])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/clients")
    assert r.status_code == 200
    assert "air2024" in r.text and "m.rau" in r.text


def test_clients_board_fragment_route(team_repo: Path):
    client = _FakeClient(workers=[{"worker": "w1", "host": "h1", "stale": False,
                                   "connected_at": 0, "last_heartbeat": 0}])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/clients/board")
    assert r.status_code == 200
    assert "w1" in r.text
    assert 'id="clientsboard"' in r.text


def test_clients_screen_route_shows_host_itself_without_any_registered_worker(team_repo: Path):
    # Batch 9 Punkt 3, User-Fund: "wir können aber doch den Host ... mit in
    # die Liste aufnehmen". WorkerRegistry kennt nur per Heartbeat gemeldete
    # Knoten — der Host meldet sich nie bei sich selbst. Die Route zeigt ihn
    # trotzdem als synthetische Zeile (_host_worker_entry()), auch ganz ohne
    # verbundene Clients — die alte "keine verbundenen Clients"-Leermeldung
    # ist für diesen Fall jetzt unerreichbar.
    app = create_app(roles.resolve({"controller"}), controller_client=_FakeClient())
    with TestClient(app) as c:
        r = c.get("/-/ui/clients")
    assert r.status_code == 200
    assert "keine Knoten" not in r.text
    assert '<span class="chip clean">connected</span>' in r.text
    assert r.text.count('role-box on"') == 1  # nur "controller" ist aktiv


# ── Open-Trust-Connect-Gate: Approve-/Block-Zelle + Route (PLAN-32 Stufe 32.1) ──


def test_node_approval_cell_pending_shows_approve_button():
    html = render._node_approval_cell("n1", "pending")
    assert '<span class="chip modified">pending</span>' in html
    assert 'hx-post="/-/ui/clients/n1/approve"' in html
    assert "Block" not in html


def test_node_approval_cell_approved_shows_block_button():
    html = render._node_approval_cell("n1", "approved")
    assert '<span class="chip clean">approved</span>' in html
    assert 'hx-post="/-/ui/clients/n1/block"' in html


def test_node_approval_cell_blocked_shows_approve_button():
    html = render._node_approval_cell("n1", "blocked")
    assert '<span class="chip conflict">blocked</span>' in html
    assert 'hx-post="/-/ui/clients/n1/approve"' in html


def test_node_approval_cell_no_node_id_no_button():
    # Host-Zeile (_host_worker_entry()) oder älterer Client ohne node_id —
    # serverseitig nicht individuell adressierbar.
    html = render._node_approval_cell(None, "approved")
    assert html == '<span class="chip clean">approved</span>'


def test_clients_table_includes_approval_column():
    workers = [{"worker": "w1", "host": "h", "node_id": "n1",
               "approval_status": "pending", "stale": False,
               "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "<th>Freigabe</th>" in html
    assert 'hx-post="/-/ui/clients/n1/approve"' in html


def test_clients_node_action_route_calls_client_and_rerenders(team_repo: Path):
    client = _FakeClient(workers=[{"worker": "w1", "host": "h", "node_id": "n1",
                                   "approval_status": "pending", "stale": False,
                                   "connected_at": 0, "last_heartbeat": 0}])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.post("/-/ui/clients/n1/approve")
    assert r.status_code == 200
    assert client.node_actions == [("n1", "approve")]
    assert 'id="clientsboard"' in r.text


def test_clients_node_action_route_rejects_unknown_verb(team_repo: Path):
    client = _FakeClient()
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.post("/-/ui/clients/n1/frobnicate")
    assert r.status_code == 404
    assert client.node_actions == []


# ── Neustart-Knöpfe (m.rau/bibi#39) ────────────────────────────────────────


def test_clients_table_offers_restart_and_deploy():
    # Zwei getrennte Verben statt eines mit Häkchen: Restart beendet nur den
    # Prozess, Deploy pullt vorher. Der Unterschied ist zu bedeutsam, um ihn
    # hinter einer Option zu verstecken.
    workers = [{"worker": "w", "host": "h", "port": 8780, "node_id": "n1",
                "stale": False, "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "<th>Neustart</th>" in html
    assert 'hx-post="/-/ui/clients/n1/restart"' in html
    assert 'hx-post="/-/ui/clients/n1/deploy"' in html
    # Ein Klick, der einen laufenden Knoten beendet, darf nicht versehentlich
    # passieren.
    assert "hx-confirm" in html


def test_clients_table_omits_restart_without_port():
    # Ohne Port gibt es keine Adresse zum Aufrufen — dann lieber keine
    # Schaltfläche als eine, die ins Leere liefe.
    workers = [{"worker": "old", "host": "h", "node_id": "n2", "stale": False,
                "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "/restart" not in html
    assert "<th>Neustart</th>" in html


def test_clients_fragment_offers_restart_all():
    # Aktion auf die Föderation, nicht auf einen Knoten — deshalb im Panel-Kopf.
    html = render.clients_fragment([], now=0)
    assert 'hx-post="/-/ui/clients/restart-all"' in html
    assert 'hx-post="/-/ui/clients/restart-all?deploy=true"' in html


def test_clients_restart_route_targets_the_node_directly(team_repo: Path):
    # m.rau/bibi#39: Host und Port kommen aus der Registry — aus dem Heartbeat
    # des Knotens selbst, also so, wie er sich erreichbar meldet.
    _FakeClient.restarts = []
    c = _FakeClient(workers=[{"worker": "w1", "host": "h1", "port": 8781,
                              "node_id": "n1", "stale": False,
                              "connected_at": 0, "last_heartbeat": 0}])
    app = create_app(roles.resolve({"controller"}), controller_client=c)
    with TestClient(app) as tc:
        r = tc.post("/-/ui/clients/n1/restart")
    assert r.status_code == 200
    assert _FakeClient.restarts == [("h1", 8781, False)]


def test_clients_deploy_route_sets_deployment_flag(team_repo: Path):
    _FakeClient.restarts = []
    c = _FakeClient(workers=[{"worker": "w1", "host": "h1", "port": 8781,
                              "node_id": "n1", "stale": False,
                              "connected_at": 0, "last_heartbeat": 0}])
    app = create_app(roles.resolve({"controller"}), controller_client=c)
    with TestClient(app) as tc:
        tc.post("/-/ui/clients/n1/deploy")
    assert _FakeClient.restarts == [("h1", 8781, True)]


def test_clients_restart_all_puts_host_last(team_repo: Path):
    """Rollierend, Host zuletzt: er trägt die Föderation. Startet er zusammen
    mit den Clients neu, laufen deren Heartbeats für die Dauer beider Neustarts
    ins Leere."""
    _FakeClient.restarts = []
    c = _FakeClient(workers=[{"worker": "client", "host": "h2", "port": 8781,
                              "node_id": "n2", "stale": False,
                              "connected_at": 0, "last_heartbeat": 0}])
    app = create_app(roles.resolve({"controller"}), controller_client=c)
    with TestClient(app) as tc:
        r = tc.post("/-/ui/clients/restart-all")
    assert r.status_code == 200
    # Der Host-Eintrag entsteht lokal (_host_worker_entry) und steht hinten.
    assert len(_FakeClient.restarts) >= 1
    assert _FakeClient.restarts[0][0] == "h2"
