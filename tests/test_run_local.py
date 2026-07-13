"""/run: lokale On-Demand-Ausführung (DESIGN §1.4; PLAN-3 §3.3b)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from bibi import repo
from bibi.ctrl import main
from bibi.daemon import job_db
from bibi.daemon.worker import run_local
from bibi.wrapper import output

pytestmark = pytest.mark.slow


def _wait_until(predicate, *, timeout=10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def gitrepo(tmp_path: Path, monkeypatch):
    root = tmp_path / "r"
    (root / "vault" / "case").mkdir(parents=True)
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname="t"\nversion="0"\n', encoding="utf-8")
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


def _conn(root: Path):
    return job_db.connect(root / "data" / "jobs.sqlite")


def test_run_local_by_cmd_writes_local_journal(gitrepo: Path):
    res = run_local(cmd="echo hallo && echo fertig", repo_root=gitrepo,
                    work_dir=gitrepo / "data" / "worktrees",
                    db_path=gitrepo / "data" / "jobs.sqlite")
    assert res["status"] == "complete" and res["exit_code"] == 0
    out = gitrepo / "data" / "job" / res["id"] / "output.jsonl"
    assert output.lines(out, "out") == ["hallo", "fertig"]

    conn = _conn(gitrepo)
    try:
        jrows = job_db.list_journal(conn)
        assert len(jrows) == 1
        assert jrows[0]["domain"] == "local"          # lokale Domäne (§1.4)
        assert jrows[0]["output_ref"] == res["output_ref"]
        # KEIN jobs-Eintrag — die zentrale Queue sieht /run nie
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        conn.close()


def test_run_local_does_not_enter_scheduler_queue(gitrepo: Path):
    run_local(cmd="echo x", repo_root=gitrepo,
              work_dir=gitrepo / "data" / "worktrees",
              db_path=gitrepo / "data" / "jobs.sqlite")
    conn = _conn(gitrepo)
    try:
        # reserve_next findet nichts — /run legt nichts in die Queue
        assert job_db.reserve_next(conn) is None
    finally:
        conn.close()


def test_run_local_by_slug(gitrepo: Path):
    (gitrepo / "vault" / "case" / "hello" / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (gitrepo / "vault" / "case" / "hello" / "README.md").write_text(
        '---\nschedule: now\njob: "echo viaslug"\n---\n', encoding="utf-8")
    # Committen: der Worktree ist `git worktree add … trunk` — das Job-cwd
    # (Verzeichnis der Schedule-MD, § Job-cwd-Fix 2026-07-05) existiert darin
    # nur, wenn die Datei schon auf trunk sitzt.
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "seed hello")
    res = run_local(slug="hello", repo_root=gitrepo,
                    work_dir=gitrepo / "data" / "worktrees",
                    db_path=gitrepo / "data" / "jobs.sqlite")
    assert res["slug"] == "hello" and res["status"] == "complete"
    out = gitrepo / "data" / "job" / res["id"] / "output.jsonl"
    assert output.lines(out, "out") == ["viaslug"]


def test_run_local_unknown_slug_raises(gitrepo: Path):
    with pytest.raises(LookupError):
        run_local(slug="nope", repo_root=gitrepo,
                  work_dir=gitrepo / "data" / "worktrees",
                  db_path=gitrepo / "data" / "jobs.sqlite")


def test_run_local_ephemeral_worktree_removed(gitrepo: Path):
    run_local(cmd="echo x", slug="eph", repo_root=gitrepo,
              work_dir=gitrepo / "data" / "worktrees",
              db_path=gitrepo / "data" / "jobs.sqlite")
    assert not (gitrepo / "data" / "worktrees" / "eph").exists()  # aufgeräumt (§3.3b)


def test_run_local_failed_cmd(gitrepo: Path):
    res = run_local(cmd="exit 5", repo_root=gitrepo,
                    work_dir=gitrepo / "data" / "worktrees",
                    db_path=gitrepo / "data" / "jobs.sqlite")
    assert res["status"] == "failed" and res["exit_code"] == 5


def test_run_local_claude_via_stub(gitrepo: Path, monkeypatch):
    # claude-Typ end-to-end durch denselben output.jsonl-Pfad (Stub statt echtem claude).
    fake = gitrepo / "fakeclaude.sh"
    fake.write_text("#!/bin/sh\necho claude-says-hi\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("BIBI_CLAUDE_BIN", str(fake))
    md = gitrepo / "vault" / "case" / "ki1" / "README.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(
        '---\nschedule: now\njob: "claude: Antworte hallo"\nmodel: claude-haiku-4-5-20251001\n---\n',
        encoding="utf-8")
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "seed ki1")
    res = run_local(slug="ki1", repo_root=gitrepo,
                    work_dir=gitrepo / "data" / "worktrees",
                    db_path=gitrepo / "data" / "jobs.sqlite")
    assert res["kind"] == "job" and res["status"] == "complete"
    out = gitrepo / "data" / "job" / res["id"] / "output.jsonl"
    assert output.lines(out, "out") == ["claude-says-hi"]


# ── CLI: bibi-ctrl run (in-process, kein Daemon nötig) ───────────────────────


def test_cli_run_cmd(gitrepo: Path, capsys):
    rc = main(["run", "--cmd", "echo cli-hallo"])
    assert rc == 0
    assert "cli-hallo" in capsys.readouterr().out
    conn = _conn(gitrepo)
    try:
        assert job_db.list_journal(conn, domain="local")
    finally:
        conn.close()


def test_cli_run_unknown_slug(gitrepo: Path, capsys):
    assert main(["run", "nope"]) == 1
    assert "nope" in capsys.readouterr().err


def test_cli_run_needs_arg(gitrepo: Path):
    assert main(["run"]) == 2


# ── POST /-/run (worker-gated) ───────────────────────────────────────────────


def test_run_endpoint(gitrepo: Path):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    from bibi.daemon.worker import Worker

    w = Worker(autopoll=False, repo_root=gitrepo,
               work_dir=gitrepo / "data" / "worktrees",
               db_path=gitrepo / "data" / "jobs.sqlite")
    app = create_app(roles.resolve({"worker"}), worker=w)
    with TestClient(app) as c:
        r = c.post("/-/run", json={"cmd": "echo via-endpoint"})
        assert r.status_code == 200
        # PLAN-21 Befund 10, 2. Nachtrag: /-/run antwortet sofort nach
        # Subprozess-Start (status="running"), nicht erst nach Lauf-Ende.
        # PLAN-28: run_pinned() ersetzt run_local() für diese Route — der
        # Wrapper-Subprozess (detach=True) meldet Commit/Terminal-Status
        # selbständig, kein Hintergrund-Thread mehr im Daemon nötig.
        slug = r.json()["slug"]
        assert r.json()["status"] == "running"
        assert _wait_until(lambda: slug not in c.get("/-/run/live").json())
        # PLAN-28: /run bekommt jetzt eine echte, gepinnte jobs-Zeile (volle
        # Scheduler-Lifecycle) — bleibt aber lokal: pinned_host erzwingt
        # genau diesen Knoten, kein anderer Worker kann sie je reservieren.
        # domain ist jetzt 'scheduled' (echter jobs-Report-Pfad), pinned_host
        # bleibt trotzdem gesetzt — das unterscheidet "meine eigene
        # /run-Historie" weiterhin von echten Team-Queue-Läufen.
        conn = _conn(gitrepo)
        try:
            rows = conn.execute("SELECT status, pinned_host FROM jobs").fetchall()
            assert len(rows) == 1
            assert rows[0]["status"] == "complete"
            assert rows[0]["pinned_host"] is not None
            assert any(j["domain"] == "scheduled" and j["pinned_host"] is not None
                      for j in job_db.list_journal(conn))
        finally:
            conn.close()
        # unbekannter slug → 404


def test_run_endpoint_works_without_any_worker_role(gitrepo: Path):
    # User-Feedback 2026-07-06: /-/run hing bisher an _add_worker_routes()
    # (nur mit --worker registriert) — ein reiner Client (Synchronizer +
    # --connect, kein --worker) bekam dadurch 404, obwohl run_local() selbst
    # gar kein Worker-Objekt braucht (genau wie die CLI, run_cmd.py). Dieser
    # Test ist der eigentliche Regressionsschutz für den Fix.
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        r = c.post("/-/run", json={"cmd": "echo via-client-only"})
        assert r.status_code == 200
        slug = r.json()["slug"]
        assert r.json()["status"] == "running"
        assert _wait_until(lambda: slug not in c.get("/-/run/live").json())
        assert c.post("/-/run", json={"slug": "nope"}).status_code == 404
        # weder slug noch cmd → 400
        assert c.post("/-/run", json={}).status_code == 400


def test_run_journal_endpoint_works_without_any_worker_or_scheduler_role(gitrepo: Path):
    # PLAN-17 Stufe 17.1: die eigene /run-Historie muss ein reiner Client (kein
    # --scheduler, kein --worker) lesen können — /-/journal selbst bleibt
    # scheduler-gated (frozen contract), /-/run/journal ist die dafür neue,
    # bewusst rollenunabhängige Route. PLAN-28: filtert jetzt "domain='local'
    # ODER pinned_host gesetzt" (mine_only) statt starr domain="local" — /run
    # bekommt jetzt domain='scheduled' (echte jobs-Zeile), bleibt aber über
    # pinned_host als eigene /run-Historie erkennbar.
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        slug = c.post("/-/run", json={"cmd": "echo local-lauf"}).json()["slug"]
        assert _wait_until(lambda: slug not in c.get("/-/run/live").json())
        r = c.get("/-/run/journal")
        assert r.status_code == 200
        rows = r.json()
        assert rows and all(row["domain"] == "scheduled" and row["pinned_host"] is not None
                            for row in rows)


def test_journal_endpoint_filters_by_domain(gitrepo: Path):
    # PLAN-17 Stufe 17.1: ein Knoten mit BEIDEN Rollen (Scheduler + eigene /run-
    # Läufe, wie sarasate) soll die disponierte /-/journal-Sicht optional auf
    # eine Domäne einschränken können — /-/journal kannte bisher nur
    # slug/host/limit/offset, kein domain-Filter. Bleibt scheduler-gated (§1.1
    # gefrorener Vertrag, s. test_daemon_contract.py) — anders als /-/run selbst
    # (rollenunabhängig) ist /-/journal Teil des eingefrorenen v3.0-Vertrags.
    #
    # PLAN-28: /-/run selbst erzeugt jetzt domain='scheduled'-Einträge (echte
    # gepinnte jobs-Zeile) — eine echte domain='local'-Zeile kommt nur noch
    # über den alten CLI-Pfad (bibi-ctrl run/write_local_journal()) zustande,
    # hier direkt nachgestellt, um den domain-Filter selbst unabhängig davon
    # zu testen.
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    app = create_app(roles.resolve({"scheduler", "synchronizer", "controller"}))
    conn = _conn(gitrepo)
    try:
        job_db.write_local_journal(
            conn, run_id="adhoc:1", slug="adhoc", kind="job", status="complete",
            exit_code=0, output_ref=None, host="h", worker="w",
            started_at=time.time(), finished_at=time.time(),
        )
    finally:
        conn.close()
    with TestClient(app) as c:
        r = c.get("/-/journal", params={"domain": "local"})
        assert r.status_code == 200
        rows = r.json()
        assert rows and all(row["domain"] == "local" for row in rows)
