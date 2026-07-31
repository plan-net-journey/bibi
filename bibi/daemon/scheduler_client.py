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

    Merge-back nach einem erfolgreichen ``complete``-Report mit Ergebnis-Branch
    läuft NICHT über diese Klasse (früher: ein ``on_complete``-Hook hier, entfernt
    PLAN-30 Ebene 1 v2, Fund 2026-07-15 — der detachte Wrapper-Subprozess meldet
    Terminal-Status per Direct-SQLite und ruft ``.report()`` hier nie auf, der Hook
    war seit dem Wrapper-Refactor 2026-06-28 unerreichbarer Code). Der Wrapper
    triggert den Merge-back stattdessen selbst per zusätzlichem HTTP-Call gegen
    ``/-/scheduler/status/{id}`` (``bibi/wrapper/__init__.py::_report_terminal()``).

    ``pinned_only`` (PLAN-28): treibt einen zweiten, rollenunabhängigen ``Worker``
    (in ``create_app`` immer gestartet), der ausschließlich ``jobs.pinned_host ==
    dieser Host``-Zeilen dispatcht (Retry-Redispatch/Deferred-Re-Arm für gepinnte
    ``/run``-Läufe) — nie die geteilte Team-Queue anfasst (s. ``reserve_next()``s
    ``pinned_only``-Filter)."""

    def __init__(self, db_path: Path | None = None, *, pinned_only: bool = False) -> None:
        self.db_path = db_path
        self.pinned_only = pinned_only

    def next(self, worker: str | None = None, host: str | None = None) -> dict | None:
        conn = job_db.connect(self.db_path)
        try:
            return job_db.reserve_next(conn, worker=worker, host=host,
                                       pinned_only=self.pinned_only)
        finally:
            conn.close()

    def report(self, job_id: str, **fields) -> str:
        conn = job_db.connect(self.db_path)
        try:
            return job_db.report_status(conn, job_id, **fields)
        finally:
            conn.close()

    def register(self, worker: str, host: str, git_status: str | None = None, *,
                 node_id: str | None = None, git_user: str | None = None,
                 role: str | None = None, port: int | None = None,
                 client_config_version: str | None = None,
                 engine: str | None = None,
                 git_commit: str | None = None) -> dict | None:
        return None  # Single-Node: keine Anmeldung, kein Bundle zu holen

    def deregister(self, node_id: str) -> bool:
        return False  # Single-Node: nie angemeldet, also nichts abzumelden


class RemoteScheduler:
    """Verbundener Worker — HTTP gegen den entfernten Scheduler (``--connect``)."""

    def __init__(self, base_url: str, *, secret: str | None = None, timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.secret = secret
        self.timeout = timeout

    def _post(self, path: str, payload: dict, *,
              extra_headers: dict[str, str] | None = None) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers[SECRET_HEADER] = self.secret
        if extra_headers:
            headers.update(extra_headers)
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

    def register(self, worker: str, host: str, git_status: str | None = None, *,
                 node_id: str | None = None, git_user: str | None = None,
                 role: str | None = None, port: int | None = None,
                 client_config_version: str | None = None,
                 engine: str | None = None,
                 git_commit: str | None = None) -> dict | None:
        # PLAN-32 Stufe 32.1/32.2: liefert jetzt die volle Host-Antwort zurück
        # (approval_status-Nebeneffekte + config_version/config_bundle) —
        # vorher wurde die Antwort verworfen. Ein non-200 (z. B. 401 bei
        # "blocked") muss als Fehler beim Aufrufer ankommen (Heartbeat._beat()s
        # bestehendes try/except erkennt das dann korrekt als fehlgeschlagenen
        # Heartbeat), nicht still verschluckt werden.
        code, body = self._post("/-/worker", {
            "worker": worker, "host": host, "git_status": git_status,
            "node_id": node_id, "git_user": git_user, "role": role, "port": port,
            "client_config_version": client_config_version,
            # m.rau/bibi#19 — beide optional im Schema, ein älterer Host
            # ignoriert sie einfach (FastAPI verwirft unbekannte Felder nicht
            # mit 422, solange das Modell sie nicht verbietet).
            "engine": engine, "git_commit": git_commit,
        })
        if code != 200:
            raise RuntimeError(f"heartbeat rejected: HTTP {code}")
        return body if isinstance(body, dict) else None

    def deregister(self, node_id: str) -> bool:
        """Beim Host abmelden (m.rau/bibi#47) — ``True`` bei HTTP 200.

        Der ``X-Bibi-Node-Id``-Header ist hier nicht Beiwerk, sondern der
        Nachweis: die Route lässt einen Knoten nur **sich selbst** abmelden und
        vergleicht Header gegen Pfad. Ohne ihn käme ein 403 zurück, und zwar
        zu Recht.

        Wirft nie. Der Aufrufer ist ein Shutdown-Pfad; ein Host, der gerade
        nicht erreichbar ist, darf das Beenden nicht aufhalten — dafür gibt es
        die Stale-Erkennung als Netz.
        """
        try:
            code, _ = self._post(f"/-/worker/{node_id}/disconnect", {},
                                 extra_headers={"X-Bibi-Node-Id": node_id})
        except Exception:  # noqa: BLE001 — Netz weg, Host weg, DNS weg
            return False
        return code == 200

    def _get(self, path: str) -> object:
        headers = {"Accept": "application/json"}
        if self.secret:
            headers[SECRET_HEADER] = self.secret
        req = urllib.request.Request(self.base + path, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            body = resp.read()
            return json.loads(body) if body else None

    def schedules(self) -> list[dict]:
        """GET ``/-/schedule`` beim entfernten Scheduler (PLAN-17 Befund 2 Punkt 3)
        — Remote-Seite des Jobs-Screen-Abgleichs. Reine Leseoperation; Fehler
        (Host down, Netz) bleiben Sache des Aufrufers (Controller fängt defensiv,
        §2.7)."""
        data = self._get("/-/schedule")
        return data.get("schedules", []) if isinstance(data, dict) else []
