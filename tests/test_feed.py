"""Feed-Datenquelle: Git-Historie → Einheiten + Urheber. Reine Funktionen und
ein echtes Scratch-Repo, kein Mock.

Kein modulweiter ``slow``-Marker (Umbauplan §6): die Scratch-Repos hier laufen
allesamt unter einer Sekunde, und ein modulweiter Marker nimmt eine ganze Datei
aus der Fast-Suite — genau so blieb ``test_push_when_ahead`` monatelang falsch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi.feed import (
    CommitInfo, agent_slugs, aggregate_feed, collect_commits, discover_cases,
    group_entries, remote_commit_base_url, unit_for_path,
)


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _commit_as(root: Path, author_name: str, author_email: str, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
        "commit", "-q", "-m", msg)


def _case(root: Path, rel: str, slug: str) -> None:
    """Ein echter Case: Ordner mit README.md, die `slug:` im Frontmatter führt."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text(
        f"---\nslug: {slug}\nstatus: open\n---\n\n# {slug}\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _case(root, "vault/case/20260601.FooBar-aa11", "FooBar")
    _commit_as(root, "Alice", "alice@x.io", "case: init FooBar")
    (root / "vault" / "CONVENTIONS.md").write_text("y", encoding="utf-8")
    _commit_as(root, "Bob", "bob@x.io", "vault: add conventions")
    (root / "pyproject.toml").write_text("[project]", encoding="utf-8")
    _commit_as(root, "Alice", "alice@x.io", "system: add pyproject")
    return root


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Ein Vault mit den Formen, die live vorkommen — verschachtelte Cases, ein
    Jahres-Archivordner, Sammelordner ohne eigene README, tiefe memo-Ablage."""
    root = tmp_path / "v"
    _case(root, "vault/case/20260601.FooBar-aa11", "FooBar")
    _case(root, "vault/case/20260601.FooBar-aa11/20260701.Inner-cc33", "Inner")
    _case(root, "vault/case/2026/20260501.Alt-bb22", "Alt")
    (root / "vault/memo/Release").mkdir(parents=True)
    (root / "vault/memo/DailyDigest/2026/07").mkdir(parents=True)
    return root


# --- collect_commits ----------------------------------------------------------

def test_collect_commits_returns_all_three(repo: Path):
    commits = collect_commits(repo)
    assert len(commits) == 3
    assert {c.author for c in commits} == {"Alice", "Bob"}


def test_collect_commits_paths_present(repo: Path):
    all_paths = {p for c in collect_commits(repo) for p in c.paths}
    assert "vault/case/20260601.FooBar-aa11/README.md" in all_paths
    assert "vault/CONVENTIONS.md" in all_paths
    assert "pyproject.toml" in all_paths


def test_collect_commits_since_days_excludes_old(repo: Path):
    assert len(collect_commits(repo, since_days=3650)) == 3


# --- agent_slugs --------------------------------------------------------------

def test_agent_slugs_empty_without_agent_branch(repo: Path):
    assert agent_slugs(repo) == {}


def test_agent_slugs_maps_commit_to_the_job_that_wrote_it(repo: Path):
    # Der Slug steht in der git-generierten Merge-Message und ist die einzige
    # verlaessliche Angabe, WELCHER Job geschrieben hat: der git-Autor ist mal
    # `bibi/<slug>`, mal `m.rau` — je nachdem, unter welcher Identitaet der Job
    # committet hat.
    _git(repo, "checkout", "-q", "-b", "agent/news-aggregator")
    (repo / "vault" / "memo").mkdir(parents=True, exist_ok=True)
    (repo / "vault" / "memo" / "News.md").write_text("n", encoding="utf-8")
    _commit_as(repo, "bibi/news-aggregator", "bot@x.io", "job output")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "-c", "user.name=m.rau", "-c", "user.email=m@x.io",
        "merge", "--no-ff", "--no-edit", "-q", "agent/news-aggregator")

    slugs = agent_slugs(repo)
    assert slugs.get(sha) == "news-aggregator"
    # die drei urspruenglichen trunk-Commits sind NICHT agent-Herkunft
    assert {c.sha for c in collect_commits(repo)}.isdisjoint(set(slugs) - {sha})


def test_agent_slugs_accepted_limitation_needs_default_merge_message(repo: Path):
    # Bewusst akzeptierte Grenze: ein Merge OHNE die git-generierte
    # "Merge branch 'agent/…'"-Message wird nicht erkannt. In der Praxis
    # irrelevant, weil mergeback.merge_back() `--no-edit` fest verdrahtet hat.
    # Die Alternative (Branch-Containment) ist live widerlegt, s. agent_slugs().
    _git(repo, "checkout", "-q", "-b", "agent/jobslug")
    (repo / "vault" / "case" / "20260601.FooBar-aa11" / "output.md").write_text(
        "agent output", encoding="utf-8")
    _commit_as(repo, "bot", "bot@bibi.local", "agent: job output")
    agent_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "--no-ff", "-m", "abweichende Nachricht, kein Standardtext",
        "agent/jobslug")

    assert agent_sha not in agent_slugs(repo)


def test_agent_slugs_does_not_misclassify_ordinary_sync_merge(repo: Path, tmp_path: Path):
    # Ein Mehrgeraete-Sync-Merge des Synchronizers hat auch eine zweite
    # Elternlinie, ist aber kein agent/*-Merge. Frueherer Fehler: "Agents
    # ausblenden versteckt Sachen, die NUR ich gemacht habe".
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(repo), "other")
    (other / "human2.md").write_text("human work on other device", encoding="utf-8")
    _commit_as(other, "Carol", "carol@x.io", "vault: human work on other device")
    other_sha = _git(other, "rev-parse", "HEAD")

    (repo / "human1.md").write_text("human work here too", encoding="utf-8")
    _commit_as(repo, "Alice", "alice@x.io", "vault: human work here too")

    _git(repo, "fetch", "-q", str(other), "trunk")
    _git(repo, "merge", "--no-edit", "FETCH_HEAD")

    assert other_sha not in agent_slugs(repo)


# --- Die Einheit: Ordner, nicht gemischt Ordner und Datei ---------------------
# FE-Spezifikation §3. Ein Case ist die Einheit, in der gearbeitet wird —
# erkannt an seiner README.md mit `slug:` im Frontmatter, derselben Definition,
# die /open benutzt.

def test_case_folder_is_the_unit(vault: Path):
    cases = discover_cases(vault)
    assert unit_for_path("vault/case/20260601.FooBar-aa11/README.md",
                         cases=cases) == "20260601.FooBar-aa11"


def test_subfolder_inside_a_case_still_belongs_to_the_case(vault: Path):
    # Ein Case zerfaellt nicht in attach/, collectors/ — er ist EINE Arbeitseinheit.
    cases = discover_cases(vault)
    assert unit_for_path("vault/case/20260601.FooBar-aa11/attach/bild.md",
                         cases=cases) == "20260601.FooBar-aa11"


def test_nested_case_wins_over_its_container(vault: Path):
    # Live: 20260802.Bibi5 liegt IN 20260621.Bibi4. Ohne diese Regel verschwindet
    # die gesamte Bibi5-Arbeit unter dem Namen des aeusseren Ordners.
    cases = discover_cases(vault)
    assert unit_for_path(
        "vault/case/20260601.FooBar-aa11/20260701.Inner-cc33/Notiz.md",
        cases=cases) == "20260701.Inner-cc33"


def test_year_archive_folder_is_not_a_unit(vault: Path):
    # Live sammelt `case/2026` Dutzende abgeschlossener Cases in EINE Zeile.
    cases = discover_cases(vault)
    assert unit_for_path("vault/case/2026/20260501.Alt-bb22/README.md",
                         cases=cases) == "20260501.Alt-bb22"


def test_moved_case_keeps_one_unit(vault: Path):
    # git fuehrt die Historie unter dem alten Pfad weiter. Aggregiert wird auf
    # den ORDNERNAMEN, sonst erscheint ein verschobener Case zweimal.
    cases = discover_cases(vault)
    alt = unit_for_path("vault/case/20260501.Alt-bb22/x.md", cases=cases)
    neu = unit_for_path("vault/case/2026/20260501.Alt-bb22/x.md", cases=cases)
    assert alt == neu == "20260501.Alt-bb22"


def test_folder_below_a_top_level_directory_is_the_unit(vault: Path):
    cases = discover_cases(vault)
    assert unit_for_path("vault/memo/Release/v030.md", cases=cases) == "memo/Release"


def test_deep_path_is_capped_at_two_levels(vault: Path):
    # memo/DailyDigest liegt teils flach, teils nach Jahr/Monat — eine Sache,
    # die sonst auf zwei Ebenen nebeneinander erschiene.
    cases = discover_cases(vault)
    assert unit_for_path("vault/memo/DailyDigest/2026/07/01.md",
                         cases=cases) == "memo/DailyDigest"


def test_file_directly_in_a_top_level_directory_stays_its_own_row(vault: Path):
    # Ein Top-Level-Ordner ist eine Ablage-Ebene, keine Arbeitseinheit — eine
    # Sammelzeile `memo` waere der SYSTEM-Fehler von vorne.
    cases = discover_cases(vault)
    assert unit_for_path("vault/memo/202608.Billing.md",
                         cases=cases) == "memo/202608.Billing.md"


def test_loose_file_at_the_vault_root_stays_its_own_row(vault: Path):
    cases = discover_cases(vault)
    assert unit_for_path("vault/CONVENTIONS.md", cases=cases) == "CONVENTIONS.md"


def test_non_markdown_has_no_unit(vault: Path):
    cases = discover_cases(vault)
    assert unit_for_path("vault/case/20260601.FooBar-aa11/bild.png", cases=cases) is None


def test_path_outside_the_vault_has_no_unit(vault: Path):
    # Die Kategorie SYSTEM entfaellt ersatzlos (FE §3).
    cases = discover_cases(vault)
    assert unit_for_path("pyproject.toml", cases=cases) is None


def test_discover_cases_ignores_folders_without_slug_frontmatter(vault: Path):
    # `case/2026` und ein Gruppenordner tragen keine README mit slug: — genau
    # daran unterscheiden sie sich von einem Case, ohne Namensmuster.
    cases = discover_cases(vault)
    assert cases == {"20260601.FooBar-aa11", "20260701.Inner-cc33", "20260501.Alt-bb22"}
    assert "2026" not in cases


def test_discover_cases_respects_custom_case_dir_name(tmp_path: Path):
    # bibi3-Kompat: case_dir_name() kann "project" statt "case" sein.
    root = tmp_path / "p"
    _case(root, "vault/project/20260601.Foo-aa11", "Foo")
    assert discover_cases(root, case_dir_name="project") == {"20260601.Foo-aa11"}
    assert discover_cases(root) == set()


# --- group_entries ------------------------------------------------------------

def _c(sha: str, epoch: float, author: str, *paths: str) -> CommitInfo:
    return CommitInfo(sha=sha, author=author, epoch=epoch, paths=tuple(paths))


def test_group_entries_counts_changes_per_unit(vault: Path):
    cases = discover_cases(vault)
    commits = [
        _c("a", 100.0, "m.rau", "vault/case/20260601.FooBar-aa11/README.md",
           "vault/case/20260601.FooBar-aa11/attach/x.md"),
        _c("b", 200.0, "m.rau", "vault/case/20260601.FooBar-aa11/Notiz.md"),
    ]
    rows = group_entries(commits, {}, cases=cases)
    assert [(r.unit, r.changes) for r in rows] == [("20260601.FooBar-aa11", 3)]


def test_group_entries_uses_the_job_slug_as_author(vault: Path):
    cases = discover_cases(vault)
    commits = [_c("a", 100.0, "m.rau", "vault/memo/News/x.md")]
    rows = group_entries(commits, {"a": "news-aggregator"}, cases=cases)
    assert rows[0].authors == frozenset({"news-aggregator"})


def test_group_entries_strips_the_bibi_prefix_from_the_git_author(vault: Path):
    # git-Autor `bibi/<slug>` und Merge-Slug sind dieselbe Angabe in zwei
    # Schreibweisen — sonst steht der Urheber doppelt in der Zeile.
    cases = discover_cases(vault)
    commits = [_c("a", 100.0, "bibi/gmail-billing", "vault/memo/Billing/x.md")]
    rows = group_entries(commits, {}, cases=cases)
    assert rows[0].authors == frozenset({"gmail-billing"})


def test_group_entries_sorted_newest_first(vault: Path):
    cases = discover_cases(vault)
    commits = [
        _c("a", 100.0, "m.rau", "vault/memo/Release/x.md"),
        _c("b", 300.0, "m.rau", "vault/CONVENTIONS.md"),
    ]
    rows = group_entries(commits, {}, cases=cases)
    assert [r.unit for r in rows] == ["CONVENTIONS.md", "memo/Release"]


def test_group_entries_last_commit_is_the_newest_touching_one(vault: Path):
    cases = discover_cases(vault)
    commits = [
        _c("alt", 100.0, "m.rau", "vault/memo/Release/x.md"),
        _c("neu", 300.0, "m.rau", "vault/memo/Release/y.md"),
    ]
    rows = group_entries(commits, {}, cases=cases)
    assert rows[0].last_commit_sha == "neu" and rows[0].last_changed == 300.0


def test_group_entries_drops_paths_without_a_unit(vault: Path):
    cases = discover_cases(vault)
    commits = [_c("a", 100.0, "m.rau", "pyproject.toml", "vault/case/x/bild.png")]
    assert group_entries(commits, {}, cases=cases) == []


# --- aggregate_feed -----------------------------------------------------------

def test_aggregate_feed_one_row_per_unit(repo: Path):
    # pyproject.toml liegt ausserhalb von vault/ und erscheint nicht mehr.
    assert {e.unit for e in aggregate_feed(repo)} == {
        "20260601.FooBar-aa11", "CONVENTIONS.md"}


def test_aggregate_feed_authors_per_unit(repo: Path):
    units = {e.unit: e for e in aggregate_feed(repo)}
    assert units["20260601.FooBar-aa11"].authors == frozenset({"Alice"})
    assert units["CONVENTIONS.md"].authors == frozenset({"Bob"})


def test_aggregate_feed_sorted_newest_first(repo: Path):
    stamps = [e.last_changed for e in aggregate_feed(repo)]
    assert stamps == sorted(stamps, reverse=True)


def test_aggregate_feed_last_commit_sha_is_the_newest_touching_commit(repo: Path):
    units = {e.unit: e for e in aggregate_feed(repo)}
    expected = _git(repo, "log", "-1", "--format=%H", "--",
                    "vault/case/20260601.FooBar-aa11/README.md")
    assert units["20260601.FooBar-aa11"].last_commit_sha == expected


def test_aggregate_feed_names_the_job_that_wrote_a_change(repo: Path):
    _git(repo, "checkout", "-q", "-b", "agent/daily-digest")
    (repo / "vault" / "case" / "20260601.FooBar-aa11" / "output.md").write_text(
        "agent output", encoding="utf-8")
    _commit_as(repo, "m.rau", "m@x.io", "agent: job output")
    _git(repo, "checkout", "-q", "trunk")
    _git(repo, "merge", "--no-ff", "--no-edit", "-q", "agent/daily-digest")

    units = {e.unit: e for e in aggregate_feed(repo)}
    assert "daily-digest" in units["20260601.FooBar-aa11"].authors


# --- GET /-/feed (rollenunabhängig) -------------------------------------------

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
        assert {"20260601.FooBar-aa11", "CONVENTIONS.md"} == {
            e["unit"] for e in body["entries"]
        }
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
        assert len(r.json()["entries"]) == 2


def test_feed_endpoint_carries_changes_and_authors(repo: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    monkeypatch.chdir(repo)
    from bibi import repo as repo_mod
    repo_mod._root_of.cache_clear()

    app = create_app(roles.resolve(set()))
    with TestClient(app) as c:
        rows = {e["unit"]: e for e in c.get("/-/feed").json()["entries"]}
        assert rows["CONVENTIONS.md"]["changes"] == 1
        assert rows["CONVENTIONS.md"]["authors"] == ["Bob"]


# --- remote_commit_base_url ---------------------------------------------------

def test_remote_commit_base_url_strips_dot_git(repo: Path):
    _git(repo, "remote", "add", "origin",
        "http://sarasate.tail9f9173.ts.net:3000/m.rau/bibi-notes.git")
    assert (remote_commit_base_url(repo)
           == "http://sarasate.tail9f9173.ts.net:3000/m.rau/bibi-notes")


def test_remote_commit_base_url_none_without_origin(repo: Path):
    assert remote_commit_base_url(repo) is None


def test_agent_slugs_needs_a_constant_number_of_git_calls(repo: Path, monkeypatch):
    """Ein ``rev-list`` **je** Merge macht das Fenster unbenutzbar.

    Live gemessen: 30 Tage brauchten 9,7 s allein hier, bei 703 Merges — der
    Controller-Selbstaufruf bricht aber nach 5 s ab und zeigt dann einen leeren
    Feed. LOAD MORE lief damit ab etwa zwoelf Klicks ins Leere.

    Die Zahl der Aufrufe darf deshalb nicht mit der Zahl der Merges wachsen.
    Rot war ``5 <= 4`` bei drei Agent-Merges.
    """
    for i in range(3):
        _git(repo, "checkout", "-q", "-b", f"agent/job{i}")
        (repo / "vault" / "memo").mkdir(parents=True, exist_ok=True)
        (repo / "vault" / "memo" / f"o{i}.md").write_text("x", encoding="utf-8")
        _commit_as(repo, "bot", "bot@x.io", f"job {i}")
        _git(repo, "checkout", "-q", "trunk")
        _git(repo, "merge", "--no-ff", "--no-edit", "-q", f"agent/job{i}")

    from bibi import feed as feed_mod
    aufrufe = []
    echt = feed_mod._run_git
    monkeypatch.setattr(feed_mod, "_run_git",
                        lambda root, args: (aufrufe.append(args), echt(root, args))[1])
    slugs = feed_mod.agent_slugs(repo)

    assert len(aufrufe) <= 2, f"{len(aufrufe)} git-Aufrufe fuer 3 Merges: {aufrufe}"
    assert set(slugs.values()) == {"job0", "job1", "job2"}


def test_agent_slugs_does_not_swallow_a_foreign_trunk_line(repo: Path, tmp_path: Path):
    """Ein Agent-Merge, der selbst auf einer Seitenlinie liegt, darf nicht die
    ganze fremde trunk-Linie erben.

    Das ist der Normalfall in diesem System: der Job laeuft auf sarasate, wird
    dort gemergt, und der Merge kommt per Sync auf den Mac — dort liegt er
    NICHT auf der lokalen First-Parent-Linie. Wer von seiner Branch-Spitze
    rueckwaerts laeuft und nur an der lokalen First-Parent-Linie stoppt,
    traversiert die komplette fremde Linie mit.

    Live gefunden beim Abgleich gegen die alte Implementierung: menschliche
    Commits (`save: bibi-notes`, `chore: bibi-Engine-Abhaengigkeit …`) standen
    unter dem Slug `Witz`. Rot war ``'Witz' is not None`` fuer den fremden
    Commit.
    """
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(repo), "other")

    # Auf dem anderen Knoten: ein menschlicher Commit, dann ein Job-Lauf mit
    # Mergeback — beides auf DESSEN trunk.
    (other / "human.md").write_text("auf einem anderen Geraet", encoding="utf-8")
    _commit_as(other, "Carol", "carol@x.io", "save: bibi-notes")
    fremd = _git(other, "rev-parse", "HEAD")

    _git(other, "checkout", "-q", "-b", "agent/Witz")
    (other / "vault" / "memo").mkdir(parents=True, exist_ok=True)
    (other / "vault" / "memo" / "witz.md").write_text("w", encoding="utf-8")
    _commit_as(other, "bot", "bot@x.io", "job output")
    eigen = _git(other, "rev-parse", "HEAD")
    _git(other, "checkout", "-q", "trunk")
    _git(other, "merge", "--no-ff", "--no-edit", "-q", "agent/Witz")

    # Hier passiert derweil auch etwas, damit der Sync ein echter Merge wird.
    (repo / "hier.md").write_text("hier", encoding="utf-8")
    _commit_as(repo, "Alice", "alice@x.io", "save: bibi-notes")
    _git(repo, "fetch", "-q", str(other), "trunk")
    _git(repo, "merge", "--no-edit", "-q", "FETCH_HEAD")

    slugs = agent_slugs(repo)
    assert slugs.get(eigen) == "Witz"
    assert slugs.get(fremd) is None, \
        f"der fremde trunk-Commit wurde {slugs.get(fremd)!r} zugeschlagen"


def test_group_entries_folds_pinned_run_slugs_into_their_job(vault: Path):
    """Ein gepinnter Lauf ist derselbe Job, kein eigener Urheber.

    Befund m.rau am Feed-Screenshot: die Urheberliste lautete
    `m.rau, news-aggregator, news-aggregator-15c7c078, news-aggregator-8791cd62,
    sync` — das sind keine fuenf Urheber, sondern drei. `run_pinned()` haengt je
    Lauf acht Hex-Zeichen an; im Archive ist der Fix seit `bucket_slug()` da,
    im Feed fehlte er.

    Rot war: `{'news-aggregator', 'news-aggregator-15c7c078'}` statt
    `{'news-aggregator'}`.
    """
    cases = discover_cases(vault)
    commits = [
        _c("a", 100.0, "m.rau", "vault/memo/News/1.md"),
        _c("b", 200.0, "m.rau", "vault/memo/News/2.md"),
    ]
    slugs = {"a": "news-aggregator", "b": "news-aggregator-15c7c078"}
    rows = group_entries(commits, slugs, cases=cases)
    assert rows[0].authors == frozenset({"news-aggregator"})


def test_group_entries_leaves_a_real_slug_alone(vault: Path):
    """Die Gegenprobe: ein echter Slug darf auf acht Hex-Zeichen enden."""
    cases = discover_cases(vault)
    commits = [_c("a", 100.0, "m.rau", "vault/memo/News/1.md")]
    rows = group_entries(commits, {"a": "20260728.at-150738-81ec"}, cases=cases)
    assert rows[0].authors == frozenset({"20260728.at-150738-81ec"})
