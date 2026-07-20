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


#: git-generierte Default-Merge-Message bei ``merge --no-ff --no-edit agent/<slug>``
#: (``mergeback.merge_back()`` — ``--no-edit`` ist hartcodiert, nie konfigurierbar).
_AGENT_MERGE_PREFIX = "Merge branch 'agent/"


def agent_commit_shas(root: Path, *, since_days: int | None = None) -> set[str]:
    """Commit-Hashes, die über einen ``agent/*``-Merge (``mergeback.merge_back()``)
    hereinkamen — Signal ist die Commit-Message, **nicht** Branch-Containment.

    Zwei verworfene Zwischenstände, beide live gegen die echte
    bibi-notes-Historie widerlegt (Design-Pass „miss trauen, nicht raten"):

    1. **First-Parent-vs-voll-Log-Mengendifferenz** (User-Fund 2026-07-06:
       „Agents ausblenden versteckt Sachen, die NUR ich gemacht habe") —
       klassifizierte JEDEN Merge als Agent-Herkunft, auch gewöhnliche
       Mehrgeräte-Sync-Merges des Synchronizers (``strategy="merge"``).
    2. **Branch-Containment** (``git branch``/``rev-list --contains`` gegen
       lebende ``agent/*``-Refs, User-Vorschlag „sauberer als die Message") —
       zwei Probleme, beide am echten Repo nachgewiesen: (a) **langsam**, ein
       Aufruf je Merge-Commit ⇒ 193 Merges ⇒ 5,7s, über dem 5s-Timeout des
       Controller-Selbstaufrufs; (b) **falsch-positiv**, weil alte Commits
       irgendwann Vorfahre praktisch jedes späteren Branches werden — alle 8
       echten Sync-Merges dieses Repos wurden fälschlich als Agent erkannt,
       weil ihr zweiter Elternteil (ein älterer Remote-Stand) transitiv
       Vorfahre von ``agent/Runner``/``agent/Witz`` ist. Containment prüft
       Erreichbarkeit, nicht „hat DIESER Merge DIESEN Branch reingeholt".

    Die Commit-Message ist damit das einzige verlässliche Signal für **dieses**
    System (``--no-edit`` ist hartcodiert, nie ein Aufruf, der sie ändern
    könnte) — kein Kompromiss, sondern die einzig korrekte Wahl hier.

    **Kein** gebündelter ``git rev-list p1a..p2a p1b..p2b …``-Aufruf über alle
    Bereiche auf einmal (dritter Zwischenstand, ebenfalls widerlegt): git
    behandelt mehrere Bereiche nicht als unabhängige Vereinigung, sondern als
    EINE globale „alle p2 minus alle p1"-Menge — der ``p1`` eines späteren
    Merges enthält chronologisch oft schon den ``p2`` eines früheren Merges
    (weil trunk zwischenzeitlich weiterging), wodurch dessen echte Commits
    fälschlich herausgerechnet werden (live beobachtet: 185 erwartete Treffer
    wurden so auf 2 zusammengestrichen). Ein ``rev-list``-Aufruf pro Bereich
    bleibt nötig — 185 Aufrufe brauchen live gegen die echte Historie ~1,8s
    (statt 5,7s vorher), der Flaschenhals war die eine überflüssige
    ``branch --contains``-Prüfung je Merge, nicht die Anzahl an sich."""
    since = _since_args(since_days)
    fmt = f"--pretty=format:{_RS}%H{_FS}%P{_FS}%s"
    merges_out = _run_log(root, ["--merges", fmt, *since])

    agent_shas: set[str] = set()
    for block in merges_out.split(_RS):
        block = block.strip()
        if not block:
            continue
        _sha, parents_s, subject = block.split(_FS, 2)
        parents = parents_s.split()
        if len(parents) == 2 and subject.startswith(_AGENT_MERGE_PREFIX):
            rev_range = f"{parents[0]}..{parents[1]}"
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


def activity_series_by_prefix(
    commits: list[CommitInfo], agent_shas: set[str], prefixes: dict[str, str],
    *, since_days: int, now: float | None = None,
    own_paths: dict[str, str] | None = None,
) -> dict[str, list[int]]:
    """Tages-Buckets (älteste zuerst, heute zuletzt) agent-verursachter Commits
    je Pfad-Präfix — Baustein für die Jobs-Sparkline (Bibi4-Iteration, User-
    Fund: "eine Sparkline, die die durch den Agenten verursachten git
    Änderungen repräsentiert"). Reine Aggregation schon gesammelter Commits
    (kein eigener Git-Aufruf) — analog zu ``heatmap_buckets()``/
    ``group_entities()``: dieselbe ``collect_commits()``-Liste bedient alle
    Jobs auf einmal, kein Aufruf je Zeile.

    ``own_paths`` (optional, Bugfix — User-Fund: "warum haben alle Runner die
    gleiche Sparkline"): ``prefix`` ist bewusst der Case-**Ordner** eines
    Jobs, nicht nur seine eigene MD (s. Aufrufer), damit Begleitdateien im
    selben Ordner mitzählen. Liegen aber MEHRERE Jobs im selben Ordner (wie
    ``Runner``/``Runner 1``.../``Runner 5`` alle in ``20260627.Test/``), matcht
    ``p.startswith(prefix)`` für jeden von ihnen gleichermaßen — jede Änderung
    an EINER Runner-MD zählte bisher für ALLE Runner im Ordner, die Serien
    waren dadurch identisch. ``own_paths`` (slug → exakter ``repo_path``)
    behebt das: ändert ein Commit genau die eigene Schedule-MD eines ANDEREN
    bekannten Jobs im selben Ordner, zählt das nur für JENEN Job — eine echte
    Begleitdatei (die zu keinem der bekannten Jobs gehört) zählt weiterhin für
    alle Jobs des Ordners, wie ursprünglich beabsichtigt. ``None``/leer
    reproduziert das alte (fehlerhafte) Verhalten unverändert."""
    now = time.time() if now is None else now
    today = datetime.datetime.fromtimestamp(now).date()
    series = {key: [0] * since_days for key in prefixes}
    own_paths = own_paths or {}
    siblings_by_key = {
        key: {p for other, p in own_paths.items() if other != key}
        for key in prefixes
    }
    for c in commits:
        if c.sha not in agent_shas:
            continue
        days_ago = (today - datetime.datetime.fromtimestamp(c.epoch).date()).days
        if not (0 <= days_ago < since_days):
            continue
        bucket = since_days - 1 - days_ago
        for key, prefix in prefixes.items():
            siblings = siblings_by_key[key]
            if any(p.startswith(prefix) and p not in siblings for p in c.paths):
                series[key][bucket] += 1
    return series


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
    """Commit-Zähler je Zelle, ``grid[week][col][hour_bucket]``. **Rollierendes
    Fenster** (PLAN-19 Befund 5, User-Entscheidung: „letzter Tag soll IMMER
    heute sein, nicht Mo-So") — Spalte 6 (letzte) ist immer heute, Spalte 0
    sechs Tage davor; Woche 0 = die letzten 7 Tage inkl. heute, Woche 1 die
    7 Tage davor, usw. — **keine** Kalenderwochen-Ausrichtung mehr. Dieselbe
    Commit-Liste wie ``aggregate_feed()`` — kein zweiter Git-Aufruf, nur
    anders aggregiert (PLAN-18 Befund 2)."""
    now_dt = datetime.datetime.fromtimestamp(now if now is not None else time.time())
    today = now_dt.date()

    grid = [[[0] * (24 // _BUCKET_HOURS) for _ in range(7)] for _ in range(weeks)]
    for c in commits:
        dt = datetime.datetime.fromtimestamp(c.epoch)
        days_ago = (today - dt.date()).days
        if days_ago < 0:
            continue  # Uhr-Drift/Zukunft — ignorieren statt negativ zu indizieren
        week_idx = days_ago // 7
        if week_idx >= weeks:
            continue
        col_idx = 6 - (days_ago % 7)
        grid[week_idx][col_idx][dt.hour // _BUCKET_HOURS] += 1
    return grid
