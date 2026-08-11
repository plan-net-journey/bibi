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
    # Die Lifecycle-Zeitreihe ist mit m.rau/bibi#121 ersatzlos entfallen — sie
    # belieferte das Landungs-Histogramm, und das ging mit #120. Der Vertrag
    # schrumpft damit, und das ist richtig: er soll beschreiben, was es gibt.
    assert not any("landings" in p for p in paths)


def test_openapi_is_versioned(client):
    spec = client.get("/-/openapi.json").json()
    assert spec["info"]["version"] == CONTRACT_VERSION == "3.3"


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
        # "starting" (m.rau/bibi#38): reserviert, aber der Wrapper ist noch
        # nicht gespawnt. Gehört in den Vertrag, weil ein Client den Zustand
        # unterscheiden können muss — „startet gerade" ist etwas anderes als
        # „wartet" und etwas anderes als „läuft".
        "pending", "starting", "running", "failed", "error", "deferred",
        "inactive", "awaiting", "complete", "zombie", "killed",
    }


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/-/scheduler/next", None),
        # KEIN /-/scheduler/status/{id} hier (mehr) — seit PLAN-30 Ebene 1 v2
        # rollenunabhängig immer real, s. test_status_route_works_without_any_role
        # unten und den Kommentar in openapi.py::add_contract_routes().
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
        # KEIN ("get", "/-/journal") hier (mehr) — seit m.rau/bibi#103
        # rollenunabhängig immer real, s. test_journal_list_is_not_a_stub
        # unten und tests/test_journal_route.py.
        ("delete", "/-/journal/1", None),
        # KEIN ("get", "/-/landings") hier (mehr) — die Route ist mit
        # m.rau/bibi#121 ersatzlos entfallen, samt ihrem Stub.
    ],
)
def test_all_stubs_return_501_json_no_html(client, method, path, body):
    r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert r.status_code == 501
    # Reine JSON-API — keine Route gibt HTML zurück (§1.1, §3.8).
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"] == "not implemented"


def test_status_route_works_without_any_role(client):
    """PLAN-30 Ebene 1 v2 (2026-07-15): anders als jede andere v3.0-Route ist
    POST /-/scheduler/status/{id} bewusst KEIN 501-Stub mehr, auch auf einem
    Daemon ganz ohne Rolle — ein gepinnter Lauf braucht sie überall, damit sein
    Wrapper-Subprozess den Merge-back-Trigger feuern kann (s. Kommentar in
    openapi.py::add_contract_routes()). 404 statt 501 beweist: die echte Route
    antwortet, nicht der Stub."""
    r = client.post("/-/scheduler/status/abc", json={"status": "running"})
    assert r.status_code == 404
    assert r.json()["error"] == "job not found"


def test_journal_list_is_not_a_stub(client):
    """m.rau/bibi#103: GET /-/journal antwortet auch ohne jede Rolle real.

    Das Journal ist keine disponierte Domäne — jeder Knoten führt sein eigenes
    und muss es ausliefern können, sonst hat das Job-Detail eines Clients keine
    LOCAL-Gruppe. Eine leere Liste statt 501 beweist: die echte Route
    antwortet, nicht der Stub."""
    r = client.get("/-/journal")
    assert r.status_code == 200
    assert r.json() == []


def test_journal_schema_carries_join_key_and_archive_time(client):
    """Beide Felder sind Vertragsbestandteil, nicht Beiwerk: ohne ``job_uid``
    kann ein Client seine Läufe nicht mit denen des Schedulers zusammenführen,
    und ohne ``archived_at`` ist unter A2 nicht unterscheidbar, wann ein Lauf
    lief und wann ihn jemand abgeräumt hat."""
    props = client.get("/-/openapi.json").json()["components"]["schemas"][
        "JournalEntryView"]["properties"]
    assert "job_uid" in props
    assert "archived_at" in props


def test_no_route_returns_html(client):
    # Der gesamte Vertrag ist HTML-frei (Korrektur an bibi3, §1.1).
    spec = client.get("/-/openapi.json").json()
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            responses = op.get("responses", {})
            for code, resp in responses.items():
                content = resp.get("content", {})
                assert "text/html" not in content, f"{method} {path} → HTML"


def test_no_static_route_is_shadowed_by_an_earlier_placeholder():
    """Starlette matcht Routen in **Registrierungsreihenfolge**, nicht nach
    Spezifität. Ein fester Pfad, der nach einem gleich langen Platzhalter-Pfad
    registriert wird, ist damit unerreichbar — und zwar still: der Platzhalter
    antwortet, nur mit dem falschen Handler.

    Live gefunden am 2026-08-05 an `/-/jobs/list`, dem Nachlade-Ziel des Bus:
    `/-/jobs/{job_uid}` stand davor und lieferte `404 job not found,
    job_uid=list`. Der Jobs-Screen aktualisierte sich deshalb nie von selbst,
    obwohl der Bus korrekt meldete (m.rau/bibi#143 war nicht die Ursache — es
    hat den toten Weg erst sichtbar gemacht).

    Deshalb eine **App-weite** Invariante statt nur eines Tests auf die eine
    Route: die Falle liegt in der Reihenfolge zweier Dekoratoren, die
    hunderte Zeilen auseinanderstehen können, und sie kostet beim nächsten Mal
    wieder Tage. Geprüft wird über alle Rollen zusammen, weil die
    Registrierung rollenabhängig ist.
    """
    app = create_app(roles.resolve({"controller", "scheduler", "synchronizer"}))
    routen = [(r.path, set(r.methods or ()))
              for r in app.routes if hasattr(r, "path") and hasattr(r, "methods")]

    def segmente(p: str) -> list[str]:
        return p.strip("/").split("/")

    for i, (pfad, methoden) in enumerate(routen):
        if "{" in pfad:
            continue
        for frueher, m_frueher in routen[:i]:
            if "{" not in frueher:
                continue
            s_neu, s_alt = segmente(pfad), segmente(frueher)
            if len(s_neu) != len(s_alt) or not (methoden & m_frueher):
                continue
            verdeckt = all(alt.startswith("{") or neu == alt
                           for neu, alt in zip(s_neu, s_alt))
            assert not verdeckt, (
                f"{pfad} ist unerreichbar — {frueher} ist vorher registriert "
                f"und schluckt ihn. Feste Pfade vor Platzhalter-Pfade legen.")


def test_no_path_is_registered_twice():
    """Die Schwester der Invariante darüber, und sie fehlte (#38).

    Jene fängt den festen Pfad, den ein **Platzhalter** schluckt. Zwei Routen
    auf **demselben** Pfad fängt sie nicht — und das ist derselbe Fehler mit
    demselben Ausgang: Starlette nimmt die erste, die zweite ist still tot.

    Live geworden beim Bau des Journal-Screens: er sollte auf `/-/journal`
    liegen, und genau dort führt der Scheduler seit jeher die Journal-API, die
    der Controller-Client selbst aufruft. Auf einem Knoten mit beiden Rollen —
    sarasate ist einer — bekäme je nach Reihenfolge entweder der Browser JSON
    oder der Client HTML. Aufgefallen ist es nur, weil das *Fragment*
    `/-/journal/list` an `/-/journal/{jid}` hängenblieb; die Kollision der
    Screen-Route selbst hätte niemand gemeldet.

    **Die 501-Stubs des gefrorenen Vertrags sind ausgenommen, und zwar
    ausdrücklich statt stillschweigend:** ``add_contract_routes()`` läuft
    zuletzt und wird von jeder echten Implementierung verdeckt — das ist die
    Bauweise (siehe den Kommentar dort zu ``POST /-/scheduler/status/{id}``),
    kein Versehen. Erkannt werden sie am Modul ihres Handlers und nicht an
    einer Liste von Pfaden: eine Liste liefe mit dem nächsten implementierten
    Endpunkt auseinander.
    """
    app = create_app(roles.resolve({"controller", "scheduler", "synchronizer"}))
    gesehen: set[tuple[str, str]] = set()
    doppelt: list[str] = []
    for r in app.routes:
        if not (hasattr(r, "path") and hasattr(r, "methods")):
            continue
        stub = getattr(getattr(r, "endpoint", None), "__module__", "") \
            == "bibi.daemon.openapi"
        for m in (r.methods or ()):
            if (r.path, m) in gesehen and not stub:
                doppelt.append(f"{m} {r.path}")
            gesehen.add((r.path, m))
    assert not doppelt, (
        "doppelt vergebene Routen — die spätere ist unerreichbar: "
        + ", ".join(sorted(doppelt)))
