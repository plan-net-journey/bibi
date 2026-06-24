"""Git-Helfer für die Lifecycle-Skills (DESIGN §4.9, A8–A11).

Repo-agnostisch: alle Operationen laufen gegen den *aktuellen* Branch des
Team-Repos (kein hardcoded ``trunk``). Reihenfolge je §4.9:
``commit → integrate (rebase/merge) → push``.

Konflikte werden hier *erkannt und sauber abgebrochen* (kind ``"conflict"``);
die KI-gestützte Auflösung (A8/A11) leistet der Skill-Layer (Claude), nicht
diese Engine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from bibi import repo

GIT_NET_TIMEOUT: float = float(os.environ.get("BIBI_GIT_NET_TIMEOUT", "12"))


def _git(args: list[str], check: bool = True,
         timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args], cwd=repo.root(),
            capture_output=True, text=True, check=check, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=1, stdout="",
            stderr=f"Timeout was reached after {timeout:g}s — origin unreachable",
        )


# --- Fehlerklassifikation (nur "conflict" erfordert Nutzeraktion) ---

_UNREACHABLE_MARKERS = (
    "Couldn't connect to server", "Could not resolve host", "Failed to connect",
    "Timeout was reached", "Connection refused", "unable to access",
    "Network is unreachable", "No route to host",
)
_AUTH_MARKERS = (
    "could not read username", "could not read password", "authentication failed",
    "invalid username or password", "terminal prompts disabled",
    "no such device or address",
)


def _is_unreachable(stderr: str) -> bool:
    return any(m in stderr for m in _UNREACHABLE_MARKERS)


def _is_auth_failure(stderr: str) -> bool:
    s = stderr.lower()
    return any(m in s for m in _AUTH_MARKERS)


def _classify_failure(stderr: str) -> str:
    if _is_unreachable(stderr):
        return "unreachable"
    if _is_auth_failure(stderr):
        return "auth"
    return "conflict"


def _has_staged() -> bool:
    return bool(_git(["diff", "--cached", "--name-only"]).stdout.strip())


# --- Bausteine ---

def current_branch() -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def stage_and_commit(scope: Path | None, message: str) -> bool:
    """Stagen (scope-begrenzt oder ganzes Repo) und committen, falls dirty.

    ``scope=None`` → ``git add -A`` (ganzes Repo, A10 „kein aktiver Case").
    ``scope=<path>`` → nur dieser Pfad (A10 „aktiver Case"); andere
    Working-Tree-Änderungen bleiben ungestaged. Gibt True zurück, wenn ein
    Commit entstanden ist.
    """
    if scope is None:
        _git(["add", "-A"])
    else:
        rel = str(scope.resolve().relative_to(repo.root()))
        _git(["add", "-A", "--", rel])
    if not _has_staged():
        return False
    _git(["commit", "-m", message])
    return True


def integrate(branch: str) -> tuple[bool, str | None]:
    """Origin minimal integrieren: fetch + ff/rebase (kein Push).

    Gibt (ok, kind) zurück. kind ist None bei Erfolg, sonst
    ``"unreachable"``/``"auth"``/``"conflict"``. Lässt nie einen
    hängengebliebenen Rebase zurück.
    """
    fetch = _git(["fetch", "origin", branch], check=False, timeout=GIT_NET_TIMEOUT)
    if fetch.returncode != 0:
        return False, _classify_failure(fetch.stderr.strip())

    local = _git(["rev-parse", "HEAD"]).stdout.strip()
    remote = _git(["rev-parse", "FETCH_HEAD"]).stdout.strip()
    if local == remote:
        return True, None
    if _git(["merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD"], check=False).returncode == 0:
        return True, None  # lokal voraus — Push erledigt den Rest
    if _git(["merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], check=False).returncode == 0:
        ff = _git(["merge", "--ff-only", "FETCH_HEAD"], check=False)
        return (True, None) if ff.returncode == 0 else (False, "conflict")
    # echte Divergenz → rebase
    rb = _git(["rebase", "FETCH_HEAD"], check=False, timeout=GIT_NET_TIMEOUT)
    if rb.returncode != 0:
        _git(["rebase", "--abort"], check=False)
        return False, _classify_failure(rb.stderr.strip())
    return True, None


def push(branch: str) -> tuple[bool, str]:
    """Branch pushen. Bei Reject einmal rebase + retry."""
    args = ["push", "-u", "origin", branch]
    proc = _git(args, check=False, timeout=GIT_NET_TIMEOUT)
    if proc.returncode == 0:
        return True, (proc.stdout + proc.stderr).strip()
    if "rejected" in proc.stderr or "non-fast-forward" in proc.stderr:
        rb = _git(["pull", "--rebase", "origin", branch], check=False, timeout=GIT_NET_TIMEOUT)
        if rb.returncode != 0:
            _git(["rebase", "--abort"], check=False)
            return False, f"rebase failed (aborted):\n{rb.stderr.strip()}"
        retry = _git(args, check=False, timeout=GIT_NET_TIMEOUT)
        return retry.returncode == 0, (retry.stdout + retry.stderr).strip()
    return False, (proc.stdout + proc.stderr).strip()


# --- Orchestrierung (§4.9: commit → integrate → push/ask) ---

def commit_and_push(scope: Path | None, message: str,
                    do_push: bool) -> tuple[bool, list[str], str | None]:
    """Vollständiger Schreibpfad. Gibt (ok, log, kind) zurück.

    ``do_push`` spiegelt die Sync-Matrix: an → pushen; aus → committen +
    integrieren, aber **nicht** pushen (der Skill fragt dann nach).
    """
    log: list[str] = []
    committed = stage_and_commit(scope, message)
    log.append(f"committed: {message}" if committed else "nothing to commit")

    branch = current_branch()
    ok, kind = integrate(branch)
    if not ok:
        log.append(f"integrate FAILED ({kind})")
        return False, log, kind
    log.append("integrated")

    if not do_push:
        log.append("nicht gepusht (auto_sync off) — push mit: bibi-ctrl save --push")
        return True, log, None

    pok, pmsg = push(branch)
    log.append(f"push {'ok' if pok else 'FAIL'}")
    if not pok and pmsg:
        log.append(pmsg)
    return (pok, log, None if pok else _classify_failure(pmsg))


def remove_path_and_push(path: Path, message: str,
                         do_push: bool) -> tuple[bool, list[str], str | None]:
    """Pfad aus Index + Working-Tree entfernen, committen, integrieren, push (gated).

    Funktioniert für getrackte wie ungetrackte Ordner: ``--ignore-unmatch``
    bleibt still, wenn nichts im Index ist; ``rmtree`` räumt Reste weg.
    """
    log: list[str] = []
    rel = str(path.resolve().relative_to(repo.root()))
    _git(["rm", "-rf", "--ignore-unmatch", "--", rel])
    if path.exists():
        shutil.rmtree(path)
    if not _has_staged():
        log.append("nothing to commit (folder was untracked)")
        return True, log, None
    _git(["commit", "-m", message])
    log.append(f"committed: {message}")

    branch = current_branch()
    ok, kind = integrate(branch)
    if not ok:
        log.append(f"integrate FAILED ({kind})")
        return False, log, kind

    if not do_push:
        log.append("nicht gepusht (auto_sync off)")
        return True, log, None
    pok, pmsg = push(branch)
    log.append(f"push {'ok' if pok else 'FAIL'}")
    if not pok and pmsg:
        log.append(pmsg)
    return (pok, log, None if pok else _classify_failure(pmsg))
