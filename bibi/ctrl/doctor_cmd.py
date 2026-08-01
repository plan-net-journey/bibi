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
``localhost``, s. ``config.public_host()``), (k) veraltete bare
``ANTHROPIC_API_KEY``/``CLAUDE_CODE_OAUTH_TOKEN``-Namen ohne ``BIBI_JOB_ENV_``-
Präfix (PLAN-32 Stufe 32.0 — Fallback bleibt funktional, aber veraltet),
(l) Credential-Drift: dasselbe Geheimnis in Keychain UND Verteilweg mit
verschiedenen Werten (konfiguriert über ``[[tool.bibi.credential_checks]]``).
Exit 1 bei Befunden (pre-commit/CI-tauglich), sonst 0. Reine Prüflogik:
``hygiene``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess

from bibi import config, git_ops, hygiene, repo
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


def _fingerprint(value: str | None) -> str | None:
    """Kurzer, stabiler Fingerabdruck — nie der Wert selbst.

    ``doctor`` läuft in Terminals, CI-Logs und pre-commit-Hooks; ein
    Credential darf dort unter keinen Umständen landen. Zwölf Hex-Zeichen
    SHA-256 reichen, um „gleich oder nicht“ zu entscheiden, und erlauben
    keinen Rückschluss auf den Wert."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _keychain_value(service: str, account: str) -> str | None:
    """Wert aus dem macOS-Keychain, oder ``None``.

    Auf Nicht-macOS gibt es ``security`` nicht — dann existiert der Ort
    schlicht nicht und es ist kein Fund (s. ``check_credential_drift``).
    Ein nicht gefundener Eintrag (Exit ≠ 0) ist ebenfalls kein Fehler."""
    if not shutil.which("security"):
        return None
    r = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
        capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _credential_pairs(cfg_and_env: dict[str, str]) -> list[hygiene.CredentialPair]:
    """Baut die Vergleichspaare aus ``[[tool.bibi.credential_checks]]``.

    Die Verteilweg-Seite akzeptiert beide Schreibweisen (mit und ohne
    ``BIBI_JOB_ENV_``-Präfix), damit dieselbe Konfiguration vor und nach der
    PLAN-32-Umbenennung trägt — dieselbe Nachsicht wie ``token_present``."""
    pairs: list[hygiene.CredentialPair] = []
    for spec in repo.credential_checks():
        env_name = spec["env"]
        env_value = (cfg_and_env.get(f"BIBI_JOB_ENV_{env_name}")
                     or cfg_and_env.get(env_name))
        kc_value = _keychain_value(spec["keychain_service"], spec["keychain_account"])
        pairs.append(hygiene.CredentialPair(
            label=env_name,
            keychain_fp=_fingerprint(kc_value),
            env_fp=_fingerprint(env_value)))
    return pairs


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
    has_apps = any(r.spec.app_port for r in discovered.found.values())
    public_host_set = bool(
        os.environ.get("BIBI_PUBLIC_HOST", "").strip()
        or config.read_env().get("BIBI_PUBLIC_HOST", "").strip()
    )
    # PLAN-32 Stufe 32.0: dieselbe Verteilt<Config<Env-Präzedenz wie
    # worker.py::_exec_config() — vorher las token_present nur os.environ,
    # nie ~/.config/bibi/env, und kannte nie die BIBI_JOB_ENV_-präfigierten
    # Varianten (Doctor-ClaudeAuth-Bug, Case 20260621.Bibi4-870bd9db,
    # live gefunden 2026-07-24: ein korrekt in ~/.config/bibi/env gesetztes
    # BIBI_JOB_ENV_CLAUDE_CODE_OAUTH_TOKEN blieb für diesen Check unsichtbar,
    # obwohl der echte Job-Exec-Pfad es längst korrekt nutzte).
    cfg_and_env = {**config.read_distributed_env(), **config.read_env(), **os.environ}
    token_present = any(
        cfg_and_env.get(key) or cfg_and_env.get(f"BIBI_JOB_ENV_{key}")
        for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
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
        + hygiene.check_legacy_job_env_names(cfg_and_env)
        + hygiene.check_legacy_worker_name(cfg_and_env)
        + hygiene.check_credential_drift(_credential_pairs(cfg_and_env))
        # m.rau/bibi#18: fehlende git-Identität. Aus dem Repo gelesen, nicht aus
        # der Knoten-Config — git löst sie selbst über lokal > global > System
        # auf, und genau diese Auflösung soll der Befund abbilden.
        + hygiene.check_git_identity(name=git_ops.git_user_name(),
                                     email=git_ops.git_user_email())
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
