"""Was der Snapshot trägt (`v0.8.15`) — und was er nicht tragen darf.

**Eine Regression aus `v0.8.13`, die den Scheduler zum Stillstand gebracht
hat.** `#182` nahm `run_snapshot` in `job_view()` auf, damit die Attributseite
eines laufenden Laufs über die API erreichbar wird. Der Grund war richtig. Nur
speist dieselbe Sicht über `job_full_view()` auch die **Serialisierung** —
und damit enthielt der gespeicherte Snapshot seinen eigenen Vorgänger.

Gemessen auf `sarasate`, `gmail-transfer` (Takt 15 min):

```
03:52     6,5 MiB
04:07    13,0 MiB
04:22    26,0 MiB
...
05:37   832,0 MiB   <- letzter erfolgreicher Lauf
```

Vier Stunden von 0,03 MiB bis zum OOM-Kill bei 14,85 GB RSS.

**Die Fehlerform ist die von `v0.8.14`, eine Ebene höher:** zwei Verbraucher
teilen sich eine Sicht, die Änderung wurde für den einen geschrieben, und die
beiden Stellen liegen fünfhundert Zeilen auseinander.

**Kein Test konnte es fangen, weil keiner die Größe gemessen hat.** Ein Test
über zwei Läufe wäre grün geblieben — bei 6 Byte statt 3. Deshalb prüft der
erste Test hier über **fünf** Läufe und gegen eine Schranke, nicht auf
Gleichheit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.schedule import parser


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _job(conn, slug: str = "wachstum") -> str:
    pr = parser.parse_text(
        f'---\nslug: {slug}\nschedule: "* * * * *"\nattempts: 9\njob: echo hi\n---\n',
        schedule_ref=f"case/x/{slug}.md", path=Path(f"case/x/{slug}.md"))
    assert pr.is_ok, pr.error
    return job_db.upsert_schedule(conn, pr, 1000.0)


def _snapshot_groesse(conn, job_id: str) -> int:
    r = conn.execute("SELECT LENGTH(run_snapshot) AS n FROM jobs WHERE id=?",
                     (job_id,)).fetchone()
    return r["n"] or 0


# ── #197: der Snapshot enthält sich selbst ─────────────────────────────────


def test_der_snapshot_waechst_ueber_laeufe_nicht(conn):
    """**Der Rot-Schritt, an der Wirkung gemessen.**

    Fünf Läufe, und der Snapshot darf danach nicht größer sein als nach dem
    ersten. Geprüft wird gegen eine großzügige Schranke statt auf Gleichheit:
    ein Snapshot **darf** sich zwischen Läufen ändern (`fire` zählt hoch,
    `attempt` auch) — er darf nur nicht **wachsen wie eine Potenz**.

    Vor dem Fix verdoppelt sich der Wert bei jedem Lauf; nach fünf Läufen ist
    er rund 32-mal so groß, und das Escaping treibt ihn noch darüber.
    """
    jid = _job(conn)
    groessen = []
    # `upsert_schedule(…, 1000.0)` legt `next_fire_at` auf die nächste volle
    # Minute (1020.0) — vor diesem Zeitpunkt ist die Zeile nicht fällig, und
    # `reserve_next()` liefert zu Recht nichts.
    for lauf in range(5):
        jetzt = 1020.0 + lauf * 60
        r = job_db.reserve_next(conn, host="h", now=jetzt)
        assert r is not None, f"Lauf {lauf} wurde nicht reserviert"
        groessen.append(_snapshot_groesse(conn, jid))
        assert job_db.report_status(conn, jid, status="complete",
                                    now=jetzt + 1) == "ok"
        conn.execute("UPDATE jobs SET next_fire_at=? WHERE id=?",
                     (jetzt + 60, jid))
        conn.commit()

    assert groessen[0] > 0, "der erste Lauf hat gar keinen Snapshot geschrieben"
    assert groessen[-1] <= groessen[0] * 2, (
        f"der Snapshot wächst über die Läufe: {groessen} Bytes — er enthält "
        f"seinen eigenen Vorgänger, und das Wachstum ist exponentiell in der "
        f"Zahl der Läufe (#197)")


def test_die_serialisierte_sicht_traegt_kein_snapshot_feld(conn):
    """**Der Wächter, und er ist der eigentliche Posten.**

    Der Test darüber misst die Wirkung an einem Job. Dieser hier schließt die
    **Klasse**: was serialisiert wird, darf das Feld nicht enthalten, in das
    es serialisiert wird — unabhängig davon, wie viele Läufe jemand probiert.

    **Er prüft `job_full_view()` und nicht die DB**, weil dort die Entscheidung
    fällt. Beide Schreibstellen (`reserve_next()` für `jobs.run_snapshot`,
    `archive_run()` für `journal.snapshot`) gehen durch diese eine Funktion —
    und genau deshalb hat der eine Fehler beide Spalten aufgebläht.
    """
    jid = _job(conn)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

    sicht = job_db.job_full_view(row)
    assert "run_snapshot" not in sicht, (
        "job_full_view() trägt run_snapshot, und genau diese Sicht wird in "
        "run_snapshot geschrieben — jeder Lauf verpackt damit den vorigen "
        "(#197)")


def test_die_api_sicht_traegt_es_weiterhin(conn):
    """**Die Gegenprobe, und sie schützt den Grund, aus dem `#182` gebaut wurde.**

    Ohne sie wäre ein Fix grün, der das Feld überall entfernt — und dann wäre
    die Attributseite eines laufenden Laufs wieder nur auf dem Knoten zu haben,
    auf dem er läuft. Genau das hat `#182` behoben.

    **Die Trennung ist der ganze Fix:** `job_view()` geht über die API hinaus
    und trägt es; `job_full_view()` wird gespeichert und trägt es nicht.
    """
    jid = _job(conn)
    conn.execute("UPDATE jobs SET run_snapshot=? WHERE id=?", ('{"a": 1}', jid))
    conn.commit()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

    assert job_db.job_view(row)["run_snapshot"] == '{"a": 1}', (
        "job_view() gibt run_snapshot nicht mehr hinaus — die Attributseite "
        "eines laufenden Laufs ist damit wieder knotengebunden (#182)")


def test_ein_bestehender_verschachtelter_snapshot_bleibt_lesbar(conn):
    """Der Bestand: 2,5 GB verschachtelte Snapshots liegen in der Live-DB.

    Der Fix verhindert neues Wachstum; er räumt nichts weg. Bis das Ausräumen
    durch ist, muss die Ansicht mit dem Vorhandenen umgehen können — und
    `_lauf_attribute()` tut das, weil ihm ein Feld zu viel egal ist.
    """
    jid = _job(conn)
    verschachtelt = json.dumps({"slug": "alt", "fire": 3,
                                "run_snapshot": json.dumps({"slug": "aelter"})})
    conn.execute("UPDATE jobs SET run_snapshot=? WHERE id=?", (verschachtelt, jid))
    conn.commit()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

    assert job_db._lauf_attribute(row)["slug"] == "alt"


# ── Retention: das Journal wächst nicht unbegrenzt ─────────────────────────


def test_journal_zeilen_aelter_als_die_frist_verschwinden(conn):
    """**Entscheidung m.rau, 2026-08-14: 90 Tage.**

    `#197` erklärt, warum die Datenbank in vier Stunden auf 2,5 GB wuchs — es
    erklärt nicht, warum es überhaupt kein Dach gab. Eine Ablage ohne Frist
    wächst mit der Laufzeit des Systems, und der einzige Grund, warum das
    bisher nicht auffiel, ist, dass die Snapshots klein waren.

    **Der Schnitt liegt am `archived_at`**, nicht am `finished_at`: ein Lauf
    zählt ab dem Moment, in dem er ins Journal kam.
    """
    _job(conn)
    jetzt = time.time()
    for tage, slug in ((120, "uralt"), (91, "knapp-drueber"),
                       (89, "knapp-drunter"), (1, "frisch")):
        conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, archived_at, "
            "finished_at) VALUES (?,?,'job','complete',?,?)",
            (f"{slug}:0", slug, jetzt - tage * 86400, jetzt - tage * 86400))
    conn.commit()

    entfernt = job_db.prune_journal(conn, max_age_days=90, now=jetzt)

    verblieben = {r["slug"] for r in conn.execute("SELECT slug FROM journal")}
    assert verblieben == {"knapp-drunter", "frisch"}, (
        f"die Frist greift nicht sauber — verblieben: {sorted(verblieben)}")
    assert entfernt == 2, f"gemeldet wurden {entfernt} entfernte Zeilen, nicht 2"


def test_die_frist_laesst_das_journal_eines_frischen_systems_in_ruhe(conn):
    """Die Gegenprobe: ohne alte Zeilen passiert nichts, und der Aufruf ist
    billig genug, um in jedem Sweep-Tick zu stehen."""
    _job(conn)
    assert job_db.prune_journal(conn, max_age_days=90) == 0


def test_der_sweeper_laesst_die_frist_wirklich_laufen(tmp_path):
    """**Sonst wäre die Frist gebauter, ungenutzter Code** — das Muster aus
    Runde 2 der neunten Klammer, fünf von sechs Posten.

    Geprüft wird über `tick_once()`, nicht über den Aufruf von
    `prune_journal()`: dass die Funktion existiert, sagt nichts darüber, ob
    sie je läuft. Genau diese Unterscheidung — Verdrahtung gegen Wirkung — hat
    `v0.8.12` gekostet.
    """
    from bibi.daemon.sweeper import Sweeper

    db = tmp_path / "jobs.sqlite"
    c = job_db.connect(db)
    jetzt = time.time()
    try:
        c.execute(
            "INSERT INTO journal (run_id, slug, kind, status, archived_at) "
            "VALUES ('alt:0','alt','job','complete',?)", (jetzt - 200 * 86400,))
        c.execute(
            "INSERT INTO journal (run_id, slug, kind, status, archived_at) "
            "VALUES ('neu:0','neu','job','complete',?)", (jetzt - 3600,))
        c.commit()
    finally:
        c.close()

    # `prune_interval=0` und ein `now` **nach** der Konstruktion: `_last_prune`
    # wird im Konstruktor auf die Uhr gesetzt, damit der erste Prune erst nach
    # einem vollen Intervall kommt (dasselbe Muster wie beim PID-Check). Mit
    # einem `now` von *vor* dem Konstruktor wäre die Differenz negativ, und der
    # Test hätte den Code für kaputt gehalten, obwohl die Messung schief war.
    feger = Sweeper(db_path=db, autorun=False, prune_interval=0)
    feger.tick_once(now=time.time() + 1)

    c = job_db.connect(db)
    try:
        uebrig = {r["slug"] for r in c.execute("SELECT slug FROM journal")}
    finally:
        c.close()
    assert uebrig == {"neu"}, (
        f"der Sweeper räumt das Journal nicht — übrig: {sorted(uebrig)} (#197)")
