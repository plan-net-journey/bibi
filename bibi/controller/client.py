"""HTTP-Client des Controllers auf die feinkörnigen ``/-/``-JSON-Endpunkte
(PLAN-4 §2.1/§4.0). **Ein** Weg für lokal *und* remote (Verbund-Sicht); der
Controller hat **keinen** direkten DB-Zugriff (Akzeptanz §5). urllib wie der
``scheduler_client`` — keine zusätzliche Abhängigkeit."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class ControllerClient:
    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, params: dict | None = None,
                 *, json_body: dict | None = None) -> object:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items()
                                                 if v is not None})
        headers = {"Accept": "application/json"}
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            body = resp.read()
            return json.loads(body) if body else None

    def _get(self, path: str, params: dict | None = None) -> object:
        return self._request("GET", path, params)

    def status(self) -> dict:
        return self._get("/-/status") or {}

    def feed(self, *, days: int | None = None, weeks: int | None = None) -> dict:
        # Feed-Screen (PLAN-18): Entitäten (Case/Vault/System) + Heatmap-Grid.
        # weeks entkoppelt von days (PLAN-20 Befund 3) — eigenes Heatmap-Fenster.
        return self._get("/-/feed", {"days": days, "weeks": weeks}) or {}

    def journal(self, *, slug: str | None = None, host: str | None = None,
                limit: int | None = None, offset: int | None = None) -> list[dict]:
        return self._get(
            "/-/journal", {"slug": slug, "host": host, "limit": limit, "offset": offset}) or []

    def run_journal(self, *, slug: str | None = None, limit: int | None = None,
                    offset: int | None = None) -> list[dict]:
        # Rollenunabhängiges Gegenstück zu journal() (PLAN-17 Stufe 17.1): nur
        # die eigene /run-Historie (domain="local" ODER pinned_host gesetzt,
        # PLAN-28 Refactor D — s. job_db.list_journal()s mine_only-Filter),
        # funktioniert auch ohne scheduler-Rolle (/-/run/journal).
        return self._get(
            "/-/run/journal", {"slug": slug, "limit": limit, "offset": offset}) or []

    def run(self, *, slug: str | None = None, cmd: str | None = None) -> dict:
        # Lokaler On-Demand-Lauf auf DIESEM Knoten (/-/run, PLAN-3 §3.3b) — der
        # Start-Button des Jobs-Screens (PLAN-17 Stufe 17.2). Antwortet seit
        # PLAN-21 Befund 10, 2. Nachtrag sofort mit status="running" (nicht
        # mehr erst nach Lauf-Ende) — s. run_live()/run_live_list() für den
        # Zwischenstand.
        return self._request("POST", "/-/run", json_body={"slug": slug, "cmd": cmd}) or {}

    def run_live_list(self) -> dict:
        # Schlanke Übersicht aller gerade laufenden lokalen /run-Ausführungen
        # (PLAN-21 Befund 10, 2. Nachtrag) — für die Jobs-Liste.
        return self._get("/-/run/live") or {}

    def run_live(self, slug: str) -> dict:
        # Voller Zwischenstand (inkl. Live-Output) EINES laufenden lokalen
        # Runs — für die Job-Detailseite. 404 (nichts läuft) → HTTPError,
        # wie überall sonst in diesem Client (Aufrufer fängt das ab, §2.7).
        return self._get(f"/-/run/live/{urllib.parse.quote(slug, safe='')}")

    def run_live_kill(self, slug: str) -> dict:
        # Laufenden lokalen Run beenden (PLAN-21 Befund 10, User-Fund
        # 2026-07-10: "natürlich müssen wir kill können"). 404 → HTTPError.
        return self._request(
            "POST", f"/-/run/live/{urllib.parse.quote(slug, safe='')}/kill") or {}

    def run_live_reset(self, slug: str) -> dict:
        # Not-Aus für eine hängen gebliebene Live-Anzeige (User-Feedback
        # 2026-07-13: "warum nicht START, RESET und KILL wie auf Host") —
        # erzwingt den Terminalstatus, auch ohne greifbaren Prozess.
        # 404 → HTTPError.
        return self._request(
            "POST", f"/-/run/live/{urllib.parse.quote(slug, safe='')}/reset") or {}

    def jobs(self, *, status: str | None = None) -> list[dict]:
        return self._get("/-/job", {"status": status}) or []

    def schedules(self) -> list[dict]:
        data = self._get("/-/schedule") or {}
        return data.get("schedules", []) if isinstance(data, dict) else []

    def landings(self, *, since: float | None = None) -> list[dict]:
        # Lauf-Historie-Chart (PLAN-21 Befund 11 v2) — scheduler-gated, 501
        # ohne Scheduler-Rolle (der Aufrufer fängt das ab, s. controller/__init__.py).
        return self._get("/-/landings", {"since": since}) or []

    def schedule_config(self, slug: str) -> dict:
        return self._get(f"/-/schedule/{urllib.parse.quote(slug, safe='')}") or {}

    def journal_entry(self, journal_id: int) -> dict:
        # Metadaten einer Journal-Zeile (Execution-Detail, §C.4).
        return self._get(f"/-/journal/{journal_id}") or {}

    def run_output(self, journal_id: int) -> dict:
        return self._get(f"/-/journal/{journal_id}/output") or {}

    def local_run_entry(self, journal_id: int) -> dict:
        # PLAN-21 Befund 10: Gegenstück zu journal_entry(), aber rollenunabhängig
        # (/-/run/journal/{id} statt des scheduler-gated /-/journal/{id}) — nur
        # die eigene /run-Historie (domain="local" ODER pinned_host gesetzt,
        # PLAN-28 Refactor D), für die eigene Lauf-Historie-Detailseite eines
        # Clients.
        return self._get(f"/-/run/journal/{journal_id}") or {}

    def local_run_output(self, journal_id: int) -> dict:
        return self._get(f"/-/run/journal/{journal_id}/output") or {}

    def local_run_delete(self, journal_id: int) -> dict:
        # Gegenstück zu delete_journal(), aber rollenunabhängig (nur die
        # eigene /run-Historie, domain="local" ODER pinned_host gesetzt) —
        # die Jobs-Detailseite eines reinen Clients.
        return self._request("DELETE", f"/-/run/journal/{journal_id}") or {}

    def job_output(self, job_id: str) -> dict:
        # Live-Output eines laufenden Jobs (getypte Events) — /-/job/{id}/output.
        return self._get(f"/-/job/{job_id}/output") or {}

    def job_action(self, job_id: str, verb: str) -> dict:
        # verb ∈ {start, reset, kill} — wirkt auf den Live-Job (§5.6).
        return self._request("POST", f"/-/job/{job_id}/{verb}") or {}


    def delete_journal(self, journal_id: int) -> dict:
        return self._request("DELETE", f"/-/journal/{journal_id}") or {}

    def rescan(self) -> dict:
        return self._request("POST", "/-/rescan") or {}

    def maintenance(self, on: bool) -> dict:
        # POST = an, DELETE = aus (§ daemon-weit, /-/maintenance).
        return self._request("POST" if on else "DELETE", "/-/maintenance") or {}
