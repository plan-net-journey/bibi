"""Tests für bibi.state (cwd + Session-Park-Marke + .state.md-Mirror)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bibi import state


def _mkcase(team_repo: Path, name: str = "20260624.Foo-deadbeef") -> Path:
    case = team_repo / "vault" / "case" / name
    case.mkdir(parents=True, exist_ok=True)
    return case


def test_get_path_none_at_repo_root(team_repo: Path):
    # cwd ist der Repo-Root, nicht in vault/ → kein aktiver Case.
    assert state.get_path() is None


def test_get_path_inside_case(team_repo: Path):
    case = team_repo / "vault" / "case" / "20260624.Foo-deadbeef"
    case.mkdir(parents=True)
    os.chdir(case)
    assert state.get_path() == "case/20260624.Foo-deadbeef"


def test_set_path_writes_mirror(team_repo: Path):
    state.set_path("case/foo-123")
    assert state.read()["path"] == "case/foo-123"


def test_set_path_none_removes_mirror(team_repo: Path):
    state.set_path("case/foo-123")
    state.set_path(None)
    assert "path" not in state.read()


# --- Session-Park-Marke: der Case überlebt den Verlust des cwd ---

def test_park_survives_cwd_leaving_the_case(team_repo: Path, monkeypatch):
    """Der Kern: /open parkt, irgendein späterer `cd` verlässt den Case — und
    der aktive Case bleibt trotzdem bekannt."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    case = _mkcase(team_repo)
    os.chdir(case)
    state.set_path("case/20260624.Foo-deadbeef")

    os.chdir(team_repo)  # cwd weg — früher hieß das "kein aktiver Case"
    assert state.get_path() == "case/20260624.Foo-deadbeef"
    assert state.path_source() == "session"


def test_cwd_wins_over_park_marker(team_repo: Path, monkeypatch):
    """Wer bewusst in einen anderen Case wechselt, meint das auch."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    _mkcase(team_repo)
    other = _mkcase(team_repo, "20260625.Bar-cafe1234")
    state.set_path("case/20260624.Foo-deadbeef")

    os.chdir(other)
    assert state.get_path() == "case/20260625.Bar-cafe1234"
    assert state.path_source() == "cwd"


def test_park_markers_are_isolated_per_session(team_repo: Path, monkeypatch):
    """Der Grund, warum früher das cwd die Quelle war — parallele Sessions
    dürfen sich nicht in die Quere kommen. Die Session-ID leistet das genauso."""
    _mkcase(team_repo, "20260624.Foo-deadbeef")
    _mkcase(team_repo, "20260625.Bar-cafe1234")

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    state.set_path("case/20260624.Foo-deadbeef")
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-B")
    state.set_path("case/20260625.Bar-cafe1234")

    assert state.get_path() == "case/20260625.Bar-cafe1234"
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    assert state.get_path() == "case/20260624.Foo-deadbeef"


def test_unpark_removes_marker(team_repo: Path, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    _mkcase(team_repo)
    state.set_path("case/20260624.Foo-deadbeef")
    assert state.park_file().exists()

    state.set_path(None)
    assert not state.park_file().exists()
    assert state.get_path() is None
    assert state.path_source() is None


def test_park_marker_ignored_when_case_folder_is_gone(team_repo: Path, monkeypatch):
    """Ein anderswo gelöschter Case darf nicht als aktiv weitergemeldet werden —
    sonst committet save in einen Pfad, den es nicht mehr gibt."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    case = _mkcase(team_repo)
    state.set_path("case/20260624.Foo-deadbeef")
    case.rmdir()
    assert state.get_path() is None


def test_adopted_session_beats_environment(team_repo: Path, monkeypatch):
    """Hook-/Statusline-Subprozesse bringen ihre session_id im Payload mit."""
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    _mkcase(team_repo)
    state.set_path("case/20260624.Foo-deadbeef")

    state.adopt_session("sess-fremd")
    assert state.get_path() is None
    state.adopt_session("sess-A")
    assert state.get_path() == "case/20260624.Foo-deadbeef"


def test_without_session_id_behaviour_is_unchanged(team_repo: Path):
    """Ohne Session-ID (Cron, fremde Shell) bleibt alles wie vorher: nur cwd."""
    case = _mkcase(team_repo)
    state.set_path("case/20260624.Foo-deadbeef")
    assert state.park_file() is None
    assert state.get_path() is None  # cwd steht im Repo-Root
    os.chdir(case)
    assert state.get_path() == "case/20260624.Foo-deadbeef"


def test_session_id_is_sanitised_into_a_flat_filename(team_repo: Path, monkeypatch):
    """Eine manipulierte Session-ID darf nicht aus data/park/ ausbrechen."""
    monkeypatch.setenv("BIBI_SESSION_ID", "../../etc/passwd")
    _mkcase(team_repo)
    state.set_path("case/20260624.Foo-deadbeef")
    pf = state.park_file()
    assert pf.parent == team_repo / "data" / "park"
    assert state.get_path() == "case/20260624.Foo-deadbeef"


def test_stale_markers_are_pruned_on_write(team_repo: Path, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    _mkcase(team_repo)
    state.set_path("case/20260624.Foo-deadbeef")
    stale = state.park_file()
    old = time.time() - state.PARK_TTL_S - 60
    os.utime(stale, (old, old))

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")
    state.set_path("case/20260624.Foo-deadbeef")
    assert not stale.exists()
    assert state.park_file().exists()


# --- m.rau/bibi#97: „nie geparkt" ist etwas anderes als „fremd geparkt" ---

def test_no_foreign_parks_when_nothing_was_ever_parked(team_repo: Path, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    assert state.foreign_parks() == {}


def test_foreign_parks_names_the_case_of_another_session(team_repo: Path, monkeypatch):
    """Der Kern von #97: die Session-ID wechselt bei jeder Wiederverbindung, die
    Marke der vorigen bleibt liegen. ``get_path()`` sagt dann dasselbe wie bei
    „nie geparkt" — obwohl ein Case gemeint ist."""
    _mkcase(team_repo)
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    state.set_path("case/20260624.Foo-deadbeef")

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")   # Wiederverbindung
    assert state.get_path() is None
    assert state.path_source() is None
    assert state.foreign_parks() == {"case/20260624.Foo-deadbeef": 1}


def test_foreign_parks_ignores_the_own_marker(team_repo: Path, monkeypatch):
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    _mkcase(team_repo)
    state.set_path("case/20260624.Foo-deadbeef")
    assert state.foreign_parks() == {}


def test_foreign_parks_counts_sessions_per_case(team_repo: Path, monkeypatch):
    """Der Live-Befund am 2026-08-01: vier Session-IDs, ein Case. Die Zahl ist
    die Aussage — sie zeigt, dass es der Normalfall ist und kein Ausrutscher."""
    _mkcase(team_repo)
    for sid in ("s1", "s2", "s3", "s4"):
        monkeypatch.setenv("BIBI_SESSION_ID", sid)
        state.set_path("case/20260624.Foo-deadbeef")

    monkeypatch.setenv("BIBI_SESSION_ID", "s5")
    assert state.foreign_parks() == {"case/20260624.Foo-deadbeef": 4}


def test_foreign_parks_ignores_a_case_that_is_gone(team_repo: Path, monkeypatch):
    """Dieselbe Vorsicht wie in ``_path_from_park()``: ein gelöschter Case ist
    kein Hinweis, sondern nur ein Rest."""
    case = _mkcase(team_repo)
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    state.set_path("case/20260624.Foo-deadbeef")
    case.rmdir()

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")
    assert state.foreign_parks() == {}


def test_foreign_parks_without_session_id(team_repo: Path, monkeypatch):
    """Ohne eigene Session-ID (Cron, fremde Shell) sind ALLE Marken fremd — es
    gibt keine eigene, gegen die abzugrenzen wäre."""
    _mkcase(team_repo)
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-A")
    state.set_path("case/20260624.Foo-deadbeef")
    monkeypatch.delenv("BIBI_SESSION_ID")
    assert state.foreign_parks() == {"case/20260624.Foo-deadbeef": 1}


def test_unparking_clears_every_marker_of_that_case(team_repo: Path, monkeypatch):
    """``/close``/``/done`` beenden einen Case für alle Sessions, nicht nur für
    die zufällig gerade laufende. Bliebe die Marke einer früheren liegen, meldete
    ``save`` den Case danach für immer als „fremd geparkt" — ein Warnhinweis, der
    nie mehr weggeht, wird ignoriert."""
    _mkcase(team_repo)
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    state.set_path("case/20260624.Foo-deadbeef")

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")
    state.set_path("case/20260624.Foo-deadbeef")
    state.set_path(None)

    assert state.foreign_parks() == {}


def test_unparking_leaves_markers_of_other_cases_alone(team_repo: Path, monkeypatch):
    _mkcase(team_repo)
    _mkcase(team_repo, "20260625.Bar-cafe1234")
    monkeypatch.setenv("BIBI_SESSION_ID", "sess-alt")
    state.set_path("case/20260625.Bar-cafe1234")

    monkeypatch.setenv("BIBI_SESSION_ID", "sess-neu")
    state.set_path("case/20260624.Foo-deadbeef")
    state.set_path(None)

    assert state.foreign_parks() == {"case/20260625.Bar-cafe1234": 1}


def test_read_defaults_without_file(team_repo: Path):
    s = state.read()
    assert s["auto_sync"] == "off"
    assert s["sync_conflict"] is False


def test_auto_sync_roundtrip(team_repo: Path):
    assert state.get_auto_sync() is False
    state.set_auto_sync(True)
    assert state.get_auto_sync() is True
    state.set_auto_sync(False)
    assert state.get_auto_sync() is False


def test_auto_sync_was_never_set_true_before_any_write(team_repo: Path):
    assert state.auto_sync_was_never_set() is True


def test_auto_sync_was_never_set_false_after_explicit_off(team_repo: Path):
    # Wichtig: auch ein bewusstes "off" zählt als "gesetzt" — der
    # scheduler-Default (daemon_cmd.py) darf ein explizites Abschalten nicht
    # überschreiben, nur die stille Werkseinstellung.
    state.set_auto_sync(False)
    assert state.auto_sync_was_never_set() is False


def test_auto_sync_was_never_set_false_after_explicit_on(team_repo: Path):
    state.set_auto_sync(True)
    assert state.auto_sync_was_never_set() is False


def test_sync_conflict_roundtrip(team_repo: Path):
    assert state.get_sync_conflict() is False
    state.set_sync_conflict(True)
    assert state.get_sync_conflict() is True


def test_state_file_lands_in_dot_claude(team_repo: Path):
    state.set_auto_sync(True)
    assert (team_repo / ".claude" / ".state.md").exists()


def test_soul_roundtrip(team_repo: Path):
    assert state.get_soul() is None
    state.set_soul("Data")
    assert state.get_soul() == "Data"
