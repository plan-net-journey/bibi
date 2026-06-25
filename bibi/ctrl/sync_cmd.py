"""``bibi-ctrl sync …`` — Git-Abgleich (PLAN-1 §1.5, §4.9).

- ``sync on|off``      — auto_sync-Flag (stehende Push-Zustimmung) umschalten
- ``sync``            — manueller Abgleich: dirty → warnen + auf /save verweisen
                        (committet NIE selbst); sauber → integrieren → push if ahead;
                        Konflikt → im Tree lassen + sync_conflict (KI-Auflösung)
- ``sync continue``   — nach KI-Auflösung der Marker den Rebase fortsetzen + push
- ``sync abort``      — offenen Rebase abbrechen
- ``sync hook-stop``  — Hintergrund: bei auto_sync transient committen + push
- ``sync hook-start`` — Hintergrund: bei auto_sync pullen; Konflikt-Warnung surface
"""

from __future__ import annotations

import argparse
import sys

from bibi import git_ops, state


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
    """Manueller Abgleich (§4.9)."""
    if git_ops.is_rebase_in_progress():
        print("Rebase offen — Marker auflösen, dann `bibi-ctrl sync continue` "
              "(oder `sync abort`).", file=sys.stderr)
        _print_conflicts()
        return 1
    if git_ops.is_dirty():
        print("Working tree dirty — erst `/save` (sync committet nicht selbst).",
              file=sys.stderr)
        return 1

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

    pok, pmsg = git_ops.push(branch)
    if pok:
        state.set_sync_conflict(False)
        print("sync ok")
        return 0
    print(f"push fehlgeschlagen: {pmsg}", file=sys.stderr)
    return 1


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
