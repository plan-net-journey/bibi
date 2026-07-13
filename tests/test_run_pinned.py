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


# ── app_port/app_prefix/exec_mode/image-Passthrough (migriert aus dem mit ────
# run_local() entfernten tests/test_run_local_app_fields.py, PLAN-28 Refactor D
# — der Bug (Fund 2026-07-10 HITL-Test-App-Migration / PLAN-24 Befund 1) galt
# run_local()s eigener Resolution-Logik; run_pinned() geht stattdessen über
# execute_reservation()s reservation.get(...)-Pfad, der das schon immer korrekt
# weiterreicht — dieser Test deckt also die ganze Kette INSERT→reserve_next()→
# execute_reservation()→_run_wrapper() ab, nicht nur die Slug-Auflösung.


def _capturing_run_wrapper(tmp_path: Path, captured: dict):
    def fake(**kwargs):
        captured.update(kwargs)
        return 0, None, tmp_path / "data" / "job" / "jid" / "output.jsonl", "detached", 999
    return fake


def test_run_pinned_passes_app_port_and_exec_mode_to_wrapper(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    _seed(gitrepo, "myapp/README.md",
          '---\nschedule: "never"\njob: "python3 myapp.py"\napp_port: 9100\n'
          'app_prefix: /myapp\nexec_mode: host\n---\n# myapp\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="myapp", repo_root=gitrepo, host="mac")
    assert captured["app_port"] == 9100
    assert captured["app_prefix"] == "/myapp"
    assert captured["exec_mode"] == "host"


def test_run_pinned_passes_schedule_image_override_to_wrapper(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    _seed(gitrepo, "customimg/README.md",
          '---\nschedule: "never"\njob: "python3 customimg.py"\n'
          'image: "registry.local/custom:7"\n---\n# customimg\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="customimg", repo_root=gitrepo, host="mac")
    assert captured["image"] == "registry.local/custom:7"


def test_run_pinned_plain_job_passes_none_for_app_fields(gitrepo, monkeypatch):
    # Ein normaler (Nicht-App-)Job hat keine app_port/exec_mode-Frontmatter —
    # die Felder müssen dann sauber None bleiben, nicht z. B. 0/"" (was
    # _run_wrapper()/exec_backend.build_exec() als "gesetzt" missverstehen
    # könnte).
    import bibi.daemon.worker as W
    _seed(gitrepo, "plainjob/README.md", '---\nschedule: "never"\njob: "echo hi"\n---\n# plain\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="plainjob", repo_root=gitrepo, host="mac")
    assert captured["app_port"] is None
    assert captured["app_prefix"] is None
    assert captured["exec_mode"] is None
    assert captured["image"] is None


def test_run_pinned_by_cmd_has_no_app_fields(gitrepo, monkeypatch):
    # Ad-hoc-Kommando (kein Slug/MD) — es gibt kein Frontmatter, aus dem
    # app_port/exec_mode kommen könnten; muss weiterhin funktionieren.
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    assert captured["app_port"] is None
    assert captured["app_prefix"] is None
    assert captured["exec_mode"] is None
    assert captured["image"] is None
