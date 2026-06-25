"""Tests für bibi.frontmatter."""

from __future__ import annotations

from pathlib import Path

from bibi import frontmatter


def test_split_with_frontmatter():
    fm, body = frontmatter.split("---\na: 1\nb: x\n---\nHallo\n")
    assert fm == {"a": 1, "b": "x"}
    assert body == "Hallo\n"


def test_split_without_frontmatter():
    fm, body = frontmatter.split("nur Text\n")
    assert fm == {}
    assert body == "nur Text\n"


def test_split_non_dict_frontmatter():
    # Eine YAML-Liste ist kein dict → leeres fm.
    fm, _ = frontmatter.split("---\n- a\n- b\n---\nrest\n")
    assert fm == {}


def test_join_roundtrip():
    text = frontmatter.join({"x": 1}, "Body\n")
    fm, body = frontmatter.split(text)
    assert fm == {"x": 1}
    assert body == "Body\n"


def test_join_empty_fm_is_body_only():
    assert frontmatter.join({}, "nur Body") == "nur Body"


def test_join_preserves_key_order():
    text = frontmatter.join({"z": 1, "a": 2, "m": 3}, "")
    assert text.index("z:") < text.index("a:") < text.index("m:")


def test_read_missing_file(tmp_path: Path):
    assert frontmatter.read(tmp_path / "nope.md") == {}


def test_patch_sets_and_preserves_body(tmp_path: Path):
    f = tmp_path / "x.md"
    f.write_text("---\nstatus: open\n---\n# Titel\n", encoding="utf-8")
    frontmatter.patch(f, status="closed", extra="y")
    fm, body = frontmatter.split(f.read_text())
    assert fm == {"status": "closed", "extra": "y"}
    assert body == "# Titel\n"


def test_patch_none_removes_key(tmp_path: Path):
    f = tmp_path / "x.md"
    f.write_text("---\na: 1\nb: 2\n---\nbody\n", encoding="utf-8")
    frontmatter.patch(f, a=None)
    fm, _ = frontmatter.split(f.read_text())
    assert fm == {"b": 2}


def test_patch_path_key_not_shadowed(tmp_path: Path):
    # `file` ist positional-only, daher darf `path` als Frontmatter-Key dienen.
    f = tmp_path / "state.md"
    f.write_text("---\nauto_sync: 'off'\n---\n", encoding="utf-8")
    frontmatter.patch(f, path="case/foo")
    fm, _ = frontmatter.split(f.read_text())
    assert fm["path"] == "case/foo"
