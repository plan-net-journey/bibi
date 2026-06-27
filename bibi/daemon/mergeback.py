"""Merge-back ``agent/<slug>`` → trunk (PLAN-6; Worker-Analyse §6).

Der Phase-3-Worktree-Lifecycle committet Job-Ergebnisse auf ``agent/<slug>``
(``worktree.commit``), führte sie aber nie nach ``trunk`` zusammen — darum erreichte
nichts den Vault. Diese Funktion schließt die Lücke **scheduler-seitig** (dort lebt
das trunk-Repo) und **unter einem gemeinsamen Lock** mit dem Synchronizer, damit der
Merge nicht gegen einen Pull/Push auf trunk rennt.

Konflikt-sicher: bei einem Merge-Konflikt wird sauber abgebrochen (``merge --abort``),
trunk bleibt unverändert und der Commit liegt weiter auf ``agent/<slug>`` — nichts
geht verloren, nichts hängt.
"""

from __future__ import annotations

import contextlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bibi.daemon.worktree import branch_name

# status: "merged" | "up_to_date" | "conflict" | "error"


@dataclass(frozen=True, slots=True)
class MergeResult:
    status: str
    trunk_sha: str = ""
    detail: str = ""


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def merge_back(*, repo_root: Path, slug: str, trunk: str = "trunk", lock=None) -> MergeResult:
    """``agent/<slug>`` nach ``trunk`` mergen (im trunk-Working-Copy ``repo_root``).

    ``lock`` (optional, ein ``threading.Lock``-artiger Kontext) wird um die
    Git-Operation gehalten — derselbe ``sync_lock`` wie im Synchronizer.
    """
    with (lock if lock is not None else contextlib.nullcontext()):
        return _merge_locked(repo_root=repo_root, slug=slug, trunk=trunk)


def _merge_locked(*, repo_root: Path, slug: str, trunk: str) -> MergeResult:
    branch = branch_name(slug)
    if _git(["rev-parse", "--verify", "--quiet", branch], cwd=repo_root).returncode != 0:
        return MergeResult("error", detail=f"branch {branch} fehlt")

    proc = _git(["merge", "--no-ff", "--no-edit", branch], cwd=repo_root)
    head = _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    if proc.returncode == 0:
        out = (proc.stdout + proc.stderr).lower()
        if "already up to date" in out:
            return MergeResult("up_to_date", trunk_sha=head)
        return MergeResult("merged", trunk_sha=head)

    # Fehlschlag: Merge-Konflikt sauber abbrechen (idempotent), sonst echter Fehler.
    if (repo_root / ".git" / "MERGE_HEAD").exists():
        _git(["merge", "--abort"], cwd=repo_root)
        return MergeResult("conflict", trunk_sha=head,
                           detail=(proc.stdout + proc.stderr).strip())
    return MergeResult("error", trunk_sha=head, detail=(proc.stdout + proc.stderr).strip())


# ── Recovery (PLAN-6 Slice C): liegengebliebene agent/*-Branches ─────────────

def unmerged_agent_branches(*, repo_root: Path, trunk: str = "trunk") -> list[str]:
    """``agent/*``-Branches mit Commits, die **nicht** in trunk stecken (sortiert).

    Nach Slice B sollte das leer sein; ein Eintrag heißt: ein erfolgreicher Lauf
    wurde nie zusammengeführt (z. B. Merge-Fehler/Konflikt, alter Daemon)."""
    # for-each-ref liefert reine Branch-Namen (``git branch`` dekoriert Worktree-
    # Branches mit ``+`` und den aktuellen mit ``*``).
    proc = _git(["for-each-ref", f"--no-merged={trunk}",
                 "--format=%(refname:short)", "refs/heads/agent/"], cwd=repo_root)
    return sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())


def remerge_all(*, repo_root: Path, trunk: str = "trunk", lock=None) -> dict[str, str]:
    """Alle unmergten ``agent/*``-Branches nach trunk mergen. ``{branch: status}``."""
    out: dict[str, str] = {}
    for branch in unmerged_agent_branches(repo_root=repo_root, trunk=trunk):
        slug = branch.removeprefix("agent/")
        out[branch] = merge_back(repo_root=repo_root, slug=slug, trunk=trunk, lock=lock).status
    return out
