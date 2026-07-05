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
    from bibi import git_ops
    return git_ops.commit_and_push(None, git_ops.auto_commit_message(), do_push=True)


def _default_pull() -> tuple[bool, str | None]:
    # strategy="merge": unbeaufsichtigter Hintergrund-Loop, niemand löst einen
    # Konflikt hier je interaktiv auf. Rebase spielt jeden Commit einzeln als
    # Patch neu ein und kann dabei an botgenerierter Historie (viele kleine
    # Job-Run-Commits) an einem Zwischenschritt scheitern, obwohl ein
    # einfacher 3-way-Merge der Endstände konfliktfrei wäre — per
    # ``git merge-tree`` in der Praxis verifiziert (PLAN: Sync-Divergenz
    # 2026-07-05). Der interaktive ``/sync``-Pfad bleibt bei Rebase (Default).
    from bibi import git_ops
    return git_ops.integrate(git_ops.current_branch(), strategy="merge")


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
        activity.emit(log, logging.INFO, "sync.push", role="synchronizer", ok=ok, kind=kind)
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
                activity.emit(log, logging.INFO, "sync.push",
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
        # Bisher stumm: ein liegengebliebener agent/*-Branch (Konflikt/Fehler)
        # verschwand ohne jede Spur im Log (verschleierte den dirty-trunk-Fund
        # 2026-07-05 lange) — jetzt sichtbar, damit ein hängender Mergeback
        # nicht erst durch manuelle Git-Archäologie auffällt.
        stuck = {b: s for b, s in results.items() if s not in ("merged", "up_to_date")}
        if stuck:
            activity.emit(log, logging.WARNING, "merge.sweep.stuck", role="synchronizer",
                          stuck=len(stuck),
                          branches=",".join(f"{b}:{s}" for b, s in stuck.items()))

    def _pull_due(self, now: float) -> bool:
        return self._last_pull_at is None or (now - self._last_pull_at) >= self.pull_interval_s

    @staticmethod
    def _resolve_conflict(oks: list[bool], kinds: list[str | None]) -> None:
        # Konflikt hat innerhalb eines Ticks Vorrang: ein erfolgreicher Pull darf
        # einen im selben Tick erkannten Push-Konflikt nicht überschreiben.
        from bibi import state
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
