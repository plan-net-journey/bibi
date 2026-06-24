"""``bibi-ctrl close|done|delete`` — Faktorisierungen auf save (PLAN-1 §1.3, §4.9).

- ``close`` = save (Case-Scope) + ``status: paused`` + ``path → None``
- ``done``  = close, final (``status: closed``)
- ``delete`` = Ordner entfernen + commit/push + ``path → None``

Alle drei brauchen einen aktiven Case (cwd im Case-Ordner); ohne → Hinweis auf
``open``. Push folgt der Sync-Matrix (``--push`` oder ``auto_sync on``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bibi import case_store, git_ops, repo, state, sync


def _active_case() -> Path | None:
    path = state.get_path()
    if not path:
        return None
    folder = repo.vault() / path
    return folder if folder.exists() else None


def _no_active() -> int:
    print("kein aktiver Case. Zuerst `bibi-ctrl open <topic>`.", file=sys.stderr)
    return 2


def _do_push(args: argparse.Namespace) -> bool:
    return args.push or sync.auto_push_enabled()


def _finish(ok: bool, log: list[str], kind: str | None, root: Path) -> int:
    """Log ausgeben, Konflikt markieren, Display-Mirror leeren, un-parken."""
    for line in log:
        print(line)
    if kind == "conflict":
        state.set_sync_conflict(True)
        print("⚠ Merge-Konflikt — KI-Auflösung nötig (/sync).", file=sys.stderr)
    state.set_path(None)
    print(f"cd: {root}")  # un-park: Skill cd't zurück zur Repo-Wurzel
    return 0 if ok else 1


def run_close(args: argparse.Namespace) -> int:
    folder = _active_case()
    if folder is None:
        return _no_active()
    case_store.set_status(folder, "paused")
    ok, log, kind = git_ops.commit_and_push(folder, f"close: {folder.name}", _do_push(args))
    return _finish(ok, log, kind, repo.root())


def run_done(args: argparse.Namespace) -> int:
    folder = _active_case()
    if folder is None:
        return _no_active()
    case_store.set_status(folder, "closed")
    ok, log, kind = git_ops.commit_and_push(folder, f"done: {folder.name}", _do_push(args))
    return _finish(ok, log, kind, repo.root())


def run_delete(args: argparse.Namespace) -> int:
    folder = _active_case()
    if folder is None:
        return _no_active()
    # cwd liegt IM zu löschenden Ordner — Root vorher auflösen und dorthin
    # wechseln, sonst scheitert jeder Path.cwd()-Aufruf nach dem rmtree.
    root = repo.root()
    os.chdir(root)
    ok, log, kind = git_ops.remove_path_and_push(folder, f"delete: {folder.name}", _do_push(args))
    return _finish(ok, log, kind, root)


def register(sub: argparse._SubParsersAction) -> None:
    pc = sub.add_parser("close", help="aktiven Case pausieren (status: paused)")
    pc.add_argument("--push", action="store_true", help="pushen unabhängig vom auto_sync-Flag")
    pc.set_defaults(func=run_close)

    pd = sub.add_parser("done", help="aktiven Case final abschließen (status: closed)")
    pd.add_argument("--push", action="store_true", help="pushen unabhängig vom auto_sync-Flag")
    pd.set_defaults(func=run_done)

    px = sub.add_parser("delete", help="aktiven Case dauerhaft entfernen")
    px.add_argument("--push", action="store_true", help="pushen unabhängig vom auto_sync-Flag")
    px.set_defaults(func=run_delete)
