"""run_pinned(): /run mit voller Scheduler-Lifecycle, gepinnt + sofort (PLAN-28)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi import repo
from bibi.daemon import job_db
from bibi.daemon.worker import run_pinned


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def gitrepo(tmp_path: Path, monkeypatch):
    root = tmp_path / "r"
    (root / "vault" / "case").mkdir(parents=True)
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname="t"\nversion="0"\n', encoding="utf-8")
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


def _seed(root: Path, rel: str, body: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"seed {rel}")


def _fake_run_wrapper(tmp_path):
    def fake(**kwargs):
        return tmp_path / "data" / "job" / "jid" / "output.jsonl", 999
    return fake


def test_run_pinned_with_cmd_creates_pinned_row_and_dispatches(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    # **Die Identitaet, nicht der Anzeigename** (m.rau/bibi#88). Hier stand
    # `== "mac"`, also der durchgereichte `host` — genau die Verwechslung, die
    # eigene /run-Laeufe unsichtbar machte, sobald der Rechner seinen Namen
    # wechselte. `host` bleibt der lesbare Name und steht weiterhin im FE und
    # in der Worker-Liste; der Schluessel, an dem die Zeile wiedergefunden
    # wird, ist jetzt stabil.
    from bibi.daemon.worker import pin_identity
    assert row["pinned_host"] == pin_identity()
    assert row["status"] == "running"  # sofort reserviert + dispatcht
    assert row["payload"] == "echo hi"
    # attempts=0 (nicht 1!) ist "kein Retry" — der Wrapper prüft attempt_cur
    # (0 bei einem frischen Job) < attempts_max; attempts=1 würde also einen
    # Retry auslösen, s. run_pinned()s Docstring. 0 matcht das historische
    # /run-Verhalten (ein Versuch, sofortiger Fehlschlag) und ist nötig, weil
    # die CLI (kein laufender Daemon) einen fälligen Retry nie bedienen könnte.
    # 1 statt 0 seit #168: `attempts` zaehlt Gesamtversuche, und der
    # CLI-Default heisst unveraendert "ein Lauf, kein Retry" — nur mit
    # der Zahl, die das auch bedeutet. Das Verhalten darueber (Exit 1,
    # "[error]", kein zweiter Lauf) ist unveraendert und steht oben.
    assert row["attempts"] == 1


def test_run_pinned_with_slug_resolves_existing_schedule(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    _seed(gitrepo, "myjob/myjob.md", '---\nschedule: never\njob: "echo from md"\n---\n')
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(slug="myjob", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    from bibi.daemon.worker import pin_identity
    assert row["payload"] == "echo from md"
    assert row["schedule_ref"] == "myjob/myjob.md"
    assert row["pinned_host"] == pin_identity()   # m.rau/bibi#88, s. oben


def test_run_pinned_unknown_slug_raises_lookup_error(gitrepo):
    with pytest.raises(LookupError):
        run_pinned(slug="nope", repo_root=gitrepo, host="mac")


def test_run_pinned_without_slug_or_cmd_raises_value_error(gitrepo):
    with pytest.raises(ValueError):
        run_pinned(repo_root=gitrepo, host="mac")


# ── PLAN-32 Stufe 32.3 — Verwaltung gepinnter Jobs (User-Fund: /run-Jobs sind
# der Scheduler-HTTP-API auf einem reinen Client-Knoten unerreichbar) ────────


def test_list_pinned_returns_only_this_hosts_rows(gitrepo, monkeypatch):
    """**Die Fremdzeile entsteht jetzt in der Datenbank, nicht per Aufruf.**

    Hier stand zweimal ``run_pinned()``, einmal mit ``host="other-host"`` — und
    das war schon vor m.rau/bibi#88 eine Fiktion: ``run_pinned()`` laeuft immer
    auf *diesem* Knoten, egal welchen Anzeigenamen man ihm mitgibt. Ein anderer
    Rechner ruft die Funktion in seinem eigenen Prozess auf; seine Zeile kommt
    hier nur ueber den Sync an.

    Seit `#88` faellt die Fiktion auf: beide Aufrufe schreiben dieselbe stabile
    Identitaet, weil beide derselbe Knoten sind. Die Zeile eines fremden Knotens
    wird deshalb direkt geschrieben — so, wie sie tatsaechlich entsteht.
    """
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res_mac = run_pinned(cmd="echo a", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    conn.execute(
        "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, status, "
        "enqueued_at, pinned_host) "
        "VALUES ('fremd1','fremd-11223344','r','job','echo b','pending',1.0,"
        "'sarasate-client')")
    conn.commit()
    rows = job_db.list_pinned(conn)
    conn.close()
    assert [r["id"] for r in rows] == [res_mac["id"]]


def test_list_pinned_empty_when_no_pinned_rows(gitrepo):
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    assert job_db.list_pinned(conn, "mac") == []
    conn.close()


def test_delete_pinned_job_removes_row(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(cmd="echo a", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    outcome = job_db.delete_pinned_job(conn, res["id"])
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    assert outcome == "ok"
    assert row is None


def test_delete_pinned_job_not_found(gitrepo):
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    outcome = job_db.delete_pinned_job(conn, "does-not-exist")
    conn.close()
    assert outcome == "not_found"


def test_delete_pinned_job_refuses_scheduler_owned_row(gitrepo):
    # Sicherheitsnetz: eine normale (nicht gepinnte) Zeile darf dieser Pfad
    # nicht loeschen -- das waere Datenverlust, den nur der reguläre
    # Reconcile-Pfad (inactive/rescan) verantworten darf.
    _seed(gitrepo, "myjob/myjob.md", '---\nschedule: never\njob: "echo hi"\n---\n')
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    job_db.rescan(conn, vault_root=gitrepo / "vault" / "case")
    row = conn.execute("SELECT id FROM jobs WHERE slug='myjob'").fetchone()
    outcome = job_db.delete_pinned_job(conn, row["id"])
    still_there = conn.execute("SELECT 1 FROM jobs WHERE id=?", (row["id"],)).fetchone()
    conn.close()
    assert outcome == "not_pinned"
    assert still_there is not None


def test_run_pinned_generates_unique_slug_per_call(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res1 = run_pinned(slug="adhoc", cmd="echo hi", repo_root=gitrepo, host="mac")
    res2 = run_pinned(slug="adhoc", cmd="echo hi", repo_root=gitrepo, host="mac")
    assert res1["id"] != res2["id"]
    assert res1["slug"] != res2["slug"]
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    conn.close()
    assert n == 2


def test_run_pinned_custom_attempts(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac", attempts=3)
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT attempts FROM jobs WHERE id=?", (res["id"],)).fetchone()
    conn.close()
    assert row["attempts"] == 3


# --- use_schedule_retry (Bugfix, User-Fund: "Runner 5" mit attempts: 2 in der
# Frontmatter exhaustierte über den START-Button trotzdem beim ersten
# Fehlschlag sofort zu error statt zweimal zu retryen — /-/run überging
# attempts/backoff/defer_time/error_time der Schedule-MD komplett) ----------


def test_run_pinned_without_use_schedule_retry_ignores_schedule_lifecycle(gitrepo, monkeypatch):
    # Default False: unveraendertes CLI-sicheres Verhalten (kein Retry, egal
    # was die Schedule-MD sagt) — genau das, was bibi-ctrl run braucht.
    import bibi.daemon.worker as W
    _seed(gitrepo, "retryjob/retryjob.md",
          '---\nschedule: never\njob: "exit 1"\nattempts: 2\nbackoff: exponential\n'
          'defer_time: 15\nerror_time: 10\n---\n')
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(slug="retryjob", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT attempts, backoff, defer_time, error_time FROM jobs WHERE id=?",
                       (res["id"],)).fetchone()
    conn.close()
    # 1 statt 0 seit #168: `attempts` zaehlt Gesamtversuche, und der
    # CLI-Default heisst unveraendert "ein Lauf, kein Retry" — nur mit
    # der Zahl, die das auch bedeutet. Das Verhalten darueber (Exit 1,
    # "[error]", kein zweiter Lauf) ist unveraendert und steht oben.
    assert row["attempts"] == 1
    assert row["backoff"] == "fixed"
    assert row["defer_time"] is None
    assert row["error_time"] is None


def test_run_pinned_with_use_schedule_retry_takes_over_schedule_lifecycle(gitrepo, monkeypatch):
    # True (gesetzt von der HTTP-Route /-/run, die einen laufenden Daemon mit
    # gepinntem Worker-Loop voraussetzt): attempts/backoff/defer_time/
    # error_time kommen jetzt aus der Schedule-MD, nicht mehr den No-Retry-
    # Defaults.
    import bibi.daemon.worker as W
    _seed(gitrepo, "retryjob/retryjob.md",
          '---\nschedule: never\njob: "exit 1"\nattempts: 2\nbackoff: exponential\n'
          'defer_time: 15\nerror_time: 10\n---\n')
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(slug="retryjob", repo_root=gitrepo, host="mac", use_schedule_retry=True)
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT attempts, backoff, defer_time, error_time FROM jobs WHERE id=?",
                       (res["id"],)).fetchone()
    conn.close()
    assert row["attempts"] == 2
    assert row["backoff"] == "exponential"
    assert row["defer_time"] == 15
    assert row["error_time"] == 10


def test_run_pinned_use_schedule_retry_by_cmd_stays_no_retry(gitrepo, monkeypatch):
    # cmd=-Ad-hoc-Laeufe haben keine ScheduleSpec, aus der sich etwas
    # uebernehmen liesse — use_schedule_retry=True darf dafuer nicht crashen
    # und muss beim sicheren No-Retry-Default bleiben.
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    res = run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac", use_schedule_retry=True)
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute("SELECT attempts, backoff, defer_time, error_time FROM jobs WHERE id=?",
                       (res["id"],)).fetchone()
    conn.close()
    # 1 statt 0 seit #168: `attempts` zaehlt Gesamtversuche, und der
    # CLI-Default heisst unveraendert "ein Lauf, kein Retry" — nur mit
    # der Zahl, die das auch bedeutet. Das Verhalten darueber (Exit 1,
    # "[error]", kein zweiter Lauf) ist unveraendert und steht oben.
    assert row["attempts"] == 1
    assert row["backoff"] == "fixed"
    assert row["defer_time"] is None
    assert row["error_time"] is None


def test_run_pinned_other_host_cannot_reserve_it(gitrepo, monkeypatch):
    # Die Pin-Garantie gilt sofort, nicht erst beim nächsten Sweep/Loop-Tick.
    import bibi.daemon.worker as W
    monkeypatch.setattr(W, "_run_wrapper", _fake_run_wrapper(gitrepo))
    run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    assert job_db.reserve_next(conn, host="sarasate", pinned_only=True) is None
    assert job_db.reserve_next(conn, host="sarasate") is None  # auch nicht im Team-Pfad
    conn.close()


# ── app_port/app_prefix/exec_mode/image-Passthrough (migriert aus dem mit ────
# run_local() entfernten tests/test_run_local_app_fields.py, PLAN-28 Refactor D
# — der Bug (Fund 2026-07-10 HITL-Test-App-Migration / PLAN-24 Befund 1) galt
# run_local()s eigener Resolution-Logik; run_pinned() geht stattdessen über
# execute_reservation()s reservation.get(...)-Pfad, der das schon immer korrekt
# weiterreicht — dieser Test deckt also die ganze Kette INSERT→reserve_next()→
# execute_reservation()→_run_wrapper() ab, nicht nur die Slug-Auflösung.


def _capturing_run_wrapper(tmp_path: Path, captured: dict):
    def fake(**kwargs):
        captured.update(kwargs)
        return tmp_path / "data" / "job" / "jid" / "output.jsonl", 999
    return fake


def test_run_pinned_passes_app_port_and_exec_mode_to_wrapper(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    _seed(gitrepo, "myapp/myapp.md",
          '---\nschedule: "never"\njob: "python3 myapp.py"\napp_port: 9100\n'
          'app_prefix: /myapp\nexec_mode: host\n---\n# myapp\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="myapp", repo_root=gitrepo, host="mac")
    assert captured["app_port"] == 9100
    assert captured["app_prefix"] == "/myapp"
    assert captured["exec_mode"] == "host"


def test_run_pinned_passes_schedule_image_override_to_wrapper(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    _seed(gitrepo, "customimg/customimg.md",
          '---\nschedule: "never"\njob: "python3 customimg.py"\n'
          'image: "registry.local/custom:7"\n---\n# customimg\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="customimg", repo_root=gitrepo, host="mac")
    assert captured["image"] == "registry.local/custom:7"


def test_run_pinned_plain_job_passes_none_for_app_fields(gitrepo, monkeypatch):
    # Ein normaler (Nicht-App-)Job hat keine app_port/exec_mode-Frontmatter —
    # die Felder müssen dann sauber None bleiben, nicht z. B. 0/"" (was
    # _run_wrapper()/exec_backend.build_exec() als "gesetzt" missverstehen
    # könnte).
    import bibi.daemon.worker as W
    _seed(gitrepo, "plainjob/plainjob.md", '---\nschedule: "never"\njob: "echo hi"\n---\n# plain\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="plainjob", repo_root=gitrepo, host="mac")
    assert captured["app_port"] is None
    assert captured["app_prefix"] is None
    assert captured["exec_mode"] is None
    assert captured["image"] is None


def test_run_pinned_by_cmd_has_no_app_fields(gitrepo, monkeypatch):
    # Ad-hoc-Kommando (kein Slug/MD) — es gibt kein Frontmatter, aus dem
    # app_port/exec_mode kommen könnten; muss weiterhin funktionieren.
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    assert captured["app_port"] is None
    assert captured["app_prefix"] is None
    assert captured["exec_mode"] is None


# ── silence_timeout/wall_time-Passthrough (Bug gefunden 2026-07-14, User-Fund:
# "warum zeigt die Attribute-Seite bei gepinnten Läufen andere Timeouts als
# beim Scheduler-Job?") — run_pinned() las s.silence_timeout/s.wall_time bisher
# nie in die INSERT-Spaltenliste, ein gepinnter Lauf bekam dadurch den SQL-
# Spalten-Default (3600s, nur für claude-Payloads richtig) statt des vom
# Parser für diesen Job berechneten Werts (z. B. 48h für Job/App-Payloads) —
# und jeden expliziten wall_time-Override aus der MD nie. ─────────────────────

def test_run_pinned_slug_uses_parser_silence_timeout_default(gitrepo, monkeypatch):
    # Kein explizites silence_timeout in der MD, einfacher Job (kein claude:,
    # kein app_port) — der Parser-Default dafür ist seit PLAN-31 Befund 4
    # DEFAULT_SILENCE_TIMEOUT_JOB (2h), nicht mehr der App-Default (48h) und
    # nicht der SQL-Spalten-Default (3600s/1h, der nur für claude-Payloads passt).
    # wall_time ist seit der zweiten Bibi4-Iteration (User-Fund: "wall_time
    # Default muss doch None sein") ein None-Sentinel wie defer_time/error_time
    # — kein ungewollter Default mehr, reines Opt-in in der MD.
    from bibi.schedule.models import DEFAULT_SILENCE_TIMEOUT_JOB
    import bibi.daemon.worker as W
    _seed(gitrepo, "plainjob/plainjob.md", '---\nschedule: "never"\njob: "echo hi"\n---\n# plain\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="plainjob", repo_root=gitrepo, host="mac")
    assert captured["silence_timeout"] == DEFAULT_SILENCE_TIMEOUT_JOB
    assert captured["wall_time"] is None


def test_run_pinned_slug_uses_app_silence_timeout_default_when_app_port_set(gitrepo, monkeypatch):
    # PLAN-31 Befund 4: ein Schedule mit app_port ist eine echte App und
    # behält den langen 48h-Default, im Unterschied zum einfachen Job oben.
    from bibi.schedule.models import DEFAULT_SILENCE_TIMEOUT_APP
    import bibi.daemon.worker as W
    _seed(gitrepo, "appjob/appjob.md",
          '---\nschedule: "never"\njob: "echo hi"\napp_port: 9100\n---\n# app\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="appjob", repo_root=gitrepo, host="mac")
    assert captured["silence_timeout"] == DEFAULT_SILENCE_TIMEOUT_APP


def test_run_pinned_slug_passes_explicit_silence_timeout_and_wall_time(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    _seed(gitrepo, "withlimits/withlimits.md",
          '---\nschedule: "never"\njob: "echo hi"\nsilence_timeout: 300\nwall_time: 120\n'
          '---\n# withlimits\n')
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(slug="withlimits", repo_root=gitrepo, host="mac")
    assert captured["silence_timeout"] == 300
    assert captured["wall_time"] == 120


def test_run_pinned_by_cmd_uses_job_type_silence_timeout_default(gitrepo, monkeypatch):
    # Ad-hoc-Kommando (kein Slug/MD, kein app_port möglich) — kein claude:-
    # Präfix ⇒ seit PLAN-31 Befund 4 der Job-Default (2h), nicht mehr der
    # App-Default (48h) und nicht der SQL-Spalten-Default.
    from bibi.schedule.models import DEFAULT_SILENCE_TIMEOUT_JOB
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    assert captured["silence_timeout"] == DEFAULT_SILENCE_TIMEOUT_JOB
    assert captured["wall_time"] is None


def test_run_pinned_by_cmd_claude_prefix_uses_claude_silence_timeout_default(gitrepo, monkeypatch):
    from bibi.schedule.models import DEFAULT_SILENCE_TIMEOUT
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(cmd="claude: hallo", repo_root=gitrepo, host="mac")
    assert captured["silence_timeout"] == DEFAULT_SILENCE_TIMEOUT
    assert captured["image"] is None


# ── in_place (User-Fund 2026-07-14, bibi-ctrl test): kein frischer Worktree, ─
# nie ein Commit — s. bibi/ctrl/test_cmd.py. Diese beiden Tests decken nur
# run_pinned()s eigene Verantwortung ab (korrekte Weitergabe von in_place/
# ephemeral bis zu _run_wrapper() — _run_wrapper() selbst ist hier komplett
# gemockt, ruft also so oder so nie worktree.prepare() auf). Der eigentliche
# Beweis "worktree.prepare() wird übersprungen, keine agent/<slug>-Branch
# entsteht" läuft mit echtem _run_wrapper() in tests/test_worker.py.

def test_run_pinned_in_place_forces_ephemeral_false(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac", in_place=True)
    assert captured["in_place"] is True
    # Kein separater Worktree existiert bei in_place — ephemeral=True würde
    # sonst versuchen, ihn aufzuräumen (s. worktree.remove()s rm-rf-Risiko,
    # Kommentar in worker.py::run_pinned()).
    assert captured["ephemeral"] is False


def test_run_pinned_default_is_not_in_place(gitrepo, monkeypatch):
    import bibi.daemon.worker as W
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))
    run_pinned(cmd="echo hi", repo_root=gitrepo, host="mac")
    assert captured["in_place"] is False
    assert captured["ephemeral"] is True


# ── #199: ein wartender Alt-Slot blockiert den Job dauerhaft ────────────────
#
# **Befund m.rau, 2026-08-14:** *„aktuell gehen Client Jobs gar nicht zu
# starten. Es kommt sofort ein Fehler!"*
#
# **Zwei Aenderungen derselben Klammer greifen ineinander.** `#191` laesst
# `run_pinned()` einen wartenden Slot **fortsetzen**, statt eine zweite Zeile
# danebenzulegen — richtig so, und der Grund steht im Docstring von
# `resume_pinned_waiting()`. `#168` hat davor die Bedeutung von `attempts`
# umgestellt: `reserve_next()` filtert seither `attempts > 0`, weil `0` jetzt
# *kein Lauf* heisst.
#
# Trifft das Fortsetzen auf eine Zeile aus der Zeit **vor** `#168`, traegt sie
# `attempts=0` in der alten Bedeutung (*ein Lauf, kein Retry*) — und wird
# genommen und sofort wieder verworfen. **Der Job ist damit dauerhaft
# blockiert:** nicht ein Start scheitert, sondern jeder, immer an derselben
# Zeile. Auf dem Mac waren es sieben Jobs, der aelteste Slot vom 05.08.
#
# **Die Fehlerform ist die dieser Klammer:** zwei Stellen entscheiden ueber
# dieselbe Frage — *darf diese Zeile laufen* —, und nur eine von beiden weiss
# von der neuen Regel.


def _wartender_alt_slot(root: Path, slug: str, *, attempts: int) -> str:
    """Eine gepinnte ``pending``-Zeile, wie sie ein frueherer Lauf hinterliess."""
    import secrets

    from bibi.daemon.worker import pin_identity
    jid = secrets.token_hex(4)
    conn = job_db.connect(root / "data" / "jobs.sqlite")
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, job_uid, schedule_ref, kind, payload, "
            "status, enqueued_at, next_fire_at, attempts, pinned_host, active) "
            "VALUES (?,?,?,?,'job','echo hi','pending',1.0,1.0,?,?,1)",
            (jid, f"{slug}-deadbeef", slug, f"{slug}/{slug}.md", attempts,
             pin_identity()))
        conn.commit()
    finally:
        conn.close()
    return jid


def test_ein_alt_slot_mit_attempts_null_blockiert_den_start_nicht(gitrepo, monkeypatch):
    """**Der Rot-Schritt zu #199.**

    Heute wirft der Aufruf ``RuntimeError: gepinnter Job 'x-deadbeef' konnte
    nicht reserviert werden`` — ``resume_pinned_waiting()`` greift die Zeile
    auf, ``reserve_next()`` verwirft sie, und niemand legt eine an, die laufen
    koennte.
    """
    import bibi.daemon.worker as W
    _seed(gitrepo, "altslot/altslot.md",
          '---\nschedule: "never"\njob: "echo hi"\n---\n# altslot\n')
    _wartender_alt_slot(gitrepo, "altslot", attempts=0)
    captured: dict = {}
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, captured))

    res = run_pinned(slug="altslot", repo_root=gitrepo, host="mac",
                     use_schedule_retry=True)

    # Der Lauf kommt zustande — und zwar auf einer **neuen** Zeile, nicht auf
    # der blockierten: der Slug traegt ein frisches Token statt `-deadbeef`.
    assert res["slug"] != "altslot-deadbeef"
    assert captured["attempts"] >= 1, "der Lauf haette kein Versuchsbudget"


def test_ein_wartender_slot_wird_weiterhin_fortgesetzt(gitrepo, monkeypatch):
    """**Die Gegenprobe, und sie schuetzt die Zusage von `#191`.**

    Der billige Fix waere, das Fortsetzen ganz aufzugeben — dann liefe der
    Fehler oben weg und `#191` mit ihm: *„ein lokaler Job erreicht ``error``
    nie, solange ein Mensch ihn per START wiederholt"*. Ein wartender Slot mit
    brauchbarem Budget muss weiterhin **dieselbe** Zeile fortsetzen.
    """
    import bibi.daemon.worker as W
    _seed(gitrepo, "weiterslot/weiterslot.md",
          '---\nschedule: "never"\njob: "echo hi"\nattempts: 3\n---\n# weiterslot\n')
    jid = _wartender_alt_slot(gitrepo, "weiterslot", attempts=3)
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, {}))

    res = run_pinned(slug="weiterslot", repo_root=gitrepo, host="mac",
                     use_schedule_retry=True)

    assert res["id"] == jid, "der wartende Slot wurde verdoppelt statt fortgesetzt"


def test_der_unreservierbare_alt_slot_bleibt_nicht_ewig_liegen(gitrepo, monkeypatch):
    """Ein Slot, den niemand holen kann, ist kein wartender Slot — er ist
    Schrott, und solange er in der Tabelle steht, sieht ein Mensch ihn als
    ausstehende Arbeit. Sieben davon lagen auf dem Mac."""
    import bibi.daemon.worker as W
    _seed(gitrepo, "schrott/schrott.md",
          '---\nschedule: "never"\njob: "echo hi"\n---\n# schrott\n')
    alt = _wartender_alt_slot(gitrepo, "schrott", attempts=0)
    monkeypatch.setattr(W, "_run_wrapper", _capturing_run_wrapper(gitrepo, {}))

    run_pinned(slug="schrott", repo_root=gitrepo, host="mac",
               use_schedule_retry=True)

    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        zeile = conn.execute("SELECT status FROM jobs WHERE id=?", (alt,)).fetchone()
    finally:
        conn.close()
    assert zeile is None or zeile["status"] != "pending", (
        "die unreservierbare Zeile steht weiterhin als wartend in der Tabelle")


# ── #210: dieselbe Zeile, aber ohne dass jemand startet ─────────────────────
#
# **Die Stilllegung aus `#199` haengt am Start.** Sie sitzt in
# `resume_pinned_waiting()`, wird also nur durchlaufen, wenn jemand den Job
# startet — und wegen `LIMIT 1` in `_pinned_row()` je Start genau einmal. Ein
# Job, den niemand mehr startet, behaelt seine toten Slots fuer immer.
#
# **Gemessen am 2026-08-15 auf dem Mac:** acht `pending`-Zeilen mit
# `attempts=0`, die aelteste vom 31. Juli. Drei davon sind die
# `burndown-app`-Zeilen, die in `#209` die Kachel verzogen haben — dort ist die
# **Anzeige** behoben, hier der **Bestand**.
#
# **Warum der Test die Zeile setzt statt sie herstellen zu lassen:** den
# Erzeugungsweg gibt es nicht mehr. Diese Zeilen stammen aus der Zeit vor
# `#168`, als `attempts=0` noch *ein Lauf ohne Retry* hiess. Die Probe gegen die
# echte Quelle ist deshalb der gemessene Bestand und kein Ablauf — genau die
# Unterscheidung, die `Iterationen.md` mit *„ein Fixture ist eine Behauptung
# ueber die Wirklichkeit"* meint.


def _slot_status(root: Path, jid: str) -> tuple[str, str | None]:
    conn = job_db.connect(root / "data" / "jobs.sqlite")
    try:
        r = conn.execute("SELECT status, reason FROM jobs WHERE id=?", (jid,)).fetchone()
        return r["status"], r["reason"]
    finally:
        conn.close()


def test_sweep_raeumt_einen_toten_slot_ohne_dass_jemand_startet(gitrepo):
    """**Der Rot-Schritt zu #210.**

    Heute ueberlebt die Zeile jeden Sweep: `sweep()` kennt nur `failed` ohne
    `next_fire_at` und abgelaufene `deferred`. Eine `pending`-Zeile, die
    `ist_dispatchbar()` nicht besteht, kommt in keinem der beiden Zweige vor.
    """
    _seed(gitrepo, "toterslot/toterslot.md",
          '---\nschedule: "never"\njob: "echo hi"\n---\n# toterslot\n')
    jid = _wartender_alt_slot(gitrepo, "toterslot", attempts=0)

    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        job_db.sweep(conn)
    finally:
        conn.close()

    status, grund = _slot_status(gitrepo, jid)
    assert status == "inactive", f"der tote Slot steht auf {status!r}"
    assert grund and "dispatchbar" in grund, f"ohne Grund im Klartext: {grund!r}"


def test_sweep_laesst_einen_wartenden_slot_in_ruhe(gitrepo):
    """**Die Gegenprobe, und sie ist die wichtigere von beiden.**

    Ein Sweeper, der wartende Slots abraeumt, waere schlimmer als der Bestand,
    den er behebt: er nimmt einem Job seinen naechsten Versuch weg. Eine
    `pending`-Zeile mit Budget muss den Sweep unveraendert ueberleben.
    """
    _seed(gitrepo, "warteslot/warteslot.md",
          '---\nschedule: "never"\njob: "echo hi"\nattempts: 3\n---\n# warteslot\n')
    jid = _wartender_alt_slot(gitrepo, "warteslot", attempts=3)

    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        job_db.sweep(conn)
    finally:
        conn.close()

    assert _slot_status(gitrepo, jid)[0] == "pending"


def test_sweep_ruehrt_einen_abgeschalteten_job_nicht_an(gitrepo):
    """**Die Grenze des Zuschnitts, als Test statt als Absichtserklaerung.**

    `ist_dispatchbar()` prueft drei Bedingungen, und sie sind verschieden
    dauerhaft: `attempts=0` steht in der MD, `active=0` und `conflict_refs`
    nimmt ein Rescan zurueck. Der Sweeper nimmt deshalb **nur** den
    `attempts`-Fall — eine stillgelegte Zeile kommt nicht von selbst zurueck,
    ein abgeschalteter Job dagegen schon.

    Ohne diese Pruefung waere ein Fix gruen, der `ist_dispatchbar()` als Ganzes
    uebernimmt — und der raeumte beim naechsten Sweep jede Zeile eines
    voruebergehend abgeschalteten Jobs ab.
    """
    _seed(gitrepo, "auszeit/auszeit.md",
          '---\nschedule: "never"\njob: "echo hi"\nattempts: 3\n---\n# auszeit\n')
    jid = _wartender_alt_slot(gitrepo, "auszeit", attempts=3)
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    try:
        conn.execute("UPDATE jobs SET active=0 WHERE id=?", (jid,))
        conn.commit()
        job_db.sweep(conn)
    finally:
        conn.close()

    assert _slot_status(gitrepo, jid)[0] == "pending"
