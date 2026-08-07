"""``bibi-ctrl status`` — Repo-State, Knoten-Konfiguration, laufender Daemon.

Drei Blöcke, und die Reihenfolge ist die Aussage — von der Absicht zur
Wirklichkeit:

- **Repo-State**: path (cwd bzw. Park-Marke der Session) sowie auto_sync,
  sync_conflict, protocol aus ``.state.md`` (letzteres nur wenn ein Case aktiv
  ist).
- **Knoten-Config** (``~/.config/bibi/env``): Rolle, Remote, Scheduler-URL —
  **Soll-Werte**, das, was beim nächsten Start gelten soll.
- **Daemon** (``data/daemon-port.json``): was **tatsächlich** läuft, unter
  welcher Adresse, mit welchen Rollen (m.rau/bibi#59).

Die letzten beiden Blöcke können auseinanderfallen, und genau dann ist die
Auskunft wertvoll: ein Daemon läuft mit den Rollen, mit denen er gestartet
wurde, nicht mit denen, die inzwischen in der ``env`` stehen. Deshalb tragen
beide das Wort dazu — ``konfiguriert`` gegen ``laufend`` — statt zweimal
schlicht „Rollen".
"""

from __future__ import annotations

import argparse

from .. import __version__, case_store, config, repo, state


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("status", help="Repo-State und Knoten-Konfiguration anzeigen")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    # --- Repo-State ---
    s = state.read()
    case_path = state.get_path()
    # Quelle mit ausweisen: "cwd" heißt, die Shell steht im Case; "session"
    # heißt, nur die Park-Marke hält ihn noch — beides funktioniert, aber der
    # Unterschied erklärt, warum ein `cd` woanders hin nichts kaputt macht.
    src = state.path_source()
    print(f"path: {case_path or '(none)'}" + (f" ({src})" if src else ""))
    # m.rau/bibi#139: die fehlende Session-ID bekommt eine eigene, benannte
    # Zeile — nicht bloß das Fehlen von „(session)" hinter dem Pfad. Ein
    # fehlendes Wort sieht aus wie „geparkt, aber über cwd", und genau daran
    # ist der Ausfall am 2026-08-05 acht Stunden lang vorbeigelesen worden.
    # Nur bei Abwesenheit gezeigt: der Normalfall bleibt still, sonst gewöhnt
    # man sich die Zeile ab — dieselbe Erwägung wie bei ``park_foreign`` unten.
    if state.session_id() is None:
        print("session_id: (keine) — diese Sitzung kann nicht parken, "
              "der aktive Case hängt allein am cwd")
    # m.rau/bibi#97: „(none)" allein deckt zwei grundverschiedene Lagen zu — nie
    # geparkt (Repo-Scope ist richtig) und geparkt unter einer Session-ID, die es
    # nicht mehr gibt (ein Case ist gemeint). Nur die zweite bekommt eine Zeile;
    # der Normalfall bleibt still, sonst gewöhnt man sich die Zeile ab.
    for rel, n in sorted(state.foreign_parks().items()):
        who = "1 Marke einer anderen Session" if n == 1 else f"{n} Marken anderer Sessions"
        print(f"park_foreign: {rel} ({who}"
              + (" — save nähme sonst das ganze Repo)" if not case_path else ")"))
    print(f"auto_sync: {s.get('auto_sync', 'off')}")
    if s.get("sync_conflict"):
        print("sync_conflict: true")
    # PLAN-30 Ebene 3: dieselbe Quarantäne-Liste aus Ebene 2 ist die
    # Eskalations-Sicht — kein zweiter Speicher-Mechanismus. Nur gezeigt, wenn
    # wirklich etwas hängt (Happy Path bleibt unverändert).
    try:
        from bibi.daemon import merge_quarantine
        stuck = merge_quarantine.escalated(repo.root())
    except SystemExit:
        stuck = []  # außerhalb eines bibi-Repos: still bleiben, wie der Rest hier
    if stuck:
        print(f"merge_stuck: {len(stuck)} ({', '.join(stuck)})")
    if case_path:
        folder = case_store.active_case()
        if folder:
            proto = case_store.read_frontmatter(folder).get("protocol", "")
            print(f"protocol: {proto or 'off'}")

    # --- Knoten-Config ---
    print(f"bibi {__version__}")
    env_path = config.env_path()
    env = config.read_env(env_path)
    if not env:
        print(f"Keine Konfiguration ({env_path}). 'bibi-ctrl init' ausführen.")
        _print_daemon()
        return 0
    print(f"  Scheduler-URL:        {env.get('BIBI_SCHEDULER_URL', '—')}")
    print(f"  Rollen (konfiguriert): {env.get('BIBI_ROLE', '—')}")
    print(f"  Git-Remote:           {env.get('BIBI_REMOTE', '—') or '—'}")

    # --- Laufender Daemon ---
    _print_daemon()
    return 0


def _fe_url(entry: dict) -> str:
    """Die Adresse, unter der das FE dieses Daemons erreichbar ist.

    Der Bind-Host entscheidet, nicht die Konfiguration: an ``127.0.0.1``
    gebunden ist der Daemon von außen grundsätzlich nicht erreichbar, und ein
    gesetztes ``BIBI_PUBLIC_HOST`` wäre dann eine Adresse, die niemanden
    erreicht. Erst wenn er auf allen Interfaces lauscht, ist der öffentliche
    Name die richtige Auskunft — dieselbe Unterscheidung, die
    ``config.public_host()`` für App-Links trifft.
    """
    host = (entry.get("host") or "127.0.0.1").strip()
    shown = config.public_host() if host in ("0.0.0.0", "::", "") else "localhost"
    return f"http://{shown}:{entry['port']}/-/"


def _origin(entry: dict) -> str:
    """Woher der laufende Daemon stammt — Sitzung, Unit oder Handstart.

    **Drei Fälle, nicht zwei** (m.rau/bibi#55). Hier stand ein binärer Schalter
    ``{True: "Sitzung", False: "Unit"}``; ein von Hand gestarteter Daemon
    schreibt aber ebenfalls ``session=False`` und wurde damit als ``Unit``
    gemeldet, auch wenn nachweislich keine existierte.

    Die Portdatei kann die beiden hinteren Fälle nicht unterscheiden — sie hält
    fest, was der startende Prozess wusste, und der weiß nicht, ob ihn ein
    Supervisor gestartet hat. Also wird nachgesehen: ``install.installed_unit()``
    beantwortet die Frage am Dateisystem.

    Die Unterscheidung ist der Grund, warum diese Zeile existiert. Ein Client mit
    ``Sitzung (61874)`` und ein Scheduler mit ``Unit bibi-notes-daemon (8780)``
    geben zwei verschiedene Zusagen darüber, was passiert, wenn der Mensch
    weggeht — und ein Handstart gibt eine dritte: gar keine.
    """
    if entry.get("session") is True:
        return "Sitzung"
    if entry.get("session") is None:
        return "unbekannt (Daemon startete vor #59)"
    try:
        from bibi.daemon import install
        unit = install.installed_unit()
    except Exception:
        unit = None
    return f"Unit {unit}" if unit else "von Hand (kein Supervisor)"


def _print_daemon() -> None:
    """Der dritte Block. Bewusst auch dann eine Zeile, wenn nichts läuft — die
    Abwesenheit einer Auskunft ist hier selbst die Auskunft, und ein stiller
    Block ließe den Leser rätseln, ob er nur nicht hinsieht."""
    try:
        from bibi.daemon import portfile
        entry = portfile.read()
    except Exception:  # außerhalb eines Repos, unlesbare Ablage — still bleiben
        entry = None
    if entry is None:
        print("Daemon: läuft nicht")
        return
    print("Daemon:")
    print(f"  FE:               {_fe_url(entry)}")
    print(f"  Port:             {entry['port']}")
    print(f"  Herkunft:         {_origin(entry)} (PID {entry.get('pid')})")
    print(f"  Rollen (laufend): {entry.get('roles') or '—'}")
