"""`bibi-ctrl init` — interaktiver Bootstrap (DESIGN §4.10).

Phase 0.1: Platzhalter mit Subkommando-Registrierung. Die vollständige
Implementierung (Schreiben von ``~/.config/bibi/env``, Idempotenz, Bestätigung
vor Überschreiben) folgt in PLAN-0 0.3.
"""

from __future__ import annotations

import argparse


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="bibi auf dieser Maschine einrichten (~/.config/bibi/env)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    print("bibi-ctrl init — noch nicht implementiert (PLAN-0 0.3).")
    return 0
