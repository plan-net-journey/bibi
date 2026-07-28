"""Synchronizer-Rolle: Git-Sync-Schicht (DESIGN §4.3, PLAN-2 §2.3).

Zwei Betriebsarten (A7):
- **Pull** — pullt kontinuierlich (Default alle 3 min), uni-direktional.
- **Push** — committet + pusht eigene Änderungen mit **adaptivem Debounce**
  (Poll alle 60 s via ``git diff --stat``; kein Datei-Watcher); schließt Pull ein.

Aufbau bewusst in drei Schichten, damit die Zeit-/Entscheidungslogik ohne Netz
und ohne asyncio testbar ist:
1. ``params_for`` / ``PushDebouncer`` — reine Entscheidung (wann pushen?).
2. ``Synchronizer.tick`` — ein deterministischer Schritt (injizierbare Git-IO + Clock).
3. ``Synchronizer.start/stop`` — der asyncio-Loop, der ``tick`` periodisch fährt.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

from bibi.daemon import activity

log = logging.getLogger("bibi.daemon.synchronizer")


# ── reine Debounce-Logik ────────────────────────────────────────────────────

@dataclass(frozen=True)
class DebounceParams:
    idle_s: int
    max_s: int


def params_for(diff_lines: int) -> DebounceParams:
    """Debounce-Fenster nach Diff-Größe (DESIGN §4.3-Tabelle)."""
    if diff_lines < 50:
        return DebounceParams(idle_s=600, max_s=1800)   # < 50  → 10 / 30 min
    if diff_lines <= 300:
        return DebounceParams(idle_s=300, max_s=900)    # 50–300 → 5 / 15 min
    return DebounceParams(idle_s=120, max_s=600)        # > 300 → 2 / 10 min


class PushDebouncer:
    """Entscheidet, wann ein dirty Tree gepusht wird.

    Ein „Änderungsfenster" öffnet sich mit der ersten erkannten Änderung
    (``first_change_at``) und verschiebt sein Idle-Maß mit jeder weiteren
    (``last_change_at``). Gepusht wird, sobald **Idle** verstrichen ist
    (Ruhe eingekehrt) **oder** das **Maximum** ab Fensterbeginn erreicht ist
    (Safety-Net gegen Dauer-Änderungen).
    """

    def __init__(self) -> None:
        self.first_change_at: float | None = None
        self.last_change_at: float | None = None
        self._last_stat = ""
        self._lines = 0

    def observe(self, now: float, diff_stat: str, diff_lines: int) -> None:
        diff_stat = diff_stat.strip()
        if not diff_stat:
            self.reset()
            return
        if diff_stat != self._last_stat:
            self.last_change_at = now
            if self.first_change_at is None:
                self.first_change_at = now
            self._last_stat = diff_stat
        self._lines = diff_lines

    def should_push(self, now: float) -> bool:
        if self.first_change_at is None or self.last_change_at is None:
            return False
        p = params_for(self._lines)
        idle = (now - self.last_change_at) >= p.idle_s
        maxed = (now - self.first_change_at) >= p.max_s
        return idle or maxed

    def reset(self) -> None:
        self.first_change_at = None
        self.last_change_at = None
        self._last_stat = ""
        self._lines = 0


# ── Default-Git-Anbindung (überschreibbar für Tests) ────────────────────────

def _default_diff_stat() -> tuple[str, int]:
    from bibi import git_ops
    return git_ops.diff_stat()


def _default_push() -> tuple[bool, list[str], str | None]:
    # commit (auto-sync) → integrate → push; transiente Message (A9), kein KI-Aufruf.
    # PLAN-21 Befund 8: läuft unbeaufsichtigt (niemand tippt hier je etwas) —
    # bibi-Identität statt der ambienten (bisher fälschlich menschlichen)
    # Git-Config, damit Commits aus diesem Hintergrund-Sweep im Log/Blame von
    # tatsächlich vom User selbst ausgelösten Commits unterscheidbar sind.
    #
    # guard_live_paths=True (Revision 2026-07-28, Befund 2): dieser Sweep ist
    # der unbeaufsichtigte Schreibpfad schlechthin, bekam Ebene 4s Idle-Guard
    # aber nie — weder für sein `integrate()` noch für den Reject-Retry in
    # `push()`, der bis dahin roh an git ging. Live beobachtet: der Retry rührte
    # den geteilten Checkout alle drei Minuten an, während derselbe Guard den
    # interaktiven Weg blockierte, und meldete seinen abgebrochenen Rebase als
    # `conflict` — womit `sync_conflict` gesetzt wurde, obwohl gar kein Konflikt
    # aufzulösen war. Beides erledigt sich damit, dass hier derselbe geschützte
    # Weg läuft wie im Pull-Loop darunter.
    from bibi import git_ops
    return git_ops.commit_and_push(
        None, git_ops.auto_commit_message(), do_push=True,
        identity=("bibi/sync", "bibi@local"), guard_live_paths=True)


def _default_pull() -> tuple[bool, str | None]:
    # strategy="merge": unbeaufsichtigter Hintergrund-Loop, niemand löst einen
    # Konflikt hier je interaktiv auf. Rebase spielt jeden Commit einzeln als
    # Patch neu ein und kann dabei an botgenerierter Historie (viele kleine
    # Job-Run-Commits) an einem Zwischenschritt scheitern, obwohl ein
    # einfacher 3-way-Merge der Endstände konfliktfrei wäre — per
    # ``git merge-tree`` in der Praxis verifiziert (PLAN: Sync-Divergenz
    # 2026-07-05). Der interaktive ``/sync``-Pfad bleibt bei Rebase (Default).
    #
    # guard_live_paths=True (PLAN-30 Nachtrag 2026-07-16): dieser Loop hatte
    # Ebene 4s Idle-Guard nie bekommen — nur der interaktive /sync-Pfad war
    # geschützt. Live beobachtet: ein echter Konflikt hier wurde alle 3
    # Minuten unbegrenzt neu versucht (kein Backoff, anders als Ebene 2 für
    # Job-Branches), dieselbe Fehlerklasse wie der Ursprungsvorfall, nur auf
    # der Pull- statt der Job-Branch-Seite. Ein Konflikt selbst bricht zwar
    # sauber ab (kein Datenverlust), aber jeder Versuch rührt den geteilten
    # Checkout an — genau das Risiko, das Ebene 4 verhindern soll, wenn eine
    # betroffene Datei gerade dirty oder kürzlich bearbeitet ist.
    from bibi import git_ops
    return git_ops.integrate(git_ops.current_branch(), strategy="merge",
                             guard_live_paths=True)


# ── Synchronizer ────────────────────────────────────────────────────────────

class Synchronizer:
    def __init__(
        self,
        *,
        pull: bool = False,
        push: bool = False,
        pull_interval_s: int = 180,
        poll_s: int = 60,
        diff_stat=_default_diff_stat,
        push_fn=_default_push,
        pull_fn=_default_pull,
        clock=time.monotonic,
        consent=None,
        lock=None,
        repo_root=None,
    ) -> None:
        self._push = push
        self._pull = pull or push          # Push schließt Pull ein (§4.3)
        # Optionales Per-Tick-Gate für den tatsächlichen Push (§4.9-Kopplung):
        # der Daemon hängt es an ``auto_sync`` (stehende Push-Zustimmung), damit
        # ``/sync on|off`` live wirkt. None = ungated (mechanische Tests).
        self._consent = consent
        # Gemeinsamer ``sync_lock`` mit dem Merge-back (PLAN-6 §3 D2): Pull/Push und
        # Merge nach trunk dürfen sich nicht überschneiden. None = ungated (Tests).
        self._lock = lock
        # F-a (PLAN-7): periodischer Merge-Sweep liegengebliebener agent/*-Branches.
        # None ⇒ kein Sweep (mechanische Tests). Sonst je Tick remerge_all.
        self._repo_root = repo_root
        self.pull_interval_s = pull_interval_s
        self.poll_s = poll_s
        self._diff_stat = diff_stat
        self._push_fn = push_fn
        self._pull_fn = pull_fn
        self._clock = clock
        self._debounce = PushDebouncer()
        self._task: asyncio.Task | None = None
        self._last_pull_at: float | None = None
        self.last_push_at: float | None = None
        self.last_ok: bool | None = None
        self.last_log: list[str] = []

    def _lock_ctx(self):
        return self._lock if self._lock is not None else contextlib.nullcontext()

    def push_now(self) -> tuple[bool, list[str], str | None] | None:
        """Sofort pushen (debouncer-unabhängig), z. B. nach einem Merge-back (D5).

        Der Push-Debouncer beobachtet nur den Working-Tree-Diff; ein Merge-Commit
        (sauberer Tree) würde ihn nie auslösen. Respektiert Push-Rolle + Zustimmung
        + ``sync_lock``. ``None`` wenn Push aus/ohne Zustimmung."""
        if not self._push or (self._consent is not None and not self._consent()):
            return None
        with self._lock_ctx():
            ok, loglines, kind = self._push_fn()
        self.last_push_at, self.last_ok, self.last_log = self._clock(), ok, loglines
        activity.emit(log, logging.INFO, "sync.push", "; ".join(loglines),
                      role="synchronizer", ok=ok, kind=kind)
        return ok, loglines, kind

    # — Laufzeit-Toggle (§4.3-Endpunkte) —
    def set_pull(self, value: bool) -> None:
        self._pull = value or self._push

    def set_push(self, value: bool) -> None:
        self._push = value
        if value:
            self._pull = True

    def status(self) -> dict:
        return {
            "pull": self._pull,
            "push": self._push,
            "last_push_at": self.last_push_at,
            "last_ok": self.last_ok,
            "pull_interval_s": self.pull_interval_s,
            "poll_s": self.poll_s,
        }

    # — ein deterministischer Schritt —
    def tick(self, now: float | None = None) -> dict:
        now = self._clock() if now is None else now
        did = {"pushed": False, "pulled": False}
        oks: list[bool] = []
        kinds: list[str | None] = []

        if self._push:
            stat, lines = self._diff_stat()
            self._debounce.observe(now, stat, lines)
            # observe() läuft immer (Fenster-Tracking); der Push selbst wartet
            # zusätzlich auf die Zustimmung. Ohne Zustimmung bleibt das Fenster
            # offen → sobald sie kommt, pusht ein bereits abgelaufenes Fenster sofort.
            if self._debounce.should_push(now) and (self._consent is None or self._consent()):
                with self._lock_ctx():
                    ok, loglines, kind = self._push_fn()
                self.last_push_at, self.last_ok, self.last_log = now, ok, loglines
                oks.append(ok)
                kinds.append(kind)
                self._debounce.reset()
                did["pushed"] = True
                activity.emit(log, logging.INFO, "sync.push", "; ".join(loglines),
                              role="synchronizer", ok=ok, kind=kind)

        if self._pull and self._pull_due(now):
            with self._lock_ctx():
                ok, kind = self._pull_fn()
            self._last_pull_at = now
            oks.append(ok)
            kinds.append(kind)
            did["pulled"] = True
            activity.emit(log, logging.INFO, "sync.pull",
                          role="synchronizer", ok=ok, kind=kind)

        self._resolve_conflict(oks, kinds)
        self._merge_sweep()
        self._worktree_sweep()
        return did

    def _merge_sweep(self) -> None:
        """F-a (PLAN-7): liegengebliebene ``agent/*``-Branches nach trunk mergen —
        das Retry-Netz für Merge-backs, die beim Report scheiterten (z. B. dirty
        Tree). Unter dem gemeinsamen ``sync_lock`` (remerge_all hält ihn selbst),
        darum **außerhalb** der Pull/Push-Lock-Blöcke aufrufen (Lock nicht reentrant)."""
        if self._repo_root is None:
            return
        from bibi.daemon import mergeback
        try:
            results = mergeback.remerge_all(repo_root=self._repo_root, lock=self._lock)
        except Exception:  # ein Sweep-Fehler darf den Sync-Loop nie killen (§2.7)
            log.warning("merge-sweep übersprungen", exc_info=True)
            return
        merged = [b for b, s in results.items() if s == "merged"]
        if merged:
            activity.emit(log, logging.INFO, "merge.sweep", role="synchronizer",
                          merged=len(merged), branches=",".join(merged))
            # Bug 2026-07-07 (User-Fund: "warum steht bei sarasate SYNC: ahead"):
            # ein Merge-Commit hinterlässt einen sofort wieder sauberen Tree — der
            # Push-Debouncer (der NUR den Working-Tree-Diff beobachtet, s.
            # push_now()-Docstring) sieht dort nie etwas, das "idle" werden könnte,
            # und pusht deshalb nie von selbst. Jeder stündliche Sweep häufte so
            # unbemerkt einen weiteren unpushten Commit an (live beobachtet: 8
            # Commits Rückstand). push_now() respektiert Push-Rolle/Zustimmung/Lock
            # selbst — hier bewusst ungated aufgerufen, kein zusätzliches Gate nötig.
            self.push_now()
        # Bisher stumm: ein liegengebliebener agent/*-Branch (Konflikt/Fehler)
        # verschwand ohne jede Spur im Log (verschleierte den dirty-trunk-Fund
        # 2026-07-05 lange) — jetzt sichtbar, damit ein hängender Mergeback
        # nicht erst durch manuelle Git-Archäologie auffällt. PLAN-30 Ebene 2:
        # nur ein Fehlschlag, der DIESEN Tick tatsächlich neu versucht wurde
        # (conflict/error), ist WARNING-würdig — "blocked" (Modus A, löst sich
        # von selbst), "quarantined" (bewusst übersprungen, s. mergeback.py)
        # und "live_edit" (Ebene 4: Datei gerade bearbeitet, ebenfalls kein
        # Fehlschlag) sind bereits bekannt/erwartet; sie hier trotzdem als
        # WARNING zu wiederholen wäre exakt das "WARNING, die niemand liest"-
        # Problem, das dieser ganze PLAN beheben soll (agent/Witz-83837197:
        # 1440+ identische Zeilen, alle 60s, nie beobachtet). "repo_busy"
        # (Review-Runde 4, Fund 1) ebenso: ein anderer, bereits offener
        # Merge/Rebase ist kein Fehlschlag DIESES Branches.
        stuck = {b: s for b, s in results.items() if s in ("conflict", "error")}
        if stuck:
            activity.emit(log, logging.WARNING, "merge.sweep.stuck", role="synchronizer",
                          stuck=len(stuck),
                          branches=",".join(f"{b}:{s}" for b, s in stuck.items()))
        quiet = {b: s for b, s in results.items()
                if s in ("blocked", "quarantined", "live_edit", "repo_busy")}
        if quiet:
            activity.emit(log, logging.DEBUG, "merge.sweep.quiet", role="synchronizer",
                          quiet=len(quiet),
                          branches=",".join(f"{b}:{s}" for b, s in quiet.items()))

    def _worktree_sweep(self) -> None:
        """Periodischer Aufräum-Sweep für Job-Worktrees (Bug "Kein Worktree
        Cleanup", Case 20260621.Bibi4-870bd9db, 2026-07-22): reguläre
        Scheduler-Dispatches legen ihren Worktree einmal an und lassen ihn
        über Fires hinweg liegen (``worktree.prepare()``, anders als
        ephemere ``run``/``test``-Läufe, die sich schon selbst aufräumen,
        s. ``worker.py::run_pinned()``) — für Jobs, die nie wieder feuern,
        entfernte das bisher niemand (live beobachtet: 19 Leichen, ~31 GB,
        sarasate-root auf 92 %). Dieselbe "noch in Gebrauch"-Regel wie
        doctors Orphan-Check (``job_db.active_worktree_slugs()``), damit
        Sweep und doctor nie auseinanderlaufen. Ungemergte Commits (F-b,
        ``worktree.prepare()``s Dokstring) werden nie weggeworfen — der
        Merge-Sweep oben bekommt zuerst die Chance, sie nach trunk zu
        holen; erst ein Worktree ohne Voraus-Commits gilt als sicher
        entfernbar. Wie ``_merge_sweep()``: ein Sweep-Fehler darf den
        Sync-Loop nie killen (§2.7)."""
        if self._repo_root is None:
            return
        work_dir = self._repo_root / "data" / "worktrees"
        if not work_dir.is_dir():
            return
        from bibi.daemon import job_db, worktree
        db_path = self._repo_root / "data" / "jobs.sqlite"
        if not db_path.exists():
            return
        try:
            conn = job_db.connect(db_path)
            try:
                known = job_db.active_worktree_slugs(conn)
            finally:
                conn.close()
        except Exception:
            log.warning("worktree-sweep übersprungen (DB nicht lesbar)", exc_info=True)
            return

        removed = []
        for path in sorted(work_dir.iterdir()):
            if not path.is_dir() or path.name in known:
                continue
            try:
                branch = worktree.branch_name(path.name)
                if worktree.is_ahead(repo_root=self._repo_root, branch=branch):
                    continue  # Merge-Sweep zuerst — nie ungemergte Arbeit wegwerfen
                with self._lock_ctx():
                    worktree.remove(repo_root=self._repo_root, worktree=path)
            except Exception:
                log.warning("worktree-sweep: %s konnte nicht entfernt werden",
                           path.name, exc_info=True)
                continue
            removed.append(path.name)
        if removed:
            activity.emit(log, logging.INFO, "worktree.sweep", role="synchronizer",
                          removed=len(removed), slugs=",".join(removed))

    def _pull_due(self, now: float) -> bool:
        return self._last_pull_at is None or (now - self._last_pull_at) >= self.pull_interval_s

    @staticmethod
    def _resolve_conflict(oks: list[bool], kinds: list[str | None]) -> None:
        # Konflikt hat innerhalb eines Ticks Vorrang: ein erfolgreicher Pull darf
        # einen im selben Tick erkannten Push-Konflikt nicht überschreiben.
        from bibi import state
        if "live_edit" in kinds:
            # Befund 1 (Sync-Divergenz 2026-07-28): ein Idle-Skip war bisher
            # nach außen komplett stumm — dass der Guard hier dauerhaft griff,
            # ließ sich nur durch Nachstellen von Hand herausfinden. Mit Pfad
            # und Alter ist sofort erkennbar, ob da ein Mensch tippt oder (wie
            # im Vorfall) die Ausgabedatei eines kurz getakteten Schedules
            # liegt, die nie ruhig genug wird.
            from bibi import git_ops
            blocking = git_ops.live_overlap_report()
            oldest = next((age for _p, age in blocking if age is not None), None)
            activity.emit(log, logging.INFO, "sync.live_edit",
                          "Pull/Push übersprungen — Pfade gerade in Bearbeitung",
                          role="synchronizer",
                          paths=",".join(p for p, _age in blocking[:5]) or "?",
                          age_s=round(oldest) if oldest is not None else -1)
        if "conflict" in kinds:
            state.set_sync_conflict(True)
            activity.emit(log, logging.WARNING, "sync.conflict",
                          "Pull/Push-Konflikt — Auflösung via /sync (§1.6)",
                          role="synchronizer")
        elif any(oks) and state.get_sync_conflict():
            state.set_sync_conflict(False)
            activity.emit(log, logging.INFO, "sync.conflict_cleared",
                          role="synchronizer")

    # — asyncio-Loop —
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            try:
                await asyncio.to_thread(self.tick)
            except Exception as e:  # defensiv: ein Tick-Fehler killt den Loop nie
                log.warning("synchronizer tick failed: %s", e)
