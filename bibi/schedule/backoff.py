"""Retry-Backoff (DESIGN §5.5; PLAN-3 §3.5) — rein.

Verzögerung vor dem nächsten Versuch, abhängig von Strategie + Versuchsnummer.
``attempt`` ist die Nummer des *gerade beendeten* Fehlversuchs (1-basiert): die
Wartezeit gilt bis zum nächsten Versuch.
"""

from __future__ import annotations

#: Default-Basiseinheit (Sekunden) für die Backoff-Berechnung — error-time-
#: Default (§5.5), nur der letzte Fallback, wenn weder ein expliziter
#: bibi.job.Failed(seconds=N) noch Schedule-Frontmatter (`error_time:`) noch
#: der globale BIBI_RETRY_BASE-Env etwas anderes vorgeben.
DEFAULT_BASE = 180.0


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


def exhausted(attempt: int, attempts: int) -> bool:
    """Sind die gewährten Wiederholungen aufgebraucht? (m.rau/bibi#128)

    ``attempts`` meint **N Retries zusätzlich zum ersten Lauf** (``parser.py``,
    Default 0 = ein Versuch, kein Retry). ``attempt`` ist der Zähler der
    bisherigen Versuche.

    **Es gibt diese Funktion, weil dieselbe Entscheidung an vier Stellen stand
    und an einer davon fehlte.** ``wrapper._finish()`` traf sie für den
    Wrapper-Pfad; ``job_db.reserve_next()`` und ``job_db.sweep()`` haben ihre
    eigenen Fassungen entfernt, beide ausdrücklich unter Berufung auf jene —
    *„ein Job landet per Konstruktion nur dann als ``failed`` in der DB, wenn
    der zuletzt gewährte Retry noch aussteht"*. Der **Setup-Fehler-Pfad** in
    ``worker.py`` hat diese Zusicherung nie eingehalten: er schrieb ``failed``
    mit neuem Termin, ohne ``attempts`` je anzusehen — er kann den Wrapper ja
    gerade nicht starten.

    Live am 2026-08-10: ein Job mit ``attempts: 0`` stand bei **488** Versuchen,
    24 Stunden lang alle drei Minuten derselbe deterministische Fehlschlag.

    **Kein viertes Sicherheitsnetz, sondern die eine Regel, auf die sich die
    drei berufen.** Wer künftig ``failed`` schreibt, fragt hier.
    """
    return attempt >= attempts
