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
    authors: frozenset[str]
    all_agent: bool            # True: JEDE beitragende Änderung war agent/*-Herkunft


def _run_log(root: Path, args: list[str]) -> str:
    proc = subprocess.run(["git", "log", *args], cwd=root,
                          capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else ""


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
    """Commit-Hashes, die über einen ``--no-ff``-Merge von ``agent/*`` kamen —
    alles außerhalb der First-Parent-Linie (zwei billige ``%H``-only Logs)."""
    since = _since_args(since_days)
    all_shas = set(_run_log(root, ["--format=%H", *since]).split())
    first_parent = set(_run_log(root, ["--first-parent", "--format=%H", *since]).split())
    return all_shas - first_parent


def classify_path(path: str, *, case_dir_name: str = "case") -> tuple[str, str]:
    """Ein geänderter Pfad → (kind, entity_name) (Doc-Taxonomie: Case/Vault/System)."""
    parts = path.split("/")
    if parts and parts[0] == "vault":
        if len(parts) > 2 and parts[1] == case_dir_name:
            return "case", parts[2]
        if len(parts) > 1:
            return "vault", "/".join(parts[1:])
    return "system", "System"


def aggregate_feed(
    root: Path, *, since_days: int | None = None, case_dir_name: str = "case",
) -> list[FeedEntity]:
    """Entitäten (neuester Zeitpunkt zuerst), eine Zeile je Case-Ordner/Vault-
    Datei/System-Sammelzeile — Kern der Feed-Änderungsliste (PLAN-18)."""
    commits = collect_commits(root, since_days=since_days)
    agent_shas = agent_commit_shas(root, since_days=since_days)

    buckets: dict[tuple[str, str], dict] = {}
    for c in commits:
        is_agent = c.sha in agent_shas
        for path in c.paths:
            key = classify_path(path, case_dir_name=case_dir_name)
            b = buckets.setdefault(key, {"last": c.epoch, "authors": set(), "agent": []})
            b["last"] = max(b["last"], c.epoch)
            b["authors"].add(c.author)
            b["agent"].append(is_agent)

    entities = [
        FeedEntity(kind=kind, name=name, last_changed=b["last"],
                  authors=frozenset(b["authors"]), all_agent=all(b["agent"]))
        for (kind, name), b in buckets.items()
    ]
    return sorted(entities, key=lambda e: e.last_changed, reverse=True)


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
