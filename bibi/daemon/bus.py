"""FE-Event-Bus (PLAN-36 Stufe 36.1): ein globaler Ereignisstrom pro Daemon.

Zwei Komponenten, bewusst FastAPI-frei (rein testbar):

- :class:`Bus` — In-Process-Pub/Sub. Publisher dürfen aus beliebigen Threads
  kommen (der Collector tickt via ``run_in_executor``, Muster ``Sweeper``);
  Konsumenten sind asyncio-Generatoren (die ``/-/events``-Route). Zwei
  Event-Klassen (PLAN-36 E2): **Zustands-Events** sind leere Dirty-Meldungen
  pro Ziel-Element, idempotent, last-write-wins — pro Abonnent per Ziel
  koalesziert, nie gepuffert-historisiert. **Append-Events** (Output-Zeilen)
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

Für gepinnte Läufe (``pinned_host`` gesetzt, Slug trägt ein
``-<8hex>``-Suffix, s. ``run_pinned()``) wird zusätzlich der Bucket-Slug
publiziert — die Client-Detailseite adressiert über ihn.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
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
        self.state: dict[str, None] = {}  # Ziel → None; Insertion-Reihenfolge
        self.appends: deque = deque()
        self.wakeup = asyncio.Event()
        self.loop = loop


class Bus:
    """Threadsicherer, historienfreier Pub/Sub — s. Modul-Docstring."""

    def __init__(self, *, append_limit: int = _APPEND_LIMIT) -> None:
        self._subs: set[_Subscriber] = set()
        self._lock = threading.Lock()
        self._append_limit = append_limit

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

    def publish_state(self, target: str) -> None:
        with self._lock:
            subs = list(self._subs)
            for s in subs:
                s.state[target] = None
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
            out: list[dict] = [{"t": "state", "target": t} for t in sub.state]
            sub.state.clear()
            out.extend(sub.appends)
            sub.appends.clear()
        return out


class Collector:
    """Der eine Poller des Knotens — s. Modul-Docstring und PLAN-36 E4.

    Lifecycle-Muster wie ``Sweeper``: ``start()``/``stop()`` im App-Lifespan,
    erst schlafen, dann ticken (derselbe Test-Schutz wie dort), Tick selbst
    blockierend via ``run_in_executor``. ``tick_once()`` ist synchron und
    direkt testbar."""

    def __init__(self, bus: Bus, *, db_path: Path | None = None,
                 repo_root: Path | None = None, interval: float = 1.0,
                 autorun: bool = True) -> None:
        self.bus = bus
        self.db_path = db_path
        self.repo_root = repo_root
        self.interval = interval
        self.autorun = autorun
        self._jobs: dict[str, tuple] = {}    # job_id → (status, fire)
        self._journal_max: int | None = None
        self._tails: dict[str, dict] = {}    # job_id → {run_id, path, kind, sent}
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
        seen: dict[str, tuple] = {}
        for r in rows:
            jid, slug = r["id"], r["slug"]
            seen[jid] = (r["status"], r["fire"], slug, r["pinned_host"])
            changed = (self._primed
                       and self._jobs.get(jid, (None,))[:2] != seen[jid][:2])
            if changed:
                self._publish_live(slug, r["pinned_host"])
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
                stats["state"] += 1
        self._jobs = seen

        for r in new_journal:
            self._publish_journal(r["slug"], r["pinned_host"])
            stats["state"] += 1
        self._journal_max = jmax

        # Tails: Output-Zuwachs publizieren; Läufe, die nicht mehr aktiv
        # wachsen (Terminal/deferred), nach einem letzten Read entlassen.
        for jid in list(self._tails):
            stats["append"] += self._pump_tail(jid)
            if seen.get(jid, (None,))[0] not in _TAILED:
                del self._tails[jid]

        self._primed = True
        return stats

    # ── Innereien ───────────────────────────────────────────────────────────

    def _publish_live(self, slug: str, pinned_host) -> None:
        self.bus.publish_state(f"live:{slug}")
        b = bucket_slug(slug, pinned_host)
        if b:
            self.bus.publish_state(f"live:{b}")

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
