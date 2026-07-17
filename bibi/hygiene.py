"""Repo-/Vault-Hygiene-Prüfungen (PLAN-5 §5.2) — reine, testbare Logik.

Adressiert die zwei „Zeitbomben" aus DESIGN §3.5:
1. Binäres, das als echter Blob (statt LFS-Pointer) in die History wandert.
2. Wachsende Flat-File-Sammeldaten, die committet werden (unbeschränktes Wachstum).

Plus die operative LFS-Voraussetzung (git-lfs installiert) und den Repo-Invariant,
dass jedes bibi-team-Repo eine ``vault/CONVENTIONS.md`` führt. Die Funktionen sind
rein (keine Subprozesse/IO) — ``bibi-ctrl doctor`` sammelt die Fakten und ruft sie auf.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

LARGE_THRESHOLD = 512 * 1024  # 512 KiB — darüber gehört Binäres in LFS (§3.5)


@dataclass(frozen=True)
class Finding:
    kind: str    # "lfs-missing" | "large-unmanaged" | "data-committed" | "conventions-missing"
                 # | "orphan-worktree" | "invalid-schedule" (PLAN-13 Stufe 13.3)
    path: str    # betroffener Pfad ("" = global)
    detail: str


#: Pflicht-Konventionsdatei jedes bibi-team-Repos (relativ zur Repo-Wurzel).
CONVENTIONS_PATH = "vault/CONVENTIONS.md"


def git_lfs_installed() -> bool:
    """``git-lfs`` auf dem PATH? (Sonst bleiben LFS-Dateien Pointer statt Inhalt.)"""
    return shutil.which("git-lfs") is not None or shutil.which("git_lfs") is not None


def git_lfs_finding(installed: bool) -> list[Finding]:
    if installed:
        return []
    return [Finding("lfs-missing", "",
                    "git-lfs nicht installiert — LFS-Dateien bleiben Pointer statt Inhalt")]


def conventions_finding(present: bool) -> list[Finding]:
    """Repo-Invariant: jedes bibi-team-Repo MUSS ``vault/CONVENTIONS.md`` führen
    (Sprache, Slash-Commands, Vokabular, Top-Level-Ordner, Naming, Frontmatter,
    Idee hinter ``case``/``memo``). Fehlt sie, ist das Repo nicht konform."""
    if present:
        return []
    return [Finding("conventions-missing", CONVENTIONS_PATH,
                    "Pflichtdatei fehlt — jedes bibi-team-Repo MUSS vault/CONVENTIONS.md führen")]


def check_large_unmanaged(files, *, threshold: int = LARGE_THRESHOLD) -> list[Finding]:
    """Große, **nicht** LFS-verwaltete getrackte Dateien → echte History-Blobs (§3.5).

    ``files``: iterable von ``(path, size:int, is_lfs:bool)``.
    """
    out: list[Finding] = []
    for path, size, is_lfs in files:
        if size > threshold and not is_lfs:
            out.append(Finding("large-unmanaged", path,
                               f"{size // 1024} KiB getrackt, nicht über LFS"))
    return out


def check_data_committed(paths) -> list[Finding]:
    """Getrackte Dateien unter einem Daten-Sammelpfad (``vault/.../data/…``) →
    sollten gitignored sein (§3.5: wachsende Rohdaten nicht in die History)."""
    out: list[Finding] = []
    for p in paths:
        if p.startswith("vault/") and "/data/" in p:
            out.append(Finding("data-committed", p,
                               "Sammeldaten gehören unter einen gitignorierten data/-Pfad"))
    return out


def check_orphan_worktrees(worktree_slugs, known_slugs) -> list[Finding]:
    """Job-Worktree-Verzeichnisse (``data/worktrees/<slug>/``) ohne zugehörige
    ``jobs``-Zeile (PLAN-13 Stufe 13.3, job-doctor-Migration bibi3→bibi4).

    Ein Worktree gehört zu genau einem Slug (``worktree.prepare()``,
    ``work_dir/<slug>/``, über Fires hinweg wiederverwendet) — verschwindet die
    Schedule-MD ohne dass das Verzeichnis geräumt wird (z. B. Case gelöscht,
    ohne dass zwischenzeitlich ein Fire lief), bleibt ein toter Checkout
    zurück. ``worktree_slugs``: Verzeichnisnamen unter ``data/worktrees/``.
    ``known_slugs``: jeder Slug, der (aktiv oder deaktiviert) noch eine
    ``jobs``-Zeile hat — nur eine komplett unbekannte Zeile gilt als Waise,
    ein bloß deaktivierter (pausierter) Slug nicht."""
    out: list[Finding] = []
    for slug in sorted(worktree_slugs):
        if slug not in known_slugs:
            out.append(Finding("orphan-worktree", f"data/worktrees/{slug}",
                               "kein zugehöriger Job mehr bekannt — Verzeichnis kann entfernt werden"))
    return out


def check_invalid_schedules(errors) -> list[Finding]:
    """Schedule-MDs, die der Parser nicht lesen konnte (PLAN-13 Stufe 13.3).

    ``errors``: iterable von Objekten mit ``.schedule_ref``/``.error``
    (``bibi.schedule.discovery.DiscoveryResult.errors``, bereits vorhandene
    Logik — hier nur gesammelt/gemeldet, nicht neu geparst)."""
    return [Finding("invalid-schedule", e.schedule_ref, e.error) for e in errors]
