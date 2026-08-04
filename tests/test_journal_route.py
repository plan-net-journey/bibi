"""GET /-/journal ohne scheduler-Rolle (m.rau/bibi#103, bibi5 Aenderung 4).

Jeder Knoten fuehrt sein eigenes, vollstaendiges Journal — Scheduler und
Client sind darin gleichwertig und unabhaengig. Die Route war bis bibi5
scheduler-gated und antwortete auf einem reinen Client mit 501; damit haette
das Job-Detail keine LOCAL-Gruppe.

Zwei Felder kommen mit: ``job_uid`` traegt den Join der kombinierten Lauf-
Liste (bislang riet ``list_journal`` ueber ``slug LIKE 'x-________'``), und
``archived_at`` unterscheidet sich unter der Archivierungsregel A2 beliebig
weit von ``finished_at`` — ein Lauf kann Tage im blockierten Slot stehen,
bevor ihn ein START ins Journal schreibt.

Kein Subprozess: direkter job_db-Seed wie in test_run_journal_detail.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app
from bibi.schedule import models


@pytest.fixture
def client_only(team_repo: Path):
    # Bewusst OHNE scheduler-Rolle — genau die Rollenmischung eines reinen
    # Clients (live: synchronizer, controller, connect).
    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        yield c


def _seed(slug: str, *, domain: str = "scheduled", pinned_host: str | None = None,
          job_uid: str | None = None, finished_at: float = 2.0,
          archived_at: float = 2.0, status: str = "complete") -> int:
    conn = job_db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO journal (run_id, slug, job_uid, kind, status, started_at, "
            "finished_at, archived_at, domain, pinned_host) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"{slug}:1:abc", slug, job_uid or models.job_uid(slug), "job",
             status, 1.0, finished_at, archived_at, domain, pinned_host),
        )
        return cur.lastrowid
    finally:
        conn.close()


def test_journal_list_without_scheduler_role(client_only):
    """Der Kern von #103: 200 statt 501, mit den eigenen Zeilen."""
    _seed("alpha")
    r = client_only.get("/-/journal")
    assert r.status_code == 200
    assert [e["slug"] for e in r.json()] == ["alpha"]


def test_journal_list_includes_team_queue_runs(client_only):
    """Anders als /-/run/journal filtert /-/journal nichts weg.

    Die Sonderroute zeigt nur ``domain='local' OR pinned_host IS NOT NULL``
    ("meine eigene /run-Historie"). Auf dem Mac-Client faellt darunter der
    gesamte Altbestand bis 04.07.2026 — 1915 von 2208 Zeilen, die dann in
    keiner Gruppe des Job-Details mehr auftauchten.
    """
    _seed("team-run", domain="scheduled", pinned_host=None)
    slugs = [e["slug"] for e in client_only.get("/-/journal").json()]
    assert "team-run" in slugs


def test_journal_entry_carries_job_uid(client_only):
    """Ohne job_uid in der Liste kann das Backend die kombinierte Lauf-Liste
    nicht joinen (FE-Spezifikation §8) und faellt auf das Suffix-Muster
    zurueck, das #102 ablesen sollte."""
    _seed("beta")
    entry = client_only.get("/-/journal").json()[0]
    assert entry["job_uid"] == models.job_uid("beta")


def test_journal_entry_carries_archived_at(client_only):
    """Unter A2 laufen finished_at und archived_at auseinander — beide muessen
    sichtbar sein, sonst ist nicht unterscheidbar, wann ein Lauf lief und wann
    ihn jemand abgeraeumt hat."""
    _seed("gamma", finished_at=100.0, archived_at=900.0)
    entry = client_only.get("/-/journal").json()[0]
    assert entry["finished_at"] == 100.0
    assert entry["archived_at"] == 900.0


def test_journal_list_filters_by_domain(client_only):
    """?domain=local leistet, was heute die Sonderroute /-/run/journal tut."""
    _seed("local-run", domain="local")
    _seed("sched-run", domain="scheduled")
    slugs = [e["slug"] for e in client_only.get("/-/journal?domain=local").json()]
    assert slugs == ["local-run"]


def test_journal_list_pages(client_only):
    """Paging ist Pflicht (FE §8): gmail-transfer allein hat 1064 Laeufe."""
    for i in range(5):
        _seed(f"p{i}", archived_at=float(i))
    first = client_only.get("/-/journal?limit=2").json()
    second = client_only.get("/-/journal?limit=2&offset=2").json()
    assert len(first) == 2 and len(second) == 2
    assert {e["slug"] for e in first}.isdisjoint({e["slug"] for e in second})
