"""Persistente Merge-Quarantäne (PLAN-30 Ebene 2, Bibi4-Case 20260621).

Verhindert, dass ein dauerhaft gegen denselben trunk-Stand fehlschlagender
``agent/*``-Branch vom Sweep jede Minute neu versucht wird — genau das, was
``agent/Witz-83837197`` real getan hat (1440+ Versuche, alle 60s, derselbe
Fehlschlag). Zwei Regeln, kombiniert (A2+A4):

- Ein Branch wird nur erneut versucht, wenn trunk sich seit seinem letzten
  Fehlschlag bewegt hat (neue Chance auf einen konfliktfreien Merge).
- Nach 3 aufeinanderfolgenden Fehlschlägen (jeder gegen einen NEUEN trunk-
  Stand — sonst würde der erste Punkt den Versuch schon verhindert haben) wird
  der Branch komplett aus dem automatischen Sweep genommen, bis ein Mensch
  eingreift (Ebene 3, noch nicht gebaut).

Nur "echte" Fehlschläge zählen (Modus B/Inhaltskonflikt, generischer Fehler) —
Modus A (Dirty-Tree-Verweigerung, löst sich von selbst sobald committet wird)
zählt NICHT, s. ``mergeback.py``.

Muss vom Aufrufer innerhalb desselben ``sync_lock``-Scopes gelesen/geschrieben
werden wie der zugehörige Merge-Versuch selbst (Review-Fund PLAN-30, 1. Runde:
``_merge_back()`` und ``_merge_sweep()`` laufen in unterschiedlichen Threads
desselben Prozesses) — dieses Modul hält bewusst keinen eigenen Lock, ein
zweiter, unabhängiger Lock würde das eigentliche Problem (Lost Update zwischen
Entscheidung und Git-Operation) nicht lösen.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

ESCALATE_AFTER = 3


@dataclass(frozen=True, slots=True)
class Entry:
    trunk_sha: str
    failures: int


def _path(repo_root: Path) -> Path:
    return repo_root / "data" / "merge_quarantine.json"


def _load(repo_root: Path) -> dict[str, Entry]:
    p = _path(repo_root)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # Review-Runde 3, Fund 3: syntaktisch gültiges, aber falsch geformtes JSON
    # (z. B. "[]"/"null" statt eines Objekts) warf zuvor AttributeError auf
    # raw.items() — das riss den GESAMTEN Merge-back für ALLE Branches mit,
    # statt defensiv auf "keine Quarantäne" zurückzufallen.
    if not isinstance(raw, dict):
        return {}
    try:
        return {branch: Entry(**fields) for branch, fields in raw.items()}
    except (TypeError, AttributeError):
        return {}  # fremdes/beschädigtes Format — lieber leer als abstürzen


def _save(repo_root: Path, data: dict[str, Entry]) -> None:
    p = _path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({branch: asdict(e) for branch, e in data.items()}, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, p)  # atomarer Rename statt Read-Modify-Write auf der echten Datei


def get(repo_root: Path, branch: str) -> Entry | None:
    return _load(repo_root).get(branch)


def record_failure(repo_root: Path, branch: str, *, trunk_sha: str) -> Entry:
    data = _load(repo_root)
    prev = data.get(branch)
    failures = (prev.failures + 1) if prev is not None else 1
    entry = Entry(trunk_sha=trunk_sha, failures=failures)
    data[branch] = entry
    _save(repo_root, data)
    return entry


def clear(repo_root: Path, branch: str) -> None:
    data = _load(repo_root)
    if branch in data:
        del data[branch]
        _save(repo_root, data)


def prune(repo_root: Path, *, keep_branches: set[str]) -> None:
    """Quarantäne-Zeilen für Branches löschen, die nicht mehr unmerged sind
    (gemergt oder gelöscht, z. B. von einem Menschen via ``/sync``)."""
    data = _load(repo_root)
    stale = [b for b in data if b not in keep_branches]
    if not stale:
        return
    for b in stale:
        del data[b]
    _save(repo_root, data)


def escalated(repo_root: Path) -> list[str]:
    """Branches, die die harte Eskalationsgrenze erreicht haben (sortiert) —
    PLAN-30 Ebene 3: dieselbe Quarantäne-Struktur IST die Eskalations-Liste,
    kein zweiter Speicher-Mechanismus. Ein Branch mit 1-2 Fehlschlägen taucht
    hier bewusst NICHT auf — der wird noch automatisch weiterversucht, sobald
    trunk sich bewegt, braucht also (noch) keine menschliche Aufmerksamkeit."""
    return sorted(b for b, e in _load(repo_root).items() if e.failures >= ESCALATE_AFTER)
