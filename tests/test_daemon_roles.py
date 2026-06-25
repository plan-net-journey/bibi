"""Rollen-Auflösung & Invarianten des Daemons (DESIGN §4.2, PLAN-2 §2.1)."""

from __future__ import annotations

from bibi.daemon import roles


def test_parse_role_env_splits_and_trims():
    assert roles.parse_role_env("worker, synchronizer") == {"worker", "synchronizer"}
    assert roles.parse_role_env("") == set()
    assert roles.parse_role_env("  scheduler ") == {"scheduler"}


def test_parse_role_env_ignores_unknown():
    # Unbekannte Tokens werden verworfen (defensiv), bekannte bleiben.
    assert roles.parse_role_env("worker,bogus") == {"worker"}


def test_resolve_synchronizer_defaults_to_pull():
    r = roles.resolve({"synchronizer"})
    assert r.synchronizer and not r.scheduler and not r.worker
    assert r.pull and not r.push  # ohne --push: nur Pull (uni-direktional, §4.3)


def test_resolve_push_implies_pull():
    r = roles.resolve({"synchronizer"}, push=True)
    assert r.push and r.pull  # --push schließt --pull ein (§4.3)


def test_resolve_connect_modifier():
    r = roles.resolve({"synchronizer"}, connect=True)
    assert r.connect


def test_validate_scheduler_excludes_connect():
    errs = roles.validate(roles.resolve({"scheduler"}, connect=True))
    assert any("connect" in e.lower() for e in errs)


def test_validate_clean_synchronizer_has_no_errors():
    assert roles.validate(roles.resolve({"synchronizer"})) == []


def test_scheduler_worker_startable_connect_not_yet():
    # Ab Stufe 3.0 starten scheduler/worker (Vertrag als Stubs); nur connect fehlt.
    assert roles.unsupported(roles.resolve({"scheduler"})) == []
    assert roles.unsupported(roles.resolve({"worker"})) == []
    assert roles.unsupported(roles.resolve({"synchronizer"})) == []
    r = roles.resolve({"synchronizer"}, connect=True)
    assert "connect" in roles.unsupported(r)


def test_no_role_is_valid_but_idle():
    r = roles.resolve(set())
    assert not any([r.synchronizer, r.scheduler, r.worker])
    assert roles.validate(r) == []
