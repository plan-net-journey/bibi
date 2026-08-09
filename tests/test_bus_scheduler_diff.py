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

    def publish_state(self, target: str, value: dict | None = None) -> None:
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
    assert "feedstatus" in bus.published


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
        # `in`, nicht `==`: eine Aenderung an `workers` bedient seit
        # m.rau/bibi#106 zwei Ziele — den Header (Anzahl) und den Nodes-Screen
        # (Inhalt). Beide sind richtig, und der Test prueft hier den Header.
        assert "feedstatus" in bus.published, f"{feld} fehlt im Fingerabdruck"

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
    assert "feedstatus" in bus.published


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
    assert "feedstatus" in bus.published


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


# ── Die Slug-Ebene: der Client sieht Job-Zustände seines Schedulers (#143) ──
#
# **Befund m.rau, 2026-08-04/05:** „ich sehe immer noch kein kontinuierliches
# Update, wenn sich ein Job Status ändert. … Ich muss immer wieder Refresh
# drücken."
#
# Der Header-Fall oben war 2026-08-03 behoben, der Job-Fall daneben nie — er
# sah aus wie derselbe, gelöste Fehler. `_diff_scheduler()` publiziert
# ausschließlich `feedstatus` und trägt dabei **aggregierte** Zähler, nie einen
# einzelnen Slug. Ein Job, der beim Scheduler von `pending` auf `running`
# wechselt, erreicht damit weder die Zeile im Jobs-Screen noch die Slot-Kachel
# im Job-Detail.
#
# Die Trennung der beiden Fingerabdrücke ist Teil des Verhaltens, nicht der
# Umsetzung: der Header hängt an einem `git status`, und ihn bei jeder
# Slug-Änderung dreckig zu machen wäre genau der Firehose, den der bestehende
# Docstring ausschließt.


def _collector_jobs(bus, status_antworten, job_antworten):
    """Collector mit gefaktem Status- **und** Job-Abruf beim Scheduler."""
    c = Collector(bus, registry=None)
    s_folge = iter(status_antworten)
    j_folge = iter(job_antworten)
    c._fetch_scheduler_status = lambda: next(s_folge, status_antworten[-1])
    c._fetch_scheduler_jobs = lambda: next(j_folge, job_antworten[-1])
    c._primed = True
    return c


#: Ein Scheduler-Status, der sich nie ändert — so ist sicher, dass ein
#: `feedstatus` in diesen Tests nur aus der Slug-Ebene stammen könnte.
_RUHIG = {"workers": [], "job_stats": {"counts": {"complete": 1}}}


def test_a_job_status_change_at_the_scheduler_publishes_its_slug():
    """Der Fall aus dem Befund: ein Job wechselt beim Scheduler den Zustand.

    Ohne diese Meldung erfährt die Slot-Kachel im Job-Detail nichts — sie hört
    auf `live:<slug>`, und dieses Target entstand bisher nur aus der **lokalen**
    Job-DB, in der auf einem Client nie etwas passiert.
    """
    bus = _Bus()
    c = _collector_jobs(bus, [_RUHIG], [
        [{"slug": "gmail-transfer", "row_status": "pending", "fire": 100.0}],
        [{"slug": "gmail-transfer", "row_status": "running", "fire": 100.0}],
    ])
    _tick(c)                      # erster Abruf: Fingerabdruck merken
    bus.published.clear()
    _tick(c)                      # jetzt läuft er
    assert "live:gmail-transfer" in bus.published


def test_a_job_change_also_pokes_the_list():
    """Die Jobs-Liste hört auf das Sammel-Target `jobs`, nicht auf jeden Slug.

    Ohne sie bewegt sich die Kachel im Detail, die Zeile in der Liste aber
    nicht — und genau die sieht man zuerst.
    """
    bus = _Bus()
    c = _collector_jobs(bus, [_RUHIG], [
        [{"slug": "gmail-transfer", "row_status": "pending", "fire": 100.0}],
        [{"slug": "gmail-transfer", "row_status": "running", "fire": 100.0}],
    ])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert "jobs" in bus.published


def test_a_new_fire_time_counts_as_a_change():
    """`next_fire_at` verschiebt sich, ohne dass ein Status wechselt.

    Der aggregierte Fingerabdruck des Headers sieht das nicht (die Zähler
    bleiben gleich); für die Zeile ist es trotzdem eine Änderung.
    """
    bus = _Bus()
    c = _collector_jobs(bus, [_RUHIG], [
        [{"slug": "Witz", "row_status": "pending", "fire": 100.0}],
        [{"slug": "Witz", "row_status": "pending", "fire": 200.0}],
    ])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert "live:Witz" in bus.published


def test_an_unchanged_scheduler_stays_quiet():
    """Sonst wäre es ein verkleideter Poll — dieselbe Regel wie oben."""
    bus = _Bus()
    zeilen = [{"slug": "Witz", "row_status": "pending", "fire": 100.0}]
    c = _collector_jobs(bus, [_RUHIG], [zeilen, [dict(zeilen[0])]])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == []


def test_a_slug_change_does_not_dirty_the_header():
    """**Die Trennung ist der Punkt dieser Änderung.**

    Der linke Header-Block hängt an einem `git status`. Ihn bei jeder
    Slug-Änderung nachladen zu lassen wäre der Firehose, den der bestehende
    Docstring ausdrücklich ausschließt (*„Zu weit gefasst machte er den Header
    bei jeder Kleinigkeit dreckig"*). Zwei Fingerabdrücke, zwei Ziele.
    """
    bus = _Bus()
    c = _collector_jobs(bus, [_RUHIG], [
        [{"slug": "Witz", "row_status": "pending", "fire": 100.0}],
        [{"slug": "Witz", "row_status": "running", "fire": 100.0}],
    ])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert "feedstatus" not in bus.published


def test_an_unreachable_scheduler_is_not_a_crash():
    """Der Host darf ausfallen (§2.7) — und der Ausfall darf nicht als
    Änderung jedes Slugs erscheinen."""
    bus = _Bus()
    c = _collector_jobs(bus, [_RUHIG], [
        [{"slug": "Witz", "row_status": "pending", "fire": 100.0}],
        None,
    ])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert "live:Witz" not in bus.published


# ── #77: der Poll wird zum Rückfall ──────────────────────────────────────────
#
# Lebt das Abonnement, ist der Slug-Poll überflüssig — jeder Wechsel kommt
# ohnehin binnen einer Sekunde über den Strom. Fällt es aus, muss der Poll
# sofort wieder greifen: ein Abriss darf den Client nicht blind machen (§2.7).


class _Abo:
    def __init__(self, live: bool) -> None:
        self.live = live


def test_a_live_subscription_replaces_the_slug_poll():
    """Beide Kanäle gleichzeitig wären doppelte Arbeit für dieselbe Auskunft."""
    bus = _Bus()
    c = Collector(bus, registry=None, subscription=_Abo(live=True))
    c._primed = True
    gerufen = []
    c._fetch_scheduler_jobs = lambda: gerufen.append(1)
    c._sched_last_fetch = 0.0
    assert c._diff_scheduler_jobs() == 0
    assert gerufen == []


def test_without_a_live_subscription_the_poll_still_runs():
    """Der Rückfall — ohne ihn wäre ein stiller Abriss eine stehende Anzeige."""
    bus = _Bus()
    c = Collector(bus, registry=None, subscription=_Abo(live=False))
    c._primed = True
    c._fetch_scheduler_jobs = lambda: [{"slug": "a", "row_status": "running", "fire": 1}]
    c._sched_jobs_snapshot = {"a": ("pending", 1)}
    assert c._diff_scheduler_jobs() > 0
    assert "live:a" in bus.published


# ── Der Nodes-Screen eines Clients (m.rau/bibi#106) ─────────────────────────


def test_a_client_publishes_nodes_when_the_federation_changes():
    """**Der Rot-Schritt von `#106`.**

    Beobachtung m.rau: *„Nodes Heartbeat — warum aktualisiert der nicht auf
    `http://127.0.0.1:53911/-/nodes`?"*

    `_diff_nodes()` steigt bei `registry is None` sofort aus, und die Registry
    gibt es nur beim Scheduler (`worker_registry = WorkerRegistry() if
    roles.scheduler else None`). **Ein Client feuert deshalb nie ein
    `nodes`-Ereignis** — und der Screen steht genau dort still, wo man ihn
    ansieht: auf `sarasate:8780` entstünden die Ereignisse, aber dort gibt es
    seit dem 2026-08-04 kein FE mehr.

    **Derselbe Schluss war für den Header schon gezogen worden.**
    `_diff_scheduler()`s eigener Docstring sagt: *„`_diff_nodes()` und
    `_diff_flags()` sehen ihn nicht: sie beobachten die lokale Registry und
    die lokalen Flags, und auf einem reinen Client ändert sich dort nie
    etwas."* Für den Nodes-Screen wurde er nicht gezogen.

    Der Abruf ist ohnehin da — `_diff_scheduler()` holt `/-/status` samt
    `workers`. Bisher zählte es davon nur die **Anzahl**; ein Knoten, der
    `stale` wird oder seinen Git-Status ändert, blieb unsichtbar.
    """
    bus = _Bus()
    c = _collector(bus, [
        {"workers": [{"worker": "a", "node_id": "1", "last_beat": 10.0,
                      "stale": False, "git_status": "clean"}],
         "job_stats": {"counts": {}}},
        {"workers": [{"worker": "a", "node_id": "1", "last_beat": 20.0,
                      "stale": False, "git_status": "clean"}],
         "job_stats": {"counts": {}}},
    ])

    _tick(c)
    bus.published.clear()
    _tick(c)

    assert "nodes" in bus.published, (
        "ein Client meldet den Knotenwechsel nicht — sein Nodes-Screen "
        "aktualisiert nie (#106)")


def test_the_header_stays_quiet_when_only_a_heartbeat_ticks():
    """Die Gegenprobe, und sie ist der Grund für den zweiten Fingerabdruck.

    Ein Heartbeat alle 10–30 s je Knoten ist genau die gewünschte Frequenz für
    die „vor Xs"-Anzeigen im Nodes-Screen — für den **Header** wäre sie
    Lärm: der hängt an einem git-Aufruf, und `_diff_scheduler()`s Docstring
    warnt ausdrücklich davor, ihn „bei jeder Kleinigkeit dreckig" zu machen.

    Beide Ziele im selben Takt, aber mit eigenem Fingerabdruck — dieselbe
    Bauweise wie `_diff_scheduler_jobs()` (m.rau/bibi#143).
    """
    bus = _Bus()
    c = _collector(bus, [
        {"workers": [{"worker": "a", "node_id": "1", "last_beat": 10.0}],
         "job_stats": {"counts": {}}},
        {"workers": [{"worker": "a", "node_id": "1", "last_beat": 20.0}],
         "job_stats": {"counts": {}}},
    ])

    _tick(c)
    bus.published.clear()
    _tick(c)

    assert "feedstatus" not in bus.published, (
        "ein reiner Heartbeat-Tick macht den Header dreckig — er haengt an "
        "einem git-Aufruf")
