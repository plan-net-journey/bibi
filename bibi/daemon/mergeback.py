"""Merge-back ``agent/<slug>`` → trunk (PLAN-6; Worker-Analyse §6).

Der Phase-3-Worktree-Lifecycle committet Job-Ergebnisse auf ``agent/<slug>``
(``worktree.commit``), führte sie aber nie nach ``trunk`` zusammen — darum erreichte
nichts den Vault. Diese Funktion schließt die Lücke **scheduler-seitig** (dort lebt
das trunk-Repo) und **unter einem gemeinsamen Lock** mit dem Synchronizer, damit der
Merge nicht gegen einen Pull/Push auf trunk rennt.

Konflikt-sicher: bei einem Merge-Konflikt wird sauber abgebrochen (``merge --abort``),
trunk bleibt unverändert und der Commit liegt weiter auf ``agent/<slug>`` — nichts
geht verloren, nichts hängt.

PLAN-30 Ebene 2: zwei Fehlschlagsarten, unterschieden anhand ``MERGE_HEAD``
(live am echten Repo bewiesen, s. PLAN-30 "Nachtrag: der exakte Stash-
Mechanismus"):

- **Modus A — Dirty-Tree-Verweigerung** (``"blocked"``): eine Datei ist im
  Working Tree dirty **und** Teil des Merge-Diffs. Git verweigert sofort, ohne
  ``MERGE_HEAD`` anzulegen — kein echter Inhaltskonflikt, löst sich von selbst,
  sobald die Datei committet wird. Zählt NICHT als Fehlschlag.
- **Modus B — echter Inhaltskonflikt** (``"conflict"``): ``MERGE_HEAD``
  existiert, Git kam bis zum eigentlichen 3-Way-Merge durch. Zählt als
  Fehlschlag (wie ein generischer ``"error"``) auf die Quarantäne (§ unten).

``"quarantined"``: kein Merge-Versuch fand statt — entweder weil trunk sich
seit dem letzten Fehlschlag nicht bewegt hat (keine neue Chance), oder weil der
Branch nach 3 aufeinanderfolgenden Fehlschlägen hart eskaliert wurde (A4,
``merge_quarantine.ESCALATE_AFTER``) — s. ``merge_quarantine.py``.

PLAN-30 Ebene 4 (G1, Nebenbedingung 0): ``"live_edit"`` — kein Merge-Versuch
fand statt, weil er mindestens einen Pfad angefasst hätte, der GERADE dirty
oder kürzlich (``IDLE_WINDOW_S``) bearbeitet wurde. Reiner Dry-Run über ``git
merge-tree --write-tree`` (git ≥2.38) — kein Working-Tree-Zugriff, also auch
keine eigene mtime-Störung durch den Check selbst. Zählt NICHT als Fehlschlag
(wie Modus A) und ist NICHT über ``force`` umgehbar (anders als die
Quarantäne-Vorprüfung) — schützt eine live bearbeitete Datei auch dann, wenn
ein Mensch die Quarantäne per ``/sync`` bewusst übersteuert.

Review-Runde 4 (Ebene 3), Fund 1 (kritisch): ``"repo_busy"`` — kein Merge-
Versuch fand statt, weil in ``repo_root`` bereits ein ANDERER Merge/Rebase
offen ist (z. B. ein von ``/sync`` interaktiv offen gelassener Job-Branch-
Konflikt, ``keep_conflict=True``). Allererste Prüfung in ``_merge_locked()``,
vor allem anderen, NICHT über ``force`` umgehbar — live gegen echtes Git
bewiesen, dass ohne diesen Guard ein Merge-Versuch für einen anderen Branch
Gits generisches "Merging is not possible"-Refusal fälschlich als eigenen
Konflikt missverstand und mit ``merge --abort`` die laufende Konflikt-
auflösung eines Menschen zerstörte, s. Docstring von
``_conflict_resolution_pending()``.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bibi.daemon import merge_quarantine
from bibi.daemon.worktree import bot_identity, branch_name
from bibi.git_ops import recently_touched_paths

# status: "merged" | "up_to_date" | "conflict" | "blocked" | "quarantined" | "error"
#       | "live_edit" | "repo_busy"

# Stabile Teilzeichenkette aus Gits eigener Meldung bei einer Dirty-Tree-
# Verweigerung ("error: Your local changes to the following files would be
# overwritten by merge: ... Aborting") — case-insensitiv geprüft, wie das
# bestehende "already up to date"-Match unten.
_DIRTY_REFUSAL_MARKER = "would be overwritten by merge"

# PLAN-30 Ebene 4, offene Frage 5: kürzer als PushDebouncers kürzeste Stufe
# (idle_s=600 für <50 Diff-Zeilen) — der für diesen interaktiveren Anwendungs-
# fall (Merge-back, nicht Push-Timing) zu träge wäre.
IDLE_WINDOW_S = 120


@dataclass(frozen=True, slots=True)
class MergeResult:
    status: str
    trunk_sha: str = ""
    detail: str = ""


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    # LC_ALL=C (Review-Runde 3, Fund 2): _DIRTY_REFUSAL_MARKER prüft Gits
    # Fehlertext auf eine feste englische Teilzeichenkette — ein NLS-fähiges
    # Git (z. B. auf Linux, anders als Apples eigenes macOS-Git) könnte sonst
    # lokalisiert antworten und Modus A fälschlich als generischen "error"
    # zählen, der (anders als Modus A) auf die Eskalationsgrenze angerechnet
    # wird. Nur für diesen Prozess erzwungen, kein globaler Seiteneffekt.
    #
    # -c core.quotepath=false (Review-Runde 5, Fund 2, live verifiziert): ohne
    # das quotet Git jeden Pfad mit Nicht-ASCII-Zeichen in JEDER Textausgabe
    # (z. B. "Kündigung.md" → "K\303\274ndigung.md") — dieses Vault hat
    # bereits solche Pfade (Getränke/, Kündigung.pdf, München_...). Jeder Code
    # hier, der Pfade aus Git-Ausgabe parst (_dirty_subset(), _live_overlap()),
    # würde den Überlapp sonst schlicht nicht erkennen.
    env = {**os.environ, "LC_ALL": "C"}
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd,
                          capture_output=True, text=True, check=False, env=env)


def _conflict_resolution_pending(repo_root: Path) -> bool:
    """True, wenn IRGENDEIN Merge oder Rebase in ``repo_root`` bereits offen
    ist — egal wer/was ihn gestartet hat. Wie ``git_ops.is_conflict_resolution_
    pending()``, aber an ein EXPLIZITES ``repo_root`` gebunden statt an das
    AMBIENTE ``repo.root()`` (dieselbe Bindungs-Diskrepanz wie bei
    ``_dirty_subset()``, s. dessen Docstring — direkter Pfad-Check statt
    ``git rev-parse --git-dir``, passend zum bereits bestehenden Muster in
    dieser Datei, z. B. der ``MERGE_HEAD``-Prüfung weiter unten).

    Review-Runde 4, Fund 1, live gegen echtes Git bewiesen: ohne diesen Guard
    interpretierte ein Merge-Versuch für Branch B, während Branch As Konflikt
    (``keep_conflict=True``) noch offen war, Gits Refusal ("Merging is not
    possible because you have unmerged files") fälschlich als EIGENEN
    Konflikt von B — schrieb eine falsche Quarantäne-Zeile für B UND rief
    ``merge --abort``, was As komplette, noch laufende Konfliktauflösung
    zurücksetzte (Dateiinhalt zurück auf den Vor-Konflikt-Stand). Muss VOR
    jedem Merge-Versuch geprüft werden, NICHT über ``force`` umgehbar — das
    wäre in genau diesem Szenario der Fehler."""
    git_dir = repo_root / ".git"
    return ((git_dir / "MERGE_HEAD").exists()
           or (git_dir / "rebase-merge").exists()
           or (git_dir / "rebase-apply").exists())


def _dirty_subset(repo_root: Path, candidates: set[str]) -> set[str]:
    """Welche der ``candidates`` (repo-root-relative Pfade) sind gerade dirty?
    Scoped per Pathspec (nur diese Pfade — billig, kein voller Status-Scan).

    Dupliziert bewusst ``git_ops.dirty_paths()``s Porcelain-v2-Parsing statt es
    zu importieren: ``git_ops._git()`` bindet an das AMBIENTE ``repo.root()``,
    diese Datei an ein EXPLIZITES ``repo_root`` je Aufruf (Test-/Lock-Isolation,
    z. B. Wegwerf-Repos in Tests) — beide Stile passen nicht zusammen, ohne
    eine der beiden Garantien zu verlieren. Dieselbe bewährte Parsing-Logik,
    nur an den anderen ``_git()``-Aufruf-Stil angepasst."""
    if not candidates:
        return set()
    proc = _git(["status", "--porcelain=v2", "--untracked-files=all", "--no-renames",
                "--", *sorted(candidates)], cwd=repo_root)
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("? "):
            found.add(line[2:])
        elif line.startswith("1 "):
            found.add(line.split(" ", 8)[-1])
        elif line.startswith("u "):
            found.add(line.split(" ", 10)[-1])
    return found & candidates


_CONFLICT_INFO_LINE = re.compile(r"^\d+ [0-9a-fA-F]+ [123]\t(.+)$", re.DOTALL)


def _merge_tree_paths(repo_root: Path, branch: str, trunk: str) -> tuple[str, set[str]]:
    """``git merge-tree --write-tree -z`` ausführen (Dry-Run, kein Working-
    Tree-Zugriff) und (tree_oid, konfliktende_pfade) zurückgeben.

    ``-z`` (Review-Runde 5, Fund 2) statt der Textform: NUL-getrennt, Pfade
    nie C-quoted (unabhängig von ``core.quotepath`` — doppelt abgesichert,
    da ``_git()`` es ohnehin schon global auf ``false`` setzt). Die
    konfliktenden Pfade werden HIER extrahiert (aus den ``<mode> <oid>
    <stage>\\t<pfad>``-Info-Zeilen), NICHT nur über einen späteren ``git
    diff --name-only`` — Review-Runde 5, Fund 1 (kritisch, live bewiesen):
    bei einem echten BINÄR-Konflikt behält der geschriebene Tree die
    ``trunk``-Seite unverändert bei (Git kann Binärinhalte nicht per Marker
    kennzeichnen), ``git diff --name-only trunk <tree-oid>`` zeigt den Pfad
    dadurch fälschlich als UNVERÄNDERT — der Idle-Guard hätte eine dirty
    Binärdatei so nie erkannt, und ein anschließender automatischer
    ``merge --abort`` hätte ihren uncommitteten Inhalt beim Live-Test
    nachweislich unwiederbringlich verworfen."""
    dry = _git(["merge-tree", "--write-tree", "-z", trunk, branch], cwd=repo_root)
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


def _live_overlap(repo_root: Path, branch: str, trunk: str, *,
                  now: float | None = None) -> set[str]:
    """PLAN-30 Ebene 4 (G1): welche Pfade würde ein Merge von ``branch`` nach
    ``trunk`` anfassen, die GERADE dirty oder kürzlich (``IDLE_WINDOW_S``)
    bearbeitet wurden? Reiner Dry-Run über ``git merge-tree --write-tree``
    (git ≥2.38, auf Mac 2.39.5 und sarasate 2.43.0 verfügbar) — berechnet das
    Merge-Ergebnis als Tree-Objekt in der Objekt-Datenbank, OHNE den Working
    Tree anzufassen (kein Stash-Tanz, keine mtime-Störung durch den Check
    selbst — anders als ein echter ``git merge``-Versuch).

    Zwei Quellen für "welche Pfade ändern sich" (Review-Runde 5, Fund 1):
    ein sauberer, konfliktfreier Diff (``git diff --name-only``) UND die
    konfliktenden Pfade aus dem Dry-Run selbst (``_merge_tree_paths()``) —
    letztere fehlen im Diff bei Binärkonflikten sonst komplett.

    ``now`` (optional): an ``recently_touched_paths()`` durchgereicht, für
    deterministische Tests (``tmp_path`` + ``os.utime()``, kein echtes Timing).

    Kann der Dry-Run selbst keine Antwort liefern (z. B. ungültige Refs), fällt
    dies defensiv auf "kein Überlapp" zurück (fail-open) — ein einzelner
    Auswertungsfehler soll nie einen sonst funktionierenden Merge dauerhaft
    blockieren, nur das ursprüngliche (Vor-Ebene-4) Verhalten für diesen einen
    Versuch wiederherstellen."""
    tree_oid, conflicted = _merge_tree_paths(repo_root, branch, trunk)
    if not tree_oid:
        return set()
    changed_proc = _git(["diff", "--name-only", trunk, tree_oid], cwd=repo_root)
    changed = {p.strip() for p in changed_proc.stdout.splitlines() if p.strip()} | conflicted
    if not changed:
        return set()
    return (_dirty_subset(repo_root, changed)
           | recently_touched_paths(repo_root, sorted(changed), within_s=IDLE_WINDOW_S, now=now))


def merge_back(*, repo_root: Path, slug: str, trunk: str = "trunk", lock=None,
               force: bool = False, keep_conflict: bool = False,
               now: float | None = None) -> MergeResult:
    """``agent/<slug>`` nach ``trunk`` mergen (im trunk-Working-Copy ``repo_root``).

    ``lock`` (optional, ein ``threading.Lock``-artiger Kontext) wird um die
    Git-Operation gehalten — derselbe ``sync_lock`` wie im Synchronizer.

    ``force`` (PLAN-30 Ebene 3, für ``/sync``): überspringt die Quarantäne-
    Vorprüfung — ein Mensch, der ``/sync`` ausführt, IST die neue Chance, auf
    die die Quarantäne sonst wartet.

    ``keep_conflict`` (PLAN-30 Ebene 3, für ``/sync``): bricht einen echten
    Inhaltskonflikt (Modus B) NICHT automatisch ab, sondern lässt Marker +
    ``MERGE_HEAD`` im Working Tree stehen — analog ``git_ops.integrate(...,
    keep_conflict=True)`` für Pull-Konflikte. Nur für den interaktiven
    ``/sync``-Pfad gedacht; Sweep/Ebene-1-Trigger nutzen weiterhin den Default
    (sauberer Abbruch, nie ein hängender Tree ohne Aufsicht).

    ``now`` (PLAN-30 Ebene 4, optional): an den Idle-Fenster-Guard
    durchgereicht, für deterministische Tests — Default ``None`` nutzt die
    echte Uhrzeit."""
    with (lock if lock is not None else contextlib.nullcontext()):
        return _merge_locked(repo_root=repo_root, slug=slug, trunk=trunk,
                             force=force, keep_conflict=keep_conflict, now=now)


def _merge_locked(*, repo_root: Path, slug: str, trunk: str,
                  force: bool = False, keep_conflict: bool = False,
                  now: float | None = None) -> MergeResult:
    # Review-Runde 4, Fund 1 (kritisch): ALLERERSTE Prüfung, vor allem
    # anderen — nie über force umgehbar. Ein bereits offener Merge/Rebase
    # (von einem ANDEREN, früheren Aufruf — dieser Aufruf hat noch keinen
    # eigenen gestartet) darf niemals einen neuen Merge-Versuch auslösen,
    # s. Docstring von _conflict_resolution_pending() für den live bewiesenen
    # Schaden ohne diesen Guard.
    if _conflict_resolution_pending(repo_root):
        return MergeResult("repo_busy", detail="ein anderer Merge/Rebase ist bereits offen")

    branch = branch_name(slug)
    if _git(["rev-parse", "--verify", "--quiet", branch], cwd=repo_root).returncode != 0:
        merge_quarantine.clear(repo_root, branch)  # Branch weg → verwaiste Zeile aufräumen
        return MergeResult("error", detail=f"branch {branch} fehlt")

    trunk_sha_before = _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    if not force:
        quarantined = merge_quarantine.get(repo_root, branch)
        if quarantined is not None:
            if quarantined.failures >= merge_quarantine.ESCALATE_AFTER:
                return MergeResult("quarantined", trunk_sha=trunk_sha_before,
                                   detail=f"eskaliert nach {quarantined.failures} Fehlschlägen")
            if quarantined.trunk_sha == trunk_sha_before:
                return MergeResult("quarantined", trunk_sha=trunk_sha_before,
                                   detail="trunk unverändert seit letztem Fehlschlag")

    # PLAN-30 Ebene 4 (G1): NICHT über force umgehbar — anders als die
    # Quarantäne-Vorprüfung oben schützt dieser Guard eine gerade bearbeitete
    # Datei auch dann, wenn ein Mensch die Quarantäne per /sync bewusst
    # übersteuert. Sicher ab hier (nach der Quarantäne-Vorprüfung): ein
    # bereits eskalierter/unveränderter Branch erreicht diesen Dry-Run gar
    # nicht erst, kein Selbstverstärkungs-Risiko durch wiederholte Sweep-
    # Versuche (Ebene 2 begrenzt die echten Versuche bereits auf neue
    # trunk-Stände).
    live = _live_overlap(repo_root, branch, trunk, now=now)
    if live:
        return MergeResult("live_edit", trunk_sha=trunk_sha_before,
                           detail=f"kürzlich/gerade bearbeitet: {', '.join(sorted(live))}")

    # PLAN-21 Befund 8: Merge-back läuft unbeaufsichtigt (Worker-Report oder
    # Synchronizer-Sweep, nie ein Mensch) — bibi-Identität statt der
    # ambienten (bisher fälschlich menschlichen) Git-Config. Derselbe
    # dynamische bibi/<slug>-Name wie der Job-Commit selbst, den dieser
    # Merge nach trunk holt.
    name, email = bot_identity(slug)
    proc = _git(
        ["-c", f"user.name={name}", "-c", f"user.email={email}",
         "merge", "--no-ff", "--no-edit", branch],
        cwd=repo_root,
    )
    head = _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    if proc.returncode == 0:
        merge_quarantine.clear(repo_root, branch)
        out = (proc.stdout + proc.stderr).lower()
        if "already up to date" in out:
            return MergeResult("up_to_date", trunk_sha=head)
        return MergeResult("merged", trunk_sha=head)

    out_raw = (proc.stdout + proc.stderr)
    out = out_raw.lower()
    # Fehlschlag: echter Konflikt (Modus B) — Fehlschlag zählt so oder so;
    # keep_conflict entscheidet nur, ob der Tree für eine Auflösung offen
    # bleibt (interaktiv, /sync) oder sauber abgebrochen wird (unbeaufsichtigt,
    # Sweep/Ebene 1). Dirty-Tree-Verweigerung (Modus A) ist kein Fehlschlag;
    # alles andere ist ein generischer Fehler, zählt aber wie B.
    if (repo_root / ".git" / "MERGE_HEAD").exists():
        merge_quarantine.record_failure(repo_root, branch, trunk_sha=head)
        if not keep_conflict:
            _git(["merge", "--abort"], cwd=repo_root)
        return MergeResult("conflict", trunk_sha=head, detail=out_raw.strip())
    if _DIRTY_REFUSAL_MARKER in out:
        return MergeResult("blocked", trunk_sha=head, detail=out_raw.strip())
    merge_quarantine.record_failure(repo_root, branch, trunk_sha=head)
    return MergeResult("error", trunk_sha=head, detail=out_raw.strip())


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
    branches = unmerged_agent_branches(repo_root=repo_root, trunk=trunk)
    # Verwaiste Quarantäne-Zeilen aufräumen (Branch von außen gemergt/gelöscht,
    # z. B. ein Mensch via /sync) — im selben Lock-Scope wie die Merge-Versuche
    # selbst (Review-Fund PLAN-30, 1. Runde: kein unlocked Read/Write neben den
    # eigentlichen Git-Operationen).
    with (lock if lock is not None else contextlib.nullcontext()):
        merge_quarantine.prune(repo_root, keep_branches=set(branches))
    out: dict[str, str] = {}
    for branch in branches:
        slug = branch.removeprefix("agent/")
        out[branch] = merge_back(repo_root=repo_root, slug=slug, trunk=trunk, lock=lock).status
    return out
