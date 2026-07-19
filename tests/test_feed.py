"""Feed-Datenquelle (PLAN-18 Stufe 18.1): Git-Historie → Entitäten (Case/Vault/
System) + Agent-Erkennung. Reine Funktionen gegen ein echtes Scratch-Repo."""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest

from bibi.feed import (
    CommitInfo, activity_series_by_prefix, agent_commit_shas, aggregate_feed,
    classify_path, collect_commits, heatmap_buckets, remote_commit_base_url,
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


def test_agent_commit_shas_accepted_limitation_needs_default_merge_message(repo: Path):
    # Zwischenstand verworfen (s. agent_commit_shas()-Docstring): Branch-
    # Containment als Zusatzsignal für abweichende Merge-Messages wurde live
    # gegen die echte bibi-notes-Historie widerlegt — alle 8 echten Sync-Merges
    # dort wurden über Containment fälschlich als Agent erkannt (alte Commits
    # werden irgendwann Vorfahre praktisch jedes späteren Branches). Bewusst
    # akzeptierte Grenze: ein Merge OHNE die git-generierte "Merge branch
    # 'agent/…'"-Message (z. B. --no-ff ohne --no-edit) wird NICHT erkannt —
    # in der Praxis irrelevant, weil mergeback.merge_back() --no-edit fest
    # verdrahtet hat, nie konfigurierbar.
    _git(repo, "checkout", "-q", "-b", "agent/jobslug")
    (repo / "vault" / "case" / "20260601.FooBar" / "output.md").write_text(
        "agent output", encoding="utf-8")
    _commit_as(repo, "bot", "bot@bibi.local", "agent: job output")
    agent_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "--no-ff", "-m", "abweichende Nachricht, kein Standardtext",
        "agent/jobslug")

    assert agent_sha not in agent_commit_shas(repo)


def test_agent_commit_shas_does_not_misclassify_ordinary_sync_merge(repo: Path, tmp_path: Path):
    # User-Fund 2026-07-06: "Agents ausblenden versteckt Sachen, die NUR ich
    # gemacht habe" — ein ganz normaler Mehrgeräte-Sync-Merge (Synchronizer,
    # strategy="merge") hat auch eine zweite Eltern-Linie, ist aber KEIN
    # agent/*-Merge. Nur Merges mit der git-generierten Message
    # "Merge branch 'agent/..." zählen als Agent-Herkunft.
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(repo), "other")
    (other / "human2.md").write_text("human work on other device", encoding="utf-8")
    _commit_as(other, "Carol", "carol@x.io", "vault: human work on other device")
    other_sha = _git(other, "rev-parse", "HEAD")

    (repo / "human1.md").write_text("human work here too", encoding="utf-8")
    _commit_as(repo, "Alice", "alice@x.io", "vault: human work here too")

    _git(repo, "fetch", "-q", str(other), "trunk")
    _git(repo, "merge", "--no-edit", "FETCH_HEAD")

    shas = agent_commit_shas(repo)
    assert other_sha not in shas


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


def test_aggregate_feed_last_commit_sha_is_the_newest_touching_commit(repo: Path):
    entities = {e.name: e for e in aggregate_feed(repo)}
    expected_sha = _git(repo, "log", "-1", "--format=%H", "--",
                        "vault/case/20260601.FooBar/README.md")
    assert entities["20260601.FooBar"].last_commit_sha == expected_sha


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


def test_heatmap_today_is_always_last_column():
    # PLAN-19 Befund 5, User-Entscheidung: rollierendes Fenster statt Mo-So —
    # "heute" (2026-07-08, ein Mittwoch) landet in Spalte 6 (letzte), egal
    # welcher Wochentag das gerade ist.
    grid = heatmap_buckets([_c(datetime.datetime(2026, 7, 8, 10, 0))], now=_NOW)
    assert grid[0][6][3] == 1  # Woche 0, Spalte 6 (heute), Stunde 10 → Bucket 3


def test_heatmap_six_days_ago_is_first_column():
    grid = heatmap_buckets([_c(datetime.datetime(2026, 7, 2, 0, 0))], now=_NOW)
    assert grid[0][0][0] == 1


def test_heatmap_seven_days_ago_starts_next_row_same_column():
    # 7 Tage vor heute = dieselbe Spalten-Position (6) wie heute, aber eine
    # Zeile weiter zurück (Woche 1) — Spalten sind relative Positionen zu
    # heute, keine festen Wochentage.
    grid = heatmap_buckets([_c(datetime.datetime(2026, 7, 1, 23, 30))], now=_NOW)
    assert grid[1][6][7] == 1


def test_heatmap_thirteen_days_ago_is_first_column_of_second_row():
    grid = heatmap_buckets([_c(datetime.datetime(2026, 6, 25, 0, 0))], now=_NOW)
    assert grid[1][0][0] == 1


def test_heatmap_drops_commits_outside_window():
    # 35+ Tage vor heute liegt außerhalb des 5-Wochen-Fensters (Woche-Index 5).
    grid = heatmap_buckets([_c(datetime.datetime(2026, 6, 1, 0, 0))], now=_NOW,
                          weeks=5)
    assert sum(sum(day) for week in grid for day in week) == 0


def test_heatmap_drops_future_commits_without_crashing():
    # Uhr-Drift zwischen Knoten (oder ein Commit "in der Zukunft" relativ zu
    # `now`) darf nicht negativ indizieren — einfach ignorieren.
    grid = heatmap_buckets([_c(datetime.datetime(2026, 7, 9, 0, 0))], now=_NOW)
    assert sum(sum(day) for week in grid for day in week) == 0


def test_heatmap_counts_multiple_commits_in_same_cell():
    commits = [_c(datetime.datetime(2026, 7, 8, 10, 0)),
              _c(datetime.datetime(2026, 7, 8, 11, 0))]
    grid = heatmap_buckets(commits, now=_NOW)
    assert grid[0][6][3] == 2


def test_heatmap_shape_default_five_weeks():
    grid = heatmap_buckets([], now=_NOW)
    assert len(grid) == 5
    assert all(len(week) == 7 for week in grid)
    assert all(len(day) == 8 for week in grid for day in week)


# --- activity_series_by_prefix (Bibi4-Iteration, Jobs-Sparkline) --------------
# User-Fund: "eine Sparkline, die die durch den Agenten verursachten git
# Änderungen repräsentiert" — Tages-Buckets je Job-Präfix, nur agent_shas
# zählen, dieselbe collect_commits()-Liste bedient alle Jobs auf einmal.


def _cp(dt: datetime.datetime, *, sha: str, paths: tuple[str, ...]) -> CommitInfo:
    return CommitInfo(sha=sha, author="a", epoch=dt.timestamp(), paths=paths)


def test_activity_series_counts_only_agent_commits():
    commits = [
        _cp(datetime.datetime(2026, 7, 8, 10, 0), sha="agent1",
           paths=("vault/case/foo/job.md",)),
        _cp(datetime.datetime(2026, 7, 8, 11, 0), sha="human1",
           paths=("vault/case/foo/job.md",)),
    ]
    series = activity_series_by_prefix(
        commits, {"agent1"}, {"foo": "vault/case/foo/"}, since_days=30, now=_NOW)
    assert sum(series["foo"]) == 1


def test_activity_series_matches_by_path_prefix_not_exact_file():
    # Andere Dateien im selben Case-Ordner (nicht nur job.md) zählen mit.
    commits = [_cp(datetime.datetime(2026, 7, 8, 10, 0), sha="agent1",
                  paths=("vault/case/foo/notes.md",))]
    series = activity_series_by_prefix(
        commits, {"agent1"}, {"foo": "vault/case/foo/"}, since_days=30, now=_NOW)
    assert sum(series["foo"]) == 1


def test_activity_series_separates_jobs_by_prefix():
    commits = [
        _cp(datetime.datetime(2026, 7, 8, 10, 0), sha="agent1",
           paths=("vault/case/foo/job.md",)),
        _cp(datetime.datetime(2026, 7, 8, 10, 0), sha="agent2",
           paths=("vault/case/bar/job.md",)),
    ]
    series = activity_series_by_prefix(
        commits, {"agent1", "agent2"},
        {"foo": "vault/case/foo/", "bar": "vault/case/bar/"}, since_days=30, now=_NOW)
    assert sum(series["foo"]) == 1
    assert sum(series["bar"]) == 1


def test_activity_series_today_is_last_bucket():
    commits = [_cp(datetime.datetime(2026, 7, 8, 10, 0), sha="agent1",
                  paths=("vault/case/foo/job.md",))]
    series = activity_series_by_prefix(
        commits, {"agent1"}, {"foo": "vault/case/foo/"}, since_days=30, now=_NOW)
    assert series["foo"][-1] == 1
    assert sum(series["foo"][:-1]) == 0


def test_activity_series_drops_commits_outside_window():
    commits = [_cp(datetime.datetime(2026, 5, 1, 0, 0), sha="agent1",
                  paths=("vault/case/foo/job.md",))]
    series = activity_series_by_prefix(
        commits, {"agent1"}, {"foo": "vault/case/foo/"}, since_days=30, now=_NOW)
    assert sum(series["foo"]) == 0


def test_activity_series_shape_matches_since_days_and_prefixes():
    series = activity_series_by_prefix(
        [], set(), {"foo": "vault/case/foo/", "bar": "vault/case/bar/"},
        since_days=30, now=_NOW)
    assert set(series) == {"foo", "bar"}
    assert len(series["foo"]) == 30 and len(series["bar"]) == 30


# --- GET /-/feed (rollenunabhängig, PLAN-18) ------------------------------------


def test_feed_endpoint_works_without_any_role(repo: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    monkeypatch.chdir(repo)
    from bibi import repo as repo_mod
    repo_mod._root_of.cache_clear()

    app = create_app(roles.resolve(set()))
    with TestClient(app) as c:
        r = c.get("/-/feed")
        assert r.status_code == 200
        body = r.json()
        assert {"20260601.FooBar", "CONVENTIONS.md", "System"} == {
            e["name"] for e in body["entities"]
        }
        assert len(body["heatmap"]) == 5
        assert body["since_days"] is None


def test_feed_endpoint_days_param(repo: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    monkeypatch.chdir(repo)
    from bibi import repo as repo_mod
    repo_mod._root_of.cache_clear()

    app = create_app(roles.resolve(set()))
    with TestClient(app) as c:
        r = c.get("/-/feed", params={"days": 3650})
        assert r.status_code == 200
        assert r.json()["since_days"] == 3650
        assert len(r.json()["entities"]) == 3


def test_feed_endpoint_weeks_param_decoupled_from_days(repo: Path, monkeypatch):
    # PLAN-20 Befund 3, User-Fund: Heatmap unabhängig von der Liste nachladbar
    # — weeks steuert NUR die Heatmap-Zeilenzahl, days bleibt unverändert das
    # Fenster der Änderungsliste. Ein kleines days-Fenster darf die Heatmap
    # nicht (mehr) leerfegen, wenn weeks größer gewählt ist.
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    monkeypatch.chdir(repo)
    from bibi import repo as repo_mod
    repo_mod._root_of.cache_clear()

    app = create_app(roles.resolve(set()))
    with TestClient(app) as c:
        r = c.get("/-/feed", params={"days": 1, "weeks": 8})
        assert r.status_code == 200
        body = r.json()
        assert body["since_days"] == 1
        assert body["weeks"] == 8
        assert len(body["heatmap"]) == 8


def test_feed_endpoint_weeks_defaults_to_heatmap_weeks(repo: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    from bibi.feed import HEATMAP_WEEKS

    monkeypatch.chdir(repo)
    from bibi import repo as repo_mod
    repo_mod._root_of.cache_clear()

    app = create_app(roles.resolve(set()))
    with TestClient(app) as c:
        r = c.get("/-/feed")
        assert r.json()["weeks"] == HEATMAP_WEEKS


# --- remote_commit_base_url ----------------------------------------------------


def test_remote_commit_base_url_strips_dot_git(repo: Path):
    _git(repo, "remote", "add", "origin",
        "http://sarasate.tail9f9173.ts.net:3000/m.rau/bibi-notes.git")
    assert (remote_commit_base_url(repo)
           == "http://sarasate.tail9f9173.ts.net:3000/m.rau/bibi-notes")


def test_remote_commit_base_url_none_without_origin(repo: Path):
    assert remote_commit_base_url(repo) is None
