"""Szenario 4: die Aktionsleiste wirkt — KILL, RESET, START (`#108`, `#84`).

**Der Prüfpunkt steht seit dem 2026-08-09 in `Iterationen.md` und ist zweimal
ausgeblieben:** *„Kill / Restart / Start — die Aktionsleiste wirkt. **Kein
heutiger Test klickt sie je an.**"* Nach zwei Akzeptanz-Durchgängen von Hand
war klar, warum: **von Hand ist es nicht zuverlässig prüfbar.**

Drei Versuche an einem Lauf von rund zwanzig Sekunden endeten alle drei
`complete`. Der Knopf war nachweislich aktiv und exakt getroffen — und das
Klick-Werkzeug hatte in derselben Sitzung mehrfach danebengetroffen: ein
referenzbasierter Klick wirkte nicht, zwei Koordinaten-Klicks trafen den Rand
statt die Mitte, ein JS-`click()` löste eine Navigation aus. **Werkzeugfehler
und Systemfehler waren nicht zu trennen**, und ein gemeldeter Fehler, der der
eigene ist, kostet mehr als er nützt.

Genau dafür gibt es diese Ebene. Hier ist ein Klick ein Klick, und derselbe Weg
hat bei `#105` funktioniert: von Hand nicht sauber reproduzierbar, im
Browser-Test beim ersten Lauf rot.

**Die drei Verben stehen in einem Test, weil sie eine Leiste sind.** Ihre
Zustände hängen aneinander — KILL ist nur aktiv, während etwas läuft, START nur,
wenn nichts läuft, und RESET holt einen verbrauchten Slot zurück. Drei getrennte
Tests müssten jeder für sich denselben Lauf aufbauen und prüften dabei die
Übergänge nicht, um die es geht.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api",
                    reason="pytest-playwright fehlt — `uv sync --group browser`")

from .browserlib import paar, warte_bis  # noqa: E402

pytestmark = pytest.mark.browser

#: Lang genug, dass ein KILL messbar früher kommt als das reguläre Ende.
#:
#: Das ist die Kernaussage des Tests und nicht Bequemlichkeit: ein Lauf, der
#: von selbst endet, während man auf ihn klickt, kann seinen Tod nicht von
#: seinem Ende unterscheiden. Die drei Versuche von Hand scheiterten genau
#: daran — zwanzig Sekunden, und jedes Mal stand am Ende `complete`, was
#: sowohl „der Klick wirkte nicht" als auch „der Klick kam zu spät" heißen
#: kann. Sechzig Sekunden lassen die beiden Fälle auseinandertreten.
_LANGER_LAUF = "job: sleep 60"


def _slot_zeile(host, slug: str) -> dict | None:
    for j in host.get_json("/-/job"):
        if j.get("slug") == slug:
            return j
    return None


def _knopf(seite, verb: str, ziel: str = "scheduler"):
    """Der Knopf eines Verbs **an einem benannten Slot** — oder `None`, wenn er
    dort ausgegraut ist.

    Ein nicht verfügbares Verb bleibt sichtbar und wird zu `<span class=
    "slot-off">` (FE-Spezifikation §5.2): sonst spränge das Layout, und die
    Auskunft „das geht hier gerade nicht" ginge verloren. Für den Test heißt
    das: die Abwesenheit des `<button>` ist selbst eine Aussage, kein Fehlen.

    **`ziel` ist nicht optional gemeint, und das ist ein Messfehler aus dem
    ersten Bau dieser Datei.** Die Seite zeigt zwei Slots, CLIENT vor
    SCHEDULER. Ohne die Angabe griff `query_selector` den ersten im DOM — beim
    KILL zufällig den richtigen (der Client-Slot hat keinen Lauf und deshalb
    keinen KILL-Knopf), beim START den falschen. Der Test startete daraufhin
    einen Lauf auf dem Client und sah beim Scheduler nichts passieren; er las
    wie ein Systembefund und war einer über mich.
    """
    # **Während des Neuladens ist die Seite kein gültiger Kontext.** Jeder
    # Verb-Klick endet mit `window.location.reload()` (`_JOBS_JS`) — wer in
    # genau dieses Fenster hinein abfragt, bekommt „Execution context was
    # destroyed" und einen Fehlschlag, der nichts über das System sagt. Ein
    # Wartelauf muss das als „noch nicht" lesen, nicht als Fehler; sonst hängt
    # das Ergebnis am Zufall des Zeitpunkts.
    from playwright.sync_api import Error as PlaywrightError
    try:
        return seite.query_selector(
            f'button.slot-do[data-verb="{verb}"][data-ziel="{ziel}"]')
    except PlaywrightError:
        return None


def _klick(seite, verb: str, ziel: str = "scheduler") -> None:
    """Den Knopf **im Moment des Klicks** holen und drücken.

    Eine Referenz, die vor einem Wartelauf entstand, kann inzwischen zu einem
    abgeräumten Dokument gehören — jeder Verb-Klick lädt die Seite neu. Ein
    `click()` darauf wirft, und der Test läse einen Zeitpunkt statt eines
    Verhaltens.
    """
    knopf = warte_bis(lambda: _knopf(seite, verb, ziel), frist=20,
                      was=f"{verb.upper()} war nicht klickbar")
    knopf.click()


def _lauf_mit_offener_detailseite(fabrik, seite):
    """Ein laufender Job beim Scheduler, die Detailseite beim Client geöffnet.

    Dieselbe Topologie wie in Szenario 2 und aus demselben Grund: die Slots
    einer Detailseite tragen ihre Knöpfe erst, wenn es eine Job-Zeile gibt, und
    die legt der Scheduler an. Der Klick geht dann über den Controller des
    Clients an den Host — genau der Umweg, den `screen_job_verb()` beschreibt
    und den ein Ein-Knoten-Aufbau gar nicht durchliefe.
    """
    from bibi.schedule.models import job_uid

    host, client = paar(fabrik, job="lang", payload=_LANGER_LAUF)
    zeile = warte_bis(lambda: _slot_zeile(host, "lang"),
                      frist=20, was="der Job tauchte beim Scheduler nicht auf")
    host.post(f"/-/job/{zeile['id']}/start")
    warte_bis(lambda: (_slot_zeile(host, "lang") or {}).get("status") == "running",
              frist=30, was="der Lauf kam nie in den Zustand running")

    seite.goto(f"{client.url}/-/jobs/{job_uid('lang')}")
    warte_bis(lambda: _knopf(seite, "kill"),
              frist=20, was="KILL wurde nie zum Knopf")
    return host, client


def test_kill_actually_kills(fabrik, seite):
    """**Der Rot-Schritt von `#108`, und die Frage, die zwei Durchgänge offen
    ließen.**

    Nicht: der Knopf entsteht. Das war schon belegt — `data-verb="kill"`,
    `disabled: false`, die Laufzeit tickte, und der Wechsel kam über den Bus.
    Sondern: **er tötet.**

    Der Unterschied ist der ganze Punkt der Ebene. Ein Test auf das Markup
    wäre auf jedem Stand grün gewesen, auf dem der Knopf gerendert wird — auch
    auf einem, an dem der Klick ins Leere geht.
    """
    host, _client = _lauf_mit_offener_detailseite(fabrik, seite)

    _klick(seite, "kill")

    zeile = warte_bis(
        lambda: (_slot_zeile(host, "lang") or {}).get("status") == "killed"
        and _slot_zeile(host, "lang"),
        frist=25, was="der Lauf erreichte nach dem Klick nie killed (#108)")
    assert zeile["reason"] == "by_user", (
        f"getötet, aber nicht durch den Knopf: reason={zeile['reason']!r}")


def test_the_bar_follows_the_state_it_just_caused(fabrik, seite):
    """Die Leiste als Leiste: was ein Klick auslöst, muss sie danach zeigen.

    Nach dem KILL ist der Slot terminal — KILL wird grau, START und RESET
    treten an seine Stelle. Ohne den Bus stünde hier weiter der alte Zustand,
    und der Klickende sähe die Wirkung seines eigenen Klicks nicht.

    **Ohne Neuladen geprüft.** Ein `reload()` würde denselben Endzustand
    zeigen und dabei genau das verschweigen, was hier interessiert.
    """
    host, _client = _lauf_mit_offener_detailseite(fabrik, seite)

    _klick(seite, "kill")
    warte_bis(lambda: (_slot_zeile(host, "lang") or {}).get("status") == "killed",
              frist=25, was="der Lauf erreichte nie killed")

    warte_bis(lambda: _knopf(seite, "kill") is None,
              frist=20, was="KILL blieb aktiv, obwohl nichts mehr läuft (#108)")
    assert _knopf(seite, "start") is not None, (
        "START kam nach dem Ende nicht zurück — die Leiste steht auf einem "
        "Zustand, den es nicht mehr gibt")


def test_start_starts_it_again(fabrik, seite):
    """Die dritte Taste, und die Gegenprobe zu beiden oberen.

    Sie beantwortet zugleich den Einwand gegen den KILL-Test: dass der Lauf
    nach dem Klick terminal ist, könnte auch heißen, dass der Klick den Slot
    **kaputtmacht** statt ihn zu beenden. Ein Slot, der danach wieder startet,
    schließt das aus.
    """
    host, _client = _lauf_mit_offener_detailseite(fabrik, seite)

    _klick(seite, "kill")
    warte_bis(lambda: (_slot_zeile(host, "lang") or {}).get("status") == "killed",
              frist=25, was="der Lauf erreichte nie killed")
    warte_bis(lambda: _knopf(seite, "start"),
              frist=20, was="START wurde nie wieder zum Knopf")

    _klick(seite, "start")

    warte_bis(lambda: (_slot_zeile(host, "lang") or {}).get("status")
              in ("starting", "running"),
              frist=30, was="START brachte den Slot nicht zurück ins Laufen (#108)")


def test_reset_takes_the_slot_back_to_waiting(fabrik, seite):
    """Die dritte Taste, und die einzige mit einem eigenen Nebeneffekt.

    START und RESET führen beide aus einem terminalen Zustand heraus und
    unterscheiden sich allein im nächsten Zeitpunkt (Zustandsmodell): START
    setzt ihn auf jetzt, RESET auf den regulären Termin. Der Slot geht dabei
    nach `pending` — und **RESET wischt zusätzlich die job-eigenen Daten**,
    was START nie tut.

    Geprüft wird der Übergang, nicht das Wischen: was auf der Platte passiert,
    gehört auf die Engine-Ebene. Was hier zu belegen war, ist, dass der Klick
    ankommt — die halbe Aussage, die zwei Durchgänge von Hand offen ließen.
    """
    host, _client = _lauf_mit_offener_detailseite(fabrik, seite)

    _klick(seite, "kill")
    warte_bis(lambda: (_slot_zeile(host, "lang") or {}).get("status") == "killed",
              frist=25, was="der Lauf erreichte nie killed")
    warte_bis(lambda: _knopf(seite, "reset"),
              frist=20, was="RESET wurde nie zum Knopf")

    _klick(seite, "reset")

    warte_bis(lambda: (_slot_zeile(host, "lang") or {}).get("status") == "pending",
              frist=25, was="RESET brachte den Slot nicht nach pending (#108)")
