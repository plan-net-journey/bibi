"""``bibi-ctrl sync …`` — Git-Abgleich (PLAN-1 §1.5, §4.9; Neufassung PLAN-25
Befund 8).

- ``sync on|off``      — auto_sync-Flag (stehende Push-Zustimmung) umschalten
- ``sync``            — manueller, (fast) immer wirksamer Abgleich:
                        1. Case-fremde dirty Änderungen automatisch clustern
                           + committen (ein Commit je fremdem Case-Ordner +
                           ein Sammel-Commit für Case-loses) — keine eigene
                           Freigabe je Cluster, der ``/sync``-Aufruf selbst
                           ist die Freigabe (Steering im Gespräch davor).
                        2. Immer integrieren (fetch + rebase) — deckt die
                           neuen Cluster-Commits UND schon vorhandene
                           "ahead"-Commits im aktiven Projekt ab. Konflikt →
                           im Tree lassen + ``sync_conflict`` (KI-Auflösung).
                        3. Immer pushen, unabhängig von ``auto_sync`` — der
                           ``/sync``-Aufruf ist die Freigabe für den Push.
                        4. Dirty Änderungen IM aktiven Projekt: nur anzeigen,
                           nicht anfassen — auf ``/save`` verweisen.
- ``sync continue``   — nach KI-Auflösung der Marker den Rebase fortsetzen + push
- ``sync abort``      — offenen Rebase abbrechen
- ``sync hook-stop``  — Hintergrund: bei auto_sync transient committen + push
- ``sync hook-start`` — Hintergrund: bei auto_sync pullen; Konflikt-Warnung surface
"""

from __future__ import annotations

import argparse
import sys

from bibi import git_ops, repo, state


def _toggle_on(_: argparse.Namespace) -> int:
    state.set_auto_sync(True)
    print("auto_sync → on")
    return 0


def _toggle_off(_: argparse.Namespace) -> int:
    state.set_auto_sync(False)
    print("auto_sync → off")
    return 0


def _print_conflicts() -> None:
    for f in git_ops.conflicted_files():
        print(f"  {f}", file=sys.stderr)


def run_sync(_: argparse.Namespace) -> int:
    """Manueller Abgleich — Neufassung PLAN-25 Befund 8: cluster-committen
    (case-fremde Änderungen) → integrieren (immer) → pushen (immer). Nur
    dirty Änderungen im aktiven Projekt bleiben unangetastet (→ ``/save``)."""
    if git_ops.is_rebase_in_progress():
        print("Rebase offen — Marker auflösen, dann `bibi-ctrl sync continue` "
              "(oder `sync abort`).", file=sys.stderr)
        _print_conflicts()
        return 1

    active_case_rel = state.get_path()
    other_cases, caseless, active_dirty = git_ops.cluster_dirty_paths(
        git_ops.dirty_paths(), case_dir_name=repo.case_dir_name(),
        active_case_rel=active_case_rel)

    for case_rel in sorted(other_cases):
        label = case_rel.rsplit("/", 1)[-1]
        if git_ops.stage_and_commit(repo.vault() / case_rel, f"sync: {label}"):
            print(f"committed: sync: {label}")
    if caseless and git_ops.stage_and_commit_paths(caseless, "sync: other changes"):
        print("committed: sync: other changes")

    branch = git_ops.current_branch()
    ok, kind = git_ops.integrate(branch, keep_conflict=True)
    if not ok:
        if kind == "conflict":
            state.set_sync_conflict(True)
            print("⚠ Merge-Konflikt — Marker auflösen, dann `bibi-ctrl sync continue`.",
                  file=sys.stderr)
            _print_conflicts()
        else:
            print(f"Abgleich fehlgeschlagen: {kind}", file=sys.stderr)
        return 1
    print("integrated")

    pok, pmsg = git_ops.push(branch)
    if not pok:
        print(f"push fehlgeschlagen: {pmsg}", file=sys.stderr)
        return 1
    state.set_sync_conflict(False)
    print("push ok")

    if active_dirty:
        print("Änderungen im aktiven Projekt (nicht angefasst) — `/save` ausführen:",
              file=sys.stderr)
        for p in active_dirty:
            print(f"  {p}", file=sys.stderr)

    print("sync ok")
    return 0


def run_continue(_: argparse.Namespace) -> int:
    if not git_ops.is_rebase_in_progress():
        print("kein Rebase im Gange.")
        return 0
    ok, log, kind = git_ops.continue_rebase_and_push()
    for line in log:
        print(line)
    if ok:
        state.set_sync_conflict(False)
        return 0
    if kind == "conflict":
        _print_conflicts()
    return 1


def run_abort(_: argparse.Namespace) -> int:
    if not git_ops.is_rebase_in_progress():
        print("kein Rebase im Gange.")
        return 0
    git_ops.abort_rebase()
    print("rebase abgebrochen.")
    return 0


def run_hook_stop(_: argparse.Namespace) -> int:
    """Stop-Hook: bei auto_sync transient committen + integrieren + push. Immer 0."""
    if not state.get_auto_sync():
        return 0
    _, _, kind = git_ops.commit_and_push(None, git_ops.auto_commit_message(), do_push=True)
    if kind == "conflict":
        state.set_sync_conflict(True)
    return 0


def run_hook_start(_: argparse.Namespace) -> int:
    """SessionStart-Hook: bei auto_sync pullen; Konflikt-Warnung surfacen."""
    if state.get_auto_sync() and not git_ops.is_dirty():
        ok, kind = git_ops.integrate(git_ops.current_branch())
        if not ok and kind == "conflict":
            state.set_sync_conflict(True)
    if state.get_sync_conflict():
        print("⚠ sync conflict — `/sync` ausführen, um den Konflikt aufzulösen.",
              file=sys.stderr)
        return 1
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("sync", help="Git-Abgleich (on|off|continue|abort|hook-* oder manuell)")
    p.set_defaults(func=run_sync)
    ssub = p.add_subparsers(dest="sync_cmd")
    ssub.add_parser("on").set_defaults(func=_toggle_on)
    ssub.add_parser("off").set_defaults(func=_toggle_off)
    ssub.add_parser("continue").set_defaults(func=run_continue)
    ssub.add_parser("abort").set_defaults(func=run_abort)
    ssub.add_parser("hook-stop").set_defaults(func=run_hook_stop)
    ssub.add_parser("hook-start").set_defaults(func=run_hook_start)
