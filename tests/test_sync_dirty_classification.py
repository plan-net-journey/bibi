"""m.rau/bibi#160 + #161 — ein dirty Working Tree ist kein Merge-Konflikt.

Zwei Hälften desselben Flags, deshalb ein Posten (v0.7.1-Plan):

- **#160 ist die Quelle.** ``_classify_failure()`` kennt nur ``unreachable`` und
  ``auth`` und fällt für alles andere auf ``conflict`` durch. Ein ``git rebase``,
  das wegen uncommitteter Änderungen gar nicht erst anläuft, wird damit zum
  Merge-Konflikt erklärt — und ``/sync`` setzt ``sync_conflict``, das danach
  bei jedem Sitzungsstart warnt, ohne dass es etwas aufzulösen gäbe.
- **#161 ist der fehlende Ausgang.** ``run_abort()`` räumt das Flag nicht weg.

Die drei Meldungsformen unten sind am 2026-08-06 gegen echtes git gemessen,
nicht aus der Erinnerung notiert — zwei Varianten von ``rebase`` (unstaged vs.
Index) und die von ``merge``, wenn der Pull eine dirty Datei überschreiben
würde. Braucht kein Git-Repo, daher nicht ``slow``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bibi import git_ops, repo, state
from bibi.ctrl import sync_cmd
from bibi.daemon import mergeback


@pytest.fixture
def quiet_repo(monkeypatch):
    """Ein Repo ohne offene Vorgänge, ohne hängende Branches, ohne dirty Pfade —
    damit nur der Pull-Schritt von ``_run_sync_apply()`` übrig bleibt.
    Dieselbe Fixture wie in ``tests/test_sync_apply_guard.py``."""
    monkeypatch.setattr(git_ops, "is_rebase_in_progress", lambda: False)
    monkeypatch.setattr(git_ops, "is_merge_in_progress", lambda: False)
    monkeypatch.setattr(git_ops, "dirty_paths", lambda: [])
    monkeypatch.setattr(git_ops, "cluster_dirty_paths", lambda *_a, **_kw: (set(), False, []))
    monkeypatch.setattr(git_ops, "current_branch", lambda: "trunk")
    monkeypatch.setattr(mergeback, "unmerged_agent_branches", lambda **_kw: [])
    monkeypatch.setattr(repo, "root", lambda: Path("/tmp"))
    monkeypatch.setattr(repo, "case_dir_name", lambda: "case")
    monkeypatch.setattr(state, "get_path", lambda: None)

# --- gemessen gegen git 2.x, 2026-08-06 -------------------------------------

REBASE_UNSTAGED = ("error: cannot rebase: You have unstaged changes.\n"
                   "error: Please commit or stash them.")
REBASE_INDEX = ("error: cannot rebase: Your index contains uncommitted changes.\n"
                "error: Please commit or stash them.")
MERGE_WOULD_OVERWRITE = (
    "error: Your local changes to the following files would be overwritten by merge:\n"
    "\tf1.txt\n"
    "Please commit your changes or stash them before you merge.\n"
    "Aborting")


@pytest.mark.parametrize("stderr", [REBASE_UNSTAGED, REBASE_INDEX, MERGE_WOULD_OVERWRITE],
                         ids=["rebase-unstaged", "rebase-index", "merge-overwrite"])
def test_dirty_working_tree_ist_kein_conflict(stderr: str) -> None:
    """#160: git lehnt wegen uncommitteter Arbeit ab — das ist kein Konflikt."""
    assert git_ops._classify_failure(stderr) == "dirty"


def test_echter_konflikt_bleibt_conflict() -> None:
    """Gegenprobe: eine Meldung ohne dirty-Marker fällt weiter auf ``conflict``.

    Ohne diese Hälfte könnte der Fix jede Fehlermeldung zu ``dirty`` erklären
    und der Test wäre trotzdem grün."""
    assert git_ops._classify_failure("error: could not apply 0a1b2c3... irgendwas") == "conflict"


def test_unreachable_und_auth_bleiben_unberuehrt() -> None:
    assert git_ops._classify_failure("fatal: unable to access 'http://…': Could not resolve host") \
        == "unreachable"


def test_sync_apply_nimmt_bei_dirty_einen_eigenen_ausgang(quiet_repo, monkeypatch, capsys) -> None:
    """#160(a): ein *benannter* Ausgang, nicht nur „kein conflict".

    Dass ``sync_conflict`` ungesetzt bleibt, wäre allein kein Nachweis — das
    gilt heute schon, weil ``"dirty"`` in den generischen ``else``-Zweig fällt
    und dort als *„Abgleich fehlgeschlagen: dirty"* landet. Diese Meldung sagt
    dem Menschen nicht, was zu tun ist, und genau daran hängt das Ticket.
    Geprüft wird deshalb beides: kein Flag **und** eine Meldung, die den Weg
    nennt (dieselbe Lehre wie bei ``#165`` — ein Test, der nicht sagen kann,
    warum er grün ist, ist keiner)."""
    gesetzt: list[bool] = []
    monkeypatch.setattr(state, "set_sync_conflict", lambda v: gesetzt.append(v))
    monkeypatch.setattr(git_ops, "integrate", lambda *_a, **_kw: (False, "dirty"))

    rc = sync_cmd._run_sync_apply()

    assert rc == 1
    assert True not in gesetzt, "ein dirty Tree darf sync_conflict nicht setzen"
    err = capsys.readouterr().err
    assert "Abgleich fehlgeschlagen" not in err, \
        "der generische Zweig ist kein benannter Ausgang"
    assert "/save" in err, "die Meldung muss den Weg heraus nennen"


def _fake_git_welt(rebase_stderr: str):
    """Eine echte Divergenz gegen origin, ohne echtes git.

    Deterministisch statt ``slow``: ``tests/test_git_ops.py`` fährt echte
    Prozesse und läuft deshalb nur mit ``--slow`` — ein Rot-Schritt, den man
    nicht sehen kann, ist aber kein Nachweis. Gefaked wird nur, was
    ``_integrate_impl()`` tatsächlich fragt."""
    import subprocess

    def fake(args, check=True, timeout=None, **_kw):
        def done(rc=0, out="", err=""):
            return subprocess.CompletedProcess(args=["git", *args], returncode=rc,
                                               stdout=out, stderr=err)
        if args[0] == "fetch":
            return done()
        if args[:2] == ["rev-parse", "HEAD"]:
            return done(out="aaaaaaa\n")
        if args[:2] == ["rev-parse", "FETCH_HEAD"]:
            return done(out="bbbbbbb\n")
        if args[0] == "merge-base":
            return done(rc=1)          # weder voraus noch hinterher → echte Divergenz
        if args[0] == "rev-list":
            return done(out="1\n")
        if args[0] == "rebase":
            return done(rc=1, err=rebase_stderr)
        return done()
    return fake


def test_vorschau_sagt_dasselbe_wie_der_scharfe_lauf(monkeypatch) -> None:
    """#160(c): die Zusage des eigenen Docstrings von ``integrate_preview()``.

    Vorher liefen die beiden auseinander: der scharfe Lauf startete den Rebase
    und bekam von git die Absage, die Vorschau prüfte per ``merge-tree`` nur den
    *Inhalt* und meldete „geht sauber durch". Wer also erst schaute und dann
    ausführte, sah zwei verschiedene Antworten auf dieselbe Frage."""
    monkeypatch.setattr(git_ops, "is_conflict_resolution_pending", lambda: False)
    monkeypatch.setattr(git_ops, "_pull_live_overlap", lambda *_a, **_kw: False)
    monkeypatch.setattr(git_ops, "_pull_merge_tree", lambda _r: ([], False))
    monkeypatch.setattr(git_ops, "is_dirty", lambda: True)
    monkeypatch.setattr(git_ops, "_git", _fake_git_welt(REBASE_UNSTAGED))

    scharf_ok, scharf_kind = git_ops.integrate("trunk", keep_conflict=True)
    vorschau_ok, vorschau_kind, _, _ = git_ops.integrate_preview("trunk")

    assert (scharf_ok, scharf_kind) == (False, "dirty")
    assert (vorschau_ok, vorschau_kind) == (False, "dirty"), \
        "die Vorschau muss denselben Ausgang vorhersagen, den --apply nimmt"


def test_vorschau_bleibt_bei_sauberem_tree_unveraendert(monkeypatch) -> None:
    """Gegenprobe: ohne dirty Arbeit sagt die Vorschau weiter ihre Zahlen.

    Ohne sie könnte der Fix jede Vorschau auf ``dirty`` setzen."""
    monkeypatch.setattr(git_ops, "is_conflict_resolution_pending", lambda: False)
    monkeypatch.setattr(git_ops, "_pull_live_overlap", lambda *_a, **_kw: False)
    monkeypatch.setattr(git_ops, "_pull_merge_tree", lambda _r: ([], False))
    monkeypatch.setattr(git_ops, "is_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "_git", _fake_git_welt(REBASE_UNSTAGED))

    ok, kind, ahead, behind = git_ops.integrate_preview("trunk")

    assert (ok, kind) == (True, None)
    assert (ahead, behind) == (1, 1)


def test_run_abort_loescht_das_flag(monkeypatch) -> None:
    """#161: wer einen Konflikt beendet, beendet auch die Warnung."""
    gesetzt: list[bool] = []
    monkeypatch.setattr(state, "set_sync_conflict", lambda v: gesetzt.append(v))
    monkeypatch.setattr(git_ops, "is_merge_in_progress", lambda: False)
    monkeypatch.setattr(git_ops, "is_rebase_in_progress", lambda: True)
    monkeypatch.setattr(git_ops, "abort_rebase", lambda: None)

    rc = sync_cmd.run_abort(argparse.Namespace())

    assert rc == 0
    assert gesetzt == [False], "abort muss sync_conflict löschen"


def test_run_abort_loescht_das_flag_auch_ohne_offenen_vorgang(monkeypatch) -> None:
    """Der Fall aus dem Ticket: das Flag steht, der Vorgang nicht (mehr).

    Genau so entsteht die Warnung, die niemand auflösen kann — ``#160`` setzt
    das Flag ohne Rebase, und ohne diesen Ausgang bleibt es für immer stehen."""
    gesetzt: list[bool] = []
    monkeypatch.setattr(state, "set_sync_conflict", lambda v: gesetzt.append(v))
    monkeypatch.setattr(git_ops, "is_merge_in_progress", lambda: False)
    monkeypatch.setattr(git_ops, "is_rebase_in_progress", lambda: False)

    rc = sync_cmd.run_abort(argparse.Namespace())

    assert rc == 0
    assert gesetzt == [False]
