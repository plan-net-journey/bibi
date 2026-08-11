"""``bibi-ctrl soul`` — aktive Persona für die Session merken (PLAN-13 Stufe 13.1).

Souls sind team-eigener Content, nicht Engine-Code: verfügbare Personas werden
dynamisch aus ``.claude/souls/*.SOUL.md`` im aktuellen Team-Repo gelesen, nicht
hartcodiert — jedes Team pflegt sein eigenes Souls-Set. Die Wahl wird
repo-global in ``.state.md`` persistiert (``state.py``, analog ``maintenance``),
case-insensitiv gematcht (Dateiname trägt die kanonische Schreibweise).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bibi import repo, state

_SOUL_FILE_RE = re.compile(r"^\d+\.(?P<name>[^.]+)\.SOUL\.md$")


def souls_dir() -> Path:
    return repo.root() / ".claude" / "souls"


def available_souls() -> dict[str, str]:
    """``{lowercase name: kanonischer Name}`` aller ``.claude/souls/*.SOUL.md``,
    sortiert nach Dateiname. Leeres Dict, wenn kein Souls-Verzeichnis existiert."""
    d = souls_dir()
    if not d.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(d.glob("*.SOUL.md")):
        m = _SOUL_FILE_RE.match(p.name)
        if m:
            out[m.group("name").lower()] = m.group("name")
    return out


def soul_path(canonical: str) -> Path | None:
    """Die Datei zur kanonischen Persona — oder ``None``, wenn keine passt."""
    d = souls_dir()
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.SOUL.md")):
        m = _SOUL_FILE_RE.match(p.name)
        if m and m.group("name").lower() == canonical.lower():
            return p
    return None


#: Der Rahmen um die Persona. Er sagt, **was** der Text ist und woher er kommt
#: — ohne ihn stünde eine Ich-Beschreibung ohne Absender im Kontext, und ein
#: Modell, das nicht weiß, ob es eine Anweisung oder ein Zitat liest, rät.
_ANSAGE = (
    "Die aktive Persona dieses Team-Repos ist **{name}** "
    "(`.claude/souls/`, gesetzt über `/soul`). Nimm sie für diese Sitzung an. "
    "Sie gilt auch für Subagenten, die du startest.\n\n{text}"
)


def run_hook(_: argparse.Namespace) -> int:
    """Die aktive Persona in den Kontext injizieren (#75 Teil B).

    **Teil A machte den Zustand sichtbar, nicht wirksam** — und sichtbar zu
    machen, was nicht wirkt, war ausdrücklich die halbe Antwort. Der Befund
    von m.rau: *„Sie vermittelt den Eindruck, als würde die Soul gar nicht
    richtig greifen, weil gar nix im Kontext ist außer die Information."*

    **Entscheidung m.rau, 2026-08-11: Variante 3.** Sie gewann, weil sie als
    einzige keinen Bruch kauft: sie *stellt wieder her*, statt zu *ersetzen*.
    ``/soul`` mitten in der Sitzung bleibt sofort wirksam (der Skill lädt die
    Datei, das Modell übernimmt sie), und weil ``SessionStart`` auch bei
    ``compact`` feuert, kommt die Persona nach einer Kompaktierung von selbst
    zurück — ohne Zutun des Modells, und das war der eigentliche Anlass.

    **Dasselbe Kommando bedient ``SubagentStart``**, damit die Zusage *„gilt
    auch für Subagenten"* strukturell eingelöst wird statt als Bitte an das
    Modell, sie weiterzureichen: ein Subagent, der die Persona nur bekommt,
    wenn jemand daran denkt, bekommt sie irgendwann nicht. Der Ereignisname
    kommt deshalb aus der Eingabe und wird zurückgegeben — ein fest
    verdrahteter wäre im Subagenten der falsche und würde still verworfen.

    **Nichts hier darf eine Sitzung kosten.** Der Hook läuft *vor* dem ersten
    Prompt; wer an dieser Stelle scheitert, scheitert dort, wo noch niemand
    etwas tun konnte. Keine Soul, eine Soul ohne Datei, eine unlesbare
    Eingabe — alle drei enden still mit 0. Der Fall „keine" ist ohnehin kein
    Fehler, sondern *„der Weg ohne weiteren Input zur Soul"* (m.rau).
    """
    import json

    ereignis = "SessionStart"
    try:
        eingabe = json.loads(sys.stdin.read() or "{}")
        if isinstance(eingabe, dict):
            ereignis = eingabe.get("hook_event_name") or ereignis
    except (ValueError, OSError):
        pass

    name = state.get_soul()
    if not name:
        return 0
    pfad = soul_path(name)
    if pfad is None:
        return 0
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError:
        return 0
    if not text.strip():
        return 0

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": ereignis,
        "additionalContext": _ANSAGE.format(name=name, text=text),
    }}, ensure_ascii=False))
    return 0


def run(args: argparse.Namespace) -> int:
    if getattr(args, "hook", False):
        return run_hook(args)

    souls = available_souls()

    if not args.name:
        current = state.get_soul()
        if current:
            print(current)
        else:
            print("keine Soul aktiv", file=sys.stderr)
        return 0

    key = args.name.strip().lower()
    if key not in souls:
        print(f"unbekannte Soul: {args.name}", file=sys.stderr)
        if souls:
            print("verfügbar: " + ", ".join(sorted(souls.values())), file=sys.stderr)
        else:
            print(f"kein Souls-Verzeichnis unter {souls_dir()}", file=sys.stderr)
        return 1

    canonical = souls[key]
    state.set_soul(canonical)
    print(canonical)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "soul",
        help="aktive Persona setzen/anzeigen (.claude/souls/*.SOUL.md)",
    )
    p.add_argument(
        "name", nargs="?", default=None,
        help="Persona-Name (case-insensitiv); ohne Argument: aktuelle Soul anzeigen",
    )
    # **Ein Flag, kein Unterkommando** — anders als bei `sync hook-start`, und
    # der Grund ist das Positional darüber: `soul hook-start` landete darin
    # („unbekannte Soul: hook-start"). Ein Unterkommando hätte damit einen
    # Namen belegt, den eine Persona tragen kann.
    p.add_argument(
        "--hook", action="store_true",
        help="SessionStart-/SubagentStart-Hook: die aktive Persona als "
             "additionalContext ausgeben (#75)",
    )
    p.set_defaults(func=run)
