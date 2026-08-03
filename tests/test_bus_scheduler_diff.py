"""Der Client beobachtet seinen Scheduler (bibi5, FE-Spezifikation §8).

**Befund m.rau, 2026-08-03:** „die Werte aktualisieren sich trotzdem nur nach
reload. Das hätte ich anders erwartet!"

Zu Recht. Der Header zeigt Werte des Schedulers — verbundene Clients, nächster
Termin, gestoppte und fertige Jobs —, aber der Bus, der ihn nachladen lässt,
läuft auf **diesem** Knoten und beobachtet **dessen** Job-DB. Auf einem reinen
Client ändert sich dort nie etwas, also feuerte er nie. Der SSE-Strom war
messbar stumm.

„Der Client ist die App, der Scheduler ist das Backend" (§8) verlangt, dass die
App ihr Backend beobachtet. Genau das tut der Collector hier — im selben Muster
wie `_diff_nodes()` (Knoten-Registry) und `_diff_flags()` (auto_sync,
maintenance): Fingerabdruck bilden, vergleichen, bei Änderung `feedstatus`
veröffentlichen. Kein Poll im Frontend, kein Nachladen ohne Anlass.
"""

from __future__ import annotations

import pytest

from bibi.daemon.bus import Collector


class _Bus:
    def __init__(self) -> None:
        self.published: list[str] = []

    def publish_state(self, target: str) -> None:
        self.published.append(target)


def _collector(bus, antworten):
    """Collector mit gefaktem Scheduler-Abruf; `antworten` wird abgearbeitet."""
    c = Collector(bus, registry=None)
    folge = iter(antworten)
    c._fetch_scheduler_status = lambda: next(folge, antworten[-1])
    c._primed = True
    return c


def _tick(c) -> int:
    """Ein Diff-Durchlauf ohne Wartezeit.

    Die Drossel (`_SCHED_POLL_S`) gehoert zum Verhalten und wird unten eigens
    geprueft — hier steht sie nur im Weg, weil die Tests zwei Zustaende
    unmittelbar nacheinander durchspielen.
    """
    c._sched_last_fetch = 0.0
    return c._diff_scheduler()


def test_a_change_at_the_scheduler_publishes_feedstatus():
    """Die Werte im Header stammen vom Scheduler — ändern sie sich dort, muss
    dieser Knoten nachladen."""
    bus = _Bus()
    c = _collector(bus, [
        {"workers": [{"worker": "a"}], "job_stats": {"counts": {"complete": 1}}},
        {"workers": [{"worker": "a"}, {"worker": "b"}], "job_stats": {"counts": {"complete": 1}}},
    ])
    _tick(c)          # erster Abruf: Fingerabdruck merken
    bus.published.clear()
    _tick(c)          # zweiter Client dazugekommen
    assert bus.published == ["feedstatus"]


def test_no_change_publishes_nothing():
    """Ein unveränderter Scheduler darf keinen Refetch auslösen — sonst wäre es
    ein verkleideter Poll, und der Header hängt an einem git-Aufruf."""
    bus = _Bus()
    zustand = {"workers": [], "job_stats": {"counts": {"complete": 3}, "next_due_at": 1.0}}
    c = _collector(bus, [zustand, dict(zustand)])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == []


def test_the_fingerprint_covers_what_the_header_shows():
    """Jeder Wert des rechten Blocks gehört hinein — und nichts sonst. Ein zu
    weiter Fingerabdruck macht den Header bei jeder Kleinigkeit dreckig, ein zu
    enger lässt ihn veralten."""
    bus = _Bus()
    basis = {"workers": [], "job_stats": {"counts": {"complete": 1}, "next_due_at": 10.0},
             "maintenance": False, "started_at": 5.0}
    for feld, wert in (("workers", [{"worker": "x"}]), ("maintenance", True),
                       ("started_at", 9.0)):
        geaendert = dict(basis)
        geaendert[feld] = wert
        c = _collector(bus, [basis, geaendert])
        _tick(c)
        bus.published.clear()
        _tick(c)
        assert bus.published == ["feedstatus"], f"{feld} fehlt im Fingerabdruck"

    geaendert = dict(basis)
    geaendert["job_stats"] = {"counts": {"complete": 2}, "next_due_at": 10.0}
    c = _collector(bus, [basis, geaendert])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == ["feedstatus"], "job_stats fehlt im Fingerabdruck"


def test_an_unreachable_scheduler_is_a_change_too():
    """Fällt der Host weg, muss der Header das zeigen (gedimmt, mit Alter) —
    also ist auch der Ausfall ein Ereignis, kein Grund zu schweigen."""
    bus = _Bus()
    c = _collector(bus, [{"workers": [], "job_stats": {}}, None])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == ["feedstatus"]


def test_a_node_without_scheduler_url_does_not_poll():
    """Ein Knoten, der selbst der Scheduler ist, ruft sich nicht über HTTP
    selbst auf — und einer ohne konfigurierten Scheduler hat nichts zu holen."""
    bus = _Bus()
    c = Collector(bus, registry=None)
    c._primed = True
    c._fetch_scheduler_status = lambda: None
    assert _tick(c) == 0
    assert bus.published == []


def test_the_scheduler_is_not_polled_on_every_tick():
    """Der Collector tickt sekuendlich; ein HTTP-Aufruf je Tick waere Netzlast
    fuer eine Anzeige, die sich selten aendert. Fuenf Sekunden sind die
    Obergrenze dafuer, wie lange der Header nach einem Ereignis veraltet sein
    darf — der Heartbeat selbst kommt nur alle 15 s."""
    bus = _Bus()
    rufe = []
    c = Collector(bus, registry=None)
    c._primed = True
    c._fetch_scheduler_status = lambda: rufe.append(1) or {"workers": []}
    c._diff_scheduler()
    c._diff_scheduler()
    c._diff_scheduler()
    assert len(rufe) == 1, "gedrosselt: ein Abruf, nicht drei"
    assert c._SCHED_POLL_S >= 1.0


def test_the_collector_asks_the_remote_scheduler_not_itself(monkeypatch, tmp_path):
    """**Live-Falle, 2026-08-03.** Der Collector fragte sich selbst.

    `config.scheduler_base_url()` bevorzugt absichtlich `BIBI_DAEMON_PORT` —
    „sprich mit MEINEM eigenen Daemon", und diese Variable setzt `bibi-ctrl
    daemon` beim Start für sich selbst. In einem Daemon-Prozess liefert die
    Funktion deshalb die **eigene** Adresse. Ihr Docstring benennt das als
    „reinen Lokalitäts-Override, kein Federations-Ziel"; hier ist genau das
    Federations-Ziel gemeint.

    Der Fehler war stumm: der Abruf gelang, der Fingerabdruck war stabil, es
    wurde nie etwas veröffentlicht. Am Bildschirm sah das aus wie „der Bus
    funktioniert nicht" — live gemessen an `workers=0, counts=(), started_at =
    eigener Prozessstart`.
    """
    monkeypatch.setenv("BIBI_DAEMON_PORT", "65200")
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://sarasate.example:8780")
    gefragt: list[str] = []

    class _Client:
        def __init__(self, url, **kw):
            gefragt.append(url)

        def status(self):
            return {"workers": [], "job_stats": {}}

    import bibi.controller.client as client_mod
    monkeypatch.setattr(client_mod, "ControllerClient", _Client)
    Collector(_Bus(), registry=None)._fetch_scheduler_status()
    assert gefragt == ["http://sarasate.example:8780"], \
        "der Collector muss den entfernten Scheduler fragen, nicht sich selbst"


# ── Der eigene Heartbeat (Befund m.rau, 2026-08-03) ─────────────────────────
#
# „der heartbeat bleibt weiter stehen." — und das lag daran, dass der
# vorherige Diff nur den *Scheduler* beobachtet. Der Heartbeat ist die einzige
# Zeile des linken Blocks, die sich regelmäßig ändert, und sie entsteht **hier**:
# alle 15 s meldet sich dieser Knoten beim Host. Niemand hat das bisher als
# Ereignis behandelt.
#
# Er gehört zum selben Muster wie `registry` — der Collector bekommt eine
# Referenz und bildet einen Fingerabdruck, statt irgendwo zu pollen.


class _HB:
    def __init__(self, last_at=None, last_ok=True):
        self.last_at, self.last_ok = last_at, last_ok


def test_a_new_heartbeat_publishes_feedstatus():
    bus = _Bus()
    hb = _HB(last_at=100.0)
    c = Collector(bus, registry=None, heartbeat=hb)
    c._primed = True
    c._diff_heartbeat()
    bus.published.clear()
    hb.last_at = 115.0          # der nächste Schlag, 15 s später
    assert c._diff_heartbeat() == 1
    assert bus.published == ["feedstatus"]


def test_an_unchanged_heartbeat_stays_quiet():
    bus = _Bus()
    hb = _HB(last_at=100.0)
    c = Collector(bus, registry=None, heartbeat=hb)
    c._primed = True
    c._diff_heartbeat()
    bus.published.clear()
    assert c._diff_heartbeat() == 0
    assert bus.published == []


def test_a_failing_heartbeat_is_an_event():
    """Von „ok" auf „nicht ok" ist die wichtigste Änderung dieser Zeile — sie
    bedeutet, dass der Knoten den Host verloren hat."""
    bus = _Bus()
    hb = _HB(last_at=100.0, last_ok=True)
    c = Collector(bus, registry=None, heartbeat=hb)
    c._primed = True
    c._diff_heartbeat()
    bus.published.clear()
    hb.last_ok = False
    assert c._diff_heartbeat() == 1


def test_without_a_heartbeat_nothing_happens():
    """Ein Knoten ohne `connect`-Rolle hat keinen — das ist kein Fehler."""
    bus = _Bus()
    c = Collector(bus, registry=None)
    c._primed = True
    assert c._diff_heartbeat() == 0
    assert bus.published == []
