"""Aktiver Zustand (DESIGN §3.2, A-Konventionen).

Zwei Geltungsbereiche, zwei Speicher:

- **Aktiver Case (`path`)** — pro Claude-Code-Session in einer **Park-Marke**
  ``data/park/<session_id>`` festgehalten, die ``/open`` schreibt. Das Bash-cwd
  bleibt vorrangige Quelle (wer bewusst in einen Case wechselt, meint das auch),
  die Marke fängt aber alles ab, was das cwd *nicht* überlebt: parallele
  Bash-Calls, die sich gegenseitig überschreiben, Hintergrund-Shells,
  Session-Neustarts und Subprozesse ohne Sicht aufs cwd (Hooks, Statusleiste).
  Die Session-ID isoliert parallele Sessions genauso zuverlässig wie früher das
  cwd — jede schreibt ihre eigene Marke. Das ``path:``-Feld in
  ``.claude/.state.md`` bleibt als **Fallback-Mirror** für Kontexte ohne
  Session-ID.
- **Repo-globale Felder** (``auto_sync``, ``sync_conflict``) — liegen in der
  geteilten, gitignoreten ``.claude/.state.md``.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from bibi import frontmatter, repo

DEFAULT_STATE: dict[str, Any] = {
    "auto_sync": "off",
    "sync_conflict": False,
}

#: Alles außerhalb dieser Zeichen wird im Marken-Dateinamen ersetzt. Die
#: Session-ID kommt aus der Umgebung bzw. einem Hook-Payload — sie darf nicht
#: als Pfad-Traversal (``../``) im Marken-Verzeichnis landen.
_UNSAFE_SESSION_CHARS = re.compile(r"[^A-Za-z0-9._-]")

#: Sessions melden ihr Ende nie — verwaiste Marken werden nach dieser Frist beim
#: nächsten Schreibvorgang weggeräumt, damit ``data/park/`` nicht volläuft.
PARK_TTL_S = 30 * 24 * 3600

_adopted_session: str | None = None


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


def adopt_session(sid: str | None) -> None:
    """Diesen Prozess an eine Session binden.

    Für Subprozesse, die ihre ``session_id`` aus einem stdin-Payload bekommen
    statt aus der Umgebung — Hook-Handler und die Statusleiste. Ein Prozess
    gehört immer zu genau einer Session, deshalb modul-global statt durch jede
    Aufrufebene gereicht.
    """
    global _adopted_session
    sid = (sid or "").strip()
    _adopted_session = sid or None


def session_id() -> str | None:
    """ID der Claude-Code-Session, zu der dieser Prozess gehört.

    ``adopt_session()`` (Hook/Statusleiste) > ``BIBI_SESSION_ID`` (Tests,
    manuelles Übersteuern) > ``CLAUDE_CODE_SESSION_ID`` (vom Bash-Tool gesetzt).
    """
    if _adopted_session:
        return _adopted_session
    for var in ("BIBI_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        raw = os.environ.get(var, "").strip()
        if raw:
            return raw
    return None


def park_file(sid: str | None = None) -> Path | None:
    """Pfad der Park-Marke dieser Session, oder None ohne Session-ID."""
    sid = sid or session_id()
    if not sid:
        return None
    return repo.data() / "park" / _UNSAFE_SESSION_CHARS.sub("_", sid)[:128]


def _path_from_cwd() -> str | None:
    try:
        rel = Path.cwd().resolve().relative_to(repo.vault().resolve())
    except ValueError:
        return None
    rel_str = str(rel)
    return rel_str if rel_str != "." else None


def _path_from_park() -> str | None:
    """Gelesene Park-Marke, aber nur wenn der Case-Ordner wirklich (noch) existiert.

    Ein von Hand gelöschter oder auf einem anderen Knoten entfernter Case darf
    nicht als aktiv weitergemeldet werden — sonst committet ``save`` in einen
    Pfad, den es gar nicht mehr gibt.
    """
    pf = park_file()
    if pf is None:
        return None
    try:
        rel = pf.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not rel or not (repo.vault() / rel).is_dir():
        return None
    return rel


def get_path() -> str | None:
    """Vault-relativer Pfad des aktiven Case, z. B. 'case/20260517.foo-abc'.

    Zwei Quellen, in dieser Reihenfolge:

    1. **Das Bash-cwd**, wenn es in ``vault/`` liegt — die explizite Geste
       gewinnt (jemand ist bewusst in einen Case gewechselt) und funktioniert
       auch ohne Session-ID.
    2. Die **Park-Marke der Session**, die ``/open`` geschrieben hat.

    None, wenn beides fehlt — dann ist wirklich kein Case aktiv.
    """
    return _path_from_cwd() or _path_from_park()


def path_source() -> str | None:
    """Woher ``get_path()`` seinen Wert nimmt: 'cwd', 'session' oder None.

    Rein diagnostisch (``bibi-ctrl status``) — macht sichtbar, ob die Shell noch
    geparkt ist oder nur noch die Session-Marke trägt.
    """
    if _path_from_cwd():
        return "cwd"
    return "session" if _path_from_park() else None


def set_path(value: str | None) -> None:
    """Aktiven Case dieser Session setzen; ``None`` un-parkt sie.

    Schreibt beides: die **Park-Marke** ``data/park/<session_id>`` (die Quelle,
    s. ``get_path``) und den ``path:``-**Mirror** in ``.state.md`` (Fallback für
    Kontexte ohne Session-ID). Ohne Session-ID bleibt es beim Mirror allein —
    dann verhält sich alles wie vor der Park-Marke.
    """
    patch(path=value)
    pf = park_file()
    if pf is None:
        return
    if value is None:
        pf.unlink(missing_ok=True)
        return
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(value, encoding="utf-8")
    _prune_park(pf)


def _prune_park(keep: Path) -> None:
    """Marken toter Sessions wegräumen (Best effort, nie fatal)."""
    cutoff = time.time() - PARK_TTL_S
    try:
        entries = list(keep.parent.iterdir())
    except OSError:
        return
    for p in entries:
        if p == keep:
            continue
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


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
