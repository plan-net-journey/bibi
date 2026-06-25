"""Tests für bibi.case_store (Anlage, Slug-Suche, Status)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bibi import case_store, frontmatter


def test_slugify_camelcase():
    assert case_store._slugify("Alpha Feature!") == "AlphaFeature"
    assert case_store._slugify("a-b_c") == "abc"


def test_slugify_empty_is_untitled():
    assert case_store._slugify("!!!") == "untitled"


def test_make_folder_name_format():
    name = case_store.make_folder_name("Foo", "deadbeef", date(2026, 6, 24))
    assert name == "20260624.Foo-deadbeef"


def test_folder_to_slug_short_roundtrip():
    assert case_store.folder_to_slug_short("20260624.Foo-deadbeef") == ("Foo", "deadbeef")


def test_folder_to_slug_short_invalid():
    with pytest.raises(ValueError):
        case_store.folder_to_slug_short("kein-valider-name")


def test_make_short_is_8_hex():
    s = case_store.make_short()
    assert len(s) == 8
    int(s, 16)  # hex-parsebar


def test_create_case_writes_readme(team_repo: Path):
    folder = case_store.create_case("Alpha Feature")
    assert folder.exists()
    assert folder.parent == team_repo.resolve() / "vault" / "case"
    fm = frontmatter.read(folder / "README.md")
    assert fm["slug"] == "AlphaFeature"
    assert fm["status"] == "open"
    assert len(fm["short"]) == 8


def test_find_matches_substring(team_repo: Path):
    case_store.create_case("Alpha Feature")
    case_store.create_case("Beta Thing")
    matches = case_store.find_matches("alpha")
    assert len(matches) == 1
    assert matches[0].slug == "AlphaFeature"


def test_find_matches_multiple(team_repo: Path):
    case_store.create_case("Alpha One")
    case_store.create_case("Alpha Two")
    assert len(case_store.find_matches("alpha")) == 2


def test_find_matches_none(team_repo: Path):
    case_store.create_case("Alpha")
    assert case_store.find_matches("zzz") == []


def test_find_matches_empty_case_dir(team_repo: Path):
    assert case_store.find_matches("x") == []


def test_find_matches_respects_case_dir(team_repo: Path, monkeypatch: pytest.MonkeyPatch):
    # In "project" anlegen, dann mit demselben case_dir wiederfinden.
    monkeypatch.setenv("BIBI_CASE_DIR", "project")
    case_store.create_case("Legacy")
    assert (team_repo / "vault" / "project").exists()
    assert len(case_store.find_matches("legacy")) == 1


def test_set_and_get_status(team_repo: Path):
    folder = case_store.create_case("Foo")
    assert case_store.get_status(folder) == "open"
    case_store.set_status(folder, "paused")
    assert case_store.get_status(folder) == "paused"


def test_set_status_invalid_raises(team_repo: Path):
    folder = case_store.create_case("Foo")
    with pytest.raises(ValueError):
        case_store.set_status(folder, "bogus")
