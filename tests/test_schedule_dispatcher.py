"""Job-Auswahl: Priorität + Fairness-Offset (DESIGN §4.4; PLAN-3 §3.2) — rein."""

from __future__ import annotations

from bibi.schedule import dispatcher as d


def _c(id, priority, enqueued_at, seq):
    return {"id": id, "priority": priority, "enqueued_at": enqueued_at, "seq": seq}


def test_empty():
    assert d.select_v1([]) is None
    assert d.select([], 0.0) == (None, 0.0)


def test_v1_priority_then_fifo():
    jobs = [_c("a", 0, 10, 1), _c("b", 5, 20, 2), _c("c", 0, 5, 3)]
    assert d.select_v1(jobs)["id"] == "b"          # höchste Prio zuerst
    jobs2 = [_c("a", 0, 10, 1), _c("c", 0, 5, 3)]
    assert d.select_v1(jobs2)["id"] == "c"         # Gleichstand → ältester (enq 5)


def test_first_pick_respects_priority():
    # Akzeptanz §3.2: priority:5-Job zuerst, dann FIFO der prio:0-Jobs.
    jobs = [_c("z1", 0, 10, 1), _c("p5", 5, 11, 2), _c("z2", 0, 12, 3)]
    chosen, off = d.select(jobs, 0.0)
    assert chosen["id"] == "p5"
    assert off == 0.0                              # Prioritäts-Sprung: Cursor bleibt


def test_then_fifo_among_equal_priority():
    jobs = [_c("z1", 0, 10, 1), _c("z2", 0, 12, 3)]   # p5 schon weg
    c1, off1 = d.select(jobs, 0.0)
    assert c1["id"] == "z1" and off1 == 10            # ältester, Cursor → 10
    jobs2 = [_c("z2", 0, 12, 3)]
    c2, off2 = d.select(jobs2, off1)
    assert c2["id"] == "z2" and off2 == 12


def test_fairness_jump_on_new_high_priority():
    # Sweep beginnt bei prio0; ein neuer Hochprio-Job bekommt sofort Zugriff.
    jobs = [_c("z1", 0, 10, 1), _c("z2", 0, 11, 2)]
    _c1, off = d.select(jobs, 0.0)               # → z1, off 10
    jobs2 = [_c("z2", 0, 11, 2), _c("H", 9, 99, 3)]
    chosen, off2 = d.select(jobs2, off)
    assert chosen["id"] == "H"                   # Sprung auf höhere Priorität
    assert off2 == off                           # Cursor unverändert beim Sprung


def test_no_starvation_finite_workload():
    # Konstruierte Queue: 1 Hochprio (jüngster) + 3 prio0 → Hochprio zuerst,
    # danach alle prio0 in FIFO-Reihenfolge (keiner ausgehungert).
    jobs = {c["id"]: c for c in [
        _c("z1", 0, 1, 1), _c("z2", 0, 2, 2), _c("z3", 0, 3, 3), _c("p", 5, 4, 4),
    ]}
    order = []
    offset = 0.0
    remaining = dict(jobs)
    for _ in range(4):
        chosen, offset = d.select(list(remaining.values()), offset)
        order.append(chosen["id"])
        del remaining[chosen["id"]]
    assert order == ["p", "z1", "z2", "z3"]


def test_cursor_wraps_when_past_newest():
    # Cursor jenseits des jüngsten → Sweep startet wieder beim ältesten.
    jobs = [_c("z1", 0, 10, 1), _c("z2", 0, 20, 2)]
    chosen, off = d.select(jobs, offset=999.0)
    assert chosen["id"] == "z1" and off == 10
