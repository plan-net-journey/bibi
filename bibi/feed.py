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

import re
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

    * **First-Parent-Mengendifferenz allein** klassifiziert jeden Merge als
      Agent-Herkunft, auch gewöhnliche Mehrgeräte-Sync-Merges des
      Synchronizers. Sie taugt als *Abbruchkante* der Traversierung (unten),
      nicht als Erkennungsmerkmal.
    * **Branch-Containment** (``rev-list --contains`` gegen lebende
      ``agent/*``-Refs) ist falsch-positiv: alte Commits werden irgendwann
      Vorfahre praktisch jedes späteren Branches, wodurch alle acht echten
      Sync-Merges dieses Repos als Agent-Herkunft galten. Containment prüft
      Erreichbarkeit, nicht „hat DIESER Merge DIESEN Branch hereingeholt".

    **Ein git-Aufruf, unabhängig von der Zahl der Merges.** Vorher lief ein
    ``rev-list`` je Merge: live 9,7 s für 30 Tage, während der
    Controller-Selbstaufruf nach 5 s abbricht und einen leeren Feed zeigt —
    LOAD MORE lief damit ab etwa zwölf Klicks ins Leere. Ein gebündelter
    ``rev-list p1a..p2a p1b..p2b …`` behebt das **nicht**: git behandelt
    mehrere Bereiche als EINE globale Menge, wodurch der ``p1`` eines späteren
    Merges die Commits eines früheren herausrechnet (live: 185 erwartete
    Treffer schrumpften auf 2).

    Stattdessen kommt der Graph in einem Stück, und die Zuordnung entsteht
    hier — entlang der Struktur, die der Graph tatsächlich hat: **jede Linie
    ist eine First-Parent-Kette, und jeder Merge auf ihr hängt eine weitere
    Linie an.** Eine Kette wird abgelaufen, bis sie auf Bekanntes trifft (der
    gemeinsame Vorfahre); ihre Seitenzweige kommen danach an die Reihe, nie
    davor. Genau diese Reihenfolge macht die Grenze richtig: der Abzweigpunkt
    eines Branches ist beim Betreten des Branches bereits bekannt.

    Zwei einfachere Fassungen waren live falsch, und beide fielen erst beim
    Abgleich gegen die alte Implementierung auf:

    * **An der eigenen First-Parent-Linie stoppen** genügt nicht. Läuft ein
      Job auf einem anderen Knoten, kommt sein Merge per Sync herüber und liegt
      hier selbst auf einer Nebenlinie; wer nur an der eigenen Linie stoppt,
      traversiert die komplette fremde trunk-Linie mit und schreibt
      menschliche Commits dem Job zu (live: `save: bibi-notes` unter `Witz`).
    * **Die Merges in Log-Reihenfolge abarbeiten** (neueste zuerst) lässt einen
      späteren Merge die Commits eines früheren Branches beanspruchen, sobald
      sein Abzweigpunkt schon Agent-Commits enthielt (live: 6 von 250 falsch,
      zwei davon unter fremdem Slug).

    **Bekannte Grenze.** Ist ein Commit über mehrere Agent-Merges erreichbar
    (verschachtelte Branches), ist seine Zuordnung nicht bestimmt — dann
    gewinnt der Merge, der ihn zuerst erreicht. Live betrifft das sieben
    Commits eines einzigen Tages im Altbestand (04.07.2026); ihr Autor hieß
    damals noch ``Bibi`` ohne Slug und trägt deshalb auch nichts bei. Die
    frühere Implementierung war dort ebenso falsch, nur anders. Gegen die
    letzten 30 Tage stimmen beide Zeichen für Zeichen überein.
    """
    fmt = f"--pretty=format:{_RS}%H{_FS}%P{_FS}%s"

    eltern: dict[str, list[str]] = {}
    reihenfolge: list[str] = []
    merge_slug: dict[str, str] = {}
    for block in _run_log(root, [fmt, *_since_args(since_days)]).split(_RS):
        block = block.strip()
        if not block:
            continue
        sha, parents_s, subject = block.split(_FS, 2)
        eltern[sha] = parents_s.split()
        reihenfolge.append(sha)
        if len(eltern[sha]) == 2 and subject.startswith(_AGENT_MERGE_PREFIX):
            merge_slug[sha] = subject.split("'")[1].removeprefix("agent/")

    # Startpunkte: was im Fenster von keinem anderen Commit als Elternteil
    # genannt wird. Normalerweise genau HEAD; mit `--since` können abgeschnittene
    # Zweige eigene Spitzen haben.
    kinder = {p for ps in eltern.values() for p in ps}
    arbeit = [(sha, None) for sha in reihenfolge if sha not in kinder]

    gesehen: set[str] = set()
    by_sha: dict[str, str] = {}
    while arbeit:
        spitze, slug = arbeit.pop(0)
        sha = spitze
        while sha and sha in eltern and sha not in gesehen:
            gesehen.add(sha)
            if slug is not None:
                by_sha[sha] = slug
            ps = eltern[sha]
            if len(ps) == 2:
                arbeit.append((ps[1], merge_slug.get(sha, slug)))
            sha = ps[0] if ps else None
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


#: Suffix, den ``worker.run_pinned()`` je Lauf anhängt (``token_hex(4)``).
_PIN_SUFFIX = re.compile(r"^(.*)-[0-9a-f]{8}$")


def _basis_slug(name: str) -> str:
    """Ein gepinnter Lauf gehört zu seinem Job — ``news-aggregator-15c7c078``
    ist kein eigener Urheber (Befund m.rau am Feed-Screenshot: fünf Urheber,
    von denen drei derselbe Job waren).

    Dieselbe Regel wie im Jobs-Screen und im Archive: die feste Länge **acht**
    trennt gepinnte Läufe von den Vier-Hex-Suffixen der ``at``-Slugs. Eine
    zweite, kontextabhängige Regel wäre genauer und hätte den Preis, dass
    dieselbe Frage an zwei Orten verschieden beantwortet wird.
    """
    m = _PIN_SUFFIX.match(name)
    return m.group(1) if m else name


def group_entries(
    commits: list[CommitInfo], slugs_by_sha: dict[str, str], *, cases: set[str],
) -> list[FeedEntry]:
    """Reine Gruppierung schon gesammelter Commits, neueste Einheit zuerst."""
    buckets: dict[str, dict] = {}
    for c in commits:
        who = _basis_slug(
            (slugs_by_sha.get(c.sha) or c.author).removeprefix(_JOB_AUTHOR_PREFIX))
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


@dataclass(frozen=True, slots=True)
class UncommittedEntry:
    """Eine Einheit mit offenen Änderungen — dieselbe Form wie ein
    :class:`FeedEntry`, zwei Felder anders.

    ``last_changed`` ist eine **Datei-Mtime** und darf ``None`` sein: eine
    gelöschte Datei hat keine mehr, und „jetzt" einzusetzen hieße, den Zeitpunkt
    des Ansehens als den der Änderung auszugeben. Einen Commit gibt es
    naturgemäß nicht — genau deshalb ist das ein eigener Block und keine
    einsortierte Zeile (Zustimmung m.rau, m.rau/bibi#133).
    """

    unit: str
    last_changed: float | None
    author: str
    states: tuple[str, ...]   # `new` · `modified` · `deleted` · `conflict`
    changes: int


def uncommitted_units(root: Path, *, case_dir_name: str = "case",
                      author: str | None = None,
                      cases: set[str] | None = None) -> list[UncommittedEntry]:
    """Offene Änderungen im Vault, als Einheiten — neueste zuerst.

    **Die einzige Stelle, an der der Feed etwas anderes liest als ``git log``.**
    Die Aggregation bleibt dieselbe: ``unit_for_path()`` bildet die Pfade aus
    ``git status`` auf genau dieselben Einheiten ab, damit ein Ordner nicht
    einmal so und einmal anders heißt, je nachdem ob seine Arbeit schon
    gespeichert ist.

    Der Urheber ist fest **der Mensch** (``git config user.name``): ein Job
    committet, was er tut, ungespeicherte Arbeit stammt also von dem, der hier
    sitzt.

    Einheiten ohne jede Mtime (nur Löschungen) stehen hinten — ohne Zeitpunkt
    gibt es nichts zu sortieren, und eine erfundene Zahl wäre schlimmer als das
    Ende der Liste.
    """
    from bibi import git_ops
    from bibi.git_status import dirty_files

    # `cases` durchreichbar: die Feed-Route hat den Verzeichnis-Scan schon
    # gemacht, und zweimal dasselbe zu begehen kostet ohne Gewinn.
    if cases is None:
        cases = discover_cases(root, case_dir_name=case_dir_name)
    wer = author if author is not None else (git_ops.git_user_name(root) or "—")
    buckets: dict[str, dict] = {}
    for pfad, zustand in dirty_files(root).items():
        unit = unit_for_path(pfad, cases=cases)
        if unit is None:
            continue
        b = buckets.setdefault(unit, {"mtime": None, "states": set(), "changes": 0})
        b["states"].add(zustand)
        b["changes"] += 1
        try:
            m = (root / pfad).stat().st_mtime
        except OSError:      # gelöscht — dann trägt diese Datei keine Zeit bei
            continue
        if b["mtime"] is None or m > b["mtime"]:
            b["mtime"] = m
    eintraege = [
        UncommittedEntry(unit=unit, last_changed=b["mtime"], author=wer,
                         states=tuple(sorted(b["states"])), changes=b["changes"])
        for unit, b in buckets.items()
    ]
    return sorted(eintraege, key=lambda e: (e.last_changed is not None,
                                            e.last_changed or 0.0), reverse=True)


def remote_commit_base_url(root: Path) -> str | None:
    """Gitea-Basis-URL für Commit-Links, aus dem ``origin``-Remote abgeleitet;
    der Aufrufer hängt ``/commit/<sha>`` an. ``None`` ohne ``origin``."""
    url = _run_git(root, ["remote", "get-url", "origin"]).strip()
    if not url:
        return None
    return url[: -len(".git")] if url.endswith(".git") else url
