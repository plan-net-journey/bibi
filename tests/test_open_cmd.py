"""Integrationstests für `bibi-ctrl open` über den CLI-Dispatcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import case_store, frontmatter, state
from bibi.ctrl import main


@pytest.fixture(autouse=True)
def _session(monkeypatch):
    """`/open` parkt die Session; ohne Session-ID gäbe es seit m.rau/bibi#99
    nichts mehr zu beobachten (der `path:`-Mirror ist entfallen)."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-open")


def test_open_creates_new_case(team_repo: Path, capsys):
    rc = main(["open", "Alpha Feature"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "erstellt: case/" in out
    assert "cd: " in out
    assert len(case_store.find_matches("alpha")) == 1


def test_open_parks_the_session(team_repo: Path, capsys):
    """`/open` parkt die Session auf den Case — die Park-Marke ist seit
    m.rau/bibi#99 der einzige Speicher dafür (kein `path:`-Mirror mehr)."""
    main(["open", "Alpha"])
    assert state.park_file().read_text().startswith("case/")
    assert "path" not in state.read()


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


def test_open_reactivates_nested_case(team_repo: Path, capsys):
    # Case wurde manuell in eine Jahres-/Monats-Gliederung verschoben.
    folder = case_store.create_case("Alpha Feature")
    case_store.set_status(folder, "paused")
    nested_dir = team_repo / "vault" / "case" / "2026" / "06"
    nested_dir.mkdir(parents=True)
    moved = folder.rename(nested_dir / folder.name)

    rc = main(["open", "alpha"])
    assert rc == 0
    assert case_store.get_status(moved) == "open"
    out = capsys.readouterr().out
    assert f"reaktiviert: case/2026/06/{folder.name}" in out
    assert f"cd: {moved.resolve()}" in out
    assert state.park_file().read_text() == f"case/2026/06/{folder.name}"


def test_open_reactivates_legacy_case_without_short_suffix(team_repo: Path, capsys):
    # Altbestand aus einer Zeit vor der -<short>-Namenskonvention, manuell nach
    # case/2026/ archiviert — kein Hash-Suffix im Ordnernamen.
    legacy_dir = team_repo / "vault" / "case" / "2026" / "20260531.LegacyThing"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "README.md").write_text(
        frontmatter.join({"slug": "LegacyThing", "status": "paused"}, "\n# Legacy\n"),
        encoding="utf-8",
    )

    rc = main(["open", "legacything"])
    assert rc == 0
    assert case_store.get_status(legacy_dir) == "open"
    out = capsys.readouterr().out
    assert "reaktiviert: case/2026/20260531.LegacyThing" in out
    assert state.park_file().read_text() == "case/2026/20260531.LegacyThing"
