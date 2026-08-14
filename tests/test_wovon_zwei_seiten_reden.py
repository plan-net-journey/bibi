"""Wovon zwei Seiten verschieden reden (`v0.8.14`) — die letzte Runde der
neunten Klammer.

Drei der vier Posten sind derselbe Fehler an drei Stellen: zwei Seiten
desselben Systems reden verschieden über dieselbe Sache, und keine merkt es.
Die Client-Seite heißt an drei Orten anders (`#193`), die Kachel liest Felder,
die ihre Quelle nicht führt (`#194`), der Client zählt Versuche anders als der
Scheduler (`#191`). In allen drei Fällen war ein Test grün.

**Die Bauart der Tests hier folgt der Lehre der Klammer**, und die ist teurer
bezahlt als die von `v0.8.13`: dort prüften Tests das falsche *Verhalten*, hier
prüften sie das richtige Verhalten an *erfundenen Daten*. `src="local"` gibt es
im Betrieb nicht, `run_id` und `attempts` stehen nicht in der Slot-Zeile —
sieben grüne Tests maßen eine Datenlage, die keine Zeile dieses Systems je hat.

Deshalb gilt hier: **wo ein Test eine Datenstruktur des Systems nachbaut,
steht eine Probe gegen die echte Quelle daneben.** Nicht für jeden Fall, aber
einmal je Struktur.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bibi.controller import jobs_view, render

NOW = 1_000_000.0


# ── #193: drei Namen für eine Seite ────────────────────────────────────────


def test_die_herkunftskuerzel_sind_die_der_echten_quelle():
    """**Die Probe gegen die Wirklichkeit, und sie steht bewusst zuerst.**

    Der Test, der `#193` durchgelassen hat, arbeitete mit ``src="local"``.
    Diesen Wert gibt es nicht: ``job_kacheln()`` vergibt ``S`` und ``C``, und
    zwar aus ``SLOT_QUELLEN``. Wer das nicht nachsieht, prüft seine eigene
    Annahme — und genau die war der Fehler.
    """
    kuerzel = {kurz for _, kurz, _ in jobs_view.SLOT_QUELLEN}
    assert kuerzel == {"S", "C"}, (
        f"die Zeilen tragen {sorted(kuerzel)} als Herkunft — jeder Test, der "
        f"etwas anderes einsetzt, prüft eine Datenlage, die es nicht gibt (#193)")
    assert jobs_view.SRC_ZU_ZIEL["C"] == "client"
    assert jobs_view.SRC_ZU_ZIEL["S"] == "scheduler"


def test_der_attrs_link_nennt_ein_ziel_das_die_route_kennt():
    """Der konkrete Fehler aus dem `v0.8.13`-Durchgang.

    ``_run_zeile()`` schrieb den rohen ``src`` in die Adresse, und der trägt
    ``C``. Gerendert wurde ``/slot/C/j7/attrs``; die Route kennt ``C`` nicht
    und antwortet mit 404 — an einem Link, den `#182` gerade erst gebaut hat.
    """
    zeile = render._run_zeile(
        {"id": None, "job_id": "j7", "src": "C", "in_slot": True,
         "status": "running", "sort_at": NOW - 60},
        basis="/-/jobs/abc123")

    assert "/slot/client/j7/attrs" in zeile, (
        f"der attrs-Link nennt eine Quelle, die die Route nicht kennt — "
        f"gerendert wurde: {re.search(r'/slot/[^\"]+', zeile)} (#193)")


def test_der_attrs_link_der_scheduler_seite_ebenso():
    """Die Gegenprobe auf der anderen Seite.

    Ohne sie wäre ein Fix grün, der schlicht ``client`` einsetzt — und der
    schickte jeden Scheduler-Lauf an die lokale DB, wo dieselbe Job-ID einen
    anderen Job meint (Zustandsmodell §1).
    """
    zeile = render._run_zeile(
        {"id": None, "job_id": "j7", "src": "S", "in_slot": True,
         "status": "running", "sort_at": NOW - 60},
        basis="/-/jobs/abc123")
    assert "/slot/scheduler/j7/attrs" in zeile, zeile


def test_kein_gerenderter_slot_weg_nennt_ein_unbekanntes_ziel():
    """**Der Wächter, und der eigentliche Posten.**

    Dieselbe Bauart wie der `#192`-Wächter, und aus demselben Grund: eine
    Prüfung, die über die *erlaubten* Ziele iteriert, kann eine Adresse nicht
    sehen, die ein **nicht** erlaubtes Ziel nennt. Sie muss vom gerenderten
    Markup zur Tabelle gehen, nicht umgekehrt.

    `#193` ist die zweite Runde in Folge mit dieser Fehlerform. Beim zweiten
    Mal ist das ein Muster und keine Wiederholung — deshalb ein Wächter und
    nicht nur eine Reparatur.
    """
    markup = "".join(
        render._run_zeile(
            {"id": None, "job_id": "j7", "src": kurz, "in_slot": True,
             "status": "running", "sort_at": NOW - 60},
            basis="/-/jobs/abc123")
        for _, kurz, _ in jobs_view.SLOT_QUELLEN)

    gesehen = set(re.findall(r"/slot/([^/\"]+)/", markup))
    assert gesehen, "keine einzige Slot-Adresse gefunden — der Test misst nichts"
    ausserhalb = gesehen - jobs_view.SLOT_ZIELE
    assert not ausserhalb, (
        f"{sorted(ausserhalb)} stehen als Ziel im Markup, werden von den "
        f"Slot-Routen aber nicht angenommen — der Link verspricht eine Seite, "
        f"die es unter diesem Namen nicht gibt (#193)")


def test_das_javascript_uebersetzt_nicht_mehr_selbst():
    """*„nicht zweimal, einmal in Python und einmal in JS"* — der Fix-Punkt 1
    aus dem Ticket.

    Die Übersetzung ``S``/``C`` stand im Browser (``const SEITE = {…}``) und
    ist damit für den serverseitig gebauten Link nicht erreichbar gewesen. Sie
    gehört auf die Seite, die **beide** Wege sieht: der Server rendert das
    fertige Ziel ins Markup, das Skript liest es.

    **Der Test prüft die Abwesenheit einer Tabelle, und das ist ungewöhnlich
    genug für eine Begründung:** solange sie dasteht, ist die zweite Meinung
    nur schlafend, nicht weg. Sie wäre der nächste Ort, an dem ein dritter Name
    entsteht.

    **Der Kommentar über dem Block sagt seit `#131`, was gelten soll** —
    *„Welcher Weg gilt, entscheidet der Server beim Rendern und legt es in die
    Zeile — der Browser raet nicht."* Er beschrieb den Output-Weg richtig und
    die Herkunft daneben falsch: die stand als Tabelle drei Zeilen tiefer.
    """
    assert "SEITE" not in render._JOB_DETAIL_JS, (
        "das JavaScript übersetzt die Herkunft weiterhin selbst — zwei "
        "Tabellen für dieselbe Sache sind der Zustand, aus dem #193 entstand")


# ── #194: die Kachel verspricht, was ihre Quelle nicht führt ───────────────


def _zeilen_beider_quellen(tmp_path) -> dict[str, dict]:
    """Die **zwei** Zeilenbauarten, die eine Kachel speisen können.

    **Das Ticket nennt eine Quelle, und beim Nachsehen waren es zwei** — das
    ist der Grund, warum diese Funktion existiert und nicht ein Fixture:

    * die **CLIENT**-Kachel bekommt die rohe DB-Zeile (``SELECT * FROM jobs``,
      ``controller/__init__.py``) und trägt damit **jede** Spalte;
    * die **SCHEDULER**-Kachel bekommt ``schedule_view()`` über ``/-/schedule``
      — eine bewusst schlankere Sicht.

    Der `#194`-Befund beschrieb ``job_view()`` als Ursache. Die speist die
    Kachel gar nicht. Wer nur eine der beiden probt, hält die Hälfte des
    Fehlers für behoben.
    """
    from bibi.daemon import job_db
    from bibi.schedule import parser

    conn = job_db.connect(tmp_path / "jobs.sqlite")
    try:
        pr = parser.parse_text(
            "---\nslug: EngineCI\nschedule: \"0 * * * *\"\nattempts: 3\n"
            "job: pytest -q\n---\n",
            schedule_ref="case/x/EngineCI.md", path=Path("case/x/EngineCI.md"))
        assert pr.is_ok, pr.error
        jid = job_db.upsert_schedule(conn, pr, 1000.0)
        roh = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return {
            "CLIENT": dict(roh),
            "SCHEDULER": job_db.schedule_view(roh, last_run=None, runtime_p90=None),
        }
    finally:
        conn.close()


def _belegt(zeile: dict) -> dict:
    """Jeden Schlüssel der echten Zeile mit einem Wert füllen.

    **Damit trennt der Wächter „der Schlüssel fehlt" von „der Wert ist gerade
    leer".** Nur das erste ist ein Befund: ein `exit_code`, der an einem
    laufenden Lauf ``None`` ist, ist richtig so; ein `run_id`, den keine Zeile
    je trägt, ist ein Versprechen ohne Deckung.

    Die Schlüsselmenge stammt aus der echten Zeile und wird **nicht** ergänzt —
    sonst prüfte der Wächter wieder eine erfundene Datenlage.
    """
    zahlen = {"attempt", "attempts", "fire", "exit_code", "priority",
              "app_port", "runtime_p90", "wall_time", "silence_timeout"}
    aus = {}
    for k, v in zeile.items():
        if v is not None:
            aus[k] = v
        elif k.endswith("_at") or k in zahlen:
            aus[k] = 1
        else:
            aus[k] = "x"
    return aus


def test_jedes_feld_jedes_sets_ist_an_beiden_quellen_gedeckt(tmp_path):
    """**Der Wächter, und die erste Anwendung der Regel dieser Klammer.**

    Ein Set, das ein Feld nennt, das seine Quelle nicht liefern kann, ist ein
    Versprechen ohne Deckung. Es fällt nicht auf, weil `_kv_*` korrekt ``None``
    zurückgibt — *„jede Angabe nur, wenn es sie gibt"* greift genau richtig und
    macht den Fehler dabei unsichtbar.

    Geprüft wird deshalb nicht die Darstellung, sondern die **Deckung**, und
    zwar über den echten Renderer an einer echten Zeile. Ein Register, das
    danebenstünde und die Bedarfe deklarierte, wäre wieder eine zweite Meinung
    — genau die Bauart, gegen die diese ganze Runde antritt.
    """
    quellen = _zeilen_beider_quellen(tmp_path)
    fehlend: dict[str, list[str]] = {}
    for quelle, zeile in quellen.items():
        voll = _belegt(zeile)
        for name, felder in render.KACHEL_SETS.items():
            for feld in felder:
                if render.KACHEL_VORRAT[feld](voll, NOW) is None:
                    fehlend.setdefault(f"{quelle}/{name}", []).append(feld)

    assert not fehlend, (
        f"{fehlend} stehen in einem Set, aber ihre Quelle trägt sie nicht — "
        f"die Kachel verspricht eine Angabe, die sie dort nie zeigen kann "
        f"(#194)")


def test_die_kachel_zeigt_die_run_id_an_beiden_quellen(tmp_path):
    """`run_id` steht in **keiner** Spalte — er ist aus ``slug``/``id``/``fire``
    berechenbar, und das stand so schon in `#181`. Gelesen wurde er trotzdem
    wie ein vorhandenes Feld, auf beiden Seiten.
    """
    for quelle, zeile in _zeilen_beider_quellen(tmp_path).items():
        kachel = jobs_view.Tile(quelle=quelle, host="mac", slot=zeile,
                                status="running", aktionen=frozenset())
        html = render.kachel_set(kachel, render.KACHEL_SETS["zuordnung"],
                                 now=NOW)
        assert "EngineCI" in html, (
            f"das Zuordnungs-Set der {quelle}-Kachel zeigt keine run_id — "
            f"vier versprochene Felder, drei gelieferte: {html!r} (#194)")


def test_die_scheduler_kachel_zeigt_try_n_von_m(tmp_path):
    """**Die Sicht, die die Scheduler-Kachel liest, führt `attempts` nicht** —
    und das ist wörtlich derselbe Fall wie `#150`.

    Der Kommentar an ``schedule_view()`` sagt es über `wall_time`: *„Die
    Spalte steht in der Tabelle, `job_full_view()` gab sie längst aus — nur die
    schlankere Sicht, die der Jobs-Screen liest, führte sie nicht."* Für
    `attempt`/`attempts` galt das weiter, eine Runde und fünfundvierzig Tickets
    später.
    """
    zeile = _zeilen_beider_quellen(tmp_path)["SCHEDULER"]
    zeile = {**zeile, "attempt": 1}
    kachel = jobs_view.Tile(quelle="SCHEDULER", host="sarasate", slot=zeile,
                            status="deferred", aktionen=frozenset())
    html = render.kachel_set(kachel, render.KACHEL_SETS["fehlschlag"], now=NOW)

    assert "1/3" in html, (
        f"das Fehlschlag-Set der Scheduler-Kachel zeigt kein try n/m — "
        f"{html!r} (#194)")


# ── #191: der Client zählt anders als der Scheduler ────────────────────────


def _git(cwd: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def gitrepo(tmp_path: Path, monkeypatch):
    """Dasselbe Repo-Gerüst wie in `test_run_pinned.py` — `run_pinned()` legt
    einen Worktree an und braucht dafür ein echtes git."""
    from bibi import repo

    root = tmp_path / "r"
    (root / "vault" / "case" / "retry").mkdir(parents=True)
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname="t"\nversion="0"\n',
                                         encoding="utf-8")
    (root / "vault" / "case" / "retry" / "retry.md").write_text(
        '---\nschedule: never\nattempts: 2\njob: "exit 1"\n---\n', encoding="utf-8")
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


def _fake_wrapper(root: Path):
    def fake(**kwargs):
        return root / "data" / "job" / "jid" / "output.jsonl", 999
    return fake


def test_start_auf_einem_wartenden_lokalen_slot_setzt_ihn_fort(gitrepo, monkeypatch):
    """**Der Rot-Schritt aus dem Ticket**, an der Wirkung gemessen.

    Ein lokaler Slot auf `failed`, dann START. Der Scheduler-Pfad schreibt
    dieselbe Zeile fort (`start_now()`: *„failed bewusst ohne Attempts-Reset,
    nur der Timer wird übersprungen"*); der Client-Pfad legte eine **neue** an
    und begann bei `attempt=0`.

    Gemessen wird die Zeilenzahl im **Bucket**, nicht der Rückgabewert: zwei
    Zeilen für denselben Job sind der sichtbare Teil des Fehlers, und die
    ältere fasst danach nie wieder jemand an.
    """
    from bibi.daemon import job_db, worker as W
    from bibi.schedule.models import job_uid

    monkeypatch.setattr(W, "_run_wrapper", _fake_wrapper(gitrepo))
    db = gitrepo / "data" / "jobs.sqlite"

    erst = W.run_pinned(slug="retry", repo_root=gitrepo, host="mac", attempts=2)
    conn = job_db.connect(db)
    try:
        # **Über `report_status()`, nicht per UPDATE.** Der erste Anlauf dieses
        # Tests schrieb `status='failed'` direkt in die Zeile und ließ
        # `locked_at` stehen — eine Datenlage, die der echte Weg nie erzeugt,
        # und `reserve_next()` überging die Zeile deshalb aus einem Grund, der
        # mit dem Ticket nichts zu tun hatte.
        #
        # Das ist die Lehre dieser Runde, angewandt auf sie selbst: **ein Test,
        # der einen Zustand herstellt statt ihn herstellen zu lassen, prüft
        # seine Annahme über den Zustand.**
        assert job_db.report_status(conn, erst["id"], status="failed") == "ok"
        # Den Zähler setzt im Betrieb der **Wrapper** (`next_attempt =
        # attempt_cur + 1`), und der ist hier gefaked. `attempt=1` an einem
        # `failed`-Slot ist genau der Zustand, den er hinterlässt — gesetzt
        # wird er hier deshalb erst *nach* `report_status()`, das `locked_at`
        # und den Rest der Zeile ordentlich hinterlässt.
        conn.execute("UPDATE jobs SET attempt=1 WHERE id=?", (erst["id"],))
        conn.commit()
    finally:
        conn.close()

    zweit = W.run_pinned(slug="retry", repo_root=gitrepo, host="mac", attempts=2)

    conn = job_db.connect(db)
    try:
        zeilen = conn.execute(
            "SELECT id, attempt, status FROM jobs WHERE job_uid=?",
            (job_uid("retry"),)).fetchall()
    finally:
        conn.close()

    assert len(zeilen) == 1, (
        f"{len(zeilen)} Zeilen für denselben Bucket — START hat eine neue "
        f"angelegt, statt die wartende fortzusetzen; die alte liegt für immer "
        f"da: {[dict(z) for z in zeilen]} (#191)")
    assert zweit["id"] == erst["id"], (
        "der Start hat eine andere Zeile erwischt als die wartende (#191)")
    assert zeilen[0]["attempt"] >= 1, (
        f"der Versuchszähler steht auf {zeilen[0]['attempt']} — er hat wieder "
        f"bei null begonnen, und damit erreicht ein lokaler Job `error` nie, "
        f"solange ein Mensch ihn per START wiederholt (#191)")


def test_ein_arbeitender_lokaler_slot_bekommt_weiterhin_eine_eigene_zeile(
        gitrepo, monkeypatch):
    """Die Gegenprobe, und sie schützt den Grund für die alte Bauart.

    `unique_slug` gibt es, damit *„ein zweiter ▶ Start nicht mit der noch nicht
    aufgeräumten Zeile des ersten Laufs kollidiert"*. Fortgesetzt wird deshalb
    nur, was **wartet** — was läuft, bekommt weiterhin seine eigene Zeile.
    """
    from bibi.daemon import job_db, worker as W
    from bibi.schedule.models import job_uid

    monkeypatch.setattr(W, "_run_wrapper", _fake_wrapper(gitrepo))
    db = gitrepo / "data" / "jobs.sqlite"

    W.run_pinned(slug="retry", repo_root=gitrepo, host="mac", attempts=2)
    W.run_pinned(slug="retry", repo_root=gitrepo, host="mac", attempts=2)

    conn = job_db.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE job_uid=?",
                         (job_uid("retry"),)).fetchone()["n"]
    finally:
        conn.close()
    assert n == 2, (
        "ein laufender Slot wurde fortgesetzt statt danebengelegt — der Fix "
        "greift zu weit und nimmt einen laufenden Prozess aus der Anzeige")


def test_beide_pfade_lassen_den_versuchszaehler_stehen(tmp_path):
    """**Der Wächter, und er hat eine andere Bauart als die zwei davor.**

    Dort prüft einer, ob eine Angabe existiert; hier prüft einer, ob **zwei
    Wege dasselbe tun**. Drei Ausprägungen derselben Zusage in einer Klammer
    (`#175` Auslöser, `#176` Umgebung, `#191` Zählung) sind der Beleg, dass ein
    Test je Pfad die Frage nicht stellt, um die es geht:

    > *„Egal ob Daemon oder Session, ein Client muss 100% das gleiche Verhalten
    > zeigen."* (m.rau, 2026-08-13)
    """
    import time as _t

    from bibi.daemon import job_db, worker as W

    db = tmp_path / "jobs.sqlite"
    conn = job_db.connect(db)
    try:
        for slug, pin in (("team-1a2b3c4d", None), ("lokal-5e6f7a8b", "testhost")):
            conn.execute(
                "INSERT INTO jobs (id, slug, job_uid, schedule_ref, kind, payload, "
                "priority, status, enqueued_at, next_fire_at, attempt, attempts, "
                "pinned_host, schedule) VALUES (?,?,?,?,?,?,0,'failed',?,?,1,2,?,'now')",
                (slug[:8], slug, slug.rsplit("-", 1)[0], f"{slug}.md", "job",
                 "exit 1", _t.time(), _t.time() - 60, pin))
        conn.commit()

        job_db.start_now(conn, "team-1a2")
        vorher = conn.execute(
            "SELECT attempt FROM jobs WHERE slug=?", ("team-1a2b3c4d",)).fetchone()
        conn.commit()
    finally:
        conn.close()

    W.resume_pinned_waiting("lokal", db_path=db, host="testhost")

    conn = job_db.connect(db)
    try:
        nachher = conn.execute(
            "SELECT attempt, status FROM jobs WHERE slug=?",
            ("lokal-5e6f7a8b",)).fetchone()
    finally:
        conn.close()

    assert vorher["attempt"] == 1, "der Scheduler-Pfad hat den Zähler angefasst"
    assert nachher["attempt"] == 1, (
        f"der Client-Pfad setzt den Zähler auf {nachher['attempt']} zurück, "
        f"der Scheduler-Pfad lässt ihn stehen — dieselbe Zusage, zwei "
        f"Verhalten (#191)")
    assert nachher["status"] == "pending", (
        "die fortgesetzte Zeile muss dispatchbar sein — der gepinnte Loop "
        "nimmt `failed` seit #175 bewusst nicht auf, und ein START ist ein "
        "Mensch und keine Frist (#191)")


# ── #186: Umbruch oder Scrollbar, entschieden am Job-Typ ───────────────────


def _zeilen(*paare) -> list[dict]:
    return [{"t": NOW, "s": s, "line": line} for s, line in paare]


def test_ein_shell_output_scrollt_statt_umzubrechen():
    """*„Entferne überall den Umbruch und führe die Scrollbar."* (m.rau)

    `out`/`err` sind Programmausgabe — Tabellen, Pfade, Stacktraces. Ein
    Umbruch zerlegt dort eine Zeile, die als Zeile gemeint ist.

    **Ein Nebenbefund zeigt, dass die Absicht schon einmal da war:** `.term`
    trägt seit jeher `overflow-x: auto` — eine horizontale Scrollbar, die
    wegen `pre-wrap` nie erscheinen konnte. Der Umbau ist an dieser Stelle
    eine Korrektur und kein Zusatz.
    """
    html = render.output_block(_zeilen(("out", "x" * 400)), "job")
    assert "term-wrap" not in html, (
        f"ein Shell-Output trägt die Umbruch-Klasse — er soll scrollen: {html[:120]!r} (#186)")
    assert re.search(r"\.term\s*\{[^}]*white-space:\s*pre\s*;", render._CSS), (
        "`.term` bricht weiterhin um — die Scrollbar daneben kann nie "
        "erscheinen (#186)")


def test_ein_claude_lauf_bricht_um():
    """**Der Fall, an dem das `s`-Feld nicht entscheidet.**

    Ein Claude-Lauf schreibt seinen Fließtext ebenfalls als `s: "out"`
    (`daemon/output_format.py`) — am Ereignistyp allein ist er von
    Programmausgabe nicht zu trennen. Die Achse, die trägt, ist der **Job-Typ**,
    und er liegt an jeder Renderstelle bereit.

    Zustimmung m.rau zur Regel am Datenmodell und zum Umbruch für
    Claude-Text: *„Einverstanden."*
    """
    html = render.output_block(_zeilen(("out", "Ich habe die Datei gelesen. " * 20)),
                               "claude")
    assert "term-wrap" in html, (
        f"ein Claude-Lauf scrollt statt umzubrechen — dort trägt die Zeile "
        f"keine Bedeutung: {html[:120]!r} (#186)")
    assert re.search(r"\.term-wrap[^}]*white-space:\s*pre-wrap", render._CSS), (
        "die Umbruch-Klasse steht im Markup, aber in keiner CSS-Regel — "
        "derselbe Fehler wie #148")


def test_phase_zeilen_brechen_um():
    """`phase` sind ganze Sätze (*„worktree: wird vorbereitet …"*) — die tragen
    ihre Bedeutung nicht in der Zeilengrenze und brechen deshalb um, auch in
    einem Shell-Job."""
    assert re.search(r"\.term\s+\.phase[^}]*white-space:\s*pre-wrap", render._CSS), (
        "eine `phase`-Zeile scrollt wie Programmausgabe — sie ist ein Satz "
        "und kein Datensatz (#186)")


def test_die_beiden_anderen_ausgabeflaechen_scrollen_ebenfalls():
    """*„Entferne **überall** den Umbruch"* — der Log-Screen und der
    ausgeklappte Bereich im Job-Detail gehören dazu.

    `.out-body` trug zusätzlich `overflow-wrap: anywhere` und brach damit sogar
    **mitten im Wort**: ein Pfad oder ein Hash wurde an beliebiger Stelle
    zerschnitten.
    """
    for klasse in (".logbox", ".out-body"):
        regel = re.search(rf"{re.escape(klasse)}\s*\{{([^}}]*)\}}", render._CSS)
        assert regel, f"{klasse} hat keine CSS-Regel"
        assert "pre-wrap" not in regel.group(1), (
            f"{klasse} bricht weiterhin um (#186)")
        assert "anywhere" not in regel.group(1), (
            f"{klasse} bricht mitten im Wort (#186)")
