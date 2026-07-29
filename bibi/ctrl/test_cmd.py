"""``bibi-ctrl test`` — Deprecation-Alias auf ``bibi-ctrl run`` (PLAN-38).

``test`` war der In-place-Sibling zu ``run`` (User-Fund 2026-07-14): direkt
gegen den Live-Checkout statt gegen einen frischen ``trunk``-Worktree, dirty
erlaubt, committet nie. Seit PLAN-38 (Entscheidung m.rau, 2026-07-27) macht
``run`` genau das — die Zweiteilung war der eigentliche Fehler, nicht eine der
beiden Hälften: sie zwang den Menschen bei jedem Aufruf zu einer Entscheidung,
die aus der Knotenrolle folgt (``run`` ist Client-only) und nicht aus der
Wortwahl folgen sollte.

Dieses Subkommando bleibt eine Übergangszeit als Alias bestehen — für
Gewohnheit und für Skripte, die es noch aufrufen —, gibt einen
Deprecation-Hinweis aus und delegiert unverändert an ``run``. Pre-1.0, kein
Backcompat-Zwang: es verschwindet, sobald der Hinweis niemanden mehr erreicht.
Die HTTP-Route ``POST /-/test`` ist bereits ersatzlos entfallen (sie hatte
weder eine ``DaemonClient``-Methode noch einen Button im Frontend).
"""

from __future__ import annotations

import argparse
import sys

from bibi.ctrl.run_cmd import run

_DEPRECATION = ("bibi-ctrl test: veraltet — `run` läuft seit PLAN-38 selbst in-place "
                "gegen den lokalen Stand. Bitte künftig `bibi-ctrl run` verwenden.")


def test(args: argparse.Namespace) -> int:
    print(_DEPRECATION, file=sys.stderr)
    return run(args)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "test", help="veraltet — Alias auf `run` (PLAN-38)")
    p.add_argument("slug", nargs="?", default=None, help="Slug einer erfassten Schedule-MD")
    p.add_argument("--cmd", dest="command", default=None, help="ad-hoc Shell-Befehl")
    p.add_argument("--kind", default="job", help="Typ für --cmd (default: job)")
    p.set_defaults(func=test)
