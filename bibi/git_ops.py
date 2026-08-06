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
import time
from pathlib import Path

from bibi import repo

GIT_NET_TIMEOUT: float = float(os.environ.get("BIBI_GIT_NET_TIMEOUT", "12"))


def _git(args: list[str], check: bool = True,
         timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    # -c core.quotepath=false (Review-Runde 5, Fund 2, live verifiziert): ohne
    # das quotet Git jeden Pfad mit Nicht-ASCII-Zeichen in JEDER Textausgabe
    # (z. B. "Kündigung.md" → "K\303\274ndigung.md") — dieses Vault hat
    # bereits solche Pfade. Jeder Code hier, der Pfade aus Git-Ausgabe parst
    # (dirty_paths(), _pull_live_overlap()), würde den Überlapp sonst
    # schlicht nicht erkennen.
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=false", *args], cwd=repo.root(),
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
# m.rau/bibi#160: git lehnt ab, weil uncommittete Arbeit im Weg steht — der
# Vorgang ist gar nicht erst angelaufen, es gibt nichts aufzulösen. Vorher fiel
# das durch auf "conflict" und setzte damit ``sync_conflict``: eine Warnung bei
# jedem Sitzungsstart, die niemand beenden konnte, weil es keinen Konflikt gab.
# Die Marker sind am 2026-08-06 gegen echtes git gemessen — zwei Varianten von
# ``rebase`` (unstaged / Index) und die von ``merge``, wenn der Pull eine dirty
# Datei überschreiben würde.
_DIRTY_TREE_MARKERS = (
    "cannot rebase: you have unstaged changes",
    "cannot rebase: your index contains uncommitted changes",
    "cannot pull with rebase: you have unstaged changes",
    "your local changes to the following files would be overwritten",
)


def _is_unreachable(stderr: str) -> bool:
    return any(m in stderr for m in _UNREACHABLE_MARKERS)


def _is_auth_failure(stderr: str) -> bool:
    s = stderr.lower()
    return any(m in s for m in _AUTH_MARKERS)


def _is_dirty_tree(stderr: str) -> bool:
    s = stderr.lower()
    return any(m in s for m in _DIRTY_TREE_MARKERS)


def _classify_failure(stderr: str) -> str:
    if _is_unreachable(stderr):
        return "unreachable"
    if _is_auth_failure(stderr):
        return "auth"
    if _is_dirty_tree(stderr):
        return "dirty"
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


def is_merge_in_progress() -> bool:
    """True, wenn ein (konfliktbehafteter) Merge aussteht — PLAN-30 Ebene 3:
    ein Job-Branch-Merge-back-Konflikt, den ``/sync`` interaktiv offen gelassen
    hat (``mergeback.merge_back(..., keep_conflict=True)``), analog
    ``is_rebase_in_progress()`` für Pull-Konflikte."""
    git_dir = Path(_git(["rev-parse", "--git-dir"]).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo.root() / git_dir
    return (git_dir / "MERGE_HEAD").exists()


def is_conflict_resolution_pending() -> bool:
    """True, wenn IRGENDEIN Merge oder Rebase gerade offen ist — unabhängig
    davon, wer/was ihn gestartet hat.

    Review-Runde 4 (PLAN-30 Ebene 3), Fund 1, live gegen echtes Git bewiesen:
    ohne diesen Guard konnte ein automatisierter Schreibvorgang, der während
    eines von ``/sync keep_conflict=True`` offen gelassenen Job-Branch-Merge-
    Konflikts lief, den Konflikt auf zwei Arten lautlos zerstören —
    (a) ``git add -A`` (unscoped) staged die noch unmerged Datei mit ihrem
    aktuellen, Konfliktmarker-behafteten Inhalt als "aufgelöst", ein
    nachfolgender ``git commit`` nimmt das klaglos an (live reproduziert:
    Trunk bekommt einen Commit mit rohen ``<<<<<<<``-Markern als Datei-
    Inhalt, keine Fehlermeldung); (b) ein Merge-Versuch für einen VÖLLIG
    ANDEREN Branch scheitert sofort mit "Merging is not possible because you
    have unmerged files" — OHNE diesen Guard interpretierte
    ``mergeback._merge_locked()`` das fälschlich als Konflikt DIESES anderen
    Branches und rief ``merge --abort`` — was live reproduziert die laufende
    Konfliktauflösung des Menschen komplett zurückgesetzt hat (Datei-Inhalt
    zurück auf den Vor-Konflikt-Stand, Auflösungsarbeit verloren).

    Jeder automatisierte Schreibvorgang (Commit, Push, Merge-back, Pull) MUSS
    dies vor jedem eigenen Schreibversuch prüfen und bei ``True`` überspringen
    — nicht durch ``force`` umgehbar, das wäre in beiden obigen Szenarien
    genau der Fehler."""
    return is_rebase_in_progress() or is_merge_in_progress()


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


def git_user_email(cwd: Path | None = None) -> str | None:
    """Die konfigurierte ``git config user.email`` — Gegenstück zu
    :func:`git_user_name` (m.rau/bibi#18).

    Getrennt abgefragt und nicht aus dem Namen abgeleitet: git löst beide
    unabhängig über lokal > global > System auf, und genau die Hälfte davon zu
    haben ist der häufigere Fall. ``None`` statt leerem String, wenn nichts
    konfiguriert ist.
    """
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "config", "user.email"],
        cwd=cwd or repo.root(), capture_output=True, text=True, check=False,
    )
    return proc.stdout.strip() or None


def git_user_name(cwd: Path | None = None) -> str | None:
    """Der lokal konfigurierte ``git config user.name`` (ambiente Identität,
    fällt heute auf `~/.gitconfig` zurück, solange kein Repository Identität-
    Setup lokal gesetzt hat) — für den Connected-Clients-Screen (Bibi4-
    Iteration, User-Fund: "wir brauchen unbedingt den hinterlegten gitea/git
    Nutzernamen ... die einzige Möglichkeit, den Menschen zu identifizieren").
    Unnormalisiert, anders als ``auto_commit_message()``s intern
    sluggifizierte Variante — hier soll der echte Anzeigename sichtbar sein.
    ``None`` statt leerem String, wenn nichts konfiguriert ist.

    ``cwd`` (anders als ``_git()``, das immer ``repo.root()`` fest verdrahtet)
    erlaubt einen expliziten Checkout — ``Heartbeat`` hat mit ``repo_root``
    schon einen eigenen Override (Tests/mehrere Instanzen desselben Prozesses),
    den auch dieser Aufruf respektieren muss, statt am tatsächlichen
    ``repo.root()`` vorbeizulesen."""
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "config", "user.name"],
        cwd=cwd or repo.root(), capture_output=True, text=True, check=False,
    )
    raw = proc.stdout.strip()
    return raw or None


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


def dirty_paths() -> list[str]:
    """Alle dirty (uncommitted) Pfade, repo-root-relativ, POSIX-Separator
    (PLAN-25 Befund 8 — Input für ``cluster_dirty_paths()``). ``--no-renames``
    (wie ``git_status.local_files_status()``) macht jede Rename-Zeile zu zwei
    einfachen Einträgen (alter Pfad "gelöscht", neuer "neu") statt eines
    schwerer zu parsenden Rename-Eintrags."""
    proc = _git(["status", "--porcelain=v2", "--untracked-files=all", "--no-renames"],
               check=False)
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("? "):
            paths.append(line[2:])
        elif line.startswith("1 "):
            # User-Fund 2026-07-14: bloßes line.split(" ")[-1] nahm nur das
            # letzte Leerzeichen-Fragment — bei Pfaden mit Space (z. B.
            # "Runner 3.md") blieb nur "3.md" übrig, git add -A -- 3.md
            # scheiterte dann mit exit 128 (Pfad existiert nicht). Porcelain
            # v2 "1"-Zeilen haben genau 8 feste Felder vor dem Pfad (XY, sub,
            # mH, mI, mW, hH, hI — plus das führende "1"): 1 <XY> <sub> <mH>
            # <mI> <mW> <hH> <hI> <path>. maxsplit=8 lässt den Pfad selbst
            # (inkl. etwaiger Leerzeichen) als letztes, unzerlegtes Element
            # stehen.
            paths.append(line.split(" ", 8)[-1])
        elif line.startswith("u "):
            # Unmerged-Zeilen haben 10 feste Felder vor dem Pfad (u, XY, sub,
            # m1, m2, m3, mW, h1, h2, h3), s. Kommentar oben — analog
            # maxsplit=10.
            paths.append(line.split(" ", 10)[-1])
    return paths


def recently_touched_paths(root: Path, paths: list[str], *, within_s: int,
                           now: float | None = None) -> set[str]:
    """PLAN-30 Ebene 4 (G1): welche der gegebenen (repo-root-relativen) Pfade
    wurden innerhalb der letzten ``within_s`` Sekunden zuletzt geschrieben?

    Reine ``stat()``-Funktion, kein Git-Aufruf (wie ``cluster_dirty_paths()``
    daneben) — ein bewusster Kompromiss: die erste echte I/O in einer sonst
    reinen Pfad-Funktion, aber vom Geist her näher an "reiner Funktion" als an
    einem Git-Kommando. ``now`` injizierbar für deterministische Tests
    (``tmp_path`` + ``os.utime()``, kein echtes Timing nötig). Ein fehlender
    Pfad zählt nicht als "kürzlich angefasst" (kein Überlapp-Risiko, wenn die
    Datei nicht mehr existiert)."""
    now = time.time() if now is None else now
    touched: set[str] = set()
    for p in paths:
        try:
            mtime = (root / p).stat().st_mtime
        except OSError:
            continue
        if now - mtime < within_s:
            touched.add(p)
    return touched


def cluster_dirty_paths(
    paths: list[str], *, case_dir_name: str, active_case_rel: str | None,
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Dirty Pfade nach Case-Zugehörigkeit gruppieren (PLAN-25 Befund 8,
    Punkt 1) — reine Pfad-Logik, kein Git-Aufruf.

    Gibt ``(other_cases, caseless, active_case)`` zurück:

    - ``other_cases``: ``{"<case_dir_name>/<slug>": [pfade]}`` — ein Cluster
      je Case-Ordner, der **nicht** der aktive ist (auch ohne aktiven Case,
      s. u.).
    - ``caseless``: Pfade außerhalb jedes Case-Ordners (``vault/memo/``,
      ``vault/attach/``, Repo-Root-Dateien, ``.claude/`` …) — ein Sammel-
      Cluster.
    - ``active_case``: Pfade, die zum aktiven Case gehören (``active_case_rel``,
      aus ``state.get_path()``) — von ``/sync`` bewusst **nicht** angefasst,
      nur angezeigt (auf ``/save`` verweisen).

    ``active_case_rel is None`` (kein Case geparkt) ⇒ **jeder** Case-Ordner
    zählt als "other" — "egal ob mit oder ohne aktives Projekt" (User-Vorgabe)."""
    vault_prefix = "vault/"
    case_prefix = f"{case_dir_name}/"
    other: dict[str, list[str]] = {}
    caseless: list[str] = []
    active: list[str] = []
    for p in paths:
        if not p.startswith(vault_prefix):
            caseless.append(p)
            continue
        rel_to_vault = p[len(vault_prefix):]
        if not rel_to_vault.startswith(case_prefix):
            caseless.append(p)
            continue
        rest = rel_to_vault[len(case_prefix):]
        folder = rest.split("/", 1)[0]
        case_rel = f"{case_dir_name}/{folder}"
        if active_case_rel is not None and case_rel == active_case_rel:
            active.append(p)
        else:
            other.setdefault(case_rel, []).append(p)
    return other, caseless, active


def stage_and_commit_paths(paths: list[str], message: str,
                          identity: tuple[str, str] | None = None) -> bool:
    """Wie ``stage_and_commit()``, aber für eine explizite Liste von Pfaden
    ohne gemeinsamen Verzeichnis-Scope (PLAN-25 Befund 8 — Sammel-Cluster für
    Case-lose Änderungen). Gibt True zurück, wenn ein Commit entstanden ist."""
    if not paths:
        return False
    _git(["add", "-A", "--", *paths])
    return _commit_if_staged(message, identity)


def snapshot_worktree() -> dict[str, object]:
    """PLAN-38 Stufe 2: Schnappschuss des Arbeitsstands **vor** einem In-place-Lauf.

    Zweck: hinterher genau die Pfade bestimmen können, die der Job selbst
    angefasst hat — und nur die committen. Ein reiner „war vorher schon dirty"-
    Vergleich reicht dafür nicht: die interessanteste Datei ist typischerweise
    genau die, die der Mensch gerade editiert hat UND der Job zusätzlich ändert
    (Anlassfall: eine Schedule-MD mit frisch geändertem Frontmatter, an die der
    Job unten etwas anhängt). Darum inhaltsbasiert statt namensbasiert.

    ``git stash create`` schreibt ein Commit-Objekt aus Index + Working Tree,
    **ohne** Working Tree, Index oder die Stash-Liste anzufassen (verifiziert) —
    der ideale Anker. Es erfasst nur getrackte Änderungen, deshalb wird die
    Untracked-Liste separat mitgeführt. Bei sauberem Tree liefert es leer; dann
    ist ``HEAD`` der richtige Anker (s. ``paths_changed_since()``).
    """
    tracked = _git(["stash", "create"], check=False).stdout.strip()
    return {"tracked": tracked, "untracked": sorted(_untracked_paths())}


def _untracked_paths() -> list[str]:
    out = _git(["ls-files", "--others", "--exclude-standard"], check=False).stdout
    return [line for line in out.splitlines() if line.strip()]


def paths_changed_since(snapshot: dict[str, object]) -> list[str]:
    """Pfade, die sich seit ``snapshot_worktree()`` geändert haben (PLAN-38 Stufe 2).

    Getrackt: Diff des Working Trees gegen den Schnappschuss-Anker (Löschungen
    eingeschlossen — ``git add -A -- <pfad>`` staget sie korrekt). Untracked:
    was jetzt da ist und vorher nicht. Gitignortes (``data/``) taucht in beiden
    Quellen nie auf und kann darum auch nicht versehentlich mitcommittet werden.
    """
    base = str(snapshot.get("tracked") or "").strip() or "HEAD"
    diff = _git(["diff", "--name-only", base], check=False)
    changed = {line for line in diff.stdout.splitlines() if line.strip()}
    before = set(snapshot.get("untracked") or [])  # type: ignore[arg-type]
    return sorted(changed | (set(_untracked_paths()) - before))


def _commit_if_staged(message: str, identity: tuple[str, str] | None) -> bool:
    if not _has_staged():
        return False
    args = []
    if identity is not None:
        name, email = identity
        args += ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    _git([*args, "commit", "-m", message])
    return True


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
    return _commit_if_staged(message, identity)


_CONFLICT_INFO_LINE = re.compile(r"^\d+ [0-9a-fA-F]+ [123]\t(.+)$", re.DOTALL)


LIVE_EDIT_WINDOW_S = 120
"""Wie lange nach dem letzten Schreibvorgang ein Pfad als "wird gerade
bearbeitet" gilt (PLAN-30 Ebene 4). Der Wert passt zu einem Menschen, der in
einer Datei arbeitet — nicht zu einem Scheduler: ein Job, der häufiger als
dieses Fenster läuft, hält seine Ausgabedatei dauerhaft "frisch" und blockiert
damit jeden Pull, der sie anfasst (Sync-Divergenz 2026-07-28, Befund 1). Wer
das nicht will, schaltet den mtime-Teil per ``live_within_s=0`` ab; der
dirty-Teil des Guards bleibt davon unberührt."""


def _pull_live_overlap(fetch_head: str, *, within_s: int = LIVE_EDIT_WINDOW_S,
                       now: float | None = None) -> set[str]:
    """PLAN-30 Ebene 5 (nutzt Ebene 4s Guard-Prinzip): welche Pfade würde das
    Einmischen von ``fetch_head`` anfassen, die GERADE dirty oder kürzlich
    bearbeitet wurden? Reiner Dry-Run über ``git merge-tree --write-tree -z``,
    dasselbe Prinzip wie ``mergeback.py::_live_overlap()``/``_merge_tree_
    paths()``, hier für den Pull-Schritt statt den Job-Branch-Merge-back —
    identische Semantik unabhängig davon, ob ``integrate()`` am Ende fast-
    forwarded, rebased oder merged: das sind exakt die Pfade, deren Inhalt
    sich im Working Tree ändern würde.

    ``-z`` statt der Textform (Review-Runde 5, Fund 1+2, live verifiziert):
    (1) bei einem echten Binärkonflikt behält der geschriebene Tree die HEAD-
    Seite unverändert bei, ``git diff --name-only`` zeigt den Pfad dadurch
    fälschlich als unverändert — die konfliktenden Pfade werden deshalb
    zusätzlich aus dem Dry-Run selbst extrahiert; (2) Pfade werden nie
    C-quoted, unabhängig von ``core.quotepath`` (das ``_git()`` ohnehin schon
    global auf ``false`` setzt — doppelt abgesichert)."""
    tree_oid, conflicted = _pull_merge_tree(fetch_head)
    if not tree_oid:
        return set()
    changed_proc = _git(["diff", "--name-only", "HEAD", tree_oid], check=False)
    changed = {p.strip() for p in changed_proc.stdout.splitlines() if p.strip()} | conflicted
    if not changed:
        return set()
    dirty = set(dirty_paths()) & changed
    return dirty | recently_touched_paths(repo.root(), sorted(changed), within_s=within_s, now=now)


def live_overlap_report(*, within_s: int = 120, now: float | None = None
                        ) -> list[tuple[str, float | None]]:
    """Welche Pfade blockieren gerade einen ``live_edit``-Skip, und wie alt ist
    ihr letzter Schreibvorgang? Liefert ``(pfad, alter_in_s)``, aufsteigend nach
    Alter; ``None`` als Alter heißt "nicht statbar" (Pfad existiert nicht mehr).

    Motivation (Sync-Divergenz 2026-07-28, Befund 1): die bisherige Meldung
    "Zieldatei wird bearbeitet" ließ offen, WELCHE Datei blockiert und ob
    überhaupt ein Mensch beteiligt ist. Im Vorfall war es die Ausgabedatei
    eines 10-Minuten-Schedules — der Guard hielt also nicht einen tippenden
    Menschen von der Datei fern, sondern den eigenen Scheduler, und die
    Empfehlung "gleich nochmal versuchen" lief ins Leere. Mit Pfad und Alter
    ist das auf einen Blick zu unterscheiden.

    Nutzt das vorhandene ``FETCH_HEAD`` und fetcht bewusst NICHT neu: die
    Funktion läuft unmittelbar nach einem ``integrate()``, das gerade gefetcht
    hat — ein zweiter Fetch würde nur ein Race-Fenster gegen einen Dritten
    (Daemon-Synchronizer, zweites ``/sync``) öffnen. Ohne ``FETCH_HEAD``
    (nie gefetcht) gibt es nichts zu berichten: leere Liste."""
    # Rein diagnostisch und ausschließlich in Fehlerpfaden aufgerufen (Skip-
    # Meldung, Daemon-Log) — darf deshalb unter keinen Umständen selbst die
    # Ursache eines Abbruchs werden, auch nicht bei kaputtem/fehlendem Repo.
    try:
        head = _git(["rev-parse", "FETCH_HEAD"], check=False)
        if head.returncode != 0:
            return []
        overlap = _pull_live_overlap(head.stdout.strip(), within_s=within_s, now=now)
    except OSError:
        return []
    if not overlap:
        return []
    ref = time.time() if now is None else now
    out: list[tuple[str, float | None]] = []
    for p in overlap:
        try:
            out.append((p, ref - (repo.root() / p).stat().st_mtime))
        except OSError:
            out.append((p, None))
    out.sort(key=lambda item: (item[1] is None, item[1], item[0]))
    return out


def _pull_merge_tree(fetch_head: str) -> tuple[str, set[str]]:
    """``git merge-tree --write-tree -z HEAD fetch_head`` ausführen (reiner
    Dry-Run, kein Working-Tree-Zugriff) und ``(tree_oid, konfliktende_pfade)``
    zurückgeben — extrahiert aus ``_pull_live_overlap()`` (Nachtrag
    2026-07-16, ``/sync``-Vorschau), damit sowohl die Idle-Guard-Überlapp-
    Prüfung als auch eine reine Konfliktvorhersage dieselbe Berechnung
    teilen, statt sie zweimal leicht unterschiedlich zu implementieren.
    Identisches Prinzip wie ``mergeback.py::_merge_tree_paths()``, hier
    bewusst dupliziert statt importiert (andere ``_git()``-Bindung: explizites
    ``repo_root`` dort, ambientes ``repo.root()`` hier)."""
    dry = _git(["merge-tree", "--write-tree", "-z", "HEAD", fetch_head], check=False)
    fields = dry.stdout.split("\x00")
    tree_oid = fields[0].strip() if fields else ""
    if not tree_oid:
        return "", set()
    conflicted: set[str] = set()
    for field in fields[1:]:
        m = _CONFLICT_INFO_LINE.match(field)
        if m:
            conflicted.add(m.group(1))
    return tree_oid, conflicted


def _integrate_impl(branch: str, keep_conflict: bool = False,
                    strategy: str = "rebase", guard_live_paths: bool = False,
                    now: float | None = None, dry_run: bool = False,
                    live_within_s: int = LIVE_EDIT_WINDOW_S
                    ) -> tuple[bool, str | None, int | None, int | None]:
    """Interne Implementierung — liefert zusätzlich ``(ahead, behind)``,
    berechnet aus DENSELBEN ``local``/``remote``-SHAs, die auch ``ok``/``kind``
    bewerten, nie aus einem später erneut aufgelösten ``origin/<branch>``
    (Race-sicher gegen einen zwischenzeitlichen Fetch durch einen Dritten,
    z. B. den Daemon-Synchronizer oder ein zweites ``/sync`` — Sync-Preview-
    Pull-Bug, 2026-07-25). Nur bei ``dry_run`` befüllt (kostet zwei zusätzliche
    ``rev-list``-Aufrufe); die echten Ausführungspfade brauchen die Zahlen nie
    — ``integrate()`` selbst verwirft sie ohnehin. Siehe :func:`integrate` für
    den vollen Docstring der Semantik, :func:`integrate_preview` für den
    Vorschau-Aufrufer."""
    if is_conflict_resolution_pending():
        return False, "repo_busy", None, None

    fetch = _git(["fetch", "origin", branch], check=False, timeout=GIT_NET_TIMEOUT)
    if fetch.returncode != 0:
        return False, _classify_failure(fetch.stderr.strip()), None, None

    local = _git(["rev-parse", "HEAD"]).stdout.strip()
    remote = _git(["rev-parse", "FETCH_HEAD"]).stdout.strip()

    def _counts() -> tuple[int, int]:
        a = _git(["rev-list", "--count", f"{remote}..{local}"])
        b = _git(["rev-list", "--count", f"{local}..{remote}"])
        return int(a.stdout.strip()), int(b.stdout.strip())

    if local == remote:
        return (True, None, 0, 0) if dry_run else (True, None, None, None)
    if _git(["merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD"], check=False).returncode == 0:
        # lokal voraus — Push erledigt den Rest
        if dry_run:
            ahead, _ = _counts()
            return True, None, ahead, 0
        return True, None, None, None

    if guard_live_paths and _pull_live_overlap(remote, within_s=live_within_s, now=now):
        return False, "live_edit", None, None

    if _git(["merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], check=False).returncode == 0:
        if dry_run:
            # würde sauber fast-forwarden, kein Konflikt möglich
            _, behind = _counts()
            return True, None, 0, behind
        ff = _git(["merge", "--ff-only", "FETCH_HEAD"], check=False)
        if ff.returncode == 0:
            return True, None, None, None
        # Bis m.rau/bibi#160 stand hier hart "conflict". Ein Fast-Forward hat
        # per Definition keinen Konflikt — scheitert er doch, steht dirty Arbeit
        # im Weg ("would be overwritten by merge"), und das ist etwas anderes.
        return False, _classify_failure(ff.stderr.strip()), None, None

    if dry_run:
        # m.rau/bibi#160(c): die Vorschau muss denselben Ausgang vorhersagen,
        # den ``--apply`` nimmt — die Zusage ihres eigenen Docstrings. Vorher
        # prüfte sie per ``merge-tree`` nur den *Inhalt* und meldete „geht
        # sauber durch", während der scharfe Lauf am dirty Tree scheiterte.
        # ``git rebase`` verlangt einen sauberen Tree, unabhängig davon, welche
        # Dateien der Pull anfasst — deshalb genügt hier ``is_dirty()``, und
        # deshalb steht es vor der Inhaltsprüfung: git kommt gar nicht so weit.
        if strategy == "rebase" and is_dirty():
            return False, "dirty", None, None
        _, conflicted = _pull_merge_tree(remote)
        if conflicted:
            return False, "conflict", None, None
        ahead, behind = _counts()
        return True, None, ahead, behind

    # echte Divergenz → rebase (Default) oder merge (bot-robust)
    if strategy == "merge":
        mg = _git(["merge", "--no-edit", "FETCH_HEAD"], check=False, timeout=GIT_NET_TIMEOUT)
        if mg.returncode != 0:
            kind = _classify_failure(mg.stderr.strip())
            _git(["merge", "--abort"], check=False)
            return False, kind, None, None
        return True, None, None, None

    rb = _git(["rebase", "FETCH_HEAD"], check=False, timeout=GIT_NET_TIMEOUT)
    if rb.returncode != 0:
        kind = _classify_failure(rb.stderr.strip())
        if kind == "conflict" and keep_conflict:
            return False, "conflict", None, None  # im Tree stehen lassen — KI-Auflösung folgt
        _git(["rebase", "--abort"], check=False)
        return False, kind, None, None
    return True, None, None, None


def integrate(branch: str, keep_conflict: bool = False,
             strategy: str = "rebase", guard_live_paths: bool = False,
             now: float | None = None, dry_run: bool = False,
             live_within_s: int = LIVE_EDIT_WINDOW_S) -> tuple[bool, str | None]:
    """Origin minimal integrieren: fetch + ff/rebase|merge (kein Push).

    Gibt (ok, kind) zurück. kind ist None bei Erfolg, sonst
    ``"unreachable"``/``"auth"``/``"conflict"``/``"live_edit"``/``"dirty"``.

    ``"dirty"`` (m.rau/bibi#160) heißt: git hat den Vorgang gar nicht erst
    begonnen, weil uncommittete Arbeit im Weg steht. Das ist **kein** Konflikt —
    es gibt nichts aufzulösen, und ``sync_conflict`` darf dafür nicht gesetzt
    werden. Der Unterschied zu ``"live_edit"``: dort fasst der Pull *genau die*
    dirty Dateien an (bibi-eigener Guard), hier blockiert der Working Tree als
    Ganzes (git-eigene Regel für Rebase).

    ``keep_conflict=False`` (Default, für save/close/done/hook-stop): bricht
    einen Konflikt sauber ab. ``keep_conflict=True`` (für interaktives
    ``/sync``): lässt den Konflikt im Working Tree stehen, damit die geteilte
    KI-Auflösung (§1.6 A) die Marker auflösen und ``continue_rebase_and_push``
    rufen kann.

    ``guard_live_paths`` (PLAN-30 Ebene 4/5, Default False): bevor irgendetwas
    geschrieben wird, prüfen, ob das Einmischen eine gerade dirty oder kürzlich
    bearbeitete Datei anfassen würde — wenn ja, den GESAMTEN Pull-Versuch
    überspringen (``kind "live_edit"``), kein Teil-Skip. ``now`` optional für
    deterministische Tests, an den Guard durchgereicht.

    ``live_within_s`` steuert davon nur den zweiten, zeitbasierten Teil;
    ``0`` schaltet ihn ab, der dirty-Teil bleibt in jedem Fall scharf. Diese
    Trennung stammt aus der Sync-Divergenz vom 2026-07-28 (Befund 1): die
    beiden Hälften des Guards schützen Unterschiedliches. "Dirty" ist echte,
    uncommittete Arbeit — die darf ein Pull nie überfahren, egal wer ihn
    auslöst. "Kürzlich geschrieben" ist dagegen eine Heuristik für *einen
    tippenden Menschen*, und die greift systematisch daneben, sobald die
    fragliche Datei die Ausgabe eines kurz getakteten Jobs ist: sie wird nie
    ruhig, der Pull nie ausgeführt, die Divergenz nie aufgelöst. Der
    interaktive ``/sync`` setzt deshalb ``live_within_s=0`` — wer explizit
    synchronisiert, ist selbst der Mensch, den die Heuristik schützen soll —
    während der unbeaufsichtigte Synchronizer-Loop beim vollen Fenster bleibt.

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

    Verweigert VOR dem Fetch (kein unnötiger Netzwerk-Aufruf), wenn irgendwo
    im Repo bereits ein Merge/Rebase offen ist (``kind="repo_busy"``, Review-
    Runde 4 Fund 1) — sonst könnte z. B. ein Rebase-Versuch mitten in einem
    von ``/sync`` offen gelassenen Job-Branch-Merge-Konflikt fehlschlagen und
    fälschlich als eigener "conflict" klassifiziert werden, statt als das zu
    erkennen, was es ist: ein anderer, bereits laufender Vorgang. NICHT über
    ``force``/einen Parameter umgehbar — das wäre in genau diesem Szenario
    der Fehler, den dieser Guard verhindern soll.

    ``dry_run`` (``/sync``-Vorschau, Nachtrag 2026-07-16): fetcht weiterhin
    wirklich (aktualisiert nur Remote-Tracking-Refs, keine Mutation am
    lokalen Branch/Working-Tree) — ohne aktuelles ``FETCH_HEAD`` gäbe es
    nichts Sinnvolles vorherzusagen. Alles danach bleibt rein lesend: ein
    möglicher Fast-Forward wird als solcher erkannt, aber NICHT ausgeführt;
    bei echter Divergenz liefert derselbe ``_pull_merge_tree()``-Dry-Run, den
    der Idle-Guard ohnehin schon nutzt, die Vorhersage statt eines echten
    ``rebase``/``merge``. Für die ``/sync``-Vorschau selbst lieber
    :func:`integrate_preview` nutzen — liefert zusätzlich ahead/behind, ohne
    ein zweites, potenziell abweichendes Race-Fenster gegen ``origin/<branch>``
    zu öffnen."""
    ok, kind, _ahead, _behind = _integrate_impl(
        branch, keep_conflict=keep_conflict, strategy=strategy,
        guard_live_paths=guard_live_paths, now=now, dry_run=dry_run,
        live_within_s=live_within_s)
    return ok, kind


def integrate_preview(branch: str, *, guard_live_paths: bool = True,
                      now: float | None = None,
                      live_within_s: int = LIVE_EDIT_WINDOW_S
                      ) -> tuple[bool, str | None, int | None, int | None]:
    """``/sync``s Vorschau: wie ``integrate(dry_run=True)``, liefert aber
    zusätzlich ``(ahead, behind)`` — beide aus demselben Fetch berechnet, den
    auch ``ok``/``kind`` bewerten. Ersetzt den früheren Zwei-Schritt-Aufruf
    (erst ``integrate(dry_run=True)``, danach separat ``ahead_count()``):
    dazwischen lag ein Race-Fenster — ein zwischenzeitlicher Fetch durch einen
    Dritten (Daemon-Synchronizer, ein zweites ``/sync``) hätte
    ``origin/<branch>`` weiterbewegen können, sodass die Zählung einen anderen
    Stand beschrieben hätte als den, den ``ok``/``kind`` tatsächlich bewertet
    haben (Sync-Preview-Pull-Bug, 2026-07-25). ``ahead``/``behind`` sind
    ``None`` nur, wenn schon ``ok`` selbst nichts zu zählen hat (Fetch schlug
    fehl, ein Rebase/Merge war schon offen, o. Ä.) — bei ``ok=True`` immer
    zwei echte Zahlen, nie ``None``."""
    return _integrate_impl(branch, keep_conflict=True, guard_live_paths=guard_live_paths,
                           now=now, dry_run=True, live_within_s=live_within_s)


def abort_rebase() -> None:
    _git(["rebase", "--abort"], check=False)


def abort_merge() -> None:
    """PLAN-30 Ebene 3, Pendant zu ``abort_rebase()`` für einen offen
    gelassenen Job-Branch-Merge-Konflikt (``keep_conflict=True``)."""
    _git(["merge", "--abort"], check=False)


def continue_merge_and_push() -> tuple[bool, list[str], str | None]:
    """Nach KI-Auflösung eines Job-Branch-Merge-Konflikts (PLAN-30 Ebene 3):
    gelöste Dateien stagen, Merge-Commit abschließen, pushen. Analog
    ``continue_rebase_and_push()``, aber ``git commit`` statt ``rebase
    --continue`` — ein Merge detached HEAD nicht, der Branch bleibt während
    des offenen Konflikts bekannt."""
    log: list[str] = []
    _git(["add", "-A"])
    commit = _git(["commit", "--no-edit"], check=False)
    if commit.returncode != 0:
        if conflicted_files():
            log.append("weiterhin Konflikte — auflösen, dann erneut continue")
            return False, log, "conflict"
        log.append(f"commit FAILED: {commit.stderr.strip()}")
        return False, log, _classify_failure(commit.stderr.strip())
    log.append("Konflikt aufgelöst, Merge abgeschlossen")
    branch = current_branch()
    pok, pmsg, pkind = push(branch)
    log.append(f"push {'ok' if pok else 'FAIL'}")
    if not pok and pmsg:
        log.append(pmsg)
    return (pok, log, None if pok else pkind)


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
    pok, pmsg, pkind = push(branch)
    log.append(f"push {'ok' if pok else 'FAIL'}")
    if not pok and pmsg:
        log.append(pmsg)
    return (pok, log, None if pok else pkind)


def push(branch: str, *, guard_live_paths: bool = False) -> tuple[bool, str, str | None]:
    """Branch pushen. Bei Reject einmal integrieren + retry.

    Gibt ``(ok, msg, kind)`` zurück — ``kind`` ist ``None`` bei Erfolg, sonst
    dieselbe Klassifikation wie :func:`integrate` (``"conflict"``,
    ``"live_edit"``, ``"unreachable"``, ``"auth"``, ``"repo_busy"``).

    Der Reject-Retry lief bis 2026-07-28 als roher ``git pull --rebase`` direkt
    über ``_git()`` — vorbei an PLAN-30 Ebene 4/5, vorbei an
    ``is_conflict_resolution_pending()`` und mit einer Fehlermeldung, die der
    Aufrufer per ``_classify_failure()`` erst wieder erraten musste. Zwei
    Folgen, beide im Vorfall belegt (Sync-Divergenz 2026-07-28, Befund 2):

    1. Ausgerechnet dieser Pfad rührte den geteilten Checkout unbeaufsichtigt
       an — im Synchronizer alle drei Minuten, ohne Backoff — während der
       Idle-Guard danebenstand und den regulären Weg blockierte.
    2. Der abgebrochene Rebase lieferte "rebase failed (aborted)"; das ist
       weder ``unreachable`` noch ``auth``, also stufte ``_classify_failure()``
       es als ``conflict`` ein und der Synchronizer setzte ``sync_conflict``.
       Ein bloßer Idle-Skip wäre so nie von einem echten Konflikt zu
       unterscheiden gewesen.

    Jetzt geht der Retry durch ``_integrate_impl()`` und erbt Guard,
    Konfliktbehandlung und Klassifikation des regulären Pfades.
    ``guard_live_paths`` Default ``False`` = bisheriges Verhalten für alle
    interaktiven Aufrufer (``/save``, ``/sync``, ``continue_*``); der
    unbeaufsichtigte Synchronizer-Sweep setzt es auf ``True``. Strategie
    bleibt bewusst ``rebase`` wie bisher — die merge-Variante ist eine
    Entscheidung des Pull-Loops (s. ``daemon/synchronizer.py``), nicht dieses
    Retrys."""
    args = ["push", "-u", "origin", branch]
    proc = _git(args, check=False, timeout=GIT_NET_TIMEOUT)
    if proc.returncode == 0:
        return True, (proc.stdout + proc.stderr).strip(), None
    if "rejected" in proc.stderr or "non-fast-forward" in proc.stderr:
        ok, kind, _a, _b = _integrate_impl(branch, guard_live_paths=guard_live_paths)
        if not ok:
            return False, f"push abgelehnt, Integration nicht möglich ({kind})", kind
        retry = _git(args, check=False, timeout=GIT_NET_TIMEOUT)
        msg = (retry.stdout + retry.stderr).strip()
        return (True, msg, None) if retry.returncode == 0 else (False, msg, _classify_failure(msg))
    msg = (proc.stdout + proc.stderr).strip()
    return False, msg, _classify_failure(msg)


# --- Orchestrierung (§4.9: commit → integrate → push/ask) ---

def commit_and_push(scope: Path | None, message: str, do_push: bool,
                    identity: tuple[str, str] | None = None, *,
                    guard_live_paths: bool = False) -> tuple[bool, list[str], str | None]:
    """Vollständiger Schreibpfad. Gibt (ok, log, kind) zurück.

    ``do_push`` spiegelt die Sync-Matrix: an → pushen; aus → committen +
    integrieren, aber **nicht** pushen (der Skill fragt dann nach).
    ``identity``: s. ``stage_and_commit()`` (PLAN-21 Befund 8).

    ``guard_live_paths`` (Default ``False``) entscheidet nach der Leitregel,
    die PLAN-30 Ebene 4/5 seit dem 2026-07-28 einheitlich zieht: der Idle-Guard
    gilt für **unbeaufsichtigte** Schreibvorgänge, nicht für solche, die ein
    anwesender Mensch gerade ausgelöst hat. ``/save``, ``/close``, ``/done``
    lassen ihn deshalb aus (der Mensch ist da und hat entschieden), der
    Synchronizer-Hintergrund-Sweep setzt ihn auf ``True`` — er ist genau der
    Fall, für den Ebene 4 gebaut wurde. Reicht bis in den Reject-Retry von
    :func:`push` durch, der ihn bis dahin komplett umging (Befund 2).

    Verweigert VOR jedem Schreibversuch, wenn irgendwo im Repo bereits ein
    Merge/Rebase offen ist (``is_conflict_resolution_pending()``, Review-
    Runde 4 Fund 1) — ``git add -A`` würde eine noch unmerged Datei sonst mit
    ihrem Konfliktmarker-Inhalt lautlos "auflösen" und committen, live gegen
    echtes Git bewiesen. Gilt für JEDEN Aufrufer dieser Funktion (``/save``,
    ``/close``, ``/done``, den Stop-Hook, den Synchronizer-Hintergrund-Push)
    — ``kind="repo_busy"`` reiht sich in den bestehenden ``(ok, log, kind)``-
    Vertrag ein, kein Aufrufer muss dafür angepasst werden."""
    if is_conflict_resolution_pending():
        return False, ["Repo hat einen offenen Merge/Rebase — erst "
                       "`bibi-ctrl sync continue` (oder `sync abort`) abschließen."], "repo_busy"
    log: list[str] = []
    committed = stage_and_commit(scope, message, identity)
    log.append(f"committed: {message}" if committed else "nothing to commit")

    branch = current_branch()
    ok, kind = integrate(branch, guard_live_paths=guard_live_paths)
    if not ok:
        log.append(f"integrate FAILED ({kind})")
        return False, log, kind
    log.append("integrated")

    if not do_push:
        log.append("nicht gepusht (auto_sync off) — push mit: bibi-ctrl save --push")
        return True, log, None

    pok, pmsg, pkind = push(branch, guard_live_paths=guard_live_paths)
    log.append(f"push {'ok' if pok else 'FAIL'}")
    if not pok and pmsg:
        log.append(pmsg)
    return (pok, log, None if pok else pkind)


def remove_path_and_push(path: Path, message: str,
                         do_push: bool) -> tuple[bool, list[str], str | None]:
    """Pfad aus Index + Working-Tree entfernen, committen, integrieren, push (gated).

    Funktioniert für getrackte wie ungetrackte Ordner: ``--ignore-unmatch``
    bleibt still, wenn nichts im Index ist; ``rmtree`` räumt Reste weg.

    Verweigert VOR dem ``git rm`` (Review-Runde 6, Fund 1 — kritisch, live
    bewiesen), wenn irgendwo im Repo bereits ein Merge/Rebase offen ist: ohne
    diesen Guard löscht ``git rm -rf`` auch eine noch unmerged Datei (löst
    ihren Konflikt lautlos durchs Entfernen), der Commit direkt danach nimmt
    das klaglos an — ``MERGE_HEAD`` verschwindet, die laufende
    Konfliktauflösung eines Menschen ist komplett weg, BEVOR ``integrate()``
    (das seit Review-Runde 4 selbst schon schützt) überhaupt erreicht wird.
    Dieselbe Prüfung wie ``commit_and_push()`` — dort nicht wiederverwendbar,
    weil ``remove_path_and_push()`` ``git rm``/``rmtree`` statt
    ``stage_and_commit()`` nutzt, ein eigener, unabhängiger Schreibpfad."""
    if is_conflict_resolution_pending():
        return False, ["Repo hat einen offenen Merge/Rebase — erst "
                       "`bibi-ctrl sync continue` (oder `sync abort`) abschließen."], "repo_busy"
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
    pok, pmsg, pkind = push(branch)
    log.append(f"push {'ok' if pok else 'FAIL'}")
    if not pok and pmsg:
        log.append(pmsg)
    return (pok, log, None if pok else pkind)
