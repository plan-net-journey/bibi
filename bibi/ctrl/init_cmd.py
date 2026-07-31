"""``bibi-ctrl init`` — interaktiver Bootstrap (DESIGN §4.10).

Fragt die drei Knoten-Parameter ab und schreibt ``~/.config/bibi/env``.
Idempotent: existiert die Datei, werden ihre Werte als Defaults vorgeschlagen
und vor dem Überschreiben wird bestätigt. Reines Python, keine externen Deps.

``--non-interactive`` (PLAN-33 Stufe 33.3): dieselbe Logik ohne ``input()`` —
ein Flag pro abfragbarem Key (``BIBI_NODE_ID`` ausgenommen, bleibt wie im
interaktiven Modus immer self-healing generiert, nie abfragbar). Fehlt ein
Flag, gilt dieselbe Default-Kette wie beim leeren Enter im interaktiven Modus
(``existing.get(key) or fallback``) — kein Caller muss alle Werte kennen.
Motivation: ``bibi-ctrl init``s Prompts per gescripteter Stdin-Eingabe zu
füttern wäre fragil (jede Prompt-Reihenfolge-Änderung bricht es leise); Flags
sind die robuste Alternative, analog zum ``--connect``/``--host``-Muster in
``daemon_cmd.py``. Gedacht für den kommenden ``/bibi-setup``-Skill (Stufe
33.5), der die Werte konversationell sammelt und dann diesen Modus aufruft —
welche Werte der Skill dabei tatsächlich erfragt vs. fest vorgibt, ist eine
Entscheidung der Skill-Seite, nicht dieser Engine-Schicht.
"""

from __future__ import annotations

import argparse
import os
import sys

from .. import config

#: Flag-``dest`` (argparse: Bindestriche -> Unterstriche) -> ``config.KEYS``-Name.
#: ``BIBI_NODE_ID`` bewusst nicht enthalten -- kein Flag dafür, s. Moduldoc.
_FLAG_TO_KEY = {
    "scheduler_url": "BIBI_SCHEDULER_URL",
    "role": "BIBI_ROLE",
    "remote": "BIBI_REMOTE",
    "claude_bin": "BIBI_CLAUDE_BIN",
    "node_name": "BIBI_NODE_NAME",
    "public_host": "BIBI_PUBLIC_HOST",
}


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="bibi auf dieser Maschine einrichten (~/.config/bibi/env)")
    p.add_argument("-f", "--force", action="store_true",
                   help="bestehende env ohne Rückfrage überschreiben (nur interaktiver Modus)")
    p.add_argument("--non-interactive", action="store_true",
                   help="Werte per Flags statt Prompts setzen, s. Moduldoc (PLAN-33)")
    p.add_argument("--scheduler-url")
    p.add_argument("--role")
    p.add_argument("--remote")
    p.add_argument("--claude-bin")
    p.add_argument("--node-name")
    p.add_argument("--public-host")
    p.set_defaults(func=run)


def _prompt(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{label}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def run(args: argparse.Namespace) -> int:
    path = config.env_path()
    existing = config.read_env(path)
    # getattr(..., default) statt args.non_interactive: robust gegen einen von
    # Hand gebauten Namespace ohne dieses Feld (bestehende Tests riefen run()
    # schon vor PLAN-33 direkt mit einem minimalen Namespace(force=...) auf,
    # nicht ueber den echten argparse-Parser).
    non_interactive = getattr(args, "non_interactive", False)

    # Flags ohne --non-interactive wuerden beim Prompt-Durchlauf unten
    # stillschweigend ignoriert (der Mensch wird trotzdem gefragt) -- lieber
    # frueh und laut ablehnen als eine falsch zusammengesetzte CLI-Aufruf
    # silent falsch interpretieren.
    passed_flags = [f for f in _FLAG_TO_KEY if getattr(args, f, None) is not None]
    if passed_flags and not non_interactive:
        print("Flags wie --scheduler-url/--role/... brauchen --non-interactive, "
              "sonst werden sie beim interaktiven Abfragen ignoriert.", file=sys.stderr)
        return 2

    if existing and not args.force and not non_interactive:
        print(f"Bestehende Konfiguration: {path}")
        for key in config.KEYS:
            print(f"  {key}={existing.get(key, '')}")
        if _prompt("Überschreiben? (j/N)", "N").lower() not in ("j", "ja", "y", "yes"):
            print("Abgebrochen — nichts geändert.")
            return 0

    values: dict[str, str] = {}
    labels = {
        "BIBI_SCHEDULER_URL": "Scheduler-URL",
        "BIBI_ROLE": "Rollen (kommagetrennt)",
        "BIBI_REMOTE": "Git-Remote",
        "BIBI_CLAUDE_BIN": "claude-Binary (Pfad/Name)",
        "BIBI_NODE_NAME": "Knoten-Name (leer = Hostname)",
        "BIBI_PUBLIC_HOST": "Von außen erreichbarer Hostname (leer = localhost, "
                            "falsch für Remote-Zugriff und App-Links)",
    }
    flag_values = {key: getattr(args, flag, None) for flag, key in _FLAG_TO_KEY.items()}

    # Die Rollen entscheiden, ob nach der Scheduler-URL überhaupt gefragt wird
    # (m.rau/bibi#61) — und stehen in ``config.KEYS`` **hinter** ihr. Deshalb
    # hier vorab auflösen, in derselben Reihenfolge wie unten in der Schleife:
    # Flag > bestehender Wert > Default.
    _role_value = (flag_values.get("BIBI_ROLE") if non_interactive else None)
    if _role_value is None:
        _role_value = existing.get("BIBI_ROLE") or config.KEYS["BIBI_ROLE"]

    # ``BIBI_ROLE`` nach vorn: es entscheidet über die Scheduler-URL, steht in
    # ``config.KEYS`` aber dahinter. Für den Menschen ist das ohnehin die
    # bessere Reihenfolge — erst *was ist dieser Knoten*, dann die Details, die
    # daraus folgen. Die übrigen Schlüssel behalten ihre Reihenfolge.
    _order = ["BIBI_ROLE", *(k for k in config.KEYS if k != "BIBI_ROLE")]
    for key in _order:
        fallback = config.KEYS[key]
        if key == "BIBI_NODE_ID":
            # Bibi4-Iteration: nie abfragen — ein Mensch soll keine UUID
            # eintippen. Bestehenden Wert übernehmen, sonst neu generieren
            # (config.node_id() selbst würde nur lesen, hier aktiv setzen,
            # damit ein --force-Rewrite ihn nicht als leer überschreibt).
            import uuid
            values[key] = existing.get(key) or uuid.uuid4().hex
            continue
        default = existing.get(key) or fallback
        explicit = flag_values.get(key)

        # Die Scheduler-URL existiert aus genau einem Grund: ``connect``. Ohne
        # diese Rolle ist sie ein Feld ohne Bedeutung, und ihr Default
        # ``http://localhost:8769`` ist eine Adresse, an der nie etwas
        # antwortet. Schlimmer als nutzlos war er sogar: ``session._host_
        # configured()`` prüft nur, *ob* die Variable gesetzt ist — jeder
        # hostlos eingerichtete Knoten hängte deshalb ``--connect`` an und
        # meldete sich bei einem Scheduler, den es nicht gibt (m.rau/bibi#61).
        #
        # Ein **bestehender** Wert bleibt trotzdem stehen: wer die Rollen
        # umstellt, soll seine Adresse nicht verlieren — unterdrückt wird nur
        # der aufgedrängte Default. Ein ausdrückliches ``--scheduler-url``
        # gewinnt ebenfalls; es ist eine Ansage, keine Voreinstellung.
        if key == "BIBI_SCHEDULER_URL" and "connect" not in _role_value:
            if explicit is not None:
                values[key] = explicit
            else:
                values[key] = existing.get(key, "")
            continue

        if non_interactive:
            values[key] = explicit if explicit is not None else default
        else:
            values[key] = _prompt(labels.get(key, key), default)
            if key == "BIBI_ROLE":
                # Der Mensch hat die Rollen gerade erst eingegeben — ab jetzt
                # gilt seine Antwort, nicht der Wert von vorher.
                _role_value = values[key]

    written = config.write_env(values, path)
    print(f"→ geschrieben: {written}")

    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        print(
            "Hinweis: CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_API_KEY ist in dieser Umgebung nicht "
            "gesetzt — claude:-Jobs schlagen ohne einen der beiden beim Spawn fehl. init "
            "schreibt dieses Credential bewusst nicht in die env-Datei (kein Secret-Handling "
            "hier) — selbst exportieren oder in der Shell-Profildatei setzen."
        )
    return 0
