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
    return errs


def unsupported(r: Roles) -> list[str]:
    """Aktive Rollen/Modifikatoren, die der Daemon (noch) nicht starten kann.

    Ab Stufe 3.0 sind das nur noch ``connect`` (Worker-Verbund, Stufe 3.6) —
    ``scheduler``/``worker`` starten und servieren den ``/-/``-Vertrag.
    """
    return [n for n in r.active_names() if n not in STARTABLE]
