"""Alle Schedules eines Vault entdecken (DESIGN §5.2; PLAN-3 §3.1).

Reine Funktionen über das Dateisystem — kein DB-Zugriff. ``walk`` liefert
Kandidaten-MDs, ``discover`` parst alle und gruppiert in *found* / *errors* /
*collisions*. Den DB-Abgleich (insert/update/remove) macht das ``rescan`` im
``job_db`` gegen dieses Ergebnis.

Slug-Kollisionen: mehrere MDs mit gleichem Slug landen in ``collisions``, **nicht**
in ``found`` — sie sind zur Laufzeit ignoriert, bis sie aufgelöst sind (§6.6).
Im bibi4-Modell ist der Slug der Schlüssel: das Verschieben einer MD mit
explizitem Slug ist damit ein reiner ``schedule_ref``-Update (gleicher Slug),
keine Sonderbehandlung nötig.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from bibi.schedule.parser import ParseResult, parse_file


@dataclass(frozen=True, slots=True)
class SlugCollision:
    """Zwei oder mehr MDs beanspruchen denselben Slug."""

    slug: str
    schedule_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    #: slug → ParseResult (nur eindeutige, fehlerfreie Specs)
    found: dict[str, ParseResult] = field(default_factory=dict)
    errors: tuple[ParseResult, ...] = ()
    collisions: tuple[SlugCollision, ...] = ()


def walk(vault_root: Path) -> Iterator[Path]:
    """``.md`` unter ``vault_root`` liefern; Punkt-Verzeichnisse überspringen."""
    if not vault_root.exists():
        return
    for path in vault_root.rglob("*.md"):
        if not path.is_file():
            continue
        rel = path.relative_to(vault_root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        yield path


def discover(vault_root: Path) -> DiscoveryResult:
    """Vault begehen, jede MD parsen, Ergebnisse gruppieren."""
    results: list[ParseResult] = []
    for p in walk(vault_root):
        try:
            results.append(parse_file(p, vault_root=vault_root))
        except Exception as exc:  # defensiv: eine kaputte MD darf den Scan nicht kippen
            ref = p.relative_to(vault_root).as_posix() if p.is_relative_to(vault_root) else str(p)
            results.append(ParseResult(schedule_ref=ref, error=f"Unerwarteter Parse-Fehler: {exc}"))

    errors = tuple(r for r in results if r.is_error)
    oks = [r for r in results if r.is_ok]

    by_slug: dict[str, list[ParseResult]] = {}
    for r in oks:
        assert r.spec is not None
        by_slug.setdefault(r.spec.slug, []).append(r)

    found: dict[str, ParseResult] = {}
    collisions: list[SlugCollision] = []
    for slug, rs in by_slug.items():
        if len(rs) == 1:
            found[slug] = rs[0]
        else:
            collisions.append(
                SlugCollision(slug=slug, schedule_refs=tuple(sorted(r.schedule_ref for r in rs)))
            )

    return DiscoveryResult(found=found, errors=errors, collisions=tuple(collisions))
