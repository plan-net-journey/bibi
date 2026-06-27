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
    from bibi import git_ops
    return git_ops.integrate(git_ops.current_branch())


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
    ) -> None:
        self._push = push
        self._pull = pull or push          # Push schließt Pull ein (§4.3)
        # Optionales Per-Tick-Gate für den tatsächlichen Push (§4.9-Kopplung):
        # der Daemon hängt es an ``auto_sync`` (stehende Push-Zustimmung), damit
        # ``/sync on|off`` live wirkt. None = ungated (mechanische Tests).
        self._consent = consent
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
                ok, loglines, kind = self._push_fn()
                self.last_push_at, self.last_ok, self.last_log = now, ok, loglines
                oks.append(ok)
                kinds.append(kind)
                self._debounce.reset()
                did["pushed"] = True
                activity.emit(log, logging.INFO, "sync.push",
                              role="synchronizer", ok=ok, kind=kind)

        if self._pull and self._pull_due(now):
            ok, kind = self._pull_fn()
            self._last_pull_at = now
            oks.append(ok)
            kinds.append(kind)
            did["pulled"] = True
            activity.emit(log, logging.INFO, "sync.pull",
                          role="synchronizer", ok=ok, kind=kind)

        self._resolve_conflict(oks, kinds)
        return did

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
