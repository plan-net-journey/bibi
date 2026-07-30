"""Welche Engine läuft hier? (m.rau/bibi#19)

Die Tests gehen nicht über ein echtes Paket, sondern über eine gefälschte
Distribution — was geprüft werden soll, ist das Ableiten der Bezeichnung aus
PEP-610-Metadaten, nicht die Installationsmechanik von uv.
"""

from __future__ import annotations

import json

import pytest

from bibi import engine_info as ei


class _FakeDist:
    def __init__(self, version: str | None, direct_url: object = None) -> None:
        self.version = version
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        if name != "direct_url.json":
            return None
        if self._direct_url is None:
            return None
        if isinstance(self._direct_url, str):
            return self._direct_url          # kaputtes JSON simulieren
        return json.dumps(self._direct_url)


def _patch(monkeypatch, dist) -> None:
    import importlib.metadata as md

    def fake(name: str):
        if dist is None:
            raise md.PackageNotFoundError(name)
        return dist

    monkeypatch.setattr(md, "distribution", fake)


def test_tag_pinning_label_is_just_the_tag(monkeypatch):
    # Der Normalfall nach der Release-Umstellung: gepinnt auf einen Tag. Ref und
    # Version sagen dasselbe, die Anzeige soll es nicht doppelt zeigen.
    _patch(monkeypatch, _FakeDist("0.2.0", {
        "url": "http://host/m.rau/bibi.git",
        "vcs_info": {"vcs": "git", "commit_id": "86ea20e6881", "requested_revision": "v0.2.0"},
    }))
    info = ei.engine_info()
    assert info.version == "0.2.0"
    assert info.ref == "v0.2.0"
    assert info.short_commit == "86ea20e"
    assert info.editable is False
    assert info.label() == "v0.2.0"


def test_branch_pinning_label_carries_the_commit(monkeypatch):
    # Ein Branch wandert — sein Name allein sagt nichts darüber, welcher Stand
    # tatsächlich installiert ist. Deshalb hier zusätzlich der Commit.
    _patch(monkeypatch, _FakeDist("0.1.0", {
        "url": "http://host/m.rau/bibi.git",
        "vcs_info": {"vcs": "git", "commit_id": "fb7f268b36a", "requested_revision": "dev"},
    }))
    assert ei.engine_info().label() == "dev @ fb7f268"


def test_editable_install_is_named_as_such(monkeypatch):
    # Der eigentliche Anlass des Issues: ein Knoten, der gegen ein
    # Arbeits-Checkout läuft, war von außen nicht erkennbar.
    _patch(monkeypatch, _FakeDist("0.2.1", {
        "url": "file:///Users/x/Project/bibi",
        "dir_info": {"editable": True},
    }))
    info = ei.engine_info()
    assert info.editable is True
    assert info.label() == "0.2.1 (editable)"


def test_wheel_install_without_direct_url_falls_back_to_version(monkeypatch):
    # Aus einem Index installiert: kein PEP-610-Eintrag, die Version ist die
    # ganze Auskunft — und das ist in Ordnung, kein Fehlerfall.
    _patch(monkeypatch, _FakeDist("0.3.0", None))
    info = ei.engine_info()
    assert info.label() == "0.3.0"
    assert info.commit is None


def test_unparsable_direct_url_still_reports_version(monkeypatch):
    _patch(monkeypatch, _FakeDist("0.3.0", "{kein json"))
    assert ei.engine_info().label() == "0.3.0"


def test_missing_distribution_never_raises(monkeypatch):
    # Ein Knoten, der seine eigene Herkunft nicht ermitteln kann, soll melden
    # was er weiß — nicht den Heartbeat verlieren.
    _patch(monkeypatch, None)
    info = ei.engine_info()
    assert info == ei.EngineInfo()
    assert info.label() == "n/a"


@pytest.mark.parametrize("ref,version,expected", [
    ("v1.0.0", "1.0.0", "v1.0.0"),   # Tag == Version → einmal genügt
    ("1.0.0", "1.0.0", "1.0.0"),     # Tag ohne "v" ebenso
])
def test_label_deduplicates_tag_and_version(monkeypatch, ref, version, expected):
    _patch(monkeypatch, _FakeDist(version, {
        "url": "http://host/x.git",
        "vcs_info": {"vcs": "git", "commit_id": "deadbeefcafe", "requested_revision": ref},
    }))
    assert ei.engine_info().label() == expected
