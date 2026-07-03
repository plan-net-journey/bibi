"""Schedule-Attribute-Seite (§3b) — reine Konfiguration + schmaler Scheduling-Rest,
kein Lauf-Zustand mehr (User-Feedback: "die [Runtime-Attribute] hängen am Lauf")."""

from __future__ import annotations

from bibi.controller import render

#: app_port bewusst ausgenommen: das Feld bleibt legitim in der Konfiguration
#: (_ATTRS_CONFIG_ORDER) stehen — nur die doppelte Runtime-Kopie fliegt raus.
_DROPPED_RUNTIME_KEYS = [
    "status", "reason", "attempt", "enqueued_at", "started_at", "finished_at",
    "exit_code", "deferred_at", "host", "worker", "output_ref", "app_url", "pid",
]


def test_schedule_attrs_page_trims_runtime_section():
    data = {
        "slug": "boom", "kind": "job", "payload": "echo hi", "schedule": "now",
        "priority": 0, "app_port": 8080,
        "id": "abc123", "next_fire_at": 123.0, "fire": 2,
        "status": "complete", "reason": "x", "attempt": 1, "enqueued_at": 1.0,
        "started_at": 2.0, "finished_at": 3.0, "exit_code": 0, "deferred_at": 4.0,
        "host": "h", "worker": "w", "output_ref": "data/x/output.jsonl",
        "app_url": "http://127.0.0.1:8080/", "pid": 999,
    }
    html = render.schedule_attrs_page("boom", data)
    assert "Scheduling" in html
    assert "Runtime" not in html
    for key in ("id", "next_fire_at", "fire"):
        assert f"<b>{key}</b>" in html
    for key in _DROPPED_RUNTIME_KEYS:
        assert f"<b>{key}</b>" not in html
