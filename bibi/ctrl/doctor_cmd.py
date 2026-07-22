"""``bibi-ctrl doctor`` — Repo-/Vault-Hygiene-Check (PLAN-5 §5.2; PLAN-13 §13.3;
PLAN-15).

Meldet (a) fehlendes git-lfs, (b) große, nicht-LFS-getrackte Dateien (würden die
History aufblähen, §3.5), (c) committete Sammeldaten unter ``vault/.../data/``,
(d) fehlende ``vault/CONVENTIONS.md`` (Repo-Invariant jedes bibi-team-Repos),
(e) verwaiste Job-Worktrees (``data/worktrees/<slug>/`` ohne ``jobs``-Zeile),
(f) Schedule-MDs, die der Parser nicht lesen konnte, (g) bloße
``<Platzhalter>``-Tags außerhalb Code/Backticks (Obsidian rendert sie falsch),
(h) hart umgebrochene Absätze (CONVENTIONS.md § Markdown style), (i) fehlender
Claude-Auth-Token trotz vorhandener ``claude:``-Jobs, (j) fehlendes
``BIBI_PUBLIC_HOST`` trotz vorhandener App-Jobs (sonst zeigen App-Links nur
``localhost``, s. ``config.public_host()``). Exit 1 bei Befunden
(pre-commit/CI-tauglich), sonst 0. Reine Prüflogik: ``hygiene``.
"""

from __future__ import annotations

import argparse
import os
import subprocess

from bibi import config, hygiene, repo
from bibi.daemon import job_db
from bibi.schedule import discovery
from bibi.schedule.models import is_claude_payload


def _tracked_files(root) -> list[str]:
    r = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                       capture_output=True, text=True, check=False)
    return [p for p in r.stdout.split("\0") if p]


def _lfs_flags(root, paths: list[str]) -> dict[str, bool]:
    """Pro Pfad: ist das ``filter``-Attribut == ``lfs``? (batched ``git check-attr``)."""
    if not paths:
        return {}
    r = subprocess.run(["git", "check-attr", "--stdin", "-z", "filter"], cwd=root,
                       input="\0".join(paths) + "\0", capture_output=True, text=True, check=False)
    toks = r.stdout.split("\0")
    flags: dict[str, bool] = {}
    # Ausgabe in Tripeln: path, "filter", value
    for i in range(0, len(toks) - 2, 3):
        flags[toks[i]] = (toks[i + 2] == "lfs")
    return flags


def _worktree_slugs(root) -> list[str]:
    d = root / "data" / "worktrees"
    if not d.is_dir():
        return []
    return [p.name for p in d.iterdir() if p.is_dir()]


def _known_slugs(root) -> set[str]:
    db_path = job_db.db_path()
    if not db_path.exists():
        return set()
    conn = job_db.connect(db_path)
    try:
        return job_db.active_worktree_slugs(conn)
    finally:
        conn.close()


def _markdown_style_findings(vault_root) -> list[hygiene.Finding]:
    """PLAN-15: jede ``.md`` unter ``vault/`` einlesen (ganzer Vault, nicht nur
    ``case/`` — die CONVENTIONS.md-Regel gilt fürs gesamte Vault), beide neuen
    Checks aufrufen. Eine kaputte/nicht lesbare Datei darf den Scan nicht
    kippen (defensiv, analog ``discovery.discover()``)."""
    out: list[hygiene.Finding] = []
    for p in discovery.walk(vault_root):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = p.relative_to(vault_root).as_posix()
        out += hygiene.check_html_placeholder_tags(rel, text)
        out += hygiene.check_markdown_hardwrap(rel, text)
    return out


def run(args: argparse.Namespace) -> int:
    root = repo.root()
    paths = _tracked_files(root)
    lfs = _lfs_flags(root, paths)
    files = []
    for p in paths:
        try:
            size = (root / p).stat().st_size
        except OSError:
            size = 0
        files.append((p, size, lfs.get(p, False)))

    discovered = discovery.discover(repo.case_dir())
    has_claude_jobs = any(
        is_claude_payload(r.spec.payload) for r in discovered.found.values()
    )
    token_present = bool(
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    )
    has_apps = any(r.spec.app_port for r in discovered.found.values())
    public_host_set = bool(
        os.environ.get("BIBI_PUBLIC_HOST", "").strip()
        or config.read_env().get("BIBI_PUBLIC_HOST", "").strip()
    )

    findings = (
        hygiene.git_lfs_finding(hygiene.git_lfs_installed())
        + hygiene.conventions_finding(hygiene.CONVENTIONS_PATH in paths)
        + hygiene.check_large_unmanaged(files)
        + hygiene.check_data_committed(paths)
        + hygiene.check_orphan_worktrees(_worktree_slugs(root), _known_slugs(root))
        + hygiene.check_invalid_schedules(discovered.errors)
        + _markdown_style_findings(repo.vault())
        + hygiene.check_missing_claude_auth(
            has_claude_jobs=has_claude_jobs, token_present=token_present)
        + hygiene.check_missing_public_host(
            has_apps=has_apps, public_host_set=public_host_set)
    )
    if not findings:
        print("doctor: keine Hygiene-Probleme ✓")
        return 0
    for f in findings:
        loc = f" {f.path}" if f.path else ""
        print(f"⚠ {f.kind}{loc}: {f.detail}")
    print(f"\n{len(findings)} Befund(e). Binäres → LFS (.gitattributes); "
          "Sammeldaten → gitignorierter data/-Pfad (DESIGN §3.5).")
    return 1


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("doctor", help="Repo-/Vault-Hygiene prüfen (LFS, Blobs, Sammeldaten)")
    p.set_defaults(func=run)
