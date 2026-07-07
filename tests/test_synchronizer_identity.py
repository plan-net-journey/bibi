"""_default_push()-Identitäts-Pass-through (PLAN-21 Befund 8).

Bewusst getrennt von tests/test_daemon_synchronizer.py (dort pytestmark =
slow) — dieser Test braucht kein Git-Repo, nur ein Monkeypatch von
git_ops.commit_and_push, darum als eigene schnelle Datei."""

from __future__ import annotations

from bibi import git_ops
from bibi.daemon import synchronizer


def test_default_push_passes_bibi_sync_identity(monkeypatch):
    captured = {}

    def fake_commit_and_push(scope, message, do_push, identity=None):
        captured["identity"] = identity
        return True, ["ok"], None

    monkeypatch.setattr(git_ops, "commit_and_push", fake_commit_and_push)

    ok, log, kind = synchronizer._default_push()

    assert ok is True
    assert captured["identity"] == ("bibi/sync", "bibi@local")
