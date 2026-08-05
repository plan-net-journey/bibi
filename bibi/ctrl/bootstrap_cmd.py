"""``bibi-ctrl bootstrap-token`` — Startschlüssel für den **ersten** Client.

Er löst den Deadlock, den die Schranke an ``approve``/``block`` erst erzeugt
(m.rau/bibi#141): ein frischer Scheduler hat null ``approved``-Knoten, der
erste Client meldet sich als ``pending`` — und weil bibi5 kein Host-FE mehr
kennt, ist niemand berechtigt, ihn freizugeben. Ohne diesen Weg bliebe nur ein
``UPDATE`` von Hand in der Scheduler-DB.

**Ausdrücklich kein Wiedergänger von ``BIBI_CONNECT_SECRET``** (Nodes.md §3.3).
Das war ein unbefristetes, geteiltes Geheimnis in der Config jedes Knotens, das
jeden jederzeit einließ. Dieser Schlüssel gilt einmal, 24 Stunden, für genau
eine Freigabe — und er wird nur ausgegeben, solange noch kein Knoten
freigeschaltet ist. Diese letzte Bedingung ist die wichtigste: sie sorgt dafür,
dass er nie zur bequemen Abkürzung werden kann, weil er in der Lage, in der man
ihn dafür missbrauchen wollte, gar nicht mehr entsteht.

Erzeugt wird er hier und nicht über eine Route: auf dem Host, per CLI, ist
genau die Stelle, an der ohnehin ein Mensch mit Shell-Zugang steht, wenn er
einen Scheduler aufsetzt.
"""

from __future__ import annotations

import argparse
import os
import time

from bibi.daemon import job_db


def _scheduler_url() -> str:
    """Die URL für die fertige Befehlszeile — beste verfügbare Auskunft.

    Auf dem Host ist ``BIBI_SCHEDULER_URL`` oft nicht gesetzt (er *ist* der
    Scheduler), deshalb der Platzhalter statt einer erfundenen Adresse: eine
    falsche URL wäre schlimmer als eine sichtbar auszufüllende.
    """
    return (os.environ.get("BIBI_SCHEDULER_URL", "").strip()
            or "http://<scheduler-host>:8780")


def run(args: argparse.Namespace) -> int:  # noqa: ARG001
    conn = job_db.connect()
    try:
        token = job_db.create_bootstrap_token(conn)
    finally:
        conn.close()

    if token is None:
        print("Es gibt bereits einen freigeschalteten Knoten — der Bootstrap ist vorbei.")
        print("Weitere Knoten werden im Nodes-Screen freigegeben, nicht per Startschlüssel.")
        return 1

    bis = time.strftime("%d/%m %H:%M",
                        time.localtime(time.time() + job_db.BOOTSTRAP_TTL_S))
    print(f"Bootstrap-Token für den ersten Client "
          f"(gültig bis {bis}, eine Verwendung):")
    print()
    # Nodes.md §3.3 skizziert `init --connect …` — dieses Flag gibt es bei
    # `init` nicht, es sitzt an `daemon`. Hier steht die Zeile, die der Parser
    # wirklich annimmt; ein Test legt sie ihm vor, statt sie anzusehen.
    print(f"    bibi-ctrl init --non-interactive --scheduler-url {_scheduler_url()}"
          f" --role connect --token {token}")
    print()
    print("Danach ist dieser Knoten approved und gibt alle weiteren im "
          "Nodes-Screen frei.")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("bootstrap-token",
                       help="Startschlüssel für den ersten Client ausgeben (m.rau/bibi#141)")
    p.set_defaults(func=run)
