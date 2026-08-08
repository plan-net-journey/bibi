"""Der Header eines Knotens ohne Gegenüber (#71) und der fünfte Fingerabdruck (#72).

**Befund m.rau, 2026-08-07:** zwei Screenshots im Abstand von 15 Sekunden,
dazwischen ein manueller Reload — *„hier musste ich manuell refreshen bevor ich
sehe:"*. Der Unterschied zwischen beiden war ``clean`` → ``modified``.

Zwei Ursachen, ein Fix. Der Header hängt am Bus-Ziel ``feedstatus``, und dafür
gab es bis ``v0.7.5`` vier Auslöser — **alle vier setzen eine Verbindung
voraus**: Job-Zustandswechsel (es laufen keine ohne Scheduler),
``_diff_flags()`` (lokale Flags stehen still), ``_diff_heartbeat()`` (kein
Heartbeat ohne ``connect``-Rolle) und ``_diff_scheduler()`` (bekommt ``None``).
Auf einem Knoten ohne erreichbaren Scheduler feuerte damit **keiner**.

Und der Git-Arbeitsbaum stand in keinem der vier Fingerabdrücke — das war
keine Nachlässigkeit, sondern eine Kosten-Entscheidung (``bus.py``: *„Zu weit
gefasst machte er den Header bei jeder Kleinigkeit dreckig — und der hängt an
einem git-Aufruf."*). Die Git-Karte hing früher an einem 30-Sekunden-Poll; der
ist mit PLAN-36 Stufe 36.3 entfallen, und **ersatzlos**.

**Entscheidung m.rau, 2026-08-07:** *„nimm die erste: über ``git_status`` im
selben Takt wie ``_diff_scheduler()`` (alle paar Sekunden, nicht bei jedem
Tick)"*. Die Alternative — ein Mindesttakt für ``feedstatus``, wenn seit N
Sekunden nichts kam — ist damit verworfen.

Damit ist es **eine Prüfung, nicht zwei**: der fünfte Fingerabdruck ist
zugleich der Auslöser, den ein Knoten ohne Scheduler bisher gar nicht hatte.
"""

from __future__ import annotations

from bibi.daemon.bus import Collector

CLEAN = {"tree": "clean", "sync": "synced", "branch": "trunk",
         "oid": "a" * 40, "ahead": 0, "behind": 0}
MODIFIED = dict(CLEAN, tree="modified")


class _Bus:
    def __init__(self) -> None:
        self.published: list[str] = []

    def publish_state(self, target: str, value: dict | None = None) -> None:
        self.published.append(target)


def _collector(bus, git_antworten, *, scheduler=None):
    """Collector mit gefaktem Git-Lesen — dasselbe Muster wie der gefakte
    Scheduler-Abruf in ``test_bus_scheduler_diff.py``.

    ``scheduler`` bleibt bewusst ``None``: das ist der Knoten, um den es geht.
    """
    c = Collector(bus, registry=None)
    folge = iter(git_antworten)
    c._read_git = lambda: next(folge, git_antworten[-1])
    c._fetch_scheduler_status = lambda: scheduler
    c._primed = True
    return c


def _tick(c) -> int:
    """Ein Git-Diff ohne Wartezeit. Die Drossel gehört zum Verhalten und wird
    unten eigens geprüft — hier stünde sie nur im Weg."""
    c._git_last_check = 0.0
    return c._diff_git()


# ── #72: die Git-Zeile bekommt einen Auslöser ───────────────────────────────


def test_a_dirty_worktree_publishes_feedstatus():
    """Der Kern: genau der Wechsel, den m.rau nur nach manuellem Reload sah.

    Der erste Durchlauf legt den Grundstand — ``_collector()`` setzt ``_primed``
    von Hand, was ``tick_once()`` erst am *Ende* des ersten Ticks tut. Dasselbe
    ``clear()`` steht aus demselben Grund in ``test_bus_scheduler_diff.py``.
    """
    bus = _Bus()
    c = _collector(bus, [CLEAN, MODIFIED])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == ["feedstatus"], "der Wechsel clean → modified blieb stumm"


def test_an_unchanged_worktree_stays_quiet():
    """Ein Fingerabdruck, der bei jedem Durchlauf feuert, ist keiner — er
    machte den Header alle paar Sekunden dreckig, und der hängt an einem
    git-Aufruf."""
    bus = _Bus()
    c = _collector(bus, [CLEAN])
    _tick(c)
    bus.published.clear()
    for _ in range(4):
        _tick(c)
    assert bus.published == []


def test_the_branch_counts_too():
    """Die Karte zeigt Tree, Sync und Branch. Der Fingerabdruck deckt ab, was
    dort steht — sonst bliebe ein Wechsel sichtbar falsch stehen."""
    bus = _Bus()
    c = _collector(bus, [CLEAN, dict(CLEAN, branch="dev")])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == ["feedstatus"]


def test_the_sync_state_counts_too():
    bus = _Bus()
    c = _collector(bus, [CLEAN, dict(CLEAN, sync="ahead", ahead=2)])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == ["feedstatus"]


def test_the_check_is_throttled_and_does_not_run_on_every_tick():
    """m.raus Entscheidung wörtlich: „alle paar Sekunden, nicht bei jedem
    Tick". Der Collector tickt sekündlich; ein ``git status`` je Tick wäre
    genau der Preis, wegen dem die Zeile ursprünglich draußen blieb."""
    bus = _Bus()
    gelesen = []

    c = Collector(bus, registry=None)
    c._primed = True

    def _lesen():
        gelesen.append(1)
        return CLEAN if len(gelesen) < 2 else MODIFIED

    c._read_git = _lesen
    c._diff_git()                       # erster Durchlauf: liest, legt an
    for _ in range(5):
        c._diff_git()                   # unmittelbar danach: gedrosselt
    assert len(gelesen) == 1, f"{len(gelesen)} git-Aufrufe statt einem"


def test_no_git_repo_is_not_a_change():
    """``working_tree_status()`` liefert ``None`` außerhalb eines Repos. Das
    ist ein Dauerzustand, kein Ereignis — er darf nicht bei jedem Durchlauf
    einen dreckigen Header erzeugen."""
    bus = _Bus()
    c = _collector(bus, [None])
    for _ in range(3):
        _tick(c)
    assert bus.published == []


# ── #71: der Knoten ohne Gegenüber aktualisiert überhaupt ───────────────────


def test_a_node_without_a_scheduler_still_updates_its_header():
    """Die eigentliche Prüfung, und sie ist bewusst nicht „der Punkt wird rot":
    ein Knoten **ohne** Scheduler muss seinen Header ohne Reload aktualisieren.

    Gefahren wird derselbe Weg wie in ``tick_once()`` — erst der
    Scheduler-Diff, der hier nichts findet, dann der Git-Diff. Vor ``v0.7.5``
    war die Summe beider null, und genau das war der Befund.
    """
    bus = _Bus()
    c = _collector(bus, [CLEAN, MODIFIED], scheduler=None)
    c._sched_last_fetch = 0.0
    assert c._diff_scheduler() == 0, "ohne Scheduler gibt es dort nichts zu melden"
    _tick(c)
    bus.published.clear()

    c._sched_last_fetch = 0.0
    n = c._diff_scheduler() + _tick(c)
    assert n > 0, "kein einziger feedstatus-Auslöser auf einem Knoten ohne Scheduler"
    assert "feedstatus" in bus.published
