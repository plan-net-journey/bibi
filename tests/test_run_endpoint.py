"""POST /-/run + bibi-ctrl run: End-to-End über den echten Wrapper-Subprozess
(DESIGN §1.4; PLAN-3 §3.3b; PLAN-28). Vormals ``test_run_local.py`` — der
namensgebende ``run_local()`` ist mit PLAN-28 Refactor D entfernt, geblieben
sind die Route-/CLI-Integrationstests, die ohnehin nie ``run_local()`` direkt
aufriefen, sondern über ``main()``/``TestClient`` gingen. Die schnellen,
gemockten Dispatch-Tests für den Nachfolger ``run_pinned()`` selbst liegen in
tests/test_run_pinned.py und tests/test_run_cmd.py."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from bibi import repo
from bibi.ctrl import main
from bibi.daemon import job_db
from bibi.daemon.worker import run_pinned

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


def test_run_pinned_ephemeral_worktree_removed(gitrepo: Path):
    # Regressionsschutz PLAN-28 Refactor D: run_pinned() erzeugt für JEDEN
    # Aufruf einen frischen, nie wiederverwendeten unique_slug — anders als ein
    # rekurrierender Scheduler-Job, dessen stabiler Slug denselben Worktree
    # über mehrere Läufe hinweg nutzt. execute_reservation() rief _run_wrapper()
    # aber zunächst immer mit ephemeral=False auf (an genau diesen
    # wiederverwendeten Fall gedacht) — ohne ephemeral=True räumt der
    # (detachte) Wrapper-Subprozess seinen Worktree nie auf, ein Leak bei jedem
    # /run/bibi-ctrl-run-Aufruf. Fund beim Entfernen von run_local(), das dies
    # vorher über seinen eigenen Blocking-Pfad (worktree.remove()) erledigte.
    res = run_pinned(cmd="echo x", repo_root=gitrepo,
                     work_dir=gitrepo / "data" / "worktrees",
                     db_path=gitrepo / "data" / "jobs.sqlite")
    wt_path = gitrepo / "data" / "worktrees" / res["slug"]
    assert _wait_until(lambda: not wt_path.exists())


# ── CLI: bibi-ctrl run (in-process, kein Daemon nötig) ───────────────────────


def test_cli_run_cmd(gitrepo: Path, capsys):
    rc = main(["run", "--cmd", "echo cli-hallo"])
    assert rc == 0
    assert "cli-hallo" in capsys.readouterr().out
    conn = _conn(gitrepo)
    try:
        rows = job_db.list_journal(conn, mine_only=True)
        assert rows and all(r["domain"] == "scheduled" and r["pinned_host"] is not None
                            for r in rows)
    finally:
        conn.close()


def test_cli_run_unknown_slug(gitrepo: Path, capsys):
    assert main(["run", "nope"]) == 1
    assert "nope" in capsys.readouterr().err


def test_cli_run_needs_arg(gitrepo: Path):
    assert main(["run"]) == 2


# ── POST /-/run (client-only, in-place) ──────────────────────────────────────


def test_run_endpoint(gitrepo: Path):
    # PLAN-38 (2026-07-27): der Knoten trägt hier bewusst Client-Rollen. Vorher
    # lief dieser Test mit ``{"worker"}`` — seit /run in-place gegen den
    # Live-Checkout läuft, lehnt die Route genau diese Rolle mit 409 ab
    # (roles.forbids_local_run(), Regel selbst in tests/test_run_client_only.py).
    # Prüfgegenstand hier bleibt der volle Lauf-Lifecycle über die Route: echte
    # jobs-Zeile, terminaler Status, pinned_host, Journal-Sicht.
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        r = c.post("/-/run", json={"cmd": "echo via-endpoint"})
        assert r.status_code == 200
        # PLAN-21 Befund 10, 2. Nachtrag: /-/run antwortet sofort nach
        # Subprozess-Start (status="running"), nicht erst nach Lauf-Ende.
        # PLAN-28: run_pinned() ersetzt run_local() für diese Route — der
        # Der detacht laufende Wrapper-Subprozess meldet Commit/Terminal-Status
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


def test_run_endpoint_runs_in_place_and_never_commits(gitrepo: Path):
    # User-Fund 2026-07-14, ursprünglich gegen POST /-/test geschrieben; seit
    # PLAN-38 (2026-07-27) ist genau das das Verhalten von POST /-/run selbst
    # und /-/test ersatzlos entfallen (dessen 404 deckt
    # tests/test_run_client_only.py ab). Prüfgegenstand unverändert: eine
    # UNCOMMITTETE Datei neben einer committeten Schedule-MD, die der frühere
    # Worktree-Lauf von trunk nie sah, muss hier lesbar sein, UND weder sie
    # noch die vom Job neu geschriebene Datei dürfen danach committet sein.
    #
    # Der zweite Teil gilt seit PLAN-38 Stufe 2 nur bei auto_sync: off — mit
    # auto_sync: on committet der Lauf sein eigenes Ergebnis bewusst selbst
    # (tests/test_run_client_only.py). Darum hier explizit gesetzt, statt sich
    # auf den Default zu verlassen.
    from fastapi.testclient import TestClient

    from bibi import state
    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    job_dir = gitrepo / "vault" / "case" / "myjob"
    job_dir.mkdir(parents=True)
    # Der Dateiname trägt den Slug (#143) — aus `README.md` wäre `README` geworden.
    (job_dir / "myjob.md").write_text(
        '---\nschedule: never\njob: "cat dirty.txt && echo touched >> new.txt"\n---\n',
        encoding="utf-8")
    _git(gitrepo, "add", "-A")
    _git(gitrepo, "commit", "-q", "-m", "seed myjob")
    (job_dir / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")  # NIE committet

    state.set_auto_sync(False)
    app = create_app(roles.resolve({"synchronizer", "controller"}))
    with TestClient(app) as c:
        r = c.post("/-/run", json={"slug": "myjob"})
        assert r.status_code == 200
        slug = r.json()["slug"]
        assert r.json()["status"] == "running"
        assert _wait_until(lambda: slug not in c.get("/-/run/live").json())

        conn = _conn(gitrepo)
        try:
            rows = conn.execute("SELECT status FROM jobs").fetchall()
            assert len(rows) == 1
            assert rows[0]["status"] == "complete"
        finally:
            conn.close()

        # Beide Dateien bleiben uncommittet, kein agent/<slug>-Branch entsteht.
        status = _git(gitrepo, "status", "--porcelain")
        assert "dirty.txt" in status
        assert "new.txt" in status
        assert _git(gitrepo, "branch", "--list", "agent/*") == ""

        # Regressionstest für den im Plan-Review gefundenen output_ref-Bug:
        # das Journal-Transkript muss den echten Output tragen (der Beweis,
        # dass dirty.txt tatsächlich gelesen wurde UND output_ref korrekt
        # berechnet wird, obwohl kein Commit passiert ist).
        journal = c.get("/-/run/journal", params={"slug": slug}).json()
        assert len(journal) == 1
        jid = journal[0]["id"]
        out = c.get(f"/-/run/journal/{jid}/output").json()
        assert out["events"]
        assert any("uncommitted" in str(e.get("line", "")) for e in out["events"])


def test_run_endpoint_works_without_any_worker_role(gitrepo: Path):
    # User-Feedback 2026-07-06: /-/run hing bisher an _add_worker_routes()
    # (nur mit --worker registriert) — ein reiner Client (Synchronizer +
    # --connect, kein --worker) bekam dadurch 404, obwohl der Dispatch selbst
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


def test_journal_endpoint_filters_by_domain(gitrepo: Path, seed_journal_row):
    # PLAN-17 Stufe 17.1: ein Knoten mit BEIDEN Rollen (Scheduler + eigene /run-
    # Läufe, wie sarasate) soll die disponierte /-/journal-Sicht optional auf
    # eine Domäne einschränken können — /-/journal kannte bisher nur
    # slug/host/limit/offset, kein domain-Filter. Bleibt scheduler-gated (§1.1
    # gefrorener Vertrag, s. test_daemon_contract.py) — anders als /-/run selbst
    # (rollenunabhängig) ist /-/journal Teil des eingefrorenen v3.0-Vertrags.
    #
    # PLAN-28 Refactor D: /-/run selbst erzeugt jetzt ausschließlich
    # domain='scheduled'-Einträge (echte gepinnte jobs-Zeile) — eine echte
    # domain='local'-Zeile kann nur noch als historischer Altbestand auf einem
    # Knoten von vor Refactor D existieren (der schreibende Pfad,
    # write_local_journal(), ist entfernt); hier direkt nachgestellt (per
    # seed_journal_row-Fixture, Ersatz für die entfernte Funktion), um den
    # domain-Filter selbst unabhängig davon zu testen.
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    app = create_app(roles.resolve({"scheduler", "synchronizer", "controller"}))
    conn = _conn(gitrepo)
    try:
        seed_journal_row(
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
