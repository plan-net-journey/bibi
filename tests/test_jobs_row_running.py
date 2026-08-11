"""Was `LAST` und `NEXT` sagen, solange ein Lauf läuft (#136).

**Der Wunsch:** Während ein Lauf läuft, sollen die Zeitspalten den **laufenden**
Lauf zeigen statt den vorigen — `LAST` seine Startzeit, `NEXT` seine bisherige
Laufzeit, mittickend.

**Warum das an #129 hängt.** Diese Spalten lesen die `jobs`-Zeile, und
`upsert_schedule()` schreibt bei jedem Rescan deren Spec-Spalten neu — darunter
`next_fire_at`, das `NEXT` heute zeigt. Eine Anzeige, die den laufenden Lauf
behauptet, aber aus einer Quelle liest, die sich unter ihr ändern darf, ist
nicht bloß gelegentlich falsch, sondern grundsätzlich nicht zusagefähig. Seit
`#129` friert der Lauf seine Attribute bei START ein; `started_at` gehört
ohnehin dem Lauf und wird von keinem Rescan angefasst.

**Die Falle steht im Ticket.** Ein Ticker macht eine Zelle lebendig, ohne dass
ihr Status je nachzieht — genau die Eigenschaft, die `#131` so unangenehm machte
(*„eine Anzeige, die stillsteht, verrät sich beim Verweilen; eine, die sich
falsch bewegt, nicht"*). Was hier tickt, muss auch refetchen; das Refetch der
Zeile hängt am Sammel-Target `jobs` und ist dort belegt.

**Die Client-Hälfte fehlt hier bewusst.** Sie verlangt eine `LAST`-Spalte im
Client-Block, und die legt erst #135 an. Sie kommt mit ihm, statt hier eine
Spalte zu erfinden, die zwei Runden später wieder umgebaut wird.
"""

from __future__ import annotations

from bibi.controller import render
from bibi.controller.jobs_view import JobRow, Segment

NOW = 1_000_000.0
GESTARTET = NOW - 90.0
FRUEHER = NOW - 7200.0
DEMNAECHST = NOW + 3600.0


def _zeile(**sched) -> str:
    row = JobRow(slug="x", segment=Segment.SCHEDULE, scheduler=sched,
                 spec={"payload": "echo hi"})
    return render._jobs_zeile(row, NOW)


def test_last_shows_the_start_of_the_running_run(monkeypatch):
    html = _zeile(row_status="running", started_at=GESTARTET,
                  last_run_at=FRUEHER, next_fire_at=DEMNAECHST)
    assert render._uhrzeit(GESTARTET, NOW) in html
    assert render._uhrzeit(FRUEHER, NOW) not in html


def test_next_shows_the_running_time_and_ticks(monkeypatch):
    """Die Laufzeit tickt im Browser (`_DURATION_JS`), sie wird nicht
    servergerendert nachgereicht — die Zelle trägt den Startzeitpunkt und die
    Art der Rechnung."""
    html = _zeile(row_status="running", started_at=GESTARTET,
                  last_run_at=FRUEHER, next_fire_at=DEMNAECHST)
    assert 'data-dur="since"' in html
    assert f'data-at="{GESTARTET}"' in html
    assert render._uhrzeit(DEMNAECHST, NOW) not in html


def test_a_rescan_during_the_run_does_not_move_the_cells():
    """Die Gegenprobe aus dem Ticket. `next_fire_at` ist eine Spec-Spalte und
    wird bei jedem Rescan neu berechnet; solange ein Lauf läuft, darf das an
    diesen Zellen nichts ändern. Ohne sie wäre der Test auch mit der heutigen,
    kippenden Quelle grün."""
    vorher = _zeile(row_status="running", started_at=GESTARTET,
                    last_run_at=FRUEHER, next_fire_at=DEMNAECHST)
    nachher = _zeile(row_status="running", started_at=GESTARTET,
                     last_run_at=FRUEHER, next_fire_at=DEMNAECHST + 12345.0)
    assert vorher == nachher


def test_a_waiting_job_still_shows_its_appointment():
    """Gegenprobe: außerhalb eines Laufs bleibt alles, wie es war — `NEXT` nennt
    den Termin, `LAST` den letzten Lauf. Ohne sie wäre eine Umstellung grün, die
    die Zukunft überall durch eine Laufzeit ersetzt."""
    html = _zeile(row_status="pending", started_at=None,
                  last_run_at=FRUEHER, next_fire_at=DEMNAECHST)
    assert render._uhrzeit(DEMNAECHST, NOW) in html
    assert render._uhrzeit(FRUEHER, NOW) in html
    assert 'data-dur="since"' not in html


def test_a_finished_job_is_untouched():
    """Zweite Gegenprobe: ein terminaler Zustand mit stehengebliebenem
    `started_at` darf keine Laufzeit hochzählen — sonst tickte eine Zelle für
    einen Lauf, der längst vorbei ist."""
    html = _zeile(row_status="complete", started_at=GESTARTET,
                  last_run_at=FRUEHER, next_fire_at=DEMNAECHST)
    assert 'data-dur="since"' not in html
    assert render._uhrzeit(DEMNAECHST, NOW) in html
