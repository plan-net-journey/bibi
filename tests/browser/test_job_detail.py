"""Szenario 5: die Lauf-Liste im Job-Detail folgt dem Slot (`#131`).

**Der Befund aus dem Akzeptanz-Durchgang zu `v0.8.0`:** die Kachel meldet
`running · 28s`, während die Zeile darunter eine Minute lang `starting`
behauptet. Beide zeigen denselben Lauf; wer auf die Liste sieht — und das ist
der Ort, an dem der Output aufklappt —, hält ihn für im Anlauf steckengeblieben.

**Warum das hier steht und nicht eine Ebene tiefer.** Die Ereignisstrecke ist
serverseitig nachgemessen und trägt: der Slot-Wechsel erzeugt `journal:<slug>`,
das Ereignis erreicht den Client, und `GET /-/jobs/<uid>/runs` antwortet mit dem
**aktuellen** Zustand — ohne Cache, ohne auf einen Journal-INSERT zu warten.
Vier von fünf Gliedern sind damit belegt. Das fünfte ist die Strecke
*Ereignis → Refetch* im Browser, und die sieht keine andere Ebene.

**Die Lauf-Liste war auf dieser Ebene bisher gar nicht vertreten.** Die vier
bestehenden Szenarien prüfen Filter und Sortierung (Jobs-Screen), die Output-Box
und die Aktionsleiste — die Region `#runs` im Job-Detail in keinem. Genau die
Region, um die `#131` geht, lag auf der einzigen Ebene, die sie sehen kann, im
toten Winkel.

## Was diese Datei NICHT prüft, und das gehört an ihren Anfang

**Sie deckt den lokalen Lauf ab, nicht den beobachteten Fall.** ``/-/run`` ist
client-only, und ein lokaler Lauf geht durch ``Collector.tick_once()`` — den
Pfad, der ``live:`` und ``journal:`` seit jeher gemeinsam publiziert und der
nie gefehlt hat. Der Befund aus `#131` entstand dagegen an einem **Scheduler**-
Lauf, den ein **Client** anzeigt: zwei Prozesse, Ereignisse über das Abonnement
aus `#77`.

**Diese Tests sind deshalb grün, seit es sie gibt, und sie waren es auch vor dem
Fix.** Das ist kein Mangel — sie sichern eine Zusage, die vorher ungesichert war
— aber es ist ausdrücklich **kein** Beleg dafür, dass `#131` behoben ist. Was
dafür fehlt, ist ein Szenario mit zwei Knoten: ein Client, der einem Scheduler
beim Laufen zusieht. Ein Test, der grün ist, weil er die falsche Konstellation
stellt, ist gefährlicher als gar keiner — er sieht aus wie Abdeckung.
"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from .browserlib import anfragen, job_md, warte_bis  # noqa: E402

pytestmark = pytest.mark.browser


#: Wie in Szenario 1: `/-/run` ist client-only, und ein lokaler Lauf ist der
#: billigste echte Zustandswechsel, den ein Test auslösen kann.
_CLIENT = "--synchronizer --controller"

#: Lang genug, dass `running` ein Fenster hat, in dem man es sehen kann. Der
#: Fehler lebt **im** Fenster zwischen `starting` und dem Journal-INSERT — ein
#: Job, der sofort fertig ist, überspringt genau die Lage, um die es geht.
_LAUFZEIT_S = 6


def _uid(slug: str) -> str:
    """Die Job-uid der Detail-Route — derselbe Digest wie in `render.py::_uid`."""
    return hashlib.md5(slug.encode()).hexdigest()  # noqa: S324


def _zeilen_zustaende(seite) -> list[str]:
    """Die Zustände, die die Lauf-Liste in ihren Zeilen zeigt.

    Die STATUS-Zelle trägt **keine eigene Klasse** — sie ist die vierte Spalte
    (``mark``, ``TIME``, ``SRC``, ``STATUS``). Das ist der Grund, warum hier
    ``nth-child`` steht statt eines sprechenden Selektors: eine Klasse zu
    erfinden, um den Test hübscher zu machen, hieße den Prüfgegenstand für die
    Prüfung zu ändern."""
    return seite.eval_on_selector_all(
        "#runs table.runs tbody tr.run td:nth-child(4)",
        "els => els.map(e => e.textContent.trim())")


def _kachel_zustand(seite) -> str:
    """Was die Kacheln über den Slots sagen — die Gegenprobe in derselben Sekunde.

    Beide Kacheln (Client und Scheduler) zusammen: welche von beiden den Lauf
    trägt, hängt daran, wer ihn ausgelöst hat, und der Test soll an dieser
    Stelle nicht mitraten."""
    return seite.eval_on_selector_all(
        "#tiles .tile-state",
        "els => els.map(e => e.textContent.trim()).join(' | ')")


def test_the_run_row_follows_the_slot_into_running(fabrik, seite):
    """`starting → running` bewegt die Lauf-Liste, ohne dass jemand neu lädt.

    **Der Kern von `#131`, als Zusage formuliert:** Kachel und Zeile zeigen zu
    keinem Zeitpunkt verschiedene Zustände desselben Laufs.

    Geprüft wird die **Wirkung**, nicht die Verdrahtung — ein Test, der
    `data-bus="journal:<slug>"` im Markup findet, wäre auch beim Auftreten des
    Fehlers grün gewesen: das Attribut steht seit `#43` dort, es feuerte nur
    niemand darauf.
    """
    root = fabrik.repo("knoten")
    job_md(root, "laeuft-kurz", payload=f"job: sleep {_LAUFZEIT_S}")
    k = fabrik.starte(root, rollen=_CLIENT)
    k.post("/-/rescan")
    seite.goto(k.url + "/-/jobs/" + _uid("laeuft-kurz"))

    vorher = len(anfragen(seite, "/runs"))
    code, body = k.post_json("/-/run", {"slug": "laeuft-kurz"})
    assert code == 200, f"der Lauf startete nicht: {code} {body}"

    # Die Kachel ist die schnellere der beiden Anzeigen — sie hängt an `live:`,
    # das in jedem Fall feuert. Sie ist deshalb der Taktgeber: sobald *sie*
    # `running` sagt, muss die Zeile es auch sagen. Auf sie zu warten statt auf
    # eine feste Frist macht den Test unabhängig von der Startdauer.
    warte_bis(lambda: "running" in _kachel_zustand(seite),
              frist=30, takt=0.2,
              was="die Kachel erreichte nie `running` — dann prüft dieser Test "
                  "nicht, was er prüfen soll")

    warte_bis(lambda: len(anfragen(seite, "/runs")) > vorher,
              frist=10, takt=0.2,
              was="die Lauf-Liste wurde nie per Bus nachgeladen — das Ereignis "
                  "kommt an (serverseitig belegt), löst im Browser aber keinen "
                  "Refetch aus")

    zustaende = _zeilen_zustaende(seite)
    assert "starting" not in zustaende, (
        f"die Kachel sagt `running`, die Zeile darunter noch `starting` "
        f"(Zeilen: {zustaende}) — genau der Widerspruch aus #131")
    assert any("running" in z for z in zustaende), (
        f"die Lauf-Liste zeigt den laufenden Slot nicht als `running` "
        f"(Zeilen: {zustaende})")


def test_the_run_list_does_not_refetch_while_nothing_changes(fabrik, seite):
    """Die Gegenprobe, und sie ist hier teurer als sie aussieht.

    Ein Refetch je Tick statt je Wechsel wäre am Symptom nicht zu unterscheiden
    — die Zeile stimmte ja —, nähme der Liste aber Scroll-Position und
    Faltzustand, und zwar mitten im Mitlesen. Der Faltzustand wird seit `#44`
    eigens über den Swap gerettet; ein Fix, der die Swap-Frequenz erhöht, macht
    genau diese Rettung zur Dauerbeschäftigung.
    """
    root = fabrik.repo("knoten")
    job_md(root, "ruht", payload="job: echo hallo")
    k = fabrik.starte(root, rollen=_CLIENT)
    k.post("/-/rescan")
    seite.goto(k.url + "/-/jobs/" + _uid("ruht"))
    warte_bis(lambda: len(anfragen(seite, "/runs")) >= 0, frist=5, takt=0.2,
              was="die Seite kam nicht hoch")

    stand = len(anfragen(seite, "/runs"))
    seite.wait_for_timeout(4000)
    assert len(anfragen(seite, "/runs")) == stand, (
        "die Lauf-Liste lädt nach, obwohl sich nichts geändert hat — ein "
        "Refetch je Tick statt je Wechsel")
