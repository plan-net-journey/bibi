"""Git-Helfer für die Lifecycle-Skills (DESIGN §4.9, A8–A11).

Repo-agnostisch: alle Operationen laufen gegen den *aktuellen* Branch des
Team-Repos (kein hardcoded ``trunk``). Reihenfolge je §4.9:
``commit → integrate (rebase/merge) → push``.

Konflikte werden hier *erkannt und sauber abgebrochen* (kind ``"conflict"``);
die KI-gestützte Auflösung (A8/A11) leistet der Skill-Layer (Claude), nicht
diese Engine.
"""

from __future__ import annotations

import datetime
import os
import platform
import re
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


def is_dirty() -> bool:
    """True, wenn der Working Tree unsaubere (uncommittete) Änderungen hat."""
    return bool(_git(["status", "--porcelain"]).stdout.strip())


def is_rebase_in_progress() -> bool:
    """True, wenn ein (konfliktbehafteter) Rebase aussteht."""
    git_dir = Path(_git(["rev-parse", "--git-dir"]).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo.root() / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def conflicted_files() -> list[str]:
    """Pfade mit Merge-Konflikt (unmerged) — Input für die KI-Auflösung."""
    out = _git(["diff", "--name-only", "--diff-filter=U"]).stdout
    return [l for l in out.splitlines() if l.strip()]


# --- Bausteine ---

def current_branch() -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def auto_commit_message() -> str:
    """Message für transiente Hintergrund-Commits (A9): ``auto: ts | user | host``."""
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    raw = _git(["config", "user.name"], check=False).stdout.strip()
    user = re.sub(r"\s+", "-", raw.lower()) or "unknown"
    return f"auto: {ts} | {user} | {platform.node()}"


def diff_stat() -> tuple[str, int]:
    """Working-Tree-Delta als (Signal, geänderte Zeilen) (DESIGN §4.3).

    Das **Signal** ist ``git status --porcelain`` — es erkennt auch *untracked*
    Dateien (neue Case-MDs!), die ``git diff --stat`` übersieht; es ändert sich,
    sobald sich am Tree etwas tut. Das **Zeilenmaß** (für die Debounce-Buckets)
    kommt aus ``git diff --shortstat HEAD`` (Insertions+Deletions getrackter
    Änderungen). Sauberer Tree → ``("", 0)``. Billig genug für den 60-s-Poll.
    """
    # ``-uall`` listet untracked Dateien einzeln (statt das Verzeichnis zu
    # kollabieren) — so ändert sich das Signal pro neuer Datei.
    signal = _git(["status", "--porcelain", "-uall"], check=False).stdout.strip()
    short = _git(["diff", "--shortstat", "HEAD"], check=False).stdout
    lines = 0
    for m in re.finditer(r"(\d+) (insertion|deletion)", short):
        lines += int(m.group(1))
    return signal, lines


def stage_and_commit(scope: Path | None, message: str,
                     identity: tuple[str, str] | None = None) -> bool:
    """Stagen (scope-begrenzt oder ganzes Repo) und committen, falls dirty.

    ``scope=None`` → ``git add -A`` (ganzes Repo, A10 „kein aktiver Case").
    ``scope=<path>`` → nur dieser Pfad (A10 „aktiver Case"); andere
    Working-Tree-Änderungen bleiben ungestaged. Gibt True zurück, wenn ein
    Commit entstanden ist.

    ``identity`` (PLAN-21 Befund 8) — ``(name, email)``, überschreibt die
    ambiente Git-Config für genau diesen Commit. ``None`` (Default, für
    ``/save``/``/close``/``/done``/``/sync`` — egal ob der User selbst tippt
    oder Claude Code es in seinem Auftrag ausführt, gilt beides als „Mensch",
    User-Entscheidung): unverändertes bisheriges Verhalten, committet unter
    der lokalen System-Identität. Gesetzt (der Synchronizer-Hintergrund-Push,
    ``daemon/synchronizer.py``): committet als bibi, weil dort nie ein Mensch
    zusieht.
    """
    if scope is None:
        _git(["add", "-A"])
    else:
        rel = str(scope.resolve().relative_to(repo.root()))
        _git(["add", "-A", "--", rel])
    if not _has_staged():
        return False
    args = []
    if identity is not None:
        name, email = identity
        args += ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    _git([*args, "commit", "-m", message])
    return True


def integrate(branch: str, keep_conflict: bool = False,
             strategy: str = "rebase") -> tuple[bool, str | None]:
    """Origin minimal integrieren: fetch + ff/rebase|merge (kein Push).

    Gibt (ok, kind) zurück. kind ist None bei Erfolg, sonst
    ``"unreachable"``/``"auth"``/``"conflict"``.

    ``keep_conflict=False`` (Default, für save/close/done/hook-stop): bricht
    einen Konflikt sauber ab. ``keep_conflict=True`` (für interaktives
    ``/sync``): lässt den Konflikt im Working Tree stehen, damit die geteilte
    KI-Auflösung (§1.6 A) die Marker auflösen und ``continue_rebase_and_push``
    rufen kann.

    ``strategy``: bei echter Divergenz (weder Fast-Forward noch identisch)
    entscheidet dies, wie integriert wird:
    - ``"rebase"`` (Default): ``git rebase FETCH_HEAD`` — saubere lineare
      Historie, passend für den interaktiven ``/sync``-Pfad, wo ein Mensch
      einen echten Konflikt auch tatsächlich auflöst.
    - ``"merge"``: ``git merge FETCH_HEAD`` — robuster für unbeaufsichtigte,
      bot-generierte Historie (der Synchronizer-Hintergrund-Pull, s.
      ``daemon/synchronizer.py``). Ein Rebase spielt jeden lokalen Commit
      einzeln als Patch neu ein; das kann bei vielen automatisierten Commits
      an einem Zwischenschritt scheitern, obwohl ein einfacher 3-way-Merge der
      beiden Endstände konfliktfrei wäre. Da hier ohnehin niemand zusieht, um
      einen Konflikt aufzulösen, ist die robustere Merge-Strategie vorzuziehen
      — ``keep_conflict`` bleibt dabei ohne Wirkung (Merge wird bei Konflikt
      immer abgebrochen, nie offen gelassen).
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

    # echte Divergenz → rebase (Default) oder merge (bot-robust)
    if strategy == "merge":
        mg = _git(["merge", "--no-edit", "FETCH_HEAD"], check=False, timeout=GIT_NET_TIMEOUT)
        if mg.returncode != 0:
            kind = _classify_failure(mg.stderr.strip())
            _git(["merge", "--abort"], check=False)
            return False, kind
        return True, None

    rb = _git(["rebase", "FETCH_HEAD"], check=False, timeout=GIT_NET_TIMEOUT)
    if rb.returncode != 0:
        kind = _classify_failure(rb.stderr.strip())
        if kind == "conflict" and keep_conflict:
            return False, "conflict"  # im Tree stehen lassen — KI-Auflösung folgt
        _git(["rebase", "--abort"], check=False)
        return False, kind
    return True, None


def abort_rebase() -> None:
    _git(["rebase", "--abort"], check=False)


def continue_rebase_and_push() -> tuple[bool, list[str], str | None]:
    """Nach KI-Auflösung: gelöste Dateien stagen, Rebase fortsetzen, pushen.

    Den Branch erst NACH ``--continue`` ermitteln: während des Rebase ist HEAD
    detached. Gibt (ok, log, kind) zurück; bleiben Konflikte → kind
    ``"conflict"`` (Rebase weiterhin offen).
    """
    log: list[str] = []
    _git(["add", "-A"])
    # core.editor=true akzeptiert die bestehende Commit-Message ohne Editor.
    cont = _git(["-c", "core.editor=true", "rebase", "--continue"], check=False)
    if cont.returncode != 0:
        if conflicted_files():
            log.append("weiterhin Konflikte — auflösen, dann erneut continue")
            return False, log, "conflict"
        log.append(f"rebase --continue FAILED: {cont.stderr.strip()}")
        return False, log, _classify_failure(cont.stderr.strip())
    log.append("Konflikt aufgelöst, rebase fortgesetzt")
    branch = current_branch()  # HEAD ist nach --continue wieder am Branch
    pok, pmsg = push(branch)
    log.append(f"push {'ok' if pok else 'FAIL'}")
    if not pok and pmsg:
        log.append(pmsg)
    return (pok, log, None if pok else _classify_failure(pmsg))


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

def commit_and_push(scope: Path | None, message: str, do_push: bool,
                    identity: tuple[str, str] | None = None) -> tuple[bool, list[str], str | None]:
    """Vollständiger Schreibpfad. Gibt (ok, log, kind) zurück.

    ``do_push`` spiegelt die Sync-Matrix: an → pushen; aus → committen +
    integrieren, aber **nicht** pushen (der Skill fragt dann nach).
    ``identity``: s. ``stage_and_commit()`` (PLAN-21 Befund 8).
    """
    log: list[str] = []
    committed = stage_and_commit(scope, message, identity)
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
