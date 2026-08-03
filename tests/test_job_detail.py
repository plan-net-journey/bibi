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
    Slug genuegt. Ohne das waere der Archive-Screen ein Weg ins Nichts."""
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


@pytest.fixture
def client(team_repo):
    from fastapi.testclient import TestClient

    from bibi.daemon import roles
    from bibi.daemon.app import create_app
    app = create_app(roles.resolve({"controller"}), controller_client=_FakeClient())
    with TestClient(app) as c:
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
    (FE-Spezifikation §1) — sie sind der Rahmen, nicht Screen-Inhalt."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    for teil in ("Feed", "Jobs", "Archive", "Nodes", "Live", "Log"):
        assert teil in text
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


def _grp(**kw):
    from bibi.controller import jobs_view
    basis = dict(scheduler_slot=None, local_slot=None,
                 scheduler_runs=[], local_runs=[],
                 scheduler_host="sarasate", local_host="Mac.fritz.box")
    basis.update(kw)
    return jobs_view.build_groups(**basis)


def test_a_group_is_missing_when_there_is_no_slot_at_all():
    """Der Unterschied zwischen *kein Platz* und *freier Platz* (§5.1): eine
    Gruppe fehlt genau dann, wenn diese Seite den Job gar nicht kennt — nicht,
    wenn ihr Slot gerade leer ist. Das ersetzt das ausgegraute Control."""
    gruppen = _grp(scheduler_slot={"status": "pending"})
    assert [g.quelle for g in gruppen] == ["SCHEDULER"]


def test_both_groups_appear_when_both_sides_know_the_job():
    gruppen = _grp(scheduler_slot={"status": "pending"},
                   local_slot={"status": "error"})
    assert [g.quelle for g in gruppen] == ["SCHEDULER", "LOCAL"]


def test_a_group_survives_an_empty_slot_if_the_side_knows_the_job():
    """`adhoc`: die Seite kennt ihn, gerade ist nichts los. `pending` ohne
    `next` ist ein *freier* Platz, kein fehlender."""
    gruppen = _grp(scheduler_slot={"status": "pending", "next_fire_at": None})
    assert len(gruppen) == 1 and gruppen[0].slot["status"] == "pending"


def test_the_slot_carries_its_own_actions():
    """Die vier Knopf-Gesichter kommen aus `slot.actions()` — dieselbe Quelle
    wie die Engine, damit Oberflaeche und Zustandsmaschine nicht auseinander
    laufen koennen."""
    from bibi.schedule import slot
    g = _grp(scheduler_slot={"status": "error"})[0]
    assert g.aktionen == slot.actions("error")
    assert slot.Verb.START in g.aktionen and slot.Verb.KILL not in g.aktionen


def test_a_consumed_oneshot_offers_no_action_bar():
    """`done` ist die Ausnahme von „ausgegraut statt ausgeblendet": ein
    verbrauchter Slot zeigt keine toten Knoepfe, das Fehlen der Leiste ist
    selbst die Aussage."""
    from bibi.schedule import slot
    g = _grp(scheduler_slot={"status": slot.DONE})[0]
    assert g.aktionen == frozenset()


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


def test_the_group_counts_all_runs_not_just_the_loaded_page():
    """`45 runs` in der Kopfzeile meint die Gesamtzahl — sonst zaehlte die
    Zahl mit jedem LOAD MORE hoch und saehe aus wie Zuwachs."""
    g = _grp(scheduler_slot={"status": "pending"},
             scheduler_runs=[{"finished_at": 1.0}], scheduler_total=45)[0]
    assert g.gesamt == 45


def test_the_page_shows_both_groups_with_their_slots(client):
    """Der Screen im Bild (§5.1): je Quelle eine faltbare Gruppe, der Slot in
    der Kopfzeile, die Laeufe darunter."""
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
    assert "LOCAL" in text
    assert "slot:" in text
    assert "running" in text


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
    assert "[KILL]" in text          # verfuegbar
    assert "&middot;START&middot;" in text or "·START·" in text  # ausgegraut


def test_an_empty_run_list_says_what_to_do(client):
    """Leerer Zustand mit Handlungsanweisung (Umbauplan §4): der haeufigste
    erste Eindruck eines neuen Jobs ist eine leere Liste."""
    c, _ = _md_job(client)
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}").text
    assert "No runs yet" in text


def test_a_side_that_has_runs_but_no_slot_still_gets_a_group():
    """Live gefunden (2026-08-03): `EngineCI` hat lokale Laeufe, aber keinen
    lokalen Slot — `bibi-ctrl run` legt Pseudo-Jobs mit Zufallssuffix an
    (`EngineCI-46ec57c7`), der Basis-Slug hat dort keine Zeile. Die Bedingung
    "kein Slot ⇒ keine Gruppe" schluckte damit die Laeufe mit.

    §5.1 sagt: eine Gruppe fehlt, wenn die Seite den Job **nicht kennt** —
    "keine MD, **nie gelaufen**". Wer gelaufen ist, ist bekannt. Die Gruppe
    erscheint also, nur ohne Slot-Zustand und ohne Knoepfe: es gibt keinen
    Platz zu bedienen, aber sehr wohl etwas zu zeigen."""
    gruppen = _grp(scheduler_slot={"status": "pending"},
                   local_runs=[{"finished_at": 1_754_000_000.0, "status": "complete"}])
    assert [g.quelle for g in gruppen] == ["SCHEDULER", "LOCAL"]
    lokal = gruppen[1]
    assert lokal.slot == {}          # kein Platz
    assert lokal.aktionen == frozenset()
    assert len(lokal.runs) == 1      # aber Historie


def test_a_side_with_neither_slot_nor_runs_stays_hidden():
    """Die Gegenprobe — sonst zeigte jeder Job zwei Gruppen, davon eine leer."""
    assert [g.quelle for g in _grp(scheduler_slot={"status": "pending"})] == ["SCHEDULER"]


def test_the_scheduler_row_uses_row_status_not_status():
    """Live gefunden: die Scheduler-Zeile aus `/-/schedule` heisst `row_status`,
    nicht `status` — `status` ist dort `None`. Ein `or "pending"` kaschierte
    das und zeigte einen Zustand, den niemand gemeldet hatte: geraten statt
    gelesen, und im Bild nicht von einer echten Reservierung zu unterscheiden.
    """
    from bibi.controller import jobs_view
    g = jobs_view.build_groups(
        scheduler_slot={"slug": "x", "row_status": "complete", "status": None},
        local_slot=None, scheduler_runs=[], local_runs=[])[0]
    assert g.slot_status == "complete"


def test_a_slot_without_any_status_is_not_invented():
    """Kein Rateschritt: fehlt jeder Zustand, sagt der Screen das, statt
    `pending` zu behaupten."""
    from bibi.controller import jobs_view
    g = jobs_view.build_groups(
        scheduler_slot={"slug": "x"}, local_slot=None,
        scheduler_runs=[], local_runs=[])[0]
    assert g.slot_status is None
    assert g.aktionen == frozenset()


# ── Output ausklappen statt Unterseite (FE-Spezifikation §5.4) ───────────────


def _seed_run(root, slug: str = "EngineCI", *, out: str = "hallo welt") -> int:
    """Eine archivierte Journal-Zeile mit Output auf Platte."""
    from bibi.daemon import job_db
    from bibi.wrapper import output as out_mod
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
            (f"{slug}:1:a", slug, "job", "complete", 1.0, 2.0, 0, rel, 2.0))
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


# ── Archive (FE-Spezifikation §6) ────────────────────────────────────────────


def test_archive_lists_runs_not_jobs(client):
    """Der Archive-Screen fuehrt **Laeufe**, nicht Jobs — eine Zeile je Lauf.
    Das Journal-Segment des Jobs-Screens ist deshalb keine Dopplung, sondern
    seine Aggregation."""
    c, root = _md_job(client)
    _seed_run(root, "EngineCI")
    _seed_run(root, "daily-digest")
    text = c.get("/-/archive").text
    assert "EngineCI" in text and "daily-digest" in text
    assert "SLUG" in text  # die zusaetzliche Spalte gegenueber §5.3


def test_archive_links_each_run_back_to_its_job(client):
    """`SLUG` verlinkt auf Job Detail — sonst waere das Archiv eine Sackgasse:
    man saehe, dass etwas lief, aber nicht, was es ist."""
    c, root = _md_job(client)
    _seed_run(root, "EngineCI")
    text = c.get("/-/archive").text
    assert f'/-/jobs/{job_uid("EngineCI")}' in text


def test_archive_reaches_runs_whose_job_is_gone(client):
    """**Der eigentliche Zweck** (§6): ein Job, dessen MD geloescht wurde, hat
    keine Zeile mehr im Jobs-Screen — seine Laeufe stehen aber weiter im
    Journal. Ohne das Archiv waeren sie unerreichbar."""
    c, root = _md_job(client)
    _seed_run(root, "Runner-Container")  # keine MD, nur Historie
    text = c.get("/-/archive").text
    assert "Runner-Container" in text
    assert f'/-/jobs/{job_uid("Runner-Container")}' in text


def test_archive_states_its_reach(client):
    """Die Reichweite steht im Bild, nicht nur im Knopf: ein `LOAD MORE`, das
    nichts mehr laedt, muss sich von „geloescht" unterscheiden lassen. Was
    nicht im Archiv steht, ist nicht verlorengegangen, sondern weggepruned."""
    c, root = _md_job(client)
    _seed_run(root)
    text = c.get("/-/archive").text
    assert "showing" in text and "pruned after" in text


def test_an_empty_archive_says_what_it_means(client):
    c, _ = _md_job(client)
    assert "No runs" in c.get("/-/archive").text


def test_archive_shows_the_base_job_of_a_pinned_run(client):
    """Live gefunden: die SLUG-Spalte fuehrte `calendar-transfer-0ea75cbc`
    neben `calendar-transfer`. Ein gepinnter Lauf traegt einen Slug mit
    Zufallssuffix (`run_pinned()`), gehoert aber zum Basis-Job — sonst zerfaellt
    er in so viele Eintraege, wie er lokale Laeufe hatte (live: 252 Pseudo-Slugs
    fuer 33 echte Jobs), und jeder Link fuehrt auf eine eigene Detailseite fuer
    denselben Job.

    Der Suffix wird nur abgeschnitten, wenn `pinned_host` gesetzt ist — genau
    die Regel, die `bus.bucket_slug()` und `job_db.list_journal()` schon
    anwenden. Ohne diesen Diskriminator waere es Raten am Namen."""
    from bibi.controller import render
    html = render.archive_page_v5(laeufe=[
        {"slug": "calendar-transfer-0ea75cbc", "pinned_host": "Mac",
         "finished_at": 1_754_000_000.0, "status": "complete"},
    ], now=1_754_000_100.0)
    assert ">calendar-transfer<" in html
    assert "0ea75cbc" not in html


def test_archive_leaves_a_real_slug_alone_even_if_it_looks_suffixed(client):
    """Die Gegenprobe: ohne `pinned_host` wird nichts abgeschnitten. Ein echter
    Slug darf auf acht Hex-Zeichen enden — `20260728.at-150738-81ec` tut es
    fast, und ein Oneshot-Slug traegt seinen Suffix mit Bedeutung."""
    from bibi.controller import render
    html = render.archive_page_v5(laeufe=[
        {"slug": "daily-digest-deadbeef", "pinned_host": None,
         "finished_at": 1_754_000_000.0, "status": "complete"},
    ], now=1_754_000_100.0)
    assert ">daily-digest-deadbeef<" in html


# ── LOAD MORE (FE-Spezifikation §5.3/§6) ─────────────────────────────────────


def test_the_archive_pages_instead_of_scrolling_endlessly(client):
    """Infinite Scrolling ist bei diesen Mengen unbrauchbar: `gmail-transfer`
    allein hat 1064 Laeufe im 5000er-Journalfenster. Tagesgruppen plus
    `LOAD MORE` geben stattdessen einen Anker und ein Ende."""
    c, root = _md_job(client)
    for i in range(3):
        _seed_run(root, f"job{i}")
    text = c.get("/-/archive?limit=2").text
    assert "LOAD MORE" in text
    assert "offset=2" in text


def test_load_more_disappears_when_everything_is_shown(client):
    """Ein Knopf, der nichts mehr laedt, ist schlimmer als keiner — er sieht
    aus wie ein Weg. Genau die Unterscheidung, die §6 fuer die Reichweite
    verlangt: was fehlt, ist weggepruned und nicht ungeladen."""
    c, root = _md_job(client)
    _seed_run(root)
    assert "LOAD MORE" not in c.get("/-/archive?limit=50").text


def test_the_next_page_continues_where_the_first_ended(client):
    """Kein Ueberlappen und kein Loch: Seite zwei beginnt beim ersten Lauf, den
    Seite eins nicht mehr trug."""
    c, root = _md_job(client)
    for i in range(4):
        _seed_run(root, f"p{i}")
    erste = c.get("/-/archive?limit=2").text
    zweite = c.get("/-/archive?limit=2&offset=2").text
    drin = lambda t: {s for s in ("p0", "p1", "p2", "p3") if f">{s}<" in t}
    assert drin(erste) and drin(zweite)
    assert not (drin(erste) & drin(zweite))


def test_the_run_list_of_a_job_pages_too(client):
    c, root = _md_job(client)
    for _ in range(3):
        _seed_run(root, "EngineCI")
    text = c.get(f"/-/jobs/{job_uid('EngineCI')}/runs?limit=2").text
    assert "LOAD MORE" in text
