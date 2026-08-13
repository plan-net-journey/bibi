"""Was der Slot zulässt (`v0.8.11`) — wer darf einen Lauf starten?

Die Kehrseite von `v0.8.10`: dort ging es darum, was ein Zeitfeld *bedeutet*,
hier darum, was ein Zustand *erlaubt*. Drei der Posten sind Fälle, in denen eine
Prüfung etwas verweigert, das das Modell ausdrücklich vorsieht — und einer ist
der umgekehrte: etwas geschieht, das niemand ausgelöst hat.

Quelle ist durchweg der `v0.8.9`-Akzeptanz-Durchgang (m.rau, 2026-08-13).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bibi.daemon import job_db, worker as worker_mod


@pytest.fixture
def conn(tmp_path: Path):
    c = job_db.connect(tmp_path / "jobs.sqlite")
    yield c
    c.close()


def _pin(conn, slug, *, status="pending", host="testhost", attempts=3, next_fire_at=0.0):
    """Eine gepinnte Zeile, wie `run_pinned()` sie anlegt."""
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, job_uid, schedule_ref, kind, payload, priority, "
        "status, enqueued_at, next_fire_at, attempts, pinned_host, schedule) "
        "VALUES (?,?,?,?,?,?,0,?,?,?,?,?,'now')",
        (jid, slug, slug.rsplit("-", 1)[0], f"{slug}.md", "job", "exit 1",
         status, time.time(), next_fire_at, attempts, host),
    )
    conn.commit()
    return jid


# ── #175: ein Client retryt nicht von selbst ────────────────────────────────


@pytest.mark.parametrize("status", ["failed", "deferred"])
def test_der_gepinnte_loop_nimmt_wartende_zeilen_nicht_auf(conn, status: str):
    """Zusage m.rau, 2026-08-13: *„Egal ob Daemon oder Session, ein Client muss
    100% das gleiche Verhalten zeigen."*

    Am 2026-08-13 live gemessen, bevor dieser Test entstand: ein lokaler Job mit
    `attempts: 2` und `error_time: 20` lief auf dem Mac (Rollen
    `synchronizer,controller`, **kein** Scheduler) **zweimal ohne einen Klick**
    durch und endete in `error`. Auf einem Knoten ohne Daemon wäre er nach dem
    ersten Fehlschlag liegengeblieben — dasselbe Job-MD, zwei Verhalten.

    **Retry-nach-Frist ist Scheduler-Verhalten.** Ein Client hat keinen
    Scheduler, also gibt es dort keine Frist, die ein Loop bedienen dürfte.
    """
    _pin(conn, "warte-a1b2c3d4", status=status, next_fire_at=time.time() - 60)
    assert job_db.reserve_next(conn, host="testhost", pinned_only=True) is None, (
        f"ein {status}-Slot darf vom gepinnten Loop nicht aufgenommen werden")


def test_der_gepinnte_loop_nimmt_pending_weiterhin_auf(conn):
    """Die Gegenprobe — sonst wäre der Fix eine Stilllegung statt einer Regel.

    `run_pinned()` legt für jeden START eine frische `pending`-Zeile an und
    reserviert **sie**. Bliebe die liegen, startete gar nichts mehr.
    """
    _pin(conn, "start-c3d4e5f6", status="pending", next_fire_at=time.time() - 60)
    r = job_db.reserve_next(conn, host="testhost", pinned_only=True)
    assert r is not None and r["slug"] == "start-c3d4e5f6"


def test_der_team_loop_nimmt_wartende_zeilen_weiter_auf(conn):
    """Die zweite Gegenprobe, und die wichtigere: **der Scheduler bleibt, wie er ist.**

    Die Sperre gehört in die Rolle, nicht in die Zustandstabelle — ein
    ungepinnter `failed`-Job auf dem Team-Pfad hat eine Frist, und die bedient
    weiterhin ein Dispatcher.
    """
    import secrets
    jid = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, priority, status, "
        "enqueued_at, next_fire_at, attempts) VALUES (?,?,?,?,?,0,'failed',?,?,3)",
        (jid, "team-e5f60718", "team-e5f6.md", "job", "exit 1", time.time(), time.time() - 60),
    )
    conn.commit()
    r = job_db.reserve_next(conn, host="testhost")
    assert r is not None and r["slug"] == "team-e5f60718", (
        "ein ungepinnter failed-Job gehört weiterhin dem Scheduler")


# ── #171: ein lokaler failed-Slot lässt sich starten ────────────────────────


@pytest.mark.parametrize("status", ["failed", "deferred"])
def test_ein_wartender_slot_blockiert_keinen_neustart(tmp_path: Path, status: str):
    """*„Einen lokalen Job im Status failed kann ich nicht starten!"* (m.rau)

    `_PINNED_LIVE_STATUSES` beantwortet zwei Fragen: *„was ist noch nicht
    abgeschlossen"* — dort gehören `failed`/`deferred` hinein, sonst zeigte die
    Kachel während der Wartephase „noch keine Läufe" — und *„blockiert etwas
    einen Neustart"*, wo sie falsch sind. **Die Liste wird nicht angeglichen;
    die Belegt-Prüfung bekommt eine eigene, engere.**
    """
    db = tmp_path / "jobs.sqlite"
    c = job_db.connect(db)
    try:
        _pin(c, f"lokal-{status}-9f8e7d6c", status=status)
    finally:
        c.close()
    assert worker_mod.local_run_blocked(f"lokal-{status}", db_path=db, host="testhost") is None, (
        f"ein {status}-Slot wartet auf einen Start und blockiert keinen")


@pytest.mark.parametrize("status", ["running", "awaiting", "starting"])
def test_ein_arbeitender_slot_blockiert_den_neustart(tmp_path: Path, status: str):
    """Die Gegenprobe: was wirklich läuft, blockiert weiterhin.

    `starting` steht mit in der engeren Liste, obwohl es in der alten gar nicht
    vorkam — ein Slot im Setup hat noch keinen Prozess, aber gleich einen.
    """
    db = tmp_path / "jobs.sqlite"
    c = job_db.connect(db)
    try:
        _pin(c, f"lokal-{status}-7d6c5b4a", status=status)
    finally:
        c.close()
    assert worker_mod.local_run_blocked(f"lokal-{status}", db_path=db, host="testhost") is not None


def test_die_anzeige_liste_bleibt_wie_sie_ist(tmp_path: Path):
    """`local_run_live()` ist die *Anzeige* und darf `failed` weiter führen.

    Ohne diesen Test wäre der naheliegende Fix, die eine Liste zu verengen —
    und damit die Kachel während jeder Wartephase auf „noch keine Läufe"
    zurückzuwerfen. **Der Fehler war nie die Liste, sondern dass eine Liste
    zwei Fragen beantwortet.**
    """
    db = tmp_path / "jobs.sqlite"
    c = job_db.connect(db)
    try:
        _pin(c, "lokal-anzeige-5b4a3c2b", status="failed")
    finally:
        c.close()
    assert worker_mod.local_run_live("lokal-anzeige", db_path=db, host="testhost") is not None


# ── #172: ein terminaler Slot wird archiviert, nicht überschrieben ──────────


@pytest.mark.parametrize("status", ["killed", "error", "zombie", "inactive"])
def test_start_archiviert_einen_terminalen_slot(tmp_path: Path, status: str):
    """*„Ein terminaler Status wird vor START archiviert, der Slot wird
    initialisiert. Das ist heute nicht so realisiert."* (m.rau)

    `slot.py` sagt für alle vier: *„beide archivieren erst (A2)"*. Auf dem
    Scheduler-Pfad passiert das; auf dem Client-Pfad legt `/-/run` eine **neue**
    Zeile an, und der alte Lauf verschwindet ersatzlos — er steht danach in
    keiner Journal-Zeile.
    """
    db = tmp_path / "jobs.sqlite"
    c = job_db.connect(db)
    try:
        _pin(c, f"alt-{status}-3c2b1a0f", status=status)
        c.execute("UPDATE jobs SET started_at=?, finished_at=? WHERE slug=?",
                  (time.time() - 30, time.time() - 25, f"alt-{status}-3c2b1a0f"))
        c.commit()

        worker_mod.archive_pinned_terminal(f"alt-{status}", db_path=db, host="testhost")

        zeilen = job_db.list_journal(c)
        assert any(j["status"] == status for j in zeilen), (
            f"der alte {status}-Lauf muss vor dem Start ins Journal")
    finally:
        c.close()


def test_start_archiviert_einen_laufenden_slot_nicht(tmp_path: Path):
    """Die Gegenprobe: nur **terminale** Zustände werden archiviert.

    Ein `running`-Slot ist nicht fertig; ihn wegzuräumen hieße, einen laufenden
    Prozess aus der Anzeige zu verlieren.
    """
    db = tmp_path / "jobs.sqlite"
    c = job_db.connect(db)
    try:
        _pin(c, "laeuft-1a0fbeef", status="running")
        worker_mod.archive_pinned_terminal("laeuft", db_path=db, host="testhost")
        assert job_db.list_journal(c) == []
    finally:
        c.close()


# ── #176: gleiche Umgebung in host und container ───────────────────────────


def test_der_host_lauf_sieht_keine_engine_interna():
    """*„Zu fragen wäre, ob umgekehrt auf dem Host alles aus dem Sub-Prozess-
    Environment herausgelöst wird, was auch auf dem Container nicht vorliegt."*

    Ein Host-Lauf erbt heute `os.environ.copy()` — nicht weil jemand mehr
    durchreicht, sondern weil dort nichts abgeschnitten wird.
    """
    from bibi.wrapper import exec_backend

    env = {
        "BIBI_EXEC_MODE": "host",
        "BIBI_WORKTREE": "/tmp/wt",
        "BIBI_RUN_ID": "r1",
        "BIBI_SILENCE_TIMEOUT": "60",
        "BIBI_JOB_ID": "j1",
        "GITEA_TOKEN": "geheim",
        "PATH": "/usr/bin",
    }
    spec = exec_backend.build_exec(["echo", "hi"], env)
    uebrig = {k for k in spec.env if k.startswith("BIBI_")}
    assert uebrig == {"BIBI_JOB_ID"}, (
        f"nur BIBI_JOB_ID bleibt (bibi.job.data_dir()), übrig war {sorted(uebrig)}")
    assert spec.env["GITEA_TOKEN"] == "geheim", "Credentials gehen weiter durch"
    assert spec.env["PATH"] == "/usr/bin"


def test_bibi_job_id_ueberlebt_in_beiden_modi():
    """Die Ausnahme, und sie ist kein historischer Rest.

    `bibi/job.py` liest sie, und `CONVENTIONS.md` schreibt den darauf gebauten
    Helfer als *den* sanktionierten Weg vor. Fiele sie, schriebe jeder Job, der
    der Konvention folgt, seine Daten nach `…/adhoc/` statt `…/<job_id>/` —
    ein stiller Datenpfad-Wechsel, der erst auffällt, wenn ein Zähler nicht
    mehr hochzählt.
    """
    from bibi.wrapper import exec_backend

    spec = exec_backend.build_exec(
        ["echo", "hi"], {"BIBI_EXEC_MODE": "host", "BIBI_JOB_ID": "j42"})
    assert spec.env.get("BIBI_JOB_ID") == "j42"


# ── #173: der Statuswechsel hängt nicht mehr am PID-Report ─────────────────


def test_running_auch_ohne_lokalen_scheduler_pfad(tmp_path: Path, monkeypatch):
    """*„Manchmal, nicht immer, wechselt der Job Status nicht, obwohl der Job
    definitiv läuft."* (m.rau, 2026-08-13)

    `running` entstand ausschließlich als Nebenwirkung von `report_pid()`. Ein
    Worker gegen einen **entfernten** Scheduler hat keinen lokalen DB-Pfad —
    dann wurde `report_pid()` nie aufgerufen, und es gab keinen zweiten Weg
    nach `running`. Der Slot stand bis zum Terminalzustand auf `starting`,
    während der Output wuchs.

    Der Rot-Schritt bildet genau diese Lage nach: ein Lauf ohne
    `_sched_db_path`, der durchläuft. Danach muss der Slot `running` gesehen
    haben — heute sieht er es nie.
    """
    from bibi.daemon import worker as W

    gemeldet: list[dict] = []

    class _FernScheduler:
        """Ein Scheduler ohne lokalen DB-Pfad — der Fall aus dem Befund."""
        db_path = None

        def report(self, job_id: str, **fields) -> str:
            gemeldet.append({"job_id": job_id, **fields})
            return "ok"

    monkeypatch.setattr(W, "_run_wrapper",
                        lambda **kw: (tmp_path / "output.jsonl", None))
    W.execute_reservation(
        {"id": "j1", "slug": "fern", "kind": "job", "payload": "echo hi",
         "attempt": 0, "attempts": 1},
        repo_root=tmp_path, work_dir=tmp_path, client=_FernScheduler(),
    )

    assert any(m.get("status") == "running" for m in gemeldet), (
        f"der Slot muss running gesehen haben, gemeldet wurde: {gemeldet}")


# ── #174: die Oberfläche sagt, dass es schiefging ──────────────────────────


def test_das_board_zeigt_einen_gescheiterten_klick():
    """*„Node blockieren. Funktioniert nicht. Ein Klick bleibt ohne Wirkung."*

    Die Route rief `node_action()` in einem nackten `try: … except: pass` und
    rendert danach das Board neu. **Jeder Fehler auf diesem Weg war damit
    unsichtbar** — 200, Board unverändert, und der Klick sah folgenlos aus,
    egal ob er an einem 403, einem Netzfehler oder einer Datenlage scheiterte.

    Dies ist die **erste** Hälfte des Fixes und die wichtigere: ohne sie ist
    die zweite (die Ursache) nicht nachweisbar.
    """
    from bibi.controller import render

    html = render.clients_fragment([], now=1.0, aktion_fehler="block failed: 403 Forbidden")
    assert "block failed: 403 Forbidden" in html
    assert "chip conflict" in html, "der Fehler muss als Störung markiert sein, nicht als Notiz"


def test_das_board_ohne_fehler_bleibt_ruhig():
    """Die Gegenprobe: ein geglückter Klick hinterlässt keine Meldung.

    Sonst stünde nach jeder Aktion ein leerer Kasten — und ein Element, das
    immer da ist, trägt keine Information mehr.
    """
    from bibi.controller import render

    html = render.clients_fragment([], now=1.0)
    assert "chip conflict" not in html
