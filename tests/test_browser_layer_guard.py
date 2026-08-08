"""Die laute Meldung der Browser-Ebene (`#84`).

*„Wo die Browser-Ebene nicht läuft (CI oder lokal), sagt sie das laut — eine
ausgefallene Prüfung muss lauter sein als ihr Ergebnis."* Das ist eine der vier
Bedingungen des Tickets, und sie ist die einzige, die man versehentlich
zurücknehmen kann, ohne dass etwas rot wird: eine Meldung, die ausbleibt, sieht
aus wie ein Lauf ohne Befund.

Geprüft wird hier die Entscheidungsfunktion, nicht die Formatierung — ob also
erkannt wird, **dass** übersprungen wurde. Genau daran ist die erste Fassung
gescheitert.
"""

from __future__ import annotations

import conftest


class _Bericht:
    def __init__(self, longrepr) -> None:
        self.longrepr = longrepr


class _Melder:
    """Das Nötigste eines ``terminalreporter``: seine ``stats``."""

    def __init__(self, uebersprungen: list) -> None:
        self.stats = {"skipped": uebersprungen}


def test_a_skipped_browser_test_is_recognised_by_its_reason():
    melder = _Melder([_Bericht(("t.py", 3, "Skipped: use --browser-tests to run"))])
    assert conftest._browser_uebersprungen(melder)


def test_a_missing_playwright_counts_as_not_run_too():
    """Der zweite Weg, auf dem die Ebene ausfällt: das Paket fehlt.

    Für den Lauf ist das dasselbe Ergebnis — geprüft wurde nichts —, und die
    Meldung gehört deshalb auch hierher. Ein Knoten ohne `pytest-playwright`
    ist der Normalfall nach einem frischen Klon."""
    melder = _Melder([_Bericht(("t.py", 1, "Skipped: pytest-playwright fehlt — …"))])
    assert conftest._browser_uebersprungen(melder)


def test_an_ordinary_skip_does_not_trigger_the_notice():
    """Sonst stünde die Meldung unter jedem Lauf — und eine Warnung, die immer
    da steht, liest niemand mehr."""
    melder = _Melder([_Bericht(("t.py", 7, "Skipped: use --slow to run"))])
    assert not conftest._browser_uebersprungen(melder)


def test_a_run_without_any_skips_is_silent():
    assert not conftest._browser_uebersprungen(_Melder([]))


def test_the_check_reads_reports_and_not_collected_items():
    """**Der eigentliche Fund, und deshalb ein eigener Test.**

    Die Suite läuft per Vorgabe unter `pytest-xdist` (`addopts = -n auto`).
    Dort sammeln die Worker ein, der Zusammenfassungs-Hook läuft auf dem
    Controller — dessen Item-Liste ist leer. Die erste Fassung dieser Prüfung
    zählte eingesammelte Items und schwieg deshalb im Normalbetrieb;
    ausgerechnet die Meldung, deren einziger Zweck es ist, nicht zu schweigen.

    Dieser Test hält die Quelle fest: die Ergebnisberichte, die von den Workern
    zurückwandern. Ein `terminalreporter` **ohne** jede Item-Liste muss
    genügen."""
    melder = _Melder([_Bericht(("t.py", 3, "Skipped: use --browser-tests to run"))])
    assert not hasattr(melder, "items")
    assert conftest._browser_uebersprungen(melder)
