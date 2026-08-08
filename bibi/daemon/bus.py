"""FE-Event-Bus (PLAN-36 Stufe 36.1): ein globaler Ereignisstrom pro Daemon.

Zwei Komponenten, bewusst FastAPI-frei (rein testbar):

- :class:`Bus` — In-Process-Pub/Sub. Publisher dürfen aus beliebigen Threads
  kommen (der Collector tickt via ``run_in_executor``, Muster ``Sweeper``);
  Konsumenten sind asyncio-Generatoren (die ``/-/events``-Route). Zwei
  Event-Klassen (PLAN-36 E2): **Zustands-Events** sind Dirty-Meldungen pro
  Ziel-Element, idempotent, last-write-wins — pro Abonnent per Ziel
  koalesziert, nie gepuffert-historisiert. Seit #79 duerfen sie den Wert
  mitfuehren, den der Diff **ohnehin verglichen hat** (``status``/``fire`` je
  Slug); die Regel dazu lautet: *trage den Wert fuer das, was du ohnehin
  vergleichst — fuege keinen Vergleich hinzu, um einen Wert tragen zu koennen.*
  Der Wert ist eine Beigabe, kein Vertrag: ein Empfaenger, der ihn ignoriert
  und refetcht, verhaelt sich wie vorher. **Append-Events** (Output-Zeilen)
  tragen Offset + formatierte Zeile; bei Überlauf eines langsamen Abonnenten
  werden sie verworfen und durch eine Dirty-Meldung fürs betroffene Ziel
  ersetzt (Refetch mit frischem Seed heilt — PLAN-36 E6). Der Bus hält
  keinerlei Historie; „Einstieg zu jeder Zeit" trägt die server-gerenderte
  Seite selbst als Snapshot (PLAN-36 E5).

- :class:`Collector` — der **eine** Poller des Knotens (PLAN-36 E4): erkennt
  per In-Memory-Snapshot-Diff Änderungen an ``jobs`` (Status/fire — bewusst
  NICHT über ``updated_at``: ``reserve_next()``s Dispatch-UPDATE setzt die
  Spalte nicht, ein Zeitstempel-Diff würde genau den pending→running-Übergang
  verpassen) und ``journal`` (``MAX(id)``-Hochstand), und tailt die
  ``output.jsonl`` aller gerade aktiven Läufe (dieselbe
  ``read_events``+Offset-Mechanik, die ``_formatted_sse()`` heute pro
  SSE-Verbindung fährt — hier einmal zentral statt pro Tab). Statusübergänge
  passieren cross-process (der detachte Wrapper schreibt Direkt-SQLite und
  ``output.jsonl`` als eigener Prozess) — deshalb ist dieser Watcher die
  Korrektheitsquelle, In-Process-Publishes wären nur ein Latenz-Bonus.

Ziel-Schema (Targets, von Stufe 36.2 konsumiert):

- ``live:<slug>`` — die Live-Region eines Jobs (Kachel + Aktionsleiste).
- ``journal:<slug>`` — die Journal-Liste eines Jobs.
- ``out:<job_id>`` — die Live-Output-Box (Append-Events).
- ``feed`` — die Feed-Liste (#80), gespeist vom Fingerabdruck ueber die
  Quellen, aus denen ``feed.py`` liest.

Für gepinnte Läufe (``pinned_host`` gesetzt, Slug trägt ein
``-<8hex>``-Suffix, s. ``run_pinned()``) wird zusätzlich der Bucket-Slug
publiziert — die Client-Detailseite adressiert über ihn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import threading
import time
from collections import deque
from pathlib import Path

from bibi import repo
from bibi.daemon import job_db, output_format
from bibi.schedule import models
from bibi.wrapper import output

log = logging.getLogger("bibi.daemon")

#: Läufe, deren Output aktiv wächst — nur die werden getailt. ``deferred``
#: gehört bewusst nicht dazu (kein laufender Prozess, Output ruht bis zum
#: Retry — der Statuswechsel selbst kommt als Zustands-Event).
_TAILED = ("running", "awaiting")

#: Slug-Suffix gepinnter Läufe (``f"{bucket}-{secrets.token_hex(4)}"``,
#: worker.py::run_pinned()) — dieselbe 8-Hex-Konvention wie das
#: ``LIKE '{slug}-________'``-Muster in ``job_db.list_journal()``.
_PIN_SUFFIX = re.compile(r"-[0-9a-f]{8}$")

#: Append-Puffer pro Abonnent — bewusst klein: bei Überlauf greift die
#: Dirty-Heilung (E6), kein wachsender Speicher pro langsamem Tab.
_APPEND_LIMIT = 512


def bucket_slug(slug: str, pinned_host) -> str | None:
    """Bucket-Slug eines gepinnten Laufs (``fe-live-probe-742ab201`` →
    ``fe-live-probe``), ``None`` wenn nicht gepinnt/kein Suffix."""
    if not pinned_host:
        return None
    stripped = _PIN_SUFFIX.sub("", slug)
    return stripped if stripped != slug else None


class _Subscriber:
    __slots__ = ("state", "appends", "wakeup", "loop")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        #: Ziel → Wert (oder ``None``); Insertion-Reihenfolge. Seit #79 darf
        #: hier der verglichene Wert stehen statt nur „dreckig".
        self.state: dict[str, dict | None] = {}
        self.appends: deque = deque()
        self.wakeup = asyncio.Event()
        self.loop = loop


class Bus:
    """Threadsicherer, historienfreier Pub/Sub — s. Modul-Docstring."""

    def __init__(self, *, append_limit: int = _APPEND_LIMIT) -> None:
        self._subs: set[_Subscriber] = set()
        self._lock = threading.Lock()
        self._append_limit = append_limit
        self._closing = False

    @property
    def closing(self) -> bool:
        """Fährt der Knoten herunter? Dann enden die Ströme von selbst."""
        return self._closing

    def begin_shutdown(self) -> None:
        """Allen Abonnenten sagen, dass Schluss ist (m.rau/bibi#176).

        Der ``/-/events``-Strom ist im Normalfall endlos. Beim Herunterfahren
        wartete uvicorn deshalb seine ``timeout_graceful_shutdown`` ab, brach
        die Task dann ab — und protokollierte den Abbruch als *„Exception in
        ASGI application"* samt rund fünfzig Zeilen Stacktrace. Kaputt war
        nichts; nur konnte das niemand der Ausgabe ansehen, und ein geplanter
        Vorgang, der wie ein Absturz aussieht, lehrt seinen Leser nebenbei,
        Tracebacks zu überfliegen.

        **Die Frist bleibt unverändert** — sie ist die Absicherung für alles,
        was sich nicht von selbst schließt. Sie greift nur nicht mehr für den
        einen Strom, von dem wir wissen, dass er nie von selbst endet.

        Idempotent und aus jedem Thread aufrufbar: der Aufruf kommt aus dem
        Signal-Handler, nicht aus dem Event-Loop. ``_wake()`` ist dafür
        gebaut (``call_soon_threadsafe``, s. dort).
        """
        with self._lock:
            self._closing = True
            subs = list(self._subs)
        for s in subs:
            self._wake(s)

    def subscribe(self) -> _Subscriber:
        sub = _Subscriber(asyncio.get_running_loop())
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def _wake(self, sub: _Subscriber) -> None:
        # call_soon_threadsafe: publish kommt regulär aus dem Executor-Thread
        # des Collectors (run_in_executor, Muster Sweeper) — Event.set() direkt
        # wäre dort nicht loop-sicher. Ein bereits geschlossener Loop (Shutdown-
        # Race) darf den Publisher nie killen.
        try:
            sub.loop.call_soon_threadsafe(sub.wakeup.set)
        except RuntimeError:
            pass

    def publish_state(self, target: str, value: dict | None = None) -> None:
        """Ein Ziel als dreckig melden — optional mit dem verglichenen Wert (#79).

        **Der Wert ist eine Beigabe, kein Vertrag.** Ein Empfaenger, der ihn
        ignoriert und stattdessen refetcht, verhaelt sich genau wie vorher; das
        ist die Bedingung, unter der die Aenderung rein additiv bleibt. Wo kein
        Wert vorliegt, entsteht auch kein Feld.

        **Koaleszenz und Historienfreiheit bleiben unangetastet.** Zwei
        Meldungen desselben Ziels sind weiterhin eine Nachricht — jetzt mit dem
        juengsten Wert. Last-write-wins gilt damit fuer den Wert genauso wie
        fuer das Ziel, und das ist die einzige Lesart, die zur Zusage des
        Busses passt: er haelt keine Historie, also auch keine Wertfolge.
        """
        with self._lock:
            subs = list(self._subs)
            for s in subs:
                s.state[target] = value
        for s in subs:
            self._wake(s)

    def publish_append(self, target: str, off: int, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
            for s in subs:
                if len(s.appends) >= self._append_limit:
                    # Überlauf: Lücke unvermeidbar → Dirty statt Append, der
                    # Refetch des Ziel-Fragments trägt den vollen frischen Seed.
                    s.state[target] = None
                else:
                    s.appends.append(
                        {"t": "append", "target": target, "off": off, "e": event})
        for s in subs:
            self._wake(s)

    async def wait(self, sub: _Subscriber, timeout: float) -> list[dict]:
        """Nächste Event-Charge für ``sub`` — ``[]`` bei Timeout (Ping-Fenster).

        Reihenfolge: Zustands-Events zuerst (idempotent, ein Refetch macht
        nachfolgende Appends desselben Ziels per Offset-Dedup harmlos), dann
        Appends in Publikationsreihenfolge."""
        try:
            await asyncio.wait_for(sub.wakeup.wait(), timeout)
        except asyncio.TimeoutError:
            return []
        with self._lock:
            sub.wakeup.clear()
            out: list[dict] = []
            for t, v in sub.state.items():
                ev: dict = {"t": "state", "target": t}
                if v is not None:
                    ev["v"] = v
                out.append(ev)
            sub.state.clear()
            out.extend(sub.appends)
            sub.appends.clear()
        return out


#: Wie lange ein Abonnement stumm sein darf, bevor es als tot gilt (#77).
#: Der Scheduler sendet alle ``EVENTS_PING_S`` (15 s) ein ``{"t":"ping"}`` —
#: dreimal nichts ist der Beleg, dass die Verbindung gestorben ist, ohne dass
#: TCP es gemerkt hätte. **Derselbe Wert wie im FE-Watchdog**, aus demselben
#: Grund: eine still gestorbene Verbindung sieht von innen aus wie ein sehr
#: ruhiger Scheduler. Ohne diesen Schutz tauschte das Release eine träge
#: Anzeige gegen eine stehende — ein Poll faellt sichtbar aus, ein Abonnement
#: kann still sterben.
_SUB_WATCHDOG_S = 45.0

#: Ziele, die ein Abonnement **nicht** uebernimmt, weil sie den eigenen Knoten
#: meinen und nicht den, von dem sie kommen (#77 trifft #80).
#:
#: ``feed`` ist der einzige: die Feed-Liste zeigt das Repo, in dem sie laeuft —
#: Commits, offene Aenderungen, Cases im lokalen Arbeitsbaum. Uebernaehme der
#: Client die Meldung des Schedulers, laedt er seine eigene Liste neu, weil
#: anderswo etwas passiert ist, und zahlt dafuer einen ``git log`` plus einen
#: ``git status``. Denselben Commit sieht er ohnehin selbst, sobald der
#: Synchronizer ihn gebracht hat.
#:
#: **Ein Eintrag ist keine Ausnahmeliste, sondern eine Aussage.** Alles andere —
#: ``live:``, ``journal:``, ``jobs``, ``nodes``, ``archived`` — ist
#: Scheduler-Zustand und gehoert uebernommen. Waechst diese Menge, ist das ein
#: Hinweis darauf, dass ein Ziel zwei Dinge zugleich meint.
_NUR_LOKALE_ZIELE = frozenset({"feed"})

#: Pause vor dem naechsten Verbindungsversuch. Kurz, weil ein Client ohne Strom
#: auf den Poll zurueckfaellt und dort ohnehin nur alle 5 s nachsieht.
_SUB_RETRY_S = 3.0

#: **Eine Uhr, nicht zwei.** Der Socket-Timeout *ist* der Watchdog: laeuft er
#: ab, hat der Strom laenger geschwiegen, als der Scheduler je schweigen darf
#: (er sendet alle ``EVENTS_PING_S`` = 15 s ein Lebenszeichen), und ein
#: Neuaufbau ist die richtige Antwort.
#:
#: Hier standen bis ``v0.7.7`` eine Sekunde und ein eigener Frist-Zaehler
#: daneben — mit der Begruendung, ``stop()`` solle nicht auf einen
#: blockierenden Read warten muessen. **Das war ein Fehler mit Ansage:** nach
#: einem ``socket.timeout`` ist der ``http.client``-Stream unbrauchbar, der
#: Lese-Loop fiel also im Sekundentakt heraus und verband neu. Weil jeder
#: Verbindungsaufbau beim Scheduler einen Resync ausloest, meldete das jedes
#: Mal jede aktive Live-Region als dreckig — der Posten kehrte damit seine
#: eigene Absicht um. Live gemessen: 100 Verbindungsaufbauten in zwei Minuten.
#:
#: ``stop()`` braucht den kurzen Timeout nicht: es schliesst die Antwort und
#: joint mit eigener Frist, und der Thread ist ``daemon`` — er haelt keinen
#: Prozess auf.


class SchedulerEvents:
    """Abonniert ``/-/events`` des Schedulers und spiegelt ihn auf den eigenen Bus (#77).

    **Der Strom lag bereit, es fehlte der Abonnent.** Der Scheduler-Collector
    tickt sekuendlich; der Client fragte ihn alle fuenf Sekunden ab. Der
    Scheduler war damit fuenfmal genauer, als der Client von ihm wissen wollte,
    und die Differenz sah man jedem Statuswechsel an (Befund m.rau, 2026-08-08:
    *„Statuswechsel kommen eindeutig zu traege."*).

    **Es aendert sich die Quelle, nicht die Struktur.** Der Mischer stand schon:
    ``Collector._diff_scheduler_jobs()`` veroeffentlicht seine Funde laengst auf
    dem eigenen Bus dieses Knotens. Hier kommen dieselben Ziele an, nur frueher
    und ohne Abfrage.

    Was **nicht** uebernommen wird, sind die Append-Ereignisse. Sie tragen
    Offsets in eine ``output.jsonl``, die es auf diesem Knoten nicht gibt; der
    Weg dorthin ist der Durchreicher aus #78 — eine eigene Verbindung je
    Output-Box, kein Sammelstrom.

    **Die Identitaet ist der Schutz.** ``/-/events`` ist bewusst ungegatet, weil
    eine ``EventSource`` keine Header setzen kann. Hier ist der Verbraucher aber
    ein Daemon, und der kann sich ausweisen: die ``node_id`` geht als
    ``X-Bibi-Node-Id`` mit, der Scheduler prueft sie gegen ``approved_nodes``.
    Wer keine Arbeit und kein Config-Bundle bekommt, soll auch keinen
    Ereignisstrom bekommen.

    Ein eigener Thread statt einer asyncio-Task: das Lesen haengt an ``urllib``
    (dieselbe Wahl wie ``ControllerClient``/``scheduler_client``, keine neue
    Abhaengigkeit), und ein blockierender Read gehoert nicht in den Event-Loop.
    """

    def __init__(self, bus: Bus, *, url: str | None = None, node_id: str | None = None,
                 watchdog: float = _SUB_WATCHDOG_S, retry: float = _SUB_RETRY_S,
                 autorun: bool = True) -> None:
        self.bus = bus
        self.url = url
        self.node_id = node_id
        self.watchdog = watchdog
        self.retry = retry
        self.autorun = autorun
        self._live = False
        self._running = False
        self._thread: threading.Thread | None = None
        self._resp = None

    @property
    def live(self) -> bool:
        """Steht die Verbindung? Der Collector entscheidet daran, ob er pollt.

        Bewusst kein „war zuletzt erfolgreich": ein Abonnement, das gerade neu
        verbindet, ist **nicht** lebendig, und in genau diesem Fenster muss der
        Poll greifen."""
        return self._live

    # ── Verbindung ──────────────────────────────────────────────────────────

    def _open(self):
        """Den Strom oeffnen — die Naht, die Tests ersetzen koennen.

        Der Socket-Timeout ist **kurz** (``_READ_TICK_S``) und ist nicht der
        Watchdog. Ihn auf die Watchdog-Frist zu setzen waere die naheliegende
        Vereinfachung und ein Fehler: ein blockierender Read laesst sich von
        aussen nicht aufbrechen, ``stop()`` haette also bis zu 45 Sekunden auf
        eine Verbindung gewartet, die niemand mehr braucht — beim
        Daemon-Shutdown genau die Sorte Haenger, die #77 verhindern soll.

        Stattdessen kehrt der Read regelmaessig ergebnislos zurueck, und
        ``_session()`` zaehlt die Stille selbst. Ein leerer Rueckkehrer ist
        keine Nachricht, nur eine Gelegenheit nachzusehen, ob es weitergehen
        soll."""
        import urllib.request
        req = urllib.request.Request(
            f"{self.url.rstrip('/')}/-/events",
            headers={"Accept": "text/event-stream"},
        )
        if self.node_id:
            req.add_header("X-Bibi-Node-Id", self.node_id)
        return urllib.request.urlopen(req, timeout=self.watchdog)  # noqa: S310

    def _handle(self, roh: str) -> None:
        """Eine ``data:``-Zeile auf den eigenen Bus uebersetzen."""
        try:
            ev = json.loads(roh)
        except ValueError:
            return
        if ev.get("t") != "state":
            # hello/ping sind Lebenszeichen, append gehoert nicht hierher (s.o.),
            # bye meldet den Shutdown des Schedulers — den merkt die Schleife
            # draussen ohnehin am Ende des Stroms.
            return
        ziel = ev.get("target")
        if ziel and ziel not in _NUR_LOKALE_ZIELE:
            # Der Wert reist mit (#79). Ohne diese Zeile ginge er genau auf der
            # Strecke verloren, fuer die er gedacht ist: der Scheduler weiss den
            # neuen Status, der Client zeigt ihn an. Ein Ereignis ohne Wert
            # bleibt eins — `v` fehlt dann schlicht.
            self.bus.publish_state(ziel, ev.get("v"))

    def _session(self) -> None:
        """Eine Verbindung von ihrem Aufbau bis zu ihrem Ende.

        Die Schleife liest zeilenweise und misst dabei die Stille. Drei Enden
        sind moeglich, und alle drei fuehren an dieselbe Stelle zurueck: der
        Strom endet regulaer (leere Zeile am EOF, der Scheduler faehrt herunter),
        die Frist laeuft ab (still gestorbene Verbindung), oder ``stop()`` hat
        das Laufen abgestellt. Danach ist ``live`` falsch, und der Poll im
        Collector traegt weiter."""
        resp = self._open()
        self._resp = resp
        try:
            self._live = True
            while self._running:
                try:
                    zeile = resp.readline()
                except TimeoutError:
                    # Der Watchdog: laenger stumm, als der Scheduler je sein
                    # darf. Ein *stiller* Strom ist dagegen der Normalfall und
                    # erreicht diese Zeile nie — dafuer sorgen die Pings.
                    log.debug("Scheduler-Abonnement stumm seit %.0fs — Neuaufbau",
                              self.watchdog)
                    return
                except (OSError, ValueError):
                    return  # Abriss oder von stop() geschlossen
                if not zeile:
                    return  # EOF: der Scheduler hat den Strom beendet
                text = zeile.decode("utf-8", "replace").rstrip("\n")
                if text.startswith("data: "):
                    self._handle(text[len("data: "):])
        finally:
            self._live = False
            self._resp = None
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

    def _loop(self) -> None:
        while self._running:
            try:
                self._session()
            except Exception as exc:  # noqa: BLE001 — der Host darf ausfallen (§2.7)
                self._live = False
                log.debug("Scheduler-Abonnement nicht verfuegbar: %s", exc)
            if self._running:
                time.sleep(self.retry)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.autorun or not self.url:
            # Kein Scheduler konfiguriert heisst: dieser Knoten **ist** einer.
            # Es gibt niemanden zu abonnieren, und das ist kein Fehlerfall.
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="scheduler-events")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._live = False
        # Den laufenden Read aufbrechen — und zwar **hart**, ueber den Socket.
        #
        # ``resp.close()`` allein genuegt nicht und ist die Falle: bei einer
        # offenen Antwort will ``http.client`` den Rest des Bodys lesen, bevor
        # es schliesst. Bei einem endlosen Strom kommt dieser Rest nie, also
        # blockiert der Aufruf bis zum Socket-Timeout — ``stop()`` haengt dann
        # 45 Sekunden an einer Verbindung, die niemand mehr braucht. Gemessen
        # beim Bau von ``v0.7.7``: ein Test, der eine Sekunde prueft, lief
        # achtundfuenfzig.
        #
        # ``shutdown()`` bricht den blockierenden ``recv`` im Lese-Thread
        # sofort auf; ``close()`` danach raeumt die Huelle. Beides
        # best-effort — ein bereits toter Socket ist genau das, was wir wollten.
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                roh = getattr(getattr(resp, "fp", None), "raw", None)
                sock = getattr(roh, "_sock", None)
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001
                pass
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class Collector:
    """Der eine Poller des Knotens — s. Modul-Docstring und PLAN-36 E4.

    Lifecycle-Muster wie ``Sweeper``: ``start()``/``stop()`` im App-Lifespan,
    erst schlafen, dann ticken (derselbe Test-Schutz wie dort), Tick selbst
    blockierend via ``run_in_executor``. ``tick_once()`` ist synchron und
    direkt testbar."""

    def __init__(self, bus: Bus, *, db_path: Path | None = None,
                 repo_root: Path | None = None, interval: float = 1.0,
                 autorun: bool = True, registry=None, heartbeat=None,
                 subscription=None) -> None:
        self.bus = bus
        #: Das Scheduler-Abonnement (#77), sofern dieser Knoten eins hat. Der
        #: Collector liest davon nur `live` — wie er von der Registry die
        #: Knotenliste liest: eine Referenz, kein Aufruf.
        self.subscription = subscription
        self.db_path = db_path
        self.repo_root = repo_root
        self.interval = interval
        self.autorun = autorun
        # PLAN-36 Stufe 36.3: WorkerRegistry (nur Scheduler-Knoten) für das
        # "nodes"-Sammel-Target — der Diff über registry.list() erkennt auch
        # den stale-Übergang OHNE neuen Heartbeat (stale wird beim list()-
        # Aufruf zeitbasiert berechnet, nicht gespeichert).
        self.registry = registry
        #: Der eigene Heartbeat (nur mit `connect`-Rolle vorhanden). Der
        #: Collector liest davon `last_at`/`last_ok`, wie er von der
        #: Registry die Knotenliste liest — kein Poll, eine Referenz.
        self.heartbeat = heartbeat
        self._jobs: dict[str, tuple] = {}    # job_id → (status, fire)
        self._journal_max: int | None = None
        self._tails: dict[str, dict] = {}    # job_id → {run_id, path, kind, sent}
        self._nodes_snapshot: tuple | None = None
        self._flags_snapshot: tuple | None = None
        self._sched_snapshot: tuple | None = None
        #: Getrennt vom groben `_sched_snapshot` (m.rau/bibi#143): Slug-Ebene
        #: des Schedulers, `slug → (status, fire)`. Ein eigener Speicher, weil
        #: er ein anderes Ziel bedient — `live:<slug>` statt `feedstatus`.
        self._sched_jobs_snapshot: dict[str, tuple] | None = None
        self._hb_snapshot: tuple | None = None
        #: Fuenfter "feedstatus"-Fingerabdruck (#72): der git-Arbeitsbaum.
        #: Eigener Speicher *und* eigene Drossel, weil er eine andere Quelle
        #: hat als der Scheduler-Poll — denselben Takt, aber keinen Netzaufruf.
        self._git_snapshot: tuple | None = None
        #: Sechster Fingerabdruck (#80), und der erste mit eigenem Ziel statt
        #: `feedstatus`: die Quellen des Feeds. Eigene Drossel, weil er einen
        #: zweiten git-Aufruf braucht (`dirty_files()`), den `_diff_git()`
        #: nicht schon macht.
        self._feed_snapshot: tuple | None = None
        self._sched_last_fetch: float = 0.0
        self._git_last_check: float = 0.0
        self._feed_last_check: float = 0.0
        self._primed = False
        self._task: asyncio.Task | None = None
        self._running = False

    # ── Tick ────────────────────────────────────────────────────────────────

    def tick_once(self) -> dict:
        root = self.repo_root or repo.root()
        conn = job_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, slug, status, fire, payload, pinned_host "
                "FROM jobs WHERE active=1").fetchall()
            jrow = conn.execute("SELECT MAX(id) AS m FROM journal").fetchone()
            jmax = jrow["m"] or 0
            new_journal = []
            if self._primed and self._journal_max is not None and jmax > self._journal_max:
                new_journal = conn.execute(
                    "SELECT slug, pinned_host FROM journal WHERE id > ?",
                    (self._journal_max,)).fetchall()
        finally:
            conn.close()

        stats = {"state": 0, "append": 0}
        any_job_change = False
        seen: dict[str, tuple] = {}
        for r in rows:
            jid, slug = r["id"], r["slug"]
            seen[jid] = (r["status"], r["fire"], slug, r["pinned_host"])
            changed = (self._primed
                       and self._jobs.get(jid, (None,))[:2] != seen[jid][:2])
            if changed:
                any_job_change = True
                self._publish_live(slug, r["pinned_host"],
                                   {"status": r["status"], "fire": r["fire"]})
                # Journal bei JEDEM Statuswechsel mit-dirty (nicht nur beim
                # Journal-INSERT unten): die Journal-Liste zeigt für laufende
                # Jobs eine Live-Platzhalterzeile (journal_fragment(),
                # live_job-Parameter) — ohne dieses Event erschiene sie auf
                # einer bereits offenen Seite erst beim nächsten Reload.
                self._publish_journal(slug, r["pinned_host"])
                stats["state"] += 1
            self._track_tail(root, r, freshly_started=changed)
        # Verschwundene Zeilen (deactivate/Löschung): Tail final leeren und die
        # Live-Region dreckig melden — der Refetch zeigt dann den neuen Zustand.
        if self._primed:
            for jid in set(self._jobs) - set(seen):
                _, _, slug, pinned = self._jobs[jid]
                self._drop_tail(jid, final_read=True)
                self._publish_live(slug, pinned)
                any_job_change = True
                stats["state"] += 1
        self._jobs = seen

        for r in new_journal:
            self._publish_journal(r["slug"], r["pinned_host"])
            stats["state"] += 1
        self._journal_max = jmax

        # Sammel-Targets (PLAN-36 Stufe 36.3) — die Listen-/Übersichts-Screens
        # hören auf EIN Target statt auf jeden Slug einzeln: "jobs" (Jobs- und
        # Archiv-Listen), "archived" (nur bei neuen Journal-Zeilen),
        # "feedstatus" (Status-Kacheln, zusätzlich vom Flags-Diff unten
        # getriggert).
        if any_job_change or new_journal:
            self.bus.publish_state("jobs")
            self.bus.publish_state("feedstatus")
            stats["state"] += 1
        if new_journal:
            # „run archived" (m.rau/bibi#108) — die einzige Verbindung zwischen
            # Strom und Liste: der Strom trägt die Liste nicht, er stößt sie an.
            # Feuert genau bei einem Journal-INSERT und deshalb, seit der
            # Archivierungsregel A2, zum richtigen Zeitpunkt: ein blockierter
            # Lauf erzeugt das Ereignis erst, wenn ihn jemand abräumt.
            #
            # Hieß bis bibi5 `chart` und meinte das Landungs-Histogramm; das
            # Chart ist mit m.rau/bibi#120 entfallen, das Ereignis nicht. Ein
            # Target, das nach seinem Zuhörer benannt ist statt nach dem, was
            # geschehen ist, verliert seinen Sinn, sobald der Zuhörer geht.
            self.bus.publish_state("archived")
            stats["state"] += 1

        stats["state"] += self._diff_nodes()
        stats["state"] += self._diff_flags()
        stats["state"] += self._diff_scheduler()
        stats["state"] += self._diff_heartbeat()
        stats["state"] += self._diff_git()
        stats["state"] += self._diff_feed()

        # Tails: Output-Zuwachs publizieren; Läufe, die nicht mehr aktiv
        # wachsen (Terminal/deferred), nach einem letzten Read entlassen.
        for jid in list(self._tails):
            stats["append"] += self._pump_tail(jid)
            if seen.get(jid, (None,))[0] not in _TAILED:
                del self._tails[jid]

        self._primed = True
        return stats

    def _diff_nodes(self) -> int:
        """"nodes"-Sammel-Target: Fingerprint über die WorkerRegistry —
        jeder Heartbeat, jeder stale-Übergang, jede Git-Status-Änderung eines
        Knotens macht den Nodes-Screen einmal dreckig (statt des früheren
        10s-Polls pro Tab). Zeitstempel-Felder (last_beat …) bleiben bewusst
        Teil des Fingerprints: ein Heartbeat alle ~10-30s je Knoten ist genau
        die gewünschte Update-Frequenz der "vor Xs"-Anzeigen dort."""
        if self.registry is None:
            return 0
        try:
            snap = tuple(sorted(
                (w.get("worker"), w.get("node_id"), w.get("last_beat"),
                 w.get("stale"), w.get("git_status"), w.get("git_user"))
                for w in self.registry.list()))
        except Exception:
            return 0
        changed = self._primed and snap != self._nodes_snapshot
        self._nodes_snapshot = snap
        if changed:
            self.bus.publish_state("nodes")
            return 1
        return 0

    def _diff_flags(self) -> int:
        """"feedstatus"-Zusatztrigger: auto_sync/sync_conflict/maintenance
        (billige ``state``-Reads) — Kachel-Quellen, die der jobs-Diff nicht
        sieht. Bewusst NICHT beobachtet (dokumentierte 36.3-Grenze): der
        Synchronizer-Innenzustand (letzter Pull/Push) — dessen Relativzeiten
        frieren zwischen echten Ereignissen ein; bei Bedarf eigener Nachtrag."""
        try:
            from bibi import state
            snap = (state.get_auto_sync(), state.get_sync_conflict(),
                    state.get_maintenance())
        except Exception:
            return 0
        changed = self._primed and snap != self._flags_snapshot
        self._flags_snapshot = snap
        if changed:
            self.bus.publish_state("feedstatus")
            return 1
        return 0

    #: Wie oft der Scheduler befragt wird. Der Collector tickt sekuendlich;
    #: ein HTTP-Aufruf je Tick waere Netzlast fuer eine Anzeige, die sich
    #: selten aendert. Fuenf Sekunden sind die Obergrenze dafuer, wie lange
    #: der Header nach einem Ereignis veraltet sein darf — der Heartbeat
    #: selbst kommt nur alle 15 s.
    _SCHED_POLL_S = 5.0

    def _diff_heartbeat(self) -> int:
        """"feedstatus"-Trigger fuer den **eigenen** Heartbeat.

        Die einzige Zeile des linken Header-Blocks, die sich regelmaessig
        aendert — alle 15 s meldet sich dieser Knoten beim Host. Sie entsteht
        hier im Prozess, wird also nicht abgefragt, sondern abgelesen: eine
        Referenz auf das Heartbeat-Objekt, derselbe Weg wie bei ``registry``.

        Ohne diesen Diff blieb die Zeile stehen, bis zufaellig etwas anderes
        den Header dreckig machte (Befund m.rau, 2026-08-03: "der heartbeat
        bleibt weiter stehen"). ``_diff_scheduler()`` sieht sie nicht — sie
        gehoert diesem Knoten, nicht dem Host.

        ``last_ok`` gehoert in den Fingerabdruck: der Wechsel von "verbunden"
        auf "nicht verbunden" ist die wichtigste Aenderung dieser Zeile.
        """
        hb = self.heartbeat
        if hb is None:
            return 0
        snap = (getattr(hb, "last_at", None), getattr(hb, "last_ok", None))
        changed = self._primed and snap != self._hb_snapshot
        self._hb_snapshot = snap
        if changed:
            self.bus.publish_state("feedstatus")
            return 1
        return 0

    def _read_git(self) -> dict | None:
        """Arbeitsbaum-Zustand dieses Repos, oder ``None`` (kein Git-Repo).

        Eigene Naht, wie sie ``_fetch_scheduler_status()`` fuer den Host ist:
        dahinter steckt ein Subprozess (``git status --porcelain=v2``), und
        Tests sollen ihn ersetzen koennen, ohne ein Repo anzulegen.

        Gelesen wird ueber ``git_status.working_tree_status()`` — dieselbe
        Funktion, aus der auch die Git-Kachel gespeist wird. Zwei Wege zur
        selben Auskunft koennten auseinanderlaufen, und der Fingerabdruck
        haette dann recht, waehrend die Kachel etwas anderes zeigt.
        """
        try:
            from bibi import repo
            from bibi.git_status import working_tree_status
            st = working_tree_status(self.repo_root or repo.root())
        except Exception:  # noqa: BLE001 — ein fehlendes Repo ist kein Fehler
            return None
        if st is None:
            return None
        return {"tree": st.tree, "sync": st.sync, "branch": st.branch,
                "oid": st.oid, "ahead": st.ahead, "behind": st.behind}

    def _diff_git(self) -> int:
        """Fuenfter "feedstatus"-Fingerabdruck: der git-Arbeitsbaum (#72).

        **Befund m.rau, 2026-08-07:** zwei Screenshots im Abstand von 15
        Sekunden, dazwischen ein manueller Reload — der Unterschied war
        ``clean`` → ``modified``.

        Der Arbeitsbaum stand in keinem der vier bisherigen Fingerabdruecke.
        Das war keine Nachlaessigkeit, sondern die Kosten-Entscheidung, die
        vier Methoden weiter unten im Docstring von ``_diff_scheduler()``
        steht: *„Zu weit gefasst machte er den Header bei jeder Kleinigkeit
        dreckig — und der haengt an einem git-Aufruf."* Die Git-Karte hing
        dafuer an einem 30-Sekunden-Poll; der ist mit PLAN-36 Stufe 36.3
        entfallen, und **ersatzlos**. Seitdem hatte die Zeile gar keinen
        Ausloeser mehr.

        **Entscheidung m.rau, 2026-08-07:** *„nimm die erste: ueber
        ``git_status`` im selben Takt wie ``_diff_scheduler()`` (alle paar
        Sekunden, nicht bei jedem Tick)"*. Die Alternative — ein Mindesttakt
        fuer ``feedstatus``, wenn seit N Sekunden nichts kam — ist verworfen:
        sie haette den Header periodisch dreckig gemacht, ohne dass sich etwas
        geaendert hat, und damit einen Poll wiederhergestellt statt ihn zu
        ersetzen.

        **Und er ist zugleich die Antwort auf #71.** Die vier bisherigen
        Auslöser setzen *alle* eine Verbindung voraus: Job-Zustaende wechseln
        nicht ohne Scheduler, ``_diff_flags()`` sieht nur lokale Flags,
        ``_diff_heartbeat()`` hat ohne ``connect``-Rolle kein Objekt, und
        ``_diff_scheduler()`` bekommt ``None``. Auf einem Knoten ohne
        erreichbaren Scheduler feuerte damit keiner — der Header stand still,
        bis jemand neu lud. Dieser hier feuert ohne Gegenueber, weil er keins
        braucht.
        """
        jetzt = time.time()
        # Derselbe Takt wie der Scheduler-Poll, ausdruecklich so entschieden.
        # Der Collector tickt sekuendlich; ein `git status` je Tick waere genau
        # der Preis, wegen dem die Zeile ueberhaupt draussen blieb.
        if jetzt - self._git_last_check < self._SCHED_POLL_S:
            return 0
        self._git_last_check = jetzt
        g = self._read_git()
        snap: tuple | None = None if g is None else (
            g.get("tree"), g.get("sync"), g.get("branch"),
            g.get("oid"), g.get("ahead"), g.get("behind"),
        )
        changed = self._primed and snap != self._git_snapshot
        self._git_snapshot = snap
        if changed:
            self.bus.publish_state("feedstatus")
            return 1
        return 0

    def _read_feed(self) -> dict | None:
        """Fingerabdruck ueber die Quellen, aus denen ``feed.py`` liest — oder
        ``None`` (kein Git-Repo).

        Zwei Werte, und sie decken alle vier Quellen ab:

        * ``oid`` — der HEAD. Er traegt die **Commits**, und mit ihnen auch die
          **Agent-Slugs** (aus ``git log``) und jeden **Case**, der bereits
          committet ist.
        * ``dirty`` — die geaenderten Pfade samt Zustand, dieselbe Quelle, aus
          der ``feed.uncommitted_units()`` seine offenen Einheiten bildet. Sie
          traegt zugleich jeden **neuen Case**: dessen ``README.md`` ist
          untracked, bevor sie committet ist.

        **Deshalb steht ``discover_cases()`` hier bewusst nicht**, obwohl der
        Feed es aufruft. Es ist ein Verzeichnis-Walk und damit der teuerste
        Teil — und er brachte nichts, was die beiden Werte oben nicht schon
        sagen. Ein Fingerabdruck, der mehr liest als noetig, verliert genau die
        Kosten-Entscheidung, um die es hier geht.

        Eigene Naht wie ``_read_git()``: dahinter stecken Subprozesse, und
        Tests sollen sie ersetzen koennen, ohne ein Repo anzulegen.
        """
        try:
            from bibi import repo
            from bibi.git_status import dirty_files, working_tree_status
            root = self.repo_root or repo.root()
            st = working_tree_status(root)
            if st is None:
                return None
            return {"oid": st.oid,
                    "dirty": tuple(sorted(dirty_files(root).items()))}
        except Exception:  # noqa: BLE001 — ein fehlendes Repo ist kein Fehler
            return None

    def _diff_feed(self) -> int:
        """Der Ausloeser des Feeds (#80).

        **``#feedboard`` trug weder ``data-bus`` noch ``data-bus-refetch``** —
        als einzige Live-Region des FE. Er aktualisierte nur beim Seitenaufbau
        und beim Klick auf ``LOAD MORE``; blieb ein Tab offen, waehrend eine
        Vault-Datei gespeichert oder ein Commit erzeugt wurde, stand er still.

        Aufgefallen ist es lange nicht, und der Grund ist lehrreich: **man
        betritt den Feed und laedt dabei die Seite.** Er ist frisch, wenn man
        hinkommt, nicht waehrend man hinsieht.

        Derselbe Takt wie ``_diff_git()``/``_diff_scheduler()``, aus demselben
        Grund — der Fingerabdruck haengt an git. Und dieselbe Regel: gemeldet
        wird nur bei echter Aenderung, der Takt begrenzt allein, **wie oft
        nachgesehen** wird. Ein Mindesttakt, der die Region periodisch dreckig
        macht, waere ein Poll durch die Hintertuer und ist schon fuer die
        Git-Zeile verworfen worden.

        Eigenes Ziel, nicht ``feedstatus``: der Header haengt an einem
        git-Aufruf, und ihn bei jeder Feed-Aenderung mitzunehmen waere genau
        der Firehose, den der Modul-Docstring ausschliesst. Zwei
        Fingerabdruecke, zwei Ziele — dieselbe Trennung wie zwischen
        ``_diff_scheduler()`` und ``_diff_scheduler_jobs()``.
        """
        jetzt = time.time()
        if jetzt - self._feed_last_check < self._SCHED_POLL_S:
            return 0
        self._feed_last_check = jetzt
        f = self._read_feed()
        snap: tuple | None = None if f is None else (f.get("oid"), f.get("dirty"))
        changed = self._primed and snap != self._feed_snapshot
        self._feed_snapshot = snap
        if changed:
            self.bus.publish_state("feed")
            return 1
        return 0

    def _fetch_scheduler_status(self) -> dict | None:
        """Status des konfigurierten Schedulers, oder ``None``.

        ``None`` heisst zweierlei und wird gleich behandelt: kein Scheduler
        konfiguriert (dieser Knoten ist selbst einer) oder nicht erreichbar.
        Im ersten Fall gibt es nichts zu beobachten, im zweiten ist der
        Ausfall die Nachricht — beides fuehrt zu einem stabilen Fingerabdruck
        bzw. zu genau einer Veroeffentlichung beim Wechsel.
        """
        try:
            # NICHT config.scheduler_base_url(): die bevorzugt bewusst
            # BIBI_DAEMON_PORT ("sprich mit MEINEM Daemon", von `bibi-ctrl
            # daemon` selbst gesetzt) und liefert in einem Daemon-Prozess
            # deshalb die eigene Adresse. Ihr Docstring nennt das einen
            # "reinen Lokalitaets-Override, kein Federations-Ziel" -- hier
            # ist aber genau das Federations-Ziel gemeint. Ohne diese
            # Unterscheidung fragt der Client sich selbst und sieht nie eine
            # Aenderung (live beobachtet: workers=0, counts=(), started_at =
            # eigener Prozessstart).
            import os
            from bibi import config
            url = (os.environ.get("BIBI_SCHEDULER_URL")
                   or config.read_env().get("BIBI_SCHEDULER_URL"))
            if not url:
                return None
            from bibi.controller.client import ControllerClient
            return ControllerClient(url, timeout=3.0).status()
        except Exception:  # noqa: BLE001 — der Host darf ausfallen (§2.7)
            return None

    def _diff_scheduler(self) -> int:
        """"feedstatus"-Trigger fuer die Werte, die vom **Scheduler** kommen.

        Der Header zeigt verbundene Clients, den naechsten Termin und die
        Job-Zaehler — alles Fremdzustand. ``_diff_nodes()`` und
        ``_diff_flags()`` sehen ihn nicht: sie beobachten die lokale Registry
        und die lokalen Flags, und auf einem reinen Client aendert sich dort
        nie etwas. Der SSE-Strom war deshalb stumm, und der Header
        aktualisierte nur beim Reload (Befund m.rau, 2026-08-03).

        Der Fingerabdruck deckt genau das ab, was im rechten Block steht. Zu
        weit gefasst machte er den Header bei jeder Kleinigkeit dreckig — und
        der haengt an einem git-Aufruf.
        """
        jetzt = time.time()
        if jetzt - self._sched_last_fetch < self._SCHED_POLL_S:
            return 0
        self._sched_last_fetch = jetzt
        s = self._fetch_scheduler_status()
        if s is None:
            snap: tuple | None = None
        else:
            js = s.get("job_stats") or {}
            snap = (
                len(s.get("workers") or []),
                tuple(sorted((js.get("counts") or {}).items())),
                js.get("next_due_at"),
                s.get("maintenance"),
                s.get("started_at"),
            )
        # "Kein Scheduler" bleibt still, weil ``None != None`` falsch ist —
        # nicht, weil eine Sperre es verhindert. Hier stand bis v0.7.5 ein
        # zusaetzliches ``war_leer = self._sched_snapshot is None and snap is
        # None`` samt ``and not war_leer`` in der Bedingung darunter. Es war
        # wirkungslos: ``war_leer`` ist genau dann wahr, wenn beide Seiten
        # ``None`` sind — und dann ist ``changed`` bereits falsch.
        #
        # Der Zuschnitt von #71 nannte diese Sperre als Grund dafuer, dass der
        # Header eines Knotens ohne Scheduler nie aktualisiert. Der Befund
        # stimmte, die Begruendung nicht: es gab hier nichts zu unterdruecken,
        # weil es nichts zu melden gab. Der fehlende Ausloeser lag woanders,
        # und er heisst jetzt ``_diff_git()``.
        changed = self._primed and snap != self._sched_snapshot
        self._sched_snapshot = snap
        n = 0
        if changed:
            self.bus.publish_state("feedstatus")
            n = 1
        # Zweiter, feiner Fingerabdruck im selben Takt (m.rau/bibi#143) — er
        # bedient ein anderes Ziel und darf den Header deshalb nicht anfassen.
        return n + self._diff_scheduler_jobs()

    def _fetch_scheduler_jobs(self) -> list[dict] | None:
        """Job-Zeilen des konfigurierten Schedulers, oder ``None``.

        Getrennt von :meth:`_fetch_scheduler_status`, weil es eine andere Route
        ist (``/-/schedule`` statt ``/-/status``) — dieselbe Adressfindung,
        dieselbe Zurueckhaltung im Fehlerfall.
        """
        try:
            import os

            from bibi import config
            url = (os.environ.get("BIBI_SCHEDULER_URL")
                   or config.read_env().get("BIBI_SCHEDULER_URL"))
            if not url:
                return None
            from bibi.controller.client import ControllerClient
            return ControllerClient(url, timeout=3.0).schedules()
        except Exception:  # noqa: BLE001 — der Host darf ausfallen (§2.7)
            return None

    def _diff_scheduler_jobs(self) -> int:
        """``live:<slug>``-Trigger fuer Job-Zustaende, die beim **Scheduler**
        wechseln (m.rau/bibi#143).

        **Befund m.rau, 2026-08-04/05:** *„ich sehe immer noch kein
        kontinuierliches Update, wenn sich ein Job Status aendert. … Ich muss
        immer wieder Refresh druecken."*

        ``_diff_jobs()`` vergleicht die **lokale** Job-DB — auf einem Client
        passiert dort nie etwas, die Jobs laufen beim Scheduler.
        ``_diff_scheduler()`` daneben sieht zwar den Scheduler, traegt aber nur
        **aggregierte** Zaehler und meldet ausschliesslich ``feedstatus``. Ein
        Job, der von ``pending`` auf ``running`` geht, erreichte damit weder die
        Zeile im Jobs-Screen noch die Slot-Kachel im Job-Detail.

        **Warum ein zweiter Fingerabdruck und nicht ein groesserer:** der linke
        Header-Block haengt an einem ``git status``. Ihn bei jeder
        Slug-Aenderung nachladen zu lassen waere genau der Firehose, den der
        Docstring oben ausschliesst. Zwei Fingerabdruecke, zwei Ziele — und die
        Trennung ist getestet, nicht bloss beabsichtigt.

        **Der Ausfall ist keine Aenderung.** Ist der Host weg (``None``), bleibt
        der letzte Stand stehen, statt jeden Slug als geaendert zu melden;
        dieselbe Zurueckhaltung wie beim groben Fingerabdruck. Der Ausfall
        selbst steht im Header, der ihn ohnehin zeigt.

        **Seit #77 ist das hier der Rueckfall, nicht der Hauptweg.** Lebt das
        Abonnement auf ``/-/events`` des Schedulers, kommt jeder Wechsel binnen
        einer Sekunde von dort — dann waere dieser Poll doppelte Arbeit fuer
        dieselbe Auskunft, mit vier Sekunden mehr Verzoegerung. Faellt das
        Abonnement aus, greift er sofort wieder: ein Abriss darf den Client
        nicht blind machen (§2.7). Genau dafuer ist ``live`` kein „war zuletzt
        erfolgreich" — waehrend eines Wiederaufbaus muss der Poll laufen.
        """
        if self.subscription is not None and self.subscription.live:
            return 0
        zeilen = self._fetch_scheduler_jobs()
        if zeilen is None:
            return 0
        snap: dict[str, tuple] = {}
        for r in zeilen:
            slug = r.get("slug")
            if not slug:
                continue
            # `row_status` zuerst: so heisst das Feld in den Scheduler-Zeilen
            # aus `/-/schedule`, wo `status` schlicht `None` ist (dieselbe
            # Reihenfolge wie in `jobs_view.py`).
            snap[slug] = (r.get("row_status") or r.get("status"),
                          r.get("fire") or r.get("next_fire_at"))
        vorher = self._sched_jobs_snapshot
        self._sched_jobs_snapshot = snap
        if not self._primed or vorher is None:
            return 0
        geaendert = [s for s, v in snap.items() if vorher.get(s) != v]
        # Ein verschwundener Slug ist ebenfalls eine Aenderung — sonst bleibt
        # eine geloeschte Zeile stehen, bis jemand neu laedt.
        geaendert += [s for s in vorher if s not in snap]
        if not geaendert:
            return 0
        for slug in geaendert:
            # Der Wert, der oben ohnehin gelesen und verglichen wurde (#79) —
            # bis dahin wurde er hier weggeworfen, um „dreckig" zu melden. Ein
            # verschwundener Slug hat keinen: `snap.get()` liefert None, und
            # ohne Wert entsteht kein Feld.
            wert = snap.get(slug)
            self.bus.publish_state(
                f"live:{slug}",
                None if wert is None else {"status": wert[0], "fire": wert[1]})
        # Die Liste hoert auf EIN Sammel-Target, nicht auf jeden Slug einzeln
        # (PLAN-36 Stufe 36.3) — ohne sie bewegt sich die Kachel im Detail und
        # die Zeile in der Liste nicht, und genau die sieht man zuerst.
        self.bus.publish_state("jobs")
        return len(geaendert) + 1

    # ── Innereien ───────────────────────────────────────────────────────────

    def _publish_live(self, slug: str, pinned_host, wert: dict | None = None) -> None:
        """Die Live-Region eines Slugs dreckig melden — mit dem Wert, falls
        bekannt (#79).

        Der Bucket-Slug bekommt denselben: er adressiert dieselbe Zeile, nur
        unter dem Namen, unter dem die Client-Detailseite sie kennt."""
        self.bus.publish_state(f"live:{slug}", wert)
        b = bucket_slug(slug, pinned_host)
        if b:
            self.bus.publish_state(f"live:{b}", wert)

    def _publish_journal(self, slug: str, pinned_host) -> None:
        self.bus.publish_state(f"journal:{slug}")
        b = bucket_slug(slug, pinned_host)
        if b:
            self.bus.publish_state(f"journal:{b}")

    def _track_tail(self, root: Path, row, *, freshly_started: bool) -> None:
        jid, status = row["id"], row["status"]
        if status not in _TAILED:
            return
        run_id = job_db.run_id_for(row["slug"], jid, row["fire"])
        tail = self._tails.get(jid)
        if tail is not None and tail["run_id"] == run_id:
            return
        path = root / "data" / "job" / run_id / "output.jsonl"
        kind = models.effective_kind(row["payload"])
        # Beim Priming mitten in einem laufenden Job (Daemon-Neustart): nur
        # NEUES streamen — den Bestand trägt das Seiten-/Fragment-Seed (E5).
        # Bei einem frisch beobachteten Start: ab Zeile 0.
        sent = 0
        if not freshly_started and not self._primed:
            sent = len(output_format.format_events(output.read_events(path), kind))
        self._tails[jid] = {"run_id": run_id, "path": path, "kind": kind, "sent": sent}

    def _pump_tail(self, jid: str) -> int:
        tail = self._tails[jid]
        formatted = output_format.format_events(
            output.read_events(tail["path"]), tail["kind"])
        n = 0
        for i, ev in enumerate(formatted[tail["sent"]:], start=tail["sent"] + 1):
            self.bus.publish_append(f"out:{jid}", i, ev)
            n += 1
        tail["sent"] = len(formatted)
        return n

    def _drop_tail(self, jid: str, *, final_read: bool) -> None:
        if jid not in self._tails:
            return
        if final_read:
            self._pump_tail(jid)
        del self._tails[jid]

    # ── Lifecycle (Muster Sweeper) ──────────────────────────────────────────

    async def _loop(self) -> None:
        await asyncio.sleep(self.interval)
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                await loop.run_in_executor(None, self.tick_once)
            except Exception:
                log.exception("Collector-Tick fehlgeschlagen")
            await asyncio.sleep(self.interval)

    async def start(self) -> None:
        if not self.autorun:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
