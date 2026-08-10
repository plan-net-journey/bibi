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
                 # | "legacy-job-env-name" (PLAN-32 Stufe 32.0)
                 # | "legacy-node-name" (PLAN-34)
                 # | "credential-drift" (dasselbe Geheimnis an zwei Orten,
                 #   unbemerkt auseinandergelaufen)
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
    ``known_slugs``: von der aufrufenden Seite (``doctor_cmd._known_slugs()``)
    bereits auf Slugs eingeschränkt, deren Job noch etwas mit seinem Worktree
    vorhat — ein deaktivierter oder terminal-ohne-künftigen-Fire-Slug zählt
    dort nicht mehr als bekannt, landet also hier korrekt als Waise."""
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


def check_legacy_job_env_names(cfg_and_env: dict[str, str]) -> list[Finding]:
    """PLAN-32 Stufe 32.0: ``ANTHROPIC_API_KEY``/``CLAUDE_CODE_OAUTH_TOKEN``
    wandern unter das ``BIBI_JOB_ENV_``-Präfix (``worker.py::_exec_config()``,
    Config-Restrukturierung — dieselbe Namenskonvention, die für beliebige
    Job-Credentials schon existiert). Der alte bare Name bleibt als Fallback
    funktional, ist aber veraltet — nur ein Fund, wenn der bare Name gesetzt
    ist UND die präfigierte Form fehlt (sonst wurde schon migriert).
    ``cfg_and_env``: zusammengeführte Sicht aus ``os.environ`` +
    ``~/.config/bibi/env``, dieselbe Präzedenz wie ``_exec_config()``."""
    out: list[Finding] = []
    for legacy in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        prefixed = f"BIBI_JOB_ENV_{legacy}"
        if cfg_and_env.get(legacy) and not cfg_and_env.get(prefixed):
            out.append(Finding(
                "legacy-job-env-name", legacy,
                f"veraltet ohne BIBI_JOB_ENV_-Präfix gesetzt — auf {prefixed} umbenennen"))
    return out


def check_legacy_worker_name(cfg_and_env: dict[str, str]) -> list[Finding]:
    """PLAN-34: ``BIBI_WORKER_NAME`` wandert zu ``BIBI_NODE_NAME`` — der alte
    Name war irreführend (galt schon immer für jeden ``--connect``-Knoten, nicht
    nur die Worker-Rolle) und passte nicht zu ``BIBI_NODE_ID``. Alter Name
    bleibt als Fallback funktional (``daemon_cmd.py::_resolve_worker_name()``),
    ist aber veraltet — nur ein Fund, wenn er gesetzt ist UND der neue Name
    fehlt (sonst wurde schon migriert). ``cfg_and_env``: zusammengeführte Sicht
    aus ``os.environ`` + ``~/.config/bibi/env``, dieselbe Präzedenz wie
    ``check_legacy_job_env_names()``."""
    if cfg_and_env.get("BIBI_WORKER_NAME") and not cfg_and_env.get("BIBI_NODE_NAME"):
        return [Finding(
            "legacy-node-name", "BIBI_WORKER_NAME",
            "veraltet — auf BIBI_NODE_NAME umbenennen (gilt für jeden --connect-Knoten, "
            "nicht nur Worker-Rolle)")]
    return []


def check_git_identity(*, name: str | None, email: str | None) -> list[Finding]:
    """Fehlende git-Identität auf diesem Knoten (m.rau/bibi#18).

    **Warum das ein Befund ist und keine Kleinigkeit:** fehlt ``user.name`` oder
    ``user.email``, rät git eine Identität aus Benutzer- und Hostnamen zusammen
    und schreibt sie mit einer Warnung in den Commit — die im Log eines
    Hintergrund-Jobs niemand liest. In einem Team-Repo tragen Beiträge dann
    ``mmu@sarasate`` statt eines Namens, und wer das später gerade zieht,
    schreibt Historie um.

    Aufgekommen beim Onboarding des ersten fremden Knotens: dort stand die
    Identität am Ende korrekt, aber **von Hand gesetzt** — weder ``/bibi-setup``
    noch ``bibi-ctrl init`` kümmern sich darum. Für den nächsten fremden Client
    wäre die Lücke also wieder offen gewesen; dieser Check macht sie sichtbar,
    statt sie zu dokumentieren.

    Der Synchronizer ist davon nicht betroffen — er committet mit einer festen
    Bot-Identität (``bibi/sync``). Es geht um die Commits des **Menschen** und
    um ``bibi-ctrl save``, das die ambiente Identität verwendet.
    """
    missing = [k for k, v in (("user.name", name), ("user.email", email))
               if not (v or "").strip()]
    if not missing:
        return []
    return [Finding(
        "git-identity", ", ".join(missing),
        "nicht gesetzt — git rät sonst Benutzer@Host und schreibt das in jeden "
        "Commit. Setzen mit: git config user.name \"…\" && git config user.email \"…\"")]


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


#: Wendungen, die eine Erwähnung als **Rückblick** ausweisen (m.rau/bibi#92).
#:
#: Deutsch und Englisch nebeneinander, weil beide vorkommen: die Skills unter
#: ``skills/`` sind englisch, Vault und ``CLAUDE.md`` deutsch. Die Liste ist
#: bewusst kurz — jeder Eintrag macht die Prüfung stiller, und eine Prüfung,
#: die zu viel verschweigt, ist so wertlos wie eine, die zu viel meldet.
#:
#: Geeicht an den zwei echten Stellen im ``bibi-setup``-Skill, die seit #58
#: vorbildlich formuliert sind: *„this skill **explained** an environment
#: variable"* und *„which **no longer exists**"*.
RETIRED_LOOKBACK = (
    "no longer", "removed", "replaced", "deprecated", "used to", "explained",
    "formerly", "until ", "was ", "were ",
    "nicht mehr", "entfällt", "entfallen", "abgeschafft", "früher", "frueher",
    "veraltet", "ersetzt", "hieß", "hiess", "bis zum", "bis ", "damals",
)


def check_retired_terms(path: str, text: str, terms) -> list[Finding]:
    """Abgeschaffte Bezeichner in **aktiver** Doku (m.rau/bibi#92).

    ``terms`` ist die gepflegte Liste aus ``[[tool.bibi.retired_terms]]``: je
    Eintrag ``term``, optional ``since`` und ``replacement``.

    **Warum eine gepflegte Liste und keine generische Erkennung:** „veralteter
    Begriff" ist maschinell nicht bestimmbar, und der Versuch erzeugte nur
    Rauschen. Die Liste ist zugleich der eigentliche Ertrag — **eine
    Aufstellung dessen, was abgeschafft wurde und wie es heute heißt, gibt es
    sonst nirgends.**

    **Ein Treffer entfällt, wenn die Zeile ihn als Rückblick ausweist** — durch
    eine Wendung aus :data:`RETIRED_LOOKBACK` oder dadurch, dass sie den
    Nachfolger gleich mitnennt. Ohne diese Regel meldete die Prüfung ihre
    eigenen Korrekturen: der ``bibi-setup``-Skill nennt ``BIBI_CONFIG_PATH``
    zweimal, beide Male richtig.

    Zeilenweise, nicht dokumentweit — der Rückblick muss **neben** dem Namen
    stehen. Ein Dokument, das auf Seite 1 „X gibt es nicht mehr" schreibt und
    auf Seite 4 zu X rät, ist genau der Fall, den dieses Ticket meint.
    """
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        klein = line.lower()
        rueckblick = any(w in klein for w in RETIRED_LOOKBACK)
        for eintrag in terms:
            begriff = (eintrag.get("term") or "").strip()
            if not begriff or begriff not in line:
                continue
            nachfolger = (eintrag.get("replacement") or "").strip()
            if rueckblick or (nachfolger and nachfolger in line):
                continue
            seit = (eintrag.get("since") or "").strip()
            detail = f"{begriff} ist abgeschafft"
            detail += f" (seit {seit})" if seit else ""
            detail += f" — heute: {nachfolger}" if nachfolger else ""
            detail += ("; ist die Erwähnung ein bewusster Rückblick, benenne "
                       "sie in derselben Zeile als solchen")
            out.append(Finding("retired-term", f"{path}:{i}", detail))
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


@dataclass(frozen=True)
class CredentialPair:
    """Ein Geheimnis, das an zwei Orten gehalten wird — als **Fingerprint**,
    nie als Wert. ``None`` heißt „an diesem Ort nicht vorhanden“."""
    label: str
    keychain_fp: str | None
    env_fp: str | None


def check_credential_drift(pairs) -> list[Finding]:
    """Dasselbe Credential an zwei Orten, unbemerkt auseinandergelaufen.

    Hintergrund (live gefunden 2026-07-31, Case 20260729.bibi4DesignStudie):
    der Gitea-Token liegt sowohl im macOS-Keychain (für interaktive Aufrufe am
    Mac) als auch als ``BIBI_JOB_ENV_*`` im Verteilweg (für Jobs auf den
    Knoten) — so trennt es das Team-Repo bewusst, weil der Verteilweg
    ungescoped ist. Beide Kopien werden aber von niemandem verglichen: der
    Token wurde neu erzeugt, der Verteilweg nachgezogen, der Keychain nicht.
    Aufgefallen ist es erst Wochen später bei einem interaktiven API-Aufruf,
    weil die Jobs unbeirrt weiterliefen.

    Nicht die Doppelhaltung ist der Fehler — die hat ihren Grund —, sondern
    dass ihr Auseinanderlaufen unsichtbar bleibt. Genau das meldet dieser
    Check.

    Gemeldet wird **nur**, wenn beide Orte einen Wert führen und die
    Fingerprints differieren. Fehlt einer, ist das kein Fund: nicht jeder
    Knoten pflegt jeden Ort (Linux hat keinen Keychain), und ein fehlender
    Eintrag ist eine Entscheidung, kein Widerspruch.
    """
    out: list[Finding] = []
    for p in pairs:
        if not p.keychain_fp or not p.env_fp:
            continue
        if p.keychain_fp != p.env_fp:
            out.append(Finding(
                "credential-drift", p.label,
                f"Keychain ({p.keychain_fp}) und Verteilweg ({p.env_fp}) tragen "
                "verschiedene Werte — eine Kopie ist veraltet; die interaktive "
                "Nutzung schlägt fehl, während Jobs weiterlaufen"))
    return out
