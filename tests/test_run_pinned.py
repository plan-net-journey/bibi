"""run_pinned(): /run mit voller Scheduler-Lifecycle, gepinnt + sofort (PLAN-28)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo
from bibi.daemon import job_db
from bibi.daemon.worker import run_pinned


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


def _seed(root: Path, rel: str, body: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"seed {rel}")


def _fake_run_wrapper(tmp_path):
    def fake(**kwargs):
        return 0, None, tmp_path / "data" / "job" / "jid" / "output.jsonl", "detached", 999
    return fake


def test_run_pinned_with_cmd_creates_pinned_row_and_dispatches(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    assert row["pinned_host"] == "mac"
    assert row["status"] == "running"  # detach=True: sofort reserviert+dispatcht
    assert row["payload"] == "echo hi"
    # attempts=0 (nicht 1!) ist "kein Retry" — der Wrapper prüft attempt_cur
    # (0 bei einem frischen Job) < attempts_max; attempts=1 würde also einen
    # Retry auslösen, s. run_pinned()s Docstring. 0 matcht das historische
    # /run-Verhalten (ein Versuch, sofortiger Fehlschlag) und ist nötig, weil
    # die CLI (kein laufender Daemon) einen fälligen Retry nie bedienen könnte.
    assert row["attempts"] == 0


def test_run_pinned_with_slug_resolves_existing_schedule(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    _seed(gitrepo, "myjob/README.md", '---\nschedule: never\njob: "echo from md"\n---\n')
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(slug="myjob", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    assert row["payload"] == "echo from md"
    assert row["schedule_ref"] == "myjob/README.md"
    assert row["pinned_host"] == "mac"


def test_run_pinned_unknown_slug_raises_lookup_error(gitrepo):
    with pytest.raises(LookupError):
        run_pinned(slug="nope", repo_root=gitrepo, host="mac")


def test_run_pinned_without_slug_or_cmd_raises_value_error(gitrepo):
    with pytest.raises(ValueError):
        run_pinned(repo_root=gitrepo, host="mac")


def test_run_pinned_generates_unique_slug_per_call(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res1 = run_pinned(slug="adhoc", cmd="echo hi", repo_root=gitrepo, host="mac")
    res2 = run_pinned(slug="adhoc", cmd="echo hi", repo_root=gitrepo, host="mac")
    assert res1["id"] != res2["id"]
    assert res1["slug"] != res2["slug"]
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    conn.close()
    assert n == 2


def test_run_pinned_custom_attempts(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac", attempts=3)
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT attempts FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    assert row["attempts"] == 3


def test_run_pinned_other_host_cannot_reserve_it(gitrepo, monkeypatch):
    # Die Pin-Garantie gilt sofort, nicht erst beim nächsten Sweep/Loop-Tick.
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    assert job_db.reserve_next(conn, host="sarasate", pinned_only=True) is None
    assert job_db.reserve_next(conn, host="sarasate") is None  # auch nicht im Team-Pfad
    conn.close()
