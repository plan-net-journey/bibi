"""Job-Auswahl: Priorität + Fairness-Offset (DESIGN §4.4; PLAN-3 §3.2).

**Reine** Funktionen über Kandidatenlisten — kein DB-, kein HTTP-Zustand (wie der
``PushDebouncer``/``lifecycle``). Der Cursor ist ein expliziter Parameter, kein
Modul-State; das DB-seitige ``reserve_next`` (job_db) hält und persistiert ihn.

bibi3 hat den Fairness-Offset nie implementiert (nur ``priority DESC,
enqueued_at ASC``, mit TODO). bibi4 konkretisiert die lose core4-Beschreibung zu
einer beweisbaren Regel:

- **top** (Prioritäts-Sicht): höchstpriorisierter Kandidat überhaupt; bei
  Gleichstand der älteste (FIFO).
- **bottom** (FIFO-Sicht): ältester Kandidat *nach* dem Offset (linearer
  Durchlauf); ist der Cursor über den jüngsten hinaus, wird auf den global
  ältesten zurückgesetzt.
- **Entscheidung:** ``top.priority > bottom.priority`` → nimm **top** (Sprung auf
  höhere Priorität, Cursor unverändert); sonst nimm **bottom** und rücke den
  Cursor auf ``bottom.enqueued_at`` vor.

Wirkung: höchste Priorität gewinnt (erster Pick wählt den Prio-Job), bei
Gleichstand läuft die Queue FIFO durch — ein endlicher Schwung Hochprio-Jobs wird
abgearbeitet, danach holt der FIFO-Durchlauf die niedrigprioren Jobs der Reihe
nach (kein Aushungern bei endlicher Last).

Kandidat = Mapping mit ``id``, ``priority`` (int), ``enqueued_at`` (float),
``seq`` (monotone Einfügereihenfolge — stabiler FIFO-Tiebreak bei gleichem
``enqueued_at``).
"""

from __future__ import annotations

from collections.abc import Sequence

Candidate = dict


def _fifo_key(c: Candidate) -> tuple[float, int]:
    return (c["enqueued_at"], c["seq"])


def _priority_key(c: Candidate) -> tuple[int, float, int]:
    # höchste Priorität zuerst (negiert), dann FIFO
    return (-c["priority"], c["enqueued_at"], c["seq"])


def select_v1(candidates: Sequence[Candidate]) -> Candidate | None:
    """Einfachste Regel (§4.4 v1): ``priority DESC, enqueued_at ASC``."""
    if not candidates:
        return None
    return min(candidates, key=_priority_key)


def select(
    candidates: Sequence[Candidate], offset: float = 0.0
) -> tuple[Candidate | None, float]:
    """Fairness-Offset-Auswahl (§4.4 v2). Gibt ``(chosen | None, new_offset)``."""
    if not candidates:
        return None, offset

    after = [c for c in candidates if c["enqueued_at"] > offset]
    pool = after if after else list(candidates)  # Cursor am Ende → Sweep zurücksetzen
    bottom = min(pool, key=_fifo_key)

    top = min(candidates, key=_priority_key)

    if top["priority"] > bottom["priority"]:
        return top, offset  # Prioritäts-Sprung — Cursor unverändert
    return bottom, bottom["enqueued_at"]  # FIFO-Fortschritt — Cursor vorrücken
