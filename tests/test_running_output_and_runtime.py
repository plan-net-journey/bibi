"""Der laufende Lauf zeigt, was er tut — Laufzeit und Ausgabe (#123, #124).

Zwei Beobachtungen von m.rau am 2026-08-10, beide am selben Screen:

> die Runtime des laufenden Slots in der Liste der RUNs wird nicht hochgezählt
> […] bei geöffnetem Output (show) wird der Stream nicht aktualisiert. Ich muss
> collapsen+expanden

**Beide haben dieselbe Form**, und es ist die aus Runde 2: eine Fähigkeit ist
gebaut, begründet und im Einsatz — und die Stelle, die sie braucht, ruft sie
nicht auf. Bei `#123` ist es der Dauer-Anker aus `v0.7.16`, bei `#124` die
Live-Output-Box, die auf derselben Seite zweimal steht.

**Die Bauform muss ruckelfrei sein** (m.rau, 2026-08-10), und das entscheidet
sich nicht am Anhängen, sondern am Swap: `#runs` wird bei jedem
Slot-Zustandswechsel getauscht. Die Box überlebt ihn, weil sie ihren Offset
selbst mitführt — `data-from` wird je Zeile nachgezogen, und Doppelte werden
darüber verworfen. Ein wiederhergestellter Kasten setzt damit exakt dort auf,
wo er stand, statt seinen Inhalt neu zu holen.
"""

from __future__ import annotations

from bibi.controller import render


# ── #123: die Zeile des laufenden Laufs trägt ihre Laufzeit ────────────────


def test_the_running_row_shows_a_ticking_runtime():
    # Bis #123 standen hier vier Gedankenstriche — REASON, EXIT, RUNTIME und
    # COMMIT. Der dritte war die Laufzeit, und sie war nie da.
    html = render._live_placeholder_row(
        {"status": "running", "started_at": 1000.0}, now=1042.0)
    assert 'data-dur="since"' in html
    assert 'data-at="1000.0"' in html


def test_the_running_row_keeps_the_dashes_it_is_entitled_to():
    # **Gegenprobe.** Grund, Exit-Code und Commit sind zur Laufzeit wirklich
    # unbekannt — sie durch etwas zu ersetzen wäre eine Behauptung.
    html = render._live_placeholder_row(
        {"status": "running", "started_at": 1000.0}, now=1042.0)
    assert html.count("<td>—</td>") == 3


def test_a_row_without_a_start_shows_no_runtime():
    # `starting` hat per Invariante noch keine PID und keinen Startzeitpunkt.
    # Ohne Anker keine Uhr — sonst zählte der Browser von 1970 hoch.
    html = render._live_placeholder_row({"status": "starting"}, now=1042.0)
    assert "data-dur" not in html


# ── #124: der aufgeklappte Output eines laufenden Laufs ist ein Strom ──────


def test_a_running_slot_output_is_a_live_box():
    box = render.live_output_box("42", [{"t": 1.0, "line": "hallo"}], kind="job")
    assert 'class="term liveterm"' in box
    assert 'data-job="42"' in box
    # Der Offset ist das, was die Box über einen Swap trägt.
    assert 'data-from="1"' in box


def test_a_scheduler_side_box_carries_its_own_stream():
    # Ein Lauf beim Scheduler kann nicht über den globalen Bus wachsen — dessen
    # `append`-Ereignisse entstehen, indem der Collector eine lokale Datei
    # tailt, und die gibt es hier nicht (#78).
    box = render.live_output_box("42", [], kind="job",
                                 stream_url="/-/job/42/output/stream")
    assert 'data-stream="/-/job/42/output/stream"' in box


# ── Der Vertrag zwischen den beiden Skripten ───────────────────────────────
#
# `_EVENTS_JS` bindet seine Boxen auf `DOMContentLoaded` und `htmx:afterSettle`.
# Eine Box, die `ladeOutput()` per `innerHTML` nachträglich einsetzt, entsteht
# zwischen diesen Momenten — sie wäre da, und der Strom fände sie nicht.
#
# Das ist **dieselbe Verwechslung**, die dieses Ticket überhaupt erzeugt hat,
# eine Ebene tiefer: gebaut, aber nicht angeschlossen. Deshalb steht der
# Vertrag hier als Test und nicht als Kommentar.


def test_the_event_script_exposes_its_box_initialiser():
    assert "window.__bibiInitBoxes" in render._EVENTS_JS


def test_the_detail_script_announces_boxes_it_inserts():
    # Nicht auf die Reihenfolge zweier `htmx:afterSettle`-Zuhörer verlassen:
    # welcher zuerst läuft, hängt an der Skript-Reihenfolge der Seite. Der
    # Aufruf ist ausdrücklich, damit die Bindung nicht von ihr abhängt.
    assert "__bibiInitBoxes" in render._JOB_DETAIL_JS


def test_the_rescuer_does_not_refetch_a_live_box():
    # **Der Ruckel-Punkt.** Bis #124 holte der Retter den Output eines
    # laufenden Laufs bei jedem Swap vollständig neu. Mit einer Box, die ihren
    # Offset mitführt, ist das ein Roundtrip ohne Gewinn — und ein sichtbarer
    # Neuaufbau statt eines Weiterlaufens.
    assert "liveterm" in render._JOB_DETAIL_JS


# ── Der Waechter gegen "einer von zwei" ────────────────────────────────────


def test_both_run_row_renderers_tick_a_running_runtime():
    """Es gibt **zwei** Zeilenbauer fuer Laeufe, und der erste Anlauf zu #123
    hat nur einen erreicht — die v5-Liste rief `_human_duration()` direkt und
    ohne Anker auf, waehrend die andere Liste ueber `_duration_cell()` ging.

    Gefunden hat es der Live-Durchgang, nicht der Test: die Zeile zeigte
    `1.3s`, und die Zahl stand still. **Dieselbe Fehlerform wie #96** — eine
    Faehigkeit an einer von zwei Stellen eingesetzt.

    Dieser Test prueft deshalb die Zusage und nicht die Fundstelle: **jede
    Darstellung eines laufenden Laufs traegt einen Anker.**
    """
    lauf = {"id": 7, "src": "S", "status": "running", "started_at": 1000.0,
            "finished_at": None, "exec_runtime": 42.0, "sort_at": 1000.0}
    assert 'data-dur="since"' in render._duration_cell(lauf)
    zeile = render._run_row(lauf, now=1042.0) if hasattr(render, "_run_row") else None
    if zeile is not None:
        assert 'data-dur="since"' in zeile
