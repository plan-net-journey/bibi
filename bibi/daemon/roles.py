"""Rollen-Auflösung & Invarianten (DESIGN §4.2, PLAN-2 §2.1).

Eine Binary, die Rollen werden per Flag kombiniert (A5). ``--connect`` ist ein
*Modifikator*, keine eigene Rolle. ``--pull``/``--push`` steuern die
Synchronizer-Betriebsart (§4.3).

Ab Stufe 3.0 starten ``synchronizer``, ``scheduler`` und ``worker`` den Daemon;
``scheduler``/``worker`` servieren zunächst den eingefrorenen ``/-/``-Vertrag als
501-Stubs (echte Ausführung folgt in 3.1–3.5). Nur der ``connect``-Modifikator
(Worker-Verbund) ist noch nicht gebaut (Stufe 3.6).
"""

from __future__ import annotations

from dataclasses import dataclass

# Bekannte Rollen-Namen (aus BIBI_ROLE / Flags). ``connect`` ist Modifikator.
# ``controller`` (Phase 4) serviert die Web-App auf ``/-/`` (PLAN-4 §2.1).
KNOWN_ROLES = frozenset({"synchronizer", "scheduler", "worker", "controller"})

# Rollen/Modifikatoren, die den Daemon starten dürfen. ``connect`` (Worker-Verbund,
# Stufe 3.6) ist seit jeher per Invariante an ``worker`` gebunden (scheduler⊥connect).
STARTABLE = frozenset({"synchronizer", "scheduler", "worker", "controller", "connect"})

#: Kanonische Reihenfolge jeder ausgegebenen Rollenmenge. Eine Menge hat keine,
#: und eine wechselnde Reihenfolge in ``BIBI_ROLE`` erzeugt Diffs, die nichts
#: bedeuten.
ROLE_ORDER = ("synchronizer", "scheduler", "worker", "controller")

# ── Profile (m.rau/bibi#174) ────────────────────────────────────────────────
#
# Rechnerisch gibt es 32 Kombinationen aus vier Rollen und einem Modifikator,
# sinnvoll sind vier. Zwei der fünf Bits sind nämlich gar keine Wahl:
# ``synchronizer`` gehört auf **jeden** Knoten (Entscheidung m.rau 2026-08-05,
# #163 — seit dem 2026-08-06 in ``validate()`` und nicht mehr nur im Default),
# und ``connect`` folgt aus der Frage, ob es einen Scheduler gibt.
#
# Übrig bleiben zwei echte Fragen: Hält dieser Knoten die Job-Datenbank? Zeigt
# er eine Oberfläche? Genau die beantwortet ein Profil. Es ist die Eingabe für
# einen Menschen; die Rollenliste bleibt das Innenleben und der Expertenweg.
PROFILES: dict[str, frozenset[str]] = {
    "client":           frozenset({"synchronizer", "controller"}),
    "worker":           frozenset({"synchronizer", "worker"}),
    # **Ohne** ``controller``: der Scheduler ist Backend (Entscheidung m.rau,
    # 2026-08-06, bei der Abnahme des v0.7.2-Plans). sarasate hat die Rolle am
    # 2026-08-04 aus demselben Grund abgegeben — „der Scheduler alleine soll
    # eigentlich nur Backend sein". Für den Erstknoten eines Teams, der noch
    # keinen Client neben sich hat, gibt es ``with_ui``.
    "scheduler":        frozenset({"synchronizer", "scheduler"}),
    "scheduler+worker": frozenset({"synchronizer", "scheduler", "worker"}),
}

#: Was ``connect`` für ein Profil bedeutet — keine Vorliebe, sondern eine Folge
#: der Knotenart. ``never`` ist bei den Scheduler-Profilen die harte Invariante
#: aus ``validate()``; ``required`` beim Worker heißt: ohne Scheduler hat er
#: niemanden, der ihm Aufträge gibt, und ein solcher Knoten startet, meldet sich
#: gesund und empfängt nie etwas.
PROFILE_CONNECT: dict[str, str] = {
    "client":           "optional",
    "worker":           "required",
    "scheduler":        "never",
    "scheduler+worker": "never",
}


def profile_roles(name: str, *, with_ui: bool = False) -> str:
    """Profilname → ``BIBI_ROLE``-Zeichenkette in kanonischer Reihenfolge.

    ``with_ui`` hängt ``controller`` an — gedacht für den **ersten** Knoten
    eines Teams, der noch keinen Client neben sich hat und sonst nichts
    anzuzeigen hätte. Auf einem Profil, das ``controller`` ohnehin trägt, ist
    das Flag wirkungslos statt ein Fehler: wer es aus Gewohnheit mitgibt, soll
    keinen Abbruch bekommen.
    """
    try:
        active = set(PROFILES[name])
    except KeyError:
        raise ValueError(
            f"unbekanntes Profil {name!r} — bekannt sind: "
            f"{', '.join(sorted(PROFILES))}"
        ) from None
    if with_ui:
        active.add("controller")
    return ",".join(r for r in ROLE_ORDER if r in active)


@dataclass(frozen=True)
class Roles:
    """Aufgelöster Rollen-Satz eines Daemon-Prozesses."""

    synchronizer: bool = False
    scheduler: bool = False
    worker: bool = False
    controller: bool = False
    connect: bool = False
    pull: bool = False
    push: bool = False

    def active_names(self) -> list[str]:
        names = [n for n in ("synchronizer", "scheduler", "worker", "controller")
                 if getattr(self, n)]
        if self.connect:
            names.append("connect")
        return names


def parse_role_env(value: str) -> set[str]:
    """``BIBI_ROLE`` (z. B. ``"worker, synchronizer"``) → Menge bekannter Rollen.

    Trimmt, splittet an ``,`` und verwirft unbekannte Tokens (defensiv).
    """
    out: set[str] = set()
    for token in value.split(","):
        token = token.strip()
        if token in KNOWN_ROLES:
            out.add(token)
    return out


def resolve(
    active: set[str],
    *,
    connect: bool = False,
    pull: bool | None = None,
    push: bool = False,
) -> Roles:
    """Rollen-Menge + Modifikatoren zu einem ``Roles`` auflösen.

    Synchronizer-Betriebsart (§4.3): ``--push`` schließt ``--pull`` ein;
    ohne ``--push`` wird (bei aktivem Synchronizer) standardmäßig gepullt.
    """
    is_sync = "synchronizer" in active
    effective_pull = bool(push) or (pull if pull is not None else is_sync)
    return Roles(
        synchronizer=is_sync,
        scheduler="scheduler" in active,
        worker="worker" in active,
        controller="controller" in active,
        connect=connect,
        pull=effective_pull if is_sync else False,
        push=push if is_sync else False,
    )


def validate(r: Roles) -> list[str]:
    """Harte Invarianten (§4.2). Leere Liste = ok."""
    errs: list[str] = []
    if r.scheduler and r.connect:
        errs.append(
            "`--scheduler` und `--connect` schließen sich aus: der Scheduler "
            "ist das Verbindungsziel, er verbindet sich nicht zu sich selbst (§4.2)."
        )
    # m.rau/bibi#163, Entscheidung m.rau 2026-08-05: „Es dürfte keinen Node
    # jemals geben, der nicht die Synchronizer-Rolle hat." Getragen wurde die
    # Regel bis hierher allein vom Default ``BIBI_ROLE=synchronizer``
    # (``config.KEYS``) — das genügt für den Normalfall und für niemanden, der
    # den Wert ausdrücklich setzt. Ohne diese Rolle gleicht ein Knoten das Repo
    # nicht ab: er arbeitet auf einem Stand, den niemand mehr bewegt, und meldet
    # dabei nichts. Ein leerer Rollensatz fällt hier ebenfalls durch — er war
    # bis zum 2026-08-06 als „gültig, aber im Leerlauf" getestet, und genau
    # dieses Modell hat die Entscheidung abgelöst.
    if not r.synchronizer:
        errs.append(
            "`synchronizer` fehlt: jeder Knoten gleicht das Repo ab (#163). "
            "Ohne diese Rolle arbeitet er auf einem Stand, den niemand bewegt. "
            "Profile setzen sie von selbst — `bibi-ctrl init --profile <name>`."
        )
    return errs


# PLAN-38 (Entscheidung m.rau, 2026-07-27): ``run`` ist ein reiner
# Client-Befehl. Er läuft in-place gegen den Live-Checkout und verändert ihn —
# auf einem Knoten mit ``scheduler``- oder ``worker``-Rolle ist das kein
# Komfort, sondern ein Risiko: dort schreibt der Job in einen geteilten
# Checkout, den der Synchronizer parallel pullt und merged, und ein regulärer
# Fire desselben Jobs erwartet die reproduzierbare Worktree-Isolation
# (``execute_reservation()``, unverändert). Wer auf so einem Knoten etwas
# starten will, nimmt den Scheduler-Weg (``bibi-ctrl job start``).
LOCAL_RUN_FORBIDDEN_ROLES = ("scheduler", "worker")


def forbids_local_run(active: set[str] | Roles) -> list[str]:
    """Rollen dieses Knotens, die ``run`` ausschließen. Leer = erlaubt (Client).

    Nimmt bewusst beide Formen an: die CLI kennt nur die Rollen-Menge aus
    ``BIBI_ROLE`` (sie baut keinen Daemon, hat also kein aufgelöstes ``Roles``),
    die HTTP-Route bekommt das ``Roles`` aus ``create_app()`` gereicht. Eine
    gemeinsame Funktion, damit CLI und Route nie auseinanderlaufen.
    """
    if isinstance(active, Roles):
        return [n for n in LOCAL_RUN_FORBIDDEN_ROLES if getattr(active, n)]
    return [n for n in LOCAL_RUN_FORBIDDEN_ROLES if n in active]


def local_run_denied_message(blocked: list[str]) -> str:
    """Einheitlicher Ablehnungstext für CLI und Route (PLAN-38)."""
    return (f"`run` ist auf diesem Knoten nicht erlaubt (Rolle: {', '.join(blocked)}) — "
            "es läuft in-place gegen den Live-Checkout und ist deshalb Client-only. "
            "Für einen Lauf auf diesem Knoten: `bibi-ctrl job start <id>` (Scheduler).")


def unsupported(r: Roles) -> list[str]:
    """Aktive Rollen/Modifikatoren, die der Daemon (noch) nicht starten kann.

    Ab Stufe 3.0 sind das nur noch ``connect`` (Worker-Verbund, Stufe 3.6) —
    ``scheduler``/``worker`` starten und servieren den ``/-/``-Vertrag.
    """
    return [n for n in r.active_names() if n not in STARTABLE]
