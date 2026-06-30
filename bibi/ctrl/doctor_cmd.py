"""``bibi-ctrl doctor`` — Repo-/Vault-Hygiene-Check (PLAN-5 §5.2).

Meldet (a) fehlendes git-lfs, (b) große, nicht-LFS-getrackte Dateien (würden die
History aufblähen, §3.5), (c) committete Sammeldaten unter ``vault/.../data/``,
(d) fehlende ``vault/CONVENTIONS.md`` (Repo-Invariant jedes bibi-team-Repos).
Exit 1 bei Befunden (pre-commit/CI-tauglich), sonst 0. Reine Prüflogik: ``hygiene``.
"""

from __future__ import annotations

import argparse
import subprocess

from bibi import hygiene, repo


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

    findings = (
        hygiene.git_lfs_finding(hygiene.git_lfs_installed())
        + hygiene.conventions_finding(hygiene.CONVENTIONS_PATH in paths)
        + hygiene.check_large_unmanaged(files)
        + hygiene.check_data_committed(paths)
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
