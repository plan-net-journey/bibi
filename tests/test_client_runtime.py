"""Die Client-Hälfte zeigt einen laufenden Lauf, mittickend (`#136`).

**Die Zusage steht seit dem Schreiben des Tickets:** *„Jobs, Client-Block —
`Last` zeigt bei `running` ebenfalls die aktuelle Laufzeit, mittickend."*
Gebaut war nur die Scheduler-Hälfte.

**Und die Client-Seite hat deren Aufteilung mitgenommen, wo sie nicht
hingehört.** Beim Scheduler steht die Laufzeit in `NEXT` — aus einem guten
Grund: *„solange ein Lauf läuft, ist die Laufzeit die Zukunft"*, und `LAST`
trägt dort die Startzeit. Der Client hat gar kein `NEXT`; er hat kein
`next_fire_at` und wird von nichts terminiert. Die Laufzeit gehört dort in die
eine Zeitspalte, die es gibt.

## Warum das erst jetzt gebaut wird

`#136` stand ausdrücklich auf `#129` — *„sollte danach gebaut werden, nicht
davor"* —, weil ``upsert_schedule()`` die `jobs`-Zeile bei jedem Rescan
überschrieb: **eine Anzeige, die den laufenden Lauf behauptet, aber aus einer
Quelle liest, die sich unter ihr ändern darf, ist nicht bloß gelegentlich
falsch, sondern grundsätzlich nicht zusagefähig.** `#129` ist geschlossen, der
Blocker ist gefallen.
"""

from __future__ import annotations

from bibi.controller import render
from bibi.controller.jobs_view import JobRow, Segment

NOW = 1_000_000.0


def _zeile(*, lokal: dict, sched: dict | None = None) -> str:
    return render._jobs_zeile(
        JobRow(slug="x", segment=Segment.SCHEDULE,
               scheduler=sched if sched is not None else {"row_status": "complete",
                                                          "last_run_at": NOW - 900},
               local=lokal, spec={"payload": "echo hi"}), NOW)


def _client_zellen(html: str) -> list[str]:
    """Die beiden letzten Zellen der Zeile — der Client-Block."""
    zellen = html.split("<td")[1:]
    return ["<td" + z for z in zellen][-2:]


def test_a_local_run_shows_its_elapsed_time_in_last():
    """**Der Rot-Schritt**: die `LAST`-Zelle zeigt heute den Zeitpunkt, zu dem
    der *vorige* Lauf fertig war — auch während gerade einer läuft."""
    html = _zeile(lokal={"status": "running", "started_at": NOW - 42,
                         "finished_at": NOW - 3600})
    letzte = _client_zellen(html)[-1]
    assert 'data-dur="since"' in letzte, f"keine tickende Laufzeit: {letzte!r}"
    assert f'data-at="{NOW - 42}"' in letzte, letzte


def test_the_ticking_cell_carries_the_anchor_not_the_number():
    """Der Server liefert den Anker, der Browser zählt (``_DURATION_JS``).

    Das ist dieselbe Bauart wie beim Scheduler und der Grund, warum eine
    Laufzeit **ohne ein einziges Server-Ereignis** weiterläuft — eine wachsende
    Dauer ändert weder Status noch Termin, es gäbe also nichts zu melden.
    """
    html = _zeile(lokal={"status": "running", "started_at": NOW - 42})
    letzte = _client_zellen(html)[-1]
    assert "data-at=" in letzte and 'class="dur"' in letzte, letzte


def test_a_finished_local_run_still_shows_when_it_ended():
    """Ohne laufenden Lauf sagt die Zelle, was sie immer gesagt hat.

    *„Der Scheduler nennt daneben den Start seines Laufs, und das ist kein
    Versehen: er weiß, wann etwas beginnt, der Client sieht, wann es geendet
    hat."*
    """
    html = _zeile(lokal={"status": "complete", "finished_at": NOW - 3600})
    letzte = _client_zellen(html)[-1]
    assert 'data-dur="since"' not in letzte, letzte
    assert "data-tp=" in letzte, letzte


def test_the_scheduler_block_keeps_its_own_split():
    """**Die Gegenprobe, und sie gehört dazu.**

    Ohne sie wäre ein Fix grün, der beide Blöcke gleichmacht und damit die
    andere Hälfte zerstört. Beim Scheduler bleibt es bei `LAST` = Startzeit und
    `NEXT` = Laufzeit; dort gibt es eine zweite Spalte, die den Termin trägt,
    und deshalb trägt die erste den Start.
    """
    html = _zeile(sched={"row_status": "running", "started_at": NOW - 42},
                  lokal={"status": "complete", "finished_at": NOW - 3600})
    # Vor dem Client-Block: die Scheduler-Zellen.
    kopf = html[:html.index(_client_zellen(html)[0])]
    assert 'data-dur="since"' in kopf, "die Laufzeit fehlt dem Scheduler-Block"
    assert "data-tp=" in kopf, "die Startzeit fehlt dem Scheduler-Block"


def test_a_scheduler_run_does_not_leak_into_the_client_column():
    """Die zweite Gegenprobe: **der Client-Block sagt nur, was hier ankam.**

    Läuft der Job beim Scheduler und hier nicht, darf die Client-Spalte keine
    Laufzeit zeigen — sie behauptete sonst einen lokalen Lauf, den es nicht
    gibt. Das ist die Umkehrung von `#146`, wo der Einspringer *fehlte*: hier
    darf er nicht erfunden werden.
    """
    html = _zeile(sched={"row_status": "running", "started_at": NOW - 42},
                  lokal={"status": "complete", "finished_at": NOW - 3600})
    letzte = _client_zellen(html)[-1]
    assert 'data-dur="since"' not in letzte, letzte
