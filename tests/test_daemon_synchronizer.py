"""Synchronizer: Debounce-Logik + Tick (DESIGN §4.3, PLAN-2 §2.3)."""

from __future__ import annotations

import pytest

from bibi import state
from bibi.daemon.synchronizer import PushDebouncer, Synchronizer, params_for

pytestmark = pytest.mark.slow


# ── reine Debounce-Logik ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "lines,idle,mx",
    [(0, 600, 1800), (49, 600, 1800), (50, 300, 900), (300, 300, 900),
     (301, 120, 600), (1000, 120, 600)],
)
def test_params_for_buckets(lines, idle, mx):
    p = params_for(lines)
    assert (p.idle_s, p.max_s) == (idle, mx)


def test_debounce_pushes_after_idle():
    d = PushDebouncer()
    d.observe(0.0, "x", 10)
    assert not d.should_push(0.0)
    assert not d.should_push(599.0)
    assert d.should_push(600.0)  # <50 Zeilen → Idle 10 min


def test_debounce_resets_idle_on_new_change():
    d = PushDebouncer()
    d.observe(0.0, "x", 10)
    d.observe(500.0, "y", 10)          # neue Änderung verschiebt das Idle-Fenster
    assert not d.should_push(600.0)
    assert d.should_push(1100.0)


def test_debounce_safety_net_fires_at_max():
    d = PushDebouncer()
    pushed_at = None
    for i in range(0, 30):
        t = i * 100.0
        d.observe(t, f"s{i}", 10)      # ständige Änderung → Idle greift nie
        if d.should_push(t):
            pushed_at = t
            break
    assert pushed_at == 1800.0          # Safety-Net = Max (30 min für <50)


def test_debounce_clean_tree_never_pushes():
    d = PushDebouncer()
    d.observe(0.0, "", 0)
    assert not d.should_push(99999.0)


# ── Synchronizer-Tick (injizierte Git-IO + Clock) ───────────────────────────

def _mk(push=False, pull=False, stat=("x", 10), push_kind=None, calls=None):
    calls = calls if calls is not None else {"push": 0, "pull": 0}

    def diff_stat():
        return stat

    def push_fn():
        calls["push"] += 1
        return (push_kind is None, ["log"], push_kind)

    def pull_fn():
        calls["pull"] += 1
        return (True, None)

    s = Synchronizer(
        push=push, pull=pull, diff_stat=diff_stat, push_fn=push_fn, pull_fn=pull_fn,
        pull_interval_s=180, poll_s=60,
    )
    return s, calls


def test_tick_pushes_after_idle(team_repo):
    s, calls = _mk(push=True)
    s.tick(0.0)
    assert calls["push"] == 0
    s.tick(600.0)
    assert calls["push"] == 1


def test_tick_push_logs_loglines_as_message(team_repo, caplog):
    # PLAN-25 Befund 3 Ebene 1, User-Fund: "sync.push ok=true kind=null" ist zu
    # dürftig — commit_and_push() (git_ops.py) liefert schon eine sprechende
    # loglines-Liste ("committed: ...", "integrated", "push ok"), die bisher
    # nie ans Aktivitätslog durchgereicht wurde (nur ok=/kind=).
    import logging as _logging
    s, _ = _mk(push=True)
    s.tick(0.0)  # öffnet das Änderungsfenster (first_change_at/last_change_at)
    with caplog.at_level(_logging.INFO, logger="bibi.daemon.synchronizer"):
        s.tick(600.0)  # Debounce-Fenster (10 min Idle bei <50 Zeilen) abgelaufen
    rec = next(r for r in caplog.records if getattr(r, "bibi", {}).get("event") == "sync.push")
    assert rec.getMessage() == "log"  # _mk()s push_fn liefert loglines=["log"]


def test_push_now_logs_loglines_as_message(team_repo, caplog):
    import logging as _logging
    s, _ = _mk(push=True)
    with caplog.at_level(_logging.INFO, logger="bibi.daemon.synchronizer"):
        s.push_now()
    rec = next(r for r in caplog.records if getattr(r, "bibi", {}).get("event") == "sync.push")
    assert rec.getMessage() == "log"


def test_tick_pulls_on_interval(team_repo):
    s, calls = _mk(pull=True, stat=("", 0))
    s.tick(0.0)
    assert calls["pull"] == 1          # erster Pull sofort
    s.tick(60.0)
    assert calls["pull"] == 1          # noch nicht fällig (3 min)
    s.tick(180.0)
    assert calls["pull"] == 2


def test_set_push_implies_pull():
    s, _ = _mk()
    s.set_push(True)
    st = s.status()
    assert st["push"] is True and st["pull"] is True
    s.set_push(False)
    assert s.status()["push"] is False


def test_conflict_sets_state(team_repo):
    s, _ = _mk(push=True, push_kind="conflict")
    s.tick(0.0)
    s.tick(600.0)
    assert state.get_sync_conflict() is True


def test_push_clears_prior_conflict(team_repo):
    state.set_sync_conflict(True)
    s, _ = _mk(push=True)            # erfolgreicher Push
    s.tick(0.0)
    s.tick(600.0)
    assert state.get_sync_conflict() is False


# ── Integration: echte Git-IO gegen bare Origin ─────────────────────────────

def test_diff_stat_detects_untracked(repo_with_origin):
    from bibi import git_ops
    root, _ = repo_with_origin
    assert git_ops.diff_stat() == ("", 0)             # sauber
    (root / "vault" / "case" / "new.md").write_text("hi\n", encoding="utf-8")
    signal, _lines = git_ops.diff_stat()
    assert "new.md" in signal                          # untracked erkannt


def test_real_push_reaches_origin(repo_with_origin):
    import subprocess
    root, origin = repo_with_origin
    (root / "vault" / "case" / "new.md").write_text("hello\n", encoding="utf-8")
    s = Synchronizer(push=True)                         # echte Git-Anbindung
    assert s.tick(0.0)["pushed"] is False               # Debounce offen
    assert s.tick(600.0)["pushed"] is True              # Idle (<50) → push
    tree = subprocess.run(
        ["git", "-C", str(origin), "ls-tree", "-r", "--name-only", "trunk"],
        capture_output=True, text=True,
    ).stdout
    assert "vault/case/new.md" in tree
    subj = subprocess.run(
        ["git", "-C", str(origin), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True,
    ).stdout
    assert subj.startswith("auto:")                     # transiente Auto-Commit-Message


# ── Nachtrag 2026-07-16: _default_pull() bekommt Ebene 4s Idle-Guard ───────
# (live entdeckt: der unbeaufsichtigte Hintergrund-Pull hatte den Guard nie
# bekommen, nur der interaktive /sync-Pfad — derselbe echte Konflikt wurde
# alle 3 Minuten unbegrenzt neu versucht, dieselbe Fehlerklasse wie der
# Ursprungsvorfall.)

def _sh(cwd, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _clone(origin, dest):
    _sh(dest.parent, "clone", "-q", str(origin), dest.name)
    _sh(dest, "config", "user.name", "O"); _sh(dest, "config", "user.email", "o@e.x")
    return dest


def test_default_pull_skips_when_target_path_recently_touched(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    other = _clone(origin, tmp_path / "other")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit")
    _sh(other, "push", "-q", "origin", "trunk")

    (root / "pyproject.toml").write_text("LOCAL dirty\n", encoding="utf-8")  # nicht committet
    head_before = _sh(root, "rev-parse", "HEAD").strip()

    s = Synchronizer(pull=True, repo_root=root)
    did = s.tick(0.0)
    assert did["pulled"] is True  # ein Pull-VERSUCH fand statt (guard != "kein Pull")
    assert _sh(root, "rev-parse", "HEAD").strip() == head_before  # aber nichts integriert
    assert (root / "pyproject.toml").read_text() == "LOCAL dirty\n"  # unangetastet


def test_default_pull_proceeds_when_no_overlap(repo_with_origin, tmp_path):
    # Ein unbeteiligter dirty Pfad darf einen ansonsten sauberen Pull nicht
    # verhindern — der Guard prüft nur echten Überlapp (dasselbe Prinzip wie
    # /syncs eigener Pull-Guard, hier für den automatischen Loop).
    root, origin = repo_with_origin
    other = _clone(origin, tmp_path / "other")
    (other / "remote.txt").write_text("r\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit")
    _sh(other, "push", "-q", "origin", "trunk")

    (root / "unrelated.txt").write_text("dirty, aber nicht Teil des Pulls\n", encoding="utf-8")

    s = Synchronizer(pull=True, repo_root=root)
    did = s.tick(0.0)
    assert did["pulled"] is True
    assert (root / "remote.txt").exists()


def test_push_gated_by_consent(team_repo):
    calls = {"push": 0}
    consent = {"on": False}

    def push_fn():
        calls["push"] += 1
        return (True, [], None)

    s = Synchronizer(
        push=True, consent=lambda: consent["on"],
        diff_stat=lambda: ("x", 10), push_fn=push_fn, pull_fn=lambda: (True, None),
    )
    s.tick(0.0)
    s.tick(600.0)
    assert calls["push"] == 0          # Zustimmung aus → kein Push (Fenster bleibt offen)
    consent["on"] = True
    s.tick(1200.0)
    assert calls["push"] == 1          # Zustimmung an → abgelaufenes Fenster pusht sofort


def test_tick_merge_sweep_merges_unmerged_branches(tmp_path):
    """F-a (PLAN-7): der Tick mergt liegengebliebene agent/*-Branches nach trunk."""
    import subprocess

    from bibi.daemon import worktree as wt

    root = tmp_path / "r"
    root.mkdir()

    def g(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    g("init", "-q", "-b", "trunk")
    g("config", "user.email", "t@e.x")
    g("config", "user.name", "t")
    (root / "f.txt").write_text("base\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    # ungemergten agent/x-Branch erzeugen
    p = wt.prepare(repo_root=root, work_dir=root / "data" / "wt", slug="x")
    (p / "n.md").write_text("hi\n")
    wt.commit(worktree=p, message="run", slug="x")
    assert wt.is_ahead(repo_root=root, branch="agent/x", trunk="trunk")

    s = Synchronizer(repo_root=root)   # kein push/pull, nur Sweep
    s.tick(0.0)
    assert not wt.is_ahead(repo_root=root, branch="agent/x", trunk="trunk")  # gemergt


def test_merge_sweep_pushes_immediately_after_merge(tmp_path):
    # Bug 2026-07-07 (User-Fund: "warum steht bei sarasate SYNC: ahead") — der
    # Push-Debouncer beobachtet nur den Working-Tree-Diff; ein Merge-Commit
    # hinterlässt sofort wieder einen sauberen Tree, der ihn nie auslöst.
    # diff_stat liefert hier bewusst "sauber" (leerer Tree), damit der Test
    # NUR den neuen push_now()-Aufruf in _merge_sweep() prüft, nicht den
    # Debounce-Pfad (der ohne den Fix bei sauberem Tree nie pushen würde).
    import subprocess

    from bibi.daemon import worktree as wt

    root = tmp_path / "r"
    root.mkdir()

    def g(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    g("init", "-q", "-b", "trunk")
    g("config", "user.email", "t@e.x")
    g("config", "user.name", "t")
    (root / "f.txt").write_text("base\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    p = wt.prepare(repo_root=root, work_dir=root / "data" / "wt", slug="x")
    (p / "n.md").write_text("hi\n")
    wt.commit(worktree=p, message="run", slug="x")

    calls = {"push": 0}

    def push_fn():
        calls["push"] += 1
        return (True, [], None)

    s = Synchronizer(push=True, repo_root=root, diff_stat=lambda: ("", 0),
                     push_fn=push_fn, pull_fn=lambda: (True, None))
    s.tick(0.0)
    assert calls["push"] == 1


def test_merge_sweep_does_not_push_when_nothing_merged(tmp_path):
    import subprocess

    root = tmp_path / "r"
    root.mkdir()

    def g(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    g("init", "-q", "-b", "trunk")
    g("config", "user.email", "t@e.x")
    g("config", "user.name", "t")
    (root / "f.txt").write_text("base\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")

    calls = {"push": 0}

    def push_fn():
        calls["push"] += 1
        return (True, [], None)

    s = Synchronizer(push=True, repo_root=root, diff_stat=lambda: ("", 0),
                     push_fn=push_fn, pull_fn=lambda: (True, None))
    s.tick(0.0)   # keine agent/*-Branches vorhanden ⇒ nichts gemergt
    assert calls["push"] == 0


def test_tick_merge_sweep_logs_stuck_conflict(tmp_path, caplog):
    """Bugfix 2026-07-05: ein Konflikt beim Merge-back darf nicht stumm
    verschwinden (verschleierte den dirty-trunk-Fund lange, s. Migration.md)."""
    import logging
    import subprocess

    from bibi.daemon import worktree as wt

    root = tmp_path / "r"
    root.mkdir()

    def g(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    g("init", "-q", "-b", "trunk")
    g("config", "user.email", "t@e.x")
    g("config", "user.name", "t")
    (root / "f.txt").write_text("base\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    # agent/x-Branch ändert f.txt ...
    p = wt.prepare(repo_root=root, work_dir=root / "data" / "wt", slug="x")
    (p / "f.txt").write_text("from agent\n")
    wt.commit(worktree=p, message="run", slug="x")
    # ... trunk ändert dieselbe Datei anders → echter Merge-Konflikt beim Sweep
    (root / "f.txt").write_text("from trunk\n")
    g("add", "-A")
    g("commit", "-q", "-m", "trunk edit")
    # Review-Runde 7 (Nachtrag): _merge_sweep() ruft mergeback.remerge_all() ohne
    # now=-Durchreichung auf — tick(0.0)s eigenes now steuert nur Push/Pull-Timing,
    # nicht den Idle-Guard im Merge-back selbst. Ohne Vordatierung würde Ebene 4s
    # Guard f.txt als "kürzlich bearbeitet" werten und den Sweep mit "live_edit"
    # statt einem echten Konflikt abschließen — os.utime() statt now=, weil hier
    # (wie bei der HTTP-Route) kein now=-Parameter bis zum Guard durchgereicht wird.
    import os
    import time
    stale = time.time() - 300
    os.utime(root / "f.txt", (stale, stale))

    s = Synchronizer(repo_root=root)  # kein push/pull, nur Sweep
    with caplog.at_level(logging.WARNING, logger="bibi.daemon.synchronizer"):
        s.tick(0.0)
    assert any(
        getattr(r, "bibi", {}).get("event") == "merge.sweep.stuck" for r in caplog.records
    )
    assert wt.is_ahead(repo_root=root, branch="agent/x", trunk="trunk")  # weiterhin unmerged


def test_tick_no_sweep_without_repo_root(team_repo):
    s, calls = _mk()           # repo_root=None ⇒ kein Sweep, kein Fehler
    s.tick(0.0)                # darf nicht werfen
