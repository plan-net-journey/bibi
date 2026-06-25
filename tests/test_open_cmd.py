"""Integrationstests für `bibi-ctrl open` über den CLI-Dispatcher."""

from __future__ import annotations

from pathlib import Path

from bibi import case_store, state
from bibi.ctrl import main


def test_open_creates_new_case(team_repo: Path, capsys):
    rc = main(["open", "Alpha Feature"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "erstellt: case/" in out
    assert "cd: " in out
    assert len(case_store.find_matches("alpha")) == 1


def test_open_sets_path_mirror(team_repo: Path, capsys):
    main(["open", "Alpha"])
    assert state.read()["path"].startswith("case/")


def test_open_reactivates_paused(team_repo: Path, capsys):
    folder = case_store.create_case("Alpha Feature")
    case_store.set_status(folder, "paused")
    rc = main(["open", "alpha"])
    assert rc == 0
    assert case_store.get_status(folder) == "open"
    assert "reaktiviert" in capsys.readouterr().out


def test_open_ambiguous_returns_2(team_repo: Path, capsys):
    case_store.create_case("Alpha One")
    case_store.create_case("Alpha Two")
    rc = main(["open", "alpha"])
    assert rc == 2
    assert "mehrdeutig" in capsys.readouterr().err


def test_open_closed_without_force_returns_2(team_repo: Path, capsys):
    folder = case_store.create_case("Alpha")
    case_store.set_status(folder, "closed")
    rc = main(["open", "alpha"])
    assert rc == 2
    assert case_store.get_status(folder) == "closed"  # unverändert
    assert "closed" in capsys.readouterr().err


def test_open_closed_with_force_reactivates(team_repo: Path, capsys):
    folder = case_store.create_case("Alpha")
    case_store.set_status(folder, "closed")
    rc = main(["open", "alpha", "--force"])
    assert rc == 0
    assert case_store.get_status(folder) == "open"


def test_open_respects_case_dir(team_repo: Path, monkeypatch, capsys):
    monkeypatch.setenv("BIBI_CASE_DIR", "project")
    rc = main(["open", "Legacy Topic"])
    assert rc == 0
    assert "erstellt: project/" in capsys.readouterr().out
    assert (team_repo / "vault" / "project").exists()


def test_no_subcommand_returns_1(team_repo: Path, capsys):
    assert main([]) == 1
