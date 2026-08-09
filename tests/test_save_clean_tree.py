"""``save --push`` bei sauberem Arbeitsbaum (m.rau/bibi#66).

Der Guard verweigert den Repo-Scope, wenn Park-Marken anderer Sessions auf
einen Case zeigen — **auch dann, wenn der Arbeitsbaum sauber ist und der Commit
null Dateien anfassen wuerde.** Nach jeder Wiederverbindung ist das der
Normalfall, nicht der Randfall: die Session-ID wechselt, die alte Marke bleibt
liegen. Wer nur noch pushen will, zahlt jedes Mal einen Fehlversuch.

Die Begruendung des Guards ist fuer sich richtig — *„ein Repo-weiter Commit
nimmt in dieser Instanz fremde, halbfertige Arbeit mit"* — aber sie setzt
voraus, dass es fremde Arbeit **gibt**. Bei null geaenderten Pfaden ist die
Menge leer, und der Guard schuetzt vor einem Schaden, der nicht eintreten kann.

**Der Fallstrick steht im Ticket und ist der eigentliche Inhalt dieser Datei:**
``_dirty_count()`` taugt nicht als Grundlage. Es faengt jede Exception und
liefert dann ``0`` — wer den Guard daran haengt, baut die Umkehrung: bei einem
git-Fehler meldet es „nichts zu tun", der Guard laesst durch, und der
Repo-Commit sammelt fremde Arbeit ein. In genau der Lage, vor der er schuetzen
soll. Die Pruefung fuer den Guard muss im Fehlerfall **sperren**.
"""

from __future__ import annotations

import argparse

import pytest

from bibi import git_ops, repo, state
from bibi.ctrl import save_cmd


@pytest.fixture
def fremde_marke(team_repo, monkeypatch):
    """Ein Repo ohne aktiven Case, aber mit der Marke einer anderen Sitzung."""
    fremd = "case/20260809.Fremd-bbb222"
    (repo.vault() / fremd).mkdir(parents=True)
    park = repo.data() / "park"
    park.mkdir(parents=True, exist_ok=True)
    (park / "andere-session").write_text(fremd, encoding="utf-8")
    monkeypatch.setenv("BIBI_SESSION_ID", "die-jetzige")
    monkeypatch.setattr(state, "_adopted_session", None, raising=False)
    state.set_path(None)
    return team_repo


def _args(**kw):
    basis = dict(repo=False, push=True, message=None)
    basis.update(kw)
    return argparse.Namespace(**basis)


def test_a_clean_tree_pushes_despite_foreign_markers(fremde_marke, monkeypatch):
    """**Der Rot-Schritt von #66.**

    Nichts einzusammeln, also nichts zu verwechseln — der Scope ist keine
    Vermutung mehr, sondern leer."""
    monkeypatch.setattr(git_ops, "dirty_paths", lambda: [])
    monkeypatch.setattr(git_ops, "commit_and_push",
                        lambda *a, **kw: (True, ["push ok"], None))
    rc = save_cmd.run(_args())
    assert rc == 0, (
        "sauberer Arbeitsbaum, und save --push verweigert trotzdem — der Guard "
        "entscheidet, bevor er weiss, ob es etwas zu entscheiden gibt (#66)")


def test_a_dirty_tree_is_still_refused(fremde_marke, monkeypatch):
    """Die Gegenprobe aus dem Ticket: der Guard darf nicht ausgehebelt, nur auf
    die Lage eingeschraenkt werden, fuer die seine Begruendung gilt."""
    monkeypatch.setattr(git_ops, "dirty_paths",
                        lambda: ["vault/case/20260809.Fremd-bbb222/README.md"])
    rc = save_cmd.run(_args())
    assert rc == 2, (
        "es liegt fremde Arbeit vor — der Repo-Scope bleibt eine Vermutung")


def test_a_git_error_locks_instead_of_letting_through(fremde_marke, monkeypatch):
    """**Der Fallstrick, und er ist die Umkehrung des Fixes.**

    ``_dirty_count()`` verschluckt jede Exception zu ``0``. Wer den Guard daran
    haengt, laesst bei einem git-Fehler durch — und der Repo-Commit sammelt
    fremde Arbeit ein, in genau der Lage, vor der er schuetzen soll. Die
    Pruefung fuer den Guard nimmt die entgegengesetzte Defensivrichtung.
    """
    def _explodiert():
        raise RuntimeError("git kaputt")

    monkeypatch.setattr(git_ops, "dirty_paths", _explodiert)
    rc = save_cmd.run(_args())
    assert rc == 2, (
        "git antwortet nicht, und der Guard laesst durch — dann sammelt der "
        "Repo-Commit fremde Arbeit ein (#66, Fallstrick)")


def test_an_explicit_repo_scope_is_untouched(fremde_marke, monkeypatch):
    """``--repo`` war nie vom Guard betroffen und bleibt es nicht."""
    monkeypatch.setattr(git_ops, "dirty_paths", lambda: ["vault/x.md"])
    monkeypatch.setattr(git_ops, "commit_and_push",
                        lambda *a, **kw: (True, ["push ok"], None))
    assert save_cmd.run(_args(repo=True)) == 0
