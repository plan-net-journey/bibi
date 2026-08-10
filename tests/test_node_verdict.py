"""Ein Urteil über zwei Größen — die Handlung statt des Rückstands (`#127`, `#126`).

**Bis `v0.7.19` reiste je Knoten genau eine Größe.** `Heartbeat._beat()` sendete
`engine`, die Registry führte eine Spalte, und `label_verdict()` konnte daraus
nur `current` oder `behind` bilden. Damit sahen **zwei verschiedene Lagen mit
zwei verschiedenen Handlungen gleich aus**:

===============================  ========================  ====================
``running = installed = expected``  alles gut                 —
``running < installed = expected``  Platte neu, Prozess alt   **Neustart**
``installed < expected``            Lock nicht durch          warten / Sync
===============================  ========================  ====================

**Die mittlere Lage ist der Auslöser, den `#103` braucht** — *Lock ist da,
Prozess ist alt, jetzt neu starten.* Ohne die zweite Größe erkennt ein
automatischer Rollout den Moment nicht und kann seinen Abschluss nicht
feststellen; `#103` sagt das selbst: *„solange `#102` offen ist, kann ein
automatischer Rollout seinen eigenen Erfolg nicht messen."*

**Und es gab zwei Vokabulare für dasselbe Urteil.** Der eigene Knoten hieß
`outdated` (``update_status()``), ein fremder `behind` (``label_verdict()``) —
zwei Funktionen, dieselbe Frage. `#126` ist genau daran entstanden: die eine
urteilte über `installed`, die andere über `running`, und niemand hat sie je
nebeneinander gehalten. **Der letzte Test hier tut das** — er bewacht keine
Funktion, sondern eine Übereinstimmung.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi.daemon import deploy, portfile


# ── Die drei Lagen (#127) ───────────────────────────────────────────────────


def test_the_lock_arrived_but_the_process_is_old_says_restart_pending():
    """Die Lage, für die es dieses Ticket gibt: hier hilft ein Neustart."""
    assert deploy.node_verdict("v0.7.19", running="v0.7.18",
                               installed="v0.7.19") == "restart pending", (
        "ein Knoten mit gepullter Lock und altem Prozess sieht aus wie einer, "
        "bei dem der Sync haengt — zwei Lagen, zwei Handlungen, eine Anzeige (#127)")


def test_the_lock_has_not_arrived_says_behind():
    """Dieselbe Version im Prozess, aber die Platte ist auch alt: kein Neustart
    hilft hier, der Sync muss erst durch."""
    assert deploy.node_verdict("v0.7.19", running="v0.7.18",
                               installed="v0.7.18") == "behind"


def test_a_node_on_the_expected_tag_says_current():
    assert deploy.node_verdict("v0.7.19", running="v0.7.19",
                               installed="v0.7.19") == "current"


def test_a_node_that_does_not_report_installed_judges_from_running_alone():
    """**Waehrend jedes Rollouts der Normalfall**, und deshalb kein ``unknown``.

    Die alten Prozesse senden das zweite Feld nicht — sie kennen es nicht. Wer
    daraus „unbestimmt" machte, verlöre eine Auskunft, die vorher dastand, und
    zwar genau in dem Moment, in dem man sie braucht.
    """
    assert deploy.node_verdict("v0.7.19", running="v0.7.18", installed=None) == "behind"
    assert deploy.node_verdict("v0.7.19", running="v0.7.19", installed=None) == "current"


def test_a_branch_pin_stays_undecided():
    """Unverändert die Zurückhaltung von ``label_verdict()``: bei einem
    Branch-Pin weiß hier niemand, ob der Branch weitergewandert ist."""
    assert deploy.node_verdict("dev", running="dev @ 86ea20e",
                               installed="dev @ 86ea20e") == "branch"


def test_a_working_checkout_carries_its_own_chip_not_a_verdict():
    for label in ("0.7.19 (editable)", "0.7.19 (local)"):
        assert deploy.node_verdict("v0.7.19", running=label,
                                   installed=label) in ("editable", "local")


def test_a_missing_side_stays_unknown():
    assert deploy.node_verdict(None, running="v0.7.19", installed="v0.7.19") == "unknown"
    assert deploy.node_verdict("v0.7.19", running=None, installed=None) == "unknown"


# ── Die zweite Größe reist (#127) ───────────────────────────────────────────


class _FakeClient:
    def __init__(self) -> None:
        self.last_kwargs: dict = {}

    def register(self, worker: str, host: str, git_status: str | None = None,
                 **kw) -> dict | None:
        self.last_kwargs = kw
        return None


def test_the_heartbeat_carries_installed_next_to_running(team_repo: Path):
    """Ohne dieses Feld sieht der Scheduler nur eine der beiden Größen — und
    er sieht ausschließlich fremde Knoten."""
    from bibi.daemon.heartbeat import Heartbeat

    portfile.write(12345, root=team_repo, session=True, engine="v0.7.18")
    client = _FakeClient()
    Heartbeat(client=client, repo_root=team_repo)._beat()
    assert client.last_kwargs["engine"] == "v0.7.18"
    assert client.last_kwargs["engine_installed"], (
        "der installierte Stand reist nicht mit — der Scheduler kann "
        "'Neustart faellig' nicht von 'Sync haengt' unterscheiden (#127)")
    assert client.last_kwargs["engine_installed"] != "v0.7.18", (
        "hier soll das venv stehen, nicht der Startstand aus der Portdatei")


def test_the_registry_keeps_installed():
    from bibi.daemon.worker_registry import WorkerRegistry

    reg = WorkerRegistry()
    reg.heartbeat("w1", "h1", node_id="n1", engine="v0.7.18",
                  engine_installed="v0.7.19")
    zeile = [w for w in reg.list() if w.get("node_id") == "n1"][0]
    assert zeile["engine"] == "v0.7.18"
    assert zeile["engine_installed"] == "v0.7.19"


def test_the_schedulers_own_row_carries_it_too(team_repo: Path):
    """Der Scheduler meldet sich nie bei sich selbst — seine Zeile entsteht in
    ``self_entry()``. Ohne das Feld dort fehlt die Auskunft genau bei dem
    Knoten, der die Tabelle rendert."""
    from bibi.daemon import node_info
    from bibi.daemon import roles as roles_mod

    portfile.write(12345, root=team_repo, session=True, engine="v0.7.18")
    entry = node_info.self_entry(roles_mod.resolve({"controller"}))
    assert entry["engine"] == "v0.7.18"
    assert entry.get("engine_installed") and entry["engine_installed"] != "v0.7.18"


# ── Ein Urteil, nicht zwei (#126) ───────────────────────────────────────────


@pytest.fixture
def gepinnt_auf_19(team_repo: Path) -> Path:
    (team_repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\n'
        'dependencies = ["bibi[daemon] @ git+http://x/bibi.git@v0.7.19"]\n',
        encoding="utf-8")
    return team_repo


class _Info:
    """Ein installierter Stand, wie ``engine_info()`` ihn liefert."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        self.editable = False
        self.local = False

    def label(self) -> str:
        return self.ref

    def tree_status(self):
        return None


def test_the_client_block_asks_for_a_restart_when_the_venv_moved_ahead(
        gepinnt_auf_19: Path):
    """`#126`: das Urteil stand auf der Platte, die Zahl daneben auf dem Prozess.

    Live am 2026-08-10 auf dem Mac: `running v0.7.18`, `verdict current`,
    `needs_update False` — während ``upgrade_notice.pending()`` im selben
    Moment zum Neustart aufforderte. Zwei Urteile über denselben Prozess,
    gegenläufig.
    """
    portfile.write(12345, root=gepinnt_auf_19, session=True, engine="v0.7.18")
    st = deploy.update_status(gepinnt_auf_19, _Info("v0.7.19"))
    assert st["running"] == "v0.7.18" and st["installed"] == "v0.7.19"
    assert st["needs_update"] is True, (
        "der Upgrade-Hinweis erlischt, sobald das venv nachzieht — genau in "
        "dem Fenster, fuer das er existiert (#126)")
    assert st["verdict"] == "restart pending"


def test_both_paths_judge_the_same_state_alike(gepinnt_auf_19: Path):
    """**Der teuerste Test dieses Releases bewacht keine Funktion, sondern eine
    Übereinstimmung.**

    Derselbe Zustand, zwei Wege: einmal lokal erhoben (``update_status()``, der
    eigene Knoten), einmal aus zwei gemeldeten Labels (``node_verdict()``, ein
    fremder). Sie waren zwei Implementierungen derselben Regel und sind
    zweimal auseinandergelaufen — `#102` und `#126`.
    """
    portfile.write(12345, root=gepinnt_auf_19, session=True, engine="v0.7.18")
    eigen = deploy.update_status(gepinnt_auf_19, _Info("v0.7.19"))
    fremd = deploy.node_verdict(eigen["expected"], running=eigen["running"],
                                installed=eigen["installed"])
    assert eigen["verdict"] == fremd, (
        "der eigene Knoten und ein fremder mit demselben Zustand bekommen "
        "verschiedene Urteile — bis v0.7.19 hiess das eine 'outdated' und das "
        "andere 'behind', und nur eines las den laufenden Stand")


def test_the_upgrade_notice_speaks_the_same_verdict(gepinnt_auf_19: Path):
    """Dieselbe Frage, dieselbe Antwort: die Statusleiste und der Screen dürfen
    sich nicht widersprechen."""
    from bibi import upgrade_notice

    portfile.write(12345, root=gepinnt_auf_19, session=True, engine="v0.7.18")
    st = upgrade_notice.pending(gepinnt_auf_19, _Info("v0.7.19"))
    assert st == {"expected": "v0.7.19", "running": "v0.7.18"}


def test_the_dead_helper_is_gone():
    """``label_is_outdated()`` hatte ausser seinen zwei Tests keinen Aufrufer.

    **Ein toter Pfad mit gruenen Tests darueber** ist die Fehlerform, die
    dieser Case seit dem 2026-08-09 fuehrt — die Abdeckung bewacht dann den
    toten Pfad und laesst den lebenden unbewacht.
    """
    assert not hasattr(deploy, "label_is_outdated")
    assert not hasattr(deploy, "label_verdict"), (
        "zwei Namen fuer dasselbe Urteil — node_verdict() ist der eine")
