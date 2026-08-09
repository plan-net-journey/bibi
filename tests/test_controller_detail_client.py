"""Die Job-Detailseite eines **Clients** (m.rau/bibi#86).

Auf einem reinen Client — dem einzigen Knotentyp, der in dieser Topologie
ueberhaupt eine Oberflaeche hat — gab es die Output-Box eines beim Scheduler
laufenden Laufs gar nicht. Nicht „sie waechst nicht": sie wurde nicht
gerendert.

``_detail_data()`` beschaffte den laufenden Lauf ueber ``client.jobs()``, und
``client`` zeigt auf den **eigenen** Daemon. ``/-/job`` und ``/-/schedule``
gibt es aber nur unter ``roles.scheduler``; auf einem Client antworten sie mit
dem eingefrorenen ``501``-Stub bzw. ``404``. Der defensive Fang lieferte
``(None, [], None)``, und ohne ``job`` rendert ``_live_panel()`` keine Box.

**Damit lag der Durchreicher aus `#78` auf einem Pfad, den in dieser Topologie
kein Knoten erreicht** — Arbeit aus zwei Releases, die in der Produktion nichts
bewirkte.

Der Jobs-Screen desselben Clients zeigte Scheduler-Zeilen sehr wohl: er geht
ueber ``_host_schedules()``, das die Scheduler-Adresse benutzt. Die Detailseite
tat das nicht. Der Fix gibt ihr denselben Weg.

Der Nachweis am Browser steht in ``tests/browser/test_output_box.py`` — hier
liegt die schnelle Ebene, damit er nicht nur im Vierstundentakt laeuft.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app


class _Stub:
    """Ein Daemon auf einem eigenen Port — entweder Scheduler oder Client.

    Bewusst kein echter Daemon: geprueft wird, **wen** die Detailseite fragt,
    nicht was ein Scheduler antwortet. Ein Stub, der nur bei den richtigen
    Pfaden etwas liefert, beantwortet genau diese Frage.

    ``rolle="client"`` antwortet wie ein Knoten **ohne** Scheduler-Rolle: der
    eingefrorene ``501``-Stub auf ``/-/job``, ``404`` auf ``/-/schedule``. Das
    ist die Produktionslage und der Grund, warum die Seite dort leer blieb.
    """

    def __init__(self, *, rolle: str = "scheduler", slug: str = "lang",
                 status: str = "running") -> None:
        self.rolle = rolle
        self.slug = slug
        self.status = status
        self.pfade: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: N802, ANN002
                pass

            def _sende(self, code: int, koerper: object) -> None:
                daten = json.dumps(koerper).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(daten)))
                self.end_headers()
                self.wfile.write(daten)

            def do_GET(self):  # noqa: N802
                stub.pfade.append(self.path)
                if stub.rolle == "client":
                    # Genau das, was ein Knoten ohne Scheduler-Rolle liefert.
                    if self.path.startswith("/-/job"):
                        self._sende(501, {"error": "not implemented",
                                          "stufe": "3.0"})
                    elif self.path.startswith("/-/schedule"):
                        self._sende(404, {"error": "not found"})
                    else:
                        self._sende(200, [])
                    return
                if self.path.startswith("/-/schedule"):
                    self._sende(200, {"schedules": [
                        {"slug": stub.slug, "schedule": "adhoc", "app_port": None}]})
                elif "/output" in self.path:
                    self._sende(200, {"events": []})
                elif self.path.startswith("/-/job"):
                    self._sende(200, [{"id": "j1", "slug": stub.slug,
                                       "status": stub.status, "pinned_host": None}])
                elif self.path.startswith("/-/journal"):
                    self._sende(200, [])
                else:
                    self.send_response(404)
                    self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _client_knoten(monkeypatch, scheduler: _Stub, eigener: _Stub):
    """Ein reiner Client: ``controller`` ohne ``scheduler``.

    **``BIBI_DAEMON_PORT`` ist hier kein Detail, sondern die Bedingung dafuer,
    dass dieser Test ueberhaupt etwas misst.** ``config.daemon_port()`` faellt
    auf den Port aus ``BIBI_SCHEDULER_URL`` zurueck, wenn keiner gesetzt und
    keine Portdatei da ist — der ``client`` des Controllers zeigte damit
    versehentlich auf den *Scheduler*-Stub, und die Seite war gruen, ohne dass
    der Fix existierte. Erst ein eigener Port trennt „wen fragt sie" von „wer
    antwortet".
    """
    monkeypatch.setenv("BIBI_SCHEDULER_URL", scheduler.url)
    monkeypatch.setenv("BIBI_DAEMON_PORT", str(eigener.port))
    return create_app(roles.resolve({"controller"}))


@pytest.fixture
def scheduler():
    s = _Stub()
    yield s
    s.stop()


@pytest.fixture
def eigener_daemon():
    s = _Stub(rolle="client")
    yield s
    s.stop()


@pytest.fixture
def client_app(team_repo, monkeypatch, scheduler, eigener_daemon):
    """Ein reiner Client, der den Stub als seinen Scheduler kennt.

    ``controller`` ohne ``scheduler`` — die Rolle, die dieser Mac und
    ``sarasate-client`` seit dem Rollenwechsel tragen und die einzige, unter
    der die Produktion diese Seite ueberhaupt aufruft.
    """
    return _client_knoten(monkeypatch, scheduler, eigener_daemon)


def test_a_client_shows_the_running_run_of_the_scheduler(client_app, scheduler):
    """Der Rot-Schritt von #86: die Seite kannte den Lauf gar nicht.

    Ohne ``job`` gibt es keine Box, keinen Zustand und keinen Knopf — die
    Detailseite eines laufenden Jobs sah auf dem Client aus wie die eines nie
    gelaufenen."""
    with TestClient(client_app) as c:
        r = c.get("/-/ui/schedule/lang")
    assert r.status_code == 200
    # Am Job-Zustand festgemacht, nicht am Wort „running": das steht auch in
    # der Journal-Zeile und in jeder Zustandslegende. `data-job` traegt die ID
    # des laufenden Laufs und entsteht nur, wenn die Seite ihn kennt.
    assert 'data-job="j1"' in r.text, (
        "die Detailseite des Clients kennt den laufenden Scheduler-Lauf nicht — "
        "sie fragt den eigenen Daemon statt den Scheduler (#86)")
    assert any(p.startswith("/-/job") for p in scheduler.pfade), (
        f"der Scheduler wurde gar nicht nach Jobs gefragt: {scheduler.pfade}")


def test_the_box_of_that_run_gets_its_own_stream(client_app):
    """Und sie bekommt ihren zweiten Strom.

    Auf dem Client gibt es keine ``output.jsonl`` des Laufs — der globale Bus
    kann die Box nicht speisen. Ohne ``data-stream`` waere sie ein Schnappschuss,
    der nicht waechst, und der Durchreicher aus `#78` bliebe ungenutzt."""
    with TestClient(client_app) as c:
        r = c.get("/-/ui/schedule/lang")
    assert "data-stream=" in r.text, (
        "die Box eines laufenden Scheduler-Laufs hat keinen eigenen Strom (#78/#86)")
    assert "/output/stream" in r.text


def test_a_terminal_run_gets_no_stream(team_repo, monkeypatch, eigener_daemon):
    """Die Gegenprobe zum Strom: ein terminaler Lauf braucht keinen.

    Ohne sie waere der Test oben auch dann gruen, wenn die Box **jedem** Lauf
    ein ``data-stream`` gaebe — und der Durchreicher bekaeme Verbindungen fuer
    Laeufe, die nichts mehr senden."""
    s = _Stub(status="complete")
    try:
        app = _client_knoten(monkeypatch, s, eigener_daemon)
        with TestClient(app) as c:
            r = c.get("/-/ui/schedule/lang")
    finally:
        s.stop()
    assert "data-stream=" not in r.text


def test_a_scheduler_reads_its_own_bus_instead_of_proxying_to_itself(
        team_repo, monkeypatch, scheduler, eigener_daemon):
    """Der Nebenbefund aus #86: geprueft wird die **Rolle**, nicht die Adresse.

    ``_output_stream_url()`` begruendete sein ``None`` mit *„auf dem Host ist
    der globale Bus der richtige Weg"* — verglichen wurde aber nur, **ob** eine
    Scheduler-Adresse gesetzt ist. Ein Scheduler, dessen ``BIBI_SCHEDULER_URL``
    auf ihn selbst zeigt (der Normalfall auf sarasate), schickte seine Box
    deshalb ueber einen Durchreicher zu sich selbst.

    Dieselbe Verwechslung von Adresse und Rolle, die ``d2c03bc`` fuer das
    Abonnement bereits korrigiert hat — dort war die Antwort dieselbe."""
    monkeypatch.setenv("BIBI_SCHEDULER_URL", scheduler.url)
    monkeypatch.setenv("BIBI_DAEMON_PORT", str(eigener_daemon.port))
    app = create_app(roles.resolve({"scheduler", "controller"}))
    with TestClient(app) as c:
        r = c.get("/-/ui/schedule/lang")
    # Absicherung gegen falsch-gruen: ohne bekannten Lauf gaebe es die Box
    # ohnehin nicht, und die Aussage unten waere gegenstandslos.
    assert 'data-job="j1"' in r.text, (
        f"der Lauf ist gar nicht bekannt — dann prueft dieser Test nichts")
    assert "data-stream=" not in r.text, (
        "ein Knoten mit Scheduler-Rolle reicht seinen eigenen Output an sich "
        "selbst durch, statt den lokalen Bus zu lesen (#86)")


# ── Der Platz ohne Zeile (m.rau/bibi#87) ────────────────────────────────────


def _lege_md(root, slug: str) -> None:
    ordner = root / "vault" / "case" / "x"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{slug}.md").write_text(
        f"---\nslug: {slug}\nschedule: adhoc\njob: echo hi\n---\n", encoding="utf-8")


def test_start_reaches_a_locally_known_job_that_has_no_row(
        team_repo, monkeypatch, scheduler, eigener_daemon):
    """**Das Henne-Ei aus `#87`, an seiner zweiten Haelfte.**

    Die Kachel traegt den Slug als Kennung, weil es keine Job-ID gibt. Die
    Verb-Route schlug den Slug aber ueber ``SELECT ... FROM jobs WHERE id=?``
    nach und antwortete ``404 job not found`` — der Knopf waere also da und
    taete nichts.

    Geprueft wird, dass die Route den Slug annimmt, nicht dass der Lauf
    gelingt: was danach kommt, ist ``/-/run`` und damit derselbe Weg wie
    ``bibi-ctrl run``.
    """
    _lege_md(team_repo, "BrowserCI")
    app = _client_knoten(monkeypatch, scheduler, eigener_daemon)
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/verb/client/BrowserCI/start")
    assert r.status_code != 404, (
        "die Route findet den lokal bekannten Slug nicht — der START-Knopf der "
        "Kachel ohne Zeile greift ins Leere (#87)")


def test_an_unknown_slug_is_still_refused(
        team_repo, monkeypatch, scheduler, eigener_daemon):
    """Die Gegenprobe, und sie ist hier keine Formalie.

    Ohne sie waere aus der Route ein Weg geworden, **jede** Zeichenkette als
    Slug zu starten. Genommen wird nur, was als MD hier liegt."""
    app = _client_knoten(monkeypatch, scheduler, eigener_daemon)
    with TestClient(app) as c:
        r = c.post("/-/ui/jobs/verb/client/gibt-es-nicht/start")
    assert r.status_code == 404
