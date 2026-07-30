"""Boot-Signale und der Doppel-Neustart (m.rau/bibi#39).

Geprüft wird die Mechanik, nicht git: der Pull ist gemockt. Was hier zählt, ist
dass Signale den Prozess überleben, genau einmal wirken und auch im Fehlerfall
verschwinden — eine Neustart-Schleife wäre der teuerste Fehlerfall, weil sie den
Knoten dauerhaft aus dem Betrieb nimmt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi.daemon import boot_signal as bs


def test_request_and_pending_roundtrip(tmp_path: Path):
    assert bs.pending(tmp_path) == []
    bs.request("deployment", tmp_path)
    assert bs.pending(tmp_path) == ["deployment"]


def test_request_is_idempotent(tmp_path: Path):
    bs.request("deployment", tmp_path)
    bs.request("deployment", tmp_path)
    assert bs.pending(tmp_path) == ["deployment"]


def test_unknown_kind_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        bs.request("frobnicate", tmp_path)


def test_apply_without_signal_does_not_ask_for_restart(tmp_path: Path):
    # Der Normalfall: jeder gewöhnliche Start läuft hier durch, ohne etwas zu
    # tun — sonst wäre der Daemon dauerhaft in einer Neustart-Schleife.
    assert bs.apply_and_clear(tmp_path) is False


def test_deployment_pulls_and_requests_restart(tmp_path: Path, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(bs, "_pull", lambda root: (calls.append(root), (True, None))[1])
    bs.request("deployment", tmp_path)
    assert bs.apply_and_clear(tmp_path) is True
    assert calls == [tmp_path]
    # Signal weg: der nächste Start ist ein gewöhnlicher.
    assert bs.pending(tmp_path) == []


def test_signal_is_cleared_before_the_work_happens(tmp_path: Path, monkeypatch):
    # Absicht: erst löschen, dann arbeiten. Stürzt die Arbeit ab, bleibt kein
    # Signal liegen, das beim nächsten Start dasselbe erneut anstößt.
    seen: list[list[str]] = []

    def fake_pull(root):
        seen.append(bs.pending(root))
        return True, None

    monkeypatch.setattr(bs, "_pull", fake_pull)
    bs.request("deployment", tmp_path)
    bs.apply_and_clear(tmp_path)
    assert seen == [[]]


def test_failed_pull_still_clears_and_restarts(tmp_path: Path, monkeypatch):
    # Der Knoten läuft dann auf dem alten Stand weiter — richtig so, aber ohne
    # Schleife. Sichtbar wird der Fehlschlag über activity, nicht über ein
    # liegengebliebenes Signal.
    monkeypatch.setattr(bs, "_pull", lambda root: (False, "conflict"))
    bs.request("deployment", tmp_path)
    assert bs.apply_and_clear(tmp_path) is True
    assert bs.pending(tmp_path) == []


def test_pull_exception_does_not_escape(tmp_path: Path, monkeypatch):
    def boom(root):
        raise RuntimeError("git weg")

    monkeypatch.setattr(bs, "_pull", boom)
    bs.request("deployment", tmp_path)
    # Ein Fehler beim Pull darf den Neustart nicht verlieren.
    assert bs.apply_and_clear(tmp_path) is True


def test_reset_removes_venv_and_implies_deployment(tmp_path: Path, monkeypatch):
    pulled: list[Path] = []
    monkeypatch.setattr(bs, "_pull", lambda root: (pulled.append(root), (True, None))[1])
    venv = tmp_path / ".venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "lib" / "x").write_text("y", encoding="utf-8")

    bs.request("reset", tmp_path)
    assert bs.apply_and_clear(tmp_path) is True
    assert not venv.exists()
    # reset impliziert deployment: ein neues venv entsteht gegen die aktuelle
    # Lock, also wird vorher gepullt.
    assert pulled == [tmp_path]


def test_reset_and_deployment_together_pull_once(tmp_path: Path, monkeypatch):
    pulled: list[Path] = []
    monkeypatch.setattr(bs, "_pull", lambda root: (pulled.append(root), (True, None))[1])
    bs.request("deployment", tmp_path)
    bs.request("reset", tmp_path)
    bs.apply_and_clear(tmp_path)
    assert len(pulled) == 1
