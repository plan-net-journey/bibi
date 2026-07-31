"""Welche Engine läuft hier eigentlich? (m.rau/bibi#19, R2 aus dem
Release-Management-Case)

Der installierte Stand ist nicht ``bibi.__version__``: die Konstante steht
statisch im Quellcode und sagt bei einem editable install überhaupt nichts über
das Installierte. Die Wahrheit liegt in den Metadaten des installierten Pakets —
``importlib.metadata`` liefert beides ohne Pfad-Arithmetik:

- die Version aus dem ``.dist-info`` (also die des tatsächlich installierten
  Pakets, nicht die eines beliebigen Checkouts im Pfad);
- ``direct_url.json`` (PEP 610) mit ``commit_id`` und ``requested_revision``,
  wenn per VCS-URL installiert wurde, bzw. ``dir_info.editable`` für einen
  editable install.

Der letzte Punkt ist der eigentliche Anlass: ein Knoten, der gegen ein
Arbeits-Checkout läuft statt gegen den gepinnten Stand, war bisher von außen
nicht erkennbar — am Mac ist das schon einmal unbemerkt passiert und ging nur
gut aus, weil die Lock zufällig auf denselben Commit zeigte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EngineInfo:
    version: str | None = None   # "0.2.0" aus dem .dist-info
    ref: str | None = None       # requested_revision: "v0.2.0" | "dev" | None
    commit: str | None = None    # commit_id, vollständig
    editable: bool = False       # läuft gegen ein Arbeits-Checkout
    url: str | None = None       # Herkunfts-URL (VCS oder Verzeichnis)

    @property
    def short_commit(self) -> str | None:
        return self.commit[:7] if self.commit else None

    @property
    def local(self) -> bool:
        """Aus einem **Verzeichnis** installiert statt aus einer VCS-URL, ohne
        editable zu sein (m.rau/bibi#58).

        Der Unterschied zum editable install ist nur die Auffindbarkeit, und
        zwar zu Ungunsten dieses Falls: ein editable install ist wenigstens
        offensichtlich, wenn man ihn sucht. Eine Kopie ist eingefroren und trägt
        keinen Hinweis auf ihre Herkunft — sie sieht aus wie ein Release und ist
        keins. Live gefunden am 2026-07-31: ein Knoten meldete ``0.4.0`` und
        lief gegen eine Kopie des Arbeits-Checkouts, samt uncommitteter
        Änderungen, die nie in einem Release waren.
        """
        return bool(self.url and self.url.startswith("file://")
                    and not self.editable)

    def label(self) -> str:
        """Eine Zeile für Menschen — die Bezeichnung, nicht die Rohdaten.

        Ein Tag ist die beste Auskunft („v0.2.0"), ein Branch die zweitbeste
        („dev @ 86ea20e" — der Name allein sagt bei einem wandernden Branch
        nichts). Ein editable install und ein lokaler Build werden ausdrücklich
        benannt, weil sie der Grund sind, warum es dieses Modul gibt.
        """
        if self.editable:
            return f"{self.version or '?'} (editable)"
        if self.local:
            return f"{self.version or '?'} (local)"
        if self.ref and self.version and self.ref.lstrip("v") == self.version:
            # Tag-Pinning: Ref und Version sagen dasselbe, einmal genügt.
            return self.ref
        if self.ref:
            sc = self.short_commit
            return f"{self.ref} @ {sc}" if sc else self.ref
        if self.version:
            return self.version
        return self.short_commit or "n/a"


def engine_info(dist_name: str = "bibi") -> EngineInfo:
    """Metadaten des installierten ``bibi``-Pakets. Nie eine Exception — ein
    Knoten, der seine eigene Herkunft nicht ermitteln kann, soll melden was er
    weiß, nicht den Heartbeat verlieren."""
    try:
        import importlib.metadata as md
        dist = md.distribution(dist_name)
    except Exception:
        return EngineInfo()

    version = getattr(dist, "version", None)

    raw = None
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        raw = None
    if not raw:
        # Aus einem Index/Wheel installiert (kein PEP-610-Eintrag): die Version
        # ist dann die ganze Auskunft, und das ist in Ordnung.
        return EngineInfo(version=version)

    try:
        data = json.loads(raw)
    except Exception:
        return EngineInfo(version=version)

    vcs = data.get("vcs_info") or {}
    dir_info = data.get("dir_info") or {}
    return EngineInfo(
        version=version,
        ref=vcs.get("requested_revision"),
        commit=vcs.get("commit_id"),
        editable=bool(dir_info.get("editable")),
        url=data.get("url"),
    )
