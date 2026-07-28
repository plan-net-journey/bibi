"""``push()``s Reject-Retry — Revision 2026-07-28, Befund 2.

Der Retry lief bis dahin als roher ``git pull --rebase`` direkt über ``_git()``:
am Idle-Guard vorbei, an ``is_conflict_resolution_pending()`` vorbei, und mit
einer Fehlermeldung ("rebase failed (aborted)"), die der Aufrufer per
``_classify_failure()`` erst wieder erraten musste — was einen bloßen Idle-Skip
ununterscheidbar von einem echten Konflikt machte und im Vorfall ein falsches
``sync_conflict`` setzte.

Braucht kein Git-Repo (``_git``/``_integrate_impl`` sind gemockt), daher
bewusst NICHT ``slow`` — anders als tests/test_git_ops.py."""

from __future__ import annotations

import subprocess

import pytest

from bibi import git_ops

_REJECT = "! [rejected]        trunk -> trunk (non-fast-forward)"


def _proc(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=["git"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


@pytest.fixture
def only_push_allowed(monkeypatch):
    """``_git`` darf nur noch ``push`` sehen: jeder andere rohe git-Aufruf ist
    genau der Regelbruch, den Befund 2 beschreibt, und lässt den Test scheitern."""
    seen: list[list[str]] = []

    def fake_git(args, **_kw):
        seen.append(list(args))
        if args and args[0] == "push":
            return _proc(1, stderr=_REJECT)
        raise AssertionError(f"roher git-Aufruf am geschützten Weg vorbei: {args}")

    monkeypatch.setattr(git_ops, "_git", fake_git)
    return seen


def test_idle_skip_is_reported_as_live_edit_not_conflict(only_push_allowed, monkeypatch):
    monkeypatch.setattr(git_ops, "_integrate_impl",
                        lambda branch, **_kw: (False, "live_edit", None, None))

    ok, msg, kind = git_ops.push("trunk", guard_live_paths=True)

    assert ok is False
    assert kind == "live_edit", "ein Idle-Skip darf nie als Konflikt durchgehen"
    assert "live_edit" in msg


def test_real_conflict_still_classified_as_conflict(only_push_allowed, monkeypatch):
    monkeypatch.setattr(git_ops, "_integrate_impl",
                        lambda branch, **_kw: (False, "conflict", None, None))

    ok, _msg, kind = git_ops.push("trunk")

    assert ok is False
    assert kind == "conflict"


def test_guard_flag_reaches_the_integration_step(only_push_allowed, monkeypatch):
    seen: dict = {}

    def fake_integrate(branch, **kw):
        seen.update(kw)
        return False, "live_edit", None, None

    monkeypatch.setattr(git_ops, "_integrate_impl", fake_integrate)

    git_ops.push("trunk", guard_live_paths=True)
    assert seen["guard_live_paths"] is True

    seen.clear()
    git_ops.push("trunk")
    assert seen["guard_live_paths"] is False, "Default bleibt das interaktive Verhalten"


def test_retry_pushes_again_after_successful_integration(monkeypatch):
    results = iter([_proc(1, stderr=_REJECT), _proc(0, stdout="Everything up-to-date")])
    pushes: list[list[str]] = []

    def fake_git(args, **_kw):
        if args and args[0] == "push":
            pushes.append(list(args))
            return next(results)
        raise AssertionError(f"roher git-Aufruf am geschützten Weg vorbei: {args}")

    monkeypatch.setattr(git_ops, "_git", fake_git)
    monkeypatch.setattr(git_ops, "_integrate_impl",
                        lambda branch, **_kw: (True, None, None, None))

    ok, msg, kind = git_ops.push("trunk")

    assert (ok, kind) == (True, None)
    assert len(pushes) == 2, "nach erfolgreicher Integration wird genau einmal erneut gepusht"
    assert "up-to-date" in msg


def test_clean_push_needs_no_integration(monkeypatch):
    monkeypatch.setattr(git_ops, "_git", lambda args, **_kw: _proc(0, stdout="pushed"))

    def explode(*_a, **_kw):
        raise AssertionError("ohne Reject darf gar nicht integriert werden")

    monkeypatch.setattr(git_ops, "_integrate_impl", explode)

    assert git_ops.push("trunk") == (True, "pushed", None)


def test_non_reject_failure_is_classified_without_integration(monkeypatch):
    monkeypatch.setattr(git_ops, "_git",
                        lambda args, **_kw: _proc(1, stderr="Could not resolve host: gitea"))

    def explode(*_a, **_kw):
        raise AssertionError("ein Netzfehler ist kein Reject — kein Integrationsversuch")

    monkeypatch.setattr(git_ops, "_integrate_impl", explode)

    ok, _msg, kind = git_ops.push("trunk")

    assert (ok, kind) == (False, "unreachable")
