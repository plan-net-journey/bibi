"""local_files_status(): Git-Status je Pfad (neu/geändert/unverändert) für
PLAN-21 Befund 10 (Jobs-Screen: lokale Repository-Realität statt Remote-
Abgleich). Nutzt das leichte ``team_repo``-Fixture (reines ``git init``, kein
Origin/Clone) — schnell, kein ``--slow``, anders als ``test_git_status.py``s
Sync-Szenarien (ahead/behind brauchen einen echten Remote)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bibi.git_status import local_files_status


def _sh(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit_all(root: Path, msg: str = "init") -> None:
    _sh(root, "add", "-A")
    _sh(root, "commit", "-q", "-m", msg)


def test_untracked_file_is_new(team_repo: Path):
    (team_repo / "vault/case/a.md").write_text("x", encoding="utf-8")
    result = local_files_status(team_repo, ["vault/case/a.md"])
    assert result == {"vault/case/a.md": "new"}


def test_committed_unchanged_file_is_clean(team_repo: Path):
    (team_repo / "vault/case/a.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    result = local_files_status(team_repo, ["vault/case/a.md"])
    assert result == {"vault/case/a.md": "clean"}


def test_committed_then_edited_file_is_modified(team_repo: Path):
    (team_repo / "vault/case/a.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    (team_repo / "vault/case/a.md").write_text("y", encoding="utf-8")
    result = local_files_status(team_repo, ["vault/case/a.md"])
    assert result == {"vault/case/a.md": "modified"}


def test_deleted_file_not_reported_as_own_status():
    # User-Fund: "Gelöschte MDs werden nicht mehr angezeigt" — kein Status
    # dafür nötig, die Funktion bekommt gelöschte Pfade schlicht nie als
    # `paths` übergeben (discovery.discover() findet sie nicht mehr). Diese
    # Funktion selbst hat also keinen "deleted"-Fall im Rückgabewert.
    assert "deleted" not in local_files_status.__doc__


def test_mixed_paths_get_independent_status(team_repo: Path):
    (team_repo / "vault/case/clean.md").write_text("x", encoding="utf-8")
    (team_repo / "vault/case/modified.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    (team_repo / "vault/case/modified.md").write_text("y", encoding="utf-8")
    (team_repo / "vault/case/new.md").write_text("z", encoding="utf-8")
    result = local_files_status(
        team_repo, ["vault/case/clean.md", "vault/case/modified.md", "vault/case/new.md"])
    assert result == {
        "vault/case/clean.md": "clean",
        "vault/case/modified.md": "modified",
        "vault/case/new.md": "new",
    }


def test_path_not_touched_by_git_defaults_to_clean_even_if_untracked_elsewhere(team_repo: Path):
    # Ein Pfad, den git status gar nicht meldet (z. B. weil eine andere Datei
    # geändert ist, diese hier aber nicht), muss trotzdem im Ergebnis stehen —
    # mit "clean", nicht fehlend.
    (team_repo / "vault/case/a.md").write_text("x", encoding="utf-8")
    (team_repo / "vault/case/b.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    (team_repo / "vault/case/b.md").write_text("y", encoding="utf-8")
    result = local_files_status(team_repo, ["vault/case/a.md"])
    assert result == {"vault/case/a.md": "clean"}


def test_empty_paths_returns_empty_dict(team_repo: Path):
    assert local_files_status(team_repo, []) == {}


def test_unmerged_conflict_reported_as_own_status_not_modified(team_repo: Path):
    # Bibi4-Iteration, User-Fund: "sind sie lokal modifiziert, konfliktär,
    # fehlen?" — ein Merge-Konflikt (Porcelain-v2 "u "-Zeile) landete zuvor
    # ununterscheidbar im selben "modified"-Topf wie eine gewöhnliche Änderung.
    path = team_repo / "vault/case/a.md"
    path.write_text("base\n", encoding="utf-8")
    _commit_all(team_repo)
    _sh(team_repo, "checkout", "-q", "-b", "other")
    path.write_text("from other branch\n", encoding="utf-8")
    _commit_all(team_repo, "other change")
    _sh(team_repo, "checkout", "-q", "trunk")
    path.write_text("from trunk\n", encoding="utf-8")
    _commit_all(team_repo, "trunk change")
    subprocess.run(["git", "merge", "-q", "other"], cwd=team_repo, capture_output=True, text=True)
    result = local_files_status(team_repo, ["vault/case/a.md"])
    assert result == {"vault/case/a.md": "conflict"}


def test_modified_path_with_space_is_reported_correctly(team_repo: Path):
    # User-Fund 2026-07-20: "Runner 1"/"Runner 5" (Pfade mit Leerzeichen)
    # wurden trotz echter Änderung als "clean" gemeldet — ein unbegrenzter
    # `.split(" ")[-1]` zerschnitt den Pfad selbst an seinem eigenen
    # Leerzeichen und lieferte nur dessen letztes Wort als Dict-Key.
    (team_repo / "vault/case/Runner 1.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    (team_repo / "vault/case/Runner 1.md").write_text("y", encoding="utf-8")
    result = local_files_status(team_repo, ["vault/case/Runner 1.md"])
    assert result == {"vault/case/Runner 1.md": "modified"}


def test_conflict_path_with_space_is_reported_correctly(team_repo: Path):
    path = team_repo / "vault/case/Runner 1.md"
    path.write_text("base\n", encoding="utf-8")
    _commit_all(team_repo)
    _sh(team_repo, "checkout", "-q", "-b", "other")
    path.write_text("from other branch\n", encoding="utf-8")
    _commit_all(team_repo, "other change")
    _sh(team_repo, "checkout", "-q", "trunk")
    path.write_text("from trunk\n", encoding="utf-8")
    _commit_all(team_repo, "trunk change")
    subprocess.run(["git", "merge", "-q", "other"], cwd=team_repo, capture_output=True, text=True)
    result = local_files_status(team_repo, ["vault/case/Runner 1.md"])
    assert result == {"vault/case/Runner 1.md": "conflict"}


def test_outside_git_repo_all_clean_no_crash(tmp_path: Path):
    # Kein Git-Repo (git-Aufruf schlägt fehl, returncode != 0) — defensiv:
    # kein Crash, alle angefragten Pfade "clean" statt eines Fehlers.
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    result = local_files_status(tmp_path, ["a.md"])
    assert result == {"a.md": "clean"}


# ── dirty_files(): alle offenen Aenderungen, nicht nur die erfragten (#133) ─
#
# `local_files_status()` beantwortet „wie steht es um DIESE Pfade" — der Feed
# fragt umgekehrt „was ist ueberhaupt offen". Beide lesen denselben
# `git status`; getrennt sind nur die Fragen.


from bibi.git_status import dirty_files  # noqa: E402


def test_dirty_files_reports_new_modified_and_deleted(team_repo: Path):
    """`deleted` ist der Fall, den `local_files_status()` bewusst nicht kennt:
    eine geloeschte Job-MD verschwindet dort von selbst aus der Liste. Im Feed
    ist genau ihr Verschwinden die Nachricht."""
    (team_repo / "vault/case/bleibt.md").write_text("x", encoding="utf-8")
    (team_repo / "vault/case/geht.md").write_text("x", encoding="utf-8")
    (team_repo / "vault/case/aendert.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    (team_repo / "vault/case/geht.md").unlink()
    (team_repo / "vault/case/aendert.md").write_text("y", encoding="utf-8")
    (team_repo / "vault/case/neu.md").write_text("z", encoding="utf-8")
    assert dirty_files(team_repo) == {
        "vault/case/geht.md": "deleted",
        "vault/case/aendert.md": "modified",
        "vault/case/neu.md": "new",
    }


def test_dirty_files_is_empty_on_a_clean_tree(team_repo: Path):
    (team_repo / "vault/case/a.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    assert dirty_files(team_repo) == {}


def test_local_files_status_still_hides_deletions(team_repo: Path):
    """Die Gegenprobe zur gemeinsamen Basis: dass beide denselben `git status`
    lesen, darf die aeltere Frage nicht umdeuten. Eine geloeschte Job-MD war
    dort nie ein eigener Zustand."""
    (team_repo / "vault/case/weg.md").write_text("x", encoding="utf-8")
    _commit_all(team_repo)
    (team_repo / "vault/case/weg.md").unlink()
    assert local_files_status(team_repo, ["vault/case/weg.md"]) == {
        "vault/case/weg.md": "modified"}
