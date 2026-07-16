"""Merge-back ``agent/<slug>`` → trunk (PLAN-6 Slice A; Worker-Analyse §6)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from bibi.daemon import merge_quarantine, mergeback, worktree as wt
from conftest import FAR_FUTURE_TS

pytestmark = pytest.mark.slow


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _run_in_worktree(repo: Path, slug: str, filename: str, content: str) -> str:
    """Einen Job simulieren: frischer Worktree, Datei schreiben, committen → SHA."""
    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug=slug)
    (path / filename).write_text(content)
    return wt.commit(worktree=path, message=f"{slug}: run", slug=slug)


def test_merge_back_fast_forward(repo: Path):
    sha = _run_in_worktree(repo, "run1", "note.md", "Witz\n")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "merged"
    # Kernkriterium (PLAN-6 §5.1): Commit von trunk aus erreichbar.
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)
    assert (repo / "note.md").read_text() == "Witz\n"
    assert res.trunk_sha == _git(repo, "rev-parse", "HEAD")


def test_merge_back_real_merge_after_trunk_advanced(repo: Path):
    sha = _run_in_worktree(repo, "run1", "note.md", "vom Job\n")
    # trunk rückt unabhängig vor (anderer Pfad, kein Konflikt):
    (repo / "other.txt").write_text("trunk moved\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk advance")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "merged"
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)
    assert (repo / "note.md").exists() and (repo / "other.txt").exists()


def test_merge_back_conflict_aborts_and_preserves(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")  # ändert dieselbe Datei
    trunk_before = _git(repo, "rev-parse", "HEAD")
    # trunk ändert dieselbe Datei anders → Konflikt:
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict")
    trunk_after = _git(repo, "rev-parse", "HEAD")
    res = mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS)
    assert res.status == "conflict"
    # trunk unverändert (Merge sauber abgebrochen), Branch intakt:
    assert _git(repo, "rev-parse", "HEAD") == trunk_after
    assert "agent/run1" in _git(repo, "branch", "--list", "agent/run1")
    # kein laufender Merge mehr (MERGE_HEAD weg):
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert trunk_before != trunk_after  # sanity


def test_merge_back_up_to_date_when_no_commit(repo: Path):
    # echo-artiger Job: Worktree, aber keine Änderung → commit() == "".
    work = repo / "data" / "worktrees"
    wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "up_to_date"


def test_merge_back_missing_branch_is_error(repo: Path):
    res = mergeback.merge_back(repo_root=repo, slug="nope")
    assert res.status == "error"


def test_unmerged_agent_branches_lists_only_unmerged(repo: Path):
    _run_in_worktree(repo, "done", "a.md", "x\n")
    mergeback.merge_back(repo_root=repo, slug="done")        # gemergt
    _run_in_worktree(repo, "pending", "b.md", "y\n")          # nicht gemergt
    unmerged = mergeback.unmerged_agent_branches(repo_root=repo)
    assert unmerged == ["agent/pending"]


def test_unmerged_ignores_branch_without_new_commits(repo: Path):
    # Branch == trunk-HEAD (kein Commit) → nicht "unmerged".
    work = repo / "data" / "worktrees"
    wt.prepare(repo_root=repo, work_dir=work, slug="empty")
    assert mergeback.unmerged_agent_branches(repo_root=repo) == []


def test_remerge_all_merges_leftovers(repo: Path):
    sha = _run_in_worktree(repo, "left", "c.md", "z\n")
    results = mergeback.remerge_all(repo_root=repo)
    assert results == {"agent/left": "merged"}
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)
    assert mergeback.unmerged_agent_branches(repo_root=repo) == []


def test_merge_back_holds_lock(repo: Path):
    _run_in_worktree(repo, "run1", "note.md", "x\n")
    lock = threading.Lock()
    lock.acquire()
    done: list[str] = []

    def attempt():
        res = mergeback.merge_back(repo_root=repo, slug="run1", lock=lock)
        done.append(res.status)

    th = threading.Thread(target=attempt)
    th.start()
    th.join(timeout=0.3)
    assert done == []  # blockiert, solange der Lock gehalten wird
    lock.release()
    th.join(timeout=2)
    assert done == ["merged"]


# ── PLAN-30 Ebene 2: Backoff/Quarantäne im Sweep ─────────────────────────────
# Modus A (Dirty-Tree-Verweigerung, kein MERGE_HEAD) vs. Modus B (echter
# Inhaltskonflikt, MERGE_HEAD) — nur Modus B (+ generische Fehler) zählen auf
# die 3-Fehlschläge-Eskalationsgrenze (A2+A4), Modus A ist kein Fehlschlag.


def test_merge_back_dirty_tree_refusal_is_blocked_not_conflict(repo: Path):
    # Vor Ebene 4 geschrieben (erwartete damals "blocked", Modus A über Gits
    # eigene Verweigerung WÄHREND eines echten Merge-Versuchs, live am echten
    # Repo bewiesen, s. PLAN-30 "Nachtrag: der exakte Stash-Mechanismus").
    # Review-Runde 7, Fund (eigene Ergänzung, über den now=-Fund hinaus):
    # Ebene 4s Idle-Guard (_live_overlap(), läuft VOR jedem echten Merge-
    # Versuch) fängt genau dieses Szenario (dirty Datei, Teil des Merge-Diffs)
    # jetzt selbst schon über _dirty_subset() ab — der echte Merge-Versuch,
    # der "blocked" erzeugen würde, wird dadurch nie mehr erreicht. "blocked"
    # bleibt als Statuswert für einen (seltenen) Fail-Open-Fall von
    # _merge_tree_paths() selbst bestehen, aber NICHT mehr für diesen,
    # ursprünglich getesteten Weg — Erwartung hier auf die jetzt tatsächliche,
    # live verifizierte Antwort korrigiert. Beobachtbares Verhalten bleibt
    # identisch (kein Fehlschlag, Datei unangetastet, keine Quarantäne).
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")
    # f.txt dirty im Working Tree (NICHT committet) + Teil des Merge-Diffs.
    (repo / "f.txt").write_text("dirty uncommitted\n")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "live_edit"
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    # Kein Merge-Versuch fand statt — Inhalt bleibt exakt erhalten:
    assert (repo / "f.txt").read_text() == "dirty uncommitted\n"
    # kein "echter" Fehlschlag → keine Quarantäne:
    assert merge_quarantine.get(repo, "agent/run1") is None


def test_merge_back_conflict_creates_quarantine_entry(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict")
    res = mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS)
    assert res.status == "conflict"
    entry = merge_quarantine.get(repo, "agent/run1")
    assert entry is not None
    assert entry.failures == 1
    assert entry.trunk_sha == _git(repo, "rev-parse", "HEAD")


def test_merge_back_skips_retry_when_trunk_unchanged_since_failure(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict")
    first = mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS)
    assert first.status == "conflict"
    head_after_first = _git(repo, "rev-parse", "HEAD")

    # Zweiter Versuch landet in der Quarantäne-Vorprüfung (läuft VOR dem
    # Idle-Guard) — braucht deshalb kein now=-Override.
    second = mergeback.merge_back(repo_root=repo, slug="run1")
    assert second.status == "quarantined"
    # kein neuer Versuch fand statt: trunk unverändert, Fehlschlag-Zähler
    # bleibt bei 1 (kein neuer Fehlschlag, nur ein übersprungener Versuch).
    assert _git(repo, "rev-parse", "HEAD") == head_after_first
    assert merge_quarantine.get(repo, "agent/run1").failures == 1


def test_merge_back_retries_when_trunk_advanced_since_failure(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict")
    assert mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS).status == "conflict"

    # trunk bewegt sich (unabhängige Änderung) → neue Chance auf konfliktfreien
    # Merge, aber f.txt bleibt divergent → wieder Konflikt, jetzt Fehlschlag 2.
    # f.txt selbst wurde seit dem ersten Versuch nicht neu geschrieben, aber
    # dessen mtime liegt im Testlauf trotzdem "gerade eben" — auch dieser
    # zweite Versuch braucht now=.
    (repo / "other.txt").write_text("trunk moved\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk advance")
    res = mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS)
    assert res.status == "conflict"
    assert merge_quarantine.get(repo, "agent/run1").failures == 2


def test_merge_back_escalates_after_three_consecutive_failures(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict 1")

    for i in range(3):
        res = mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS)
        assert res.status == "conflict", f"Versuch {i + 1} sollte noch konfliktieren"
        (repo / f"advance{i}.txt").write_text("x\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"trunk advance {i}")

    assert merge_quarantine.get(repo, "agent/run1").failures == 3
    head_before = _git(repo, "rev-parse", "HEAD")
    # 4. Versuch, trunk erneut fortgeschritten — trotzdem hart eskaliert,
    # kein neuer Merge-Versuch mehr (A4: komplett aus dem Sweep genommen).
    # Eskalation wird schon in der Quarantäne-Vorprüfung erkannt, vor dem
    # Idle-Guard — kein now=-Override nötig.
    escalated = mergeback.merge_back(repo_root=repo, slug="run1")
    assert escalated.status == "quarantined"
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert merge_quarantine.get(repo, "agent/run1").failures == 3


def test_merge_back_clears_quarantine_on_successful_merge(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict")
    assert mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS).status == "conflict"
    assert merge_quarantine.get(repo, "agent/run1") is not None

    # trunk auf die Job-Version zurückführen → Merge geht jetzt sauber durch.
    # f.txt wird hier erneut geschrieben — auch dieser (jetzt konfliktfreie)
    # Versuch braucht now=, sonst würde der Idle-Guard ihn genauso überspringen.
    (repo / "f.txt").write_text("job version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk resolves conflict")
    res = mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS)
    assert res.status == "merged"
    assert merge_quarantine.get(repo, "agent/run1") is None


def test_remerge_all_prunes_quarantine_for_branches_no_longer_unmerged(repo: Path):
    _run_in_worktree(repo, "run1", "f.txt", "job version\n")
    (repo / "f.txt").write_text("trunk version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk conflict")
    assert mergeback.merge_back(repo_root=repo, slug="run1", now=FAR_FUTURE_TS).status == "conflict"
    assert merge_quarantine.get(repo, "agent/run1") is not None

    # Branch von außen aufgeräumt (z. B. Mensch löst manuell via /sync) — der
    # Sweep sieht ihn nicht mehr als unmerged, muss die verwaiste Zeile löschen.
    # Worktree zuerst entfernen (Review-Runde 3, Fund 1): "agent/run1" ist noch
    # in data/worktrees/run1 ausgecheckt, Git verweigert sonst "branch -D".
    wt.remove(repo_root=repo, worktree=repo / "data" / "worktrees" / "run1")
    _git(repo, "branch", "-D", "agent/run1")
    mergeback.remerge_all(repo_root=repo)
    assert merge_quarantine.get(repo, "agent/run1") is None


# ── PLAN-30 Ebene 4: Idle-Fenster-Guard (Nebenbedingung 0) ───────────────────
# Ein Merge, der eine gerade dirty oder kürzlich bearbeitete Datei anfassen
# würde, wird komplett übersprungen — reiner git-merge-tree-Dry-Run, kein
# Working-Tree-Zugriff, kein Fehlschlag, nicht über force umgehbar.


def test_merge_back_skips_when_target_path_is_dirty(repo: Path):
    _run_in_worktree(repo, "run1", "note.md", "job content\n")
    (repo / "note.md").write_text("dirty local edit\n", encoding="utf-8")  # nicht committet
    head_before = _git(repo, "rev-parse", "HEAD")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "live_edit"
    assert _git(repo, "rev-parse", "HEAD") == head_before  # kein Merge-Versuch
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert (repo / "note.md").read_text() == "dirty local edit\n"  # unangetastet
    assert merge_quarantine.get(repo, "agent/run1") is None  # kein Fehlschlag


def test_merge_back_skips_when_target_path_recently_touched_but_clean(repo: Path):
    (repo / "note.md").write_text("trunk\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add note.md")
    mtime_of_note = (repo / "note.md").stat().st_mtime
    _run_in_worktree(repo, "run1", "note.md", "job content\n")
    head_before = _git(repo, "rev-parse", "HEAD")
    res = mergeback.merge_back(repo_root=repo, slug="run1", now=mtime_of_note + 10)
    assert res.status == "live_edit"
    assert _git(repo, "rev-parse", "HEAD") == head_before


def test_merge_back_proceeds_once_idle_window_passed(repo: Path):
    (repo / "note.md").write_text("trunk\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add note.md")
    mtime_of_note = (repo / "note.md").stat().st_mtime
    sha = _run_in_worktree(repo, "run1", "note.md", "job content\n")
    res = mergeback.merge_back(repo_root=repo, slug="run1", now=mtime_of_note + 121)
    assert res.status == "merged"
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)


def test_merge_back_force_does_not_bypass_live_edit_guard(repo: Path):
    _run_in_worktree(repo, "run1", "note.md", "job content\n")
    (repo / "note.md").write_text("dirty\n", encoding="utf-8")
    res = mergeback.merge_back(repo_root=repo, slug="run1", force=True)
    assert res.status == "live_edit"


def test_merge_back_live_edit_leaves_no_quarantine_trace(repo: Path):
    _run_in_worktree(repo, "run1", "note.md", "job content\n")
    (repo / "note.md").write_text("dirty\n", encoding="utf-8")
    mergeback.merge_back(repo_root=repo, slug="run1")
    mergeback.merge_back(repo_root=repo, slug="run1")  # zweimal — zählt trotzdem nicht
    assert merge_quarantine.get(repo, "agent/run1") is None


def test_merge_back_unrelated_dirty_file_does_not_block(repo: Path):
    # Nur Überlapp mit dem Merge-Diff zählt — eine dirty Datei, die der Merge
    # gar nicht anfasst, darf einen ansonsten sauberen Merge nicht verhindern.
    sha = _run_in_worktree(repo, "run1", "note.md", "job content\n")
    (repo / "unrelated.txt").write_text("dirty, aber nicht Teil des Merges\n",
                                        encoding="utf-8")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "merged"
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "trunk"],
                   cwd=repo, check=True)


# ── Review-Runde 4, Fund 1 (kritisch): ein bereits offener Merge/Rebase darf
# NIE einen zweiten, unabhängigen Merge-Versuch auslösen — live gegen echtes
# Git bewiesen, dass sonst (a) ein Merge-Versuch für einen ANDEREN Branch
# fälschlich als eigener Konflikt gewertet wird und mit "merge --abort" die
# laufende Konfliktauflösung eines Menschen zerstört, und (b) ein
# automatisierter Whole-Repo-Commit die noch unmerged Datei lautlos mit ihrem
# Konfliktmarker-Inhalt "auflöst" (s. test_git_ops.py für Fund (b)).


def test_merge_back_refuses_when_other_conflict_already_open(repo: Path):
    # Branch A: echter Konflikt, mit keep_conflict=True offen gelassen (wie
    # /sync es tut).
    _run_in_worktree(repo, "a", "conflicted.md", "job version\n")
    (repo / "conflicted.md").write_text("trunk version\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk diverge")
    first = mergeback.merge_back(repo_root=repo, slug="a", keep_conflict=True, now=FAR_FUTURE_TS)
    assert first.status == "conflict"
    assert (repo / ".git" / "MERGE_HEAD").exists()
    conflicted_content_during_open_conflict = (repo / "conflicted.md").read_text()
    assert "<<<<<<<" in conflicted_content_during_open_conflict

    # Branch B: völlig unabhängig, würde für sich genommen sauber mergen.
    _run_in_worktree(repo, "b", "other.md", "b content\n")

    # repo_busy wird schon vor dem Idle-Guard erkannt (allererste Prüfung in
    # _merge_locked()) — kein now=-Override für B nötig.
    second = mergeback.merge_back(repo_root=repo, slug="b")
    assert second.status == "repo_busy"
    # A's Konflikt-Zustand ist UNANGETASTET — nicht durch B's Versuch
    # abgebrochen, kein Verlust der Auflösungsarbeit:
    assert (repo / ".git" / "MERGE_HEAD").exists()
    assert (repo / "conflicted.md").read_text() == conflicted_content_during_open_conflict
    # B bekommt KEINE falsche Quarantäne-Zeile für einen Konflikt, den es nie hatte:
    assert merge_quarantine.get(repo, "agent/b") is None


def test_merge_back_repo_busy_not_bypassable_by_force(repo: Path):
    _run_in_worktree(repo, "a", "conflicted.md", "job version\n")
    (repo / "conflicted.md").write_text("trunk version\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk diverge")
    mergeback.merge_back(repo_root=repo, slug="a", keep_conflict=True, now=FAR_FUTURE_TS)
    assert (repo / ".git" / "MERGE_HEAD").exists()

    _run_in_worktree(repo, "b", "other.md", "b content\n")
    res = mergeback.merge_back(repo_root=repo, slug="b", force=True)
    assert res.status == "repo_busy"


# ── Review-Runde 5, Fund 1 (kritisch): `git diff --name-only` verpasst
# Binärkonflikte (der geschriebene Tree behält bei einem echten Binärkonflikt
# die trunk-Seite unverändert bei) — live bewiesen, dass eine dirty
# Binärdatei ohne die Zusatz-Erkennung in _merge_tree_paths() unerkannt
# geblieben wäre und ein automatischer `merge --abort` ihren uncommitteten
# Inhalt unwiederbringlich verworfen hätte.


def test_merge_back_detects_dirty_binary_conflict_via_live_edit(repo: Path):
    import random
    random.seed(1)
    base = bytes(random.randrange(256) for _ in range(200))
    job = bytes(random.randrange(256) for _ in range(200))
    trunk_change = bytes(random.randrange(256) for _ in range(200))
    dirty_local = bytes(random.randrange(256) for _ in range(200))

    (repo / "blob.bin").write_bytes(base)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add binary")

    work = repo / "data" / "worktrees"
    path = wt.prepare(repo_root=repo, work_dir=work, slug="run1")
    (path / "blob.bin").write_bytes(job)
    wt.commit(worktree=path, message="run1: run", slug="run1")

    (repo / "blob.bin").write_bytes(trunk_change)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "trunk diverge (binary)")

    # Working Tree macht blob.bin zusätzlich dirty (uncommitteter lokaler
    # Edit) — genau der Fall, den Modus A bei Textdateien abfängt, bei
    # Binärdateien aber NICHT (git verweigert dort nicht vorab).
    (repo / "blob.bin").write_bytes(dirty_local)

    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "live_edit"
    # Der Merge wurde nie versucht — kein MERGE_HEAD, kein "merge --abort",
    # der uncommittete Inhalt bleibt exakt erhalten:
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert (repo / "blob.bin").read_bytes() == dirty_local


def test_merge_back_handles_non_ascii_path_overlap(repo: Path):
    _run_in_worktree(repo, "run1", "Kündigung.md", "job version\n")
    (repo / "Kündigung.md").write_text("dirty lokal\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD")
    res = mergeback.merge_back(repo_root=repo, slug="run1")
    assert res.status == "live_edit"
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert (repo / "Kündigung.md").read_text(encoding="utf-8") == "dirty lokal\n"
