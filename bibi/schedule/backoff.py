"""Retry-Backoff (DESIGN §5.5; PLAN-3 §3.5) — rein.

Verzögerung vor dem nächsten Versuch, abhängig von Strategie + Versuchsnummer.
``attempt`` ist die Nummer des *gerade beendeten* Fehlversuchs (1-basiert): die
Wartezeit gilt bis zum nächsten Versuch.
"""

from __future__ import annotations

#: Default-Basiseinheit (Sekunden) für die Backoff-Berechnung.
DEFAULT_BASE = 30.0


def delay(strategy: str, attempt: int, *, base: float = DEFAULT_BASE) -> float:
    """Backoff-Verzögerung in Sekunden.

    - ``fixed``       → ``base``
    - ``linear``      → ``base * attempt``
    - ``exponential`` → ``base * 2**(attempt-1)``

    Unbekannte Strategie ⇒ ``fixed`` (defensiv). ``attempt < 1`` ⇒ 0.
    """
    if attempt < 1:
        return 0.0
    if strategy == "linear":
        return base * attempt
    if strategy == "exponential":
        return base * (2 ** (attempt - 1))
    return base  # fixed (Default)
