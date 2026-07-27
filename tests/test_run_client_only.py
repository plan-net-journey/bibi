"""PLAN-38 (Entscheidung m.rau, 2026-07-27): ``run`` ist Client-only und läuft
in-place gegen den lokalen Stand; bei ``auto_sync: on`` committet der Lauf sein
eigenes Ergebnis mit Job-Provenienz.

Schnell: kein echter Wrapper-Subprozess — ``run_pinned`` bzw. ``_run_wrapper``
werden gemockt, die Git-Anteile laufen gegen ein echtes tmp-Repo (schnell genug
und der eigentliche Prüfgegenstand bei Stufe 2)."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi import git_ops, repo, state
from bibi.ctrl import run_cmd
from bibi.daemon import roles as R
from bibi.daemon.app import create_app


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class _Args(argparse.Namespace):
    def __init__(self, slug=None, command=None, kind="job"):
        super().__init__()
        self.slug, self.command, self.kind = slug, command, kind


# ── Rollen-Gate (die Regel selbst) ───────────────────────────────────────────

def test_forbids_local_run_akzeptiert_menge_und_roles():
    assert R.forbids_local_run(set()) == []
    assert R.forbids_local_run({"synchronizer", "controller"}) == []
    assert R.forbids_local_run({"scheduler"}) == ["scheduler"]
    assert R.forbids_local_run({"worker", "synchronizer"}) == ["worker"]
    # Host-Vollausbau: beide Rollen werden gemeldet, Reihenfolge stabil.
    assert R.forbids_local_run({"scheduler", "worker"}) == ["scheduler", "worker"]
    # Dieselbe Regel über das aufgelöste Roles-Objekt (Weg der HTTP-Route).
    assert R.forbids_local_run(R.resolve({"synchronizer", "controller"}, connect=True)) == []
    assert R.forbids_local_run(R.resolve({"scheduler", "worker"})) == ["scheduler", "worker"]


def test_denied_message_nennt_rolle_und_alternative():
    msg = R.local_run_denied_message(["scheduler"])
    assert "scheduler" in msg
    assert "job start" in msg  # verweist auf den Scheduler-Weg


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_run_lehnt_auf_scheduler_knoten_ab(team_repo: Path, monkeypatch, capsys):
    monkeypatch.setattr("bibi.config.read_env", lambda *a, **k: {"BIBI_ROLE": "scheduler,worker"})
    called: list = []
    monkeypatch.setattr(run_cmd, "run_pinned", lambda **kw: called.append(kw))

    rc = run_cmd.run(_Args(command="echo hi"))

    assert rc == 2
    assert called == []  # nichts gestartet, nicht nur nachträglich gemeldet
    assert "Client-only" in capsys.readouterr().err


def test_cli_run_laeuft_auf_client_und_in_place(team_repo: Path, monkeypatch):
    monkeypatch.setattr("bibi.config.read_env",
                        lambda *a, **k: {"BIBI_ROLE": "synchronizer,controller"})
    captured: dict = {}

    def fake_run_pinned(**kwargs):
        captured.update(kwargs)
        return {"id": "j1", "output_ref": "data/job/j1/output.jsonl", "kind": "job"}

    monkeypatch.setattr(run_cmd, "run_pinned", fake_run_pinned)
    monkeypatch.setattr(run_cmd, "_wait_until_terminal",
                        lambda _id: {"status": "complete", "exit_code": 0})
    (team_repo / "data" / "job" / "j1").mkdir(parents=True)
    (team_repo / "data" / "job" / "j1" / "output.jsonl").write_text("", encoding="utf-8")

    rc = run_cmd.run(_Args(command="echo hi"))

    assert rc == 0
    assert captured["in_place"] is True  # der Kern von PLAN-38


def test_cli_run_sagt_auto_sync_an(team_repo: Path, monkeypatch, capsys):
    monkeypatch.setattr("bibi.config.read_env", lambda *a, **k: {"BIBI_ROLE": "controller"})
    monkeypatch.setattr(run_cmd, "run_pinned",
                        lambda **kw: {"id": "j1", "output_ref": "data/job/j1/output.jsonl",
                                      "kind": "job"})
    monkeypatch.setattr(run_cmd, "_wait_until_terminal",
                        lambda _id: {"status": "complete", "exit_code": 0})
    (team_repo / "data" / "job" / "j1").mkdir(parents=True)
    (team_repo / "data" / "job" / "j1" / "output.jsonl").write_text("", encoding="utf-8")

    state.set_auto_sync(True)
    run_cmd.run(_Args(command="echo hi"))
    assert "auto_sync ist an" in capsys.readouterr().err

    state.set_auto_sync(False)
    run_cmd.run(_Args(command="echo hi"))
    assert "auto_sync ist an" not in capsys.readouterr().err


# ── HTTP-Route ───────────────────────────────────────────────────────────────

def test_route_run_lehnt_auf_scheduler_knoten_ab(team_repo: Path):
    app = create_app(R.resolve({"scheduler", "worker", "controller"}))
    with TestClient(app) as client:
        r = client.post("/-/run", json={"cmd": "echo hi"})
    assert r.status_code == 409
    assert r.json()["roles"] == ["scheduler", "worker"]


def test_route_test_existiert_nicht_mehr(team_repo: Path):
    app = create_app(R.resolve({"synchronizer", "controller"}, connect=True))
    with TestClient(app) as client:
        r = client.post("/-/test", json={"cmd": "echo hi"})
    assert r.status_code == 404


# ── Stufe 2: Schnappschuss + Pfad-Diff ───────────────────────────────────────

def test_paths_changed_since_findet_nur_die_lauf_aenderungen(team_repo: Path):
    tracked = team_repo / "vault" / "case" / "a.md"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("eins\n", encoding="utf-8")
    fremd = team_repo / "vault" / "case" / "fremd.md"
    fremd.write_text("unbeteiligt\n", encoding="utf-8")
    _git(team_repo, "add", "-A")
    _git(team_repo, "commit", "-q", "-m", "basis")

    # Ausgangslage wie im Anlassfall: der Mensch hat schon editiert (dirty),
    # zusätzlich liegt eine unbeteiligte untracked Datei herum.
    tracked.write_text("eins\nzwei (mensch)\n", encoding="utf-8")
    fremd.write_text("unbeteiligt, aber geändert\n", encoding="utf-8")
    (team_repo / "vault" / "case" / "notiz.md").write_text("nur meins\n", encoding="utf-8")

    snapshot = git_ops.snapshot_worktree()

    # Jetzt „läuft der Job": er hängt an die bereits dirty Datei an und legt
    # eine neue Datei an. Die fremden Änderungen rührt er nicht an.
    tracked.write_text("eins\nzwei (mensch)\ndrei (job)\n", encoding="utf-8")
    (team_repo / "vault" / "case" / "ergebnis.md").write_text("job-output\n", encoding="utf-8")

    changed = git_ops.paths_changed_since(snapshot)

    assert changed == ["vault/case/a.md", "vault/case/ergebnis.md"]
    # Genau der Punkt: die vom Menschen editierte Datei ist dabei (der Job hat
    # sie ja auch angefasst), seine unbeteiligte Handarbeit aber nicht.
    assert "vault/case/fremd.md" not in changed
    assert "vault/case/notiz.md" not in changed


def test_snapshot_bei_sauberem_tree_faellt_auf_head_zurueck(team_repo: Path):
    snapshot = git_ops.snapshot_worktree()
    assert snapshot["tracked"] == ""  # git stash create liefert nichts
    neu = team_repo / "vault" / "case" / "b.md"
    neu.parent.mkdir(parents=True, exist_ok=True)
    neu.write_text("job\n", encoding="utf-8")
    assert git_ops.paths_changed_since(snapshot) == ["vault/case/b.md"]


def test_snapshot_ruehrt_stash_liste_und_tree_nicht_an(team_repo: Path):
    datei = team_repo / "vault" / "case" / "c.md"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("dirty\n", encoding="utf-8")

    git_ops.snapshot_worktree()

    assert _git(team_repo, "stash", "list") == ""
    assert datei.read_text(encoding="utf-8") == "dirty\n"


# ── Stufe 2: der Commit selbst ───────────────────────────────────────────────

def _seeded_env(root: Path, snapshot: dict, *, slug="Witz") -> dict[str, str]:
    seed = root / "data" / "job" / "r1"
    seed.mkdir(parents=True, exist_ok=True)
    (seed / "inplace-seed.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return {"BIBI_IN_PLACE": "1", "BIBI_INPLACE_SEED": str(seed / "inplace-seed.json"),
            "BIBI_JOB_SLUG": slug, "BIBI_RUN_ID": "r1",
            "BIBI_OUTPUT_PATH": str(seed / "output.jsonl")}


def test_commit_in_place_committet_nur_die_job_pfade(team_repo: Path):
    from bibi.wrapper import _commit_in_place

    ziel = team_repo / "vault" / "case" / "Witz.md"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text("1. alter Witz\n", encoding="utf-8")
    _git(team_repo, "add", "-A")
    _git(team_repo, "commit", "-q", "-m", "basis")

    ziel.write_text("---\nmodel: haiku\n---\n1. alter Witz\n", encoding="utf-8")  # Mensch
    fremd = team_repo / "vault" / "case" / "andere-baustelle.md"
    fremd.write_text("halbfertig\n", encoding="utf-8")                            # Mensch
    snapshot = git_ops.snapshot_worktree()

    ziel.write_text("---\nmodel: haiku\n---\n1. alter Witz\n2. neuer Witz\n",
                    encoding="utf-8")                                             # Job
    sha = _commit_in_place(_seeded_env(team_repo, snapshot))

    assert sha
    betreff = _git(team_repo, "log", "-1", "--pretty=%s")
    assert betreff == "Witz: run r1"
    assert _git(team_repo, "log", "-1", "--pretty=%an") == "bibi/Witz"
    # Nur die Job-Datei ist drin — inklusive der Frontmatter-Änderung des
    # Menschen an derselben Datei (dateigranular, unvermeidbar und gewollt).
    dateien = _git(team_repo, "show", "--name-only", "--pretty=", "HEAD").splitlines()
    assert dateien == ["vault/case/Witz.md"]
    # Die unbeteiligte Baustelle bleibt liegen.
    assert "andere-baustelle.md" in _git(team_repo, "status", "--porcelain")


def test_commit_in_place_ohne_seed_committet_nichts(team_repo: Path):
    from bibi.wrapper import _commit_in_place

    _git(team_repo, "add", "-A")
    _git(team_repo, "commit", "-q", "-m", "basis")
    datei = team_repo / "vault" / "case" / "d.md"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("ergebnis\n", encoding="utf-8")
    vorher = _git(team_repo, "rev-parse", "HEAD")

    # auto_sync aus ⇒ der Worker setzt BIBI_INPLACE_SEED gar nicht erst.
    assert _commit_in_place({"BIBI_IN_PLACE": "1", "BIBI_JOB_SLUG": "x"}) is None

    assert _git(team_repo, "rev-parse", "HEAD") == vorher
    assert "d.md" in _git(team_repo, "status", "--porcelain", "-uall")


def test_commit_in_place_loescht_die_seed_datei(team_repo: Path):
    from bibi.wrapper import _commit_in_place

    snapshot = git_ops.snapshot_worktree()
    env = _seeded_env(team_repo, snapshot)
    (team_repo / "vault" / "case" / "e.md").parent.mkdir(parents=True, exist_ok=True)
    (team_repo / "vault" / "case" / "e.md").write_text("x\n", encoding="utf-8")

    _commit_in_place(env)

    assert not Path(env["BIBI_INPLACE_SEED"]).exists()


def test_worker_schreibt_seed_nur_bei_auto_sync(team_repo: Path):
    from bibi.daemon.worker import _write_inplace_seed

    run_dir = team_repo / "data" / "job" / "r2"
    run_dir.mkdir(parents=True)

    state.set_auto_sync(False)
    assert _write_inplace_seed(run_dir) is None
    assert not (run_dir / "inplace-seed.json").exists()

    state.set_auto_sync(True)
    seed = _write_inplace_seed(run_dir)
    assert seed is not None and seed.exists()
    assert set(json.loads(seed.read_text(encoding="utf-8"))) == {"tracked", "untracked"}
