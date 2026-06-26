"""``bibi-ctrl job`` CLI — Routing + Ausgabe (PLAN-3 §3.1).

HTTP wird gemockt (``_req``): die CLI ist ein dünner Client; getestet werden
Verdrahtung, Exit-Codes und Formatierung, nicht der Daemon (der hat eigene Tests).
"""

from __future__ import annotations

import pytest

from bibi.ctrl import job_cmd, main


def _patch(monkeypatch, code, body):
    monkeypatch.setattr(job_cmd, "_req", lambda url, method="GET": (code, body))


def test_list_formats_rows(monkeypatch, capsys):
    _patch(monkeypatch, 200, [
        {"slug": "hello", "status": "pending", "kind": "job", "id": "ab12cd34", "reason": None},
    ])
    assert main(["job", "list"]) == 0
    out = capsys.readouterr().out
    assert "hello" in out and "pending" in out and "(job)" in out and "ab12cd34" in out


def test_list_empty(monkeypatch, capsys):
    _patch(monkeypatch, 200, [])
    assert main(["job", "list"]) == 0
    assert "(keine Jobs)" in capsys.readouterr().out


def test_list_501_when_no_scheduler(monkeypatch, capsys):
    _patch(monkeypatch, 501, {"error": "not implemented"})
    assert main(["job", "list"]) == 1
    assert "Scheduler-Rolle nicht aktiv" in capsys.readouterr().err


def test_show_404(monkeypatch, capsys):
    _patch(monkeypatch, 404, {"error": "job not found"})
    assert main(["job", "show", "nope"]) == 1
    assert "kein Job mit id nope" in capsys.readouterr().err


def test_show_prints_json(monkeypatch, capsys):
    _patch(monkeypatch, 200, {"id": "x", "slug": "hello", "status": "pending"})
    assert main(["job", "show", "x"]) == 0
    assert '"slug": "hello"' in capsys.readouterr().out


def test_rescan_counts(monkeypatch, capsys):
    _patch(monkeypatch, 200, {"inserted": 2, "updated": 1, "removed": 0,
                              "errors": [], "collisions": []})
    assert main(["job", "rescan"]) == 0
    assert "inserted=2 updated=1 removed=0" in capsys.readouterr().out


def test_rescan_reports_collisions(monkeypatch, capsys):
    _patch(monkeypatch, 200, {"inserted": 0, "updated": 0, "removed": 0, "errors": [],
                              "collisions": [{"slug": "dup", "schedule_refs": ["a.md", "b.md"]}]})
    assert main(["job", "rescan"]) == 0
    assert "collision: slug 'dup'" in capsys.readouterr().err


def test_job_no_subcommand_prints_help(capsys):
    assert main(["job"]) == 1
