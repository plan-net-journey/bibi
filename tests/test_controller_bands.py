"""Stufe 14.4 (PLAN-14) — Root-Bänder als Drei-Gruppen-Modell (Feedback 2026-07-01).

Trennlinie ist nicht mehr Laufzeit-Historie, sondern „braucht es jetzt eine
Handlung von mir?":
- **will run** = pending/failed/deferred + complete MIT Schedule — läuft von
  selbst weiter, keine Handlung nötig.
- **requires action** = error/awaiting/inactive/zombie/killed — OHNE running,
  ein laufender Job braucht keine Handlung, er tut gerade genau das, was er soll.
- **journal** = alle running (sortiert nach Startzeit) + eindeutige (unique)
  Journal-Einträge, die nicht schon in den ersten zwei Gruppen stecken.
  complete MIT `at:` landet hier automatisch residual (kein Sonderfall nötig).

Überschriften statt Buttons, scrollbare max-height-Area statt Collapse/Expand
(Stufe-6-Revision aus Frontend-Plan.md, User-bestätigt)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


def _job(slug: str, status: str, *, jid=None, started_at=900.0,
         next_fire_at=None, reason=None, schedule=None) -> dict:
    return {"id": jid or slug, "slug": slug, "kind": "job", "status": status,
            "reason": reason, "started_at": started_at, "finished_at": None,
            "next_fire_at": next_fire_at, "exit_code": None, "host": "h",
            "worker": "w", "output_ref": None, "priority": 0, "enqueued_at": 0,
            "attempt": 0, "schedule": schedule}


def _run(slug: str, status: str = "complete", *, jid=None, started_at=500.0,
         finished_at=600.0) -> dict:
    return {"id": jid or 1, "run_id": f"{slug}:1", "slug": slug, "kind": "job",
            "status": status, "reason": None, "started_at": started_at,
            "finished_at": finished_at, "exit_code": 0, "exec_runtime": 100.0,
            "host": "h", "worker": "w", "output_ref": None, "commit_sha": None,
            "branch": None, "domain": "scheduled", "payload": None}


# ── pure Renderer ─────────────────────────────────────────────────────────────


def test_will_run_includes_pending_failed_deferred_and_scheduled_complete():
    jobs = [_job("p", "pending"), _job("f", "failed"), _job("d", "deferred"),
            _job("c", "complete", schedule="0 9 * * *")]
    html = render.bands_fragment(jobs, [], now=1000.0)
    for s in ("p", "f", "d", "c"):
        assert s in html


def test_will_run_excludes_oneshot_complete():
    # complete OHNE schedule (at:) hat keine Zukunft im Sinne von "will run" —
    # landet stattdessen residual im journal-Band.
    html = render.bands_fragment([_job("c", "complete", schedule=None)], [], now=1.0)
    assert "Will Run" in html
    wr_section = html.split("Requires Action")[0]
    assert "c" not in wr_section


def test_requires_action_excludes_running():
    html = render.bands_fragment([_job("r", "running")], [], now=1.0)
    section = html.split("Requires Action")[1].split("Will Run")[0]
    assert 'schedule/r"' not in section


def test_requires_action_includes_error_awaiting_inactive_zombie_killed():
    jobs = [_job("e", "error"), _job("aw", "awaiting"), _job("ia", "inactive"),
            _job("zo", "zombie"), _job("ki", "killed")]
    html = render.bands_fragment(jobs, [], now=1.0)
    section = html.split("Requires Action")[1].split("Will Run")[0]
    for s in ("e", "aw", "ia", "zo", "ki"):
        assert s in section


def test_journal_group_includes_running_and_unique_journal_entries():
    jobs = [_job("r", "running", started_at=100.0)]
    journal = [_run("old", started_at=50.0, finished_at=60.0)]
    html = render.bands_fragment(jobs, journal, now=1000.0)
    section = html.split("Journal")[1]
    assert "r" in section and "old" in section


def test_journal_group_excludes_slugs_already_in_will_run_or_requires_action():
    jobs = [_job("p", "pending")]
    journal = [_run("p", finished_at=10.0)]  # gleicher Slug wie im will-run-Band
    html = render.bands_fragment(jobs, journal, now=1000.0)
    section = html.split("Journal")[1]
    assert 'schedule/p"' not in section


def test_journal_group_oneshot_complete_lands_here_residually():
    html = render.bands_fragment([_job("c", "complete", schedule=None)], [], now=1.0)
    section = html.split("Journal")[1]
    assert "c" in section


def test_counts_in_headings():
    jobs = [_job("a", "running"), _job("b", "failed"), _job("c", "pending")]
    html = render.bands_fragment(jobs, [], now=1.0)
    assert "Requires Action (0)" in html
    assert "Will Run (2)" in html
    assert "Journal (1)" in html  # a (running)


def test_empty_placeholders():
    html = render.bands_fragment([], [], now=1.0)
    assert "Requires Action (0)" in html and "Will Run (0)" in html
    assert "Journal (0)" in html


def test_escapes_slug():
    html = render.bands_fragment([_job("<x>", "running")], [], now=1.0)
    assert "<x>" not in html.replace("/-/ui/schedule/", "")
    assert "&lt;x&gt;" in html


def test_no_collapse_buttons_or_localstorage():
    html = render.bands_fragment([_job("a", "running")], [], now=1.0)
    assert "bibiToggleBand" not in html and "bandtog" not in html
    assert "bandscroll" in html


def test_every_job_appears_in_exactly_one_group():
    jobs = [_job("p", "pending"), _job("r", "running"), _job("e", "error")]
    html = render.bands_fragment(jobs, [], now=1.0)
    for s in ("p", "r", "e"):
        assert html.count(f'schedule/{s}"') == 1


def test_feed_page_embeds_bands():
    html = render.feed_page([], jobs=[_job("a", "running")], now=1.0)
    assert 'id="bands"' in html
    assert "Journal (1)" in html  # running landet im journal-Band


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
        assert "Will Run (1)" in r.text


def test_ui_feed_includes_bands(team_repo: Path):
    client = FakeClient([_job("a", "running")], journal=[])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/feed")
        assert 'id="bands"' in r.text and "Journal (1)" in r.text
