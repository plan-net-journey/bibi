"""Feed-Datenquelle: Git-Historie → **Einheiten**, je mit letztem Änderungs-
zeitpunkt, Umfang und Urheber (FE-Spezifikation §3).

Eine Einheit ist der Ordner, in dem gearbeitet wird. Für alles unterhalb von
``vault/`` gilt der Reihe nach:

* liegt der Pfad in einem **Case**, ist der Case die Einheit — auch wenn die
  Datei tief darin liegt (``attach/``, ``collectors/``) und auch, wenn der Case
  in einem anderen Case oder in einem Jahres-Archivordner steckt;
* sonst ist es der Ordner unterhalb der Ablage-Ebene (``memo/Release``), auf
  zwei Ebenen gedeckelt;
* liegt die Datei direkt in einer Ablage-Ebene oder in der Vault-Wurzel, ist sie
  ihre eigene Zeile. Ein Top-Level-Ordner ist eine Ablage, keine Arbeitseinheit
  — eine Sammelzeile ``memo`` verstecke genau das, was die Zeile zeigen soll.

Nur Markdown, nur unterhalb von ``vault/``. Alles andere hat keine Einheit und
erscheint nicht.

Ein einziger ``git log --name-status``-Aufruf trägt die ganze Aggregation; die
Case-Erkennung kostet einen Verzeichnis-Scan.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_RS = "\x1e"  # Record Separator — trennt Commits im geparsten Log
_FS = "\x1f"  # Field Separator — trennt Hash/Autor/Zeitstempel je Commit

#: Ordnertiefe, ab der ein Nicht-Case zusammengefasst wird. `memo/DailyDigest`
#: liegt live teils flach, teils nach Jahr/Monat — ohne Deckel stünde dieselbe
#: Sache auf mehreren Ebenen nebeneinander.
_MAX_FOLDER_DEPTH = 2

#: Präfix, unter dem Jobs committen (`bibi/<slug>`). Der Slug aus der Merge-
#: Message trägt ihn nicht — ohne Abschneiden stünde derselbe Urheber doppelt.
_JOB_AUTHOR_PREFIX = "bibi/"


@dataclass(frozen=True, slots=True)
class CommitInfo:
    sha: str
    author: str
    epoch: float
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeedEntry:
    unit: str                  # Case-Ordnername, Ordnerpfad oder Dateipfad
    last_changed: float
    last_commit_sha: str       # Commit, der last_changed erzeugt hat (Link zum Server)
    authors: frozenset[str]    # Job-Slug bei Agent-Herkunft, sonst git-Autor
    changes: int               # geänderte Dateien in dieser Einheit im Fenster


def _run_git(root: Path, args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=root,
                          capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else ""


def _run_log(root: Path, args: list[str]) -> str:
    return _run_git(root, ["log", *args])


def _since_args(since_days: int | None) -> list[str]:
    return [f"--since={since_days} days ago"] if since_days is not None else []


def _parse_log(output: str) -> list[CommitInfo]:
    commits: list[CommitInfo] = []
    for block in output.split(_RS):
        if not block.strip():
            continue
        lines = block.splitlines()
        sha, author, epoch_s = lines[0].split(_FS)
        paths: list[str] = []
        for line in lines[1:]:
            if not line.strip():
                continue
            paths.append(line.split("\t")[-1])  # bei R/C: neuer Pfad (letztes Feld)
        commits.append(CommitInfo(sha=sha, author=author, epoch=float(epoch_s),
                                  paths=tuple(paths)))
    return commits


def collect_commits(root: Path, *, since_days: int | None = None) -> list[CommitInfo]:
    """Alle Commits (+ geänderte Pfade) im Zeitfenster, ein einziger Git-Aufruf."""
    fmt = f"--pretty=format:{_RS}%H{_FS}%an{_FS}%at"
    out = _run_log(root, ["--name-status", fmt, *_since_args(since_days)])
    return _parse_log(out)


#: git-generierte Default-Merge-Message bei ``merge --no-ff --no-edit agent/<slug>``
#: (``mergeback.merge_back()`` — ``--no-edit`` ist hartcodiert, nie konfigurierbar).
_AGENT_MERGE_PREFIX = "Merge branch 'agent/"


def agent_slugs(root: Path, *, since_days: int | None = None) -> dict[str, str]:
    """Commit-Hash → Slug des Jobs, der ihn geschrieben hat.

    Erkannt wird die Herkunft an der Merge-Message, nicht an Branch-Containment.
    Zwei naheliegende Alternativen sind live gegen die echte bibi-notes-Historie
    widerlegt worden und dürfen nicht zurückkommen:

    * **First-Parent-Mengendifferenz** klassifiziert jeden Merge als Agent-
      Herkunft, auch gewöhnliche Mehrgeräte-Sync-Merges des Synchronizers.
    * **Branch-Containment** (``rev-list --contains`` gegen lebende
      ``agent/*``-Refs) ist langsam (ein Aufruf je Merge-Commit) und
      falsch-positiv: alte Commits werden irgendwann Vorfahre praktisch jedes
      späteren Branches, wodurch alle acht echten Sync-Merges dieses Repos als
      Agent-Herkunft galten. Containment prüft Erreichbarkeit, nicht „hat
      DIESER Merge DIESEN Branch hereingeholt".

    Je Merge ein eigener ``rev-list``-Aufruf. **Nicht** gebündelt über alle
    Bereiche: git behandelt mehrere Bereiche als EINE globale Menge, wodurch
    der ``p1`` eines späteren Merges die Commits eines früheren herausrechnet
    (live: 185 erwartete Treffer schrumpften auf 2).
    """
    fmt = f"--pretty=format:{_RS}%H{_FS}%P{_FS}%s"
    merges_out = _run_log(root, ["--merges", fmt, *_since_args(since_days)])

    by_sha: dict[str, str] = {}
    for block in merges_out.split(_RS):
        block = block.strip()
        if not block:
            continue
        _sha, parents_s, subject = block.split(_FS, 2)
        parents = parents_s.split()
        if len(parents) == 2 and subject.startswith(_AGENT_MERGE_PREFIX):
            slug = subject.split("'")[1].removeprefix("agent/")
            rev_range = f"{parents[0]}..{parents[1]}"
            for sha in _run_git(root, ["rev-list", rev_range]).split():
                by_sha[sha] = slug
    return by_sha


def _has_slug_frontmatter(readme: Path) -> bool:
    """Trägt die README ein ``slug:`` im Frontmatter? Das ist die Definition
    eines Case, die auch ``bibi-ctrl open`` benutzt — kein Namensmuster."""
    try:
        with readme.open(encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return False
            for line in fh:
                if line.strip() == "---":
                    return False
                if line.startswith("slug:"):
                    return True
    except OSError:
        return False
    return False


def discover_cases(root: Path, *, case_dir_name: str = "case") -> set[str]:
    """Namen aller Case-Ordner im Vault.

    Der **Name** statt des Pfades, weil ein verschobener Case sonst zweimal
    erschiene: git führt seine Historie unter dem alten Pfad weiter. Live
    steht ``20260621.Bibi4-870bd9db`` in drei Pfadvarianten und ist ein Case.

    Sammelordner fallen dadurch heraus, ohne dass man sie kennen muss: ein
    Jahresarchiv (``case/2026``) und ein Gruppenordner mit Unter-Cases tragen
    keine eigene README mit ``slug:``.
    """
    base = Path(root) / "vault" / case_dir_name
    if not base.is_dir():
        return set()
    return {readme.parent.name for readme in base.rglob("README.md")
            if _has_slug_frontmatter(readme)}


def unit_for_path(path: str, *, cases: set[str]) -> str | None:
    """Ein geänderter Pfad → seine Einheit, oder ``None``.

    Von innen nach außen gesucht, damit ein verschachtelter Case seinen
    Container schlägt: ``case/Bibi4/Bibi5/x.md`` gehört zu Bibi5, nicht zu
    Bibi4 — sonst verschwindet die Arbeit unter dem Namen des äußeren Ordners.
    """
    if not path.startswith("vault/") or not path.endswith(".md"):
        return None
    parts = path.split("/")[1:]
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] in cases:
            return parts[i]
    folders = parts[:-1]
    if len(folders) < _MAX_FOLDER_DEPTH:
        return "/".join(parts)
    return "/".join(folders[:_MAX_FOLDER_DEPTH])


def group_entries(
    commits: list[CommitInfo], slugs_by_sha: dict[str, str], *, cases: set[str],
) -> list[FeedEntry]:
    """Reine Gruppierung schon gesammelter Commits, neueste Einheit zuerst."""
    buckets: dict[str, dict] = {}
    for c in commits:
        who = (slugs_by_sha.get(c.sha) or c.author).removeprefix(_JOB_AUTHOR_PREFIX)
        for path in c.paths:
            unit = unit_for_path(path, cases=cases)
            if unit is None:
                continue
            b = buckets.setdefault(
                unit, {"last": c.epoch, "sha": c.sha, "authors": set(), "changes": 0})
            if c.epoch > b["last"]:
                b["last"], b["sha"] = c.epoch, c.sha
            b["authors"].add(who)
            b["changes"] += 1

    entries = [
        FeedEntry(unit=unit, last_changed=b["last"], last_commit_sha=b["sha"],
                  authors=frozenset(b["authors"]), changes=b["changes"])
        for unit, b in buckets.items()
    ]
    return sorted(entries, key=lambda e: e.last_changed, reverse=True)


def aggregate_feed(
    root: Path, *, since_days: int | None = None, case_dir_name: str = "case",
) -> list[FeedEntry]:
    """Einheiten im Zeitfenster, neueste zuerst — der Kern der Feed-Liste."""
    commits = collect_commits(root, since_days=since_days)
    slugs = agent_slugs(root, since_days=since_days)
    cases = discover_cases(root, case_dir_name=case_dir_name)
    return group_entries(commits, slugs, cases=cases)


def remote_commit_base_url(root: Path) -> str | None:
    """Gitea-Basis-URL für Commit-Links, aus dem ``origin``-Remote abgeleitet;
    der Aufrufer hängt ``/commit/<sha>`` an. ``None`` ohne ``origin``."""
    url = _run_git(root, ["remote", "get-url", "origin"]).strip()
    if not url:
        return None
    return url[: -len(".git")] if url.endswith(".git") else url
