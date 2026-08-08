"""Szenario 3: der Strom ist ruhig, wenn nichts passiert (`#77`, `#84`).

Gefunden am 2026-08-08, Minuten nach dem Rollout von `v0.7.6`, und nicht durch
eine Meldung, sondern durchs Mithören: am Client-Strom liefen `live:`/`journal:`
alle vier Sekunden ohne Wert vorbei, im Scheduler-Log standen **100
Verbindungsaufbauten in zwei Minuten**.

Die Ursache war eine Absicht, die sich umkehrte. Der Socket-Timeout stand auf
einer Sekunde, damit `stop()` schnell greift — nach einem `socket.timeout` ist
der `http.client`-Strom aber unbrauchbar. Der Lese-Loop fiel im Sekundentakt
heraus, wartete die Retry-Pause ab und verband neu. **Und weil der Scheduler bei
jedem Verbindungsaufbau seinen Resync schickt, meldete das jedes Mal jede aktive
Live-Region als dreckig.** Statt den Fünf-Sekunden-Poll zu ersetzen, lud ein
offener Tab nun alle vier Sekunden alles nach.

**Warum das nur ein Browser sieht.** Serverseitig ist jeder einzelne Schritt
korrekt: der Strom bricht ab, der Client verbindet neu, der Scheduler
resynchronisiert, der Client meldet die Region dreckig, der Browser lädt nach.
Kein Test kann sagen „das ist zu oft", solange niemand mitzählt, was an einem
offenen Tab tatsächlich ankommt. Genau das tut dieser hier.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from .browserlib import anfragen, job_md, paar, warte_bis  # noqa: E402

pytestmark = pytest.mark.browser

#: Ein Lauf, der lange genug lebt, um die Messung zu überdauern, und dabei
#: **schweigt**. Stille ist der Punkt: ein Job, der Zeilen ausgibt, erzeugt
#: `append`-Ereignisse und damit echten Verkehr — der ist hier nicht der Fehler
#: und würde die Messung verwässern.
_STILLER_LAUF = "job: sleep 60"

#: Wie lange am offenen Tab gemessen wird.
#:
#: Zwölf Sekunden sind kein runder Wert, sondern vier Retry-Pausen
#: (``_SUB_RETRY_S`` = 3 s). Vor `d2c03bc` verband sich das Abonnement in genau
#: diesem Takt neu; das Produktionsbild waren *„vier in vier Sekunden"*. Wer
#: kürzer misst, kann einen einzelnen Neuaufbau für Rauschen halten.
_FENSTER_S = 12.0

#: Wie viele Refetches in diesem Fenster noch als „ruhig" gelten.
#:
#: Nicht null: ein Lauf wechselt beim Starten seinen Zustand, und wenn dieser
#: Wechsel ins Fenster fällt, ist der Refetch **richtig**. Eine Schwelle von
#: eins unterscheidet „meldet, was passiert" von „meldet im Takt".
_ERLAUBT = 1


def test_an_open_tab_is_quiet_while_nothing_happens(fabrik, seite):
    """**Das Szenario aus `#84`, Nummer 3.**

    Ein Client mit offenem Tab auf der Detailseite eines Laufs, der beim
    Scheduler läuft und schweigt. In zwölf Sekunden darf die Live-Region
    höchstens einmal nachladen.

    Die Detailseite und nicht der Jobs-Screen: der Resync des Schedulers meldet
    beim Verbindungsaufbau **die aktiven Live-Regionen** (`live:<slug>`,
    `journal:<slug>`), nicht das Sammel-Target `jobs`. Ein Tab auf der Liste
    hätte den Sturm also gar nicht gesehen — er traf die Seite, auf der man
    einem Lauf zusieht, und das ist die, auf der man am längsten steht.
    """
    host, client = paar(fabrik)
    # **In beide Vaults.** Im Betrieb teilen sich die Knoten einen Vault über
    # git; hier sind es zwei Verzeichnisse, und der Synchronizer hat kein
    # gemeinsames Origin. Die MD von Hand auf beide Seiten zu legen ist der
    # ehrlichere Weg als ein Origin nur für diesen Zweck: geprüft wird der
    # Ereignisweg, nicht der Sync.
    job_md(host.root, "still", payload=_STILLER_LAUF)
    job_md(client.root, "still", payload=_STILLER_LAUF)
    assert host.post("/-/rescan")[0] == 200
    zeilen = warte_bis(lambda: [j for j in host.get_json("/-/job")
                                if j.get("slug") == "still"],
                       frist=20, was="der Job tauchte beim Scheduler nicht auf")
    host.post(f"/-/job/{zeilen[0]['id']}/start")
    warte_bis(lambda: any(j.get("status") == "running"
                          for j in host.get_json("/-/job") if j.get("slug") == "still"),
              frist=30, was="der Lauf kam nie in den Zustand running")

    seite.goto(client.url + "/-/jobs/" + _uid("still"))
    ziel = warte_bis(lambda: seite.evaluate(
        "() => { const e = document.querySelector('[data-bus^=\"live:\"]');"
        " return e && e.getAttribute('data-bus-refetch'); }"),
        frist=20, was=f"die Live-Region fehlt\n{client.ausgabe()}")

    # Erst zur Ruhe kommen lassen: der Seitenaufbau selbst und der Resync des
    # ersten Verbindungsaufbaus gehören nicht in die Messung.
    time.sleep(3.0)
    vorher = len(anfragen(seite, ziel))
    time.sleep(_FENSTER_S)
    nachher = len(anfragen(seite, ziel))

    assert nachher - vorher <= _ERLAUBT, (
        f"{nachher - vorher} Refetches in {_FENSTER_S:.0f}s an einem stehenden "
        "System — das Abonnement baut sich neu auf, statt stillzuhalten, und "
        "jeder Aufbau schickt den Resync des Schedulers hinterher (#77)")


def test_the_subscription_stays_up_instead_of_reconnecting(fabrik):
    """Dieselbe Zusage eine Ebene tiefer, ohne Browser — und deshalb hier.

    Die Messung oben sieht die **Folge** (zu viele Refetches). Diese hier sieht
    die **Ursache**: dass das Abonnement steht. Beide zusammen sagen etwas, das
    keine allein sagt — wäre nur die obere grün, könnte auch schlicht der
    Resync ausgefallen sein, und das wäre kein Erfolg, sondern der nächste
    Fehler.
    """
    host, client = paar(fabrik)
    warte_bis(lambda: client.get_json("/-/status").get("node", {}) is not None,
              frist=10, was="kein Status")
    # Der Host führt Buch über die Verbindungen, die er annimmt: jede taucht als
    # `GET /-/events` in seiner Ausgabe auf. Ein stehendes Abonnement erzeugt
    # genau eine; das Produktionsbild waren hundert in zwei Minuten.
    vorher = host.ausgabe().count("GET /-/events")
    time.sleep(_FENSTER_S)
    nachher = host.ausgabe().count("GET /-/events")

    assert nachher - vorher <= _ERLAUBT, (
        f"{nachher - vorher} Verbindungsaufbauten in {_FENSTER_S:.0f}s — das "
        "Abonnement hält nicht, es wird ständig neu aufgebaut (#77)")


def _uid(slug: str) -> str:
    from bibi.schedule.models import job_uid
    return job_uid(slug)
