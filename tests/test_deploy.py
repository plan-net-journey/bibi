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


def test_running_comes_from_the_portfile_not_the_disk(proj: Path):
    """**Der Rot-Schritt von `#102`.**

    `#81` hat `running` von `installed` getrennt, und der Kommentar daneben
    sagt, was das Feld tragen soll: *„den Stand DIESES Prozesses … in einem
    langlebigen Daemon sein Startstand."* Gesetzt wurde es aber auf
    `info.label()` — dieselbe Quelle wie `installed`, also `direct_url.json`
    im venv, und die liest bei jedem Abruf frisch von der Platte.

    **Live am 2026-08-09:** `sarasate:8780` lief seit `10:59:36` unverändert,
    ich hatte dazwischen `uv sync` ausgeführt. `/-/status` meldete
    `running: v0.7.11`, das FE zeigte den Chip `current` — für einen Prozess,
    der `v0.7.10`-Code ausführte. Der Gegenbeweis stand im selben System: die
    `#97`-Karteileiche, die `v0.7.11` beim ersten Rescan gelöscht hätte, stand
    unverändert.

    **Die Portdatei führt die Angabe längst** (`portfile.write(engine=…)`,
    beim Start geschrieben), und `upgrade_notice.pending()` benutzt sie seit
    `#81` genau dafür. `update_status()` — die Quelle für `/-/status` und den
    Nodes-Screen — tat es nicht: dieselbe Fähigkeit, gebaut und begründet, an
    der zweiten Stelle nicht benutzt.
    """
    from bibi.daemon import portfile

    class _Info:
        """Das venv nach einem `uv sync`: schon auf dem neuen Stand."""

        ref = label_ref = "v0.7.11"
        editable = local = False

        def label(self):
            return "v0.7.11"

    # Der Prozess ist mit dem alten Stand gestartet und laeuft unveraendert.
    portfile.write(8780, root=proj, engine="v0.7.10")

    st = deploy.update_status(proj, _Info())

    assert st["installed"] == "v0.7.11", "die Platte ist aktuell — das ist richtig"
    assert st["running"] == "v0.7.10", (
        "running meldet die Platte statt den Startstand des Prozesses (#102)")


def test_running_falls_back_to_the_disk_without_a_portfile(proj: Path):
    """Die Gegenprobe: ohne Portdatei bleibt es beim venv.

    In einem frisch gestarteten CLI-Aufruf ist das dasselbe, und ein fehlender
    Eintrag (Daemon älter als diese Änderung) ist kein Grund, die Auskunft
    ganz aufzugeben — dieselbe Regel, die `upgrade_notice` schon anwendet.
    """
    class _Info:
        ref = label_ref = "v0.7.11"
        editable = local = False

        def label(self):
            return "v0.7.11"

    st = deploy.update_status(proj, _Info())
    assert st["running"] == "v0.7.11"


def test_the_heartbeat_reports_the_running_engine_not_the_disk(team_repo: Path, monkeypatch):
    """**Die zweite Stelle von `#102`, und die sichtbare.**

    `update_status()` speist `/-/status`. Den **Chip im Nodes-Screen** speist
    etwas anderes: `node_info.self_entry()` legt `engine` in den Heartbeat, der
    Scheduler merkt ihn sich, und `render._node_engine_cell()` fällt darüber
    sein Urteil (`current` / `behind`). Auch dort stand `engine_info().label()`
    — die Platte.

    **Das war der Teil, den m.rau gesehen hat:** sein FE zeigte `sarasate:8780`
    mit `v0.7.11` und dem Chip `current`, während der Prozess seit `10:59:36`
    lief und `v0.7.10`-Code ausführte. Ein Fix nur in `update_status()` hätte
    `/-/status` geheilt und den Chip weiter lügen lassen.

    Ein Knoten berichtet hier über sich selbst — die Frage lautet „was läuft
    dort", nicht „was liegt dort auf der Platte".
    """
    from bibi.daemon import node_info, portfile, roles as roles_mod

    portfile.write(8780, root=team_repo, engine="v0.7.10")

    class _Info:
        ref = label_ref = "v0.7.11"
        editable = local = False

        def label(self):
            return "v0.7.11"

        def tree_status(self):
            return None

    import bibi.engine_info as ei_mod
    monkeypatch.setattr(ei_mod, "engine_info", lambda *a, **k: _Info())

    entry = node_info.self_entry(roles_mod.resolve({"controller"}))

    assert entry["engine"] == "v0.7.10", (
        "der Heartbeat meldet die Platte statt den laufenden Stand (#102) — "
        "der Chip im Nodes-Screen sagt daraufhin 'current' für einen Prozess, "
        "der alten Code faehrt")
