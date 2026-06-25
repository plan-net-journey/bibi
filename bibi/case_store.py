"""Case-Store: Ordner anlegen, Frontmatter patchen, Slug-Suche (DESIGN §3.2).

Case-Ordner: ``vault/<case_dir>/YYYYmmdd.<slug>-<short>/`` mit ``README.md``
und Frontmatter ``slug, short, status, created``.

``short = uuid4().hex[:8]`` — eine ID, als Suffix im Ordnernamen.
Das Case-Verzeichnis ist konfigurierbar (``repo.case_dir``); Default ``case``,
bibi3-Kompat via ``case_dir = "project"``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from bibi import frontmatter, repo, state

VALID_STATUS = {"open", "paused", "closed"}

_FOLDER_RE = re.compile(r"^\d{8}\.(.+)-([0-9a-f]{8})$")


def _slugify(topic: str) -> str:
    """CamelCase-Slug. Nicht-alphanumerische Zeichen entfernen."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", topic)
    return cleaned or "untitled"


def make_short() -> str:
    return uuid.uuid4().hex[:8]


def make_folder_name(slug: str, short: str, today: date | None = None) -> str:
    today = today or date.today()
    return f"{today:%Y%m%d}.{slug}-{short}"


def folder_to_slug_short(folder_name: str) -> tuple[str, str]:
    """`20260517.MyTopic-deadbeef` → ('MyTopic', 'deadbeef')."""
    m = _FOLDER_RE.match(folder_name)
    if not m:
        raise ValueError(f"folder name {folder_name} does not match pattern")
    return m.group(1), m.group(2)


@dataclass(frozen=True)
class Match:
    folder_name: str
    slug: str
    short: str

    @property
    def folder(self) -> Path:
        return repo.case_dir() / self.folder_name


def find_matches(topic_or_fragment: str) -> list[Match]:
    """Substring-Match gegen Ordnernamen im Case-Verzeichnis."""
    case_dir = repo.case_dir()
    if not case_dir.exists():
        return []
    # Ein eingefügter "<case_dir>/<folder>"-Pfad wird toleriert: führendes
    # Verzeichnis-Segment entfernen, damit sein Slug keinen Extra-Token bekommt.
    fragment = topic_or_fragment.strip().removeprefix(f"{repo.case_dir_name()}/")
    needle = _slugify(fragment).lower()
    matches: list[Match] = []
    for p in sorted(case_dir.iterdir()):
        if not p.is_dir():
            continue
        try:
            slug, short = folder_to_slug_short(p.name)
        except ValueError:
            continue
        # Beide Seiten slugifizieren, damit ein voller Ordnername (mit
        # Datum/Punkten/Bindestrichen) ebenfalls matcht.
        if needle not in slug.lower() and needle not in _slugify(p.name).lower():
            continue
        matches.append(Match(folder_name=p.name, slug=slug, short=short))
    return matches


def create_case(topic: str) -> Path:
    """``vault/<case_dir>/<date>.<slug>-<short>/README.md`` anlegen."""
    case_dir = repo.case_dir()
    case_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(topic)
    short = make_short()
    folder_name = make_folder_name(slug, short)
    folder = case_dir / folder_name
    folder.mkdir(parents=False, exist_ok=False)

    fm = {
        "slug": slug,
        "short": short,
        "status": "open",
        "created": date.today().isoformat(),
    }
    body = f"\n# {topic}\n\nAngelegt am {date.today().isoformat()}.\n"
    (folder / "README.md").write_text(frontmatter.join(fm, body), encoding="utf-8")
    return folder


def active_case() -> Path | None:
    """Ordner des aktiven Case (aus dem geparkten cwd) oder None.

    Geteilt von close/done/delete/on-stop. Die Wahrheit ist das cwd
    (``state.get_path``); der ``.state.md``-Mirror wird nicht herangezogen.
    """
    path = state.get_path()
    if not path:
        return None
    folder = repo.vault() / path
    return folder if folder.exists() else None


def read_frontmatter(folder: Path) -> dict[str, Any]:
    return frontmatter.read(folder / "README.md")


def set_status(folder: Path, status: str) -> None:
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status {status!r}, must be one of {VALID_STATUS}")
    frontmatter.patch(folder / "README.md", status=status)


def get_status(folder: Path) -> str | None:
    return read_frontmatter(folder).get("status")
