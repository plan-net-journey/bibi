"""Job Detail: der Weg von der URL zurueck zum Job (FE-Spezifikation §5).

Die URL traegt den `job_uid` (`/-/jobs/{job_uid}`), und der ist ein md5 —
nicht umkehrbar. Der Weg zurueck fuehrt deshalb ueber die bekannten Slugs:
wer in Frage kommt, wird gehasht und verglichen. Das klingt nach Umweg, ist
aber genau die Eigenschaft, die `job_uid` ueberhaupt brauchbar macht — er ist
deterministisch, also auf beiden Seiten ohne Absprache berechenbar
(Zustandsmodell §6).

Warum nicht einfach `SELECT slug FROM jobs WHERE job_uid=?`: das findet nur,
was eine Job-Zeile hat. Ein Lauf aus der Historie, dessen MD geloescht wurde
(`dropped`), hat keine — und in Bestandszeilen ist `job_uid` ohnehin `NULL`,
weil die Migration bewusst ohne Backfill lief. Die Kandidatenliste aus Slugs
deckt beide Faelle ab, ohne eine zweite Wahrheit einzufuehren.

Reine Funktion, keine Datenbank, kein HTTP — dieselbe Trennung wie beim
Jobs-Screen (`jobs_view.build_rows()`).
"""

from __future__ import annotations

import re

import pytest

from bibi.controller import jobs_view
from bibi.schedule.models import job_uid


def test_finds_the_slug_behind_a_job_uid():
    assert jobs_view.slug_for(job_uid("EngineCI"), ["daily-digest", "EngineCI"]) == "EngineCI"


def test_returns_none_when_nothing_matches():
    """Ein unbekannter `job_uid` ist ein 404, kein Absturz und kein leerer
    Screen: die URL kann aus einem Lesezeichen stammen, dessen Job es nicht
    mehr gibt."""
    assert jobs_view.slug_for(job_uid("weg"), ["EngineCI"]) is None


def test_finds_a_homeless_run_from_the_journal():
    """Der Fall, der die Datenbank-Abfrage nicht koennte: die MD ist geloescht,
    es gibt keine Job-Zeile mehr — aber die Laeufe stehen im Journal, und ihr
    Slug genuegt. Seit m.rau/bibi#130 ist das der einzige Weg zu ihnen: das
    JOURNAL-Segment fuehrt sie, der Archive-Screen ist gestrichen."""
    assert jobs_view.slug_for(job_uid("Runner-Container"),
                              ["EngineCI", "Runner-Container"]) == "Runner-Container"


def test_a_pinned_run_resolves_to_its_base_job():
    """Ein lokaler Lauf traegt einen Slug mit Zufallssuffix
    (`EngineCI-46ec57c7`). Er gehoert zum Basis-Job und darf keine eigene
    Detailseite bekommen — sonst zerfiele ein Job in so viele Seiten, wie er
    lokale Laeufe hatte (live: 252 Pseudo-Slugs fuer 33 echte Jobs)."""
    kandidaten = ["EngineCI", "EngineCI-46ec57c7"]
    assert jobs_view.slug_for(job_uid("EngineCI"), kandidaten) == "EngineCI"


def test_the_first_match_wins_and_it_is_stable():
    """Zwei MDs mit demselben Slug ergeben denselben `job_uid` — das ist die
    beabsichtigte Kollision (`duplicate`, Zustandsmodell §6). Die Aufloesung
    muss davon unbeeindruckt genau einen Slug liefern, sonst haengt die
    Detailseite von der Reihenfolge der Kandidaten ab."""
    assert jobs_view.slug_for(job_uid("Runner"), ["Runner", "Runner"]) == "Runner"


def test_duplicates_in_the_candidate_list_do_not_matter():
    kandidaten = ["a", "b", "a", "EngineCI", "b"]
    assert jobs_view.slug_for(job_uid("EngineCI"), kandidaten) == "EngineCI"


@pytest.mark.parametrize("kaputt", ["", "nicht-hex", "x" * 32])
def test_a_malformed_uid_is_simply_unknown(kaputt):
    """Kein Sonderweg fuer Unsinn: was nicht passt, passt nicht — und der
    Aufrufer macht daraus ein 404 wie bei jedem anderen unbekannten Job."""
    assert jobs_view.slug_for(kaputt, ["EngineCI"]) is None


# ── die Route ────────────────────────────────────────────────────────────────


class _FakeClient:
    """Genügend Scheduler für einen Screen — der Rest kommt lokal."""

    def __init__(self):
        self.rebuilt: list[str] = []
        self.gestartet: list[str] = []
        self.gekillt: list[str] = []
        self.zurueckgesetzt: list[str] = []

    def status(self) -> dict:
        return {"now": 1_000_000.0, "roles": ["scheduler"]}

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def run_output(self, jid):
        # Der Host haelt den Output seiner eigenen Laeufe.
        return {"events": [{"text": f"scheduler run {jid}"}]} if jid == 23611 else {}

    def run_journal(self, **_):
        return []

    def jobs(self, **_):
        return []

    def run(self, *, slug=None, cmd=None):  # noqa: ARG002
        # START auf dem Client: `POST /-/run` legt eine echte, gepinnte
        # jobs-Zeile an und dispatcht sofort — der 501-Stub `/-/job/{id}/start`
        # ist auf einem reinen Client nicht der Weg (m.rau/bibi#135).
        self.gestartet.append(slug)
        return {"id": "neu", "slug": slug, "status": "running"}

    def run_live_kill(self, slug):
        self.gekillt.append(slug)
        return {"slug": slug, "killed": True}

    def run_live_reset(self, slug):
        self.zurueckgesetzt.append(slug)
        return {"slug": slug, "reset": True}

    def run_rebuild(self, slug):
        # Der Client-Weg fuer REBUILD (`POST /-/run/live/{slug}/rebuild`).
        # Er nimmt den **Slug**, nicht die Job-ID — die Route dort schlaegt den
        # Exec-Mode ueber die Schedule-MD nach, nicht ueber die DB.
        self.rebuilt.append(slug)
        return {"slug": slug, "rebuilt": True}


@pytest.fixture
def client(team_repo):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    fake = _FakeClient()
    app = create_app(roles.resolve({"controller"}), controller_client=fake)
    with TestClient(app) as c:
        c.fake = fake
        yield c, team_repo


def _md(root, rel: str, body: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_the_slug_link_from_the_jobs_screen_is_not_a_dead_end(client):
    """Der Befund, der diesen Screen ausgeloest hat: der Jobs-Screen verlinkt
    jeden Slug auf `/-/jobs/{job_uid}`, und das antwortete mit 404. Ein toter
    Link ist schlimmer als eine fehlende Route — er sieht aus wie ein Weg."""
    c, root = _md_job(client)
    r = c.get(f"/-/jobs/{job_uid('EngineCI')}")
    assert r.status_code == 200
    assert "EngineCI" in r.text


def _md_job(client):
    c, root = client
    _md(root, "ci/EngineCI.md",
        '---\nslug: EngineCI\nschedule: "0 * * * *"\njob: "pytest -q"\n---\n')
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        job_db.rescan(conn, vault_root=root / "vault" / "case")
    finally:
        conn.close()
    return c, root


def test_an_unknown_job_uid_is_a_404(client):
    c, _ = client
    assert c.get(f"/-/jobs/{job_uid('gibt-es-nicht')}").status_code == 404


def test_the_page_carries_the_shell(client):
    """App-Bar und Header stehen auf **jedem** Screen und jeder Unterseite
    (FE-Spezifikation §1) — sie sind der Rahmen, nicht Screen-Inhalt.

    Der Test verlangte bis `#131` zusaetzlich `Archive` und war **gruen aus dem
    falschen Grund**: der Tab ist seit `#130` weg, gefunden wurde das Wort in
    einem CSS-Kommentar, den dieselbe Seite einbettet. Genau die Kategorie, die
    `#130` an zwei anderen Tests aufgedeckt hat — ein Substring-Test bindet an
    das ganze Dokument, nicht an die Stelle, die er meint. Deshalb steht hier
    jetzt die Gegenprobe mit dabei."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    from bibi.controller import render
    for name, _ in render.SCREENS:
        assert f">{name}<" in text, f"{name} fehlt in der App-Bar"
    assert ">Archive<" not in text
    assert "CLIENT" in text and "SCHEDULER" in text


# ── Job Attributes (FE-Spezifikation §5.5) ───────────────────────────────────


def test_the_attrs_link_in_the_header_is_not_a_dead_end(client):
    """Derselbe Fehler wie beim Slug-Link, nur eine Ebene tiefer und von mir
    selbst gebaut: die Kopfzeile traegt `[ATTRS]`, und die Unterseite gab es
    nicht. Ein Knopf, der ins Leere fuehrt, ist schlimmer als keiner."""
    c, _ = _md_job(client)
    seite = c.get(f"/-/jobs/{job_uid('EngineCI')}")
    assert f"/-/jobs/{job_uid('EngineCI')}/attrs" in seite.text  # der Link steht da
    assert c.get(f"/-/jobs/{job_uid('EngineCI')}/attrs").status_code == 200


def test_attrs_separates_set_values_from_defaults(client):
    """Zwei Signale statt einem (§5.5): ein gesetzter Wert steht normal, ein
    Default gedimmt **und in Klammern**. Dimmung allein geht in hellen Themes
    und auf schlechten Monitoren verloren — dann saehe ein geerbter Wert aus
    wie eine Entscheidung."""
    c, root = client
    _md(root, "ci/Timeouts.md",
        '---\nslug: Timeouts\nschedule: "0 * * * *"\nsilence_timeout: 1800\n'
        'job: "echo hi"\n---\n')
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        job_db.rescan(conn, vault_root=root / "vault" / "case")
    finally:
        conn.close()
    text = c.get(f"/-/jobs/{job_uid('Timeouts')}/attrs").text
    assert "1800" in text                     # gesetzt: normal
    assert re.search(r"\(\s*\d+\s*\)", text)  # geerbt: in Klammern
    # Und der gesetzte Wert steht *nicht* in Klammern — sonst waere die
    # Unterscheidung blosse Dekoration.
    assert not re.search(r"\(\s*1800\s*\)", text)


def test_attrs_leads_back_to_the_job(client):
    """`◂ back to job` — eine Unterseite ohne Rueckweg ist eine Sackgasse."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}/attrs").text
    assert f'href="/-/jobs/{job_uid("EngineCI")}"' in text


def test_attrs_of_an_unknown_job_is_a_404(client):
    c, _ = client
    assert c.get(f"/-/jobs/{job_uid('gibt-es-nicht')}/attrs").status_code == 404


# ── Quell-Gruppen und Lauf-Liste (FE-Spezifikation §5.1–§5.3) ────────────────


def test_runs_are_grouped_by_day_newest_first():
    """Tagesgruppen geben einen greifbaren Anker („was lief gestern?"), den
    eine gleichfoermige Endlosliste nicht hat (§5.3)."""
    from bibi.controller import jobs_view
    runs = [
        {"finished_at": 1_754_003_600.0, "status": "complete"},   # spaeter
        {"finished_at": 1_754_000_000.0, "status": "error"},      # frueher, selber Tag
        {"finished_at": 1_753_800_000.0, "status": "complete"},   # anderer Tag
    ]
    tage = jobs_view.by_day(runs)
    assert len(tage) == 2
    assert tage[0][1][0]["finished_at"] > tage[0][1][1]["finished_at"]
    # Über die Läufe geprüft, nicht über die Tages-Strings: `dd/mm/yyyy` ist
    # ein Anzeigeformat und lexikografisch nicht sortierbar.
    assert tage[0][1][0]["finished_at"] > tage[1][1][0]["finished_at"]


def test_runs_are_sorted_by_finished_at_not_by_archived_at():
    """Der Kern der Entscheidung aus §5.3: unter A2 laufen beide Zeiten
    beliebig weit auseinander. Ein Lauf, der tagelang blockiert stand, gehoert
    an seinen Lauf-Tag — nicht an den Tag, an dem ihn jemand abgeraeumt hat."""
    from bibi.controller import jobs_view
    alt_aber_frisch_archiviert = {"finished_at": 1_753_800_000.0,
                                  "archived_at": 1_754_100_000.0, "status": "killed"}
    neu = {"finished_at": 1_754_000_000.0, "archived_at": 1_754_000_100.0,
           "status": "complete"}
    tage = jobs_view.by_day([alt_aber_frisch_archiviert, neu])
    assert tage[0][1][0] is neu  # der juengere *Lauf* steht oben


def _laufender_slot(client):
    """Ein Job, dessen lokaler Slot einen laufenden Lauf haelt."""
    c, root = _md_job(client)
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        conn.execute("UPDATE jobs SET next_fire_at=1.0 WHERE slug='EngineCI'")
        jid = job_db.reserve_next(conn, worker="w1", host="h")["id"]
        job_db.report_status(conn, jid, status="running")
    finally:
        conn.close()
    return c, root


def test_the_page_shows_the_tiles_above_and_one_list_below(client):
    """Der Screen im Bild (§5.1): oben die Kacheln — was ich tun kann —, unten
    **eine** Liste ueber beide Quellen — was geschehen ist. Die frueheren zwei
    faltbaren Gruppen sind weg; mit ihnen die Faltung und der zweite Ort fuer
    den Output."""
    c, _ = _laufender_slot(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert 'class="tile' in text
    assert "running" in text
    # Genau **eine** Lauf-Tabelle, nicht je Quelle eine.
    assert text.count('<table class="runs"') == 1
    # Und keine Faltung mehr: sie war der Ersatz fuer den Herkunftsfilter.
    assert "data-fold=" not in text


def test_the_list_carries_a_source_column(client):
    """`S`/`C` (§5.3). Die Spalte stand im urspruenglichen Entwurf und war nur
    der Faltung zum Opfer gefallen — die es nicht mehr gibt. Ohne sie waere die
    zusammengefuehrte Liste nicht mehr lesbar: dieselbe Zeile saehe fuer einen
    Scheduler- und einen lokalen Lauf gleich aus."""
    c, _ = _laufender_slot(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    kopf = text[text.index('<table class="runs"'):]
    for spalte in ("TIME", "SRC", "STATUS", "EXIT", "RUNTIME", "COMMIT"):
        assert f"<th>{spalte}</th>" in kopf, f"Spalte {spalte} fehlt"
    # Das Datum steht in der Tagestrennlinie, nicht noch einmal je Zeile.
    assert "<th>DATE</th>" not in kopf


def test_the_run_in_the_slot_is_marked_in_the_list(client):
    """Die Marke bedeutet „steht im Slot", nicht „laeuft" — sie traegt beide
    Faelle, die ein Slot kennen kann. **Sie ist der Bezug zwischen oben und
    unten:** die Kachel gehoert zu der Zeile, die ihre Marke traegt."""
    c, _ = _laufender_slot(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    # An der Zeile geprueft, nicht am Dokument: `run-in-slot` steht auch im
    # eingebetteten CSS, und ein blosses `in text` waere dort schon gruen —
    # dieselbe Falle wie beim `Archive`-Rest aus `#130`.
    assert 'class="run run-in-slot"' in text


def test_terminal_becoming_moves_nothing(client):
    """§5.1: *„Beim Terminalwerden bewegt sich nichts."* Der Nachweis ist die
    `run_id`: dieselbe Zeile traegt sie vor und nach der Archivierung, deshalb
    bleibt auch der Ausklappbereich, wo er ist. Vorher war es ein Sprung
    zwischen zwei Bereichen — Slot-Kopfzeile und Liste."""
    from bibi.daemon import job_db
    c, _ = _laufender_slot(client)
    conn = job_db.connect()
    try:
        zeile = conn.execute("SELECT id, slug, fire FROM jobs WHERE slug='EngineCI'").fetchone()
        rid = job_db.run_id_for(zeile["slug"], zeile["id"], zeile["fire"])
    finally:
        conn.close()
    vorher = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert f'data-run="{rid}"' in vorher
    conn = job_db.connect()
    try:
        jid = conn.execute("SELECT id FROM jobs WHERE slug='EngineCI'").fetchone()["id"]
        job_db.report_status(conn, jid, status="complete", exit_code=0)
    finally:
        conn.close()
    nachher = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert f'data-run="{rid}"' in nachher   # dieselbe Zeile, nur archiviert
    assert nachher.count(f'data-run="{rid}"') == 1   # und nicht doppelt


def test_a_pending_slot_shows_a_tile_but_no_row(client):
    """§5.1: ein `pending`-Slot bekommt keine Zeile, weil es noch keinen Lauf
    gibt. Sein Zustand steht in der Kachel — und **nur** dort."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert 'class="tile' in text and "pending" in text
    assert 'class="run run-in-slot"' not in text


def test_a_running_slot_offers_kill_but_not_start(client):
    """Besetzt: START waere ein zweiter Lauf auf demselben Platz. Der Knopf
    bleibt sichtbar und ausgegraut, statt zu verschwinden — sonst springt das
    Layout und die Information „das geht hier nicht" geht verloren (§5.2)."""
    c, root = _md_job(client)
    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        jid = conn.execute("SELECT id FROM jobs WHERE slug='EngineCI'").fetchone()["id"]
        job_db.report_status(conn, jid, status="starting")
        job_db.report_status(conn, jid, status="running")
    finally:
        conn.close()
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    # Eckige Klammern sind weg (Befund m.rau) — sie waren das Wireframe-Zeichen
    # fuer „hier ist eine Aktion"; im Browser traegt die Form das.
    assert 'data-verb="kill"' in text            # verfuegbar und verdrahtet
    assert 'class="slot-off">START<' in text     # sichtbar, aber ausgegraut
    assert 'data-verb="start"' not in text


def test_an_empty_run_list_says_what_to_do(client):
    """Leerer Zustand mit Handlungsanweisung (Umbauplan §4): der haeufigste
    erste Eindruck eines neuen Jobs ist eine leere Liste."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert "No runs yet" in text


def test_a_side_with_neither_slot_nor_runs_stays_hidden():
    """Die Gegenprobe — sonst zeigte jeder Job zwei Gruppen, davon eine leer."""
    assert [g.quelle for g in _grp(scheduler_slot={"status": "pending"})] == ["SCHEDULER"]


# ── Der Lauf, der im Slot steht (m.rau/bibi#131) ─────────────────────────────
#
# Die Liste fuehrt **jeden** Lauf, auch den noch nicht archivierten. Welche
# Slot-Zustaende einen solchen Lauf halten, sagt die Archivierungsregel
# (Zustandsmodell §3) und nicht eine Statusliste: `pending` hat noch keinen,
# `complete` ist nach A1 laengst im Journal, `done` ist verbraucht. Alles
# dazwischen haelt einen Lauf, der nirgendwo sonst steht.


def test_a_running_slot_becomes_the_topmost_run():
    """Der Fall, der `#131` ausgeloest hat: *„wenn in den Kacheln der current
    Job Status steht, wo steht dann der Output?"* — er steht in der Liste, wie
    jeder andere Lauf auch."""
    zeile = jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": "running",
         "started_at": 1_754_000_000.0},
        src="S", now=1_754_000_003.0)
    assert zeile is not None
    assert zeile["in_slot"] is True and zeile["src"] == "S"
    assert zeile["status"] == "running"


@pytest.mark.parametrize("status", ["pending", "complete", "done"])
def test_three_slot_states_hold_no_run_of_their_own(status):
    """Die drei Ausnahmen, jede aus einem anderen Grund: `pending` hat noch
    keinen Lauf (§5.1: „bekommt keine Zeile"), `complete` ist nach A1 schon
    archiviert — eine zweite Zeile waere derselbe Lauf doppelt —, und `done`
    ist ein verbrauchter Slot, kein Lauf."""
    assert jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": status,
         "started_at": 1_754_000_000.0, "finished_at": 1_754_000_005.0},
        src="S", now=2_000_000_000.0) is None


@pytest.mark.parametrize("status", ["error", "inactive", "zombie", "killed"])
def test_a_blocked_terminal_run_stays_in_the_list(status):
    """A2: diese vier bleiben stehen, bis ein Mensch START oder RESET
    ausloest. Bis dahin gibt es sie **nur** im Slot — ohne diese Zeile waere
    ein gescheiterter Lauf der einzige, dessen Output niemand sehen kann."""
    zeile = jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": status,
         "reason": "nonzero_exit", "started_at": 1_754_000_000.0,
         "finished_at": 1_754_000_231.9, "exit_code": 1},
        src="S", now=1_754_100_000.0)
    assert zeile is not None and zeile["status"] == status
    assert zeile["reason"] == "nonzero_exit" and zeile["exit_code"] == 1


def test_a_slot_without_a_start_has_nothing_to_show():
    """Ein Zustand allein macht noch keinen Lauf: ohne `started_at` hat nie
    einer begonnen. Der Fall entsteht nach RESET, das `started_at` ausdruecklich
    raeumt (`report_status()`)."""
    assert jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": "error"},
        src="S", now=1_754_100_000.0) is None


def test_the_slot_run_carries_the_canonical_run_id():
    """Derselbe `run_id`, den der Worker bildet — sonst zeigte der Deep-Link
    `#run=` vor der Archivierung auf etwas anderes als danach, und der
    Ausklappbereich waere an die Zeilenposition gebunden statt an den Lauf."""
    from bibi.daemon import job_db
    zeile = jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": "running",
         "started_at": 1_754_000_000.0}, src="S", now=1_754_000_003.0)
    assert zeile["run_id"] == job_db.run_id_for("EngineCI", "j1", 7)


def test_a_running_run_is_sorted_by_its_start():
    """`finished_at` fehlt, solange der Lauf laeuft — die Liste sortiert und
    gruppiert deshalb nach `sort_at`, das darauf zurueckfaellt. Ohne das fiele
    der laufende Lauf aus den Tagesgruppen heraus (`by_day()` ueberspringt, was
    keinen Zeitstempel hat) und waere gerade der eine nicht sichtbar, den man
    sucht."""
    zeile = jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": "running",
         "started_at": 1_754_000_000.0}, src="S", now=1_754_000_003.0)
    assert zeile["finished_at"] is None
    assert zeile["sort_at"] == 1_754_000_000.0


def test_a_running_run_shows_how_long_it_has_been_going():
    """`3.0s` im Wireframe (§5.1): die RUNTIME-Spalte eines laufenden Laufs
    misst gegen jetzt, weil es noch kein Ende gibt."""
    zeile = jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": "running",
         "started_at": 1_754_000_000.0}, src="S", now=1_754_000_003.0)
    assert zeile["exec_runtime"] == pytest.approx(3.0)


def test_a_finished_run_measures_against_its_end_not_against_now():
    """Die Gegenprobe zum vorigen: ein blockierter Lauf steht tagelang im Slot,
    und seine Laufzeit darf nicht mitwachsen. Genau dieser Fehler steckt in
    `exec_runtime` des Schedulers (`6d 1h` fuer einen 3-Sekunden-Lauf,
    m.rau/bibi#123) — hier wird er nicht wiederholt."""
    zeile = jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": "error",
         "started_at": 1_754_000_000.0, "finished_at": 1_754_000_231.9},
        src="S", now=1_754_600_000.0)
    assert zeile["exec_runtime"] == pytest.approx(231.9)


def test_the_slot_run_reads_row_status_not_status():
    """Dieselbe Falle wie bei den Kacheln: in der Scheduler-Antwort heisst das
    Feld `row_status`, `status` ist dort `None`."""
    zeile = jobs_view.slot_run(
        {"id": "j1", "slug": "EngineCI", "fire": 7, "row_status": "running",
         "status": None, "started_at": 1_754_000_000.0}, src="S", now=1_754_000_003.0)
    assert zeile["status"] == "running"


# ── Kacheln und EINE Lauf-Liste (FE-Spezifikation §5.1–§5.3) ─────────────────


def _liste(**kw):
    basis = dict(scheduler_slot=None, client_slot=None,
                 scheduler_runs=[], client_runs=[],
                 scheduler_host="sarasate", client_host="Mac.fritz.box",
                 now=1_754_100_000.0)
    basis.update(kw)
    return jobs_view.build_run_list(**basis)


def test_a_tile_is_missing_when_the_side_has_no_slot():
    """Der Unterschied zwischen *kein Platz* und *freier Platz* (§5.1.1): eine
    Kachel fehlt genau dann, wenn diese Seite keinen Slot hat — nicht, wenn er
    gerade leer ist."""
    assert [k.quelle for k in _liste(scheduler_slot={"row_status": "pending"}).tiles] \
        == ["SCHEDULER"]


def test_both_tiles_appear_side_by_side_when_both_sides_know_the_job():
    """Zwei Kacheln **nebeneinander**, weil sie gleichrangig sind und man sie
    staendig vergleicht („laeuft es beim Scheduler, aber lokal nicht?")."""
    kacheln = _liste(scheduler_slot={"row_status": "pending"},
                     client_slot={"status": "error"}).tiles
    assert [k.quelle for k in kacheln] == ["CLIENT", "SCHEDULER"]  # #147


def test_an_empty_slot_still_gets_its_tile():
    """`adhoc`: die Seite kennt ihn, gerade ist nichts los. `pending` ohne
    `next` ist ein *freier* Platz, kein fehlender."""
    kacheln = _liste(scheduler_slot={"row_status": "pending",
                                     "next_fire_at": None}).tiles
    assert len(kacheln) == 1 and kacheln[0].status == "pending"


def test_the_tile_carries_the_actions_of_its_state():
    """Die vier Knopf-Gesichter kommen aus `slot.actions()` — dieselbe Quelle
    wie die Engine, damit Oberflaeche und Zustandsmaschine nicht auseinander
    laufen koennen."""
    from bibi.schedule import slot
    k = _liste(scheduler_slot={"row_status": "error"}).tiles[0]
    assert k.aktionen == slot.actions("error")
    assert slot.Verb.START in k.aktionen and slot.Verb.KILL not in k.aktionen


def test_a_consumed_oneshot_offers_no_action_bar():
    """`done` ist die Ausnahme von „ausgegraut statt ausgeblendet": ein
    verbrauchter Slot zeigt keine toten Knoepfe, das Fehlen der Leiste ist
    selbst die Aussage (§5.2)."""
    from bibi.schedule import slot
    assert _liste(scheduler_slot={"row_status": slot.DONE}).tiles[0].aktionen \
        == frozenset()


def test_the_tile_reads_row_status_not_status():
    """Live gefunden: die Scheduler-Zeile aus `/-/schedule` heisst `row_status`,
    `status` ist dort `None`. Ein `or "pending"` kaschierte das und zeigte einen
    Zustand, den niemand gemeldet hatte."""
    k = _liste(scheduler_slot={"slug": "x", "row_status": "complete",
                               "status": None}).tiles[0]
    assert k.status == "complete"


def test_a_slot_without_any_status_is_not_invented():
    """Kein Rateschritt: fehlt jeder Zustand, sagt der Screen das, statt
    `pending` zu behaupten."""
    k = _liste(scheduler_slot={"slug": "x"}).tiles[0]
    assert k.status is None and k.aktionen == frozenset()


def test_one_list_carries_both_sources():
    """**Eine** Liste statt zweier Gruppen (§5.3). Die fruehere Faltung entstand
    gegen die Auffindbarkeit (1064 Scheduler-Laeufe gegen 9 lokale); das loest
    der Herkunftsfilter mit Zaehlung, und er zeigt zusaetzlich, *dass* es lokale
    gibt — eine zugeklappte Gruppe sagte das auch, aber nur, wenn man sie fand.
    """
    liste = _liste(
        scheduler_runs=[{"finished_at": 1_754_000_000.0, "status": "complete"}],
        client_runs=[{"finished_at": 1_754_003_600.0, "status": "error"}])
    assert [r["src"] for r in liste.runs] == ["C", "S"]  # juengster zuerst


def test_the_origin_filter_counts_both_sides():
    """`scheduler 182 · client 9` — die Zaehlung ist der Ersatz fuer die
    Faltung: sie beantwortet „gibt es hier ueberhaupt lokale Laeufe?", ohne dass
    man eine Gruppe aufklappen muss."""
    liste = _liste(scheduler_runs=[{"finished_at": 1.0, "status": "complete"}],
                   client_runs=[{"finished_at": 2.0, "status": "complete"}],
                   scheduler_total=182, client_total=9)
    assert liste.counts == {"S": 182, "C": 9}


def test_the_count_is_the_total_not_the_loaded_page():
    """Sonst zaehlte die Zahl mit jedem LOAD MORE hoch und saehe aus wie
    Zuwachs statt wie Fortschritt."""
    liste = _liste(scheduler_runs=[{"finished_at": 1.0, "status": "complete"}],
                   scheduler_total=45)
    assert liste.counts["S"] == 45


def test_the_run_in_the_slot_is_part_of_the_same_list():
    """Der Kern von `#131`: der laufende Lauf steht nicht mehr an der
    Slot-Kopfzeile, sondern als Zeile in derselben Liste — mit Marke."""
    liste = _liste(
        scheduler_slot={"id": "j1", "slug": "EngineCI", "fire": 7,
                        "row_status": "running", "started_at": 1_754_099_997.0},
        scheduler_runs=[{"finished_at": 1_754_000_000.0, "status": "complete"}])
    assert len(liste.runs) == 2
    assert liste.runs[0]["in_slot"] is True and liste.runs[0]["status"] == "running"
    assert liste.runs[1].get("in_slot") is not True


def test_the_slot_run_counts_towards_its_source():
    """Sonst zeigte der Filter `scheduler 0`, waehrend eine Scheduler-Zeile
    sichtbar in der Liste steht."""
    liste = _liste(
        scheduler_slot={"id": "j1", "slug": "EngineCI", "fire": 7,
                        "row_status": "running", "started_at": 1_754_099_997.0},
        scheduler_total=0)
    assert liste.counts["S"] == 1


def test_a_pending_slot_adds_no_row():
    """§5.1: „Ein `pending`-Slot bekommt keine Zeile, weil es noch keinen Lauf
    gibt; sein Zustand steht nur in der Kachel."""
    liste = _liste(scheduler_slot={"id": "j1", "slug": "EngineCI",
                                   "row_status": "pending",
                                   "next_fire_at": 1_754_200_000.0})
    assert liste.runs == [] and liste.tiles[0].status == "pending"


def test_a_run_in_both_stores_is_one_row_and_it_is_marked():
    """Ein Lauf kann in **beiden** Speichern stehen, und das ist kein Rennen,
    sondern der Normalfall nach einem KILL: der Lauf ist archiviert, der Slot
    traegt seinen Zustand weiter, bis jemand START oder RESET drueckt.

    **Live gefunden (2026-08-04):** `Runner` zeigte in der Kachel `killed ·
    by_user`, und die Liste fuehrte den Lauf nicht als Slot-Lauf — die erste
    Fassung verwarf ihn zugunsten des Journal-Eintrags. Damit verlor die Zeile
    ihre Marke, fiel aus dem Fenster-Schutz und die Kachel zeigte ins Leere.

    Richtig ist: **eine** Zeile, und sie traegt die Marke. Der Lauf ist
    derselbe, egal in welchem Speicher er gerade liegt — die Marke sagt „steht
    im Slot", nicht „ist unarchiviert"."""
    from bibi.daemon import job_db
    rid = job_db.run_id_for("EngineCI", "j1", 7)
    liste = _liste(
        scheduler_slot={"id": "j1", "slug": "EngineCI", "fire": 7,
                        "row_status": "killed", "started_at": 1_754_000_000.0,
                        "finished_at": 1_754_000_010.0},
        scheduler_runs=[{"run_id": rid, "id": 1938, "finished_at": 1_754_000_010.0,
                         "status": "killed"}])
    assert len(liste.runs) == 1
    assert liste.runs[0]["in_slot"] is True
    # Und es bleibt der **archivierte** Eintrag: nur er hat die Journal-ID, ueber
    # die sein Output erreichbar ist.
    assert liste.runs[0]["id"] == 1938


def test_a_side_without_a_slot_keeps_its_runs():
    """Live gefunden (2026-08-03) und mit der Umstellung auf eine Liste neu zu
    beantworten: `EngineCI` hat lokale Laeufe, aber keinen lokalen Slot —
    `bibi-ctrl run` legt Pseudo-Jobs mit Zufallssuffix an, der Basis-Slug hat
    dort keine Zeile.

    Frueher rettete das eine Gruppe *ohne* Slot. Jetzt braucht es sie nicht
    mehr: die Laeufe stehen in der gemeinsamen Liste, und der Herkunftsfilter
    zaehlt sie. Die Kachel entfaellt, weil es keinen Platz zu bedienen gibt —
    genau das, was §5.1.1 verlangt."""
    liste = _liste(scheduler_slot={"row_status": "pending"},
                   client_runs=[{"finished_at": 1_754_000_000.0,
                                 "status": "complete"}])
    assert [k.quelle for k in liste.tiles] == ["SCHEDULER"]
    assert [r["src"] for r in liste.runs] == ["C"]
    assert liste.counts["C"] == 1


def test_a_side_with_neither_slot_nor_runs_stays_hidden():
    """Die Gegenprobe zur Kachel-Regel — sonst zeigte jeder Job zwei Kacheln,
    davon eine leere. Eine leere Kachel behauptet einen Platz, den es nicht
    gibt, und ihre tote Knopfleiste sieht aus wie eine bedienbare."""
    liste = _liste(scheduler_slot={"row_status": "pending"})
    assert [k.quelle for k in liste.tiles] == ["SCHEDULER"]
    assert liste.counts["C"] == 0


def test_runs_are_sorted_by_when_they_ran_not_by_when_they_were_filed():
    """Der Kern der Entscheidung aus §5.3: unter A2 laufen Lauf- und
    Aufraeum-Zeit beliebig weit auseinander. Ein Lauf, der tagelang blockiert
    stand, gehoert an seinen Lauf-Tag — nicht an den Tag, an dem ihn jemand
    abgeraeumt hat."""
    alt_aber_frisch_archiviert = {"finished_at": 1_753_800_000.0,
                                  "archived_at": 1_754_100_000.0, "status": "killed"}
    neu = {"finished_at": 1_754_000_000.0, "archived_at": 1_754_000_100.0,
           "status": "complete"}
    liste = _liste(scheduler_runs=[alt_aber_frisch_archiviert, neu])
    assert liste.runs[0]["finished_at"] == 1_754_000_000.0


def test_every_run_can_be_grouped_by_day():
    """Die Tagesgruppen brauchen einen Zeitstempel, den **jeder** Lauf hat —
    auch der laufende, dem `finished_at` noch fehlt. Ohne `sort_at` fiele
    gerade er heraus."""
    liste = _liste(
        scheduler_slot={"id": "j1", "slug": "EngineCI", "fire": 7,
                        "row_status": "running", "started_at": 1_754_099_997.0},
        scheduler_runs=[{"finished_at": 1_754_000_000.0, "status": "complete"}])
    tage = jobs_view.by_day(liste.runs, ts_key="sort_at")
    assert sum(len(l) for _, l in tage) == 2


# ── LOAD MORE erweitert um eine Menge, nicht um einen Tag (§5.3) ─────────────


def _tage(*abstaende: float, jetzt: float = 1_754_100_000.0) -> list[dict]:
    """Laeufe, je einer `n` Tage vor `jetzt`."""
    return [{"sort_at": jetzt - t * 86_400, "status": "complete"} for t in abstaende]


def test_load_more_widens_until_ten_new_entries():
    """Der Knopf nimmt Tage dazu, **bis zehn neue Eintraege zusammenkommen** —
    sonst verspricht er „mehr" und liefert an einem ruhigen Tag eine einzige
    Zeile. Das war der Befund am Feed (Klasse A), und dieselbe Falle steht hier
    noch einmal."""
    runs = _tage(*[1.0] * 3, *[9.0] * 12)   # 3 nah, 12 in neun Tagen
    fenster = jobs_view.naechstes_fenster(runs, aktuell=2, jetzt=1_754_100_000.0)
    assert fenster is not None and fenster >= 10


def test_load_more_gives_up_after_thirty_empty_days():
    """Die Gegenrichtung: nach dreissig Tagen am Stueck ohne Zuwachs hoert er
    auf zu suchen. Ohne diese Grenze liefe die Erweiterung bei einem Job mit
    einer langen Pause bis zum aeltesten Lauf durch — und laedt dann alles auf
    einmal, statt „mehr"."""
    runs = _tage(1.0, 2.0, 200.0)          # eine sehr lange Luecke
    fenster = jobs_view.naechstes_fenster(runs, aktuell=3, jetzt=1_754_100_000.0)
    assert fenster == 33                    # 3 + 30, dann Schluss


def test_load_more_disappears_when_nothing_is_left():
    """`None` heisst „kein Knopf". Ein Knopf, der nichts mehr laedt, ist
    schlimmer als keiner — er sieht aus wie ein Weg (§5.3: die Reichweite muss
    „da war nichts" von „noch nicht geladen" unterscheidbar machen)."""
    runs = _tage(1.0, 2.0)
    assert jobs_view.naechstes_fenster(runs, aktuell=30, jetzt=1_754_100_000.0) is None


def test_the_window_cuts_by_run_time():
    """Das Fenster schneidet nach der **Lauf**-Zeit, wie die Sortierung auch —
    nicht nach `archived_at`. Sonst faellt ein Lauf aus dem Fenster, weil ihn
    jemand spaet abgeraeumt hat, und die Reichweiten-Angabe stimmte nicht mehr
    mit den Tagestrennlinien ueberein."""
    runs = _tage(1.0, 5.0, 40.0)
    drin = jobs_view.im_fenster(runs, tage=30, jetzt=1_754_100_000.0)
    assert len(drin) == 2


def test_the_window_never_cuts_away_the_run_in_the_slot():
    """**Live gefunden (2026-08-04, Browser-Abnahme):** `Runner` trug in seiner
    CLIENT-Kachel `killed · by_user`, und die Liste fuehrte diesen Lauf nicht —
    er endete vor 31,0 Tagen und fiel damit knapp aus dem 30-Tage-Fenster.

    Damit zeigte die Kachel auf eine Zeile, die es nicht gab, und der Bezug
    zwischen oben und unten (§5.1: „die Kachel gehoert zu der Zeile, die ihre
    Marke traegt") lief ins Leere. Der Slot-Lauf ist **kein Historieneintrag**,
    sondern der aktuelle Zustand — ein Zeitfenster ueber die Historie darf ihn
    nicht wegschneiden."""
    alt = jobs_view.slot_run(
        {"id": "j1", "slug": "Runner", "fire": 1140, "row_status": "killed",
         "reason": "by_user", "started_at": 1_751_400_000.0,
         "finished_at": 1_751_401_000.0},          # 31 Tage vor `jetzt`
        src="C", now=1_754_100_000.0)
    drin = jobs_view.im_fenster([alt], tage=30, jetzt=1_754_100_000.0)
    assert drin == [alt]


def test_the_state_filter_is_multiple_choice():
    """On/off und mehrfach waehlbar (§5.3) — „zeig mir alles, was schiefging"
    ist eine Frage, nicht fuenf."""
    runs = [{"status": "complete", "src": "S"}, {"status": "error", "src": "S"},
            {"status": "killed", "src": "C"}]
    assert len(jobs_view.gefiltert(runs, status=["error", "killed"], src=[])) == 2
    # Kein Filter heisst **alle**, nicht keine.
    assert len(jobs_view.gefiltert(runs, status=[], src=[])) == 3


def test_the_origin_filter_narrows_to_one_side():
    """Genau das, was die Faltung konnte — nur sichtbar und ohne Suchen."""
    runs = [{"status": "complete", "src": "S"}, {"status": "complete", "src": "C"}]
    assert [r["src"] for r in jobs_view.gefiltert(runs, status=[], src=["C"])] == ["C"]


# ── Output ausklappen statt Unterseite (FE-Spezifikation §5.4) ───────────────


def _seed_run(root, slug: str = "EngineCI", *, out: str = "hallo welt",
              vor_tagen: float = 0.0) -> int:
    """Eine archivierte Journal-Zeile mit Output auf Platte.

    **Mit einer echten Zeit, nicht mit `1.0`.** Die Liste zeigt seit `#131` ein
    Zeitfenster (Default 30 Tage) — ein Lauf, der laut Fixture 1970 endete,
    faellt heraus, und der Test praeft dann eine leere Liste. Genau die Sorte
    Fixture, die ihre eigene Erfindung prueft.
    """
    import time as _t
    from bibi.daemon import job_db
    from bibi.wrapper import output as out_mod
    ende = _t.time() - vor_tagen * 86_400
    rel = f"data/job/{slug}-1/output.jsonl"
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    out_mod.append(p, "out", out, t=1.0)
    conn = job_db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, started_at, "
            "finished_at, exit_code, output_ref, archived_at, domain) "
            "VALUES (?,?,?,?,?,?,?,?,?, 'local')",
            (f"{slug}:{int(vor_tagen)}:a", slug, "job", "complete",
             ende - 5, ende, 0, rel, ende))
        return cur.lastrowid
    finally:
        conn.close()


def test_a_run_row_offers_show_instead_of_a_link_away(client):
    """Es gibt keinen eigenen Lauf-Screen: `show` klappt die Zeile auf, statt
    die Seite zu verlassen. Wer drei Laeufe vergleichen will, soll nicht
    dreimal navigieren muessen."""
    c, root = _md_job(client)
    _seed_run(root)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert "[show]" in text
    assert "/-/ui/run/" not in text  # kein Weg auf den alten Lauf-Screen


def test_the_output_of_a_run_can_be_fetched(client):
    """Der Ausklappbereich holt sich seinen Inhalt selbst — die Liste traegt
    ihn nicht mit, sonst waere jede Seite so gross wie alle Ausgaben zusammen."""
    c, root = _md_job(client)
    jid = _seed_run(root, out="147 passed in 6.9s")
    r = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs/{jid}/output")
    assert r.status_code == 200
    assert "147 passed" in r.text


def test_the_output_of_an_unknown_run_is_a_404(client):
    c, _ = _md_job(client)
    assert c.get(f"/-/jobs/{job_uid('EngineCI')}/runs/999999/output").status_code == 404


def test_every_run_keeps_its_own_url(client):
    """`#run=<id>` (§5.4): ein Aufruf mit Anker oeffnet genau diese Zeile
    ausgeklappt. Das ist die Bedingung dafuer, dass der Status-Klick aus dem
    Jobs-Screen ueberhaupt ein Ziel hat."""
    c, root = _md_job(client)
    jid = _seed_run(root)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert f'data-jid="{jid}"' in text


def test_the_output_of_the_running_job_can_be_fetched(client):
    """**Der Fall, der `#131` ausgeloest hat:** *„wenn in den Kacheln der
    current Job Status steht, wo steht dann der Output?"*

    Ein Lauf, der noch im Slot steht, hat keine Journal-Zeile — unter A2
    entsteht sie erst auf START/RESET. Die Output-Route suchte aber
    ausschliesslich dort. Damit waere ausgerechnet der laufende Job der einzige
    gewesen, dessen Ausgabe niemand oeffnen kann, und die neue Liste haette
    eine Zeile mit einem `show` gezeigt, das ins Leere greift."""
    import time as _t
    from bibi.daemon import job_db
    from bibi.wrapper import output as out_mod
    c, root = _md_job(client)
    conn = job_db.connect()
    try:
        conn.execute("UPDATE jobs SET next_fire_at=1.0 WHERE slug='EngineCI'")
        jid = job_db.reserve_next(conn, worker="w1", host="h")["id"]
        rel = "data/job/laeuft/output.jsonl"
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        out_mod.append(p, "out", "147 passed, 2 skipped", t=_t.time())
        job_db.report_status(conn, jid, status="running", output_ref=rel)
    finally:
        conn.close()
    r = c.get(f"/-/jobs/{job_uid('EngineCI')}/slot/client/{jid}/output")
    assert r.status_code == 200
    assert "147 passed" in r.text


def test_the_running_row_points_at_the_slot_not_at_a_journal_id(client):
    """Die Zeile muss selbst sagen, woher ihr Output kommt. Ein `data-jid` auf
    einem Lauf ohne Journal-Zeile waere leer — und die Route antwortete mit
    404, obwohl die Ausgabe auf Platte liegt."""
    c, _ = _laufender_slot(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    zeile = text[text.index('class="run run-in-slot"'):][:700]
    assert 'data-slot="' in zeile and 'data-src="C"' in zeile
    assert 'data-jid=""' not in zeile


def test_the_output_of_a_scheduler_run_comes_from_the_host(client):
    """Live gefunden: die Journal-IDs der beiden Seiten sind verschiedene
    Zaehler — lokal reicht er bis 2224, beim Scheduler bis 23611. Eine
    Output-Route, die nur lokal sucht, antwortet fuer fast jeden Lauf des
    Screens mit 404, weil die meisten drueben liefen.

    Beide Seiten sind eigenstaendig (Zustandsmodell §1); zusammengefuehrt wird
    in der Anzeige, und dazu gehoert, den Output dort zu holen, wo er liegt."""
    c, _ = _md_job(client)
    r = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs/23611/output")
    assert r.status_code == 200
    assert "scheduler run 23611" in r.text


# ── Der Archive-Screen ist gestrichen (m.rau/bibi#130) ───────────────────────


def test_the_archive_screen_is_gone(client):
    """**Entscheidung m.rau 2026-08-04:** *„Die Frage ‚was lief' kann wegfallen.
    Weil wichtiger ist: laeuft alles."* Die beantwortet die `RELIABILITY`-Spalte
    im Jobs-Screen in einer Zahl; ein Screen, der Laeufe nach Zeit auflistet,
    beantwortet dieselbe Frage langsamer (FE-Spezifikation §1).

    Fuenf Routen fallen: der bibi5-Screen und die vier bibi4-Altrouten, die noch
    erreichbar waren, nur nicht mehr verlinkt.
    """
    c, _ = _md_job(client)
    for weg in ("/-/archive", "/-/ui/archive", "/-/ui/archive/list",
                "/-/ui/jobs/archive", "/-/ui/jobs/archive/list"):
        assert c.get(weg).status_code == 404, f"{weg} antwortet noch"


def test_the_app_bar_carries_five_screens():
    """Fuenf Screens statt sechs — und der Test prueft die Reihenfolge mit, weil
    die App-Bar auf jedem Screen steht: ein verrutschter Tab faellt sonst nur
    dem auf, der hinsieht."""
    from bibi.controller import render
    assert [name for name, _ in render.SCREENS] == \
        ["Feed", "Jobs", "Nodes", "Live", "Log"]


def test_no_archive_renderer_is_left_anywhere():
    """**Der Nachweis, dass es wirklich weg ist** — ein Test, keine Liste von
    Hand (dasselbe Muster wie `#120`/`#121`).

    Er nennt die Namen einzeln statt ein Praefix zu verbieten, und das ist hier
    entscheidend: `archived` heisst das Bus-Ereignis, `archived_at` eine
    Journal-Spalte, `_archive_run()` die Archivierungsregel A1/A2 — drei Dinge,
    die bleiben und deren Namen ein Praefix-Verbot mitreissen wuerde.
    """
    from pathlib import Path

    from bibi import controller as controller_pkg
    from bibi.controller import render

    tot = {
        render: ("archive_page_v5", "archive_page", "archive_fragment",
                 "jobs_archive_page", "jobs_archive_fragment",
                 "_client_archive_table", "_client_archive_row",
                 "_group_schedules", "_is_archived_oneshot", "schedule_list"),
    }
    for modul, namen in tot.items():
        for name in namen:
            assert not hasattr(modul, name), f"{modul.__name__}.{name} lebt noch"

    for modul in (render, controller_pkg):
        quelle = Path(modul.__file__).read_text()
        for weg in ('"/-/archive"', '"/-/ui/archive"', '"/-/ui/jobs/archive"'):
            assert weg not in quelle, f"die Route {weg} steht noch in {modul.__name__}"

    # Die Gegenprobe: die Journal-Liste heisst nicht mehr nach dem Archiv, aber
    # sie bleibt — sie speist den Pro-Job-Lookup des Jobs-Screens (`_jobs_data`),
    # nicht den entfallenen Screen. Genau die `_effective_days`-Falle aus #121.
    assert "_local_journal_runs" in Path(controller_pkg.__file__).read_text()


# ── Was vom Archive-Screen bleibt (m.rau/bibi#130) ───────────────────────────
#
# Der Screen ist gestrichen; drei seiner Aussagen gelten weiter und stehen
# deshalb hier, an dem Ort, der sie jetzt einloest. Sie ersatzlos mitzuloeschen
# waere der eigentliche Fehler: eine Aussage verschwindet dann nicht, weil sie
# falsch wurde, sondern weil ihr Traeger ging.


def test_a_job_that_only_ran_locally_is_still_reachable(client):
    """**Die Aussage, die die Streichung ueberhaupt erst erlaubt.**

    Gerettet aus `test_archive_reaches_runs_whose_job_is_gone`. Der Archive-
    Screen war der einzige Weg zu einem heimatlosen Lauf — einem Job, dessen MD
    geloescht wurde und dessen Laeufe nur noch im Journal stehen. FE §1 erklaert
    diesen Einwand fuer erledigt: *„das JOURNAL-Segment im Jobs-Screen fuehrt
    jeden Job, auch den ohne MD, und ueber ihn ist jeder Lauf erreichbar."*

    **Das stimmte nicht.** `_journal_for_rows()` fragte ausschliesslich den
    Scheduler; ein Job, der nur lokal lief (`bibi-ctrl run`), hatte dort keine
    Zeile und fiel damit aus der Klassifikation heraus. Live gemessen:
    **20 von 33** lokal gelaufenen Jobs standen nicht im Jobs-Screen. Ohne
    diesen Test haette die Streichung genau das genommen, wofuer sie sich
    ausdruecklich auf einen Ersatz berufen hat.

    Rot war `assert 'Runner-Container' in <jobs-screen>` — der Slug fehlte.
    """
    c, root = _md_job(client)
    _seed_run(root, "Runner-Container")  # keine MD, nur lokale Historie
    text = c.get("/-/jobs").text
    assert "Runner-Container" in text, "der heimatlose Lauf ist unerreichbar"
    assert f'/-/jobs/{job_uid("Runner-Container")}' in text


def test_the_run_list_of_a_job_loads_more(client):
    """Die Liste laedt nach — **um ein Zeitfenster, nicht um einen Offset**.

    Das ist die Umstellung aus `#131`: die Liste ist tageweise gruppiert, und
    ein Nachladen „um eine Seite" schnitte mitten in einen Tag. Der Knopf
    verbreitert deshalb das Fenster, und er erscheint nur, wenn ausserhalb
    wirklich noch etwas liegt."""
    c, root = _md_job(client)
    _seed_run(root, "EngineCI")
    _seed_run(root, "EngineCI", vor_tagen=40)   # ausserhalb des 30-Tage-Fensters
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs").text
    assert "LOAD MORE" in text


def test_load_more_disappears_when_everything_is_shown(client):
    """Gerettet aus dem Archive-Screen — und der Umzug war noetig, nicht bloss
    ordentlich: der Test stand auf `/-/archive`, und ohne die Route waere er
    gruen geblieben, weil in einem 404 kein `LOAD MORE` steht. Ein Test, der aus
    dem falschen Grund gruen ist, ist schlimmer als keiner.

    Ein Knopf, der nichts mehr laedt, ist schlimmer als keiner — er sieht aus
    wie ein Weg."""
    c, root = _md_job(client)
    _seed_run(root, "EngineCI")
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs?limit=50").text
    assert "LOAD MORE" not in text


def test_loading_more_adds_without_losing(client):
    """Kein Loch und kein Verlust: nach dem Klick steht mehr da, und nichts
    faellt weg.

    Dieselbe Aussage wie beim frueheren Offset-Paging („kein Ueberlappen und
    kein Loch"), auf den neuen Traeger gestellt. Beim Fenster stellt sie sich
    anders: das breitere **enthaelt** das schmalere, statt daneben zu liegen —
    deshalb kann es hier kein Loch geben, wohl aber einen Verlust, wenn das
    Fenster falsch herum schnitte."""
    c, root = _md_job(client)
    neu = _seed_run(root, "EngineCI")
    alt = _seed_run(root, "EngineCI", vor_tagen=40)
    eng = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs").text
    weit = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs?days=90").text
    assert f'data-jid="{neu}"' in eng and f'data-jid="{alt}"' not in eng
    assert f'data-jid="{neu}"' in weit and f'data-jid="{alt}"' in weit


def test_runtime_is_human_readable_not_raw_seconds():
    """Die RUNTIME-Spalte zeigt eine Dauer, keinen Float.

    Live gefunden beim Bauen der Wireframes: im Archive stand
    `2.8007938861846924`, waehrend der Jobs-Screen an derselben Stelle
    `2m 53s` zeigt — `_human_duration()` fehlte an zwei Stellen, beide aus
    Schritt 2 (Lauf-Liste in Job Detail und Archive).

    Rot war `assert '2.8007938861846924' not in html`.
    """
    from bibi.controller import render
    lauf = {"run_id": "x:1:2", "slug": "EngineCI", "status": "complete",
            "exit_code": 0, "finished_at": 1785833522.9, "domain": "host",
            "exec_runtime": 2.8007938861846924}

    detail = render.job_detail_page_v5(
        slug="EngineCI", spec={"slug": "EngineCI"}, now=1785833600.0,
        liste=_liste(scheduler_slot={"row_status": "pending"},
                     scheduler_runs=[dict(lauf)], now=1785833600.0))
    assert "2.8007938861846924" not in detail, "rohe Sekunden in der Lauf-Liste"
    assert "2.8s" in detail


def test_long_runtime_is_shown_in_minutes():
    """Die zweite Haelfte derselben Aussage, umgezogen vom Archive-Screen auf
    die Lauf-Liste — sie prueft die andere Seite von `_human_duration()`, den
    Uebergang in Minuten."""
    from bibi.controller import render
    lang = {"run_id": "x:1:3", "slug": "EngineCI", "status": "complete",
            "exit_code": 0, "finished_at": 1785833522.9, "domain": "host",
            "exec_runtime": 274.1314046382904}
    html = render.job_detail_page_v5(
        slug="EngineCI", spec={"slug": "EngineCI"}, now=1785833600.0,
        liste=_liste(scheduler_slot={"row_status": "pending"},
                     scheduler_runs=[lang], now=1785833600.0))
    assert "274.1314046382904" not in html
    assert "4m 34s" in html


# ── Die drei Verben sind verdrahtet (Befund m.rau 2026-08-04) ────────────────

def test_slot_buttons_carry_target_and_id():
    """START/RESET/KILL waren Attrappen.

    Befund m.rau: *„START, RESET, KILL haben alle keinen Effekt."* Im Markup
    stand `<button class="slot-do" data-verb="start">` — ohne `onclick`, ohne
    `hx-post`, und ohne JavaScript, das `data-verb` liest. Gebaut war die
    *Anzeige* der verfuegbaren Verben, nicht die Verben.

    Ein Knopf braucht drei Dinge, um zu wirken: das Verb, die Job-ID und die
    Seite, auf der er wirkt — der Scheduler-Slot liegt auf sarasate, ein POST
    an den lokalen Daemon traefe den falschen Job.

    Rot war: `data-id` und `data-ziel` fehlten im Markup.
    """
    from bibi.controller import render
    liste = _liste(scheduler_slot={"row_status": "error", "id": "sched-77"},
                   client_slot={"status": "pending", "id": "local-42"})
    html = render.job_detail_page_v5(slug="EngineCI", spec={"slug": "EngineCI"},
                                     now=1785833600.0, liste=liste)
    assert 'data-verb="start"' in html
    assert 'data-id="sched-77"' in html and 'data-ziel="scheduler"' in html
    assert 'data-id="local-42"' in html and 'data-ziel="client"' in html


def test_slot_buttons_have_a_handler():
    """Ohne Handler ist `data-verb` nur Dekoration."""
    from bibi.controller import render
    liste = _liste(scheduler_slot={"row_status": "error", "id": "sched-77"})
    html = render.job_detail_page_v5(slug="EngineCI", spec={"slug": "EngineCI"},
                                     now=1785833600.0, liste=liste)
    assert "slot-do" in html
    assert "addEventListener" in html, "kein JavaScript, das die Knoepfe bedient"
    assert "/-/ui/jobs/verb/" in html, "kein Ziel, an das gepostet wird"


def test_unavailable_verbs_stay_disabled_not_clickable():
    """Ausgegraute Verben bleiben sichtbar (FE §5.2) — aber sie duerfen nicht
    posten. Sonst wirkt ein toter Knopf wie ein defekter."""
    from bibi.controller import render
    # `running`: nur KILL ist zulaessig
    liste = _liste(scheduler_slot={"row_status": "running", "id": "sched-77"})
    html = render.job_detail_page_v5(slug="EngineCI", spec={"slug": "EngineCI"},
                                     now=1785833600.0, liste=liste)
    assert 'data-verb="kill"' in html
    assert 'data-verb="start"' not in html
    assert "START" in html  # sichtbar, aber als .slot-off


# ── Die CLIENT-Kachel zeigt zurueck, nicht nach vorn ─────────────────────────
#
# Live gefunden (2026-08-04, Browser): `daily-digest` trug in seiner
# CLIENT-Kachel `pending · next 17:20` — einen Termin, den es dort nicht gibt.
# Der Client hat keinen Dispatcher (Zustandsmodell §1: der Client-Slot entsteht
# **nur** durch /run), `next_fire_at` stammt aus der Zeit, als dieser Mac selbst
# Scheduler war, und wird lokal von niemandem mehr ausgewertet. Die Kachel
# versprach damit einen Lauf, der nie kommt.


def _kachel_html(**kw):
    from bibi.controller import render
    from bibi.schedule.models import job_uid
    # `slug`/`job_uid` tragen seit m.rau/bibi#152 den Bus-Wrapper der Region;
    # fuer die Kachel-Aussagen hier ist nur wichtig, dass sie gesetzt sind.
    return render.job_tiles_fragment(_liste(**kw).tiles, now=1_754_100_000.0,
                                     slug="probe", job_uid=job_uid("probe"))


def test_the_client_tile_says_idle_not_pending():
    """`pending` verspricht „reserviert, wartet" — auf dem Client wartet
    niemand. `idle` sagt, was dort wirklich ist: ein freier Platz."""
    html = _kachel_html(client_slot={"status": "pending", "id": "c1"})
    assert "idle" in html
    assert "pending" not in html, "der geratene Termin-Zustand steht noch da"


def test_the_scheduler_tile_keeps_pending():
    """Die Gegenprobe, und der Grund fuer die Unterscheidung: beim Scheduler
    ist `pending` wahr — dort steht der Platz reserviert und die Uhr laeuft."""
    html = _kachel_html(scheduler_slot={"row_status": "pending", "id": "s1"})
    assert "pending" in html and "idle" not in html


def test_the_client_tile_shows_the_last_run_not_the_next():
    """Der Scheduler-Slot zeigt nach vorn, der Client-Slot zurueck. `next` waere
    hier eine Behauptung ueber die Zukunft, die niemand einloest."""
    html = _kachel_html(client_slot={"status": "pending", "id": "c1",
                                     "next_fire_at": 1_754_200_000.0},
                        client_runs=[{"run_id": "r1", "status": "complete",
                                      "finished_at": 1_754_099_000.0}])
    assert "last " in html
    assert "next " not in html


def test_the_client_tile_stays_silent_without_a_last_run():
    """Ohne lokalen Lauf steht nur der Zustand da. Ein leeres `last —` waere
    ein Feld, das nach einem Fehler aussieht."""
    html = _kachel_html(client_slot={"status": "pending", "id": "c1"})
    assert "last " not in html


def test_the_client_tile_offers_rebuild_for_container_jobs():
    """REBUILD als vierter Knopf (Entscheidung m.rau, 2026-08-04) — bis hierher
    gab es ihn in bibi5 nirgends, obwohl FE §5.1.1 ihn fuer Container-Jobs
    vorsieht. Er verwirft das per-Job-Image, der naechste Lauf startet vom
    Default-Image."""
    html = _kachel_html(client_slot={"status": "pending", "id": "c1",
                                     "exec_mode": "container"})
    assert "REBUILD" in html


def test_a_host_job_has_no_rebuild_button_at_all():
    """Nicht ausgegraut, sondern abwesend (PLAN-24 Befund 5): ein Host-Job hat
    kein per-Job-Image, das ein Reset braeuchte. Ausgegraut wird, was der
    *Zustand* verbietet — nicht, was es fuer diesen Job nicht gibt."""
    html = _kachel_html(client_slot={"status": "pending", "id": "c1",
                                     "exec_mode": "host"})
    assert "REBUILD" not in html


def test_rebuild_posts_to_the_client_side():
    """Das Image liegt auf dem Knoten, der es gebaut hat. Ein REBUILD, das an
    den Host geht, wuerde das falsche verwerfen."""
    html = _kachel_html(client_slot={"status": "pending", "id": "c1",
                                     "exec_mode": "container"})
    assert 'data-verb="rebuild"' in html and 'data-ziel="client"' in html


def _lokale_zeile(slug: str, jid: str, **felder) -> None:
    """Eine Job-Zeile in der lokalen DB — der Client-Slot dieses Slugs."""
    import time as _t

    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        spalten = {"id": jid, "slug": slug, "schedule_ref": f"{slug}.md",
                   "kind": "job", "payload": "echo", "status": "pending",
                   "enqueued_at": _t.time(), **felder}
        conn.execute(
            f"INSERT INTO jobs ({','.join(spalten)}) "
            f"VALUES ({','.join(':' + k for k in spalten)})", spalten)
    finally:
        conn.close()


def test_rebuild_on_the_client_takes_the_run_live_route(client):
    """**Gemessen, nicht angenommen** (2026-08-04, Testclient auf :65200):
    `POST /-/job/{id}/rebuild` gibt es auf einem reinen Client nicht — die
    Route haengt an der `worker`-Rolle und antwortet dort `{"detail":"Not
    Found"}`. Der Weg, der antwortet, ist `POST /-/run/live/{slug}/rebuild`,
    und der nimmt den **Slug**."""
    c, _ = client
    _lokale_zeile("EngineCI", "c1", exec_mode="container")
    r = c.post("/-/ui/jobs/verb/client/c1/rebuild")
    assert r.status_code == 200, r.text
    assert c.fake.rebuilt == ["EngineCI"], "nicht der Slug, sondern die Job-ID gesendet"


def test_rebuild_on_an_unknown_job_is_a_404(client):
    """Ohne Zeile gibt es keinen Slug — und ein geratener waere schlimmer als
    ein 404."""
    c, _ = client
    assert c.post("/-/ui/jobs/verb/client/gibt-es-nicht/rebuild").status_code == 404
    assert c.fake.rebuilt == []


# ── Der Client-Slot ist der gepinnte Lauf, nicht die Schedule-Zeile (#135) ───
#
# Live gefunden (2026-08-04, Browser): `burndown-app` lief seit dem Vortag
# lokal — und die Seite zeigte **keine** CLIENT-Kachel, `client 0` und „No runs
# yet". Der laufende Lauf war auf dem ganzen Screen unsichtbar.
#
# Der Grund: `bibi-ctrl run` legt seine Zeile unter `<slug>-<token>` an
# (`run_pinned()`), der Screen suchte aber nach dem Basis-Slug. Was er dort
# fand, war eine rescan-erzeugte Zeile — auf diesem Mac alle am 2026-07-31
# 15:39 zuletzt angefasst, seither eingefroren, aus der Zeit, als er selbst
# Scheduler war. Die Kachel zeigte also eine Karteileiche und der echte Slot
# gar nicht. Entscheidung m.rau: „ein reiner Client laesst den Job laufen wie
# /run. Insofern besetzt dieser laufende Job doch den Slot."


def _gepinnter_lauf(slug: str, jid: str, status: str, **felder) -> None:
    """Ein `/run`-Lauf, wie `run_pinned()` ihn anlegt: eigener Slug mit
    8-stelligem Token, `pinned_host` gesetzt."""
    import socket
    import time as _t

    from bibi.daemon import job_db
    conn = job_db.connect()
    try:
        spalten = {"id": jid, "slug": f"{slug}-{jid[:8]}", "schedule_ref": f"{slug}.md",
                   "kind": "job", "payload": "echo", "status": status,
                   "pinned_host": socket.gethostname(), "host": socket.gethostname(),
                   "enqueued_at": _t.time(), "started_at": _t.time() - 30, **felder}
        conn.execute(
            f"INSERT INTO jobs ({','.join(spalten)}) "
            f"VALUES ({','.join(':' + k for k in spalten)})", spalten)
    finally:
        conn.close()


def test_a_running_local_run_owns_the_client_tile(client):
    """Der Live-Befund: ein laufender `/run` war auf dem Screen nirgends. Er
    besetzt den Slot — genau das ist der Sinn des Client-Slots."""
    c, _ = _md_job(client)
    _gepinnter_lauf("EngineCI", "aa11bb22", "running")
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    kacheln = text[text.index('class="tiles"'):text.index('RUNS')]
    assert "CLIENT" in kacheln, "keine CLIENT-Kachel, obwohl lokal etwas laeuft"
    assert "running" in kacheln


def test_the_running_local_run_also_gets_its_row(client):
    """Und er steht in der Liste, mit der Marke — sonst zeigte die Kachel auf
    eine Zeile, die es nicht gibt (derselbe Fehler wie bei `Runner`, #131)."""
    c, _ = _md_job(client)
    _gepinnter_lauf("EngineCI", "aa11bb22", "running")
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    liste = text[text.index('<table class="runs"'):]
    assert "run-in-slot" in liste, "der laufende Lauf traegt keine Marke"
    assert ">C<" in liste, "er steht nicht als Client-Lauf in der Liste"


def test_a_terminal_local_run_stays_in_the_slot(client):
    """„Wenn er einen terminalen Status erreicht, ist das immer noch der Slot"
    (m.rau). Unter A2 wartet er dort auf einen Menschen — mit START und RESET."""
    c, _ = _md_job(client)
    _gepinnter_lauf("EngineCI", "cc33dd44", "error", reason="nonzero_exit",
                    finished_at=1_785_000_000.0, exit_code=1)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    kacheln = text[text.index('class="tiles"'):text.index('RUNS')]
    assert "error" in kacheln and "nonzero_exit" in kacheln
    assert "idle" not in kacheln, "der belegte Slot zeigt sich als frei"


def test_without_a_local_run_the_tile_is_idle(client):
    """Die Gegenprobe: ohne `/run` ist der Platz frei, aber er ist da — sonst
    gaebe es auf einem Client keinen Weg, einen Job lokal zu starten."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    kacheln = text[text.index('class="tiles"'):text.index('RUNS')]
    assert "CLIENT" in kacheln and "idle" in kacheln


def test_start_on_the_client_goes_through_run_not_the_stub(client):
    """**Gemessen am Testclient auf :65200:** `POST /-/job/{id}/start` gibt dort
    `501 not implemented` zurueck — die Job-Verb-Routen sind ohne
    `scheduler`-Rolle Stubs. Der Weg, der wirkt, ist `POST /-/run`
    (m.rau/bibi#135): eine echte gepinnte Zeile, durch dieselbe Retry-/Error-/
    Deferred-/Zombie-Maschine, nur ohne Dispatcher davor — ausgeloest vom
    Menschen, nicht von der Uhr."""
    c, _ = client
    _lokale_zeile("EngineCI", "c1")
    r = c.post("/-/ui/jobs/verb/client/c1/start")
    assert r.status_code == 200, r.text
    assert c.fake.gestartet == ["EngineCI"]


def test_the_verbs_use_the_bucket_slug_not_the_pinned_one(client):
    """Der gepinnte Lauf heisst `<slug>-<token>`; `/-/run` und die
    `/-/run/live/*`-Routen erwarten den **Bucket**-Slug. Ohne die Rueckfuehrung
    triebe jeder Klick einen Lauf auf einem Slug an, den keine MD kennt."""
    c, _ = client
    _lokale_zeile("EngineCI-aa11bb22", "c9", pinned_host="Mac.fritz.box",
                  status="error")
    assert c.post("/-/ui/jobs/verb/client/c9/start").status_code == 200
    assert c.post("/-/ui/jobs/verb/client/c9/reset").status_code == 200
    assert c.fake.gestartet == ["EngineCI"] and c.fake.zurueckgesetzt == ["EngineCI"]


def test_kill_on_the_client_hits_the_run_live_route(client):
    """KILL wirkt auf den laufenden `/run`, nicht auf eine Scheduler-Zeile —
    dort gibt es keine."""
    c, _ = client
    _lokale_zeile("EngineCI-aa11bb22", "c9", pinned_host="Mac.fritz.box",
                  status="running")
    assert c.post("/-/ui/jobs/verb/client/c9/kill").status_code == 200
    assert c.fake.gekillt == ["EngineCI"]


def test_the_output_of_a_running_local_run_is_reachable(client):
    """**Der Fall, aus dem `#131` entstand — und er war noch offen.** Live
    gefunden (2026-08-04): `burndown-app` lief seit einem Tag, seine
    `output.jsonl` trug 239 Zeilen, zuletzt zwei Minuten alt, und die
    aufgeklappte Zeile sagte `(no output yet)`.

    Ursache ist dieselbe Regel wie beim Output-Fix vom selben Tag, nur
    andersherum: diese Route las **ausschliesslich** `output_ref`, und die ist
    waehrend `running` immer `NULL` — der Wrapper fuellt sie erst beim
    Terminal-Report. Richtig ist beides in der richtigen Reihenfolge: erst der
    Verweis, dann die Neuberechnung aus der `run_id`."""
    from bibi.wrapper import output as output_mod
    c, root = _md_job(client)
    _gepinnter_lauf("EngineCI", "aa11bb22", "running")  # output_ref bleibt NULL
    from bibi.daemon import job_db
    run_id = job_db.run_id_for("EngineCI-aa11bb22", "aa11bb22", 0)
    output_mod.append(root / "data" / "job" / run_id / "output.jsonl", "out", "laeuft noch")
    r = c.get(f"/-/jobs/{job_uid('EngineCI')}/slot/client/aa11bb22/output")
    assert r.status_code == 200
    assert "laeuft noch" in r.text, r.text


# ── Die Fehlerdurchreichung der Verb-Route ─────────────────────────────────
#
# Befund vom 2026-08-04: ein `409` des Hosts erreichte den Klickenden als
# `502 HTTP Error 409: Conflict` — die Route verpackte jeden Fehler gleich.
# „Bad Gateway" fuer einen Konflikt ist eine Falschaussage, und der Knopf
# zeigt sie im `alert()`.


class _HostMitFehler:
    """Ein Scheduler, der auf `job_action` mit einem HTTP-Status antwortet."""

    def __init__(self, code: int, body: bytes = b'{"error":"job is running"}') -> None:
        self.code, self.body = code, body

    def __call__(self, url, *, timeout=5.0):
        return self

    def status(self) -> dict:
        return {}

    def schedules(self) -> list:
        return []

    def journal(self, **_):
        return []

    def job_action(self, job_id: str, verb: str):
        import io
        import urllib.error
        raise urllib.error.HTTPError(
            "http://host/-/job", self.code, "Conflict", {},
            io.BytesIO(self.body))


def test_a_conflict_from_the_host_stays_a_conflict(client, monkeypatch):
    """Der Status des Hosts ist die Aussage — der Controller ist hier Bote,
    nicht Urheber."""
    from bibi import controller as controller_pkg
    c, _ = client
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://host.invalid:8780")
    monkeypatch.setattr(controller_pkg, "ControllerClient", _HostMitFehler(409))
    r = c.post("/-/ui/jobs/verb/scheduler/j1/kill")
    assert r.status_code == 409
    assert "job is running" in r.json()["error"], r.json()


def test_a_dead_host_is_still_a_bad_gateway(client, monkeypatch):
    """Die Gegenprobe: kommt gar keine Antwort, ist `502` richtig — dann ist
    der Controller tatsaechlich der, bei dem es haengt."""
    from bibi import controller as controller_pkg

    class _Tot:
        def __call__(self, url, *, timeout=5.0):
            return self

        def __getattr__(self, name):
            def _ruf(*_a, **_kw):
                raise OSError("connection refused")
            return _ruf

    c, _ = client
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://host.invalid:8780")
    monkeypatch.setattr(controller_pkg, "ControllerClient", _Tot())
    assert c.post("/-/ui/jobs/verb/scheduler/j1/kill").status_code == 502


# ── Ein Oneshot hat keinen lokalen Platz (FE §5.1.1) ──────────────────────


def test_a_oneshot_greys_out_its_client_tile(): 
    """**Diese Entscheidung ist gedreht** (m.rau, 2026-08-05, m.rau/bibi#146):
    *„wenn local nicht geht, wie z.B. `/at` … dann bitte ausgrauen, nicht
    verbergen."*

    Die Regel selbst gilt weiter — ein Oneshot laeuft nie lokal (FE §5.1.1,
    Zustandsmodell §5), es gibt dort keinen Platz zu bedienen. Nur ihre
    Darstellung war falsch: **ein Element, das fehlt, ist von einem, das es nie
    gab, nicht zu unterscheiden.** Wer die Kachel sucht und nicht findet, prueft
    zuerst, ob er im falschen Screen ist — nicht, ob die Aktion hier unmoeglich
    ist. Die ausgegraute Kachel beantwortet beides auf einen Blick.

    Dasselbe Muster hat der Umbau an anderer Stelle schon durchgesetzt: der
    offline-Header wird gedimmt und behaelt seine Werte, statt zu verschwinden
    (FE §2).
    """
    liste = _liste(scheduler_slot={"id": "s1", "row_status": "pending"},
                   client_slot={"id": "c1", "status": "complete"},
                   oneshot=True)
    assert [k.quelle for k in liste.tiles] == ["CLIENT", "SCHEDULER"]
    client = liste.tiles[0]
    assert client.disabled, "die CLIENT-Kachel ist nicht als gesperrt markiert"
    assert client.aktionen == frozenset(), "eine gesperrte Kachel bietet keine Verben"


def test_the_greyed_out_tile_says_why():
    """**Der Grund gehoert sichtbar dazu**, nicht nur als `title` auf Hover —
    auf einem Touch-Geraet gibt es kein Hover. Eine graue Kachel ohne Begruendung
    ist nur eine andere Art, nichts zu sagen."""
    from bibi.controller import render
    liste = _liste(client_slot={"id": "c1", "status": "complete"}, oneshot=True)
    html = render.job_tiles_fragment(liste.tiles, now=1_754_100_000.0,
                                     slug="x", job_uid="abc")
    assert "oneshots never run locally" in html


def test_a_disabled_tile_carries_no_buttons_in_the_html():
    """Die Gegenprobe zur Optik: ausgegraut heisst **nicht bedienbar**. Ein
    Knopf, der nur grau aussieht und trotzdem postet, waere schlimmer als der
    frueher fehlende — er verspricht eine Wirkung, die es nicht gibt."""
    from bibi.controller import render
    liste = _liste(client_slot={"id": "c1", "status": "complete"}, oneshot=True)
    html = render.job_tiles_fragment(liste.tiles, now=1_754_100_000.0,
                                     slug="x", job_uid="abc")
    kachel = html.split('class="tile', 1)[1].split("</div></div>", 1)[0]
    assert "hx-post" not in kachel


def test_a_recurring_job_keeps_its_client_tile():
    """Die Gegenprobe: die Regel gilt fuer Oneshots, nicht fuer jeden Job mit
    lokalem Slot — sonst verschwaende sie die halbe Seite."""
    liste = _liste(scheduler_slot={"id": "s1", "row_status": "pending"},
                   client_slot={"id": "c1", "status": "complete"})
    assert [k.quelle for k in liste.tiles] == ["CLIENT", "SCHEDULER"]  # #147


def test_the_oneshots_local_runs_are_still_listed():
    """Was gelaufen ist, bleibt sichtbar — es fehlt nur der Platz zum Bedienen.
    Ein Lauf, den niemand mehr findet, waere schlimmer als eine Kachel zuviel.

    Seit m.rau/bibi#146 ist die Kachel gesperrt statt weg; die Aussage dieses
    Tests ist davon unberuehrt — sie gilt der **Liste**.
    """
    liste = _liste(client_slot={"id": "c1", "status": "complete"},
                   client_runs=[{"run_id": "x:1", "status": "complete",
                                 "finished_at": 1_754_000_000.0}],
                   oneshot=True)
    assert [k.disabled for k in liste.tiles] == ["oneshots never run locally"]
    assert [r["run_id"] for r in liste.runs] == ["x:1"]


# ── Der App-Link im Job Detail (m.rau/bibi#145) ────────────────────────────


def test_an_app_job_offers_the_link_to_its_app():
    """**Befund m.rau, 2026-08-05:** bei einem Job vom Typ `app` wird die URL
    des Dienstes nicht angezeigt.

    Und zwar nie — nicht erst, seit der Scheduler den Job kennt. Das Ticket
    vermutete die Ursache in `_local_job_meta_line(include_app_link=job is
    None)`, aber diese Bedingung sitzt in `jobs_detail_live_fragment()`, dem
    **alten** Detail unter `/-/ui/jobs/detail/…`. Der bibi5-Screen ruft sie gar
    nicht auf: er rendert Kopf, Kacheln und Lauf-Liste — und keine davon führte
    je einen App-Link.

    Der Link gehört in den **Kopf**, nicht in eine Kachel: `app_port` steht im
    MD-Frontmatter und gilt für den Job, nicht für einen Lauf. Eine Kachel
    beschreibt einen Slot und wäre der falsche Ort für etwas, das auch ohne
    jeden Lauf gilt.

    **Umgezogen mit `#104`** (2026-08-09): der Link steht nicht mehr im Kopf,
    sondern in den Slot-Kacheln. Die Anforderung ist dieselbe geblieben — eine
    App stellt ihren Link bereit —, nur der Ort hat sich geändert, weil erst
    die Kachel den Knoten kennt, auf dem die App läuft. Der Test prüft deshalb
    weiter die Fähigkeit und nicht mehr die alte Stelle.
    """
    from bibi.controller import render as r
    liste = _liste(scheduler_slot={"status": "complete"}, app_port=8899)
    html = r.job_tiles_fragment(liste.tiles, now=1_754_100_000.0,
                                slug="burndown-app", job_uid="u1")
    assert 'href="http://sarasate:8899/"' in html, (
        "kein App-Link an der Kachel des ausführenden Knotens")


def test_a_plain_job_offers_no_app_link():
    """Die Gegenprobe: der Link haengt an `app_port`, nicht am Screen. Ohne
    Port darf dort nichts stehen — ein Link ins Leere ist schlechter als
    keiner."""
    from bibi.controller import render
    html = render.job_detail_page_v5(
        slug="normal", now=1_754_100_000.0,
        spec={"slug": "normal", "kind": "job", "schedule": "adhoc"})
    assert "APP" not in html.replace("APPLICATION", "")


def test_the_app_link_uses_the_public_host_not_localhost():
    """Auf einem Client zeigt `localhost` auf den falschen Rechner, sobald
    jemand das FE aus dem Tailnet aufruft. `config.public_host()` ist die
    Quelle, die auch jedes andere Fragment benutzt.

    **`#104` hat gezeigt, dass `public_host()` dafür die falsche Quelle war:**
    es ist der Knoten, der die *Seite rendert*, nicht der, auf dem die App
    *läuft*. Die Kachel kennt den richtigen über `Tile.host`. Der Test prüft
    seither, dass jede Seite ihren eigenen Knoten verlinkt — die schärfere
    Fassung derselben Frage.
    """
    from bibi.controller import render as r
    liste = _liste(scheduler_slot={"status": "complete"},
                   client_slot={"status": "complete"}, app_port=8899)
    html = r.job_tiles_fragment(liste.tiles, now=1_754_100_000.0,
                                slug="burndown-app", job_uid="u1")
    assert 'href="http://sarasate:8899/"' in html
    assert 'href="http://Mac.fritz.box:8899/"' in html


# ── Der Client-Slug ohne Zeile (m.rau/bibi#87) ──────────────────────────────


def test_a_client_tile_appears_for_a_locally_known_job_without_a_row():
    """**Der Rot-Schritt von `#87`.**

    Eine Kachel entstand nur aus einem *Slot*, und den Client-Slot beschaffte
    `_job_lauf_liste()` aus zwei Quellen: der gepinnten Zeile eines
    `bibi-ctrl run`-Laufs oder — als Rückfall — der Basis-Slug-Zeile. Basis-
    Zeilen legt aber ausschliesslich `job_db.rescan()` an, und der Rescanner
    haengt an der `scheduler`-Rolle. **Ein reiner Client bekommt deshalb nie
    eine**, und der Rückfall greift dort nur fuer Slugs aus einer Zeit, in der
    der Knoten selbst Scheduler war.

    Damit kehrte sich die Begruendung des Rückfalls in ihr Gegenteil: er ist
    dafuer da, dass ein Client, auf dem noch nie etwas lief, eine Kachel hat —
    und genau dieser Fall trat dauerhaft nicht ein. Live gemessen am Mac
    (`v0.7.9`, 2026-08-09): `BrowserCI` hatte keine CLIENT-Kachel, waehrend
    drei aeltere Jobs eine hatten, weil dort einmal ein `/run`-Lauf gepinnt
    wurde.

    Das Henne-Ei dahinter: START ruft `/-/run`, also genau das, was
    `bibi-ctrl run` tut. Der Knopf erschien erst, **nachdem** man den Job
    einmal ohne ihn gestartet hatte.
    """
    kacheln = _liste(client_slug="BrowserCI").tiles
    klient = [k for k in kacheln if k.quelle == "CLIENT"]
    assert len(klient) == 1, "die lokal bekannte MD bekommt keine Kachel (#87)"
    assert klient[0].status == ""
    assert {v.value for v in klient[0].aktionen} == {"start"}, (
        "eine Kachel ohne Zeile kann nur gestartet werden — KILL und RESET "
        "haetten nichts, worauf sie wirken")


def test_that_tile_carries_the_slug_so_start_can_reach_it():
    """Ohne Kennung kein Knopf: `_slot_leiste()` zeichnet ein Verb nur mit
    `job_id`. Der Client-Slot ist ohnehin slug-basiert — alle vier Verben
    gehen ueber `/-/run` bzw. `/-/run/live/*`, und die ID ist dort nur ein
    Umweg, um an den Slug zu kommen."""
    klient = [k for k in _liste(client_slug="BrowserCI").tiles
              if k.quelle == "CLIENT"][0]
    assert klient.slot.get("id") == "BrowserCI"


def test_without_a_local_md_there_is_still_no_client_tile():
    """Die Gegenprobe, und sie traegt die Regel aus §5.1.1.

    Ohne sie waere der Test oben auch dann gruen, wenn **jeder** Job eine
    CLIENT-Kachel bekaeme — und der Screen boete einen Platz an, den es nicht
    gibt. Ein Job, dessen MD hier nicht liegt, kann hier auch nicht laufen."""
    assert [k.quelle for k in _liste(scheduler_slot={"row_status": "pending"}).tiles] \
        == ["SCHEDULER"]


def test_a_real_row_still_wins_over_the_bare_slug():
    """Existiert eine Zeile, zaehlt sie — der Slug ist der Rückfall, nicht der
    Vorrang. Sonst verlöre eine Kachel ihren Zustand, sobald der Slug bekannt
    ist."""
    klient = [k for k in _liste(client_slot={"status": "error"},
                                client_slug="BrowserCI").tiles
              if k.quelle == "CLIENT"][0]
    assert klient.status == "error"
    assert {v.value for v in klient.aktionen} == {"start", "reset"}


def test_a_oneshot_stays_locked_even_when_the_md_is_local():
    """Ein Oneshot hat keinen lokalen Platz (§5.1.1) — daran aendert eine
    lokal liegende MD nichts. Die Sperre aus `#146` muss den neuen Weg
    ueberleben, sonst bietet der Screen einen Start an, den `run_pinned()`
    zwar ausfuehrte, den das Zustandsmodell aber nicht kennt."""
    klient = [k for k in _liste(client_slug="einmal", oneshot=True).tiles
              if k.quelle == "CLIENT"][0]
    assert klient.disabled == "oneshots never run locally"
    assert klient.aktionen == frozenset()


def test_the_v5_detail_names_an_app_an_app():
    """**Der Rot-Schritt der vierten Fundstelle von `#96`** — die das Ticket
    nicht kannte, weil sie live gefunden wurde statt im Code.

    `job_detail_page_v5()` liest `spec["kind"]` roh. `kind` ist aber seit
    PLAN-10 (Unified Job Model) **immer** `"job"` und trägt keine Information
    mehr — das sagt `_effective_sched_type()` in seinem eigenen Docstring.
    Die Seite schreibt deshalb `job` direkt neben den `[APP]`-Link, den sie
    aus demselben `spec` gerade gebaut hat.

    Live am 2026-08-09 auf `/-/jobs/8f2d8fd7…`:

        jd-meta">job &middot; on_demand<
        class="cta" href="http://localhost:9110/"

    **Warum der bestehende Test das nicht sah:** `test_an_app_job_offers_the_
    link_to_its_app` gibt `kind: "app"` im Spec mit — einen Wert, den die
    echte DB nicht mehr liefert. Ein Test, dessen Aufbau einen Zustand
    herstellt, den es nicht gibt, ist auch dann grün, wenn er nichts prüft
    (die `#88`-Lehre). Dieser Test lässt `kind` deshalb weg.
    """
    from bibi.controller import render
    html = render.job_detail_page_v5(
        slug="burndown-app", now=1_754_100_000.0,
        spec={"slug": "burndown-app", "schedule": "on_demand",
              "app_port": 9110})
    meta = [z for z in html.split('jd-meta">') if z][1][:40]
    assert "app" in meta, (
        f"der Typ sagt {meta!r} statt 'app' — neben dem App-Link aus "
        f"demselben Spec (#96)")


# ── Der App-Link gehört an den Knoten, der die App fährt (#104) ─────────────


def test_each_tile_links_the_app_on_its_own_node():
    """**Der Rot-Schritt von `#104`.**

    Der App-Link trug bisher `config.public_host()` — den Hostnamen des
    Knotens, der die *Seite rendert*, nicht den, auf dem die App *läuft*.
    Live am 2026-08-09: dieselbe App, im Mac-FE `localhost:9110` (dort läuft
    nichts), im Client-FE `sarasate…:9110` (richtig, aber nur weil Betrachter
    und Ausführender zufällig derselbe Rechner sind).

    **Die Kachel kann es besser, und zwar ohne neue Angabe:** sie beschreibt
    *einen* Slot und trägt dessen Knoten längst in `Tile.host` — genau die
    Information, die `public_host()` per Bauart nicht hat. Entscheidung m.rau,
    2026-08-09: *„in die Kacheln der Detailseite"*.
    """
    from bibi.controller import render as r
    liste = _liste(scheduler_slot={"status": "complete"},
                   client_slot={"status": "complete"},
                   app_port=9110)
    html = r.job_tiles_fragment(liste.tiles, now=1_754_100_000.0,
                                slug="burndown-app", job_uid="u1")
    assert 'href="http://sarasate:9110/"' in html, (
        "die Scheduler-Kachel verlinkt die App nicht auf ihrem Knoten (#104)")
    assert 'href="http://Mac.fritz.box:9110/"' in html, (
        "die Client-Kachel verlinkt die App nicht auf ihrem Knoten (#104)")


def test_a_tile_without_an_app_port_has_no_link():
    """Die Gegenprobe: der Link hängt an `app_port`. Ein Job ohne Port bekommt
    keinen — ein Link ins Leere ist schlechter als keiner."""
    from bibi.controller import render as r
    liste = _liste(scheduler_slot={"status": "complete"})
    html = r.job_tiles_fragment(liste.tiles, now=1_754_100_000.0,
                                slug="normal", job_uid="u2")
    assert 'class="tile' in html, "Absicherung: es gibt überhaupt eine Kachel"
    assert "http://sarasate:" not in html


def test_the_jobs_table_names_the_port_without_linking_it():
    """**Die Rücknahme meiner eigenen Regression aus `#96`.**

    `#96` hat die Type-Zelle korrekt verlinkt — und damit einen bestehenden
    Fehler vervielfacht: im Mac-FE standen danach fünf tote Links
    (`localhost:9100`–`9110`). Vor `#96` stand dort schmuckloser Text und der
    Fehler steckte nur im `[APP]`-CTA der Detailseite.

    Der Typ bleibt richtig — das war der unstrittige Teil von `#96`. Der Link
    zieht in die Kacheln, wo der Knoten bekannt ist.
    """
    from bibi.controller import render as r
    zeilen = jobs_view.build_rows(
        local=[{"slug": "app1", "schedule": "0 * * * *", "payload": "echo hi",
                "repo_path": "case/x/app1.md", "app_port": 9100}],
        scheduler=[], journal=[], now=1_754_100_000.0)
    html = r.jobs_screen(zeilen, now=1_754_100_000.0, public_host="Mac.fritz.box")
    assert "app :9100" in html, "der Typ samt Port bleibt sichtbar (#96)"
    assert "href=\"http://Mac.fritz.box:9100/\"" not in html, (
        "die Jobs-Tabelle verlinkt die App auf den Betrachter-Host (#104)")


# ── Die Output-Box eines claude-Laufs (#99) ─────────────────────────────────


def _claude_stream(*chunks, kind="thinking"):
    """Roh-Events wie sie `--include-partial-messages` liefert: ein
    `content_block_start`, dann Token-Deltas."""
    import json as _json
    evs = [{"t": 1.0, "s": "out", "line": _json.dumps(
        {"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": kind}}})}]
    feld = "thinking" if kind == "thinking" else "text"
    for i, c in enumerate(chunks):
        evs.append({"t": 1.0 + i, "s": "out", "line": _json.dumps(
            {"type": "stream_event", "event": {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": f"{kind}_delta", feld: c}}})})
    return evs


def test_the_run_output_joins_token_deltas():
    """**Der Rot-Schritt von `#99`.**

    Live am 2026-08-09, ein `Witz`-Lauf aufgeklappt über `[show]`:

        Der Benut
        zer möchte
        , dass ich:

    Umbruch mitten im Wort — jeder Token-Delta wurde eine eigene Zeile.

    **Alle drei Bausteine dagegen sind gebaut:** `output_format.format_events()`
    typisiert die Deltas, `_merge_deltas()` fügt sie zusammen, `_event_line()`
    setzt `thinking` per CSS-Klasse ab. `output_block()` verbindet die drei zu
    genau dieser Antwort. Die v5-Route `screen_job_run_output()` benutzt keins
    davon — sie baut ihr eigenes `"\\n".join(...)` über die Roh-Events.
    """
    from bibi.controller import render as r
    from bibi.daemon import output_format

    evs = output_format.format_events(
        _claude_stream("Der Benut", "zer möchte", ", dass ich:"), "claude")
    html = r.output_block(evs, "claude")

    assert "Der Benutzer möchte, dass ich:" in html, (
        "die Token-Deltas werden nicht zusammengefügt (#99)")


def test_thinking_is_set_apart_and_collapsible():
    """m.raus Vorgabe zu `#99`: *„Streaming, zurück gesetzt, **etwas** im
    Hintergrund · einklappbar"*.

    Die ersten beiden Punkte trägt `.term .thinking` (gedimmt, kursiv) — schon
    vorhanden, nur nie angewendet. Der dritte braucht eine eigene Struktur:
    zusammenhängendes `thinking` steht in einem `<details>`, damit es
    zusammengefaltet erreichbar bleibt statt weggeworfen zu werden.
    """
    from bibi.controller import render as r
    from bibi.daemon import output_format

    evs = output_format.format_events(
        _claude_stream("Ich überlege", " kurz."), "claude")
    html = r.output_block(evs, "claude")

    assert "<details" in html, "thinking ist nicht einklappbar (#99)"
    assert 'class="thinking"' in html, "thinking ist nicht abgesetzt (#99)"


def test_plain_output_stays_unfolded():
    """Die Gegenprobe: gewöhnlicher Output wird nicht eingeklappt — nur das
    Denken tritt zurück, das Ergebnis nicht."""
    from bibi.controller import render as r
    from bibi.daemon import output_format

    evs = output_format.format_events(
        _claude_stream("fertig", kind="text"), "claude")
    html = r.output_block(evs, "claude")
    assert "fertig" in html
    assert "<details" not in html


# ── Die Zusammenfassung sagt, was darunter steht (#107) ─────────────────────


def test_the_fold_counts_lines_not_events():
    """**Der Rot-Schritt von `#107`, erste Haelfte.**

    Live an einem `Witz`-Lauf: der Block sagte `thinking (1 line)` und zeigte
    fuenfzehn. Beides war fuer sich richtig — `_merge_deltas()` fuegt die
    Token-Deltas zu **einem** Event zusammen, und dessen `line` traegt die
    Umbrueche; gezaehlt wurden die Events.

    Fuer einen zusammenhaengenden Denkabschnitt stand dort deshalb **immer**
    `1 line`, unabhaengig von seiner Laenge. Eine Zusammenfassung, die dem
    widerspricht, was sichtbar darunter steht, ist schlimmer als keine.
    """
    from bibi.controller import render as r

    evs = [{"t": 1, "s": "thinking", "line": "erste Zeile"},
           {"t": 1, "s": "thinking", "line": "\nzweite Zeile", "delta": True}]
    html = r.output_block(evs, "claude")
    assert "(2 lines)" in html, (
        "die Zusammenfassung zaehlt Events statt Zeilen (#107)")


def test_a_single_line_stays_singular():
    """Die Gegenprobe: eine Zeile bleibt eine — der Zaehler darf die Mehrzahl
    nicht zur Regel machen, nur weil er jetzt anders zaehlt."""
    from bibi.controller import render as r

    html = r.output_block([{"t": 1, "s": "thinking", "line": "nur ein Gedanke"}],
                          "claude")
    assert "(1 line)" in html


def test_an_empty_event_gets_no_timestamp_of_its_own():
    """**Der Rot-Schritt von `#107`, zweite Haelfte.**

    Im selben Lauf bestanden zwei von drei Zeilen **nur** aus der Uhrzeit: der
    Formatter liefert Events mit leerem `line`, und `_event_line()` rendert
    fuer jedes brav ein Praefix samt leerem `<span>`.

    Im Rohtext fiel das nicht auf, weil dort eine leere Zeile eine leere Zeile
    war. Mit Praefix wird daraus eine Zeile, die aussieht, als haette zu diesem
    Zeitpunkt etwas stattgefunden — der Zeitstempel behauptet ein Ereignis.
    """
    from bibi.controller import render as r

    html = r.output_block([{"t": 1, "s": "out", "line": ""},
                           {"t": 1, "s": "out", "line": "echt"}], "job")
    assert "echt" in html
    assert html.count('class="lts"') == 1, (
        "eine leere Zeile traegt eine Uhrzeit ohne Text (#107)")
