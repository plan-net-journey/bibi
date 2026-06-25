"""Rollen-Auflösung & Invarianten (DESIGN §4.2, PLAN-2 §2.1).

Eine Binary, die Rollen werden per Flag kombiniert (A5). ``--connect`` ist ein
*Modifikator*, keine eigene Rolle. ``--pull``/``--push`` steuern die
Synchronizer-Betriebsart (§4.3).

Phase 2 implementiert real nur ``synchronizer``; ``scheduler``/``worker`` und
der ``connect``-Modifikator werden erkannt, aber als „noch nicht in dieser
Phase" gemeldet (PLAN-2 §2.1).
"""

from __future__ import annotations

from dataclasses import dataclass

# Bekannte Rollen-Namen (aus BIBI_ROLE / Flags). ``connect`` ist Modifikator.
KNOWN_ROLES = frozenset({"synchronizer", "scheduler", "worker"})

# In Phase 2 tatsächlich aktive Rollen/Modifikatoren.
PHASE2_IMPLEMENTED = frozenset({"synchronizer"})


@dataclass(frozen=True)
class Roles:
    """Aufgelöster Rollen-Satz eines Daemon-Prozesses."""

    synchronizer: bool = False
    scheduler: bool = False
    worker: bool = False
    connect: bool = False
    pull: bool = False
    push: bool = False

    def active_names(self) -> list[str]:
        names = [n for n in ("synchronizer", "scheduler", "worker") if getattr(self, n)]
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


def unsupported_in_phase2(r: Roles) -> list[str]:
    """Aktive Rollen/Modifikatoren, die Phase 2 (noch) nicht ausführt."""
    return [n for n in r.active_names() if n not in PHASE2_IMPLEMENTED]
