"""YAML frontmatter parse/write for Markdown files.

Convention: frontmatter is YAML between two `---` lines at the start of the
file. If no frontmatter is present, parse returns {}. write preserves the
existing body.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)", re.DOTALL)


def split(text: str) -> tuple[dict[str, Any], str]:
    """Parse frontmatter and body. Missing frontmatter → ({}, text)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_raw, body = m.group(1), m.group(2)
    fm = yaml.safe_load(fm_raw) or {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, body


def join(fm: dict[str, Any], body: str) -> str:
    """Render frontmatter + body. Empty frontmatter → body only."""
    if not fm:
        return body
    fm_yaml = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{fm_yaml}\n---\n{body}"


def read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return split(path.read_text(encoding="utf-8"))[0]


def patch(file: Path, /, **updates: Any) -> None:
    """Patch frontmatter fields atomically. Keys with value=None are removed.

    `file` is positional-only so that `**updates` may contain a field named
    `path` (e.g. for `.state.md::path`) without colliding with the parameter.
    """
    text = file.read_text(encoding="utf-8") if file.exists() else ""
    fm, body = split(text)
    for key, val in updates.items():
        if val is None:
            fm.pop(key, None)
        else:
            fm[key] = val
    file.write_text(join(fm, body), encoding="utf-8")
