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

    def _get(self, path: str, params: dict | None = None) -> object:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items()
                                                 if v is not None})
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            body = resp.read()
            return json.loads(body) if body else None

    def status(self) -> dict:
        return self._get("/-/status") or {}

    def journal(self, *, slug: str | None = None, host: str | None = None) -> list[dict]:
        return self._get("/-/journal", {"slug": slug, "host": host}) or []

    def jobs(self, *, status: str | None = None) -> list[dict]:
        return self._get("/-/job", {"status": status}) or []

    def schedules(self) -> list[dict]:
        data = self._get("/-/schedule") or {}
        return data.get("schedules", []) if isinstance(data, dict) else []
