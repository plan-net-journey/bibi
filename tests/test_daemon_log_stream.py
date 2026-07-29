"""``/-/log/stream`` — SSE-Aktivitätslog (PLAN-5 §5.4 Slice B)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from bibi.daemon import roles
from bibi.daemon.app import create_app


def _seed_log(root: Path, *events: str) -> None:
    d = root / "data" / "daemon-log"
    d.mkdir(parents=True, exist_ok=True)
    (d / "daemon.jsonl").write_text(
        "".join(json.dumps({"ts": "2026-06-27T08:30:00+00:00", "level": "INFO",
                            "role": "scheduler", "event": e, "msg": ""}) + "\n"
                for e in events),
        encoding="utf-8",
    )


def test_log_stream_backfill_snapshot(team_repo: Path):
    # follow=false ⇒ nur Backfill, Stream terminiert (testbar via client.get).
    _seed_log(team_repo, "scheduler.rescan", "worker.pickup")
    c = TestClient(create_app(roles.resolve(set())))  # unbedingt, jede Rolle
    r = c.get("/-/log/stream", params={"follow": "false", "n": 10})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "data: " in r.text
    assert "scheduler.rescan" in r.text and "worker.pickup" in r.text


def test_log_stream_backfill_respects_n(team_repo: Path):
    _seed_log(team_repo, "e1", "e2", "e3")
    c = TestClient(create_app(roles.resolve(set())))
    r = c.get("/-/log/stream", params={"follow": "false", "n": 1})
    assert r.text.count("data: ") == 1 and "e3" in r.text and "e1" not in r.text


def test_log_stream_empty_when_no_log(team_repo: Path):
    c = TestClient(create_app(roles.resolve(set())))
    r = c.get("/-/log/stream", params={"follow": "false"})
    assert r.status_code == 200 and r.text.strip() == ""
