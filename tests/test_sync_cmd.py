"""Integrationstests für `bibi-ctrl sync …` (PLAN-1 §1.5, §4.9)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bibi import git_ops, state
from bibi.ctrl import main

import pytest
pytestmark = pytest.mark.slow

from conftest import FAR_FUTURE_TS


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _origin_head(origin: Path) -> str:
    return _sh(origin, "log", "-1", "--pretty=%s").strip()


def _local_head(root: Path) -> str:
    return _sh(root, "log", "-1", "--pretty=%s").strip()


def _clone(origin: Path, dest: Path) -> Path:
    _sh(dest.parent, "clone", "-q", str(origin), dest.name)
    _sh(dest, "config", "user.name", "O"); _sh(dest, "config", "user.email", "o@e.x")
    return dest


def _remote_ahead(origin: Path, tmp_path: Path, fname="remote.txt"):
    other = _clone(origin, tmp_path / "other")
    (other / fname).write_text("r", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote change")
    _sh(other, "push", "-q", "origin", "trunk")


def _diverge(origin: Path, tmp_path: Path):
    other = _clone(origin, tmp_path / "other")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit")
    _sh(other, "push", "-q", "origin", "trunk")


def _commit_local_edit(root: Path, text: str = "LOCAL\n") -> None:
    """Committet eine lokale Änderung an pyproject.toml UND datiert sie vor
    (Review-Runde 7, Nachtrag): `/sync`s eigener Pull-Schritt läuft mit
    `guard_live_paths=True` (Ebene 4/5, `git_ops._pull_live_overlap()`) — ohne
    Vordatierung wertet der Guard diese gerade committete Datei als "kürzlich
    bearbeitet" und überspringt den Pull (`live_edit`) statt in den von diesen
    Tests eigentlich gewollten Rebase-Konflikt zu laufen. `main(["sync"])`
    nimmt kein `now=`-Override entgegen (anders als `mergeback.merge_back()`
    in Tests, die die Funktion direkt aufrufen) — deshalb hier `os.utime()`
    statt `now=`, dieselbe Technik wie in `test_mergeback_route.py`."""
    import os
    import time
    (root / "pyproject.toml").write_text(text, encoding="utf-8")
    git_ops.stage_and_commit(None, "local edit")
    stale = time.time() - 300
    os.utime(root / "pyproject.toml", (stale, stale))


# --- on/off ---

def test_sync_on_off(repo_with_origin):
    assert main(["sync", "on"]) == 0
    assert state.get_auto_sync() is True
    assert main(["sync", "off"]) == 0
    assert state.get_auto_sync() is False


# --- manueller sync (§4.9) — committet seit PLAN-30 Ebene 5 NICHTS mehr,
# egal ob im aktiven Projekt oder in fremden Cases: nur noch anzeigen,
# committen ist ausschließlich /saves Aufgabe (löst Befund 2 / Requirement 4).

def test_sync_caseless_dirty_is_shown_not_committed(repo_with_origin, capsys):
    root, origin = repo_with_origin
    head_before = _local_head(root)
    (root / "x.txt").write_text("x", encoding="utf-8")
    rc = main(["sync"])
    assert rc == 0
    assert _local_head(root) == head_before  # kein neuer Commit
    assert _origin_head(origin) == head_before
    status = _sh(root, "status", "--porcelain")
    assert "x.txt" in status  # weiterhin dirty
    assert "save" in capsys.readouterr().err.lower()


def test_sync_other_case_is_shown_not_committed_active_untouched(repo_with_origin, monkeypatch, capsys):
    root, origin = repo_with_origin
    head_before = _local_head(root)
    active = root / "vault" / "case" / "20260101.active-aaa"
    other = root / "vault" / "case" / "20260202.other-bbb"
    active.mkdir(parents=True)
    other.mkdir(parents=True)
    (active / "README.md").write_text("wip", encoding="utf-8")
    (other / "README.md").write_text("other case change", encoding="utf-8")
    monkeypatch.chdir(active)

    rc = main(["sync"])
    assert rc == 0
    assert _origin_head(origin) == head_before  # nichts committet/gepusht
    # Bewusst mit Pfadangabe (nicht der unscoped `git status --porcelain` von
    # oben): beide Case-Ordner sind hier das allererste, je committete Material
    # unter vault/ — ein unscoped Aufruf würde sie git-Default-mäßig zu einer
    # einzigen "?? vault/"-Zeile zusammenfassen (live geprüft), unabhängig
    # davon, ob /sync die Pfade korrekt einzeln erkannt hat oder nicht. Mit
    # Pfadangabe wertet git jeden Case-Ordner gezielt aus und listet ihn
    # individuell, das ist die eigentlich zu prüfende Aussage.
    assert "vault/case/20260101.active-aaa/" in _sh(
        root, "status", "--porcelain", "--", "vault/case/20260101.active-aaa")
    assert "vault/case/20260202.other-bbb/" in _sh(
        root, "status", "--porcelain", "--", "vault/case/20260202.other-bbb")
    err = capsys.readouterr().err
    assert "20260202.other-bbb" in err  # angezeigt, nicht angefasst


def test_sync_no_active_case_shows_every_case_not_committed(repo_with_origin, capsys):
    root, origin = repo_with_origin
    head_before = _local_head(root)
    case = root / "vault" / "case" / "20260101.a-aaa"
    case.mkdir(parents=True)
    (case / "README.md").write_text("x", encoding="utf-8")
    rc = main(["sync"])
    assert rc == 0
    assert _origin_head(origin) == head_before
    assert "20260101.a-aaa" in capsys.readouterr().err


def test_sync_multiple_other_cases_all_shown_none_committed(repo_with_origin, tmp_path, capsys):
    root, origin = repo_with_origin
    head_before = _local_head(root)
    for slug in ("20260101.a-aaa", "20260202.b-bbb"):
        case = root / "vault" / "case" / slug
        case.mkdir(parents=True)
        (case / "README.md").write_text("x", encoding="utf-8")
    rc = main(["sync"])
    assert rc == 0
    assert _origin_head(origin) == head_before
    err = capsys.readouterr().err
    assert "20260101.a-aaa" in err
    assert "20260202.b-bbb" in err


def test_sync_clean_pulls(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _remote_ahead(origin, tmp_path)
    rc = main(["sync"])
    assert rc == 0
    assert (root / "remote.txt").exists()


def test_sync_pushes_local_ahead(repo_with_origin):
    root, origin = repo_with_origin
    (root / "a.txt").write_text("a", encoding="utf-8")
    git_ops.stage_and_commit(None, "local commit")
    rc = main(["sync"])
    assert rc == 0
    assert _origin_head(origin) == "local commit"


def test_sync_conflict_keeps_and_flags(repo_with_origin, tmp_path, capsys):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    _commit_local_edit(root)
    rc = main(["sync"])
    assert rc == 1
    assert state.get_sync_conflict() is True
    assert git_ops.is_rebase_in_progress() is True
    assert "pyproject.toml" in capsys.readouterr().err


def test_sync_continue_resolves_and_clears_flag(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    _commit_local_edit(root)
    main(["sync"])  # → Konflikt offen
    (root / "pyproject.toml").write_text("RESOLVED\n", encoding="utf-8")  # KI-Auflösung
    rc = main(["sync", "continue"])
    assert rc == 0
    assert git_ops.is_rebase_in_progress() is False
    assert state.get_sync_conflict() is False
    assert "RESOLVED" in _sh(origin, "show", "trunk:pyproject.toml")


def test_sync_abort_clears(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    _commit_local_edit(root)
    main(["sync"])
    rc = main(["sync", "abort"])
    assert rc == 0
    assert git_ops.is_rebase_in_progress() is False


def test_sync_in_progress_guard(repo_with_origin, tmp_path, capsys):
    root, origin = repo_with_origin
    _diverge(origin, tmp_path)
    _commit_local_edit(root)
    main(["sync"])           # Rebase offen
    rc = main(["sync"])      # erneuter sync → Guard
    assert rc == 1
    assert "continue" in capsys.readouterr().err


# --- PLAN-30 Ebene 3: eskalierte Job-Branch-Konflikte über /sync auflösen ---
# (Requirement 2 — anders als oben Requirement 3/Pull-Konflikte, aber
# dasselbe Werkzeug: sync/sync continue/sync abort.)

def _make_escalated_conflict(root: Path, slug: str = "c") -> None:
    """3 Fehlschläge gegen wechselnde trunk-Stände → hart eskaliert
    (merge_quarantine.ESCALATE_AFTER), wie im echten Vorfall (agent/Witz).

    Braucht BEIDE Techniken (Review-Runde 7, Nachtrag): now=FAR_FUTURE_TS auf
    den drei Schleifenaufrufen selbst — UND danach nochmal os.utime() auf die
    Datei, für den main(["sync"])-Aufruf der Aufrufer, das kein now= entgegen-
    nimmt. now= allein reicht nicht: jeder konfliktierende merge_back()-
    Aufruf ruft bei "conflict" intern git merge --abort auf, was den
    Working-Tree-Inhalt der Datei auf den trunk-Stand zurückschreibt und dabei
    ihre mtime auf "jetzt" zurücksetzt — ein einmaliges os.utime() VOR der
    Schleife hält also nur bis zum ersten Abort, live geprüft (Versuch 2
    scheiterte sonst wieder mit "live_edit"). os.utime() allein reicht auch
    nicht: es müsste dann vor JEDEM der drei Aufrufe erneut gesetzt werden,
    was dasselbe leistet wie now= aber unnötig kompliziert wäre — now= für die
    Schleife, os.utime() als Abschluss-Zustand für alles danach."""
    import os
    import time
    from bibi.daemon import mergeback, worktree as wt
    work = root / "data" / "worktrees"
    path = wt.prepare(repo_root=root, work_dir=work, slug=slug)
    (path / "pyproject.toml").write_text("JOB\n", encoding="utf-8")
    wt.commit(worktree=path, message=f"{slug}: run", slug=slug)
    (root / "pyproject.toml").write_text("TRUNK\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "trunk diverge")
    for i in range(3):
        res = mergeback.merge_back(repo_root=root, slug=slug, now=FAR_FUTURE_TS)
        assert res.status == "conflict", f"Versuch {i + 1} sollte konfliktieren"
        (root / f"advance{i}.txt").write_text("x\n", encoding="utf-8")
        git_ops.stage_and_commit(None, f"trunk advance {i}")
    stale = time.time() - 300
    os.utime(root / "pyproject.toml", (stale, stale))


def test_sync_resolves_escalated_merge_branch_conflict(repo_with_origin, capsys):
    from bibi.daemon import merge_quarantine
    root, _origin = repo_with_origin
    _make_escalated_conflict(root, "c")
    assert merge_quarantine.escalated(root) == ["agent/c"]

    rc = main(["sync"])
    assert rc == 1  # Konflikt offen, restlicher /sync-Ablauf nicht gelaufen
    assert git_ops.is_merge_in_progress() is True
    assert "agent/c" in capsys.readouterr().err


def test_sync_continue_resolves_merge_branch_and_clears_quarantine(repo_with_origin, tmp_path):
    from bibi.daemon import merge_quarantine
    root, origin = repo_with_origin
    _make_escalated_conflict(root, "c")
    main(["sync"])  # → Merge-Konflikt offen
    (root / "pyproject.toml").write_text("RESOLVED\n", encoding="utf-8")  # KI-Auflösung
    rc = main(["sync", "continue"])
    assert rc == 0
    assert git_ops.is_merge_in_progress() is False
    assert merge_quarantine.get(root, "agent/c") is None
    assert "RESOLVED" in _sh(origin, "show", "trunk:pyproject.toml")
    # Requirement 3s globales Flag bleibt unberührt von diesem Requirement-2-Pfad:
    assert state.get_sync_conflict() is False


def test_sync_abort_merge_branch_keeps_quarantine(repo_with_origin):
    from bibi.daemon import merge_quarantine
    root, _origin = repo_with_origin
    _make_escalated_conflict(root, "c")
    main(["sync"])  # → Merge-Konflikt offen
    rc = main(["sync", "abort"])
    assert rc == 0
    assert git_ops.is_merge_in_progress() is False
    # Branch bleibt unmerged + eskaliert — abort löst nichts, nur der Tree ist wieder sauber.
    assert merge_quarantine.get(root, "agent/c") is not None
    assert merge_quarantine.escalated(root) == ["agent/c"]


def test_sync_force_merges_escalated_branch_that_is_now_clean(repo_with_origin):
    # Eskaliert (3 Fehlschläge), aber der Konflikt ist inzwischen behoben (der
    # trunk-seitige Stand passt jetzt zur Job-Version) — /sync (force=True)
    # merged sofort sauber, kein offener Konflikt, keine Quarantäne mehr.
    from bibi.daemon import merge_quarantine
    root, origin = repo_with_origin
    _make_escalated_conflict(root, "c")
    (root / "pyproject.toml").write_text("JOB\n", encoding="utf-8")  # passt jetzt zur Job-Version
    git_ops.stage_and_commit(None, "trunk aligns with job")

    rc = main(["sync"])
    assert rc == 0
    assert merge_quarantine.get(root, "agent/c") is None
    assert "JOB" in _sh(origin, "show", "trunk:pyproject.toml")


def test_sync_ignores_orphaned_quarantine_entry_without_real_branch(repo_with_origin, capsys):
    # Nachtrag 2026-07-16: /sync fragt seit der /sync-Erweiterung NICHT mehr
    # merge_quarantine.escalated() (nur eskalierte Branches), sondern
    # mergeback.unmerged_agent_branches() (JEDER unmergte Branch, echte
    # git-Refs) — ein Quarantäne-Eintrag ohne zugehörigen echten Branch (hier:
    # "agent/almost" existiert als Ref gar nicht) taucht dort nie auf und wird
    # deshalb weiterhin ignoriert, unabhängig von seiner Fehlschlag-Zahl.
    # /sync läuft normal durch (Ebene 5: zeigt die dirty Datei nur an,
    # committet nicht mehr).
    from bibi.daemon import merge_quarantine
    root, _origin = repo_with_origin
    merge_quarantine.record_failure(root, "agent/almost", trunk_sha="deadbeef")
    (root / "x.txt").write_text("x", encoding="utf-8")
    rc = main(["sync"])
    assert rc == 0
    assert "x.txt" in _sh(root, "status", "--porcelain")
    assert "save" in capsys.readouterr().err.lower()


def _make_pending_conflict(root: Path, slug: str = "c", failures: int = 0) -> None:
    """Wie ``_make_escalated_conflict()``, aber mit frei wählbarer, absichtlich
    NICHT eskalierter Fehlschlagzahl (< ``ESCALATE_AFTER=3``) — für die
    /sync-Erweiterung (Nachtrag 2026-07-16): testet, dass `/sync` jetzt auch
    einen Branch anfasst, der die alte Eskalationsschwelle nie erreicht."""
    import os
    import time
    from bibi.daemon import mergeback, worktree as wt
    work = root / "data" / "worktrees"
    path = wt.prepare(repo_root=root, work_dir=work, slug=slug)
    (path / "pyproject.toml").write_text("JOB\n", encoding="utf-8")
    wt.commit(worktree=path, message=f"{slug}: run", slug=slug)
    (root / "pyproject.toml").write_text("TRUNK\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "trunk diverge")
    for i in range(failures):
        res = mergeback.merge_back(repo_root=root, slug=slug, now=FAR_FUTURE_TS)
        assert res.status == "conflict", f"Versuch {i + 1} sollte konfliktieren"
        (root / f"advance{i}.txt").write_text("x\n", encoding="utf-8")
        git_ops.stage_and_commit(None, f"trunk advance {i}")
    stale = time.time() - 300
    os.utime(root / "pyproject.toml", (stale, stale))


def test_sync_resolves_not_yet_escalated_merge_branch_conflict(repo_with_origin, capsys):
    # Kern der /sync-Erweiterung: ein Branch mit nur EINEM Fehlschlag (weit
    # unter ESCALATE_AFTER=3, laut altem Verhalten "nicht /syncs Sache") wird
    # jetzt trotzdem sofort angefasst — ein expliziter /sync-Aufruf ist selbst
    # die Zustimmung, nicht erst auf zufällige trunk-Bewegungen zu warten.
    from bibi.daemon import merge_quarantine
    root, _origin = repo_with_origin
    _make_pending_conflict(root, "c", failures=1)
    assert merge_quarantine.escalated(root) == []  # ausdrücklich NICHT eskaliert
    assert merge_quarantine.get(root, "agent/c").failures == 1

    rc = main(["sync"])
    assert rc == 1  # Konflikt offen, restlicher /sync-Ablauf nicht gelaufen
    assert git_ops.is_merge_in_progress() is True
    assert "agent/c" in capsys.readouterr().err


def test_sync_resolves_branch_with_zero_prior_failures(repo_with_origin, capsys):
    # Noch weiter unten in der Skala: ein Branch, der noch NIE versucht wurde
    # (kein Quarantäne-Eintrag überhaupt) — auch der ist jetzt sofort /syncs
    # Sache, nicht nur Ebene 1s (verpasster) Sofort-Versuch.
    from bibi.daemon import merge_quarantine
    root, _origin = repo_with_origin
    _make_pending_conflict(root, "c", failures=0)
    assert merge_quarantine.get(root, "agent/c") is None  # noch nie versucht

    rc = main(["sync"])
    assert rc == 1
    assert git_ops.is_merge_in_progress() is True
    assert "agent/c" in capsys.readouterr().err


def test_sync_continues_normal_flow_after_quiet_live_edit_branch(repo_with_origin, tmp_path, capsys):
    # Ein unmergter Branch, der GERADE (Idle-Guard, Ebene 4) nicht anfassbar
    # ist, darf den Rest von /sync (Pull/Push/Dirty-Anzeige) nicht blockieren
    # — anders als ein echter Konflikt hinterlässt "live_edit" keinen offenen
    # Zustand, der eine Pause rechtfertigt.
    from bibi.daemon import mergeback, worktree as wt
    root, origin = repo_with_origin
    work = root / "data" / "worktrees"
    path = wt.prepare(repo_root=root, work_dir=work, slug="c")
    (path / "pyproject.toml").write_text("JOB\n", encoding="utf-8")
    wt.commit(worktree=path, message="c: run", slug="c")
    (root / "pyproject.toml").write_text("TRUNK\n", encoding="utf-8")
    git_ops.stage_and_commit(None, "trunk diverge")
    # pyproject.toml bewusst NICHT vordatiert — bleibt "kürzlich bearbeitet",
    # Ebene 4s Guard soll hier genau das erkennen (live_edit).

    rc = main(["sync"])
    assert rc == 0  # kein Show-Stopper — normaler Ablauf lief bis zum Ende
    assert git_ops.is_merge_in_progress() is False  # kein echter Versuch geöffnet
    captured = capsys.readouterr()
    assert "agent/c" in captured.err and "live_edit" in captured.err
    assert "sync ok" in captured.out  # normaler Ablauf ist tatsächlich bis zum Ende gelaufen


# --- PLAN-30 Ebene 4/5: Idle-Fenster-Guard schützt /syncs eigenen Pull-Schritt ---

def test_sync_pull_skips_when_target_path_is_dirty(repo_with_origin, tmp_path, capsys):
    root, origin = repo_with_origin
    other = _clone(origin, tmp_path / "other")
    (other / "pyproject.toml").write_text("REMOTE\n", encoding="utf-8")
    _sh(other, "add", "-A"); _sh(other, "commit", "-q", "-m", "remote edit")
    _sh(other, "push", "-q", "origin", "trunk")

    (root / "pyproject.toml").write_text("LOCAL dirty\n", encoding="utf-8")  # nicht committet
    head_before = _local_head(root)
    rc = main(["sync"])
    assert rc == 1
    assert _local_head(root) == head_before  # kein Pull-Versuch fand statt
    assert (root / "pyproject.toml").read_text() == "LOCAL dirty\n"  # unangetastet
    err = capsys.readouterr().err.lower()
    assert "übersprungen" in err or "sync" in err


def test_sync_pull_proceeds_when_no_overlap(repo_with_origin, tmp_path):
    # Ein unbeteiligter dirty Pfad darf einen ansonsten sauberen Pull nicht
    # verhindern — der Guard prüft nur echten Überlapp.
    root, origin = repo_with_origin
    _remote_ahead(origin, tmp_path)
    (root / "unrelated.txt").write_text("dirty, aber nicht Teil des Pulls\n",
                                        encoding="utf-8")
    rc = main(["sync"])
    assert rc == 0
    assert (root / "remote.txt").exists()


# --- Hooks (gated by auto_sync) ---

def test_hook_stop_noop_when_off(repo_with_origin):
    root, origin = repo_with_origin
    (root / "x.txt").write_text("x", encoding="utf-8")
    assert main(["sync", "hook-stop"]) == 0
    assert _origin_head(origin) == "init"   # nichts passiert


def test_hook_stop_commits_and_pushes_when_on(repo_with_origin):
    root, origin = repo_with_origin
    state.set_auto_sync(True)
    (root / "x.txt").write_text("x", encoding="utf-8")
    assert main(["sync", "hook-stop"]) == 0
    assert _origin_head(origin).startswith("auto:")  # transienter Auto-Commit


def test_hook_start_pulls_when_on(repo_with_origin, tmp_path):
    root, origin = repo_with_origin
    state.set_auto_sync(True)
    _remote_ahead(origin, tmp_path)
    main(["sync", "hook-start"])
    assert (root / "remote.txt").exists()


def test_hook_start_warns_on_conflict_flag(repo_with_origin, capsys):
    root, origin = repo_with_origin
    state.set_sync_conflict(True)
    rc = main(["sync", "hook-start"])
    assert rc == 1
    assert "sync" in capsys.readouterr().err.lower()
