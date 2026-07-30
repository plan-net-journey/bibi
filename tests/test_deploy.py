"""Erwartete Engine-Version setzen (m.rau/bibi#39)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi.daemon import deploy


PYPROJECT = '''[project]
name = "x"
dependencies = [
    "bibi[daemon] @ git+http://host/m.rau/bibi.git@v0.2.0",
]
'''


@pytest.fixture()
def proj(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text("rev=v0.2.0\n", encoding="utf-8")
    return tmp_path


def test_current_ref_reads_the_intention(proj: Path):
    # Die Absicht steht in pyproject.toml, nicht in der Lock — genau diese
    # Trennung ist der Grund, warum es kein drittes Feld gibt.
    assert deploy.current_ref(proj) == "v0.2.0"


def test_rejects_implausible_refs(proj: Path):
    # Hier wird eine Zeile ersetzt, die anschließend committet und auf jeden
    # Knoten verteilt wird — der Filter ist bewusst eng.
    for bad in ("", "  ", 'v1"; rm -rf /', "a b", "x" * 80):
        res = deploy.set_expected_version(bad, proj)
        assert res["ok"] is False
        assert deploy.current_ref(proj) == "v0.2.0"   # unverändert


def test_same_ref_is_a_noop(proj: Path):
    res = deploy.set_expected_version("v0.2.0", proj)
    assert res == {"ok": True, "changed": False, "ref": "v0.2.0",
                   "note": "unverändert — schon auf diesem Ref"}


def test_failed_lock_rolls_the_file_back(proj: Path, monkeypatch):
    """Der teuerste Fehlerfall: ein pyproject.toml, das auf einen unauflösbaren
    Ref zeigt, lässt jeden weiteren `uv run` auf diesem Knoten scheitern — also
    auch den Daemon-Start. Deshalb wird zurückgerollt."""
    def fake_run(cmd, **kw):
        if cmd[:2] == ["uv", "lock"]:
            return subprocess.CompletedProcess(cmd, 1, "", "no such tag")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    res = deploy.set_expected_version("v9.9.9", proj)
    assert res["ok"] is False
    assert "uv lock" in res["error"]
    # Entscheidend: die Datei zeigt wieder auf den alten Ref.
    assert deploy.current_ref(proj) == "v0.2.0"


def test_successful_set_writes_commits_and_pushes(proj: Path, monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(deploy.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    import bibi.git_ops as go
    monkeypatch.setattr(go, "stage_and_commit_paths",
                        lambda paths, msg, identity=None: calls.setdefault("paths", paths) or True)
    monkeypatch.setattr(go, "current_branch", lambda: "trunk")
    monkeypatch.setattr(go, "push", lambda b, **kw: (True, "", None))

    res = deploy.set_expected_version("v0.2.3", proj)
    assert res["ok"] and res["changed"]
    assert res["was"] == "v0.2.0" and res["ref"] == "v0.2.3"
    assert res["pushed"] is True
    # Nur die beiden Dateien, nicht der ganze Baum.
    assert calls["paths"] == ["pyproject.toml", "uv.lock"]
    assert deploy.current_ref(proj) == "v0.2.3"


def test_without_push_the_intention_stays_local(proj: Path, monkeypatch):
    monkeypatch.setattr(deploy.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    import bibi.git_ops as go
    monkeypatch.setattr(go, "stage_and_commit_paths", lambda *a, **kw: True)
    monkeypatch.setattr(go, "current_branch", lambda: "trunk")
    pushed = []
    monkeypatch.setattr(go, "push", lambda b, **kw: pushed.append(b) or (True, "", None))

    res = deploy.set_expected_version("v0.2.3", proj, push=False)
    assert res["ok"] and pushed == []
