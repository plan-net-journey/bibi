"""Synchronizer: Debounce-Logik + Tick (DESIGN §4.3, PLAN-2 §2.3)."""

from __future__ import annotations

import pytest

from bibi import state
from bibi.daemon.synchronizer import PushDebouncer, Synchronizer, params_for


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
