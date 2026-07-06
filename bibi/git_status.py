"""Read-only Arbeitskopie-Status (Tree/Sync/Branch) — geteilte Basis für
``bibi-ctrl statusline``, den Heartbeat (A12) und die Feed-Kopfzeile (PLAN-18).

Ein einziger ``git status --porcelain=v2 --branch``-Aufruf, keine Farben, kein
Rendering — jeder Verwender formatiert das Ergebnis selbst (CLI: ANSI-Farben;
Heartbeat: kompakter String hoch zum Scheduler; Feed: HTML-Kachel).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkingTreeStatus:
    tree: str            # "clean" | "modified"
    sync: str            # "synced" | "ahead" | "behind" | "conflict"
    branch: str | None   # None bei detached HEAD


def working_tree_status(root: Path | None = None) -> WorkingTreeStatus | None:
    """Tree/Sync/Branch des Arbeitsverzeichnisses unter ``root`` (Default: cwd).
    ``None`` wenn kein Git-Repo oder der Aufruf sonst fehlschlägt — Verwender
    entscheiden selbst über ihren eigenen Fallback (z. B. "n/a" vs. leerer String)."""
    proc = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain=v2", "--branch"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return None

    branch: str | None = None
    ahead = behind = 0
    dirty = False
    for line in proc.stdout.splitlines():
        if line.startswith("# branch.head "):
            head = line.split()[-1]
            branch = None if head == "(detached)" else head
        elif line.startswith("# branch.ab "):
            a, b = line.split()[-2:]
            ahead, behind = int(a.lstrip("+")), int(b.lstrip("-"))
        elif line and not line.startswith("#"):
            dirty = True

    tree = "modified" if dirty else "clean"
    if ahead and behind:
        sync = "conflict"
    elif ahead:
        sync = "ahead"
    elif behind:
        sync = "behind"
    else:
        sync = "synced"

    return WorkingTreeStatus(tree=tree, sync=sync, branch=branch)
