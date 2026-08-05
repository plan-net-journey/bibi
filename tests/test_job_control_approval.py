"""Job-Control-Approval-Gate (``/-/job*``-Aktionen, ``/-/run``, ``/-/test``) —
Live-Fund 2026-07-25, Job-Control-Approval-Bug: ein ``pending``- oder sogar
``blocked``-Knoten konnte trotzdem uneingeschränkt Jobs auflisten/starten. Deckt
``_require_approved_or_local()`` ab: Loopback/lokale Aufrufe bleiben frei,
entfernte Aufrufe brauchen ``X-Bibi-Node-Id`` + ``approved``-Status. Die bewusst
UNGEGATETEN reinen Lese-/Output-Routen (render.py-EventSource-Abhängigkeit,
s. Docstring von ``_require_approved_or_local()``) werden hier ebenfalls positiv
abgesichert, damit dieser Scope nicht versehentlich später mit-verengt wird."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app

_REMOTE = ("192.0.2.10", 51234)  # RFC 5737 TEST-NET-1 — nie ein echter Peer


def _seed(repo_root: Path, rel: str, text: str) -> None:
    p = repo_root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def gated(team_repo: Path):
    """Scheduler+Worker-App mit einer pending fälligen Job-Zeile. ``local`` ist
    der gewohnte Loopback-Client (Setup/Vergleich, wie in den übrigen
    Daemon-Tests), ``remote`` derselbe App-Prozess, aber mit einer echten
    Fremd-IP als ASGI-``client``-Scope statt eines Header-Tricks — ein echter
    Netzwerk-Peer kann ``127.0.0.1``/``testclient`` nie liefern."""
    app = create_app(roles.resolve({"scheduler", "worker"}))
    with TestClient(app) as local:
        _seed(team_repo, "a/README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
        local.post("/-/rescan")
        jid = local.get("/-/job").json()[0]["id"]
        remote = TestClient(app, client=_REMOTE)
        yield local, remote, jid


# ── Gated: Kontroll-/Listing-Routen (genau das, was mustertest live konnte) ──


def test_remote_without_node_id_is_blocked(gated):
    _, remote, jid = gated
    assert remote.get("/-/job").status_code == 403
    assert remote.get(f"/-/job/{jid}").status_code == 403
    assert remote.post(f"/-/job/{jid}/start").status_code == 403


def test_remote_pending_node_is_blocked(gated):
    _, remote, _ = gated
    r = remote.get("/-/job", headers={"X-Bibi-Node-Id": "brand-new-remote"})
    assert r.status_code == 403
    assert "not approved" in r.json()["detail"]


def test_remote_blocked_node_is_blocked(gated):
    local, remote, jid = gated
    local.post("/-/worker/blocked-remote/block")
    h = {"X-Bibi-Node-Id": "blocked-remote"}
    assert remote.post(f"/-/job/{jid}/start", headers=h).status_code == 403


def test_remote_approved_node_can_control_jobs(gated):
    local, remote, jid = gated
    local.post("/-/worker/good-remote/approve")
    h = {"X-Bibi-Node-Id": "good-remote"}
    assert remote.get("/-/job", headers=h).status_code == 200
    assert remote.post(f"/-/job/{jid}/start", headers=h).status_code == 200


def test_remote_dispatch_routes_blocked_without_approval(gated):
    # `/-/test` ist mit PLAN-38 (2026-07-27) ersatzlos entfallen — `/-/run`
    # läuft selbst in-place, die Route war danach ein exaktes Duplikat. Das
    # Approval-Gate gilt unverändert für den verbliebenen Dispatch-Weg.
    _, remote, _ = gated
    assert remote.post("/-/run", json={"cmd": "echo hi"}).status_code == 403


# ── Loopback bleibt frei (kein Regressionsrisiko für den Host selbst) ───────


def test_local_loopback_unaffected(gated):
    local, _, jid = gated
    assert local.get("/-/job").status_code == 200
    assert local.post(f"/-/job/{jid}/start").status_code == 200


# ── Befund 4 (Live-Test PLAN-37, 2026-07-27): co-located fremder Knoten ─────
# Auf sarasate teilen sich Host (8780), Client (8781) und der Testknoten (8782)
# eine Maschine — Loopback heißt dort gerade NICHT "derselbe Knoten". Live
# reproduziert: `mmu` (Status pending) bekam über 127.0.0.1 die volle Job-Liste,
# über die Tailscale-Adresse korrekt 403.


def test_loopback_with_foreign_pending_node_id_is_blocked(gated):
    local, _, jid = gated
    h = {"X-Bibi-Node-Id": "co-located-mustertest"}  # nie approved
    assert local.get("/-/job", headers=h).status_code == 403
    assert local.post(f"/-/job/{jid}/start", headers=h).status_code == 403
    assert local.post("/-/run", json={"cmd": "echo hi"}, headers=h).status_code == 403


def test_loopback_with_foreign_approved_node_id_passes(gated):
    local, _, _ = gated
    local.post("/-/worker/co-located-client/approve")
    r = local.get("/-/job", headers={"X-Bibi-Node-Id": "co-located-client"})
    assert r.status_code == 200  # der sarasate-Client (8781→8780) darf weiterhin


def test_loopback_with_own_node_id_is_free(gated):
    """Echtes Selbstgespräch: die CLI dieses Knotens schickt die eigene node_id
    (``job_cmd.py``) — die darf nie an der eigenen Freischaltung scheitern, sonst
    sperrt sich ein frisch initialisierter Host selbst aus."""
    from bibi import config
    local, _, jid = gated
    h = {"X-Bibi-Node-Id": config.node_id()}
    assert local.get("/-/job", headers=h).status_code == 200
    assert local.post(f"/-/job/{jid}/start", headers=h).status_code == 200


def test_loopback_without_header_stays_free(gated):
    """Bewusste Grenze des Fixes: der eigene Controller/das FE (DaemonClient
    schickt keine node_id) muss weiter durchkommen."""
    local, _, _ = gated
    assert local.get("/-/job").status_code == 200


# ── Bewusst NICHT gegatete Lese-Routen (render.py-EventSource-Abhängigkeit) ─


def test_remote_read_only_job_status_stays_open(gated):
    # Kein Exploit-Regressionsschutz, sondern eine bewusste Scope-Entscheidung
    # (s. _require_approved_or_local()-Docstring) — dieser Test hält sie fest,
    # damit sie nicht später versehentlich mit-verengt wird.
    _, remote, jid = gated
    assert remote.get(f"/-/job/{jid}/status").status_code == 200
    assert remote.get(f"/-/job/{jid}/log").status_code == 200


def test_remote_run_journal_read_stays_open(gated):
    _, remote, _ = gated
    assert remote.get("/-/run/journal").status_code == 200


# ── Befund 5 (Live-Test PLAN-37): die CLI muss die Abweisung auch SAGEN ─────
# Live beobachtet: `bibi-ctrl job list` auf dem pending-Knoten gab gar nichts
# aus und beendete sich mit 1 — für den Menschen nicht von "keine Jobs" zu
# unterscheiden, obwohl die Begründung beim Approval-Modell der ganze Punkt ist.


def test_cli_meldet_abweisung_statt_stumm_zu_scheitern(capsys):
    from bibi.ctrl.job_cmd import _fail

    rc = _fail(403, {"detail": "node not approved (status: pending)"}, "job list")

    assert rc == 1
    err = capsys.readouterr().err
    assert "abgewiesen" in err
    assert "pending" in err        # der Grund steht drin
    assert "freigeschaltet" in err  # und was zu tun ist


def test_cli_meldet_auch_unerwartete_codes(capsys):
    from bibi.ctrl.job_cmd import _fail

    assert _fail(500, None, "job kill") == 1
    assert "HTTP 500" in capsys.readouterr().err


def test_cli_schweigt_wenn_req_schon_gemeldet_hat(capsys):
    """code=0 heißt: _req() hat "daemon nicht erreichbar" bereits ausgegeben —
    keine zweite, verwirrende Zeile hinterherschieben."""
    from bibi.ctrl.job_cmd import _fail

    assert _fail(0, None, "job list") == 1
    assert capsys.readouterr().err == ""


# ── Der Controller muss sich ausweisen (Live-Fund 2026-08-04) ────────────────


def test_the_controller_client_sends_its_node_id(monkeypatch):
    """**Alle vier Verben der SCHEDULER-Kachel waren tot**, und niemand hat es
    gemerkt: `ControllerClient` schickte keinen `X-Bibi-Node-Id`, der Host
    verlangt ihn seit dem 2026-07-25 fuer jede Job-Control-Route, und ein
    Klick antwortete `HTTP Error 403: Forbidden`.

    Unsichtbar war es, weil sarasate bis zum 2026-08-04 selbst die
    `controller`-Rolle trug: dort lief die Abnahme ueber das **eigene** FE,
    also als Loopback-Aufruf ohne Header — und genau der ist ausdruecklich
    erlaubt. Mit dem Wegfall der Rolle wurde aus demselben Klick ein
    entfernter Aufruf, und die Luecke lag offen.

    Live gemessen (Mac gegen sarasate:8780): ohne Header `403 node approval
    required`, mit Header `404 job not found` — dieselbe Anfrage, nur die
    Identitaet fehlte.
    """
    from bibi import config
    from bibi.controller.client import ControllerClient
    gesehen: dict = {}

    class _Antwort:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):  # noqa: ARG001
        gesehen["headers"] = dict(req.header_items())
        return _Antwort()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    ControllerClient("http://host:8780").job_action("j1", "kill")
    # header_items() liefert die Namen in Titel-Schreibweise.
    schluessel = {k.lower(): v for k, v in gesehen["headers"].items()}
    assert schluessel.get("X-bibi-node-id".lower()) == config.node_id()


def test_a_remote_verb_without_the_header_is_refused(team_repo):  # noqa: ARG001
    """Die Gegenprobe, und der Grund, warum der Header noetig ist: der Host
    weist einen entfernten Aufruf ohne Identitaet ab. Ohne diesen Test waere
    der obige nur eine Behauptung ueber ein Detail."""
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    from bibi.daemon.worker import Worker
    app = create_app(roles.resolve({"scheduler", "worker"}),
                     worker=Worker(autopoll=False, worker_name="w1"))
    with TestClient(app, client=("100.64.0.9", 55000)) as c:
        assert c.post("/-/job/egal/kill").status_code == 403
