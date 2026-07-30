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


def test_status_verdict_flags_failing_recurring_job(sched):
    # Wiederkehrender Job re-armt nach Fehlschlag zu `pending` (Zeile harmlos), aber
    # der LETZTE LAUF im Journal ist `error` → Verdikt darf NICHT „alles lief" sein.
    client, root = sched
    _seed(root, "witzy/README.md", '---\nschedule: "*/3 * * * *"\njob: "claude: x"\n---\n')
    client.post("/-/rescan")  # jobs-Zeile pending
    c = job_db.connect()
    try:
        c.execute(
            "INSERT INTO journal (run_id, slug, kind, status, finished_at, exit_code, "
            "archived_at, domain) VALUES ('witzy:0','witzy','job','error',100.0,1,100.0,'scheduled')"
        )
        c.commit()
    finally:
        c.close()
    v = client.get("/-/status").json()["verdict"]
    assert v["ok"] is False
    devs = [d for d in v["deviations"] if d["slug"] == "witzy"]
    assert devs and devs[0]["status"] == "error" and devs[0].get("last_run") is True


def test_status_verdict_running_not_flagged_by_old_failure(sched):
    # Läuft der Job gerade (running), zählt ein alter Fehllauf NICHT als Abweichung.
    client, root = sched
    _seed(root, "busy/README.md", '---\nschedule: now\njob: "claude: x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]  # sofort fällig → running
    assert jid
    c = job_db.connect()
    try:
        c.execute(
            "INSERT INTO journal (run_id, slug, kind, status, finished_at, exit_code, "
            "archived_at, domain) VALUES ('busy:0','busy','job','error',100.0,1,100.0,'scheduled')"
        )
        c.commit()
    finally:
        c.close()
    v = client.get("/-/status").json()["verdict"]
    assert not [d for d in v["deviations"] if d["slug"] == "busy"]


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


# ── /-/status-job_stats + /-/landings (PLAN-21 Befund 11 v2 Chart) ───────────


def test_status_job_stats_counts_and_running_since_uptime(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    client.post("/-/scheduler/next")  # a → starting (#38: running erst nach dem Spawn)
    stats = client.get("/-/status").json()["job_stats"]
    assert stats["counts"] == {"starting": 1}
    assert stats["running_since_uptime"] == 1


def test_status_job_stats_absent_without_scheduler_role(team_repo):
    app = create_app(roles.resolve({"sync"}))
    with TestClient(app) as client:
        assert "job_stats" not in client.get("/-/status").json()


def test_status_job_stats_includes_next_due_at(sched):
    # PLAN-26 Befund 3 Redesign — Sub-Zeile "Nächster Job in …" der Job-
    # Status-Kachel.
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    stats = client.get("/-/status").json()["job_stats"]
    assert stats["next_due_at"] is not None


def test_status_job_stats_includes_complete_since_uptime(sched):
    # PLAN-26 Befund 3 — Job-Status-Kachel: complete_since_uptime ist ein
    # kumulativer Prozesslaufzeit-Zähler wie running_since_uptime, nicht die
    # Live-Zählung aus counts (die sinkt, sobald abgeschlossene Jobs archiviert
    # werden).
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    stats = client.get("/-/status").json()["job_stats"]
    assert stats["complete_since_uptime"] == 1


def test_landings_route_returns_terminal_journal_entries(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    rows = client.get("/-/landings").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "complete"


def test_landings_route_since_filters(sched):
    client, root = sched
    _seed(root, "a/README.md", '---\nschedule: now\njob: "x"\n---\n')
    client.post("/-/rescan")
    jid = client.post("/-/scheduler/next").json()["id"]
    client.post(f"/-/scheduler/status/{jid}", json={"status": "complete", "exit_code": 0})
    future = 9999999999.0
    assert client.get("/-/landings", params={"since": future}).json() == []
