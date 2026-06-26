"""Gefrorener /-/-API-Vertrag: Schemata + 501-Stubs (PLAN-3 §1.1/§3.0)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.daemon.openapi import CONTRACT_VERSION


@pytest.fixture
def client(team_repo):
    # Idle-Daemon genügt: der Vertrag ist rollenunabhängig sichtbar (§1.1).
    app = create_app(roles.resolve(set()))
    with TestClient(app) as c:
        yield c


def test_openapi_covers_job_scheduler_worker_journal(client):
    paths = client.get("/-/openapi.json").json()["paths"].keys()
    # Vollständige Abdeckung der vier Vertrags-Bereiche (§3.8).
    assert "/-/job" in paths
    assert "/-/job/{id}" in paths
    assert "/-/scheduler/next" in paths
    assert "/-/scheduler/status/{id}" in paths
    assert "/-/worker" in paths
    assert "/-/journal" in paths


def test_openapi_is_versioned(client):
    spec = client.get("/-/openapi.json").json()
    assert spec["info"]["version"] == CONTRACT_VERSION == "3.0"


def test_schemas_present_in_components(client):
    schemas = client.get("/-/openapi.json").json()["components"]["schemas"].keys()
    for name in ("JobView", "JobReservation", "StatusReport", "JournalEntryView",
                 "WorkerView"):
        assert name in schemas


def test_status_enum_in_schema(client):
    # Der Status-Graph (§5.4) ist im Vertrag dokumentiert.
    schemas = client.get("/-/openapi.json").json()["components"]["schemas"]
    status_values = set(schemas["Status"]["enum"])
    assert status_values == {
        "pending", "running", "failed", "error", "deferred",
        "inactive", "awaiting", "complete", "zombie", "killed",
    }


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/-/scheduler/next", None),
        ("post", "/-/scheduler/status/abc", {"status": "running"}),
        ("get", "/-/job", None),
        ("get", "/-/job/abc", None),
        ("get", "/-/job/abc/status", None),
        ("get", "/-/job/abc/out", None),
        ("get", "/-/job/abc/err", None),
        ("get", "/-/job/abc/log", None),
        ("get", "/-/job/abc/stream", None),
        ("post", "/-/job/abc/kill", None),
        ("post", "/-/job/abc/start", None),
        ("post", "/-/job/abc/reset", None),
        ("get", "/-/worker", None),
        ("get", "/-/journal", None),
    ],
)
def test_all_stubs_return_501_json_no_html(client, method, path, body):
    r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert r.status_code == 501
    # Reine JSON-API — keine Route gibt HTML zurück (§1.1, §3.8).
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"] == "not implemented"


def test_no_route_returns_html(client):
    # Der gesamte Vertrag ist HTML-frei (Korrektur an bibi3, §1.1).
    spec = client.get("/-/openapi.json").json()
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            responses = op.get("responses", {})
            for code, resp in responses.items():
                content = resp.get("content", {})
                assert "text/html" not in content, f"{method} {path} → HTML"
