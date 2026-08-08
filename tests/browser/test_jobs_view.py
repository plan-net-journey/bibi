"""Szenario 1: die Ansichtswahl überlebt Navigation und Nachladen (`#83`, `#84`).

Der Fall aus der Abnahme von `v0.7.7`, m.rau am 2026-08-08: *„Filter Settings,
Sortierungen gehen verloren."* Präzisiert auf Nachfrage: *„er muss auch greifen,
wenn ich über Tabs navigiere. Wenn ich zurück komme, erwarte ich gleiche
Filter."*

`81ea6dd` hat dazu die serverseitige Hälfte geschlossen — `/-/jobs/list` merkt
sich die Ansicht jetzt auch. Was dabei offenblieb, sieht man nur im Browser und
steht in ``test_the_filter_handles_survive_a_bus_refetch``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from .browserlib import anfragen, job_md, warte_bis  # noqa: E402

pytestmark = pytest.mark.browser


#: Ein **Client**: Synchronizer + Controller, wie der Mac, an dem die Befunde
#: entstanden sind. Die Rolle ist hier keine Beiläufigkeit — `/-/run` ist
#: client-only (ein Scheduler-Knoten antwortet `409`), und ein lokaler Lauf ist
#: der billigste echte Zustandswechsel, den ein Test auslösen kann.
_CLIENT = "--synchronizer --controller"


def _knoten_mit_zwei_jobs(fabrik):
    """Ein Knoten mit je einem Job je TYPE — sonst filtert der Klick nichts weg
    und der Test bestünde auch bei kaputtem Filter."""
    root = fabrik.repo("knoten")
    job_md(root, "ein-shell-job", payload="job: echo hallo")
    # `job: "claude: …"` — der claude-Typ ist ein Prefix **im Payload**, kein
    # eigener Frontmatter-Schlüssel (§5.3). Die Anführungszeichen sind nötig:
    # ohne sie ist die Zeile kein gültiges YAML, die MD fällt still aus dem
    # Scan, und der Filter-Test prüfte gegen eine Liste mit einer Zeile.
    job_md(root, "ein-claude-job", payload='job: "claude: sag hallo"')
    return fabrik.starte(root, rollen=_CLIENT)


def _sichtbare_slugs(seite) -> set[str]:
    """Die Slugs der gezeigten Zeilen. Der Slug steht im ``title`` des Links —
    die Zelle selbst zeigt ihn gekürzt."""
    return set(seite.eval_on_selector_all(
        "table.jobs td.slug a", "els => els.map(e => e.getAttribute('title'))"))


def test_the_jobs_screen_comes_up_at_all(fabrik, seite):
    """Der Rauchtest der Ebene: echter Daemon, echter Browser, echte Seite.

    Steht er nicht, sagt jeder folgende Fehlschlag nichts über sein Szenario —
    deshalb zuerst und deshalb ohne jede Zusatzbedingung."""
    k = _knoten_mit_zwei_jobs(fabrik)
    seite.goto(k.url + "/-/jobs")
    assert _sichtbare_slugs(seite) == {"ein-shell-job", "ein-claude-job"}, \
        f"der Jobs-Screen zeigt nicht beide Jobs\n{k.ausgabe()}"


def test_a_filter_survives_leaving_and_returning(fabrik, seite):
    """**Das Szenario aus `#84`, Nummer 1.** Filter setzen, wegnavigieren,
    zurückkommen — die Wahl steht noch.

    Der Weg ist der des Nutzers und nicht der der Route: ein Klick auf den
    Knopf, ein Wechsel auf einen anderen Screen, ein Klick zurück. Was dabei
    serverseitig Cookie heißt und welche der beiden Routen ihn schreibt, weiß
    dieser Test nicht — und genau deshalb bemerkt er auch eine Verschiebung
    zwischen ihnen."""
    k = _knoten_mit_zwei_jobs(fabrik)
    seite.goto(k.url + "/-/jobs")

    seite.click('.fltr[data-filter="claude"]')
    seite.wait_for_url("**typ=claude*")
    assert _sichtbare_slugs(seite) == {"ein-claude-job"}, "der Filter greift nicht"

    seite.goto(k.url + "/-/feed")
    seite.goto(k.url + "/-/jobs")          # ohne Query — wie der Tab-Klick

    assert _sichtbare_slugs(seite) == {"ein-claude-job"}, (
        "die Filterwahl war nach dem Seitenwechsel weg")


def test_a_deep_link_still_beats_the_remembered_view(fabrik, seite):
    """Die Gegenrichtung, und die Bedingung, unter der das Merken harmlos ist.

    Eine geteilte URL muss stärker sein als die eigene Erinnerung — sonst legt
    der Empfänger seine Sicht über die geteilte und bekommt etwas anderes zu
    sehen als der Absender. `f=1` heißt dabei „diese Query ist die Antwort, auch
    wo sie schweigt": ohne das Zeichen brächte der Cookie den eben abgewählten
    Filter zurück, und der Knopf wäre tot."""
    k = _knoten_mit_zwei_jobs(fabrik)
    seite.goto(k.url + "/-/jobs")
    seite.click('.fltr[data-filter="claude"]')
    seite.wait_for_url("**typ=claude*")

    seite.goto(k.url + "/-/jobs?f=1")      # alles abgewählt, ausdrücklich

    assert _sichtbare_slugs(seite) == {"ein-shell-job", "ein-claude-job"}, \
        "der abgewählte Filter kam über die Erinnerung zurück"


def test_the_filter_handles_survive_a_bus_refetch(fabrik, seite):
    """**Was `#83` offengelassen hat, und was nur ein Browser sieht.**

    `_JOBS_JS` hängt seine Klick-Handler direkt an die Knöpfe
    (``document.querySelectorAll('.fltr[data-filter]')``) — und die Knöpfe
    stehen **in** ``#jobs``, der Region, die der Bus bei jedem Zustandswechsel
    per ``outerHTML``-Swap ersetzt. Nach dem ersten Refetch sind es andere
    Elemente, und an ihnen hängt nichts mehr.

    Serverseitig ist daran nichts zu sehen: die Route liefert dieselben Knöpfe
    wie vorher, und jeder Zeichenketten-Test bleibt grün. Im Browser ist der
    Knopf tot, bis jemand neu lädt — was m.raus Befund *„Filter Settings gehen
    verloren"* für den Nutzer ununterscheidbar von einem verlorenen Cookie
    macht.

    Der Refetch wird von einem echten Lauf ausgelöst, nicht nachgestellt: ein
    Job, der startet und endet, erzeugt genau die Zustandswechsel, die im
    Betrieb dahinterstehen."""
    k = _knoten_mit_zwei_jobs(fabrik)
    job_md(k.root, "kurz", payload="job: echo fertig")
    k.post("/-/rescan")
    seite.goto(k.url + "/-/jobs")

    vorher = len(anfragen(seite, "/-/jobs/list"))
    code, body = k.post_json("/-/run", {"slug": "kurz"})
    assert code == 200, f"der Lauf startete nicht: {code} {body}"
    warte_bis(lambda: len(anfragen(seite, "/-/jobs/list")) > vorher,
              frist=30, was="die Jobs-Liste wurde nie per Bus nachgeladen")

    seite.click('.fltr[data-filter="claude"]')
    # Bewusst nicht `wait_for_url`: dessen Zeitüberschreitung meldet „Timeout
    # 5000ms exceeded" und verschweigt, worum es ging. Ein toter Knopf ist ein
    # Befund und soll wie einer klingen.
    warte_bis(lambda: "typ=claude" in seite.url, frist=5, takt=0.1,
              was="nach einem Bus-Refetch ist der Filter-Knopf tot — der Klick "
                  "löst nichts aus, weil die Handler an Elementen hängen, die "
                  "der outerHTML-Swap ersetzt hat")
    assert _sichtbare_slugs(seite) == {"ein-claude-job"}, \
        "der Filter navigierte, griff aber nicht"


def test_the_sort_handles_survive_a_bus_refetch(fabrik, seite):
    """Dieselbe Zusage für die Spaltenköpfe — die zweite Hälfte von m.raus
    Meldung.

    *„Filter Settings, Sortierungen gehen verloren"* nannte beides, und beides
    hing an derselben Bindung: `th[data-sort]` steht ebenso in `#jobs` wie die
    Knöpfe. Ein eigener Test, weil der Klick hier ein Kind treffen kann — der
    Pfeil im sortierten Kopf ist Text im `th`, und ein Handler ohne `closest`
    ginge daran vorbei."""
    k = _knoten_mit_zwei_jobs(fabrik)
    job_md(k.root, "kurz", payload="job: echo fertig")
    seite.goto(k.url + "/-/jobs")

    vorher = len(anfragen(seite, "/-/jobs/list"))
    assert k.post_json("/-/run", {"slug": "kurz"})[0] == 200
    warte_bis(lambda: len(anfragen(seite, "/-/jobs/list")) > vorher,
              frist=30, was="die Jobs-Liste wurde nie per Bus nachgeladen")

    seite.click('th[data-sort="slug"]')
    warte_bis(lambda: "sort=slug" in seite.url, frist=5, takt=0.1,
              was="nach einem Bus-Refetch ist der Spaltenkopf tot")
