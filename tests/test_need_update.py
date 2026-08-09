"""NEED UPDATE — lokaler Soll/Ist-Vergleich (m.rau/bibi#43).

Beide Angaben liegen ohnehin auf jedem Knoten: das Soll in ``pyproject.toml``,
das Ist in ``direct_url.json``. Kein neues Protokollfeld, keine Host-
Abhängigkeit — und es funktioniert gerade dann, wenn der Host nicht erreichbar
ist. Genau das braucht ein hostloses Team.
"""

from __future__ import annotations

import pathlib
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


def test_a_local_build_gets_its_own_verdict(team_repo: Path):
    """Live gefunden am 2026-07-31 (m.rau/bibi#58).

    Der Mac meldete `0.4.0` und sah aus wie ein sauberes Release — lief aber
    gegen eine Kopie des Arbeits-Checkouts, samt uncommitteter Änderungen. Vor
    diesem Urteil war das schlicht `unknown`, und „unbekannt" sagt zu wenig über
    einen Zustand, den man kennt.
    """
    _repo_with_ref(team_repo, "v0.4.0")
    info = EngineInfo(version="0.4.0", url="file:///Users/mrau/Project/bibi")
    assert info.local is True
    assert info.label() == "0.4.0 (local)"
    out = deploy.update_status(info=info)
    assert out["verdict"] == "local"
    assert out["needs_update"] is False


def test_editable_is_not_counted_as_local():
    # Der editable install hat sein eigenes Urteil und seinen eigenen Chip.
    info = EngineInfo(version="0.4.0", url="file:///x", editable=True)
    assert info.local is False
    assert info.label() == "0.4.0 (editable)"


def test_a_vcs_install_is_not_local():
    info = EngineInfo(version="0.4.0", ref="v0.4.0",
                      url="http://sarasate:3000/m.rau/bibi.git")
    assert info.local is False


def test_local_build_is_flagged_in_the_nodes_screen():
    html = render._node_engine_cell("0.4.0 (local)", "v0.4.0")
    assert "local" in html and "chip conflict" in html
    # Kein NEED UPDATE daneben: ein Neustart holt keinen gepinnten Stand, wenn
    # das venv aus einem Verzeichnis kommt.
    assert "NEED UPDATE" not in html


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


def _header(engine: dict) -> str:
    return render.status_header({"hostname": "sarasate", "engine": engine}, {},
                                now=0.0, scheduler_host="sarasate")


def test_a_node_in_arrears_says_so_in_its_own_header():
    """Der Umzug von `_host_card()` in den Header (`#100`).

    Die Karte trug `NEED UPDATE`, `v0.3.0 → v0.4.0` und einen Knopf, der den
    Neustart lokal über `127.0.0.1` auslöste. Der Header trägt davon die
    **Aussage**, nicht den Knopf: die FE-Spezifikation §2 führt die `bibi`-Zeile
    als *„laufende Version, dahinter `requires upgrade` nur wenn abweichend"*,
    und die Nodes-Ausarbeitung vom 2026-08-05 lässt **alle** Neustart- und
    Deploy-Knöpfe entfallen, weil das Ausrollen von selbst geschieht (`#103`).

    **Was hier steht, ist deshalb die Hälfte, die bleibt** — und sie braucht
    diesen Test, weil sie bisher keinen hatte: die Karte war getestet, ihr
    Nachfolger nicht. Genau der Abstand, um den es in `#100` geht.
    """
    html = _header({"needs_update": True, "running": "v0.3.0", "expected": "v0.4.0"})
    assert "v0.3.0" in html
    assert "requires upgrade" in html


def test_a_current_node_stays_quiet():
    html = _header({"needs_update": False, "running": "v0.4.0"})
    assert "requires upgrade" not in html


def test_the_self_update_button_is_gone_with_its_route():
    """Ein Knopf ohne Route wäre schlimmer als keiner — er verspricht etwas.

    `/-/ui/self/update` war die letzte Adresse, die nur aus der abgelösten
    Kachel heraus erreichbar war. Sie ist mit ihr entfallen; das Ausrollen
    hängt ab `#103` am gesetzten SOLL-Stand, nicht an einem zweiten Klick.
    """
    quelle = pathlib.Path(render.__file__).read_text(encoding="utf-8")
    assert "/-/ui/self/update" not in quelle


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


# --- Symmetrie zur Repo-Zelle (m.rau/bibi#67) --------------------------------
#
# m.rau: "jeweils für beide, Repo und Engine, dieselbe dreiteilige Auskunft":
#
#     Engine:  dev (#hash)     modified   behind
#     Repo:    trunk (#hash)   modified   synced
#
# Die Repo-Seite hat diese Form bereits (_node_git_status_chips). Die
# Engine-Zelle zeigte allein das Label — kein Arbeitsbaum-Chip, kein
# Aktualitäts-Chip.


def test_engine_cell_shows_currency_as_a_chip():
    """`behind` ist das verdict aus #43 — es existierte schon, trat aber nur
    als NEED-UPDATE-Zeile auf, nicht als Chip in der Reihe."""
    html = render._node_engine_cell("v0.3.0", "v0.4.0")
    assert "chip" in html and "behind" in html


def test_engine_cell_marks_a_current_node():
    html = render._node_engine_cell("v0.4.0", "v0.4.0")
    assert "current" in html
    assert "behind" not in html


def test_engine_cell_shows_a_branch_pin_as_undetermined():
    """Bei einem Branch-Pin weiß lokal niemand, ob der Branch weitergewandert
    ist. Lieber `branch` sagen als `current` behaupten."""
    html = render._node_engine_cell("dev @ 86ea20e", "dev")
    assert "branch" in html
    assert "current" not in html


def test_engine_cell_shows_the_working_tree_when_there_is_one():
    """`modified` gibt es nur, wo ein Checkout existiert — bei editable und
    lokalem Build."""
    html = render._node_engine_cell("0.4.2 (editable)", "v0.4.2", tree="modified")
    assert "modified" in html


def test_engine_cell_omits_the_tree_chip_for_a_vcs_pin():
    """Ein VCS-Pin hat keinen Arbeitsbaum. Dort entfällt der Chip, statt
    `clean` zu behaupten — eine Aussage, die niemand geprüft hat."""
    html = render._node_engine_cell("v0.4.2", "v0.4.2", tree=None)
    # Auf den Chip-*Text* prüfen, nicht auf die Zeichenkette: "clean" steht
    # auch in der CSS-Klasse `chip clean`, die der Aktualitäts-Chip für
    # `current` trägt.
    assert ">clean<" not in html
    assert ">modified<" not in html


def test_engine_cell_still_names_editable_and_local():
    """Die bestehende Warnung bleibt: beide sind der Grund, warum es das
    Engine-Info-Modul überhaupt gibt."""
    assert "editable" in render._node_engine_cell("0.4.2 (editable)", "v0.4.2")
    assert "local" in render._node_engine_cell("0.4.0 (local)", "v0.4.0")
