"""``bibi-ctrl upgrade`` — die erwartete Engine-Version setzen (m.rau/bibi#155).

**Was hier automatisiert wird, ist das Setzen der Zahl — nicht das Folgen.**
Das Soll steht in ``pyproject.toml`` des Team-Repos, das Ist in der
``direct_url.json`` des venv, und ``update_status()`` vergleicht beide rein
lokal: der Knoten holt sich den Soll-Stand selbst, seit ``#103`` gibt es
deshalb keine ``Restart``-Spalte mehr. Handarbeit war nur die Zahl, einmal je
Team-Repo.

**Warum die Zahl bleibt, obwohl ein „immer das letzte" naheliegt:** sie trägt
den Rückweg, und auf dem ruht die Freigabe für autonome Release-Runden.
``Iterationen.md`` führt ihn als harte Abbruchgrenze — *„Innerhalb einer
Minor-Reihe ist ein Rollback ein Pin: Version zurücksetzen, ``uv sync``,
fertig."* Ein Knoten, der von sich aus dem letzten Release folgte, hätte diesen
Rückweg nicht: er holte sich die kaputte Version binnen 180 s wieder. **Ein
automatisches Folgen tauscht eine wiederkehrende Handbewegung gegen den Verlust
der einzigen Notbremse** — und der Fall, in dem sie gebraucht wird, ist derselbe,
in dem niemand mehr eingreifen kann. Dazu kommt: die Zahl ist committet und
beantwortet damit, welchen Stand ein Team **wann** gefahren hat.

**Entscheidung m.rau, 2026-08-12: beide Repos folgen derselben Release-Linie.**
Damit ist die Frage beantwortet, die den Umfang schnitt — es braucht keinen
Mehr-Repo-Schalter, sondern ein Kommando, das man je Repo einmal aufruft.

Dieselbe Arbeit kann der Nodes-Screen über ``_expected_version_form()`` mit zwei
Knöpfen; hier geht sie ohne Browser und über mehrere Repos hinweg.
"""

from __future__ import annotations

import argparse
import sys

from bibi.daemon import deploy


def run(args: argparse.Namespace) -> int:
    ref = (getattr(args, "ref", None) or "").strip()
    if not ref:
        # **Die höchste verfügbare, und keine eigene Sortierung.**
        # ``available_refs()`` liefert neueste zuerst; eine zweite Ordnung hier
        # wäre die dritte Stelle, an der dieses Repo Versionen sortiert.
        refs = deploy.available_refs()
        if not refs:
            print("keine Tags gefunden — Version bitte angeben "
                  "(bibi-ctrl upgrade <ref>)", file=sys.stderr)
            return 1
        ref = refs[0]
        print(f"höchste verfügbare Version: {ref}")

    ergebnis = deploy.set_expected_version(ref, push=not getattr(args, "no_push", False))
    if not ergebnis.get("ok"):
        print(ergebnis.get("error", "unbekannter Fehler"), file=sys.stderr)
        if ergebnis.get("detail"):
            print(f"  {ergebnis['detail']}", file=sys.stderr)
        return 1

    if not ergebnis.get("changed"):
        # Kein Fehler: der zweite Aufruf mit demselben Ref ist der Normalfall,
        # und ein Kommando, das sich nicht wiederholen lässt, taugt nicht als
        # Rückweg.
        print(ergebnis.get("note", f"unverändert — schon auf {ref}"))
        return 0

    print(f"{ergebnis.get('was', '?')} → {ref}"
          + ("" if ergebnis.get("pushed") else "  (nicht gepusht)"))
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "upgrade",
        help="erwartete Engine-Version setzen (pyproject.toml + uv.lock, committen, pushen)",
    )
    p.add_argument(
        "ref", nargs="?", default=None,
        help="Tag (v0.8.15) oder Branch (dev); ohne Argument die höchste verfügbare Version",
    )
    # **Ein Branch bleibt ein gültiger Wert** (#155, wörtlich): `dev`
    # unterscheidet das Urteil `branch` von `outdated`, und ein Kommando, das
    # nur Tags annimmt, nimmt der Engine-Entwicklung ihr Werkzeug. Deshalb
    # steht hier keine Tag-Prüfung — `set_expected_version()` hat ihre eigene.
    p.add_argument(
        "--no-push", action="store_true",
        help="nur lokal setzen; die anderen Knoten erfahren nichts davon",
    )
    p.set_defaults(func=run)
