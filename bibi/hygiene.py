"""Repo-/Vault-Hygiene-Prüfungen (PLAN-5 §5.2) — reine, testbare Logik.

Adressiert die zwei „Zeitbomben" aus DESIGN §3.5:
1. Binäres, das als echter Blob (statt LFS-Pointer) in die History wandert.
2. Wachsende Flat-File-Sammeldaten, die committet werden (unbeschränktes Wachstum).

Plus die operative LFS-Voraussetzung (git-lfs installiert) und den Repo-Invariant,
dass jedes bibi-team-Repo eine ``vault/CONVENTIONS.md`` führt. Die Funktionen sind
rein (keine Subprozesse/IO) — ``bibi-ctrl doctor`` sammelt die Fakten und ruft sie auf.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass

LARGE_THRESHOLD = 512 * 1024  # 512 KiB — darüber gehört Binäres in LFS (§3.5)


@dataclass(frozen=True)
class Finding:
    kind: str    # "lfs-missing" | "large-unmanaged" | "data-committed" | "conventions-missing"
                 # | "orphan-worktree" | "invalid-schedule" (PLAN-13 Stufe 13.3)
                 # | "html-placeholder-tag" | "markdown-hardwrap" (PLAN-15)
                 # | "claude-auth-missing" | "public-host-missing"
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


def check_missing_claude_auth(*, has_claude_jobs: bool, token_present: bool) -> list[Finding]:
    """``claude:``-Jobs brauchen ``CLAUDE_CODE_OAUTH_TOKEN`` oder
    ``ANTHROPIC_API_KEY`` in der Umgebung des Daemon-Prozesses
    (``bibi/wrapper/exec_backend.py::_CONTAINER_ENV``,
    ``bibi/daemon/worker.py``) — ohne beides schlägt jeder ``claude:``-Job
    beim Spawn fehl. Nur ein Fund, wenn das Vault tatsächlich ``claude:``-Jobs
    enthält — kein False Positive für reine Host-/App-Setups ohne
    Claude-Nutzung."""
    if has_claude_jobs and not token_present:
        return [Finding(
            "claude-auth-missing", "",
            "vault/ enthält claude:-Jobs, aber weder CLAUDE_CODE_OAUTH_TOKEN "
            "noch ANTHROPIC_API_KEY ist in der Umgebung gesetzt")]
    return []


def check_missing_public_host(*, has_apps: bool, public_host_set: bool) -> list[Finding]:
    """``BIBI_PUBLIC_HOST`` fehlt trotz App-Jobs im Vault (Bibi4-Iteration,
    User-Fund: ein Client zeigte den Hostnamen seines Schedulers statt seines
    eigenen in App-Links). Ohne explizites ``BIBI_PUBLIC_HOST`` liefert
    ``config.public_host()`` seit dem Wegfall der ``BIBI_SCHEDULER_URL``-
    Heuristik nur noch den ``localhost``-Default — für jeden Knoten, der von
    einem anderen Rechner erreicht wird, falsch. Nur ein Fund, wenn das Vault
    tatsächlich App-Jobs (``app_port`` gesetzt) enthält — kein False Positive
    für Setups ohne Apps, analog ``check_missing_claude_auth``."""
    if has_apps and not public_host_set:
        return [Finding(
            "public-host-missing", "",
            "vault/ enthält App-Jobs (app_port gesetzt), aber BIBI_PUBLIC_HOST "
            "ist nicht gesetzt — App-Links zeigen nur 'localhost'")]
    return []


# ── PLAN-15: Markdown-Hygiene (kaputte Platzhalter-Tags, Hartumbruch) ────────

_TAG_RE = re.compile(r"<([a-zA-Z/][^<>\n]{0,40})>")
_AUTOLINK_SCHEME_RE = re.compile(r"^(https?://|mailto:)")
_AUTOLINK_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*$")
_INLINE_CODE_RE = re.compile(r"``.+?``|`[^`\n]+`")
_NUMBERED_LIST_RE = re.compile(r"^\d+[.)]\s")
_VOID_ELEMENTS = {
    "br", "hr", "img", "input", "meta", "link",
    "area", "base", "col", "embed", "param", "source", "track", "wbr",
}


def check_html_placeholder_tags(path: str, text: str) -> list[Finding]:
    """Bloße ``<Platzhalter>``-artige Winkelklammer-Tags außerhalb Code-Fences/
    Backticks (CONVENTIONS.md § Markdown style, User-Fund 2026-07-18).

    Nicht auf "sieht nach echtem HTML aus" beschränkt — Obsidians permissive
    Inline-HTML-Erkennung behandelt ``<cutoff>`` genauso als offenes,
    nie geschlossenes Tag wie ein ``<script>``-Fragment; ein Platzhalterwort
    ist für den Renderer nicht weniger riskant als "echtes" HTML. Auto-Links
    (``<https://…>``, ``<mailto:…>``, bloße ``<name@host>``-Adressen) sind
    gültiges Markdown, kein Fund. Ebenso kein Fund: self-closing Tags
    (``<path .../>``) und HTML5-Void-Elemente ohne Slash (``<br>``, ``<img
    …>``, …) — die sind mechanisch bereits vollständig, kein "geöffnet und
    nie geschlossen". Ein per Slash geschlossenes Tag (``</svg>``) bleibt
    dagegen ein Fund: ob irgendwo im Dokument ein passendes öffnendes Tag
    existiert, prüft dieser Check nicht (out of scope). Bereits per Backtick
    escapte Stellen (die empfohlene Lösung) werden vor der Prüfung entfernt,
    damit ein korrigierter Platzhalter nicht erneut anschlägt."""
    out: list[Finding] = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or line.startswith("    ") or line.startswith("\t"):
            continue
        scrubbed = _INLINE_CODE_RE.sub("", line)
        for m in _TAG_RE.finditer(scrubbed):
            inner = m.group(1)
            if _AUTOLINK_SCHEME_RE.match(inner) or _AUTOLINK_EMAIL_RE.match(inner):
                continue
            if inner.rstrip().endswith("/"):
                continue
            tag_name = inner.split()[0].lstrip("/").lower() if inner.split() else ""
            if not inner.startswith("/") and tag_name in _VOID_ELEMENTS:
                continue
            out.append(Finding(
                "html-placeholder-tag", f"{path}:{i}",
                f"<{inner}> — Obsidian interpretiert das als offenes, nie "
                f"geschlossenes HTML-Tag; in Backticks setzen (`<{inner}>`) "
                "oder in einen Code-Block"))
    return out


def _is_structured_line(line: str, stripped: str) -> bool:
    return bool(
        stripped
        and (stripped[0] in "-*+>|"
             or _NUMBERED_LIST_RE.match(stripped)
             or stripped.startswith("#")
             or line.startswith("    ")
             or line.startswith("\t"))
    )


def check_markdown_hardwrap(path: str, text: str) -> list[Finding]:
    """Über mehrere physische Zeilen hart umgebrochene Absätze (CONVENTIONS.md
    § Markdown style: eine physische Zeile pro Absatz, kein 80-Spalten-Umbruch).

    Ein Fund pro zusammenhängendem Absatz (nicht pro Fortsetzungszeile) —
    lesbarer in der ``doctor``-Ausgabe, ein 5-zeiliger Absatz erzeugt eine
    Meldung, keine vier. Ausnahmen (legitim mehrzeilig): Listen, Tabellen,
    Blockquotes, eingerückter/eingezäunter Code, Überschriften, Frontmatter."""
    out: list[Finding] = []
    lines = text.splitlines()
    in_fence = False
    in_frontmatter = False
    run_start: int | None = None
    run_len = 0

    def flush() -> None:
        nonlocal run_start, run_len
        if run_start is not None and run_len >= 2:
            end = run_start + run_len - 1
            out.append(Finding(
                "markdown-hardwrap", f"{path}:{run_start}-{end}",
                f"Absatz über {run_len} Zeilen hart umgebrochen — "
                "CONVENTIONS.md verlangt eine physische Zeile pro Absatz"))
        run_start = None
        run_len = 0

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if i == 1 and stripped == "---":
            in_frontmatter = True
            flush()
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            flush()
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence or not stripped:
            flush()
            continue
        if _is_structured_line(line, stripped):
            flush()
            continue
        if run_start is None:
            run_start = i
        run_len += 1
    flush()
    return out
