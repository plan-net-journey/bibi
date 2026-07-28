"""``/sync`` und Ebene 4s Idle-Guard — Revision 2026-07-28, Befund 1.

Der Pull-Schritt galt als "automatischer Schreibvorgang, nur manuell
angestoßen" und bekam deshalb denselben Idle-Guard wie der Hintergrund-Loop.
Im Vorfall war die blockierende Datei die Ausgabedatei eines 10-Minuten-
Schedules: der Guard hielt damit nicht einen tippenden Menschen fern, sondern
den eigenen Scheduler — und weil er im Daemon-Loop ebenso greift, war die
Divergenz über keinen der beiden Wege mehr auflösbar. Jetzt gilt für den
expliziten Aufruf derselbe Grundsatz wie in Schritt 0: der anwesende Mensch IST
die Zustimmung.

Braucht kein Git-Repo, daher NICHT ``slow`` — anders als tests/test_sync_cmd.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import git_ops, repo, state
from bibi.ctrl import sync_cmd
from bibi.daemon import mergeback


@pytest.fixture
def quiet_repo(monkeypatch):
    """Ein Repo ohne offene Vorgänge, ohne hängende Branches, ohne dirty Pfade —
    damit nur der Pull/Push-Teil von ``_run_sync_apply()`` übrig bleibt."""
    monkeypatch.setattr(git_ops, "is_rebase_in_progress", lambda: False)
    monkeypatch.setattr(git_ops, "is_merge_in_progress", lambda: False)
    monkeypatch.setattr(git_ops, "dirty_paths", lambda: [])
    monkeypatch.setattr(git_ops, "cluster_dirty_paths", lambda *_a, **_kw: (set(), False, []))
    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(mergeback, "unmerged_agent_branches", lambda **_kw: [])
    monkeypatch.setattr(repo, "root", lambda: Path("/tmp"))
    monkeypatch.setattr(repo, "case_dir_name", lambda: "case")
    monkeypatch.setattr(state, "get_path", lambda: None)
    monkeypatch.setattr(state, "set_sync_conflict", lambda _v: None)


def test_apply_keeps_dirty_guard_but_drops_the_time_heuristic(quiet_repo, monkeypatch):
    """Die beiden Hälften des Guards schützen Unterschiedliches: "dirty" ist
    echte uncommittete Arbeit und bleibt tabu, "kürzlich geschrieben" ist eine
    Heuristik für einen tippenden Menschen — und genau der ruft hier gerade
    ``/sync`` auf."""
    seen: dict = {}

    def fake_integrate(branch, keep_conflict=False, **kw):
        seen.update(kw)
        seen["keep_conflict"] = keep_conflict
        return True, None

    monkeypatch.setattr(git_ops, "integrate", fake_integrate)
    monkeypatch.setattr(git_ops, "push", lambda _b, **_kw: (True, "ok", None))

    assert sync_cmd._run_sync_apply() == 0
    assert seen["guard_live_paths"] is True, "unfertige Arbeit bleibt geschützt"
    assert seen["live_within_s"] == 0, \
        "das Zeitfenster blockiert den expliziten Aufruf nicht mehr (Befund 1)"
    assert seen["keep_conflict"] is True, "ein Konflikt bleibt für die Auflösung im Tree"


def test_preview_predicts_the_same_run(quiet_repo, monkeypatch):
    """Die Vorschau muss denselben Lauf vorhersagen, den ``--apply`` fährt —
    mit dem vollen Zeitfenster hätte sie ein Überspringen angekündigt, das gar
    nicht mehr eintritt."""
    seen: dict = {}

    def fake_preview(branch, **kw):
        seen.update(kw)
        return True, None, 0, 0

    monkeypatch.setattr(git_ops, "integrate_preview", fake_preview)

    sync_cmd._run_sync_preview()
    assert seen["guard_live_paths"] is True
    assert seen["live_within_s"] == 0


def test_skipped_pull_names_the_blocking_paths(quiet_repo, monkeypatch, capsys):
    """Befund 1, zweiter Teil: "Zieldatei wird bearbeitet" nannte weder die
    Datei noch einen Weg nach vorn."""
    monkeypatch.setattr(git_ops, "integrate", lambda *_a, **_kw: (False, "live_edit"))
    monkeypatch.setattr(git_ops, "live_overlap_report",
                        lambda **_kw: [("vault/case/x/notiz.md", 3.0)])

    assert sync_cmd._run_sync_apply() == 1
    err = capsys.readouterr().err
    assert "vault/case/x/notiz.md" in err
    assert "/save" in err


def test_push_conflict_still_sets_the_flag(quiet_repo, monkeypatch):
    """``push()`` liefert seine Klassifikation jetzt selbst; ein echter Konflikt
    muss weiterhin ``sync_conflict`` setzen (Befund 2 durfte das nicht kosten)."""
    flag: dict = {}
    monkeypatch.setattr(state, "set_sync_conflict", lambda v: flag.__setitem__("value", v))
    monkeypatch.setattr(git_ops, "integrate", lambda *_a, **_kw: (True, None))
    monkeypatch.setattr(git_ops, "push", lambda _b, **_kw: (False, "rejected", "conflict"))

    assert sync_cmd._run_sync_apply() == 1
    assert flag["value"] is True


def test_push_live_edit_does_not_set_the_flag(quiet_repo, monkeypatch):
    """Der Gegenfall — ein Idle-Skip im Push-Retry ist kein Konflikt und darf
    das Flag nicht mehr setzen. Genau das ging vor dem Fix schief, weil der
    abgebrochene Rebase als ``conflict`` klassifiziert wurde."""
    flag: dict = {}
    monkeypatch.setattr(state, "set_sync_conflict", lambda v: flag.__setitem__("value", v))
    monkeypatch.setattr(git_ops, "integrate", lambda *_a, **_kw: (True, None))
    monkeypatch.setattr(git_ops, "push", lambda _b, **_kw: (False, "skipped", "live_edit"))

    assert sync_cmd._run_sync_apply() == 1
    assert "value" not in flag
