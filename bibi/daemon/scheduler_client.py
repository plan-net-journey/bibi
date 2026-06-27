"""Scheduler-Client: wie der Worker an Arbeit kommt + meldet (DESIGN §4.5; PLAN-3 §3.6).

Zwei Implementierungen hinter einer Schnittstelle (``next``/``report``/``register``):

- **``LocalScheduler``** — Single-Node: ruft ``job_db`` direkt (genau 1 Scheduler im
  selben Prozess). Kein Netz.
- **``RemoteScheduler``** — verbundener Worker (``--connect``): HTTP gegen
  ``BIBI_SCHEDULER_URL`` (``/-/scheduler/next``, ``/-/scheduler/status/{id}``,
  ``/-/worker``). Optionaler Shared-Secret-Header (§1.3).

So bleibt der Worker-Kern (Worktree + Wrapper + output.jsonl) identisch — nur der
Auswahl-/Melde-/Anmelde-Pfad wechselt zwischen lokal und remote.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from bibi.daemon import job_db

SECRET_HEADER = "X-Bibi-Secret"


class LocalScheduler:
    """In-Process-Scheduler — direkte ``job_db``-Aufrufe (Single-Node).

    ``on_complete(branch)`` (optional): Hook, der nach einem erfolgreichen terminalen
    ``complete``-Report mit Ergebnis-Branch feuert — der lokale Worker geht **nicht**
    über die HTTP-Route ``/-/scheduler/status``, darum hängt der Merge-back hier
    (PLAN-6; sonst mergt nur der Remote-Pfad). Wird in ``create_app`` verdrahtet."""

    def __init__(self, db_path: Path | None = None, *, on_complete=None) -> None:
        self.db_path = db_path
        self.on_complete = on_complete

    def next(self, worker: str | None = None, host: str | None = None) -> dict | None:
        conn = job_db.connect(self.db_path)
        try:
            return job_db.reserve_next(conn, worker=worker, host=host)
        finally:
            conn.close()

    def report(self, job_id: str, **fields) -> str:
        conn = job_db.connect(self.db_path)
        try:
            res = job_db.report_status(conn, job_id, **fields)
        finally:
            conn.close()
        if (res == "ok" and self.on_complete is not None
                and fields.get("status") == "complete" and fields.get("branch")):
            self.on_complete(fields["branch"])
        return res

    def register(self, worker: str, host: str, git_status: str | None = None) -> None:
        pass  # Single-Node: keine Anmeldung nötig


class RemoteScheduler:
    """Verbundener Worker — HTTP gegen den entfernten Scheduler (``--connect``)."""

    def __init__(self, base_url: str, *, secret: str | None = None, timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.secret = secret
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers[SECRET_HEADER] = self.secret
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or "null")
            except Exception:
                return e.code, None

    def next(self, worker: str | None = None, host: str | None = None) -> dict | None:
        code, body = self._post("/-/scheduler/next", {"worker": worker})
        if code == 200 and isinstance(body, dict):
            return body
        return None  # 204 (leer) oder Fehler

    def report(self, job_id: str, **fields) -> str:
        # None-Werte weglassen (StatusReport-Defaults greifen).
        payload = {k: v for k, v in fields.items() if v is not None}
        code, _ = self._post(f"/-/scheduler/status/{job_id}", payload)
        return {200: "ok", 409: "invalid", 404: "not_found"}.get(code, "error")

    def register(self, worker: str, host: str, git_status: str | None = None) -> None:
        self._post("/-/worker", {"worker": worker, "host": host, "git_status": git_status})
