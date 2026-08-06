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
from ..daemon import roles as R

#: Flag-``dest`` (argparse: Bindestriche -> Unterstriche) -> ``config.KEYS``-Name.
#: ``BIBI_NODE_ID`` bewusst nicht enthalten -- kein Flag dafür, s. Moduldoc.
_FLAG_TO_KEY = {
    "scheduler_url": "BIBI_SCHEDULER_URL",
    "role": "BIBI_ROLE",
    "remote": "BIBI_REMOTE",
    "claude_bin": "BIBI_CLAUDE_BIN",
    "node_name": "BIBI_NODE_NAME",
    "public_host": "BIBI_PUBLIC_HOST",
    # m.rau/bibi#141: der Startschlüssel des ersten Clients. Er wird gesetzt wie
    # jeder andere Wert, verhält sich danach aber anders — der Heartbeat löscht
    # ihn nach dem ersten Erfolg wieder aus der env (s. ``config.KEYS``).
    "token": "BIBI_BOOTSTRAP_TOKEN",
}


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="bibi auf dieser Maschine einrichten (~/.config/bibi/env)")
    p.add_argument("-f", "--force", action="store_true",
                   help="bestehende env ohne Rückfrage überschreiben (nur interaktiver Modus)")
    p.add_argument("--non-interactive", action="store_true",
                   help="Werte per Flags statt Prompts setzen, s. Moduldoc (PLAN-33)")
    p.add_argument("--scheduler-url")
    # m.rau/bibi#174. Bewusst **ohne** ``choices=``: argparse würde bei einem
    # unbekannten Wert selbst ``SystemExit(2)`` werfen, und dann kann ``run()``
    # die Meldung nicht formulieren — die soll aber die vier bekannten Profile
    # nennen, denn genau daran scheitert jemand, der sie nicht auswendig kennt.
    p.add_argument("--profile",
                   help="Knotenart statt Rollenliste: "
                        + " | ".join(sorted(R.PROFILES))
                        + " (m.rau/bibi#174)")
    p.add_argument("--with-ui", action="store_true",
                   help="dem Profil zusätzlich controller geben — für den "
                        "ersten Knoten eines Teams, der noch keinen Client "
                        "neben sich hat")
    p.add_argument("--role")
    p.add_argument("--remote")
    p.add_argument("--claude-bin")
    p.add_argument("--node-name")
    p.add_argument("--public-host")
    p.add_argument("--token",
                   help="Bootstrap-Token des Schedulers (m.rau/bibi#141) — "
                        "einmalig, wird nach dem ersten Heartbeat verworfen")
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
    # ── Profil auflösen (m.rau/bibi#174) ────────────────────────────────────
    # Ein Profil ist die Eingabe für einen Menschen, die Rollenliste bleibt das
    # Innenleben. Beides zusammen wären zwei Antworten auf dieselbe Frage —
    # welche gilt, wäre geraten, also lieber laut abbrechen.
    profile = getattr(args, "profile", None)
    with_ui = getattr(args, "with_ui", False)
    if profile is not None and getattr(args, "role", None) is not None:
        print("--profile und --role beantworten dieselbe Frage — bitte nur eines "
              "von beiden. Das Profil leitet die Rollen ab, die Liste setzt sie "
              "direkt (Expertenweg).", file=sys.stderr)
        return 2
    if profile is not None:
        try:
            args.role = R.profile_roles(profile, with_ui=with_ui)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    elif with_ui:
        print("--with-ui gilt für ein Profil und braucht deshalb --profile.",
              file=sys.stderr)
        return 2

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
        # Eine Frage, zwei zulässige Antworten (m.rau/bibi#174): der Profilname
        # für alle, die das Modell nicht kennen — und weiterhin die Rollenliste
        # für die, die es kennen. Sie verschwindet nicht, sie ist nur nicht mehr
        # die erste Frage. Dass beide Wörter im Prompt stehen, ist Absicht: er
        # muss aus sich heraus verständlich sein, ohne Handbuch daneben.
        "BIBI_ROLE": ("Knotenart: " + " | ".join(sorted(R.PROFILES))
                      + " (oder Rollen kommagetrennt)"),
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
    #: Die gewählte Knotenart, sofern eine gewählt wurde — sonst ``None`` für
    #: den Expertenweg (rohe Rollenliste). Sie entscheidet unten über die
    #: Scheduler-Frage, und zwar besser als der bisherige Textvergleich.
    _profile = profile

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
        # Ob überhaupt gefragt wird, entscheidet ab #174 die **Knotenart** —
        # und das schließt eine Lücke, die der Textvergleich hinterließ: das
        # Wort ``connect`` ist gar keine Rolle (``parse_role_env`` wirft es
        # weg, ``daemon_cmd`` nennt es ausdrücklich „kein BIBI_ROLE-Mitglied"),
        # es war hier nur ein Merkwort. Wer sinnvollerweise
        # ``synchronizer,controller`` eintrug, wurde deshalb **nie** nach der
        # Scheduler-URL gefragt und hatte hinterher keinen Scheduler, ohne dass
        # ihm das jemand sagte. Für den Expertenweg ohne Profil bleibt der alte
        # Vergleich bestehen: dort ist das Merkwort die einzige Angabe, die es
        # gibt.
        if key == "BIBI_SCHEDULER_URL":
            _mode = R.PROFILE_CONNECT.get(_profile) if _profile else None
            _wants_url = (_mode != "never") if _mode else ("connect" in _role_value)
            if not _wants_url:
                if explicit is not None:
                    values[key] = explicit
                else:
                    values[key] = existing.get(key, "")
                continue
            # Gefragt wird — aber für ein **Profil** taugt der Engine-Default
            # als Antwort nicht. ``http://localhost:8769`` ist die Adresse, an
            # der nie etwas antwortet (m.rau/bibi#61); ihn einzusetzen hieße,
            # eine fehlende Angabe als vorhandene auszugeben, und für ein
            # Profil mit ``required`` würde die Prüfung unten daran vorbeilaufen.
            #
            # Der **Expertenweg** behält ihn: wer ``connect`` in die Rollen
            # schreibt, hat die Frage bewusst bejaht, und dort ist der Default
            # ein Vorschlag statt einer Behauptung — so steht es seit #61, und
            # das ändert dieses Ticket nicht.
            if _profile:
                default = existing.get(key, "")

        if non_interactive:
            values[key] = explicit if explicit is not None else default
        else:
            values[key] = _prompt(labels.get(key, key), default)
            if key == "BIBI_ROLE":
                # Der Mensch hat die Knotenart gerade erst eingegeben — ab jetzt
                # gilt seine Antwort, nicht der Wert von vorher. Ist es ein
                # Profilname, leiten wir die Rollen ab; alles andere gilt als
                # Rollenliste (Expertenweg). Eine Rückfrageschleife gibt es
                # bewusst nicht: ein Tippfehler landet als unbekanntes Token in
                # der Rollenmenge, und die weist ``validate()`` beim
                # Daemon-Start ab — mit einer Meldung, die das Profil nennt.
                answer = values[key].strip()
                if answer in R.PROFILES:
                    _profile = answer
                    values[key] = R.profile_roles(answer, with_ui=with_ui)
                else:
                    _profile = None
                _role_value = values[key]

    # Ein Worker ohne Scheduler ist keine Aufstellung, sondern eine
    # Fehlkonfiguration: er startet, meldet sich gesund und bekommt nie einen
    # Auftrag — die unangenehmste Sorte, weil nichts davon nach einem Fehler
    # aussieht. Geprüft wird der **wirksame** Wert, nicht ob das Flag mitkam:
    # wer die URL schon in der env stehen hat, hat sie ja.
    if _profile and R.PROFILE_CONNECT[_profile] == "required" \
            and not values.get("BIBI_SCHEDULER_URL", "").strip():
        print(f"Profil {_profile!r} braucht eine Scheduler-URL — ohne sie hat "
              "dieser Knoten niemanden, der ihm Aufträge gibt: er startet, "
              "meldet sich gesund und empfängt nie etwas. Entweder "
              "--scheduler-url mitgeben, oder es ist in Wahrheit ein Client.",
              file=sys.stderr)
        return 2

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
