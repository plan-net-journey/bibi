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
