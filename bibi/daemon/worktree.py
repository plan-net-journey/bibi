"""Git-Worktree-Lifecycle für den Worker-Run (DESIGN §1.3/§7.7; PLAN-3 §3.3).

Jeder Job läuft in einem **frischen Worktree** auf Branch ``agent/<slug>`` (von
trunk-HEAD) — kein Job schreibt direkt in ``trunk`` (Worktree-Isolation §1.3).
Der Branch wird wiederverwendet, der Worktree pro Run neu erstellt; Commits
laufen als **Bibi**. Portiert + verschlankt aus bibi3 ``git_worktree.py``.

Defensiv: Fehler tragen die git-stderr als ``GitOpError`` nach oben.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

BOT_NAME = "Bibi"
BOT_EMAIL = "bibi@local"


class GitOpError(RuntimeError):
    def __init__(self, cmd: list[str], stderr: str, code: int):
        super().__init__(f"git {' '.join(cmd)} → exit {code}: {stderr.strip()}")
        self.cmd, self.stderr, self.code = cmd, stderr, code


@dataclass(frozen=True, slots=True)
class GitResult:
    code: int
    stdout: str
    stderr: str


def _git(args: list[str], *, cwd: Path, check: bool = True) -> GitResult:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise GitOpError(args, proc.stderr, proc.returncode)
    return GitResult(code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def branch_name(slug: str) -> str:
    return f"agent/{slug}"


def head_commit(worktree: Path) -> str:
    """Voll-SHA von HEAD im Worktree, oder "" bei Fehler (best-effort)."""
    try:
        r = _git(["rev-parse", "HEAD"], cwd=worktree, check=False)
    except OSError:
        return ""
    return r.stdout.strip() if r.code == 0 else ""


def prepare(*, repo_root: Path, work_dir: Path, slug: str, trunk: str = "trunk") -> Path:
    """Frischen Worktree unter ``work_dir/<slug>/`` auf ``agent/<slug>`` anlegen.

    Ein evtl. veralteter Worktree (z. B. nach Crash) wird vorher entfernt;
    ``git worktree add -B`` setzt den Branch auf trunk-HEAD und verlinkt neu."""
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / slug
    if path.exists():
        _git(["worktree", "remove", "--force", str(path)], cwd=repo_root, check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    _git(["worktree", "add", "-B", branch_name(slug), str(path), trunk], cwd=repo_root)
    return path


def remove(*, repo_root: Path, worktree: Path) -> None:
    """Worktree entfernen (force, idempotent)."""
    if worktree.exists():
        _git(["worktree", "remove", "--force", str(worktree)], cwd=repo_root, check=False)
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)


def commit(*, worktree: Path, message: str, slug: str = "") -> str:
    """Alle Änderungen stagen + als Bibi committen. Voll-SHA, oder "" wenn clean.

    Output liegt in ``data/`` (gitignored, §4.4) — ein reiner ``echo``-Job ändert
    nichts und liefert daher "" (kein neuer Commit, der Branch existiert dennoch)."""
    _git(["add", "-A"], cwd=worktree)
    if not _git(["status", "--porcelain"], cwd=worktree).stdout.strip():
        return ""
    _git(
        ["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
         "commit", "-m", message, "-m", f"slug: {slug}"],
        cwd=worktree,
    )
    return head_commit(worktree)
