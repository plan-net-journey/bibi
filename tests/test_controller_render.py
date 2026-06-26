"""Stufe 4.1 — Verdikt + Abweichungs-Seite (PLAN-4 §4.1, Ebene 0+1).

Die Render-Funktionen sind **pur** (Daten-dict → HTML) und damit voll unit-testbar,
unabhängig von HTTP/DB. Der Controller holt die Daten via HTTP aus ``/-/status``.
"""

from __future__ import annotations

from bibi.controller import render


def _status(*, ok, problems=0, overdue=0, deviations=None, overdue_jobs=None) -> dict:
    return {"roles": ["scheduler", "controller"], "verdict": {
        "ok": ok, "problems": problems, "overdue": overdue,
        "deviations": deviations or [], "overdue_jobs": overdue_jobs or [],
    }}


def test_verdict_fragment_green():
    html = render.verdict_fragment(_status(ok=True))
    assert "alles lief" in html
    assert 'class="banner ok"' in html
    assert "Problem" not in html  # keine Abweichungs-Sprache im grünen Zustand


def test_verdict_fragment_singular_problem():
    dev = [{"slug": "demo", "status": "failed", "reason": None, "host": "n1",
            "finished_at": 1000.0, "exit_code": 1}]
    html = render.verdict_fragment(
        _status(ok=False, problems=1, deviations=dev), now=1180.0)
    assert "banner bad" in html
    assert "1 Problem" in html and "1 Probleme" not in html  # Singular
    assert "demo" in html
    assert "failed" in html


def test_verdict_fragment_plural_and_overdue():
    dev = [{"slug": "a", "status": "failed", "reason": None, "host": "n1"},
           {"slug": "b", "status": "killed", "reason": "by_user", "host": "n1"}]
    od = [{"slug": "c", "status": "pending", "host": "n2", "next_fire_at": 10.0}]
    html = render.verdict_fragment(
        _status(ok=False, problems=2, overdue=1, deviations=dev, overdue_jobs=od),
        now=1000.0)
    assert "2 Probleme" in html
    assert "1 überfällig" in html
    assert "by_user" in html
    assert "c" in html  # überfälliger Schedule gelistet


def test_verdict_fragment_escapes_slug():
    dev = [{"slug": "<script>x", "status": "failed", "reason": None, "host": "n1"}]
    html = render.verdict_fragment(_status(ok=False, problems=1, deviations=dev))
    assert "<script>x" not in html
    assert "&lt;script&gt;x" in html


def test_verdict_fragment_polls():
    # htmx-Selbstaktualisierung (PLAN-4 §4.1).
    html = render.verdict_fragment(_status(ok=True))
    assert 'hx-get="/-/ui/verdict"' in html
    assert "every" in html


def test_verdict_fragment_no_verdict():
    html = render.verdict_fragment({"roles": ["controller"]})
    assert "Scheduler" in html  # kein Verdikt → Hinweis statt Absturz


def test_dashboard_page_embeds_fragment():
    html = render.dashboard_page(_status(ok=True))
    assert html.lower().startswith("<!doctype html>")
    assert "htmx" in html
    assert "alles lief" in html  # initiales Server-Render, nicht erst per JS
    assert 'id="verdict"' in html
