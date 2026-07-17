"""Aktiver Zustand (DESIGN §3.2, A-Konventionen).

Zwei Geltungsbereiche, zwei Speicher:

- **Aktiver Case (`path`)** — die *Wahrheit* ist das Bash-cwd, von ``/open`` in
  den Case-Ordner geparkt. ``get_path()`` leitet ihn aus ``Path.cwd()`` ab;
  Prozess-State, der Kontext-Kompaktierung übersteht und zwischen parallelen
  Sessions nie kollidiert (jede Shell hat ihr eigenes cwd). Das ``path:``-Feld
  in ``.claude/.state.md`` ist nur ein **Display-Mirror** für die Statusline.
- **Repo-globale Felder** (``auto_sync``, ``sync_conflict``) — liegen in der
  geteilten, gitignoreten ``.claude/.state.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bibi import frontmatter, repo

DEFAULT_STATE: dict[str, Any] = {
    "auto_sync": "off",
    "sync_conflict": False,
}


def read() -> dict[str, Any]:
    sp = repo.state_path()
    if not sp.exists():
        return dict(DEFAULT_STATE)
    return {**DEFAULT_STATE, **frontmatter.read(sp)}


def patch(**updates: Any) -> None:
    sp = repo.state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    if not sp.exists():
        sp.write_text(frontmatter.join(DEFAULT_STATE, ""), encoding="utf-8")
    frontmatter.patch(sp, **updates)


def get_path() -> str | None:
    """Vault-relativer Pfad des aktiven Case, aus dem Bash-cwd abgeleitet.

    z. B. 'case/20260517.foo-abc'. None, wenn das cwd nicht in ``vault/`` liegt
    (kein Case geparkt) — sicheres „kein aktiver Case" statt Fehlschreibung.
    """
    try:
        rel = Path.cwd().resolve().relative_to(repo.vault().resolve())
    except ValueError:
        return None
    rel_str = str(rel)
    return rel_str if rel_str != "." else None


def set_path(value: str | None) -> None:
    """``path:``-**Display-Mirror** in ``.state.md`` aktualisieren.

    Setzt NICHT den aktiven Case — das ist das Bash-cwd (siehe ``get_path``).
    """
    patch(path=value)


def get_auto_sync() -> bool:
    return read().get("auto_sync", "off") == "on"


def set_auto_sync(value: bool) -> None:
    patch(auto_sync="on" if value else "off")


def auto_sync_was_never_set() -> bool:
    """True, wenn ``auto_sync`` noch nie explizit geschrieben wurde (weder per
    ``set_auto_sync()`` noch von Hand in ``.state.md``) — im Unterschied zu
    ``get_auto_sync()``, das immer den (ggf. defaulteten) Wert liefert. Für den
    scheduler-Default (``daemon_cmd.py``, User-Fund 2026-07-07): der Default
    soll nur greifen, solange niemand je bewusst umgeschaltet hat."""
    return "auto_sync" not in frontmatter.read(repo.state_path())


def get_sync_conflict() -> bool:
    return bool(read().get("sync_conflict", False))


def set_sync_conflict(value: bool) -> None:
    patch(sync_conflict=value)


def get_maintenance() -> bool:
    return bool(read().get("maintenance", False))


def set_maintenance(value: bool) -> None:
    patch(maintenance=value)


def get_soul() -> str | None:
    return read().get("soul") or None


def set_soul(value: str) -> None:
    patch(soul=value)
