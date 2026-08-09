"""Die Selbstauskunft eines Knotens — dieselben Felder, die sein Heartbeat
trägt, nur von ihm selbst erhoben statt beim Scheduler abgeholt.

Sie ist nötig, weil **der Scheduler sich nie bei sich selbst meldet** (dieselbe
``scheduler``+``connect``-Ausschluss-Invariante wie beim „warum sehe ich den
Worker nicht"-Fund, ``daemon/roles.py``). Seine ``WorkerRegistry`` führt jeden
anderen Knoten und ihn selbst nicht — wer die Flotte vollständig zeigen will,
muss ihn also **fragen**, statt ihn zu suchen.

Solange der Nodes-Screen nur auf dem Host lief, genügte dafür eine lokale
Fassung im Controller: der eine fehlende Knoten war der eigene. Mit dem Wegfall
der ``controller``-Rolle auf sarasate (2026-08-04) läuft der Screen auf einem
Client, und der kann über einen fremden Knoten nichts lokal erheben. Damit
wurde aus einer Anzeigehilfe eine Auskunft, die über HTTP gehen muss.
"""

from __future__ import annotations

import os
import socket


def self_entry(roles) -> dict:
    """Wer antwortet hier — Identität, Rollen, Repo-Stand und Engine.

    Die Feldnamen sind bewusst die des Heartbeats (``scheduler_client.register()``),
    damit die Zeile im Nodes-Screen neben echten, gemeldeten Zeilen nicht anders
    aussieht und derselbe Renderer sie ohne Sonderfall trägt.

    Durchweg defensiv (§2.7): ein Knoten, der seine eigene Herkunft nicht
    ermitteln kann, soll melden was er weiß, statt die Auskunft ganz zu
    verlieren.
    """
    from bibi import config, git_ops, repo as repo_mod
    from bibi.engine_info import engine_info
    from bibi.git_status import working_tree_status

    git_user = git_status = "—"
    git_commit: str | None = None
    try:
        root = repo_mod.root()
        git_user = git_ops.git_user_name(root) or "—"
        s = working_tree_status(root)
        if s is not None:
            git_status = f"{s.branch or '(detached)'} · {s.tree} · {s.sync}"
            git_commit = s.oid[:7] if s.oid else None
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        pass
    # m.rau/bibi#19: welche Engine fährt dieser Knoten — für den Host war das
    # ausgerechnet die Angabe, die fehlte, weil sein Eintrag nicht aus einem
    # Heartbeat entsteht. m.rau/bibi#67 ergänzt den Arbeitsbaum des
    # Engine-Checkouts (``None`` bei einem VCS-Pin, dann entfällt der Chip).
    engine = engine_tree = None
    try:
        info = engine_info()
        engine, engine_tree = info.label(), info.tree_status()
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        pass
    # **Der laufende Stand, nicht der auf der Platte** (m.rau/bibi#102).
    # ``engine_info()`` liest ``direct_url.json`` im venv, und zwar bei jedem
    # Aufruf frisch: nach einem ``uv sync`` ohne Neustart meldet ein Daemon
    # sonst den neuen Stand, waehrend er den alten faehrt. Diese Angabe reist
    # im Heartbeat zum Scheduler und wird dort zum Chip im Nodes-Screen — live
    # am 2026-08-09 stand `sarasate:8780` mit `v0.7.11` und `current` da,
    # waehrend der Prozess seit `10:59:36` v0.7.10-Code ausfuehrte.
    #
    # Die Portdatei haelt den Startstand fest (``portfile.write(engine=…)``)
    # und ist die einzige Stelle, die das tut. Fehlt sie oder ihr Feld, bleibt
    # es beim venv: unbekannt ist kein Grund, die Auskunft ganz aufzugeben.
    try:
        from bibi.daemon import portfile
        laufend = (portfile.read() or {}).get("engine")
        if laufend:
            engine = laufend
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        pass
    raw_port = os.environ.get("BIBI_DAEMON_PORT")
    env = config.read_env()
    # m.rau/bibi#44: ob ein Neustart-Knopf für diesen Knoten überhaupt einen
    # Neustart bedeutet. ``None`` bei einem Daemon, der vor #59 startete.
    try:
        from bibi.daemon import portfile
        session = (portfile.read() or {}).get("session")
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        session = None
    return {
        "session": session,
        "worker": (env.get("BIBI_NODE_NAME") or env.get("BIBI_WORKER_NAME")
                   or socket.gethostname()),
        "host": socket.gethostname(),
        "role": ",".join(roles.active_names()),
        "node_id": config.node_id(),
        "port": int(raw_port) if raw_port and raw_port.isdigit() else None,
        "git_user": git_user,
        "git_status": git_status,
        "git_commit": git_commit,
        "engine": engine,
        "engine_tree": engine_tree,
        # Ein Knoten, der von sich selbst berichtet, ist per Definition
        # erreichbar — und er schaltet sich nicht selbst frei (kein eigener
        # approved_nodes-Eintrag, PLAN-32 Stufe 32.1).
        "stale": False,
        "connected_at": None,
        "last_heartbeat": None,
        "approval_status": "approved",
    }
