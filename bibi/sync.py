"""Geteilte Sync-Modus-Logik (PLAN-1 §1.6 B, DESIGN §4.9).

Eine Quelle für die binäre Frage „Auto-Push an oder aus?", die alle
schreibenden Skills (``/save``, ``/close``, ``/done``, ``/sync``) honorieren.

Phase 1: die ``auto_sync``-Flag in ``.state.md`` ist die „stehende
Push-Zustimmung" (§4.9), getoggelt von ``/sync on|off``. Die spätere
Daemon-Phase kann diese eine Funktion an ``BIBI_ROLE`` koppeln, ohne die
Aufrufer zu ändern.
"""

from __future__ import annotations

from bibi import state


def auto_push_enabled() -> bool:
    """True, wenn schreibende Skills ohne Rückfrage pushen dürfen."""
    return state.get_auto_sync()
