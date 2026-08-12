"""Woran der Fortschrittsbalken sich misst (#150).

`#146` hat in `v0.8.5` repariert, **welcher Datensatz** den laufenden Lauf
trägt — Marker und tickende Zeit erscheinen seither auch beim lokalen Lauf.
Nicht repariert war, **woran der Balken sich misst.**

**Die Begründung im Docstring stimmt, ihre Umsetzung nicht.** P90 ist eine
Eigenschaft des Jobs und nicht seines Startwegs — aber `runtime_p90()` rechnet
ausschliesslich über die Scheduler-Datenbank, und zwar ausdrücklich nur über
`domain='scheduled'`. **Ein Job, der nur lokal läuft, sammelt dort nie Läufe.**

Befund m.rau, 2026-08-12: *„der Fortschrittsbalken erscheint nur bei Scheduler
Läufen! Warum nicht bei Client Läufen?"* — live nachgemessen an den beiden zu
diesem Zeitpunkt lokal laufenden Jobs: beide `runtime_p90 = None`.

**Dazu ein zweiter Fund, der eigenständig zählt:** die dokumentierte Kaskade
`P90 → wall_time → nichts` erreichte ihre mittlere Stufe nie. `schedule_view()`
lieferte `wall_time` nicht aus — das Feld steht in der Tabelle und wird von
`job_full_view()` auch ausgegeben, nur die Sicht, die der Renderer liest, führte
es nicht.
"""

from __future__ import annotations

from bibi.controller import render
from bibi.daemon import job_db


def _lauf(**kw) -> dict:
    return {"row_status": "running", "started_at": 1_000_000.0, **kw}


NOW = 1_000_010.0     # zehn Sekunden nach dem Start


# ── Die mittlere Stufe der Kaskade ─────────────────────────────────────────


def test_the_schedule_view_carries_the_time_limit(tmp_path):
    """`wall_time` erreicht den Renderer.

    Ohne dieses Feld ist `mass.get("wall_time")` **immer** `None`, und der
    Fallback, den der Docstring von `_pbar()` als Normalfall beschreibt, ist
    seit seinem Bau tot.
    """
    conn = job_db.connect(tmp_path / "jobs.sqlite")
    conn.execute(
        "INSERT INTO jobs (slug, kind, schedule, payload, status, wall_time,"
        "                  active, schedule_ref)"
        " VALUES ('a', 'job', '0 * * * *', 'echo hi', 'pending', 900, 1, 'a.md')")
    conn.commit()
    zeile = conn.execute("SELECT * FROM jobs WHERE slug='a'").fetchone()
    assert job_db.schedule_view(zeile).get("wall_time") == 900


def test_the_bar_falls_back_to_the_time_limit():
    html = render._pbar(_lauf(), {"wall_time": 900}, NOW)
    assert 'data-refkind="wall"' in html, html


# ── Der Massstab des benutzten Startwegs ───────────────────────────────────


def test_a_local_run_measures_against_its_own_history():
    """Der lokale Datensatz bringt seinen eigenen Massstab mit."""
    lokal = _lauf(status="running", runtime_p90=20.0)
    html = render._pbar(lokal, {}, NOW)
    assert 'data-refkind="p90"' in html and 'data-ref="20.0"' in html, html


def test_the_scheduler_keeps_the_first_word():
    """**Der bestehende Fall bleibt unverändert.** Trägt der Scheduler eine
    P90, misst sich der Lauf an ihr — auch ein lokal gestarteter. Er ist
    derselbe Job auf derselben Maschine, und die längere Historie ist die
    bessere Auskunft."""
    lokal = _lauf(status="running", runtime_p90=99.0)
    html = render._pbar(lokal, {"runtime_p90": 20.0}, NOW)
    assert 'data-ref="20.0"' in html, html


def test_no_measure_still_means_no_bar():
    """**Die Gegenprobe, und sie hat beim `v0.8.5`-Bau schon einmal
    gegriffen.**

    Der damalige Balken-Test blieb auch nach dem Fix rot — weil sein Datum
    keine P90 trug. Das war kein Fehler des Fixes, sondern die Bestätigung
    seiner Trennung: *„ein erfundener Massstab ist schlimmer als keiner."*
    """
    assert render._pbar(_lauf(), {}, NOW) == ""


# ── Eine Regel, nicht zwei ─────────────────────────────────────────────────


def test_both_sides_rank_the_same_way():
    """**Der P90-Rang steht an einer Stelle.**

    Zwei Implementierungen derselben Regel sind in diesem Code schon zweimal
    auseinandergelaufen (`#102`, `#126`, beide am Aktualitäts-Urteil). Die
    lokale Seite ruft deshalb dieselbe Funktion wie die SQL-Abfrage.

    Nearest-rank über zehn Werte ist der neunte: `ceil(0.9 * 10) = 9`.
    """
    werte = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert job_db.p90_rang(werte) == 9.0


def test_under_five_runs_there_is_no_percentile():
    """*„Ein P90 über drei Werte ist eine Behauptung"* (`#132`). Der
    Mindestbestand gilt auf beiden Seiten gleich."""
    assert job_db.p90_rang([1.0, 2.0, 3.0, 4.0]) is None


# ── Die lokale P90 entsteht überhaupt ──────────────────────────────────────


def _lauf_eintrag(slug, i, dauer, status="complete"):
    return {"slug": slug, "status": status, "exec_runtime": dauer,
            "finished_at": 1_000_000.0 + i}


def test_the_local_side_computes_its_own_percentile():
    """**Der Teil, ohne den der Rest wirkungslos bleibt.**

    `_pbar()` liest den lokalen Massstab am Lauf-Datensatz — den muss jemand
    dorthin schreiben. Die Scheduler-Seite tut das seit `#132` über eine
    SQL-Abfrage; die Client-Seite hatte nichts Vergleichbares, und `#146` hat
    das nicht bemerkt, weil sein Test einen Datensatz von Hand baute.
    """
    from bibi.controller import _local_run_status_aus

    eintraege = [_lauf_eintrag("a", i, float(i + 1)) for i in range(10)]
    aus = _local_run_status_aus(eintraege)
    # Nearest-rank über zehn Werte 1.0 … 10.0 ist der neunte.
    assert aus["a"]["runtime_p90"] == 9.0


def test_only_finished_local_runs_count():
    """**Die Gegenprobe, und sie ist dieselbe wie auf der Scheduler-Seite:**
    nur `complete` zählt. Auf der Scheduler-DB nachgemessen (23 793 Läufe)
    gehört *jeder* Wert über einer Stunde zu einem `killed` — ein Lauf, der
    abgebrochen wurde, hat nie zu Ende gerechnet und beantwortet die Frage der
    Spalte gar nicht.
    """
    from bibi.controller import _local_run_status_aus

    eintraege = ([_lauf_eintrag("a", i, 2.0) for i in range(4)]
                 + [_lauf_eintrag("a", 9, 9999.0, status="killed")])
    aus = _local_run_status_aus(eintraege)
    assert aus["a"].get("runtime_p90") is None, "ein Abbruch ist in die P90 gegangen"
