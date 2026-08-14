"""Connected-Clients-Screen (Host, Bibi4-Iteration) — Backend (WorkerRegistry,
/-/worker) existierte schon lange, hier nur die erste Darstellung dafür."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


# ── _clients_table()/clients_fragment()/clients_page() (rein) ──────────────


def test_clients_table_empty_state():
    html = render._clients_table([], now=100)
    assert "no nodes" in html


def test_clients_table_renders_worker_row():
    workers = [{
        "worker": "air2024", "host": "mac", "port": 8780, "git_user": "m.rau",
        # `role` trägt seit #118 den Link: ohne Controller-Rolle gibt es kein
        # `/-/`, und der Name bleibt Text. Ein Client hat sie, deshalb steht sie
        # hier — die Zeile beschreibt einen Knoten mit Frontend.
        "role": "controller,synchronizer",
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


def test_node_git_status_chips_shows_conflict_tree_with_its_own_class():
    """#114: working_tree_status() kann jetzt "conflict" liefern (offener
    Merge) — der Nodes-Screen darf dafuer nicht auf die generische Fallback-
    Klasse "chip" zurueckfallen wie fuer ein wirklich unbekanntes Wort."""
    html = render._node_git_status_chips("trunk · conflict · synced")
    assert '<span class="chip conflict">conflict</span>' in html, (
        f"conflict faellt auf die generische Chip-Klasse zurueck: {html!r}")


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
    assert '<td class="mono">—</td>' in html
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
    assert "no nodes" not in r.text
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
    assert "<th>Approval</th>" in html
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


# ── Neustart-Knöpfe (m.rau/bibi#39) — im FE zurückgebaut mit #103 ──────────
#
# Hier standen drei Tests auf die Knöpfe: `Restart`/`Deploy` je Zeile, die
# leere Zelle ohne Port, und `Restart all`/`Deploy all` im Panel-Kopf. Sie
# beschrieben ein FE, das es nicht mehr gibt. Was an ihre Stelle tritt, steht
# am Fuß dieser Datei — dieselben Stellen, umgekehrte Aussage.
#
# **Die Routen darunter sind unberührt geblieben**, und die Tests darauf
# stehen weiter: der Rückbau war FE-only.


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


def test_expected_version_form_shows_current_ref(monkeypatch):
    # m.rau/bibi#39: der angezeigte Wert kommt aus pyproject.toml (der Absicht),
    # nicht aus der Lock (dem Ergebnis).
    from bibi.daemon import deploy as dm
    monkeypatch.setattr(dm, "current_ref", lambda root=None: "v0.2.3")
    html = render.clients_fragment([], now=0)
    assert 'name="version"' in html
    assert 'value="v0.2.3"' in html
    assert 'hx-post="/-/ui/clients/expected-version"' in html
    # `Set + deploy` ist mit #103 gefallen — das Setzen IST seit der
    # Entscheidung der Auslöser, nicht ein zweiter Klick danach.


def test_expected_version_form_reports_failure_prominently(monkeypatch):
    # Der Fehlerfall ist der wichtigere: uv lock scheitert, wenn der Tag nicht
    # existiert — dann wurde zurückgerollt und nichts committet. Das muss man
    # sehen, sonst hält man den Deploy für erfolgt.
    from bibi.daemon import deploy as dm
    monkeypatch.setattr(dm, "current_ref", lambda root=None: "v0.2.3")
    html = render.clients_fragment(
        [], now=0,
        deploy_result={"ok": False, "error": "uv lock fehlgeschlagen für v9.9.9",
                       "detail": "no such tag"})
    assert 'class="chip conflict"' in html
    assert "v9.9.9" in html


def test_expected_version_form_warns_when_not_pushed(monkeypatch):
    # Ohne Push bleibt die Absicht lokal und der Deploy liefe auf den anderen
    # Knoten ins Leere — das darf nicht wie Erfolg aussehen.
    from bibi.daemon import deploy as dm
    monkeypatch.setattr(dm, "current_ref", lambda root=None: "v0.2.3")
    html = render.clients_fragment(
        [], now=0,
        deploy_result={"ok": True, "changed": True, "ref": "v0.2.3",
                       "was": "v0.2.2", "pushed": False})
    assert "NOT pushed" in html


# ── m.rau/bibi#44: ein Sitzungs-Knoten hat keinen Supervisor ────────────────
#
# **Der Befund hat den Rückbau aus #103 überlebt, sein Knopf nicht.** Die drei
# Tests hier prüften Chip *und* Verb *und* Rückfrage — die letzten beiden gab
# es nur, weil es einen Knopf gab, der die eigene Sitzung abschoss. Was bleibt,
# ist die Aussage über den Knoten, und sie wird mit dem automatischen Rollout
# wichtiger: geprüft am Fuß dieser Datei.


def test_a_node_of_unknown_origin_claims_nothing():
    """Ein Client, der noch kein ``session`` meldet (älter als #44), schweigt —
    eine Behauptung über die Herkunft wäre schlechter als keine."""
    workers = [{"worker": "alt", "host": "h", "port": 8782, "node_id": "n3",
                "stale": False, "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert ">session<" not in html


# ── Der Nodes-Screen auf einem Client (Selbstaufruf-Falle, vierter Fall) ────
#
# Der Screen war für den **Host** gebaut und funktionierte dort: seine
# WorkerRegistry führt jeden Knoten, der sich per Heartbeat gemeldet hat. Auf
# einem Client ist ``status["workers"]`` leer — dort meldet sich niemand an —,
# und übrig blieb allein die synthetische Eigenzeile. Mit dem Wegfall der
# ``controller``-Rolle auf sarasate am 2026-08-04 gab es keinen Knoten mehr, auf
# dem er richtig funktionierte: eine Rollenänderung hat einen latenten Fehler
# sichtbar gemacht, nicht verursacht.
#
# Dass es ein Fehler ist und keine Absicht, sagt der Screen selbst — er trägt
# „Restart all", „Deploy all" und die erwartete Engine-Version. Flottensteuerung
# auf einer Tabelle mit einem einzigen Knoten ist sinnlos.


def _knoten(html: str) -> set[str]:
    """Die Namen **in der Tabelle**, nicht auf der Seite.

    Die vierte Lehre aus m.rau/bibi#131: ein ``"X" in html`` prüft das ganze
    Dokument. Der Hostname des Schedulers steht auch in der Kopfzeile — ein
    Test darauf wäre grün, ohne dass je eine Zeile entstünde.
    """
    koerper = html.split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    namen = set()
    for tr in re.findall(r"<tr>.*?</tr>", koerper, re.S):
        erste = re.search(r"<td>(.*?)</td>", tr, re.S)
        if erste:
            namen.add(re.sub(r"<[^>]+>", "", erste.group(1)).strip().split(" :")[0])
    return namen


def _worker(name: str, node_id: str, port: int = 8781) -> dict:
    return {"worker": name, "host": name, "port": port, "node_id": node_id,
            "git_user": "m.rau", "git_status": "trunk · clean · synced",
            "engine": "bibi5 @ a48c6db", "stale": False,
            "connected_at": 0, "last_heartbeat": 0, "approval_status": "approved"}


class _FernerScheduler:
    """Ersatz für ``ControllerClient``, wenn der Scheduler **woanders** läuft.

    Fabrik und Verbindung in einem: sowohl ``_scheduler_status()`` als auch
    ``_host_client()`` rufen ``ControllerClient(url, timeout=…)``.
    """

    def __init__(self, status: dict) -> None:
        self._status = status

    def __call__(self, url: str, *, timeout: float = 5.0):
        return self

    def status(self) -> dict:
        return self._status

    def schedules(self) -> list[dict]:
        return []

    def journal(self, **_):
        return []


def _client_app(monkeypatch, scheduler_status: dict):
    """Ein Knoten mit ``controller``-Rolle, dessen Scheduler entfernt läuft."""
    from bibi import controller as controller_pkg

    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://scheduler.invalid:8780")
    monkeypatch.setattr(controller_pkg, "ControllerClient",
                        _FernerScheduler(scheduler_status))
    return create_app(roles.resolve({"controller"}), controller_client=_FakeClient())


def test_the_nodes_screen_on_a_client_shows_the_whole_federation(
        team_repo: Path, monkeypatch):
    """**Der Fund vom 2026-08-04 nachts.** Ein Client führt keine
    Worker-Registry — die Föderationssicht gehört dem Scheduler.

    Live gemessen: ``/-/worker`` des Schedulers führte beide Knoten
    (``sarasate-client`` und ``Mac.fritz.box``, beide ``stale: false``), der
    Nodes-Screen des Mac zeigte davon **einen**: sich selbst. Der Header
    derselben Seite schrieb zwei Zeilen darüber ``clients 2`` — der Screen
    widersprach sich also innerhalb eines Bildschirms.
    """
    app = _client_app(monkeypatch, {
        "roles": ["scheduler"],
        "workers": [_worker("sarasate-client", "n-client"),
                    _worker("fremder-knoten", "n-fremd")],
    })
    with TestClient(app) as c:
        html = c.get("/-/ui/clients").text
    assert {"sarasate-client", "fremder-knoten"} <= _knoten(html)


def test_the_nodes_screen_on_a_client_shows_the_scheduler_itself(
        team_repo: Path, monkeypatch):
    """Der Scheduler heartbeatet sich nie bei sich selbst — er steht in keiner
    Registry, auch nicht in seiner eigenen.

    Auf dem Host war das folgenlos: dort entstand seine Zeile lokal
    (``_host_worker_entry()``). Von einem Client aus gibt es diese Quelle nicht,
    und ohne Ersatz fehlte ausgerechnet der Knoten, dem die Flotte gehört.
    ``Restart all`` hätte ihn stumm ausgelassen — genau die halbe Reparatur,
    die m.rau/bibi#122 gekostet hat.
    """
    app = _client_app(monkeypatch, {
        "roles": ["scheduler"],
        "workers": [_worker("sarasate-client", "n-client")],
        "node": _worker("sarasate", "n-sched", port=8780),
    })
    with TestClient(app) as c:
        html = c.get("/-/ui/clients").text
    assert "sarasate" in _knoten(html)


def test_the_own_node_appears_exactly_once(team_repo: Path, monkeypatch):
    """Die Gegenprobe zur ersten Hälfte: kennt der Scheduler uns bereits, darf
    die synthetische Eigenzeile nicht danebenstehen.

    Die Registry-Zeile ist dabei die reichere — sie trägt ``Connected seit`` und
    ``Letzter Heartbeat``, die eine lokal gebaute Zeile nicht kennen kann.
    """
    from bibi import config

    eigen = config.node_id()
    app = _client_app(monkeypatch, {
        "roles": ["scheduler"],
        "workers": [_worker("testnode.invalid", eigen, port=64409)],
        "node": _worker("sarasate", "n-sched", port=8780),
    })
    with TestClient(app) as c:
        html = c.get("/-/ui/clients").text
    koerper = html.split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    assert len(re.findall(r"<tr>", koerper)) == 2, koerper


def test_an_absent_scheduler_still_shows_the_own_node(team_repo: Path, monkeypatch):
    """Ist der Scheduler weg, bleibt die eigene Zeile — sonst stünde der Screen
    leer da und behauptete, es gebe keinen Knoten, während man auf ihm sitzt."""
    from bibi import controller as controller_pkg

    class _Tot:
        def __call__(self, url, *, timeout=5.0):
            return self

        def __getattr__(self, name):
            def _ruf(*_a, **_kw):
                raise OSError("scheduler weg")
            return _ruf

    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://scheduler.invalid:8780")
    monkeypatch.setattr(controller_pkg, "ControllerClient", _Tot())
    app = create_app(roles.resolve({"controller"}), controller_client=_FakeClient())
    with TestClient(app) as c:
        html = c.get("/-/ui/clients").text
    assert "no nodes" not in html
    assert "testnode.invalid" in _knoten(html)


def test_restart_all_on_a_client_reaches_the_whole_federation(
        team_repo: Path, monkeypatch):
    """Der funktionale Beweis, dass die Liste die Flotte ist und nicht die
    Anzeige: „Restart all" läuft über dieselbe Quelle."""
    _FakeClient.restarts = []
    app = _client_app(monkeypatch, {
        "roles": ["scheduler"],
        "workers": [_worker("sarasate-client", "n-client", port=8781)],
        "node": _worker("sarasate", "n-sched", port=8780),
    })
    with TestClient(app) as c:
        r = c.post("/-/ui/clients/restart-all")
    assert r.status_code == 200
    getroffen = {port for _host, port, _deploy in _FakeClient.restarts}
    assert {8780, 8781} <= getroffen, _FakeClient.restarts


def test_restart_all_leaves_the_scheduler_and_the_own_node_for_last(
        team_repo: Path, monkeypatch):
    """Rollierend, und die Reihenfolge ist nicht beliebig.

    **Der Scheduler trägt die Föderation** — startet er zusammen mit den
    Clients neu, laufen deren Heartbeats für die Dauer beider Neustarts ins
    Leere. **Der eigene Knoten führt die Schleife aus** — wer sich selbst in der
    Mitte neu startet, stellt den Rest nie zu.

    Auf dem Host fielen beide Rollen zusammen, „Host zuletzt" genügte. Von einem
    Client aus sind es zwei verschiedene Knoten, und die eigene Zeile stand
    ohne Sortierung ganz **vorn**: der Klick hätte zuerst den Browser abgehängt,
    der ihn ausgelöst hat.
    """
    _FakeClient.restarts = []
    monkeypatch.setenv("BIBI_DAEMON_PORT", "64409")
    app = _client_app(monkeypatch, {
        "roles": ["scheduler"],
        "workers": [_worker("anderer-client", "n-anderer", port=8782)],
        "node": dict(_worker("sarasate", "n-sched", port=8780),
                     role="synchronizer,scheduler,worker"),
    })
    with TestClient(app) as c:
        c.post("/-/ui/clients/restart-all")
    assert [port for _host, port, _deploy in _FakeClient.restarts] == [8782, 8780, 64409]


# ── #103: die Knöpfe fallen im FE, die Endpunkte bleiben ────────────────────
#
# Entscheidung m.rau, 2026-08-09, zu #98: *„diese Knöpfe entfallen. Deployment
# soll automatisch erfolgen, wenn eine Version gesetzt ist. Rückbau!"* — und
# die Reihenfolge ist ausdrücklich aufgetrennt: *„Die Endpunkte können bestehen
# bleiben. Das FE kann die Buttons trotzdem schon zurück bauen."*
#
# Der entschiedene Bauplan liegt außerhalb des Boards, in
# `20260805.LiveLogNodes.md`: *„Ein Scheduler/Worker/Client kann nicht
# gestoppt, gestartet, restartet oder deployed werden. Das gilt ebenso für
# Restart all und Deploy all. Das sind keine vorgesehenen Aktionen."*


def test_no_node_offers_a_restart_or_deploy_anymore():
    workers = [{"worker": "w", "host": "h", "port": 8780, "node_id": "n1",
                "stale": False, "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert "/-/ui/clients/n1/restart" not in html
    assert "/-/ui/clients/n1/deploy" not in html
    assert "<th>Restart</th>" not in html


def test_the_session_node_offers_no_stop_either():
    workers = [{"worker": "air2024", "host": "mac", "port": 8780, "node_id": "n1",
                "session": True, "stale": False, "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert ">Stop" not in html
    assert "Deploy + stop" not in html
    assert "nobody brings it back" not in html


def test_the_session_chip_moves_instead_of_disappearing():
    """**Die Entscheidung, die der Rückbau erzwingt.**

    Der Chip sitzt heute *in* der Restart-Zelle, weil er vor einem Klick warnt,
    der die eigene Sitzung abschießt (#44). Mit der Zelle verschwände er — und
    das wäre falsch: er sagt etwas über den **Knoten**, nicht über den Knopf,
    und diese Aussage wird mit dem Auto-Upgrade *wichtiger* statt überflüssig.
    Ein Sitzungs-Knoten, der sich selbst neu startet, kommt von allein nicht
    zurück.
    """
    workers = [{"worker": "air2024", "host": "mac", "port": 8780, "node_id": "n1",
                "session": True, "stale": False, "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert ">session<" in html
    assert "no supervisor" in html


def test_a_supervised_node_carries_no_session_chip():
    workers = [{"worker": "sarasate", "host": "s", "port": 8781, "node_id": "n2",
                "session": False, "stale": False, "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert ">session<" not in html


def test_the_panel_head_offers_no_fleet_restart_anymore():
    html = render.clients_fragment([], now=0)
    assert "restart-all" not in html
    assert "Restart all" not in html
    assert "Deploy all" not in html


def test_setting_the_expected_version_stays_and_deploying_goes():
    """Der Nodes-Screen tut laut Bauplan genau das: *„Mit dem Nodes Screen wird
    die SOLL Version spezifiziert."* Der zweite Knopf war der Handgriff danach
    — und genau der ist der Auslöser geworden."""
    html = render._expected_version_form(None)
    assert ">Set" in html
    assert "Set + deploy" not in html
    assert "deploy=true" not in html


def test_approve_and_block_are_untouched():
    """Die Gegenprobe, die verhindert, dass der Rückbau zu weit geht: sie sind
    die einzigen Handles, die der Bauplan ausdrücklich vorsieht."""
    workers = [{"worker": "w", "host": "h", "port": 8780, "node_id": "n1",
                "approval_status": "approved", "stale": False,
                "connected_at": 0, "last_heartbeat": 0}]
    html = render._clients_table(workers, now=0)
    assert 'hx-post="/-/ui/clients/n1/block"' in html
    workers[0]["approval_status"] = "pending"
    assert 'hx-post="/-/ui/clients/n1/approve"' in render._clients_table(workers, now=0)


def test_the_endpoints_stay_reachable(team_repo: Path):
    """**FE-only**, nach m.raus Auftrennung. `release.sh` rollt über
    `ssh … systemctl restart` aus und ruft keine dieser Routen — der Satz aus
    dem Ticket, es gäbe ohne die Knöpfe keinen Weg mehr, gilt für das FE und
    nicht für das Release. Sie fallen erst, wenn der Auslöser gebaut ist."""
    app = create_app(roles.resolve({"controller"}), controller_client=_FakeClient())
    pfade = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/-/ui/clients/restart-all" in pfade
    assert "/-/ui/clients/{node_id}/{verb}" in pfade


# ── #206: APPROVE/BLOCK fragen den eigenen Daemon statt den Scheduler ────────
#
# **Befund m.rau, 2026-08-14:** *„Im Nodes Screen funktioniert BLOCK immer noch
# nicht."* Das „immer noch" zeigt auf `#174` — dort ist der Fehler **sichtbar
# gemacht** worden, nicht behoben, und genau deshalb ist er jetzt nachweisbar:
# die Oberflaeche zeigt `failed: HTTP Error 404: Not Found`.
#
# `clients_node_action()` ruft `client.node_action(...)`, und `client` zeigt auf
# den **eigenen** Daemon. Die Route `/-/worker/{id}/approve|block` haengt an der
# `scheduler`-Rolle — auf einem reinen Client gibt es sie dort nicht. Die
# Loesung steht zwoelf Zeilen tiefer in derselben Datei (`_host_client()`) und
# wird nicht benutzt.


def test_approve_geht_an_den_scheduler_und_nicht_an_den_eigenen_daemon(
        team_repo: Path, monkeypatch):
    """**Der Rot-Schritt zu #206.**

    Der Knoten traegt hier nur `controller` — also den Fall aus dem Befund: den
    Mac, auf dem gearbeitet wird. Heute landet der Aufruf am eigenen Daemon, wo
    die Route nicht existiert, und der Knopf antwortet mit 404.
    """
    import bibi.controller as controller_mod

    eigener = _FakeClient()
    scheduler = _FakeClient()
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://scheduler.example:8780")
    monkeypatch.setattr(controller_mod, "ControllerClient",
                        lambda url, timeout=None: scheduler)

    app = create_app(roles.resolve({"controller"}), controller_client=eigener)
    with TestClient(app) as c:
        r = c.post("/-/ui/clients/node-1/approve")

    assert r.status_code == 200
    assert scheduler.node_actions == [("node-1", "approve")], (
        "die Aktion hat den Scheduler nicht erreicht")
    assert eigener.node_actions == [], (
        "der Knopf hat den eigenen Daemon gefragt — dort gibt es die Route nicht")


def test_ohne_eigenen_scheduler_bleibt_block_am_eigenen_client(team_repo: Path,
                                                               monkeypatch):
    """**Die Gegenprobe: ein Scheduler fragt sich nicht selbst ueber HTTP.**

    Ohne `BIBI_SCHEDULER_URL` ist dieser Knoten der Scheduler, und
    `_host_client()` gibt dann ausdruecklich den eigenen Client zurueck —
    *„ein HTTP-Aufruf ueber sich selbst waere ein Umweg."* Ohne diese Pruefung
    waere ein Fix gruen, der immer ueber das Netz geht.
    """
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)
    eigener = _FakeClient()

    app = create_app(roles.resolve({"scheduler", "controller"}),
                     controller_client=eigener)
    with TestClient(app) as c:
        r = c.post("/-/ui/clients/node-2/block")

    assert r.status_code == 200
    assert eigener.node_actions == [("node-2", "block")]
