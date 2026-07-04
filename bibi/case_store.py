"""Case-Store: Ordner anlegen, Frontmatter patchen, Slug-Suche (DESIGN §3.2).

Case-Ordner: ``vault/<case_dir>/YYYYmmdd.<slug>-<short>/`` mit ``README.md``
und Frontmatter ``slug, short, status, created``. ``create_case`` legt sie
immer flach direkt unter ``case_dir`` an; die Suche (``find_matches``) findet
Cases aber auch beliebig tief in Unterordnern (z. B. nach Jahr/Monat einsortiert),
falls sie dorthin verschoben wurden. Das gilt auch für Altbestand ohne
``-<short>``-Suffix im Ordnernamen (Zeit vor der aktuellen Konvention) — dort
wird der Case per ``slug``-Frontmatter erkannt statt per Namensmuster.

``short = uuid4().hex[:8]`` — eine ID, als Suffix im Ordnernamen.
Das Case-Verzeichnis ist konfigurierbar (``repo.case_dir``); Default ``case``,
bibi3-Kompat via ``case_dir = "project"``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
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
    #: Pfad relativ zu ``case_dir``, z. B. ``20260624.Foo-deadbeef`` (flach)
    #: oder ``2026/06/20260624.Foo-deadbeef`` (verschoben/verschachtelt).
    folder_name: str
    slug: str
    #: ``None`` für Altbestand ohne ``-<short>``-Suffix (Slug kommt dann aus
    #: der Frontmatter statt aus dem Ordnernamen geparst, siehe find_matches).
    short: str | None

    @property
    def folder(self) -> Path:
        return repo.case_dir() / self.folder_name


def _has_case_frontmatter(folder: Path) -> bool:
    """True, wenn ``folder/README.md`` einen ``slug``-Key trägt.

    Fängt Case-Ordner aus einer Zeit vor der ``-<short>``-Namenskonvention ab
    (manuell archiviert, kein Suffix im Namen) — ohne diesen Check hält
    ``_iter_case_dirs`` sie für einen reinen Gliederungsordner, steigt
    vergeblich hinein (README ist keine Unterordner) und der Case verschwindet
    komplett aus ``find_matches``.
    """
    readme = folder / "README.md"
    if not readme.is_file():
        return False
    try:
        fm = frontmatter.read(readme)
    except Exception:
        return False
    return "slug" in fm


def _iter_case_dirs(root: Path) -> Iterator[Path]:
    """Case-Ordner rekursiv unter ``root`` finden.

    Sortiert pro Verzeichnisebene. Ein Ordner gilt als Case-Blatt — wird
    geliefert, aber nicht selbst durchsucht, da sein Inhalt (Notizen, Anhänge)
    kein Container für weitere Cases ist —, wenn sein Name dem Case-Muster
    entspricht **oder** er direkt eine ``README.md`` mit ``slug``-Frontmatter
    enthält (Altbestand ohne Namenssuffix). Alles andere (z. B. eine
    Jahres-/Monats-Gliederung wie ``2026/06/``) wird durchstiegen, damit
    verschobene Cases trotzdem gefunden werden.
    """
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if _FOLDER_RE.match(p.name) or _has_case_frontmatter(p):
            yield p
            continue
        yield from _iter_case_dirs(p)


def find_matches(topic_or_fragment: str) -> list[Match]:
    """Substring-Match gegen Case-Ordner, rekursiv unter dem Case-Verzeichnis."""
    case_dir = repo.case_dir()
    if not case_dir.exists():
        return []
    # Ein eingefügter "<case_dir>/<folder>"-Pfad wird toleriert: führendes
    # Verzeichnis-Segment entfernen, damit sein Slug keinen Extra-Token bekommt.
    fragment = topic_or_fragment.strip().removeprefix(f"{repo.case_dir_name()}/")
    needle = _slugify(fragment).lower()
    matches: list[Match] = []
    for p in _iter_case_dirs(case_dir):
        try:
            slug, short = folder_to_slug_short(p.name)
        except ValueError:
            # Kein "-<short>"-Suffix im Namen (Altbestand) — Slug kommt dann
            # aus der Frontmatter; _has_case_frontmatter hat sie oben schon
            # als lesbar mit slug-Key verifiziert.
            slug = read_frontmatter(p)["slug"]
            short = None
        # Beide Seiten slugifizieren, damit ein voller Ordnername (mit
        # Datum/Punkten/Bindestrichen) ebenfalls matcht.
        if needle not in slug.lower() and needle not in _slugify(p.name).lower():
            continue
        rel = p.relative_to(case_dir).as_posix()
        matches.append(Match(folder_name=rel, slug=slug, short=short))
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
