"""``bibi-ctrl soul`` — aktive Persona setzen/anzeigen (PLAN-13 Stufe 13.1)."""

from __future__ import annotations

from pathlib import Path

from bibi import state
from bibi.ctrl import main


def _write_soul(root: Path, filename: str, text: str = "# SOUL\n") -> None:
    d = root / ".claude" / "souls"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(text, encoding="utf-8")


def test_soul_no_souls_dir_reports_and_fails(team_repo: Path, capsys):
    rc = main(["soul", "Data"])
    assert rc == 1
    assert "kein Souls-Verzeichnis" in capsys.readouterr().err


def test_soul_unknown_name_lists_available(team_repo: Path, capsys):
    _write_soul(team_repo, "12.Data.SOUL.md")
    _write_soul(team_repo, "01.Rook.SOUL.md")
    rc = main(["soul", "Nonexistent"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unbekannte Soul: Nonexistent" in err
    assert "Data" in err and "Rook" in err


def test_soul_sets_active_soul_case_insensitive(team_repo: Path, capsys):
    _write_soul(team_repo, "12.Data.SOUL.md")
    rc = main(["soul", "data"])  # lowercase Eingabe
    assert rc == 0
    assert capsys.readouterr().out.strip() == "Data"  # kanonische Schreibweise
    assert state.get_soul() == "Data"


def test_soul_no_argument_shows_current(team_repo: Path, capsys):
    _write_soul(team_repo, "12.Data.SOUL.md")
    main(["soul", "Data"])
    capsys.readouterr()
    rc = main(["soul"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "Data"


def test_soul_no_argument_none_active_reports(team_repo: Path, capsys):
    rc = main(["soul"])
    assert rc == 0
    assert "keine Soul aktiv" in capsys.readouterr().err


def test_soul_switching_updates_state(team_repo: Path, capsys):
    _write_soul(team_repo, "12.Data.SOUL.md")
    _write_soul(team_repo, "01.Rook.SOUL.md")
    main(["soul", "Data"])
    assert state.get_soul() == "Data"
    main(["soul", "Rook"])
    assert state.get_soul() == "Rook"
