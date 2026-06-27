"""Stufe 1 — Feed-Renderer + Screen (Frontend-Plan §C.1).

Pure ``feed_row``/``feed_list``/``feed_page`` (Daten-dict → HTML, unit-testbar) +
die Routen ``/-/ui/feed`` (Seite, Server-Backfill) und ``/-/ui/feed/list`` (Fragment).
Live hängt der Screen am ``/-/feed/stream``-SSE (Stufe 0); neueste UNTEN (Tail)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


def _row(run_id: str, *, status="complete", slug=None, jid=1, finished_at=1000.0,
         exit_code=0, commit_sha=None) -> dict:
    slug = slug or run_id.split(":")[0]
    return {"id": jid, "run_id": run_id, "slug": slug, "kind": "job",
            "status": status, "reason": None, "started_at": finished_at - 5,
            "finished_at": finished_at, "exit_code": exit_code,
            "commit_sha": commit_sha, "branch": None, "host": "h", "worker": "w",
            "output_ref": f"data/job/{run_id}/output.jsonl"}


# ── pure Renderer ─────────────────────────────────────────────────────────────


def test_feed_list_newest_at_bottom():
    # Journal liefert neueste zuerst (archived_at DESC); der Feed rendert neueste UNTEN.
    rows = [_row("C:3", jid=3, finished_at=3000.0),
            _row("B:2", jid=2, finished_at=2000.0),
            _row("A:1", jid=1, finished_at=1000.0)]
    html = render.feed_list(rows)
    assert 'id="feed"' in html
    assert html.index("A:1") < html.index("B:2") < html.index("C:3")


def test_feed_row_links_slug_and_run():
    html = render.feed_row(_row("Witz:54", jid=42, slug="Witz"))
    assert 'href="/-/ui/schedule/Witz"' in html       # slug → Schedule-Detail
    assert 'href="/-/ui/run/42"' in html              # run_id → Execution-Detail (Stufe 4)
    assert "Witz:54" in html


def test_feed_row_status_class_prominent():
    assert 'class="st complete"' in render.feed_row(_row("X:1", status="complete"))
    assert 'class="st error"' in render.feed_row(_row("Y:1", status="error", exit_code=1))


def test_feed_row_commit_and_exit():
    html = render.feed_row(_row("X:1", exit_code=0, commit_sha="094df71abcdef"))
    assert "094df71" in html and "exit 0" in html
    bare = render.feed_row(_row("Y:1", exit_code=None, commit_sha=None))
    assert "⎘" not in bare and "exit" not in bare


def test_feed_row_escapes_slug():
    html = render.feed_row(_row("a:1", slug="<script>x", jid=1))
    assert "<script>x" not in html.replace("/-/ui/schedule/", "")
    assert "&lt;script&gt;x" in html


def test_feed_page_embeds_backfill_and_sse():
    html = render.feed_page([_row("A:1")])
    assert html.lower().startswith("<!doctype html>")
    assert 'id="feed"' in html and "A:1" in html      # Server-Render-Backfill (kein nur-JS)
    assert "/-/feed/stream" in html and "EventSource" in html
    assert "/-/ui/logs" in html and "/-/docs" in html  # Nav


# ── Routen ────────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, journal: list[dict]) -> None:
        self._journal = journal

    def journal(self, *, slug=None, host=None) -> list[dict]:
        return self._journal


def test_ui_feed_route(team_repo: Path):
    client = FakeClient([_row("Witz:54", jid=42)])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/feed")
        assert r.status_code == 200
        assert 'id="feed"' in r.text and "Witz:54" in r.text
        assert "/-/feed/stream" in r.text


def test_ui_feed_list_fragment(team_repo: Path):
    client = FakeClient([_row("B:2", jid=2, finished_at=2000.0),
                         _row("A:1", jid=1, finished_at=1000.0)])
    app = create_app(roles.resolve({"controller"}), controller_client=client)
    with TestClient(app) as c:
        r = c.get("/-/ui/feed/list")
        assert r.status_code == 200
        assert 'id="feed"' in r.text
        assert r.text.index("A:1") < r.text.index("B:2")  # neueste unten
