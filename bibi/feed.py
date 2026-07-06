"""Feed-Datenquelle (PLAN-18 Stufe 18.1): Git-Historie → Entitäten (Case/Vault/
System), je mit letztem Änderungszeitpunkt + Autoren-Set + Agent-Herkunft.

**Ein** ``git log --name-status``-Aufruf für die komplette Aggregation (auch
Grundlage der Heatmap, Stufe 18.2) — keine Pfad-für-Pfad-Zusatzaufrufe (der
Verdacht zur bibi-v3-Langsamkeit, PLAN-18 Design-Pass).

Agent-Erkennung **nicht** über ``git branch --contains`` (ein Aufruf pro
Commit, außerdem sind ``agent/*``-Branches nach dem Merge nicht garantiert
vorhanden) — stattdessen Mengendifferenz voller Log vs. ``--first-parent``-Log:
``mergeback.merge_back()`` mergt mit ``--no-ff``, jeder Commit, der NICHT auf
der First-Parent-Linie von ``trunk`` liegt, kam über einen solchen Merge herein.
"""

from __future__ import annotations

import datetime
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_RS = "\x1e"  # Record Separator — trennt Commits im geparsten Log
_FS = "\x1f"  # Field Separator — trennt Hash/Autor/Zeitstempel je Commit


@dataclass(frozen=True, slots=True)
class CommitInfo:
    sha: str
    author: str
    epoch: float
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeedEntity:
    kind: str                  # "case" | "vault" | "system"
    name: str
    last_changed: float
    last_commit_sha: str       # Commit, der last_changed erzeugt hat (Link zum Server)
    authors: frozenset[str]
    all_agent: bool            # True: JEDE beitragende Änderung war agent/*-Herkunft


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


def agent_commit_shas(root: Path, *, since_days: int | None = None) -> set[str]:
    """Commit-Hashes, die über einen ``agent/*``-Merge (``mergeback.merge_back()``)
    hereinkamen.

    **Nicht** über first-parent-vs-voll-Log-Mengendifferenz (früherer Bug,
    User-Fund 2026-07-06: „Agents ausblenden versteckt Sachen, die NUR ich
    gemacht habe") — das klassifizierte JEDEN Merge-Commit als Agent-Herkunft,
    auch ganz gewöhnliche Mehrgeräte-Sync-Merges des Synchronizers
    (``strategy="merge"``, ``daemon/synchronizer.py``), deren zweite
    Eltern-Linie genauso echte, aber fremd-authored Commits enthält.

    **Auch nicht** über die Commit-Message (Zwischenstand, User-Korrektur
    2026-07-06: „sauberer, den Branch als Agenten-Signal zu verwenden statt
    auf die Message zu gehen") — stattdessen die tatsächliche Branch-
    Zugehörigkeit (``git branch --contains``): für jeden Merge-Commit prüft
    diese Funktion, ob sein zweiter Elternteil auf einem ``agent/*``-Branch
    liegt (Branches werden im Code nirgends gelöscht, Containment bleibt
    dauerhaft prüfbar). Nur dann zählt dessen zweite Eltern-Linie
    (``git rev-list p1..p2``) als Agent-Herkunft."""
    since = _since_args(since_days)
    fmt = f"--pretty=format:{_RS}%H{_FS}%P"
    out = _run_log(root, ["--merges", fmt, *since])

    agent_shas: set[str] = set()
    for block in out.split(_RS):
        block = block.strip()
        if not block:
            continue
        sha, parents_s = block.split(_FS)
        parents = parents_s.split()
        if len(parents) != 2:
            continue
        second_parent = parents[1]
        on_agent_branch = _run_git(
            root, ["branch", "--list", "agent/*", "--contains", second_parent])
        if on_agent_branch.strip():
            rev_range = f"{parents[0]}..{second_parent}"
            agent_shas.update(_run_git(root, ["rev-list", rev_range]).split())
    return agent_shas


def classify_path(path: str, *, case_dir_name: str = "case") -> tuple[str, str]:
    """Ein geänderter Pfad → (kind, entity_name) (Doc-Taxonomie: Case/Vault/System)."""
    parts = path.split("/")
    if parts and parts[0] == "vault":
        if len(parts) > 2 and parts[1] == case_dir_name:
            return "case", parts[2]
        if len(parts) > 1:
            return "vault", "/".join(parts[1:])
    return "system", "System"


def group_entities(
    commits: list[CommitInfo], agent_shas: set[str], *, case_dir_name: str = "case",
) -> list[FeedEntity]:
    """Reine Gruppierung schon gesammelter Commits (kein Git-Aufruf) — Baustein
    von ``aggregate_feed()``, direkt wiederverwendbar, wenn dieselbe
    ``collect_commits()``-Liste auch die Heatmap speist (ein Aufruf, zwei
    Aggregationen, PLAN-18 Befund 2)."""
    buckets: dict[tuple[str, str], dict] = {}
    for c in commits:
        is_agent = c.sha in agent_shas
        for path in c.paths:
            key = classify_path(path, case_dir_name=case_dir_name)
            b = buckets.setdefault(
                key, {"last": c.epoch, "last_sha": c.sha, "authors": set(), "agent": []})
            if c.epoch > b["last"]:
                b["last"], b["last_sha"] = c.epoch, c.sha
            b["authors"].add(c.author)
            b["agent"].append(is_agent)

    entities = [
        FeedEntity(kind=kind, name=name, last_changed=b["last"], last_commit_sha=b["last_sha"],
                  authors=frozenset(b["authors"]), all_agent=all(b["agent"]))
        for (kind, name), b in buckets.items()
    ]
    return sorted(entities, key=lambda e: e.last_changed, reverse=True)


def aggregate_feed(
    root: Path, *, since_days: int | None = None, case_dir_name: str = "case",
) -> list[FeedEntity]:
    """Entitäten (neuester Zeitpunkt zuerst), eine Zeile je Case-Ordner/Vault-
    Datei/System-Sammelzeile — Kern der Feed-Änderungsliste (PLAN-18). Bequemer
    Wrapper um ``collect_commits()`` + ``agent_commit_shas()`` +
    ``group_entities()`` für den Alleinstellungs-/Testfall."""
    commits = collect_commits(root, since_days=since_days)
    agent_shas = agent_commit_shas(root, since_days=since_days)
    return group_entities(commits, agent_shas, case_dir_name=case_dir_name)


def remote_commit_base_url(root: Path) -> str | None:
    """Gitea-Basis-URL für Commit-Links, aus dem konfigurierten ``origin``-
    Remote abgeleitet (PLAN-17 Befund 3: ``.git``-Suffix strippen, Aufrufer
    hängt ``/commit/<sha>`` an) — konfigurierbar über den Remote selbst, keine
    neue Einstellung nötig. ``None`` ohne konfiguriertes ``origin``."""
    url = _run_git(root, ["remote", "get-url", "origin"]).strip()
    if not url:
        return None
    return url[: -len(".git")] if url.endswith(".git") else url


#: Heatmap-Layout (Wireframe, verifiziert): 5 Wochen-Zeilen × 7 Tage × 8 3h-Buckets.
HEATMAP_WEEKS = 5
_BUCKET_HOURS = 3


def heatmap_buckets(
    commits: list[CommitInfo], *, weeks: int = HEATMAP_WEEKS, now: float | None = None,
) -> list[list[list[int]]]:
    """Commit-Zähler je Zelle, ``grid[week][weekday][hour_bucket]``. Woche 0 =
    aktuelle Kalenderwoche (Montag–Sonntag), absteigend in die Vergangenheit;
    Tag 0 = Montag. Dieselbe Commit-Liste wie ``aggregate_feed()`` — kein
    zweiter Git-Aufruf, nur anders aggregiert (PLAN-18 Befund 2)."""
    now_dt = datetime.datetime.fromtimestamp(now if now is not None else time.time())
    monday = (now_dt - datetime.timedelta(days=now_dt.weekday())).date()

    grid = [[[0] * (24 // _BUCKET_HOURS) for _ in range(7)] for _ in range(weeks)]
    for c in commits:
        dt = datetime.datetime.fromtimestamp(c.epoch)
        days_since_monday = (dt.date() - monday).days
        week_idx = -(days_since_monday // 7)
        if not (0 <= week_idx < weeks):
            continue
        weekday_idx = days_since_monday % 7
        grid[week_idx][weekday_idx][dt.hour // _BUCKET_HOURS] += 1
    return grid
