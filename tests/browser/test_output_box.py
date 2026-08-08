"""Szenario 2: die Output-Box wächst über Refetches hinweg (`#82`, `#78`, `#84`).

Der Befund aus der Abnahme von `v0.7.7`, m.rau am 2026-08-08: *„der Refresh —
insbesondere beim Output eines laufenden Jobs — funktioniert noch nicht so ganz.
Was soll ich sagen. Vielleicht ein Gefühl."*

Dahinter lagen zwei Fehler, die sich gegenseitig verstärkten. `#77` ließ das
Abonnement des Clients im Sekundentakt neu verbinden, und **jeder Verbindungs-
aufbau schickt den Resync des Schedulers** — also für jeden laufenden Lauf ein
`live:<slug>`. Der Browser lud die Region daraufhin alle paar Sekunden nach.
`#82` machte aus jedem dieser Nachladevorgänge eine zurückgelassene
`EventSource`: der `outerHTML`-Swap ersetzte die Box, die neue öffnete ihren
eigenen Strom, die alte behielt ihren. Nach `_MAX_OUTPUT_PROXIES` antwortete der
Durchreicher `429`, und die Box wuchs nicht mehr.

**Warum der Nachweis in den Browser gehört, steht im Fix selbst.** `81ea6dd`
hat einen serverseitigen Zähler-Test wieder ausgebaut: mit `TestClient.stream()`
startet der Generator erst beim Lesen, der Lauf brauchte 112 Sekunden und traf
am Ende keine Aussage. Der Kommentar an seiner Stelle sagt, was stattdessen zu
tun ist: *„Wer ein Browser-Problem serverseitig nachstellen will, baut den
Browser nach."*
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from .browserlib import job_md, stroeme, warte_bis  # noqa: E402

pytestmark = pytest.mark.browser

#: Ein Lauf, der spricht — **mit Pausen**.
#:
#: Die drei Sekunden zwischen zwei Zeilen sind der ganze Unterschied zwischen
#: einem Test, der `#82` findet, und einem, der auch `#78` findet. Der
#: Durchreicher las bis `a7648ed` mit einem Sekunden-Timeout, während der
#: Output-Strom des Schedulers erst nach 15 s Stille pingt: jede Pause, die
#: länger als eine Sekunde dauert, warf die Verbindung weg und setzte sie auf
#: einem unbrauchbaren Strom fort. **Stille ist beim Output der Normalfall** —
#: ein Job denkt nach —, und ein Lauf, der im Sekundentakt plappert, geht an
#: genau diesem Fehler vorbei. Beim ersten Bau dieser Datei tat er das: eine
#: Zeile je Sekunde, und der Fehlerstand blieb grün.
_SPRECHENDER_LAUF = ("job: bash -c 'for i in 1 2 3 4 5 6 7 8 9 10 11 12; "
                     "do echo Zeile $i; sleep 3; done'")

#: So oft wird die Region nachgeladen. Vier reichen: das Leck war linear (eine
#: Leiche je Refetch), und der Durchreicher gibt bei `_MAX_OUTPUT_PROXIES` = 8
#: auf. Mehr Runden kosten nur Laufzeit.
_RUNDEN = 4

#: Wie lange dem Strom beim Stillhalten zugesehen wird. Drei Sendepausen des
#: Laufs — lang genug, dass ein Sekunden-Timeout mehrfach zuschlüge, kurz genug,
#: dass der 15-Sekunden-Ping der Gegenseite die Messung nicht verwässert.
_STILLE_S = 9.0


def _knoten_mit_laufender_box(fabrik):
    """Ein Knoten, auf dem eine Box mit **eigenem Strom** entsteht, samt Lauf.

    **Die Rollenwahl ist hier der halbe Test, und sie ist unbequem.** Eine Box
    mit ``data-stream`` entsteht nur, wenn ``_output_stream_url()`` eine URL
    liefert — und das verlangt zweierlei: einen laufenden Lauf, den
    ``client.jobs()`` **sieht**, und eine gesetzte ``BIBI_SCHEDULER_URL``. Der
    ``ControllerClient`` fragt dafür den eigenen Daemon (``/-/job``), und diese
    Route hat nur ein Knoten mit Scheduler-Rolle. Auf einem reinen Client
    antwortet sie mit dem eingefrorenen ``501``-Stub — dort gibt es die Box
    also gar nicht.

    Der Aufbau hier ist folglich das Profil ``scheduler+worker`` **mit**
    Oberfläche (``profile_roles(..., with_ui=True)``, gedacht für den ersten
    Knoten eines Teams) und einer Scheduler-Adresse, die auf ihn selbst zeigt —
    genau die Konstellation, die `d2c03bc` auf sarasate vorfand.

    **Dass das die einzige ist, in der es die Box gibt, ist ein Befund und kein
    Testdetail** — er steht in
    [`#86`](https://github.com/plan-net-journey/bibi/issues/86).
    """
    root = fabrik.repo("knoten")
    job_md(root, "lang", payload=_SPRECHENDER_LAUF)
    k = fabrik.starte(root, rollen="--synchronizer --scheduler --worker --controller",
                      scheduler_ist_selbst=True)
    zeilen = warte_bis(lambda: [j for j in k.get_json("/-/job")
                                if j.get("slug") == "lang"],
                       frist=20, was="der Job tauchte beim Scheduler nicht auf")
    k.post(f"/-/job/{zeilen[0]['id']}/start")
    warte_bis(lambda: any(j.get("status") == "running"
                          for j in k.get_json("/-/job") if j.get("slug") == "lang"),
              frist=30, was="der Lauf kam nie in den Zustand running")
    return k


def _box(seite):
    return seite.query_selector(".liveterm[data-stream]")


def _zeilen_im_kasten(seite) -> int:
    return seite.evaluate(
        "() => { const b = document.querySelector('.liveterm[data-stream]');"
        " return b ? b.textContent.split('\\n').filter(z => z.trim()).length : 0; }")


def _lade_region_nach(seite) -> None:
    """Die Region nachladen — **genau so, wie der Bus es tut**.

    Das ist die Zeile aus ``_EVENTS_JS``, Zeichen für Zeichen: dieselbe
    htmx-Fassung, derselbe ``outerHTML``-Swap, dieselbe Aufräumkette. Nur der
    Auslöser ist hier der Test statt eines Ereignisses vom Host — und das ist
    Absicht, nicht Bequemlichkeit: **was ein Refetch mit den Strömen macht,
    hängt nicht davon ab, wer ihn anstößt**, und der Auslöser selbst ist in
    Szenario 1 und 3 belegt.

    Ihn hier realistisch zu erzeugen, hieße den Client zum Wiederverbinden zu
    zwingen (der Resync des Hosts ist es, der `live:<slug>` mehrfach meldet) —
    also ausgerechnet `#77` nachzustellen, um `#82` zu prüfen. Zwei Fehler in
    einem Test, und keiner davon sauber.
    """
    seite.evaluate("""() => {
      const el = document.querySelector('[data-bus^="live:"]');
      htmx.ajax('GET', el.getAttribute('data-bus-refetch'),
                {source: el, target: el, swap: 'outerHTML'});
    }""")


def test_the_box_of_a_scheduler_run_gets_its_own_stream(fabrik, seite):
    """Die Voraussetzung, ohne die der Rest nichts prüft (`#78`).

    Ein Lauf beim Scheduler hat auf dem Client keine ``output.jsonl`` — der
    globale Bus kann die Box also nicht speisen. Sie bekommt deshalb einen
    eigenen Strom auf den Durchreicher dieses Knotens. Steht das Attribut
    nicht, ist jede Aussage der folgenden Tests über „den Strom der Box"
    gegenstandslos."""
    k = _knoten_mit_laufender_box(fabrik)
    seite.goto(k.url + "/-/ui/schedule/lang")
    warte_bis(lambda: _box(seite) is not None, frist=30,
              was=f"die Box eines laufenden Scheduler-Laufs fehlt\n{k.ausgabe()}")
    assert "/output/stream" in _box(seite).get_attribute("data-stream")


def test_the_number_of_open_streams_does_not_grow_with_refetches(fabrik, seite):
    """**Das Szenario aus `#84`, Nummer 2 — die Zusage von `#82`.**

    Vier Refetches, vier neue Boxen, vier neue Ströme — und danach dürfen
    trotzdem nur zwei offen sein: der globale Ereignisstrom der Seite und der
    der aktuellen Box. Jede darüber hinaus ist eine Leiche, und acht davon
    lassen den Durchreicher `429` antworten.

    Gezählt wird im Browser, weil die Aussage eine über den Browser ist: eine
    Box, die verschwindet, schließt ihren Strom. Serverseitig sieht man
    denselben Sachverhalt erst, wenn er längst geschadet hat."""
    k = _knoten_mit_laufender_box(fabrik)
    seite.goto(k.url + "/-/ui/schedule/lang")
    warte_bis(lambda: _box(seite) is not None, frist=30, was="die Box kam nicht")

    vorher = stroeme(seite)
    for runde in range(_RUNDEN):
        _lade_region_nach(seite)
        warte_bis(lambda: _box(seite) is not None, frist=10,
                  was=f"die Box war nach Refetch {runde + 1} weg")
        warte_bis(lambda: stroeme(seite)["gebaut"] > vorher["gebaut"] + runde,
                  frist=10, was=f"Refetch {runde + 1} baute keinen neuen Strom auf")

    jetzt = stroeme(seite)
    assert jetzt["gebaut"] >= vorher["gebaut"] + _RUNDEN, (
        "die Refetches haben gar keine neuen Ströme erzeugt — dann prüft dieser "
        f"Test nichts: {jetzt}")
    assert jetzt["offen"] <= 2, (
        f"{jetzt['offen']} offene Ströme nach {_RUNDEN} Refetches — jede "
        "ausgetauschte Box lässt ihren Strom offen zurück (#82). Erlaubt sind "
        f"zwei: der Ereignisstrom der Seite und der der aktuellen Box. {jetzt}")


def test_the_box_keeps_growing_across_refetches(fabrik, seite):
    """Die Wirkung, um die es dem Nutzer geht — und der Punkt, an dem das Leck
    sichtbar wurde.

    Ein Strom, der leckt, fällt nicht als Zähler auf, sondern als Box, die
    stehenbleibt. Genau das war m.raus *„der Refresh funktioniert noch nicht so
    ganz"*. Dieser Test misst deshalb nicht die Verbindungen, sondern die
    Zeilen: nach den Refetches muss mehr dastehen als davor."""
    k = _knoten_mit_laufender_box(fabrik)
    seite.goto(k.url + "/-/ui/schedule/lang")
    warte_bis(lambda: _zeilen_im_kasten(seite) > 0, frist=30,
              was="die Box blieb leer")

    vorher = _zeilen_im_kasten(seite)
    for _ in range(_RUNDEN):
        _lade_region_nach(seite)
        warte_bis(lambda: _box(seite) is not None, frist=10, was="die Box war weg")

    warte_bis(lambda: _zeilen_im_kasten(seite) > vorher, frist=30,
              was=f"die Box wächst nach {_RUNDEN} Refetches nicht mehr — der "
                  f"Durchreicher antwortet vermutlich 429 (#82). Vorher: {vorher}")


def test_the_box_stream_survives_a_silent_stretch(fabrik, seite):
    """**Die Zusage von `#78`, und sie braucht ein anderes Maß.**

    Der naheliegende Test — „nach den Refetches steht mehr in der Box" — kann
    diesen Fehler nicht sehen, und das ist beim Bau dieser Datei zunächst
    passiert. Jeder Refetch liefert die Box **frisch geseedet** aus: der Server
    rendert den bisherigen Output hinein. Die Box wächst dadurch auch dann,
    wenn ihr Strom längst tot ist — gemessen würde der Refetch, nicht der
    Strom.

    Gezählt wird deshalb, wie oft die Verbindung **aufgebaut** wird, ohne dass
    jemand nachlädt. Bis `a7648ed` las der Durchreicher mit einem
    Sekunden-Timeout, während der Output-Strom des Schedulers erst nach 15 s
    Stille pingt; jede Pause über einer Sekunde warf die Verbindung weg, und
    der Browser holte sie umgehend nach. Ein Job wie `burndown-app`, der alle
    300 s erhebt, hätte seine Box im Sekundentakt neu verbunden.

    Ein Aufbau ist der richtige Wert: der erste."""
    k = _knoten_mit_laufender_box(fabrik)
    seite.goto(k.url + "/-/ui/schedule/lang")
    warte_bis(lambda: _box(seite) is not None, frist=30, was="die Box kam nicht")
    warte_bis(lambda: stroeme(seite)["geoeffnet"] >= 2, frist=15,
              was="der Strom der Box kam nie zustande")

    vorher = stroeme(seite)["neuverbunden"]
    time.sleep(_STILLE_S)
    jetzt = stroeme(seite)

    assert jetzt["neuverbunden"] - vorher == 0, (
        f"{jetzt['neuverbunden'] - vorher} Wiederverbindungen in "
        f"{_STILLE_S:.0f}s, in denen der Lauf nur mit Pausen sprach — der "
        "Durchreicher wirft die Verbindung weg, bevor der Output-Strom pingt "
        f"(#78). {jetzt}")
