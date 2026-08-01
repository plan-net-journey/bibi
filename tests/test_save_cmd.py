"""Integrationstests für `bibi-ctrl save` (A10-Scope + Sync-Matrix §4.9)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from bibi import case_store, state
from bibi.ctrl import main

import pytest
pytestmark = pytest.mark.slow


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _origin_head(origin: Path) -> str:
    return _sh(origin, "log", "-1", "--pretty=%s").strip()


def _local_head(root: Path) -> str:
    return _sh(root, "log", "-1", "--pretty=%s").strip()


# --- A10: Scope ---

def test_save_no_active_case_full_scope(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    rc = main(["save", "--push"])
    assert rc == 0
    files = _sh(root, "show", "--name-only", "--pretty=format:", "HEAD")
    assert "a.txt" in files and "b.txt" in files  # ganzes Repo


def test_save_active_case_path_scope(repo_with_origin):
    root, origin = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    (root / "outside.txt").write_text("x", encoding="utf-8")  # scope-fremd
    os.chdir(folder)  # Case parken → aktiver Case
    rc = main(["save", "--push"])
    assert rc == 0
    files = _sh(root, "show", "--name-only", "--pretty=format:", "HEAD")
    assert any("AlphaFeature" in f for f in files.splitlines())
    assert "outside.txt" not in files
    assert "save: 20" in _local_head(root)  # "save: <YYYYmmdd…folder>"


def test_save_stays_case_scoped_after_cwd_left_the_case(repo_with_origin, monkeypatch):
    """Der Kern der Session-Park-Marke: ``/open`` parkt, irgendein späterer
    ``cd`` verlässt den Case — und ``save`` committet trotzdem nur den Case
    statt still auf das ganze Repo umzuschwenken."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    root, _ = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    os.chdir(folder)
    state.set_path(f"case/{folder.name}")  # wie /open
    (root / "outside.txt").write_text("x", encoding="utf-8")

    os.chdir(root)  # cwd weg — früher hieß das: Repo-Scope
    assert main(["save", "--push"]) == 0
    files = _sh(root, "show", "--name-only", "--pretty=format:", "HEAD")
    assert "outside.txt" not in files
    assert any("AlphaFeature" in f for f in files.splitlines())


def test_save_repo_flag_forces_full_scope_despite_active_case(repo_with_origin, monkeypatch):
    """Das Gegenstück: Repo-Scope ist jetzt eine Ansage, kein Nebenprodukt."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    root, _ = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    os.chdir(folder)
    state.set_path(f"case/{folder.name}")
    (root / "outside.txt").write_text("x", encoding="utf-8")

    assert main(["save", "--repo", "--push"]) == 0
    files = _sh(root, "show", "--name-only", "--pretty=format:", "HEAD")
    assert "outside.txt" in files
    assert f"save: {root.name}" in _local_head(root)


# --- m.rau/bibi#97: der Scope steht vorher da, nicht erst in der Commit-Message ---

def _lines(capsys) -> list[str]:
    return capsys.readouterr().out.splitlines()


def test_save_names_the_repo_scope_before_committing(repo_with_origin, capsys):
    """Der Kern von #97: bisher war die Default-Commit-Message der einzige
    sichtbare Unterschied zwischen Case- und Repo-Scope — und die liest man erst
    hinterher im Log. Ein Repo-weiter Commit kann fremde, halbfertige Arbeit
    mitnehmen; das muss vor dem Schreiben dastehen, nicht danach."""
    root, _ = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")

    assert main(["save", "--push"]) == 0
    out = _lines(capsys)
    scope_line = next(i for i, ln in enumerate(out) if ln.startswith("Scope:"))
    commit_line = next(i for i, ln in enumerate(out) if ln.startswith("committed:"))
    assert scope_line < commit_line                     # vorher, nicht hinterher
    assert "ganzes Repo" in out[scope_line]
    assert "2 Datei(en)" in out[scope_line]             # samt Dateizahl


def test_save_names_the_case_scope_too(repo_with_origin, capsys):
    """Symmetrie, und sie kostet nichts: wer den Scope nur im auffälligen Fall
    nennt, lässt den Leser raten, ob im anderen Fall überhaupt geprüft wurde."""
    root, _ = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    os.chdir(folder)
    assert main(["save", "--push"]) == 0
    assert any(ln.startswith("Scope: case/") for ln in _lines(capsys))


def test_save_refuses_when_a_marker_of_another_session_points_at_a_case(
        repo_with_origin, monkeypatch, capsys):
    """Die eine Lage, in der ``save`` nicht raten darf: es gibt eine Park-Marke
    auf einen existierenden Case, sie gehört nur einer Session-ID, die es nicht
    mehr gibt. Repo-Scope ist hier keine Feststellung, sondern eine Vermutung."""
    root, _ = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    state.set_path(f"case/{folder.name}")
    os.chdir(root)
    (root / "fremd.txt").write_text("x", encoding="utf-8")

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")   # Wiederverbindung
    assert main(["save", "--push"]) == 2                # eigener Code: nicht „fehlgeschlagen"
    err = capsys.readouterr().err
    assert folder.name in err                           # der gemeinte Case wird benannt
    assert "--repo" in err                              # und der Weg daran vorbei
    assert _local_head(root) == "init"                  # nichts committet


def test_save_repo_flag_answers_the_refusal(repo_with_origin, monkeypatch, capsys):
    root, _ = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    state.set_path(f"case/{folder.name}")
    os.chdir(root)
    (root / "fremd.txt").write_text("x", encoding="utf-8")

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")
    assert main(["save", "--repo", "--push"]) == 0
    assert "fremd.txt" in _sh(root, "show", "--name-only", "--pretty=format:", "HEAD")


def test_save_does_not_refuse_when_nothing_was_ever_parked(
        repo_with_origin, monkeypatch, capsys):
    """Die Gegenprobe, und sie ist der Grund für die ganze Unterscheidung: „nie
    geparkt" ist ein völlig normaler Zustand (frisches Repo, Job, Hook). Wer
    beide Lagen gleich behandelt, macht aus einer Warnung eine Belästigung."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")
    root, _ = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    assert main(["save", "--push"]) == 0


def test_save_does_not_refuse_when_the_marker_is_the_own_one(
        repo_with_origin, monkeypatch):
    """Eine eigene Marke ist kein Fremdbefund — sie führt schlicht in den
    Case-Scope."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    root, _ = repo_with_origin
    folder = case_store.create_case("Alpha Feature")
    state.set_path(f"case/{folder.name}")
    assert main(["save", "--push"]) == 0


# --- Sync-Matrix §4.9 ---

def test_save_auto_push_off_commits_but_does_not_push(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    rc = main(["save"])  # auto_sync default off, kein --push
    assert rc == 0
    assert _local_head(root).startswith("save:")     # lokal committed
    assert _origin_head(origin) == "init"            # NICHT gepusht


def test_save_auto_push_on_pushes(repo_with_origin):
    root, origin = repo_with_origin
    state.set_auto_sync(True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    rc = main(["save"])
    assert rc == 0
    assert _origin_head(origin).startswith("save:")   # gepusht


def test_save_force_push_overrides_off(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    rc = main(["save", "--push"])
    assert rc == 0
    assert _origin_head(origin).startswith("save:")


def test_save_custom_message(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    main(["save", "--push", "-m", "save: eigener Text"])
    assert _origin_head(origin) == "save: eigener Text"


def test_save_nothing_to_commit_is_ok(repo_with_origin):
    root, origin = repo_with_origin
    rc = main(["save", "--push"])
    assert rc == 0


# --- Konflikt ---

def test_save_conflict_sets_flag_and_returns_1(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = tmp_path / "other"
    _sh(tmp_path, "clone", "-q", str(origin), "other")
    _sh(other, "config", "user.name", "O"); _sh(other, "config", "user.email", "o@e.x")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote"); _sh(other, "push", "-q", "origin", "trunk")

    (root / "pyproject.toml").write_text("LOCAL\n", encoding="utf-8")
    rc = main(["save", "--push"])
    assert rc == 1
    assert state.get_sync_conflict() is True
