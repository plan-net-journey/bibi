"""Der Feed bekommt einen Auslöser (#80).

**Befund:** ``#feedboard`` trug weder ``data-bus`` noch ``data-bus-refetch`` —
als einzige Live-Region des FE. ``#feedstatus``, ``#jobstatuscard``,
``#clientsboard``, ``#jobs``, ``#tiles``, ``#runs`` und die Journal-Liste tun
das alle. Der Feed aktualisierte deshalb **nur** beim Seitenaufbau und beim
Klick auf ``LOAD MORE``.

**Warum es nicht auffiel:** man betritt den Feed und lädt dabei die Seite. Die
Einschätzung *„Der Feed scheint in Ordnung"* (m.rau, 2026-08-08) war
folgerichtig — er ist frisch, wenn man hinkommt, nicht während man hinsieht.

Der Fingerabdruck ist derselbe Griff wie ``_diff_git()``, im selben Takt: nur
bei echter Änderung wird gemeldet, der Takt begrenzt allein, **wie oft
nachgesehen** wird. Das ist die Kosten-Entscheidung, die schon für die Git-Zeile
getroffen wurde — ein Mindesttakt, der die Region periodisch dreckig macht,
wäre ein Poll durch die Hintertür und ist genau deshalb verworfen.
"""

from __future__ import annotations

from bibi.daemon.bus import Collector

#: Was der Feed liest, auf zwei Werte eingedampft — s. `Collector._read_feed()`.
STAND = {"oid": "a" * 40, "dirty": (("vault/case/a/README.md", "M"),)}
NEUER_COMMIT = dict(STAND, oid="b" * 40)
NEUE_DATEI = dict(STAND, dirty=(("vault/case/a/README.md", "M"),
                                ("vault/case/b/README.md", "?")))


class _Bus:
    def __init__(self) -> None:
        self.published: list[str] = []

    def publish_state(self, target: str, value: dict | None = None) -> None:
        self.published.append(target)


def _collector(bus, antworten):
    c = Collector(bus, registry=None)
    folge = iter(antworten)
    c._read_feed = lambda: next(folge, antworten[-1])
    c._primed = True
    return c


def _tick(c) -> int:
    """Ein Feed-Diff ohne Wartezeit — die Drossel wird unten eigens geprüft."""
    c._feed_last_check = 0.0
    return c._diff_feed()


def test_a_new_commit_dirties_the_feed():
    """Der Feed zeigt Commits — entsteht einer, muss er nachladen.

    Der neue Commit deckt zugleich die entdeckten Cases und die Agent-Slugs mit
    ab: beide stammen aus ``git log`` bzw. entstehen als Datei, die vorher
    unversioniert war."""
    bus = _Bus()
    c = _collector(bus, [STAND, NEUER_COMMIT])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == ["feed"]


def test_a_changed_vault_file_dirties_the_feed():
    """Die zweite Quelle: was noch nicht committet ist, steht in keinem
    ``git log`` und wäre sonst der einzige Zustand des Vaults, den der Feed
    nicht mitbekommt, während man hinsieht."""
    bus = _Bus()
    c = _collector(bus, [STAND, NEUE_DATEI])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == ["feed"]


def test_without_a_change_nothing_fires():
    """Der Gegentest, und die eigentliche Zusage.

    Gemeldet wird nur bei echter Änderung des Fingerabdrucks. Ein periodisches
    Dirty wäre der verworfene Gegenentwurf: es hätte die Region regelmäßig
    nachladen lassen, ohne dass sich etwas geändert hat."""
    bus = _Bus()
    c = _collector(bus, [STAND, dict(STAND)])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert bus.published == []


def test_the_feed_is_not_checked_on_every_tick():
    """Der Collector tickt sekündlich; der Fingerabdruck hängt an git."""
    bus = _Bus()
    c = _collector(bus, [STAND, NEUER_COMMIT])
    gelesen = {"n": 0}
    quelle = c._read_feed

    def _zaehlend():
        gelesen["n"] += 1
        return quelle()
    c._read_feed = _zaehlend

    _tick(c)                 # erster Blick
    c._diff_feed()           # sofort danach: die Drossel greift
    c._diff_feed()
    assert gelesen["n"] == 1


def test_the_feed_target_does_not_dirty_the_header():
    """Zwei Fingerabdrücke, zwei Ziele.

    Der Header hängt an einem git-Aufruf; ihn bei jeder Feed-Änderung
    mitzunehmen wäre genau der Firehose, den ``bus.py`` ausschließt."""
    bus = _Bus()
    c = _collector(bus, [STAND, NEUER_COMMIT])
    _tick(c)
    bus.published.clear()
    _tick(c)
    assert "feedstatus" not in bus.published


def test_an_unreadable_repo_is_not_a_crash():
    """Kein Repo, kein Feed — aber auch kein Absturz des Collector-Ticks."""
    bus = _Bus()
    c = Collector(bus, registry=None)
    c._primed = True
    c._read_feed = lambda: None
    c._feed_last_check = 0.0
    assert c._diff_feed() == 0
    assert bus.published == []
