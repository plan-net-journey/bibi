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
    """Der **Client** eines Paars, auf dessen Scheduler ein Lauf läuft.

    **Bis zum 2026-08-09 stand hier ein einzelner Knoten**, und die Begründung
    dafür war selbst schon ein Befund: eine Box mit ``data-stream`` entstand
    nur in der Konstellation ``scheduler+worker+controller`` mit einer
    Scheduler-Adresse auf sich selbst. Auf einem reinen Client gab es sie gar
    nicht — ``_detail_data()`` fragte den eigenen Daemon, und dessen ``/-/job``
    antwortet ohne Scheduler-Rolle mit dem eingefrorenen ``501``-Stub.

    **Diese eine Konstellation war die falsche**
    ([`#86`](https://github.com/plan-net-journey/bibi/issues/86)). Sie ist seit
    dem Rollenwechsel am 2026-08-04 in der Produktion nirgends mehr im Einsatz,
    und sie schickte ihre Box über einen Durchreicher zu sich selbst — obwohl
    der Docstring von ``_output_stream_url()`` genau das ausschloss. Seit der
    Fix die **Rolle** prüft statt der Adresse, gibt es dort keinen zweiten
    Strom mehr, und das ist richtig: der globale Bus reicht.

    Was bleibt, ist die Topologie, für die der zweite Strom überhaupt gebaut
    wurde — der Lauf beim Scheduler, die Oberfläche beim Client. Genau die
    verlangt das Ticket auch als Prüfgrundlage.
    """
    from .browserlib import paar

    host, client = paar(fabrik, job="lang", payload=_SPRECHENDER_LAUF)
    zeilen = warte_bis(lambda: [j for j in host.get_json("/-/job")
                                if j.get("slug") == "lang"],
                       frist=20, was="der Job tauchte beim Scheduler nicht auf")
    host.post(f"/-/job/{zeilen[0]['id']}/start")
    warte_bis(lambda: any(j.get("status") == "running"
                          for j in host.get_json("/-/job") if j.get("slug") == "lang"),
              frist=30, was="der Lauf kam nie in den Zustand running")
    return client


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



#: Ein Lauf, dessen letzte Ausgabe unmittelbar vor dem Ende kommt. Genau diese
#: Zeile fehlte m.rau am 2026-08-09 im FE, während der Status schon `complete`
#: sagte — sie erschien erst nach einem manuellen Reload.
_LAUF_MIT_SCHLUSSWORT = "job: bash -c 'echo ANFANG; sleep 2; echo ENDE'"


def test_the_last_output_line_arrives_without_a_reload(fabrik, seite):
    """**Der Rot-Schritt von `#105`.**

    Beobachtung m.rau: *„Output wird bei lokalem Client immer noch nicht
    aktualisiert! … Status Complete, ohne dass ich im Output `ENDE` sehe. Beim
    Reload kommt das."*

    **Es ist kein Race, sondern eine Entscheidung, die einen Fall nicht bedacht
    hat.** Der Faltzustand-Retter aus `#44` sichert den Text der offenen Box vor
    einem `#runs`-Swap und schreibt ihn danach zurück. Sein eigener Kommentar
    nennt den Grund:

        Den Text mitretten statt neu zu holen: er ist schon da, und ein
        Roundtrip je Refetch waere bei einem laufenden Job der Sekundentakt.

    Bei einem laufenden Job ist der Text aber **noch nicht fertig**. Wird der
    Lauf terminal, feuert der Bus, `#runs` wird getauscht — und der Retter
    schreibt den alten, unvollständigen Stand zurück. Danach kommt kein
    Ereignis mehr, das ihn korrigieren könnte.

    Der dritte Anlauf an dieser Stelle: `#78` hat den Strom durchgereicht,
    `#86` die Box gebaut. Beide prüfen, **dass** etwas ankommt — keiner, dass
    nichts fehlt, **wenn es vorbei ist**.
    """
    from .browserlib import paar
    from bibi.schedule.models import job_uid

    host, client = paar(fabrik, job="schluss", payload=_LAUF_MIT_SCHLUSSWORT)
    zeilen = warte_bis(lambda: [j for j in host.get_json("/-/job")
                                if j.get("slug") == "schluss"],
                       frist=20, was="der Job tauchte beim Scheduler nicht auf")
    host.post(f"/-/job/{zeilen[0]['id']}/start")

    seite.goto(client.url + f"/-/jobs/{job_uid('schluss')}")

    # Aufklappen, **waehrend** er laeuft — das ist die Lage, um die es geht.
    knopf = warte_bis(lambda: seite.query_selector(".run-show"),
                      frist=30, was="es erschien keine Lauf-Zeile")
    knopf.click()
    warte_bis(lambda: "ANFANG" in (seite.query_selector(".out-body").text_content() or ""),
              frist=30, was="die Box zeigte nicht einmal den Anfang")

    # Jetzt laeuft er zu Ende. Der Bus tauscht `#runs`, der Retter greift.
    warte_bis(lambda: any(j.get("status") == "complete"
                          for j in host.get_json("/-/job")
                          if j.get("slug") == "schluss"),
              frist=40, was="der Lauf wurde nie terminal")

    warte_bis(
        lambda: "ENDE" in (
            (seite.query_selector(".out-body").text_content() or "")
            if seite.query_selector(".out-body") else ""),
        frist=20,
        was="die letzte Ausgabezeile fehlt, obwohl der Lauf fertig ist — erst "
            "ein Reload holt sie (#105)")
