"""Vault-Walk + Kollisions-Erkennung (DESIGN §5.2; PLAN-3 §3.1)."""

from __future__ import annotations

from pathlib import Path

from bibi.schedule.discovery import discover, walk


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_walk_skips_dot_dirs(tmp_path: Path):
    _write(tmp_path / "case" / "a" / "README.md", "---\nschedule: now\njob: x\n---\n")
    _write(tmp_path / ".git" / "b.md", "---\nschedule: now\njob: x\n---\n")
    found = {p.name for p in walk(tmp_path)}
    assert "README.md" in found
    # .git-MD wird nicht gelaufen
    assert all(".git" not in p.parts for p in walk(tmp_path))


def test_walk_missing_vault_is_empty(tmp_path: Path):
    assert list(walk(tmp_path / "nope")) == []


def test_discover_groups_found(tmp_path: Path):
    _write(tmp_path / "case" / "hello" / "README.md", '---\nschedule: now\njob: "echo hi"\n---\n')
    _write(tmp_path / "case" / "daily.md", '---\nschedule: "0 9 * * *"\nclaude: "x"\n---\n')
    res = discover(tmp_path)
    assert set(res.found) == {"hello", "daily"}
    assert res.errors == ()
    assert res.collisions == ()


def test_discover_reports_errors(tmp_path: Path):
    _write(tmp_path / "case" / "bad.md", '---\nschedule: "broken cron"\njob: "x"\n---\n')
    res = discover(tmp_path)
    assert len(res.errors) == 1
    assert "cron" in res.errors[0].error.lower()
    assert res.found == {}


def test_discover_skips_non_schedule_mds(tmp_path: Path):
    _write(tmp_path / "case" / "notes.md", "# just notes\nno frontmatter")
    res = discover(tmp_path)
    assert res.found == {} and res.errors == () and res.collisions == ()


def test_discover_detects_slug_collision(tmp_path: Path):
    # zwei MDs mit explizit gleichem Slug → Kollision, keine in found
    _write(tmp_path / "case" / "a.md", '---\nslug: dup\nschedule: now\njob: "x"\n---\n')
    _write(tmp_path / "case" / "b.md", '---\nslug: dup\nschedule: now\njob: "y"\n---\n')
    res = discover(tmp_path)
    assert res.found == {}
    assert len(res.collisions) == 1
    c = res.collisions[0]
    assert c.slug == "dup"
    assert c.schedule_refs == ("case/a.md", "case/b.md")
