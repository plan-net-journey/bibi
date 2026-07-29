"""``bibi-ctrl job`` CLI — Routing + Ausgabe (PLAN-3 §3.1).

HTTP wird gemockt (``_req``): die CLI ist ein dünner Client; getestet werden
Verdrahtung, Exit-Codes und Formatierung, nicht der Daemon (der hat eigene Tests).
"""

from __future__ import annotations

import argparse

import pytest

from bibi.ctrl import job_cmd, main


def _patch(monkeypatch, code, body):
    monkeypatch.setattr(job_cmd, "_req", lambda url, method="GET": (code, body))


# ── _base() (PLAN-13 Stufe 13.0) ─────────────────────────────────────────────


def test_base_uses_scheduler_base_url_without_port_override(monkeypatch):
    monkeypatch.setattr(job_cmd.config, "scheduler_base_url",
                        lambda: "http://sarasate.tail9f9173.ts.net:8780")
    assert job_cmd._base(argparse.Namespace(port=0)) == "http://sarasate.tail9f9173.ts.net:8780"


def test_base_explicit_port_stays_local(monkeypatch):
    # --port bleibt ein reiner Lokalitäts-Override, unabhängig davon, wohin
    # BIBI_SCHEDULER_URL zeigt.
    monkeypatch.setattr(job_cmd.config, "scheduler_base_url",
                        lambda: "http://sarasate.tail9f9173.ts.net:8780")
    assert job_cmd._base(argparse.Namespace(port=9000)) == "http://127.0.0.1:9000"


def test_list_targets_remote_scheduler_url(monkeypatch, capsys):
    # End-to-end: main(["job", "list"]) baut die URL tatsächlich aus
    # scheduler_base_url(), nicht mehr blind aus 127.0.0.1.
    monkeypatch.setattr(job_cmd.config, "scheduler_base_url",
                        lambda: "http://sarasate.tail9f9173.ts.net:8780")
    seen_urls = []

    def fake_req(url, method="GET"):
        seen_urls.append(url)
        return 200, []
    monkeypatch.setattr(job_cmd, "_req", fake_req)
    assert main(["job", "list"]) == 0
    assert seen_urls == ["http://sarasate.tail9f9173.ts.net:8780/-/job"]


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
