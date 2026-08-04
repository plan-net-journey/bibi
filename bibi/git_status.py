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
    sync: str            # "synced" | "ahead" | "behind" | "diverged"
    branch: str | None   # None bei detached HEAD
    oid: str | None = None    # voller Commit-Hash (# branch.oid), None wenn unparsbar
    ahead: int = 0
    behind: int = 0


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
    oid: str | None = None
    ahead = behind = 0
    dirty = False
    for line in proc.stdout.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split()[-1]
        elif line.startswith("# branch.head "):
            head = line.split()[-1]
            branch = None if head == "(detached)" else head
        elif line.startswith("# branch.ab "):
            a, b = line.split()[-2:]
            ahead, behind = int(a.lstrip("+")), int(b.lstrip("-"))
        elif line and not line.startswith("#"):
            dirty = True

    tree = "modified" if dirty else "clean"
    if ahead and behind:
        # Bibi4-Iteration (Batch 7 Stufe 3, User-Fund: "ich verstehe die
        # Bedeutung von conflict und sync: !conflict nicht") — "conflict"
        # war hier eine unglückliche Namenswahl für "gleichzeitig vor UND
        # hinter dem Remote", kein echter, blockierender Merge-Konflikt (den
        # gibt es an anderer Stelle bereits korrekt benannt: der per-Datei-
        # Chip in local_files_status() unten, und der daemon-globale
        # sync_conflict-Flag in state.py/.state.md). "diverged" trifft den
        # tatsächlichen Git-Graph-Zustand, ohne den echten Fall zu verwässern.
        sync = "diverged"
    elif ahead:
        sync = "ahead"
    elif behind:
        sync = "behind"
    else:
        sync = "synced"

    return WorkingTreeStatus(tree=tree, sync=sync, branch=branch,
                             oid=oid, ahead=ahead, behind=behind)


def local_files_status(root: Path | None, paths: list[str]) -> dict[str, str]:
    """Git-Status je Pfad — "new" (neu/untracked) | "modified" (getrackt,
    geändert) | "conflict" (Merge-Konflikt) | "clean" (getrackt, unverändert)
    — für die lokal entdeckten Job-MDs (PLAN-21 Befund 10, User-Fund: "die
    Jobs im Repository plus ihr git Status (neu, geändert, etc.) anzeigen";
    gelöschte MDs will der User **nicht** als eigenen Status sehen — sie
    verschwinden von selbst, da ``discovery.discover()`` (Dateisystem-Scan)
    sie ohnehin nicht mehr findet). "conflict" (Bibi4-Iteration, User-Fund:
    "sind sie lokal modifiziert, konfliktär, fehlen?") war zuvor nicht von
    "modified" unterschieden — Porcelain v2 markiert Merge-Konflikte bereits
    eindeutig mit einer eigenen ``u ``-Zeile (unmerged), nur bisher in
    denselben Topf wie ``1 `` (ordinary changed) geworfen.

    Ein einziger ``git status``-Aufruf für alle Pfade statt einem je Datei.
    ``paths`` sind repo-root-relative Pfade (POSIX-Separator, wie
    ``Path.relative_to().as_posix()`` liefert). ``--no-renames``, damit ein
    Rename immer als zwei einfache Zeilen (alter Pfad "gelöscht" — hier
    irrelevant, neuer Pfad "new") statt eines schwerer zu parsenden
    Rename-Eintrags erscheint.

    User-Fund 2026-07-20 ("Runner 1"/"Runner 5" trotz echter Änderung als
    "clean" gemeldet): ``1 ``-Zeilen (ordinary changed) haben laut Porcelain-
    v2-Format sieben feste Felder vor dem Pfad (``XY sub mH mI mW hH hI``),
    ``u ``-Zeilen (unmerged) zehn (``XY sub m1 m2 m3 mW h1 h2 h3``) — ein
    unbegrenzter ``.split(" ")[-1]`` zerschnitt bei jedem Pfad MIT Leerzeichen
    (jede ``Runner N.md`` in diesem Vault) auch den Pfad selbst und lieferte
    nur dessen letztes Wort als Dict-Key, der nie auf den vollen ``repo_path``
    aus ``paths`` matchte — Fallback auf "clean". ``maxsplit`` begrenzt den
    Split auf die Präfix-Felder, ein Leerzeichen im Pfad bleibt im letzten
    Element erhalten — analog zum ``? ``-Zweig, der mit ``line[2:]`` von
    Anfang an korrekt war."""
    dirty = dirty_files(root)
    # "deleted" gibt es hier bewusst nicht: eine gelöschte Job-MD verschwindet
    # von selbst aus `paths` (``discovery.discover()`` findet sie nicht mehr),
    # und wo sie doch noch mitkommt, ist "modified" die alte, bewährte Antwort.
    return {p: ("modified" if dirty.get(p) == "deleted" else dirty.get(p, "clean"))
            for p in paths}


def dirty_files(root: Path | None = None) -> dict[str, str]:
    """Alle offenen Änderungen im Arbeitsbaum: Pfad → ``new`` | ``modified`` |
    ``deleted`` | ``conflict``. Ein sauberer Baum liefert ein leeres Dict.

    Die Gegenfrage zu :func:`local_files_status`, die wissen will, wie es um
    **bestimmte** Pfade steht: hier ist die Menge selbst die Antwort. Der Feed
    braucht das für den ``UNCOMMITTED``-Block (m.rau/bibi#133) — was noch nicht
    committet ist, steht in keinem ``git log`` und wäre sonst der einzige
    Zustand des Vaults, den der Feed nicht kennt.

    ``deleted`` ist der Unterschied zur älteren Funktion: dort war das
    ausdrücklich kein eigener Zustand, hier ist das Verschwinden die Nachricht.
    Porcelain v2 trägt es im ``XY``-Feld der ``1 ``-Zeile — ``D`` an einer der
    beiden Stellen (gestaged oder im Arbeitsbaum).

    Ein einziger ``git status``-Aufruf, ``--no-renames``: ein Rename erscheint
    damit als gelöscht + neu, was hier die ehrlichere Auskunft ist.
    """
    proc = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain=v2",
         "--untracked-files=all", "--no-renames"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    dirty: dict[str, str] = {}
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if line.startswith("? "):
                dirty[line[2:]] = "new"
            elif line.startswith("u "):
                dirty[line.split(" ", 10)[-1]] = "conflict"
            elif line.startswith("1 "):
                felder = line.split(" ", 8)
                dirty[felder[-1]] = "deleted" if "D" in felder[1] else "modified"
    return dirty
