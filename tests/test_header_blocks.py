"""Der Header: zwei Blöcke nach Herkunft (bibi5, FE-Spezifikation §2).

Links steht, was dieser Knoten selbst weiß; rechts, was der Scheduler sagt.
Die Trennung ist nicht kosmetisch, sondern folgt dem Ausfall: fällt der Host
weg, verlieren genau die rechten Werte gleichzeitig ihre Gültigkeit — und nur
sie. Vier Status-Kacheln nebeneinander konnten das nicht zeigen, weil sie
Herkunft und Aktualität vermischten.
"""

from __future__ import annotations

import pytest

from bibi.controller import render

NOW = 1_000_000.0

CLIENT_STATUS = {
    "roles": ["synchronizer", "controller", "connect"],
    "hostname": "Mac.fritz.box",
    "auto_sync": False,
    "maintenance": False,
    "started_at": NOW - 48_300,
    "engine": {"running": "v0.6.0", "expected": "v0.6.0", "needs_update": False},
    "connect": {"ok": True, "last_at": NOW - 21},
}
GIT = {"branch": "trunk", "tree": "modified", "sync": "synced", "commit": "4715f43"}
SCHEDULER_STATUS = {
    "hostname": "sarasate.tail9f9173.ts.net",
    "started_at": NOW - 48_300,
    "workers": [{"worker": "sarasate-client"}, {"worker": "mac"}],
    "job_stats": {"counts": {"complete": 52, "killed": 2, "error": 1, "pending": 3}},
    "next_fire_at": NOW + 120,
}


def _header(**kw) -> str:
    # `scheduler_host` kommt immer mit: er stammt aus der Konfiguration dieses
    # Knotens (`config.scheduler_base_url()`), nicht aus der Antwort des Hosts.
    # Genau deshalb steht er auch dann da, wenn nichts mehr antwortet.
    kw.setdefault("scheduler_host", "sarasate.tail9f9173.ts.net")
    return render.status_header(
        CLIENT_STATUS, GIT, scheduler=kw.pop("scheduler", SCHEDULER_STATUS),
        now=kw.pop("now", NOW), **kw)


# ── die beiden Blöcke ───────────────────────────────────────────────────────


def test_two_blocks_named_by_origin():
    """`CLIENT` und `SCHEDULER` — die Titel sagen, woher die Zahlen kommen."""
    html = _header()
    assert "CLIENT" in html and "SCHEDULER" in html


def test_client_block_carries_its_own_hostname():
    html = _header()
    assert "Mac.fritz.box" in html


def test_scheduler_hostname_comes_from_the_client():
    """Er steht auch bei Ausfall da — er ist der Anker, an dem der leere Block
    hängt. Deshalb stammt er aus der Konfiguration dieses Knotens, nicht aus
    der Antwort des Hosts."""
    html = _header(scheduler=None)
    assert "sarasate.tail9f9173.ts.net" in html


def test_client_block_has_its_four_rows():
    html = _header()
    for label in ("heartbeat", "project", "bibi"):
        assert label in html, label
    assert "auto-sync: off" in html
    assert "trunk" in html and "modified" in html
    assert "v0.6.0" in html


def test_scheduler_block_has_its_four_rows():
    html = _header()
    for label in ("clients", "next job", "uptime"):
        assert label in html, label
    assert "2 connected" in html


# ── Offline: dimmen, nicht leeren ───────────────────────────────────────────


def test_offline_keeps_the_last_values_and_dates_them():
    """Kein achtfaches `offline`. Der Block behält seine Werte, wird gedimmt
    und trägt in der Titelzeile das Alter des Standes — sonst weiß niemand, ob
    „2 connected" von vor einer Minute oder von gestern stammt."""
    html = _header(scheduler=SCHEDULER_STATUS, scheduler_stale_since=NOW - 240)
    assert "no contact for" in html
    assert "2 connected" in html, "die letzten Werte bleiben stehen"
    assert "dimmed" in html or "stale" in html


def test_offline_marks_the_scheduler_hostname_red():
    """Rot trägt genau eine Bedeutung: nicht erreichbar oder nicht in Ordnung.
    Dass der Hostname vom Client stammt und deshalb stehenbleibt, ist kein
    Widerspruch — er bleibt sichtbar **und** wird rot, weil er den Ausfall
    trägt."""
    html = _header(scheduler=SCHEDULER_STATUS, scheduler_stale_since=NOW - 240)
    rot = [z for z in html.splitlines() if "sarasate" in z]
    assert rot and any("bad" in z or "red" in z or "danger" in z for z in rot), \
        "der Scheduler-Hostname muss bei Ausfall rot sein"


def test_auto_sync_off_is_not_an_error():
    """Eine Einstellung, kein Fehler — sie bleibt neutral."""
    html = _header()
    zeile = [z for z in html.splitlines() if "auto-sync" in z]
    assert zeile and not any("bad" in z or "danger" in z for z in zeile)


# ── was der Header nicht mehr trägt ─────────────────────────────────────────


def test_no_job_matrix_and_no_status_cards():
    """Die 3×3-Job-Matrix verlässt den Header; ihre Aussage steht jetzt in der
    `next job`-Zeile als drei Zahlen und ausführlich im Jobs-Screen."""
    html = _header()
    assert "statuscards" not in html
    assert "jobmatrix" not in html and "job-matrix" not in html
