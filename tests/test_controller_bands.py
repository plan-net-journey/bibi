"""Stufe 2 — Bänder „aktiv"/„wartet" + Headline-Zähler + Klapp-Toggle (§C.2).

Quelle = ``jobs``-Tabelle (Live-State). Achse #2: **nicht im Journal** → Band.
- aktiv = alles außer ``pending`` und ``complete``: running/awaiting/failed/
  deferred ("in Bewegung") UND error/inactive/zombie/killed ("Problem, braucht
  Aufmerksamkeit") — sonst verschwindet ein gekillter one-shot/app-Job (kein
  Retry, kein nächster Termin) spurlos aus jeder Übersicht (nur ``complete``
  ist „erledigt, nichts zu tun").
- wartet = pending
Bänder in der Kopfzeile gezählt, per Klick auf-/zuklappbar (localStorage)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


def _job(slug: str, status: str, *, jid=None, started_at=900.0,
         next_fire_at=None, reason=None) -> dict:
    return {"id": jid or slug, "slug": slug, "kind": "job", "status": status,
            "reason": reason, "started_at": started_at, "finished_at": None,
            "next_fire_at": next_fire_at, "exit_code": None, "host": "h",
            "worker": "w", "output_ref": None, "priority": 0, "enqueued_at": 0,
            "attempt": 0}


# ── pure Renderer ─────────────────────────────────────────────────────────────


def test_bands_membership_only_pending_and_complete_excluded():
    jobs = [_job("run1", "running"), _job("retry1", "failed", next_fire_at=2000.0),
            _job("def1", "deferred"), _job("wait1", "pending", next_fire_at=3000.0),
            _job("done1", "complete"), _job("dead1", "killed"),
            _job("await1", "awaiting"), _job("err1", "error"),
            _job("zomb1", "zombie"), _job("inact1", "inactive")]
    html = render.bands_fragment(jobs, now=1000.0)
    for s in ("run1", "retry1", "def1", "wait1", "dead1", "await1", "err1", "zomb1", "inact1"):
        assert s in html
    assert "done1" not in html  # complete = erledigt, einzig kein Handlungsbedarf


def test_bands_problem_states_in_aktiv_not_just_wartet():
    # Ein gekillter one-shot-Job (schedule: never) hat kein next_fire_at und
    # damit auch keinen Platz im "wartet"-Band — er muss in "aktiv" auftauchen,
    # sonst ist er nach einem Kill aus beiden Bändern verschwunden.
    html = render.bands_fragment([_job("hitl", "killed", next_fire_at=None)], now=1.0)
    assert "hitl" in html and "1 aktiv" in html


def test_bands_counts_in_headline():
    jobs = [_job("a", "running"), _job("b", "failed"), _job("c", "pending")]
    html = render.bands_fragment(jobs, now=1.0)
    assert "2 aktiv" in html and "1 wartet" in html


def test_bands_collapse_markup():
    html = render.bands_fragment([_job("a", "running")], now=1.0)
    assert 'id="bands"' in html
    assert 'data-band="aktiv"' in html and 'data-band="wartet"' in html
    assert 'class="bandtog"' in html and "bibiToggleBand" in html


def test_bands_empty_placeholders():
    html = render.bands_fragment([], now=1.0)
    assert "0 aktiv" in html and "0 wartet" in html
    assert "nichts aktiv" in html


def test_bands_escapes_slug():
    html = render.bands_fragment([_job("<x>", "running")], now=1.0)
    assert "<x>" not in html.replace("/-/ui/schedule/", "")
    assert "&lt;x&gt;" in html


def test_feed_page_embeds_bands():
    html = render.feed_page([], jobs=[_job("a", "running")], now=1.0)
    assert 'id="bands"' in html and "1 aktiv" in html
    assert "bibiToggleBand" in html  # _BANDS_JS verdrahtet


# ── Routen ────────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, jobs: list[dict], journal: list[dict] | None = None) -> None:
        self._jobs = jobs
        self._journal = journal or []

    def jobs(self, *, status=None) -> list[dict]:
        return self._jobs

    def journal(self, *, slug=None, host=None) -> list[dict]:
        return self._journal


def test_ui_feed_bands_route(team_repo: Path):
    client = FakeClient([_job("a", "running"), _job("b", "pending")])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/feed/bands")
        assert r.status_code == 200 and 'id="bands"' in r.text
        assert "1 aktiv" in r.text and "1 wartet" in r.text


def test_ui_feed_includes_bands(team_repo: Path):
    client = FakeClient([_job("a", "running")], journal=[])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/feed")
        assert 'id="bands"' in r.text and "1 aktiv" in r.text
