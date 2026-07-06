"""Feed-Datenquelle (PLAN-18 Stufe 18.1): Git-Historie → Entitäten (Case/Vault/
System) + Agent-Erkennung. Reine Funktionen gegen ein echtes Scratch-Repo."""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest

from bibi.feed import (
    CommitInfo, agent_commit_shas, aggregate_feed, classify_path, collect_commits,
    heatmap_buckets,
)

pytestmark = pytest.mark.slow


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _commit_as(root: Path, author_name: str, author_email: str, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
        "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    (root / "vault" / "case" / "20260601.FooBar").mkdir(parents=True)
    root_git = root
    _git(root_git, "init", "-q", "-b", "trunk")
    (root / "vault" / "case" / "20260601.FooBar" / "README.md").write_text(
        "x", encoding="utf-8")
    _commit_as(root, "Alice", "alice@x.io", "case: init FooBar")
    (root / "vault" / "CONVENTIONS.md").write_text("y", encoding="utf-8")
    _commit_as(root, "Bob", "bob@x.io", "vault: add conventions")
    (root / "pyproject.toml").write_text("[project]", encoding="utf-8")
    _commit_as(root, "Alice", "alice@x.io", "system: add pyproject")
    return root


# --- classify_path ------------------------------------------------------------

def test_classify_case_path():
    assert classify_path("vault/case/20260601.FooBar/README.md") == ("case", "20260601.FooBar")


def test_classify_vault_path():
    assert classify_path("vault/CONVENTIONS.md") == ("vault", "CONVENTIONS.md")


def test_classify_system_path():
    assert classify_path("pyproject.toml") == ("system", "System")


def test_classify_respects_custom_case_dir_name():
    # bibi3-Kompat: case_dir_name() kann "project" statt "case" sein (repo.py)
    assert classify_path("vault/project/X/README.md", case_dir_name="project") == ("case", "X")
    # derselbe Pfad ohne die passende case_dir_name ist eine gewöhnliche Vault-Datei
    assert classify_path("vault/project/X/README.md", case_dir_name="case") == ("vault", "project/X/README.md")


# --- collect_commits -----------------------------------------------------------

def test_collect_commits_returns_all_three(repo: Path):
    commits = collect_commits(repo)
    assert len(commits) == 3
    assert {c.author for c in commits} == {"Alice", "Bob"}


def test_collect_commits_paths_present(repo: Path):
    commits = collect_commits(repo)
    all_paths = {p for c in commits for p in c.paths}
    assert "vault/case/20260601.FooBar/README.md" in all_paths
    assert "vault/CONVENTIONS.md" in all_paths
    assert "pyproject.toml" in all_paths


def test_collect_commits_since_days_excludes_old(repo: Path):
    # Alle Commits sind "jetzt" (frisches Scratch-Repo) — since=0 Tage schließt
    # nichts aus, since eines Zeitpunkts VOR der Repo-Erstellung schon.
    commits = collect_commits(repo, since_days=3650)
    assert len(commits) == 3


# --- agent_commit_shas ---------------------------------------------------------

def test_agent_commit_shas_empty_without_agent_branch(repo: Path):
    assert agent_commit_shas(repo) == set()


def test_agent_commit_shas_detects_no_ff_merge(repo: Path):
    _git(repo, "checkout", "-q", "-b", "agent/jobslug")
    (repo / "vault" / "case" / "20260601.FooBar" / "output.md").write_text(
        "agent output", encoding="utf-8")
    _commit_as(repo, "bot", "bot@bibi.local", "agent: job output")
    agent_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "--no-ff", "--no-edit", "agent/jobslug")

    shas = agent_commit_shas(repo)
    assert agent_sha in shas
    # die drei ursprünglichen trunk-Commits sind NICHT agent-Herkunft
    trunk_commits = collect_commits(repo)
    non_agent_shas = {c.sha for c in trunk_commits} - {agent_sha}
    assert shas.isdisjoint(non_agent_shas)


# --- aggregate_feed -------------------------------------------------------------

def test_aggregate_feed_one_row_per_entity(repo: Path):
    entities = aggregate_feed(repo)
    keys = {(e.kind, e.name) for e in entities}
    assert keys == {("case", "20260601.FooBar"), ("vault", "CONVENTIONS.md"),
                    ("system", "System")}


def test_aggregate_feed_authors_per_entity(repo: Path):
    entities = {e.name: e for e in aggregate_feed(repo)}
    assert entities["20260601.FooBar"].authors == frozenset({"Alice"})
    assert entities["CONVENTIONS.md"].authors == frozenset({"Bob"})
    assert entities["System"].authors == frozenset({"Alice"})


def test_aggregate_feed_sorted_newest_first(repo: Path):
    entities = aggregate_feed(repo)
    timestamps = [e.last_changed for e in entities]
    assert timestamps == sorted(timestamps, reverse=True)


def test_aggregate_feed_all_agent_flag(repo: Path):
    _git(repo, "checkout", "-q", "-b", "agent/jobslug")
    (repo / "vault" / "case" / "20260601.FooBar" / "output.md").write_text(
        "agent output", encoding="utf-8")
    _commit_as(repo, "bot", "bot@bibi.local", "agent: job output")
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "--no-ff", "--no-edit", "agent/jobslug")

    entities = {e.name: e for e in aggregate_feed(repo)}
    # FooBar hat einen menschlichen (Alice) + einen agent-Commit → nicht all_agent
    assert entities["20260601.FooBar"].all_agent is False
    # CONVENTIONS.md / System wurden nie von einem Agent berührt
    assert entities["CONVENTIONS.md"].all_agent is False


# --- heatmap_buckets (PLAN-18 Stufe 18.2) --------------------------------------
# 2026-07-08 10:30 ist ein Mittwoch, Montag dieser Woche ist 2026-07-06.

_NOW = datetime.datetime(2026, 7, 8, 10, 30).timestamp()


def _c(dt: datetime.datetime) -> CommitInfo:
    return CommitInfo(sha="x", author="a", epoch=dt.timestamp(), paths=())


def test_heatmap_places_commit_in_correct_cell():
    grid = heatmap_buckets([_c(datetime.datetime(2026, 7, 8, 10, 0))], now=_NOW)
    assert grid[0][2][3] == 1  # Woche 0, Mittwoch (idx 2), Stunde 10 → Bucket 3 (09-12h)


def test_heatmap_monday_start_of_week_is_hour_bucket_zero():
    grid = heatmap_buckets([_c(datetime.datetime(2026, 7, 6, 0, 0))], now=_NOW)
    assert grid[0][0][0] == 1


def test_heatmap_sunday_end_of_week_is_last_bucket():
    grid = heatmap_buckets([_c(datetime.datetime(2026, 7, 12, 23, 30))], now=_NOW)
    assert grid[0][6][7] == 1


def test_heatmap_last_week_monday():
    grid = heatmap_buckets([_c(datetime.datetime(2026, 6, 29, 0, 0))], now=_NOW)
    assert grid[1][0][0] == 1


def test_heatmap_drops_commits_outside_window():
    # 5 Wochen vor dieser Woche (Woche-Index 5) liegt außerhalb des 5-Wochen-Fensters.
    grid = heatmap_buckets([_c(datetime.datetime(2026, 6, 1, 0, 0))], now=_NOW,
                          weeks=5)
    assert sum(sum(day) for week in grid for day in week) == 0


def test_heatmap_counts_multiple_commits_in_same_cell():
    commits = [_c(datetime.datetime(2026, 7, 8, 10, 0)),
              _c(datetime.datetime(2026, 7, 8, 11, 0))]
    grid = heatmap_buckets(commits, now=_NOW)
    assert grid[0][2][3] == 2


def test_heatmap_shape_default_five_weeks():
    grid = heatmap_buckets([], now=_NOW)
    assert len(grid) == 5
    assert all(len(week) == 7 for week in grid)
    assert all(len(day) == 8 for week in grid for day in week)
