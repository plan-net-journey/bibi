"""Dauern ticken im Browser — Thema A der FE-Ereignisarchitektur (#122).

**Der Befund, der das ausgelöst hat** (m.rau, 2026-08-10): *„Wenn ich den
Runner starte, bekomme ich genau 2 runtime Aktualisierungen: eine am Anfang
und eine am Ende."* Die Zahl zwei ist exakt erklärbar — der Bus-Fingerabdruck
führt Status und Termin, sonst nichts. Eine wachsende Laufzeit ändert keins von
beidem, also gibt es kein Ereignis; die zwei Aktualisierungen sind die zwei
Zustandswechsel.

**Die Reparatur geht in die entgegengesetzte Richtung von „mehr Ereignisse".**
Eine Dauer ist keine Nachricht vom Server, sondern eine reine Funktion aus
einem Anker und *jetzt*. Der Browser kann sie selbst zählen — genau das tut die
Kopfzeilen-Uhr seit jeher. Der Server liefert den Anker, der Browser tickt.

**Der Preis dieser Bauform ist eine Doppelimplementierung**, und der teuerste
Test hier bewacht genau sie: die Formatierungsregeln stehen ab jetzt zweimal,
in ``render.py`` und im Ticker. Laufen sie auseinander, springt der Text beim
ersten Tick sichtbar um — und beide Seiten sind für sich grün.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bibi.controller import render

_HARNESS = Path(__file__).parent / "assets" / "duration_js_harness.js"

#: Grenzwerte, nicht Zufallszahlen — je einer unter, auf und über jeder Stufe
#: der drei Regeln. Die 9,95 ist Absicht: Pythons `:.1f` rundet auf `10.0s`,
#: und ob JS dasselbe tut, ist genau die Sorte Abweichung, die im Betrieb als
#: Zucken auffällt und im Code niemandem.
_SEKUNDEN = [0, 0.4, 1, 5.5, 9.4, 9.95, 10, 59, 60, 61, 119, 3599, 3600,
             3661, 86399, 86400, 90061]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")
def test_the_browser_formats_durations_exactly_like_the_renderer():
    aufgabe = {"since": _SEKUNDEN, "ago": _SEKUNDEN, "until": _SEKUNDEN}
    proc = subprocess.run(
        ["node", str(_HARNESS), json.dumps(aufgabe)],
        input=render._DURATION_JS, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    js = json.loads(proc.stdout)
    assert "fehler" not in js, js["fehler"]

    # Python-Seite: dieselben Werte durch dieselben drei Regeln. `now` ist
    # fest, damit der Vergleich nicht selbst von der Uhr abhängt.
    # Verglichen werden die **Regeln**, nicht die Hüllen — `_ago()` & Co.
    # liefern jetzt ein `<span>`, und ein Vergleich gegen dessen Markup würde
    # die Frage verfehlen, um die es geht.
    erwartet = {
        "since": [render._dauer_text(s) for s in _SEKUNDEN],
        "ago": [render._ago_text(max(0, int(s))) for s in _SEKUNDEN],
        "until": [render._until_text(int(s)) for s in _SEKUNDEN],
    }
    for art in erwartet:
        for wert, py, browser in zip(_SEKUNDEN, erwartet[art], js[art]):
            assert py == browser, (
                f"{art} bei {wert}s: Renderer sagt {py!r}, Browser sagt {browser!r}")


# ── Der Anker muss im Markup stehen, sonst kann niemand zählen ──────────────


def test_a_running_runtime_carries_its_anchor():
    # Ein laufender Lauf: `finished_at` fehlt. Die Zelle muss den Startpunkt
    # tragen, nicht nur den ausgerechneten Text.
    html = render._duration_cell({"started_at": 1000.0, "finished_at": None,
                                  "exec_runtime": 42.0})
    assert 'data-dur="since"' in html
    assert 'data-at="1000.0"' in html


def test_a_finished_runtime_does_not_tick():
    # **Die Gegenprobe, und sie ist die wichtigere.** Eine abgeschlossene
    # Laufzeit ist ein Ergebnis, keine Uhr — sie darf nicht weiterzählen.
    # Ohne diesen Test wäre „alles tickt" grün und im Betrieb absurd.
    html = render._duration_cell({"started_at": 1000.0, "finished_at": 1042.0,
                                  "exec_runtime": 42.0})
    assert "data-dur" not in html
    assert "42s" in html


def test_a_p90_never_ticks():
    # `runtime_p90` ist eine Kennzahl über viele Läufe, kein Zeitraum, der
    # gerade vergeht.
    assert "data-dur" not in render._human_duration(31.0)


def test_the_heartbeat_age_carries_its_anchor():
    """`_ago()` hat immer einen Anker — **seit #184 den des Zeitpunkts.**

    Hier stand `data-dur="ago"`, und die Begruendung war: *„es ist eine
    Distanz zu jetzt"*. Beide Anker ticken; nur `data-tp` folgt dem
    abs./rel.-Umschalter. Solange `_ago()` den Dauer-Anker trug, stand
    `Last heartbeat` fest auf relativ, waehrend `Connected since` zwei Spalten
    weiter fest auf absolut stand.

    **Die alte Zusage ist nicht gebrochen, sondern abgeloest** (m.rau,
    2026-08-13): *„Ebenso die Spalte Heartbeat. Diese ist immer relativ. Beide
    sollen ebenfalls umgeschaltet werden."* Was bleibt, ist der Anker; was
    faellt, ist die Festlegung auf eine der beiden Darstellungen.
    """
    html = render._ago(1000.0, 1042.0)
    assert 'data-tp="1000.0"' in html, html
    assert 'data-abs="' in html, html


def test_the_next_run_countdown_carries_its_anchor():
    html = render._until(2000.0, 1000.0)
    assert 'data-dur="until"' in html and 'data-at="2000.0"' in html


def test_a_missing_timestamp_stays_a_dash_without_an_anchor():
    # Kein Wert heißt kein Anker — sonst zählte der Browser von 1970 hoch.
    assert render._ago(None, 1000.0) == "—"
    assert render._until(None, 1000.0) == "—"
    assert render._human_duration(None) == "—"


# ── Die Screens, die es sichtbar macht ─────────────────────────────────────


def test_the_nodes_table_heartbeat_column_ticks():
    html = render._clients_table(
        [{"worker": "sarasate", "host": "sarasate", "port": 8781,
          "role": "controller", "stale": False, "connected_at": 0,
          "last_heartbeat": 990}], now=1000)
    # Seit #184 der Zeitpunkt-Anker: er tickt genauso und folgt zusaetzlich
    # dem Umschalter (s. `test_the_heartbeat_age_carries_its_anchor`).
    assert 'data-tp="990"' in html, html


def test_the_ticker_is_shipped_with_the_page():
    # Ein Ticker, den keine Seite lädt, ist gebauter ungenutzter Code — das
    # Muster aus Runde 2, fünf von sechs Posten.
    seite = render.clients_page([], now=0)
    assert "__bibiDauer" in seite
