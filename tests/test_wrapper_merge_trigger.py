"""Ebene 1 v2 (PLAN-30, Bibi4-Case 20260621): echter, Wrapper-getriggerter
Merge-back — über die reale Subprozess-/HTTP-Grenze, kein Mock.

Vorherige Absicherung (``test_mergeback_route.py::test_local_scheduler_report_merges``)
konstruierte ``LocalScheduler`` manuell und rief ``.report()`` direkt auf — genau der
Pfad, den der reale, detachte Wrapper-Subprozess nie nimmt (er schreibt Terminal-
Status per Direct-SQLite, s. ``bibi/wrapper/__init__.py::_report_terminal()``).
Diese Datei startet einen echten, socket-gebundenen uvicorn-Server + lässt den
Job über den echten ``python -m bibi.wrapper``-Subprozess laufen (wie in
``test_worker.py``) — die einzige Instanz, die trunk bewegen kann, ist also
tatsächlich der neue Wrapper-Trigger: kein Synchronizer/Sweep läuft in diesen
Tests mit, ein Fortschritt von trunk kann nur von dort kommen.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from bibi import repo
from bibi.daemon import job_db, roles as roles_mod
from bibi.daemon.app import create_app
from bibi.daemon.worker import Worker, run_pinned

pytestmark = pytest.mark.slow

_TERMINAL = frozenset({"complete", "error", "killed", "zombie", "inactive"})


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LiveServer:
    """Ein echter uvicorn-Server auf einem echten Socket — der Wrapper spricht
    per ``urllib`` (echtes HTTP), keine ASGI-``TestClient``-Abkürzung, sonst
    würde genau die Prozessgrenze übersprungen, die dieser Test beweisen soll."""

    def __init__(self, app, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "_LiveServer":
        self.thread.start()
        deadline = time.monotonic() + 5.0
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started, "uvicorn-Testserver nicht gestartet"
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5.0)


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


def _seed(root: Path, rel: str, body: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    # Committen (nicht nur schreiben): Job-cwd/Rescan brauchen die Datei auf trunk.
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"seed {rel}")


def _wait_terminal(jid: str, timeout: float = 30.0) -> dict:
    """Wie der gleichnamige Helfer in ``test_worker.py`` — und mit demselben
    Fehler behaftet gewesen (m.rau/bibi#87): beim Ablauf der Frist gab er still
    die letzte Zeile zurück, und der Test scheiterte danach an seiner eigenen
    Assertion, als wäre das Ergebnis falsch statt die Zeit zu knapp.

    Beide Tests dieser Datei waren am 2026-07-31 auf dem Mac rot, während sie
    auf sarasate grün blieben — dieselbe Klasse Befund, nur andersherum verteilt
    als bei den Container-Tests.
    """
    started = time.monotonic()
    deadline = started + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        conn = job_db.connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        finally:
            conn.close()
        if row:
            last = dict(row)
            if row["status"] in _TERMINAL:
                return last
        time.sleep(0.05)
    raise AssertionError(
        f"Job {jid} wurde nach {time.monotonic() - started:.1f}s nicht terminal — "
        f"Status: {last.get('status', '(keine Zeile)')}. Abgelaufene Wartefrist, "
        "kein falsches Ergebnis.")


def _wait_trunk_advances(root: Path, before: str, timeout: float = 8.0) -> str:
    """Wartet, bis sich trunk bewegt — ruft dabei selbst nie remerge_all()/den
    Sweep auf. Bleibt trunk stehen, schlägt die Assertion im Test fehl."""
    deadline = time.monotonic() + timeout
    head = before
    while time.monotonic() < deadline:
        head = _git(root, "rev-parse", "trunk")
        if head != before:
            return head
        time.sleep(0.05)
    return head


def test_pinned_job_merges_immediately_without_sweep(gitrepo: Path, monkeypatch):
    """2b (gepinnter Lauf, z. B. Mac/Client ohne Scheduler-Rolle): trunk bekommt
    den Merge-Commit, ohne dass ein Sweep je lief."""
    port = _free_port()
    monkeypatch.setenv("BIBI_DAEMON_PORT", str(port))
    sync_lock = threading.Lock()
    # Client-artige Rollen (kein scheduler, kein worker) — genau der Knotentyp,
    # auf dem die Status-Route bisher (roles.scheduler-Gate) gar nicht existierte.
    app = create_app(roles_mod.resolve({"controller"}), sync_lock=sync_lock)
    with _LiveServer(app, port):
        # "echo x" allein erzeugt laut worktree.commit()s eigener Doku NIE einen
        # Commit (Output landet unter gitignored data/) — branch bliebe None, der
        # neue Trigger würde nie feuern, unabhängig davon, ob er funktioniert
        # (Review-Runde 2, Fund 1). Eine getrackte Datei schreiben stattdessen.
        _seed(gitrepo, "witz/witz.md", '---\nschedule: now\njob: "echo hi > result.md"\n---\n')
        before = _git(gitrepo, "rev-parse", "trunk")

        result = run_pinned(slug="witz", repo_root=gitrepo,
                            work_dir=gitrepo / "data" / "worktrees")
        row = _wait_terminal(result["id"])
        assert row.get("status") == "complete", row

        after = _wait_trunk_advances(gitrepo, before)
        assert after != before, "trunk hat sich nie bewegt — Sofort-Merge feuerte nicht"
        # run_pinned() hängt an jeden Slug einen zufälligen Hex-Suffix (UNIQUE-
        # Constraint auf jobs.slug, worker.py::run_pinned()) — "agent/witz" wäre
        # nie der echte Branch-Name (Review-Runde 2, Fund 1, zweiter Teilbug).
        branch = f"agent/{result['slug']}"
        subprocess.run(["git", "merge-base", "--is-ancestor", branch, "trunk"],
                       cwd=gitrepo, check=True)


def test_scheduler_dispatched_job_merges_immediately_without_sweep(gitrepo: Path, monkeypatch):
    """2a (Scheduler-dispatchter Lauf): derselbe Beweis für den regulären
    Worker-Pfad — Review-Fund: auch dieser hing bisher komplett am Sweep,
    nicht nur der gepinnte Pfad."""
    port = _free_port()
    monkeypatch.setenv("BIBI_DAEMON_PORT", str(port))
    sync_lock = threading.Lock()
    app = create_app(roles_mod.resolve({"scheduler"}), sync_lock=sync_lock)
    with _LiveServer(app, port):
        # s. Kommentar im gepinnten Test oben — "echo x" erzeugt nie einen Commit.
        _seed(gitrepo, "run1/run1.md", '---\nschedule: now\njob: "echo hi > result.md"\n---\n')
        conn = job_db.connect()
        try:
            job_db.rescan(conn)
            jid = conn.execute(
                "SELECT id FROM jobs WHERE status='pending' LIMIT 1").fetchone()["id"]
        finally:
            conn.close()
        before = _git(gitrepo, "rev-parse", "trunk")

        worker = Worker(repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
                        worker_name="t")
        assert worker.tick_once() is True
        row = _wait_terminal(jid)
        assert row.get("status") == "complete", row

        after = _wait_trunk_advances(gitrepo, before)
        assert after != before, "trunk hat sich nie bewegt — Sofort-Merge feuerte nicht"
        subprocess.run(["git", "merge-base", "--is-ancestor", "agent/run1", "trunk"],
                       cwd=gitrepo, check=True)
