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

**Die Region war auf dieser Ebene gar nicht vertreten.** Die vier bestehenden
Szenarien prüfen Filter und Sortierung (Jobs-Screen), die Output-Box und die
Aktionsleiste — die Region `#runs` im Job-Detail in keinem. Genau die Region,
um die `#131` geht, lag auf der einzigen Ebene, die sie sehen kann, im toten
Winkel.

## Die Topologie ist der Prüfgegenstand, nicht sein Rahmen

**Der erste Anlauf dieser Datei prüfte einen lokalen Lauf und war grün — auch
vor dem Fix.** `/-/run` ist client-only, und ein lokaler Lauf geht durch
`Collector.tick_once()`, den Pfad, der `live:` und `journal:` seit jeher
gemeinsam publiziert und der nie gefehlt hat. Der Befund entstand dagegen an
einem **Scheduler**-Lauf, den ein **Client** anzeigt: zwei Prozesse, Ereignisse
über das Abonnement aus `#77`.

**Ein Test, der grün ist, weil er die falsche Konstellation stellt, ist
gefährlicher als gar keiner — er sieht aus wie Abdeckung.** Deshalb läuft der
Kern dieser Datei über `paar()`, und der lokale Fall bleibt als ausdrücklich
benannte Gegenprobe daneben stehen: er sichert, dass der Weg, der immer trug,
weiter trägt.

## Diese Datei reproduziert `#131` nicht, und das ist nachgemessen

**Auch in der Zwei-Knoten-Topologie ist der Widerspruch nicht herzustellen.**
Verifiziert durch Zurücknehmen des Fixes (`692ebc6`): mit und ohne ihn sind
alle drei Tests grün. Der Fehler tritt hier also nicht auf — weder wird er vom
Fix behoben, noch fehlt dem Test die richtige Topologie.

**Was das über den Fix aussagt:** er betrifft `_diff_scheduler_jobs()`, den
**Poll-Rückfall**. In diesem Aufbau steht das Abonnement, die Funktion kehrt
sofort zurück, und die Ereignisse kommen gespiegelt vom Scheduler — dessen
lokaler Pfad beide Ziele publiziert. Der Fix ist durch drei Tests in
`tests/test_bus.py` belegt, zwei davon rot gesehen; **belegt ist er dort, nicht
hier.**

**Was offen bleibt:** die Bedingung, unter der der Widerspruch in der
Produktion entstand. Kandidaten sind ein zeitweise abgerissenes Abonnement
(dann greift der behobene Pfad und der Fix wirkt), Last, oder ein Timing, das
ein frisch aufgesetztes Paar nicht erreicht. **Diese Datei schließt keinen
davon aus — sie hält fest, dass drei naheliegende Konstellationen es nicht
sind.** Das ist weniger als eine Diagnose und mehr als eine Vermutung.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from .browserlib import anfragen, job_md, paar, warte_bis  # noqa: E402

pytestmark = pytest.mark.browser


#: Lang genug, dass `running` ein Fenster hat, in dem man es sehen kann. Der
#: Fehler lebt **im** Fenster zwischen `starting` und dem Journal-INSERT — ein
#: Lauf, der sofort fertig ist, überspringt genau die Lage, um die es geht.
_LANGER_LAUF = "job: sleep 20"

#: Wie in Szenario 1: `/-/run` ist client-only, und ein lokaler Lauf ist der
#: billigste echte Zustandswechsel — hier nur noch für die Gegenprobe.
_CLIENT = "--synchronizer --controller"


def _uid(slug: str) -> str:
    from bibi.schedule.models import job_uid
    return job_uid(slug)


def _zeilen_zustaende(seite) -> list[str]:
    """Die Zustände, die die Lauf-Liste in ihren Zeilen zeigt.

    Die STATUS-Zelle trägt **keine eigene Klasse** — sie ist die vierte Spalte
    (`mark`, `TIME`, `SRC`, `STATUS`). Das ist der Grund, warum hier
    `nth-child` steht statt eines sprechenden Selektors: eine Klasse zu
    erfinden, um den Test hübscher zu machen, hieße den Prüfgegenstand für die
    Prüfung zu ändern."""
    return seite.eval_on_selector_all(
        "#runs table.runs tbody tr.run td:nth-child(4)",
        "els => els.map(e => e.textContent.trim())")


def _kachel_zustand(seite) -> str:
    """Was die Kacheln über den Slots sagen — die Gegenprobe in derselben
    Sekunde. Beide Kacheln zusammen: welche den Lauf trägt, hängt daran, wer
    ihn ausgelöst hat, und der Test soll an dieser Stelle nicht mitraten."""
    return seite.eval_on_selector_all(
        "#tiles .tile-state",
        "els => els.map(e => e.textContent.trim()).join(' | ')")


def _starte_beim_scheduler(host, slug: str) -> None:
    zeilen = warte_bis(lambda: [j for j in host.get_json("/-/job")
                                if j.get("slug") == slug],
                       frist=20, was="der Job tauchte beim Scheduler nicht auf")
    host.post(f"/-/job/{zeilen[0]['id']}/start")


def test_the_run_row_follows_a_scheduler_slot_into_running(fabrik, seite):
    """**Der Kern von `#131`, in der Topologie, in der er beobachtet wurde.**

    Ein Lauf beim Scheduler, das Job-Detail im Browser des Clients, die Seite
    **vor** dem Start offen — sonst entsteht der Zustand beim Seitenaufbau und
    nicht über den Bus, und der Test prüfte das Rendern statt das Nachziehen.

    Als Zusage formuliert: Kachel und Zeile zeigen zu keinem Zeitpunkt
    verschiedene Zustände desselben Laufs.

    Geprüft wird die **Wirkung**, nicht die Verdrahtung — ein Test, der
    `data-bus="journal:<slug>"` im Markup findet, wäre auch beim Auftreten des
    Fehlers grün gewesen: das Attribut steht seit `#43` dort, es feuerte nur
    niemand darauf.
    """
    host, client = paar(fabrik, job="lang", payload=_LANGER_LAUF)
    seite.goto(client.url + "/-/jobs/" + _uid("lang"))
    vorher = len(anfragen(seite, "/runs"))

    _starte_beim_scheduler(host, "lang")

    # Die Kachel ist die schnellere der beiden Anzeigen — sie hängt an `live:`,
    # das in jedem Fall feuert. Sie ist deshalb der Taktgeber: sobald *sie*
    # `running` sagt, muss die Zeile es auch sagen. Auf sie zu warten statt auf
    # eine feste Frist macht den Test unabhängig von der Startdauer.
    warte_bis(lambda: "running" in _kachel_zustand(seite),
              frist=40, takt=0.2,
              was=f"die Kachel erreichte nie `running` — dann prüft dieser Test "
                  f"nicht, was er prüfen soll\n{client.ausgabe()}")

    warte_bis(lambda: len(anfragen(seite, "/runs")) > vorher,
              frist=15, takt=0.2,
              was="die Lauf-Liste wurde nie per Bus nachgeladen — das Ereignis "
                  "kommt an (serverseitig belegt), löst im Browser aber keinen "
                  "Refetch aus")

    zustaende = warte_bis(
        lambda: (_zeilen_zustaende(seite)
                 if any("running" in z for z in _zeilen_zustaende(seite)) else None),
        frist=15, takt=0.2,
        was=f"die Kachel sagt `running`, die Lauf-Liste nicht — genau der "
            f"Widerspruch aus #131 (Zeilen zuletzt: {_zeilen_zustaende(seite)})")
    assert "starting" not in zustaende, (
        f"die Zeile steht auf `starting`, während die Kachel `running` sagt "
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
    seite.wait_for_selector("#runs", timeout=10_000)

    stand = len(anfragen(seite, "/runs"))
    seite.wait_for_timeout(4000)
    assert len(anfragen(seite, "/runs")) == stand, (
        "die Lauf-Liste lädt nach, obwohl sich nichts geändert hat — ein "
        "Refetch je Tick statt je Wechsel")


def test_a_local_run_still_moves_the_row(fabrik, seite):
    """Der Weg, der immer trug, trägt weiter — ausdrücklich als Gegenprobe.

    Ein lokaler Lauf geht durch `tick_once()`, wo `live:` und `journal:`
    gemeinsam publiziert werden. Dieser Test war schon vor dem Fix grün, und
    das ist hier kein Mangel, sondern seine Aufgabe: er sichert die Hälfte, die
    funktioniert, gegen einen Umbau, der sie mitnimmt.
    """
    root = fabrik.repo("knoten")
    job_md(root, "lokal", payload="job: sleep 6")
    k = fabrik.starte(root, rollen=_CLIENT)
    k.post("/-/rescan")
    seite.goto(k.url + "/-/jobs/" + _uid("lokal"))

    code, body = k.post_json("/-/run", {"slug": "lokal"})
    assert code == 200, f"der Lauf startete nicht: {code} {body}"
    warte_bis(lambda: "running" in _kachel_zustand(seite), frist=30, takt=0.2,
              was="die Kachel erreichte nie `running`")
    zustaende = warte_bis(
        lambda: (_zeilen_zustaende(seite)
                 if any("running" in z for z in _zeilen_zustaende(seite)) else None),
        frist=15, takt=0.2, was="die Lauf-Liste zeigt den lokalen Lauf nicht")
    assert "starting" not in zustaende, f"Zeilen: {zustaende}"
