"""Stufe 4.0 — Engine-Vorarbeit für die Controller-App (PLAN-4 §2.3/§4.0):

- Journal **v6**: Commit-SHA + Branch je Lauf persistieren (T2-Lücke geschlossen).
- ``DELETE /-/journal/{id}``: einen Lauf-Record löschen (A15: nur DB, kein MD-CRUD).
- ``/-/status``-**Verdikt**: server-seitig „läuft alles?" (Probleme + überfällig).

Test-driven: ``pytest -k "journal_commit or journal_delete or status_verdict"``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.daemon import job_db, roles
from bibi.daemon.app import create_app


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


@pytest.fixture
def sched(team_repo: Path):
    app = create_app(roles.resolve({"scheduler"}))
    with TestClient(app) as client:
        yield client, team_repo


def _seed(repo_root: Path, rel: str, text: str) -> None:
    p = repo_root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ── Journal v6: Commit-SHA + Branch ──────────────────────────────────────────


def test_journal_commit_schema_is_v6(conn):
    assert job_db.SCHEMA_VERSION >= 6
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(journal)")}
    assert {"commit_sha", "branch"} <= cols


def test_journal_commit_migration_v5_to_v6(tmp_path: Path):
    # Eine v5-DB (journal ohne commit_sha/branch) → reconnect migriert additiv.
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript(
        "CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, "
        "slug TEXT, kind TEXT, status TEXT, archived_at REAL NOT NULL DEFAULT 0, "
        "domain TEXT NOT NULL DEFAULT 'scheduled');"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "PRAGMA user_version = 5;"
    )
    c.commit()
    c.close()
    c2 = job_db.connect(p)
    try:
        cols = {r["name"] for r in c2.execute("PRAGMA table_info(journal)")}
        assert {"commit_sha", "branch"} <= cols
        assert c2.execute("PRAGMA user_version").fetchone()[0] == job_db.SCHEMA_VERSION
    finally:
        c2.close()


def test_journal_commit_persisted_via_report(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    r = client.post(
        f"/-/scheduler/status/{jid}",
        json={"status": "complete", "exit_code": 0,
              "commit_sha": "abc1234", "branch": "agent/a"},
    )
    assert r.status_code == 200
    entry = client.get("/-/journal").json()[0]
    assert entry["commit_sha"] == "abc1234"
    assert entry["branch"] == "agent/a"


def test_journal_commit_absent_is_null(sched):
    # Ein vom Sweeper/ohne Worker-Commit erzeugter Terminal hat keinen SHA.
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    entry = client.get("/-/journal").json()[0]
    assert entry["commit_sha"] is None
    assert entry["branch"] is None


# ── DELETE /-/journal/{id} (A15: nur Lauf-Records, kein MD-CRUD) ──────────────


def test_journal_delete_removes_row(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    entries = client.get("/-/journal").json()
    assert len(entries) == 1
    row_id = entries[0]["id"]
    r = client.delete(f"/-/journal/{row_id}")
    assert r.status_code == 200
    assert client.get("/-/journal").json() == []


def test_journal_delete_unknown_is_404(sched):
    client, _ = sched
    assert client.delete("/-/journal/999999").status_code == 404


# ── /-/status-Verdikt (server-seitig, DB-nah, wiederverwendbar) ──────────────


def test_status_verdict_clean_is_ok(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: never\njob: "x"\n---\n')
    client.post("/-/rescan")
    v = client.get("/-/status").json()["verdict"]
    assert v["ok"] is True
    assert v["problems"] == 0
    assert v["overdue"] == 0
    assert v["deviations"] == []


def test_status_verdict_counts_problems(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    client.post(f"/-/scheduler/status/{jid}", json={"status": "failed", "exit_code": 1})
    v = client.get("/-/status").json()["verdict"]
    assert v["ok"] is False
    assert v["problems"] == 1
    assert v["deviations"][0]["slug"] == "a"
    assert v["deviations"][0]["status"] == "failed"


def test_status_verdict_counts_overdue(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: "0 9 * * *"\njob: "x"\n---\n')
    client.post("/-/rescan")
    # next_fire_at künstlich in die Vergangenheit ziehen → überfällig.
    c = job_db.connect()
    try:
        c.execute("UPDATE jobs SET next_fire_at=1.0 WHERE status='pending'")
        c.commit()
    finally:
        c.close()
    v = client.get("/-/status").json()["verdict"]
    assert v["ok"] is False
    assert v["overdue"] == 1


def test_status_verdict_absent_without_scheduler_role(team_repo):
    # /-/status bleibt rollenabhängig: ohne Scheduler keine DB-nahe Verdikt-Sicht.
    app = create_app(roles.resolve({"sync"}))
    with TestClient(app) as client:
        assert "verdict" not in client.get("/-/status").json()
