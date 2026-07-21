"""Git-Worktree-Lifecycle für den Worker-Run (DESIGN §1.3/§7.7; PLAN-3 §3.3).

Jeder Job läuft in einem **frischen Worktree** auf Branch ``agent/<slug>`` (von
trunk-HEAD) — kein Job schreibt direkt in ``trunk`` (Worktree-Isolation §1.3).
Der Branch wird wiederverwendet, der Worktree pro Run neu erstellt; Commits
laufen als **bibi/<slug>** (PLAN-21 Befund 8, User-Entscheidung: 2 Git-
Identitäten — Mensch für alles interaktive, ``bibi/<slug>`` für alles
unbeaufsichtigt Automatisierte; dynamischer Name statt der vorherigen festen
"Bibi", damit Log/Blame den auslösenden Job unterscheidbar machen, ohne
einen Gitea-Account pro Job anzulegen — Gitea gruppiert ohnehin über die
konstante Email zu einer Bot-Identität). Portiert + verschlankt aus bibi3
``git_worktree.py``.

Defensiv: Fehler tragen die git-stderr als ``GitOpError`` nach oben.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("bibi.worktree")

BOT_EMAIL = "bibi@local"

#: Bibi4-Iteration, User-Fund "Runner 5 hängt" (Beobachtungen.md): mehrere
#: gleichzeitige Job-Dispatches lösen mehrere parallele ``git worktree add``+
#: ``git-lfs filter-process``-Subprozessketten aus, die um CPU/IO konkurrieren
#: — gemessen bis zu 107,8s für einen einzelnen prepare()-Aufruf, der
#: `tick_once()`s komplette Pinned-Worker-Schleife für die Dauer blockiert
#: (kein Deadlock, aber unbegrenzte, ungeschützte Wartezeit). Serialisiert
#: statt begrenzt (Semaphore(1) statt z. B. 2-3) — User-Entscheidung, s.
#: Beobachtungen.md "(b)!": lieber Dispatches nacheinander abarbeiten als
#: sich weiterhin gegenseitig ausbremsen.
_PREPARE_SEMAPHORE = threading.Semaphore(1)

#: Analog zu exec_backend._TAG_UNSAFE_RE: Slugs kommen oft roh aus einem
#: Dateistamm (schedule.parser.derive_slug — kein slugify dort, da der Slug
#: auch als CLI-Adressierung dient) und können Zeichen enthalten, die in
#: Git-Refs ungültig sind (Leerzeichen, ``:`` u. a.) — ``git worktree add -B``
#: schlägt sonst mit "not a valid branch name" fehl, bevor der Job überhaupt
#: läuft (User-Fund 2026-07-14: ``bibi-ctrl run "Runner 1"``).
_BRANCH_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def bot_identity(slug: str = "") -> tuple[str, str]:
    """``(name, email)`` der bibi-Bot-Identität für einen Job-Commit — der Name
    trägt den Slug (PLAN-21 Befund 8), die Email bleibt konstant (Gitea
    gruppiert darüber zu einer Bot-Identität)."""
    return (f"bibi/{slug}" if slug else "bibi"), BOT_EMAIL


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
    """``agent/<slug>``, Slug git-ref-sicher normalisiert. Unsichere Zeichen
    zu ``-`` kollabiert; ``..`` zusätzlich entschärft (in Git-Refs verboten,
    von der Zeichen-Whitelist allein nicht abgedeckt)."""
    safe = _BRANCH_UNSAFE_RE.sub("-", slug).strip("-.")
    safe = re.sub(r"\.{2,}", "-", safe) or "job"
    return f"agent/{safe}"


def head_commit(worktree: Path) -> str:
    """Voll-SHA von HEAD im Worktree, oder "" bei Fehler (best-effort)."""
    try:
        r = _git(["rev-parse", "HEAD"], cwd=worktree, check=False)
    except OSError:
        return ""
    return r.stdout.strip() if r.code == 0 else ""


def is_ahead(*, repo_root: Path, branch: str, trunk: str = "trunk") -> bool:
    """Hat ``branch`` Commits, die **nicht** in ``trunk`` stecken? (False bei Fehlen)."""
    r = _git(["rev-list", "--count", f"{trunk}..{branch}"], cwd=repo_root, check=False)
    return r.code == 0 and r.stdout.strip() not in ("", "0")


def prepare(*, repo_root: Path, work_dir: Path, slug: str, trunk: str = "trunk") -> Path:
    """Frischen Worktree unter ``work_dir/<slug>/`` auf ``agent/<slug>`` anlegen.

    Ein evtl. veralteter Worktree (z. B. nach Crash) wird vorher entfernt;
    ``git worktree add -B`` setzt den Branch auf trunk-HEAD und verlinkt neu.

    **F-b (PLAN-7):** Hat ``agent/<slug>`` noch **ungemergte** Commits voraus von
    trunk (Merge-back stand aus), würde ``-B`` sie verwerfen → Datenverlust. Darum:
    abbrechen statt verwerfen. Der periodische Merge-Sweep (F-a) holt den Branch
    nach; der Lauf scheitert sauber als ``failed`` und wird neu versucht.

    Der eigentliche Git-/LFS-Anteil läuft serialisiert über ``_PREPARE_SEMAPHORE``
    (s. dortiger Kommentar) — der günstige ``is_ahead()``-Vorab-Check bleibt
    außerhalb, der blockiert nichts, den soll ein wartender Aufrufer nicht auch
    noch verzögern."""
    branch = branch_name(slug)
    if is_ahead(repo_root=repo_root, branch=branch, trunk=trunk):
        raise GitOpError(
            ["worktree", "prepare"],
            f"{branch} hat ungemergte Commits voraus von {trunk} — "
            "-B-Reset verweigert (F-b), Merge-Sweep holt nach", 1)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / slug
    with _PREPARE_SEMAPHORE:
        if path.exists():
            _git(["worktree", "remove", "--force", str(path)], cwd=repo_root, check=False)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        _git(["worktree", "add", "-B", branch_name(slug), str(path), trunk], cwd=repo_root)
    return path


def remove(*, repo_root: Path, worktree: Path) -> None:
    """Worktree entfernen (force, idempotent).

    Defense-in-Depth-Guard (User-Fund 2026-07-14, ``bibi-ctrl test``): weigert
    sich, wenn ``worktree`` auf denselben Pfad wie ``repo_root`` auflöst — für
    ``in_place``-Läufe (kein separater Worktree, ``wt_path is repo_root``) darf
    das nie passieren, sonst würde ``shutil.rmtree(..., ignore_errors=True)``
    unten das komplette Live-Repo löschen, ``.git`` eingeschlossen. Die
    eigentliche Absicherung ist ``run_pinned()``s erzwungenes
    ``ephemeral=False`` bei ``in_place=True`` (kein Aufrufer sollte ``remove()``
    für einen in-place-Lauf überhaupt erreichen) — dieser Guard ist die zweite,
    unabhängige Sicherung, falls diese Verdrahtung je auseinanderdriftet."""
    if worktree.resolve() == repo_root.resolve():
        log.error("worktree.remove() verweigert: worktree == repo_root (%s) — kein Löschen", repo_root)
        return
    if worktree.exists():
        _git(["worktree", "remove", "--force", str(worktree)], cwd=repo_root, check=False)
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)


def commit(*, worktree: Path, message: str, slug: str = "") -> str:
    """Alle Änderungen stagen + als ``bibi/<slug>`` committen. Voll-SHA, oder ""
    wenn clean.

    Output liegt in ``data/`` (gitignored, §4.4) — ein reiner ``echo``-Job ändert
    nichts und liefert daher "" (kein neuer Commit, der Branch existiert dennoch)."""
    _git(["add", "-A"], cwd=worktree)
    if not _git(["status", "--porcelain"], cwd=worktree).stdout.strip():
        return ""
    name, email = bot_identity(slug)
    _git(
        ["-c", f"user.name={name}", "-c", f"user.email={email}",
         "commit", "-m", message, "-m", f"slug: {slug}"],
        cwd=worktree,
    )
    return head_commit(worktree)
