"""Boot-Signale (m.rau/bibi#39).

Nach dem Einwand von m.rau (2026-07-30) ist hier nur noch ``reset`` übrig: der
Deploy-Pull läuft im Request, nicht als Boot-Signal (s. Modul-Docstring von
``boot_signal``). Was hier zählt, ist dass ein Signal den Prozess überlebt,
genau einmal wirkt und auch im Fehlerfall verschwindet — eine Neustart-Schleife
wäre der teuerste Fehlerfall, weil sie den Knoten dauerhaft aus dem Betrieb
nimmt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi.daemon import boot_signal as bs


def test_request_and_pending_roundtrip(tmp_path: Path):
    assert bs.pending(tmp_path) == []
    bs.request("reset", tmp_path)
    assert bs.pending(tmp_path) == ["reset"]


def test_request_is_idempotent(tmp_path: Path):
    bs.request("reset", tmp_path)
    bs.request("reset", tmp_path)
    assert bs.pending(tmp_path) == ["reset"]


def test_unknown_kind_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        bs.request("frobnicate", tmp_path)


def test_deployment_is_no_longer_a_boot_signal(tmp_path: Path):
    # Der Pull gehört in den Request: liegt die neue Lock vor dem ERSTEN
    # Neustart im Checkout, genügt ein Durchlauf statt zweier.
    with pytest.raises(ValueError):
        bs.request("deployment", tmp_path)


def test_apply_without_signal_does_not_ask_for_restart(tmp_path: Path):
    # Der Normalfall: jeder gewöhnliche Start läuft hier durch, ohne etwas zu
    # tun — sonst wäre der Daemon dauerhaft in einer Neustart-Schleife.
    assert bs.apply_and_clear(tmp_path) is False


def test_reset_removes_venv_and_requests_restart(tmp_path: Path):
    venv = tmp_path / ".venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "lib" / "x").write_text("y", encoding="utf-8")

    bs.request("reset", tmp_path)
    assert bs.apply_and_clear(tmp_path) is True
    assert not venv.exists()
    # Signal weg: der nächste Start ist ein gewöhnlicher.
    assert bs.pending(tmp_path) == []


def test_signal_is_cleared_even_if_venv_is_missing(tmp_path: Path):
    # Kein venv da (z. B. schon entfernt): kein Fehler, und das Signal bleibt
    # nicht liegen.
    bs.request("reset", tmp_path)
    assert bs.apply_and_clear(tmp_path) is True
    assert bs.pending(tmp_path) == []


def test_signal_is_cleared_before_the_work_happens(tmp_path: Path, monkeypatch):
    # Absicht: erst löschen, dann arbeiten. Stürzt die Arbeit ab, bleibt kein
    # Signal liegen, das beim nächsten Start dasselbe erneut anstößt.
    seen: list[list[str]] = []

    def fake_rmtree(path, **kw):
        seen.append(bs.pending(tmp_path))

    monkeypatch.setattr(bs.shutil, "rmtree", fake_rmtree)
    (tmp_path / ".venv").mkdir()
    bs.request("reset", tmp_path)
    bs.apply_and_clear(tmp_path)
    assert seen == [[]]
