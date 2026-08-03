"""Die Zeilen des Jobs-Screens (bibi5, FE-Spezifikation §4).

Eine Zeile je Slug, nicht je Speicher. Der Slug ist die Identität — auch dann,
wenn er auf beiden Seiten existiert, und das ist der Normalfall: live haben 13
von 13 aktiven Scheduler-Schedules eine lokale MD. Zwei Zeilen je Slug würden
54 statt 36 erzeugen und bei jeder Sortierung außer der nach Slug auseinander-
reißen.

Reine Funktion über Listen — kein Datenbankzugriff, kein HTTP. Dadurch ist die
Klassifikation ohne Fixtures prüfbar, und sie ist es, die schwierig ist: welches
Segment, welches Beziehungslabel, was zählt als sichtbar.
"""

from __future__ import annotations

import pytest

from bibi.controller.jobs_view import Segment, build_rows

NOW = 1_000_000.0


def _lokal(slug, schedule="0 * * * *", **kw):
    return {"slug": slug, "schedule": schedule, "payload": "echo hi", **kw}


def _sched(slug, status="complete", schedule="0 * * * *", **kw):
    return {"slug": slug, "status": status, "schedule": schedule, **kw}


# ── Segment 1: was einen erwarteten nächsten Lauf hat ───────────────────────


def test_a_cron_job_lands_in_schedule():
    rows = build_rows(local=[_lokal("nightly")], scheduler=[], journal=[], now=NOW)
    assert rows[0].segment is Segment.SCHEDULE


def test_startup_counts_as_schedule():
    """`startup` hat einen Termin — den nächsten Daemon-Start."""
    rows = build_rows(local=[_lokal("app", schedule="startup")], scheduler=[],
                      journal=[], now=NOW)
    assert rows[0].segment is Segment.SCHEDULE


def test_an_open_oneshot_is_scheduled():
    """Ein `at`-Job mit `status: open` steht noch aus und gehört zu dem, was
    kommt — auch wenn sein Zeitpunkt bereits verstrichen ist."""
    rows = build_rows(local=[_lokal("20260728.at-1507", schedule=None,
                                    at="2026-07-28T15:07:00", status="open")],
                      scheduler=[], journal=[], now=NOW)
    assert rows[0].segment is Segment.SCHEDULE


# ── Segment 2: was gerufen wird ─────────────────────────────────────────────


def test_adhoc_lands_in_its_own_segment():
    rows = build_rows(local=[_lokal("Runner", schedule="adhoc")], scheduler=[],
                      journal=[], now=NOW)
    assert rows[0].segment is Segment.ADHOC


def test_on_demand_is_the_same_thing():
    """`on_demand` ist die kanonische Form, `adhoc` die sprechende — der
    Parser löst auf, der Screen darf beide sehen."""
    rows = build_rows(local=[_lokal("Runner", schedule="on_demand")], scheduler=[],
                      journal=[], now=NOW)
    assert rows[0].segment is Segment.ADHOC


def test_a_service_is_not_separated_from_other_adhoc_jobs():
    """Dienste (`app_port`) stehen bei den anderen — sie sind kein eigener Typ,
    nur einer, der lange läuft."""
    rows = build_rows(local=[_lokal("hitl-app", schedule="adhoc", app_port=9100)],
                      scheduler=[], journal=[], now=NOW)
    assert rows[0].segment is Segment.ADHOC


# ── „never" verschwindet — Änderung 11 ──────────────────────────────────────


def test_never_without_history_is_invisible():
    """Der Kern von Änderung 11: wer `never` schreibt, nimmt die MD aus dem
    Blick — ohne ungültige Syntax und ohne Fehlerliste."""
    rows = build_rows(local=[_lokal("ruht", schedule="never")], scheduler=[],
                      journal=[], now=NOW)
    assert rows == []


def test_never_with_history_shows_up_in_journal():
    """…und wird wiederauffindbar, sobald sie Historie hat."""
    rows = build_rows(local=[_lokal("ruht", schedule="never")], scheduler=[],
                      journal=[{"slug": "ruht", "status": "complete",
                                "archived_at": NOW - 3600}], now=NOW)
    assert len(rows) == 1
    assert rows[0].segment is Segment.JOURNAL


def test_an_empty_schedule_is_never_too():
    for wert in (None, "", "~", "-"):
        rows = build_rows(local=[_lokal("ruht", schedule=wert)], scheduler=[],
                          journal=[], now=NOW)
        assert rows == [], f"{wert!r} muss wie never wirken"


# ── Beziehung zwischen den beiden Speichern ─────────────────────────────────


def test_a_job_on_both_sides_carries_no_label():
    """Der Normalfall bekommt kein Etikett — sonst trägt jede Zeile eines."""
    rows = build_rows(local=[_lokal("gmail")], scheduler=[_sched("gmail")],
                      journal=[], now=NOW)
    assert rows[0].relation is None


def test_only_local_is_new():
    rows = build_rows(local=[_lokal("frisch")], scheduler=[], journal=[], now=NOW)
    assert rows[0].relation == "new"


def test_only_scheduler_is_deleted():
    """Der Scheduler führt ihn, die MD ist weg — das ist kein „dropped", denn
    er ist noch aktiv."""
    rows = build_rows(local=[], scheduler=[_sched("verwaist")], journal=[], now=NOW)
    assert rows[0].relation == "deleted"


def test_history_only_is_dropped():
    """Weder MD noch Scheduler-Eintrag, aber Läufe im Journal."""
    rows = build_rows(local=[], scheduler=[],
                      journal=[{"slug": "alt", "status": "complete",
                                "archived_at": NOW - 7200}], now=NOW)
    assert rows[0].relation == "dropped"
    assert rows[0].segment is Segment.JOURNAL


def test_two_files_claiming_one_slug_are_duplicates():
    """Das einzige Label, das ein Problem im Vault meldet statt eines
    Verhältnisses zwischen zwei Speichern — es verlangt eine Umbenennung,
    keinen Sync. Beide Pfade müssen dranhängen, sonst weiß niemand, welche
    Dateien gemeint sind."""
    rows = build_rows(
        local=[_lokal("Backup", repo_path="case/eins/Backup.md"),
               _lokal("Backup", repo_path="case/zwei/Backup.md")],
        scheduler=[], journal=[], now=NOW)
    assert len(rows) == 1
    assert rows[0].relation == "duplicate"
    assert set(rows[0].paths) == {"case/eins/Backup.md", "case/zwei/Backup.md"}


# ── Eine Zeile, zwei Seiten ─────────────────────────────────────────────────


def test_both_sides_are_carried_in_one_row():
    rows = build_rows(local=[_lokal("EngineCI")],
                      scheduler=[_sched("EngineCI", status="complete")],
                      journal=[], now=NOW,
                      local_runs={"EngineCI": {"status": "error", "exec_runtime": 231.9}})
    assert len(rows) == 1
    assert rows[0].scheduler["status"] == "complete"
    assert rows[0].local["status"] == "error"


# ── Die 24H-Kennzahl (FE-Spezifikation §4.4) ───────────────────────────────
#
# `complete / expected + manual = %`. Sie ersetzt Chart, Sparkline und Heatmap
# vollständig, und sie kann etwas, das keins davon konnte: „da ist etwas
# passiert" (Zähler unter Nenner) von „da habe ich etwas gemacht" (`+n`)
# unterscheiden. Ein Chart zeigt beides als Ausschlag und kann die Richtung
# nicht benennen. Außerdem ist es eine Zahl — sortierbar, filterbar, summierbar.

from bibi.controller.jobs_view import Quote, quote_24h   # noqa: E402


def _lauf(slug, status="complete", vor=3600.0, **kw):
    return {"slug": slug, "status": status, "archived_at": NOW - vor, **kw}


def test_everything_as_planned():
    q = quote_24h(runs=[_lauf("x") for _ in range(96)], expected=96, manual=0)
    assert (q.complete, q.expected, q.manual) == (96, 96, 0)
    assert q.prozent == 100
    assert str(q) == "96/96+0 100%"


def test_manual_starts_widen_the_denominator():
    """`30/24+6 100%` — 24 geplant, 6 von Hand, alle 30 erfolgreich. Ohne das
    `+6` sähe es nach 125 % aus, und niemand wüsste, woher der Überschuss kommt."""
    q = quote_24h(runs=[_lauf("x") for _ in range(30)], expected=24, manual=6)
    assert str(q) == "30/24+6 100%"


def test_missed_firings_show_as_a_gap():
    """Immer abgerundet: 71/96 sind 73,96 % und werden zu 73, nicht zu 74.

    Die FE-Spezifikation rechnet in ihren Beispielen uneinheitlich (dort steht
    hier „74 %", zwei Zeilen weiter aber „66 %" für 66,67 %). Von den beiden
    Regeln ist Abrunden die ehrlichere — eine Erfolgsquote soll nie mehr
    behaupten als erreicht wurde — und die einzige ohne Sonderfall bei 100 %.
    """
    q = quote_24h(runs=[_lauf("x") for _ in range(71)], expected=96, manual=0)
    assert str(q) == "71/96+0 73%"


def test_adhoc_has_no_plan():
    """`2/0+3 66%` — kein Plan, drei Starts, zwei davon erfolgreich. Der Nenner
    ist dann allein das, was jemand angestoßen hat."""
    q = quote_24h(runs=[_lauf("x"), _lauf("x"), _lauf("x", status="error")],
                  expected=0, manual=3)
    assert str(q) == "2/0+3 66%"


def test_only_the_last_day_counts():
    alt = [_lauf("x", vor=25 * 3600) for _ in range(50)]
    neu = [_lauf("x", vor=3600) for _ in range(2)]
    q = quote_24h(runs=alt + neu, expected=2, manual=0, now=NOW)
    assert q.complete == 2, "was älter als 24 h ist, zählt nicht mit"


def test_failed_runs_do_not_count_as_complete():
    q = quote_24h(runs=[_lauf("x"), _lauf("x", status="error"),
                        _lauf("x", status="killed")], expected=3, manual=0)
    assert q.complete == 1
    assert str(q) == "1/3+0 33%"


def test_nothing_expected_and_nothing_run_shows_a_dash():
    """Ein Job ohne Erwartung und ohne Läufe hat keine Quote — dort steht ein
    Strich, keine 0 %. Null Prozent hieße, er hätte versagt."""
    q = quote_24h(runs=[], expected=0, manual=0)
    assert str(q) == "—"
    assert q.prozent is None


def test_almost_complete_never_reads_as_hundred():
    """99,6 % dürfen nicht als „alles gut" erscheinen, wenn ein Lauf fehlt —
    der Fall, an dem sich die Rundungsregel entscheidet."""
    q = quote_24h(runs=[_lauf("x") for _ in range(239)], expected=240, manual=0)
    assert q.prozent == 99
    q2 = quote_24h(runs=[_lauf("x") for _ in range(240)], expected=240, manual=0)
    assert q2.prozent == 100


# ── Die Feldnamen des Schedulers (Live-Abnahme 2026-08-03) ─────────────────


def test_the_scheduler_calls_its_trigger_trigger():
    """`/-/schedule` liefert `trigger`, nicht `schedule` — live abgenommen.
    Für Zeilen, die es nur beim Host gibt, ist das die einzige Quelle."""
    rows = build_rows(local=[], scheduler=[{"slug": "nur-host", "trigger": "adhoc"}],
                      journal=[], now=NOW)
    assert rows[0].segment is Segment.ADHOC


def test_a_host_only_never_job_stays_invisible():
    """Auch über das andere Feld muss „ruht" wirken, sonst erschiene ein
    stillgelegter Job wieder, sobald seine MD verschwindet."""
    rows = build_rows(local=[], scheduler=[{"slug": "ruht", "trigger": "never"}],
                      journal=[], now=NOW)
    assert rows == []


# ── Erwartete Feuerungen aus dem Trigger ───────────────────────────────────


def test_hourly_cron_expects_24_firings_a_day():
    from bibi.controller.jobs_view import erwartete_laeufe
    assert erwartete_laeufe("0 * * * *") == 24


def test_every_fifteen_minutes_expects_96():
    from bibi.controller.jobs_view import erwartete_laeufe
    assert erwartete_laeufe("*/15 * * * *") == 96


def test_a_daily_job_expects_one():
    from bibi.controller.jobs_view import erwartete_laeufe
    assert erwartete_laeufe("0 3 * * *") == 1


def test_adhoc_and_never_expect_nothing():
    """Ohne Rhythmus gibt es keine Erwartung — der Nenner ist dann allein das,
    was jemand angestoßen hat."""
    from bibi.controller.jobs_view import erwartete_laeufe
    for wert in ("adhoc", "on_demand", "never", "startup", None, ""):
        assert erwartete_laeufe(wert) == 0, wert


def test_an_unparsable_expression_expects_nothing():
    """Ein kaputter Ausdruck darf den Screen nicht kippen — er hat dann eben
    keine Erwartung, und die Zeile zeigt einen Strich."""
    from bibi.controller.jobs_view import erwartete_laeufe
    assert erwartete_laeufe("kein cron") == 0


def test_an_inactive_scheduler_entry_belongs_to_history():
    """`active=0` heißt: der Host führt ihn nicht mehr aus. Er gehört zur
    Historie, nicht zu dem, was kommt — sonst stünden in Segment 1 Jobs, die
    seit Tagen tot sind (live: 16 von 29 Schedules sind inaktiv)."""
    rows = build_rows(local=[], scheduler=[{"slug": "alt", "trigger": "0 * * * *",
                                            "active": 0}],
                      journal=[{"slug": "alt", "status": "complete",
                                "finished_at": NOW - 7200}], now=NOW)
    assert rows[0].segment is Segment.JOURNAL


def test_an_inactive_entry_without_history_is_gone():
    rows = build_rows(local=[], scheduler=[{"slug": "alt", "trigger": "0 * * * *",
                                            "active": 0}], journal=[], now=NOW)
    assert rows == []


def test_the_quote_reads_finished_at_when_archived_at_is_absent():
    """Die HTTP-Antwort von `/-/journal` trägt `finished_at`, nicht
    `archived_at` — live abgenommen. Ohne diesen Rückfall zählte die Kennzahl
    überall 0, obwohl die Läufe da waren."""
    laeufe = [{"slug": "x", "status": "complete", "finished_at": NOW - 600}
              for _ in range(3)]
    q = quote_24h(runs=laeufe, expected=24, manual=0, now=NOW)
    assert q.complete == 3


# ── Befunde vom laufenden System (m.rau, 2026-08-03 16:14) ─────────────────


def test_a_collision_must_reach_the_screen_as_duplicate():
    """**Befund m.rau:** „Wenn ich den Job _Runner_ in 2 unterschiedlichen
    Directories habe, erscheint er als _deleted_. Hier wird kein _duplicate_
    angezeigt. Warum nicht?"

    Weil die Discovery kollidierende Slugs bewusst **nicht** in `found` legt —
    sie sind zur Laufzeit ignoriert, bis sie aufgelöst sind. Der Screen las
    aber nur `found` und sah den Slug deshalb gar nicht; der Scheduler kannte
    ihn noch, also blieb `deleted` übrig. Die Erkennung war da, die Anzeige
    bekam sie nie zu sehen.
    """
    zeilen = build_rows(
        local=[{"slug": "Runner", "schedule": "adhoc", "payload": "echo hi",
                "repo_path": "case/eins/Runner.md"},
               {"slug": "Runner", "schedule": "adhoc", "payload": "echo hi",
                "repo_path": "case/zwei/Runner.md"}],
        scheduler=[{"slug": "Runner", "trigger": "adhoc", "active": 1}],
        journal=[], now=NOW)
    assert len(zeilen) == 1
    assert zeilen[0].relation == "duplicate", \
        "die Kollision schlaegt jede andere Beziehung — sie verlangt Handeln"


def test_a_changed_file_is_modified():
    """**Befund m.rau:** „Wenn ich einen Job ändere, erscheint kein Chip
    _modified_. Ist die git Status Überprüfung und Anzeige noch nicht
    realisiert?"

    Sie war es nicht: `build_rows()` kannte nur `new`, `deleted`, `dropped`
    und `duplicate`. `modified` heißt „beide Seiten kennen ihn, die lokale MD
    weicht ab" — und woher das kommt, weiß nur git.
    """
    zeilen = build_rows(
        local=[{"slug": "Runner", "schedule": "adhoc", "payload": "echo hi",
                "repo_path": "case/x/Runner.md", "git_status": "modified"}],
        scheduler=[{"slug": "Runner", "trigger": "adhoc", "active": 1}],
        journal=[], now=NOW)
    assert zeilen[0].relation == "modified"


def test_a_clean_file_carries_no_label():
    zeilen = build_rows(
        local=[{"slug": "Runner", "schedule": "adhoc", "payload": "echo hi",
                "repo_path": "case/x/Runner.md", "git_status": "clean"}],
        scheduler=[{"slug": "Runner", "trigger": "adhoc", "active": 1}],
        journal=[], now=NOW)
    assert zeilen[0].relation is None


def test_a_duplicate_stays_in_the_band_its_files_belong_to():
    """Ein `adhoc`-Job, der zweimal existiert, gehört ins ADHOC-Band — dort
    sucht man ihn. Ihn ins Journal zu schieben, weil die Klassifikation seinen
    Trigger nicht kennt, versteckt genau das, was Aufmerksamkeit braucht."""
    zeilen = build_rows(
        local=[{"slug": "Runner", "schedule": "adhoc", "payload": "echo hi",
                "repo_path": "case/eins/Runner.md"},
               {"slug": "Runner", "schedule": "adhoc", "payload": "echo hi",
                "repo_path": "case/zwei/Runner.md"}],
        scheduler=[], journal=[{"slug": "Runner", "status": "complete",
                                "finished_at": NOW - 600}], now=NOW)
    assert zeilen[0].relation == "duplicate"
    assert zeilen[0].segment is Segment.ADHOC
