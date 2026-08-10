"""#113/#111: die Selbstauskunft eines Knotens fehlten zwei Felder.

``node_info.self_entry()`` speist die Zeile des Schedulers im Nodes-Screen —
er meldet sich nie per Heartbeat bei sich selbst (dieselbe
``scheduler``+``connect``-Ausschluss-Invariante wie in ``daemon/roles.py``),
also entsteht seine Zeile ausschliesslich hier, nicht aus der Registry.

``sync_conflict``/``auto_sync`` (#113): dieselben Felder, die ``Heartbeat.
_sync_state()`` fuer jeden **anderen** Knoten schon traegt — der Scheduler
selbst fuehrte sie bisher nicht, der Nodes-Screen konnte fuer ihn also nie
``sync blocked``/``sync off`` zeigen, unabhaengig vom tatsaechlichen Zustand.

``merge_stuck`` (#111): die eskalierte Merge-Quarantaene (``data/
merge_quarantine.json``) reiste bisher **nirgendwohin** — sichtbar nur lokal
in ``bibi-ctrl status``/der Statusline desselben Knotens. Ein Client, dessen
Nodes-Screen den Scheduler zeigt, konnte einen haengenden ``agent/*``-Branch
dort nie sehen.
"""

from __future__ import annotations

from pathlib import Path

from bibi.daemon import node_info
from bibi.daemon import roles as roles_mod


def test_self_entry_carries_sync_conflict(team_repo: Path):
    from bibi import state
    state.set_sync_conflict(True)
    entry = node_info.self_entry(roles_mod.resolve({"controller"}))
    assert entry.get("sync_conflict") is True, (
        "die Selbstauskunft des Schedulers kennt sync_conflict nicht — sein "
        "Nodes-Screen-Chip bleibt immer still, egal was tatsaechlich los ist (#113)")


def test_self_entry_carries_auto_sync(team_repo: Path):
    from bibi import state
    state.set_auto_sync(False)
    entry = node_info.self_entry(roles_mod.resolve({"controller"}))
    assert entry.get("auto_sync") is False


def test_self_entry_reports_no_stuck_branches_by_default(team_repo: Path):
    entry = node_info.self_entry(roles_mod.resolve({"controller"}))
    assert entry.get("merge_stuck") == []


def test_self_entry_names_an_escalated_branch(team_repo: Path):
    """#111: der Abnahmefall — ``agent/Witz`` steht in der Zeile von sarasate."""
    from bibi.daemon import merge_quarantine
    for _ in range(merge_quarantine.ESCALATE_AFTER):
        merge_quarantine.record_failure(team_repo, "agent/Witz", trunk_sha="deadbeef")
    entry = node_info.self_entry(roles_mod.resolve({"controller"}))
    assert entry.get("merge_stuck") == ["agent/Witz"], (
        "eine eskalierte Merge-Quarantaene reist nirgendwohin — der Nodes-"
        "Screen eines anderen Knotens kann sie nie zeigen (#111)")
