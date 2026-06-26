"""Registry verbundener Worker beim Scheduler (DESIGN §4.5/A12; PLAN-3 §3.6).

In-Memory (ein Daemon-Prozess), thread-safe — verbundene Worker melden sich per
Heartbeat (``POST /-/worker``) an; ``GET /-/worker`` + ``/-/status`` lesen sie.
Geht der Daemon neu auf, bauen die Heartbeats die Registry binnen Sekunden wieder
auf (keine Persistenz nötig).
"""

from __future__ import annotations

import threading
import time

#: Ohne Heartbeat seit dieser Spanne (s) gilt ein Worker als veraltet.
STALE_AFTER = 60.0


class WorkerRegistry:
    def __init__(self) -> None:
        self._w: dict[str, dict] = {}
        self._lock = threading.Lock()

    def heartbeat(
        self, worker: str, host: str, git_status: str | None = None,
        now: float | None = None,
    ) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            entry = self._w.get(worker) or {"worker": worker, "connected_at": now}
            entry.update(host=host, git_status=git_status, last_heartbeat=now)
            self._w[worker] = entry
            return dict(entry)

    def list(self, *, stale_after: float = STALE_AFTER, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with self._lock:
            return [
                {**e, "stale": (now - e["last_heartbeat"]) > stale_after}
                for e in self._w.values()
            ]

    def fresh_count(self, *, stale_after: float = STALE_AFTER, now: float | None = None) -> int:
        return sum(1 for w in self.list(stale_after=stale_after, now=now) if not w["stale"])
