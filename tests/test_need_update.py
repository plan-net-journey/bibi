"""NEED UPDATE — lokaler Soll/Ist-Vergleich (m.rau/bibi#43).

Beide Angaben liegen ohnehin auf jedem Knoten: das Soll in ``pyproject.toml``,
das Ist in ``direct_url.json``. Kein neues Protokollfeld, keine Host-
Abhängigkeit — und es funktioniert gerade dann, wenn der Host nicht erreichbar
ist. Genau das braucht ein hostloses Team.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import deploy, roles
from bibi.daemon.app import create_app
from bibi.engine_info import EngineInfo

_PYPROJECT = """\
[project]
name = "t"
version = "0.0.0"
dependencies = [
  "bibi[daemon] @ git+http://sarasate:3000/m.rau/bibi.git@{ref}",
]
"""


def _repo_with_ref(root: Path, ref: str) -> Path:
    (root / "pyproject.toml").write_text(_PYPROJECT.format(ref=ref), encoding="utf-8")
    return root


# ── Das Urteil ──────────────────────────────────────────────────────────────


def test_outdated_when_tags_differ(team_repo: Path):
    _repo_with_ref(team_repo, "v0.4.0")
    out = deploy.update_status(info=EngineInfo(version="0.3.0", ref="v0.3.0",
                                               commit="a" * 40))
    assert out["verdict"] == "outdated"
    assert out["needs_update"] is True
    assert out["expected"] == "v0.4.0"
    assert out["running"] == "v0.3.0"


def test_current_when_tags_match(team_repo: Path):
    _repo_with_ref(team_repo, "v0.4.0")
    out = deploy.update_status(info=EngineInfo(version="0.4.0", ref="v0.4.0"))
    assert out["verdict"] == "current"
    assert out["needs_update"] is False


def test_v_prefix_is_spelling_not_a_difference(team_repo: Path):
    # Sonst meldete ein aus einem Index installierter Knoten dauerhaft
    # NEED UPDATE: dort steht die nackte Version, im pyproject der Tag.
    _repo_with_ref(team_repo, "v0.4.0")
    out = deploy.update_status(info=EngineInfo(version="0.4.0", ref="0.4.0"))
    assert out["verdict"] == "current"


def test_branch_pin_is_undecidable_locally(team_repo: Path):
    # Ein Tag steht still, ein Branch wandert. Ob `dev` weitergelaufen ist, weiß
    # hier lokal niemand — dann lieber „unbestimmt" sagen als raten.
    _repo_with_ref(team_repo, "dev")
    out = deploy.update_status(info=EngineInfo(version="0.4.0", ref="dev",
                                               commit="b" * 40))
    assert out["verdict"] == "branch"
    assert out["needs_update"] is False


def test_editable_is_an_intention_not_a_lag(team_repo: Path):
    _repo_with_ref(team_repo, "v0.4.0")
    out = deploy.update_status(info=EngineInfo(version="0.4.0", editable=True))
    assert out["verdict"] == "editable"
    assert out["needs_update"] is False


def test_unknown_without_an_expected_ref(team_repo: Path):
    # pyproject ohne bibi-Abhängigkeit (Skelett, fremdes Repo).
    out = deploy.update_status(info=EngineInfo(version="0.4.0", ref="v0.4.0"))
    assert out["verdict"] == "unknown"
    assert out["needs_update"] is False


def test_unknown_without_a_running_ref(team_repo: Path):
    _repo_with_ref(team_repo, "v0.4.0")
    out = deploy.update_status(info=EngineInfo(version="0.4.0"))
    assert out["verdict"] == "unknown"


# ── Dasselbe Urteil für einen fremden Knoten ────────────────────────────────


@pytest.mark.parametrize("expected,label,outdated", [
    ("v0.4.0", "v0.3.0", True),
    ("v0.4.0", "v0.4.0", False),
    ("v0.4.0", "0.4.0", False),            # aus einem Index installiert
    ("v0.4.0", "dev @ 86ea20e", True),     # Branch statt gepinntem Tag
    ("v0.4.0", "0.4.0 (editable)", False),  # Absicht, kein Rückstand
    ("dev", "v0.3.0", False),              # Soll ist kein Tag ⇒ kein Urteil
    (None, "v0.3.0", False),
    ("v0.4.0", None, False),
    ("v0.4.0", "", False),
])
def test_label_is_outdated(expected, label, outdated):
    assert deploy.label_is_outdated(expected, label) is outdated


# ── /-/status trägt das Urteil ──────────────────────────────────────────────


def test_status_carries_the_engine_verdict(team_repo: Path):
    _repo_with_ref(team_repo, "v0.4.0")
    app = create_app(roles.resolve({"controller"}))
    with TestClient(app) as c:
        eng = c.get("/-/status").json()["engine"]
    assert "verdict" in eng and "needs_update" in eng


def test_status_survives_a_broken_pyproject(team_repo: Path):
    (team_repo / "pyproject.toml").write_text("kein toml [[[", encoding="utf-8")
    app = create_app(roles.resolve({"controller"}))
    with TestClient(app) as c:
        r = c.get("/-/status")
    assert r.status_code == 200
    assert r.json()["engine"]["needs_update"] is False


# ── Die Anzeige ─────────────────────────────────────────────────────────────


def test_host_card_shows_need_update_with_a_button():
    html = render._host_card(
        {"hostname": "sarasate",
         "engine": {"needs_update": True, "running": "v0.3.0", "expected": "v0.4.0"}},
        None, 0.0)
    assert "NEED UPDATE" in html
    assert "v0.3.0 → v0.4.0" in html
    # Der Knopf läuft lokal über 127.0.0.1 — genau daran scheiterte der
    # Restart-Knopf im Nodes-Screen beim Mac.
    assert 'hx-post="/-/ui/self/update"' in html


def test_host_card_stays_quiet_when_current():
    html = render._host_card(
        {"hostname": "sarasate", "engine": {"needs_update": False}}, None, 0.0)
    assert "NEED UPDATE" not in html


def test_client_card_shows_need_update():
    # Auf einem Client wiegt es schwerer: er wird nicht bedient, er läuft mit —
    # und ein hostloser Knoten hat gar keinen Nodes-Screen.
    html = render._host_card(
        {"connect": {"ok": True, "last_at": 0.0},
         "engine": {"needs_update": True, "running": "v0.3.0", "expected": "v0.4.0"}},
        "http://sarasate:8780", 0.0)
    assert "NEED UPDATE" in html


def test_node_engine_cell_flags_an_outdated_node():
    assert "NEED UPDATE" in render._node_engine_cell("v0.3.0", "v0.4.0")
    assert "NEED UPDATE" not in render._node_engine_cell("v0.4.0", "v0.4.0")


def test_node_engine_cell_without_an_expected_ref_is_unchanged():
    # Ein falsches NEED UPDATE wäre schlimmer als ein fehlendes — es schickt
    # jemanden los, etwas zu reparieren, das in Ordnung ist.
    assert "NEED UPDATE" not in render._node_engine_cell("v0.3.0", None)


def test_editable_keeps_its_own_chip_and_gets_no_need_update():
    html = render._node_engine_cell("0.4.0 (editable)", "v0.9.9")
    assert "editable" in html
    assert "NEED UPDATE" not in html


def test_clients_table_marks_the_outdated_node(team_repo: Path):
    _repo_with_ref(team_repo, "v0.4.0")
    html = render.clients_fragment([
        {"worker": "mac", "host": "Mac", "engine": "v0.3.0", "node_id": "n1",
         "last_heartbeat": 0.0, "connected_at": 0.0},
        {"worker": "sara", "host": "sara", "engine": "v0.4.0", "node_id": "n2",
         "last_heartbeat": 0.0, "connected_at": 0.0},
    ], now=0.0)
    assert html.count("NEED UPDATE") == 1
