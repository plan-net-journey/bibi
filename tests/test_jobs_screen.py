"""Der Jobs-Screen (bibi5, FE-Spezifikation §4).

Der zentrale Screen: er ersetzt den früheren Schedules-Tab und führt Scheduler-
und Client-Sicht zusammen. Drei Bänder, eine Zeile je Slug, zwei Zustandsblöcke
nebeneinander.

Die Bänder sind keine Sortierordnung, sondern eine Klassifikation — und der
Grund für Bänder statt einer flachen Liste ist die gestaffelte Filtermenge:
`TYPE` und `STATUS` wirken überall, die drei Journal-Filter nur im dritten
Band. Eine gestaffelte Filtermenge braucht einen Ort je Staffel.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.controller.jobs_view import Segment, build_rows
from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.schedule.models import job_uid


class _FakeClient:
    def __init__(self, status: dict, *, run_journal: list[dict] | None = None,
                 run_live: dict[str, dict] | None = None) -> None:
        self._status = status
        self._run_journal = run_journal or []
        self._run_live = run_live or {}

    def status(self) -> dict:
        return self._status

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def run_journal(self, **_):
        return self._run_journal

    def run_live_list(self):
        return self._run_live

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(status: dict, *, run_journal: list[dict] | None = None,
              run_live: dict[str, dict] | None = None):
        return create_app(
            roles.resolve({"controller"}),
            controller_client=_FakeClient(status, run_journal=run_journal,
                                          run_live=run_live))
    return _make

NOW = 1_000_000.0


def _md(slug, schedule="0 * * * *", **kw):
    return {"slug": slug, "schedule": schedule, "payload": "echo hi",
            "repo_path": f"case/x/{slug}.md", **kw}


def _zeilen(**kw):
    return build_rows(local=kw.pop("local", []), scheduler=kw.pop("scheduler", []),
                      journal=kw.pop("journal", []), now=NOW, **kw)


# ── Die drei Bänder ────────────────────────────────────────────────────────


def test_both_bands_are_always_there():
    """Auch leer — sonst verschiebt sich das Layout, je nachdem was gerade
    existiert, und man sucht ein Band, das nur gerade nichts enthält.

    **Zwei seit #38**, nicht mehr drei: das JOURNAL-Segment steht auf einem
    eigenen Screen. Dass es hier nicht mehr auftaucht, prüft
    `test_journal_screen.py` — dort steht auch der gemessene Grund dafür."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    for band in ("SCHEDULE", "ADHOC"):
        assert band in html, band


def test_each_band_carries_its_count():
    html = render.jobs_screen(
        _zeilen(local=[_md("a"), _md("b"), _md("c", schedule="adhoc")]), now=NOW)
    assert re.search(r"SCHEDULE\D*2", html)
    assert re.search(r"ADHOC\D*1", html)


def test_rows_land_in_their_band():
    html = render.jobs_screen(
        _zeilen(local=[_md("geplant"), _md("gerufen", schedule="adhoc")]), now=NOW)
    vor_adhoc = html.split("ADHOC", 1)[0]
    assert "geplant" in vor_adhoc
    assert "gerufen" not in vor_adhoc


# ── Zwei Zustandsblöcke in einer Zeile ─────────────────────────────────────


def test_one_row_carries_both_sides():
    html = render.jobs_screen(
        _zeilen(local=[_md("EngineCI")],
                scheduler=[{"slug": "EngineCI", "status": "complete",
                            "schedule": "0 * * * *"}],
                local_runs={"EngineCI": {"status": "error", "exec_runtime": 231.9}}),
        now=NOW)
    assert html.count(">EngineCI<") == 1, "ein Slug, eine Zeile"
    assert "complete" in html and "error" in html


def test_the_two_blocks_are_labelled():
    """Ohne Beschriftung wüsste niemand, welche Spalte welche Seite meint —
    und die beiden zeigen regelmäßig Verschiedenes.

    Beide heißen seit m.rau/bibi#147 überall gleich: `CLIENT` und `SCHEDULER`.
    Vorher sagte der Header `CLIENT`, die Tabelle `LOCAL` — zwei Wörter für
    dieselbe Sache, und `LOCAL` ist obendrein nur aus Sicht des Betrachters
    lokal, während `CLIENT` die Herkunft benennt.
    """
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    assert "SCHEDULER" in html and "CLIENT" in html
    assert "LOCAL" not in html


# ── Die LOCAL-Spalte liest beide Speicher ──────────────────────────────────


def _zeile_von(html: str, slug: str) -> str:
    """Die eine ``<tr>`` dieses Slugs — nicht die ganze Seite.

    Die vierte Lehre aus m.rau/bibi#131: ein ``"X" in html`` prüft die Seite und
    nicht das Element. ``running`` steht auch im Filter-Knopf der Kopfleiste,
    ein Test darauf wäre grün, ohne dass die Zelle je gefüllt würde.
    """
    # `<tr` statt `<tr>`: die Zeile traegt seit #67 ein `data-row`, an dem der
    # Zell-Diff sie ueber einen Swap hinweg wiedererkennt.
    for tr in re.findall(r"<tr[ >].*?</tr>", html, re.S):
        if f'title="{slug}"' in tr:
            return tr
    raise AssertionError(f"keine Zeile für {slug!r} im Screen")


def _mit_job_md(root: Path, slug: str) -> None:
    ordner = root / "vault" / "case" / "x"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{slug}.md").write_text(
        f"---\nslug: {slug}\nschedule: adhoc\njob: sleep 9000\n---\n", encoding="utf-8")


def test_a_locally_running_job_shows_its_state(app_with, team_repo: Path):
    """**Der laufende lokale Lauf gehört in die LOCAL-Spalte** (FE §4.3: dort
    steht der *„Zustand in der lokalen Job-DB"*, und das ist der Slot).

    Live gefunden am 2026-08-04, unmittelbar nachdem der Mac-Client auf bibi5
    umgestellt war: `burndown-app` lief seit einem Tag lokal, Job Detail zeigte
    `running · 1d 9h` und `client 1` — der Jobs-Screen zeigte in derselben
    Zeile `—`. Zwei Screens, dieselbe Tatsache, zwei Antworten.

    Die Ursache ist **eine Quelle zu wenig**: `_local_run_status()` liest nur
    `run_journal()`, also archivierte Läufe. Nach A1/A2 (Zustandsmodell §3) ist
    ein laufender Lauf gerade *nicht* archiviert, er steht im Slot. Ein Job,
    dessen einziger lokaler Lauf gerade läuft, sah deshalb aus wie einer, der
    nie lokal lief — und was die Spalte stattdessen zeigte, war bei
    `hitl-test-app` ein `killed` vom 4. Juli.

    Der passende Helfer war die ganze Zeit da: `client.run_live_list()`, dessen
    Docstring ihn wörtlich *„für die Jobs-Liste"* nennt. Der bibi4-Pfad
    `_jobs_data()` benutzt ihn auch; beim v5-Umbau ist er nicht mitgekommen.
    Das ist die dritte Lehre in neuem Gewand — die Journal-Hälfte wurde auf
    ihren neuen Träger gestellt, die Live-Hälfte mit dem alten gelöscht.
    """
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]},
                   run_live={"laeuft": {"id": "abc", "started_at": NOW - 60,
                                        "status": "running"}})
    with TestClient(app) as c:
        html = c.get("/-/jobs", headers={"accept": "text/html"}).text
    assert "<td>running</td>" in _zeile_von(html, "laeuft")


def test_the_running_run_beats_the_archived_one(app_with, team_repo: Path):
    """Der Slot ist jünger als jedes Archiv — sonst stünde er nicht im Slot.

    Ohne diese Vorrangregel gewänne der letzte archivierte Lauf, und die Zeile
    zeigte `complete`, während der Job gerade läuft. Das ist schlimmer als das
    `—` von vorher: ein leeres Feld ist erkennbar unvollständig, ein falscher
    Zustand nicht.
    """
    _mit_job_md(team_repo, "laeuft")
    app = app_with(
        {"roles": ["controller"]},
        run_journal=[{"slug": "laeuft", "status": "complete",
                      "finished_at": NOW - 3600, "exec_runtime": 4.2}],
        run_live={"laeuft": {"id": "abc", "started_at": NOW - 60,
                             "status": "running"}})
    with TestClient(app) as c:
        html = c.get("/-/jobs", headers={"accept": "text/html"}).text
    zeile = _zeile_von(html, "laeuft")
    assert "<td>running</td>" in zeile
    assert "<td>complete</td>" not in zeile


def test_without_a_running_run_the_archive_still_shows(app_with, team_repo: Path):
    """Die Gegenprobe: der Live-Zweig darf den bestehenden nicht verdrängen.

    Was da sein muss **und** was nicht da sein darf — die zweite Hälfte der
    vierten Lehre. Ohne sie wäre ein `return live_only` genauso grün.
    """
    _mit_job_md(team_repo, "laeuft")
    app = app_with(
        {"roles": ["controller"]},
        run_journal=[{"slug": "laeuft", "status": "complete",
                      "finished_at": NOW - 3600, "exec_runtime": 4.2}])
    with TestClient(app) as c:
        html = c.get("/-/jobs", headers={"accept": "text/html"}).text
    assert "<td>complete</td>" in _zeile_von(html, "laeuft")


# ── Ein abwesender Scheduler kostet einen Versuch, nicht drei ──────────────


class _ToterScheduler:
    """Ein ``ControllerClient``, der auf jeden Aufruf mit einem Ausfall
    antwortet und mitzählt, wie oft er es tun musste."""

    def __init__(self, versuche: list) -> None:
        self._versuche = versuche

    def __call__(self, url: str, *, timeout: float = 5.0):
        aufrufer = self

        class _Verbindung:
            def __getattr__(self, name: str):
                def _ruf(*_a, **_kw):
                    aufrufer._versuche.append((name, timeout))
                    raise OSError("scheduler weg")
                return _ruf

        return _Verbindung()


def test_an_absent_scheduler_costs_one_attempt_per_page(app_with, team_repo: Path,
                                                        monkeypatch):
    """**m.rau/bibi#122, der einzige P1.** Ist der Scheduler weg, darf ein
    Seitenaufbau *einen* Timeout kosten — nicht drei hintereinander.

    Live gemessen am 2026-08-04 gegen eine Blackhole-Adresse: fünf Aufrufe von
    `/-/jobs` brauchten **11,9 · 10,3 · 11,8 · 10,3 · 11,8 Sekunden**. Das
    Ticket nennt 5 s; es sind gut doppelt so viele, und der Backoff greift
    sichtbar nicht — sonst wäre der zweite Aufruf schnell gewesen.

    Der Grund ist eine **halbe** Reparatur. Klasse A (`a75026f`) hat Timeout und
    Merker eingebaut, aber nur an `_scheduler_status()`. Der Screen fragt den
    Host jedoch dreimal, und die beiden Datenabrufe gehen ungeschützt über
    `_host_client()` mit vollen 5 s: 1,5 + 5 + 5 = 11,5 — die gemessene Zahl.

    **Ein Merker für alle drei** kostet schon den ersten Aufbau nur den ersten
    Timeout: der Fehlschlag überspringt die beiden folgenden Abrufe sofort. Das
    ist die sechste Lehre wörtlich — nicht mehr Sorgfalt an drei Stellen,
    sondern eine Stelle.
    """
    from bibi import controller as controller_pkg

    versuche: list = []
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://10.255.255.1:8780")
    monkeypatch.setattr(controller_pkg, "ControllerClient", _ToterScheduler(versuche))
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        assert c.get("/-/jobs", headers={"accept": "text/html"}).status_code == 200
    assert len(versuche) == 1, (
        f"ein abwesender Scheduler wurde {len(versuche)}× gefragt: {versuche}")


def test_the_absent_scheduler_stays_unasked_for_a_while(app_with, team_repo: Path,
                                                        monkeypatch):
    """Der zweite Seitenaufbau innerhalb der Pause fragt gar nicht mehr.

    Ohne diese Hälfte wäre die Reparatur nur ein Drittel wert: der Screen wird
    im Ausfall ja nicht einmal geladen, sondern immer wieder.
    """
    from bibi import controller as controller_pkg

    versuche: list = []
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://10.255.255.1:8780")
    monkeypatch.setattr(controller_pkg, "ControllerClient", _ToterScheduler(versuche))
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        c.get("/-/jobs", headers={"accept": "text/html"})
        vorher = len(versuche)
        c.get("/-/jobs", headers={"accept": "text/html"})
        c.get("/-/jobs", headers={"accept": "text/html"})
    assert len(versuche) == vorher, (
        f"in der Backoff-Pause wurde erneut gefragt: {versuche[vorher:]}")


# ── Was fehlt, wenn nichts da ist ──────────────────────────────────────────


def test_an_empty_screen_says_what_to_do():
    """Der häufigste erste Eindruck eines neuen Knotens. Was hier steht, ist
    die eigentliche Einstiegsdokumentation — heute stand dort `— no schedules —`
    (Umbauplan §4, fünfte Fertigstellungsbedingung)."""
    html = render.jobs_screen([], now=NOW)
    assert "no schedules" not in html.lower()
    # Ein Satz, der sagt, was fehlt und was man tun kann.
    assert "vault" in html.lower()
    assert re.search(r"schedule:|`at:`|at:", html), "nennt den konkreten Schlüssel"


def test_an_empty_band_explains_itself():
    """Ein leeres Band ist nicht dasselbe wie ein leerer Screen: hier gibt es
    Jobs, nur keine dieser Art."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    nach_adhoc = html.split("ADHOC", 1)[1].split("JOURNAL", 1)[0]
    assert "—" in nach_adhoc or "none" in nach_adhoc.lower()


# ── Beziehungslabel ────────────────────────────────────────────────────────


def test_the_relation_appears_next_to_the_slug():
    """Die Beziehung steht weiter an der Zeile — seit `#31` als Chip statt in
    Klammern. Geprüft wird der Ort und die Angabe, nicht die Schreibweise; die
    Form hat ihren eigenen Test (`test_the_relation_is_a_chip_not_a_parenthesis`)."""
    html = render.jobs_screen(_zeilen(local=[_md("frisch")]), now=NOW)
    assert ">new</span>" in html


def test_duplicate_is_the_only_red_label():
    """Als einziges meldet es ein Problem im Vault statt eines Verhältnisses
    zwischen zwei Speichern — es verlangt eine Umbenennung, keinen Sync."""
    zeilen = _zeilen(local=[_md("Backup", repo_path="case/eins/Backup.md"),
                            _md("Backup", repo_path="case/zwei/Backup.md")])
    html = render.jobs_screen(zeilen, now=NOW)
    assert ">duplicate</span>" in html
    zelle = [z for z in html.split("<td") if "duplicate" in z][0]
    assert "bad" in zelle


def test_duplicate_carries_both_paths():
    """Ohne die Pfade ist die Meldung unbrauchbar: niemand weiß, welche zwei
    Dateien gemeint sind."""
    zeilen = _zeilen(local=[_md("Backup", repo_path="case/eins/Backup.md"),
                            _md("Backup", repo_path="case/zwei/Backup.md")])
    html = render.jobs_screen(zeilen, now=NOW)
    assert "case/eins/Backup.md" in html and "case/zwei/Backup.md" in html


# ── Slug-Spalte ────────────────────────────────────────────────────────────


def test_a_long_slug_is_shortened_in_the_middle():
    """Vorne steht das Datum, hinten der Zweck — hinten kürzen verliert beides.
    Der volle Slug bleibt im `title`."""
    lang = "20260609.dr-stage3-snowflake-databricks-activation"
    html = render.jobs_screen(_zeilen(local=[_md(lang)]), now=NOW)
    assert lang in html, "der volle Slug muss im title stehen"
    assert "…" in html
    assert "20260609" in html and "activation" in html


def test_the_slug_links_to_the_job_by_uid():
    """`/-/jobs/{job_uid}` — die Identität steht in der URL, nicht der Name.
    Damit überlebt ein Deep-Link eine Umbenennung, sofern der Slug explizit
    gesetzt ist."""
    from bibi.schedule.models import job_uid
    html = render.jobs_screen(_zeilen(local=[_md("EngineCI")]), now=NOW)
    assert f'/-/jobs/{job_uid("EngineCI")}' in html


# ── Filterleiste und Sortierköpfe (FE-Spezifikation §4.5/§4.6) ─────────────


def test_the_filter_bar_offers_both_groups():
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    for wert in ("job", "claude", "app", "waiting", "running", "stopped"):
        assert f'data-filter="{wert}"' in html, wert


def test_an_active_filter_is_marked():
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW, typ=["job"])
    aktiv = [z for z in html.split("<") if 'data-filter="job"' in z]
    assert aktiv and "on" in aktiv[0]


# Die drei Journal-Filter standen am dritten Band, weil eine gestaffelte
# Filtermenge einen Ort je Staffel braucht. Mit #38 ist das Band ein eigener
# Screen — sie stehen dort, und dass sie hier verschwunden sind, prüft
# `test_journal_screen.py::test_the_journal_filters_leave_the_jobs_screen_
# with_their_band`. Die Staffelung selbst ist damit nicht aufgehoben, sondern
# über zwei Screens verteilt.


def test_column_heads_are_clickable():
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    for spalte in ("slug", "type", "status", "last", "next", "24h"):
        assert f'data-sort="{spalte}"' in html, spalte


def test_the_active_sort_column_shows_its_direction():
    """Ohne Pfeil weiß niemand, ob gerade auf- oder absteigend sortiert ist —
    und ein zweiter Klick fühlt sich dann folgenlos an."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW,
                              sort="slug", direction="desc")
    kopf = [z for z in html.split("<") if 'data-sort="slug"' in z][0]
    assert "↓" in kopf or "desc" in kopf


def test_filtered_out_rows_are_gone_not_greyed():
    """Ein Filter blendet aus, er dimmt nicht — sonst zählte die Bandkopfzeile
    etwas anderes als das, was man sieht."""
    zeilen = _zeilen(local=[_md("job1"), _md("app1", app_port=9100)])
    html = render.jobs_screen(zeilen, now=NOW, typ=["app"])
    assert "app1" in html and "job1" not in html


def test_the_band_count_follows_the_filter():
    zeilen = _zeilen(local=[_md("job1"), _md("app1", app_port=9100)])
    html = render.jobs_screen(zeilen, now=NOW, typ=["app"])
    assert re.search(r"SCHEDULE\D*1", html), "die Zahl zählt, was sichtbar ist"


# ── Die alten Screens sind abgelöst (Umbauplan §1, „jeden Stein umdrehen") ──


def test_the_old_screen_routes_are_gone(app_with):
    """`/-/ui/jobs` und `/-/ui/schedules` waren zwei Screens unter einem Namen,
    weil es zwei Frontends gab. Jetzt gibt es einen Screen und eine Adresse:
    `/-/jobs`. Vor 1.0 wird gebrochen statt umgeleitet — eine Weiche, die
    niemand mehr braucht, ist nur eine Stelle, an der man später rätselt."""
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        assert c.get("/-/ui/jobs").status_code == 404
        assert c.get("/-/ui/schedules").status_code == 404
        assert c.get("/-/jobs").status_code == 200


def test_the_old_job_detail_is_off_not_merely_unlinked(app_with):
    """Die Entscheidung aus `#100`, ausgesprochen statt abgewartet.

    `/-/ui/jobs/detail/{slug}` antwortete mit `200`, während die einzigen
    Einstiege im abgelösten `_jobs_row()` standen — **ein dritter Zustand, den
    niemand gewollt hat**: nicht verlinkt, nicht abgeschaltet, aber getestet.
    Zehn Routen und vier Renderer hingen daran, dazu 69 Tests, die grün einen
    Screen bewachten, den kein Mensch mehr erreichte.

    `/-/jobs/{uid}` hat ihn ersetzt. Abschalten statt verlinken, weil zwei
    Detailseiten für denselben Job zwei Wahrheiten wären.
    """
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        for pfad in ("/-/ui/jobs/detail/x", "/-/ui/jobs/detail/x/attrs",
                     "/-/ui/jobs/detail/x/live", "/-/ui/jobs/detail/x/runs",
                     "/-/ui/jobs/detail/x/journal"):
            assert c.get(pfad).status_code == 404, pfad
        for pfad in ("/-/ui/jobs/detail/x/kill", "/-/ui/jobs/detail/x/reset",
                     "/-/ui/jobs/detail/x/rebuild", "/-/ui/jobs/detail/x/start"):
            assert c.post(pfad).status_code == 404, pfad


def test_nothing_links_to_the_old_routes_anymore():
    """Ein toter Link ist schlimmer als eine fehlende Route: er sieht aus wie
    ein Weg."""
    quelle = (Path(render.__file__)).read_text()
    assert '"/-/ui/jobs"' not in quelle
    assert "/-/ui/jobs/detail" not in quelle
    assert '"/-/ui/schedules"' not in quelle


def test_no_chart_no_sparkline_anywhere_in_the_renderer():
    """**Der Nachweis, dass es wirklich weg ist** (FE-Spezifikation §9).

    Chart.js, Sparklines und die Landungs-Histogramme fallen ersatzlos — die
    `24H`-Kennzahl tritt an ihre Stelle. Eine Liste von Hand zu führen, was
    entfernt wurde, veraltet beim ersten Vergessen; dieser Test veraltet nicht.

    Er ist zugleich die Antwort auf einen Fehlversuch: das Innenleben in einem
    Zug mit den Routen zu entfernen brachte 104 rote Tests, woraufhin ich es
    vertagen wollte. Der Umbauplan §1 sagt dazu das Nötige — ein separater
    Aufräum-Pass wird nie priorisiert.
    """
    quelle = Path(render.__file__).read_text()
    for wort in ("sparkline", "Sparkline", "chartjs", "Chart.js", "chart.umd"):
        assert wort not in quelle, f"{wort!r} lebt noch in render.py"


def test_the_landings_chain_is_gone_everywhere():
    """Der Rest von #120, nachgeholt statt vertagt (m.rau/bibi#121).

    Beim Entfernen der Charts blieb ihre Zulieferkette stehen — nicht weil sie
    gebraucht wurde, sondern weil sie nicht im Suchmuster lag: sie heisst nicht
    "chart", sondern "landings". Aufgefallen ist es erst, als ein Test dieser
    Kette fuer die Archivierungsregel (#101) angepasst werden musste — Arbeit
    an Code, den niemand mehr ruft.

    Die Kette reichte vom Renderer bis in den gefrorenen API-Vertrag. Gerade
    dort ist sie mehr als Kosmetik: wer den Vertrag liest, haelt eine Route
    fuer ein Versprechen.

    **Was hierbleibt und warum:** `_effective_days` sieht dazugehoerig aus, hat
    aber zwei echte Aufrufer im Feed — die Namensaehnlichkeit zu
    `_effective_resolution` ist der ganze Zusammenhang. Genau solche
    Beinahe-Treffer sind der Grund, warum dieser Test Namen einzeln nennt
    statt ein Praefix zu verbieten.
    """
    from bibi import controller as controller_pkg
    from bibi.controller import ControllerClient
    from bibi.daemon import app as daemon_app
    from bibi.daemon import job_db, openapi

    tot = {
        render: ("timeseries_fragment", "_landings_buckets", "_resolution_links",
                 "_current_state_chips", "_cookie_resolution_value",
                 "_LANDING_ORDER", "_RESOLUTION_WINDOWS",
                 "_DEFAULT_RESOLUTION_MINUTES"),
        job_db: ("journal_landings",),
    }
    for modul, namen in tot.items():
        for name in namen:
            assert not hasattr(modul, name), f"{modul.__name__}.{name} lebt noch"

    assert not hasattr(ControllerClient, "landings")

    for modul in (daemon_app, openapi, controller_pkg):
        quelle = Path(modul.__file__).read_text()
        assert "/-/landings" not in quelle, \
            f"die Route /-/landings steht noch in {modul.__name__}"
        assert "_effective_resolution" not in quelle, \
            f"_effective_resolution lebt noch in {modul.__name__}"

    # Die Gegenprobe: der Feed braucht `_effective_days` weiterhin.
    assert "_effective_days" in Path(controller_pkg.__file__).read_text()


def test_a_job_never_run_locally_shows_dashes():
    """Gerettet aus `test_jobs_table_no_local_run_yet_shows_dash_for_last_and_runtime`.

    Der alte Screen prüfte das an einer Zelle mit `hx-preserve`; die Aussage
    gilt weiter und gehört zum neuen: wo kein lokaler Lauf war, steht ein
    Strich — keine Null, keine leere Zelle.
    """
    html = render.jobs_screen(_zeilen(local=[_md("nie-gelaufen")]), now=NOW)
    zeile = [z for z in re.split(r"<tr[ >]", html) if "nie-gelaufen" in z][0]
    # Beide Formen zaehlen: die RUNTIME-Zelle meldet sich seit #67 vom Zell-Diff
    # ab und traegt dafuer ein Attribut, die STATUS-Zelle nicht.
    striche = zeile.count("<td>—</td>") + zeile.count("<td data-nodiff>—</td>")
    assert striche >= 2, "STATUS und RUNTIME der lokalen Seite"


def test_the_screen_reloads_itself_on_job_changes():
    """**Befund m.rau:** „Als ich ihn gelöscht habe, musste ich erst RELOAD
    drücken, bevor der Job wieder aus der Liste verschwand. Findet dort kein
    kontinuierliches Update statt? Haben wir dafür nicht den Stream?"

    Doch — der Screen war nur nicht daran angeschlossen. Der Bus meldet jede
    Job-Zustandsänderung unter dem Target `jobs`; die Liste muss sich dort
    anmelden, sonst zeigt sie den Stand vom Seitenaufbau.

    Eine gelöschte MD ist allerdings **kein** Job-Ereignis: sie fällt erst beim
    Rescan auf. Deshalb hört die Liste zusätzlich auf `feedstatus` — dort
    landet, was der Collector beim Vergleich mit dem Scheduler bemerkt.
    """
    html = render.jobs_page_v5([], now=NOW)
    assert 'data-bus="jobs"' in html
    # Seit #156 trägt die URL die aktuelle Ansicht als Query — geprüft wird
    # weiterhin nur, dass es *die Liste* ist und nicht die Seite.
    assert 'data-bus-refetch="/-/jobs/list?' in html


def test_the_list_fragment_is_the_refetch_target():
    """Nachgeladen wird die Liste, nicht die Seite: sonst verlöre man bei jedem
    Ereignis die Scroll-Position und den Fokus."""
    html = render.jobs_page_v5([], now=NOW)
    assert '<div id="jobs"' in html


def test_the_refetch_target_actually_answers(app_with, team_repo: Path):
    """**Befund m.rau, 2026-08-05:** „Bei mir bleibt nach START weiterhin
    einfach nur *starting* stehen. Und erst ein RELOAD produziert dann das
    *complete*."

    Die beiden Tests darüber prüfen, dass die Liste sich beim Bus **anmeldet** —
    nicht, dass die angemeldete URL antwortet. Sie tat es nicht:
    `/-/jobs/{job_uid}` ist vor `/-/jobs/list` registriert, und Starlette matcht
    in Registrierungsreihenfolge. Der Platzhalter schluckte `list` und lieferte
    `404 {"error": "job not found", "job_uid": "list"}`; htmx swappt nur bei
    2xx, also blieb die Zeile stehen, bis jemand neu lud.

    **Eine Absicht zu prüfen ist billiger als ihre Wirkung** — und genau deshalb
    war der Weg seit seinem Einbau (`e38d29a`, 2026-08-03) tot, ohne dass ein
    Test es bemerkte. Der Bus meldete darüber die ganze Zeit korrekt; am
    laufenden System kamen `live:<slug>` und `jobs` nachweislich an.
    """
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]},
                   run_live={"laeuft": {"id": "abc", "started_at": NOW - 60,
                                        "status": "running"}})
    with TestClient(app) as c:
        r = c.get("/-/jobs/list", headers={"accept": "text/html"})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    assert "laeuft" in r.text


def test_the_refetched_fragment_stays_subscribed(app_with, team_repo: Path):
    """Der zweite Halbsatz desselben Befundes: **einmal aktualisieren genügt
    nicht.**

    `_EVENTS_JS` swappt mit `swap: 'outerHTML'` — das Ziel-Element wird durch
    die Antwort *ersetzt*, nicht befüllt. Trägt die Antwort ihr eigenes
    `data-bus` nicht, ist die Region nach dem ersten Update taub: der Wrapper
    mit der Anmeldung ist weg, und kein weiteres Ereignis findet sie je wieder.

    Genau so liefert es jedes andere Fragment (`/-/ui/feed/status`,
    `/-/ui/clients/board`) — die Konvention stand, nur dieses eine hielt sie
    nicht. Aufgefallen ist es nie, weil die Route davor `404` gab und der Swap
    also nie stattfand: **ein Fehler hat den anderen verdeckt.**
    """
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        frag = c.get("/-/jobs/list", headers={"accept": "text/html"}).text
    assert 'id="jobs"' in frag
    assert 'data-bus="jobs"' in frag
    assert 'data-bus-refetch="/-/jobs/list?' in frag  # Query seit #156


def test_the_page_does_not_nest_the_wrapper_twice(app_with, team_repo: Path):
    """Die Gegenprobe zum Test darüber: Seite und Fragment teilen sich **eine**
    Quelle für den Wrapper. Baute die Seite ihn zusätzlich selbst, stünde
    `id="jobs"` zweimal ineinander — und `document.querySelectorAll` träfe
    beide, was jeden Refetch verdoppelt."""
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        seite = c.get("/-/jobs", headers={"accept": "text/html"}).text
    assert seite.count('id="jobs"') == 1
    assert seite.count('data-bus="jobs"') == 1


def test_the_page_carries_the_bus_client(app_with, team_repo: Path):
    """**Befund m.rau, 2026-08-05, nach dem Fix darüber:** die Zeile bewegt
    sich weiterhin nicht.

    Die Tests darüber prüfen die Anmeldung (`data-bus`) und ihre Route
    (`/-/jobs/list`) — beide stimmten danach. Nur: **eine Anmeldung ohne
    Empfänger bleibt folgenlos.** `_EVENTS_JS` ist der einzige Ort, der die
    `EventSource` gegen `/-/events` aufbaut und ein `state`-Ereignis in einen
    htmx-Refetch übersetzt; `jobs_page_v5` band es nie ein. Am laufenden FE war
    `window._bibiEvents` deshalb schlicht `undefined` — kein Strom, kein
    Refetch, und die Seite zeigte den Stand ihres Aufbaus.

    Dass es allen anderen Screens (Feed, Nodes, Live, Log) beiliegt und nur den
    beiden v5-Seiten fehlt, macht es zum Versehen des Neubaus, nicht zur
    Entscheidung — der Kommentar in `jobs_page_v5` rechnet ausdrücklich mit dem
    Bus (»widersprach es dem `outerHTML` des Bus«).

    **Drei Glieder, drei Ausfälle, ein Symptom.** Jedes für sich war
    hinreichend, die Zeile stehenzulassen, und jedes verdeckte das nächste.
    """
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        seite = c.get("/-/jobs", headers={"accept": "text/html"}).text
    assert "new EventSource('/-/events')" in seite


def test_the_detail_page_carries_the_bus_client():
    """Dieselbe Lücke in derselben Generation: `job_detail_page_v5` liefert
    `_JOB_DETAIL_JS` und `_SLOT_JS` aus, aber keinen Bus-Client.

    Das ist die Voraussetzung von m.rau/bibi#152 — welche Regionen des Details
    sich am Bus anmelden, ist erst dann eine sinnvolle Frage, wenn überhaupt
    ein Strom existiert."""
    html = render.job_detail_page_v5(slug="laeuft", spec=_md("laeuft"), now=NOW)
    assert "new EventSource('/-/events')" in html


# ── Die Slot-Kacheln am Bus (m.rau/bibi#152) ───────────────────────────────


def test_the_tiles_subscribe_to_the_bus():
    """**Befund m.rau:** nach START bleibt der Kachel-Status stehen.

    `_SLOT_JS` lädt nach einem Verb-Klick die Seite neu, die Kachel zeigt also
    `starting` — und bleibt dort. Angemeldet war bisher **nur** die Lauf-Liste
    (damals `data-bus="archived"`, seit #43 `journal:<slug>`); die Kacheln
    standen absichtlich außerhalb, weil ein Swap die Knöpfe unter dem Klick
    wegnähme.

    Der Einwand war berechtigt, der Preis war der gemeldete Fehler. Aufgelöst
    wird er nicht durch Timing, sondern strukturell: die Knöpfe hören seit
    diesem Fix delegiert (s. `test_the_slot_buttons_survive_a_swap`), damit
    kostet ein Swap sie nichts mehr.

    `live:<slug>` ist das ereignisgenaue Target dieses einen Jobs — dasselbe,
    an dem schon die ältere Detail-Fassung hing.
    """
    html = render.job_detail_page_v5(slug="laeuft", spec=_md("laeuft"), now=NOW)
    assert 'data-bus="live:laeuft"' in html


def test_the_tiles_refetch_target_actually_answers(app_with, team_repo: Path):
    """**Die Lehre aus #151, hier von vornherein angewandt:** eine Anmeldung zu
    prüfen ist billiger als ihre Wirkung — und deshalb blieb dort eine Route
    zwei Tage lang tot, die jeder Test für vorhanden hielt. Also wird hier die
    URL wirklich abgerufen, nicht bloß ihr Vorkommen im HTML behauptet."""
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        r = c.get(f"/-/jobs/{job_uid('laeuft')}/tiles", headers={"accept": "text/html"})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"


def test_the_refetched_tiles_stay_subscribed(app_with, team_repo: Path):
    """Zweite Lehre aus #151: `_EVENTS_JS` swappt mit `outerHTML`. Trägt die
    Antwort ihren `data-bus`-Wrapper nicht selbst, ist die Region nach dem
    ersten Update abgemeldet — sie aktualisiert genau einmal und nie wieder."""
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        frag = c.get(f"/-/jobs/{job_uid('laeuft')}/tiles",
                     headers={"accept": "text/html"}).text
    assert 'data-bus="live:laeuft"' in frag
    assert f'data-bus-refetch="/-/jobs/{job_uid("laeuft")}/tiles"' in frag


def test_the_detail_page_does_not_nest_the_tiles_wrapper_twice(app_with, team_repo: Path):
    """Gegenprobe: Seite und Fragment teilen **eine** Quelle für den Wrapper.
    Baute die Seite ihn zusätzlich selbst, träfe `querySelectorAll` beide und
    jeder Refetch liefe doppelt."""
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        seite = c.get(f"/-/jobs/{job_uid('laeuft')}", headers={"accept": "text/html"}).text
    assert seite.count('data-bus="live:laeuft"') == 1


def test_the_runs_fragment_stays_subscribed(app_with, team_repo: Path):
    """Derselbe Fehler wie in #151, nur auf der Detail-Seite und bisher
    unbemerkt: `/-/jobs/{uid}/runs` lieferte die Liste **ohne** den Wrapper
    `<div id="runs" data-bus="…">`, der nur in der Seite stand. Der erste
    Bus-Swap hätte die Region also abgemeldet.

    Aufgefallen ist es erst beim Nachziehen der Kacheln — und auch hier hat
    ein Fehler den anderen verdeckt: solange die Seite gar keinen Bus-Client
    auslieferte (#153), fand nie ein Swap statt.

    Das Ziel hieß bis v0.7.5 `archived` und heißt seit #43 `journal:<slug>`.
    Geprüft wird hier weiterhin, **dass** das Fragment die Anmeldung mitbringt
    — welches Ziel es ist, prüfen die #43-Tests weiter unten.
    """
    _mit_job_md(team_repo, "laeuft")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        frag = c.get(f"/-/jobs/{job_uid('laeuft')}/runs",
                     headers={"accept": "text/html"}).text
    assert 'id="runs" data-bus="journal:laeuft"' in frag


def test_the_slot_buttons_survive_a_swap():
    """Der Grund, warum die Kacheln bisher außerhalb standen: `_SLOT_JS` band
    seine Listener **direkt** an jeden `button.slot-do` beim Seitenaufbau. Nach
    einem `outerHTML`-Swap wären die neuen Knöpfe stumm — sie sähen aus wie
    Knöpfe und täten nichts.

    Delegiert am `document` gehört der Listener nicht mehr dem Element, sondern
    der Seite; ausgetauschte Knöpfe wirken sofort. `_JOB_DETAIL_JS` macht es
    für die Output-Faltung längst so — hier war es die letzte direkte Bindung.

    Der Test liest Quelltext statt Verhalten, weil das Verhalten im Browser
    liegt; er sichert damit die *Struktur*, die den Swap überlebt, nicht ihre
    Wirkung. Die Wirkung ist am laufenden FE geprüft.
    """
    assert "document.addEventListener('click'" in render._SLOT_JS
    assert "querySelectorAll('button.slot-do')" not in render._SLOT_JS


# ── RUNTIME ist eine Scheduler-Eigenschaft und ein Perzentil (#132) ────────


def test_runtime_shows_the_p90_from_the_scheduler():
    """**Vorgabe m.rau:** *„Die Runtime ist ebenfalls eine Scheduler
    Eigenschaft. Sie kommt vom Scheduler und ist der 90. Perzentil P90 der
    Laufzeit der letzten 30 Laeufe."*

    Vorher stand dort `exec_runtime` des letzten **lokalen** Laufs — zwei
    Abweichungen in einer Zelle: die falsche Seite und die falsche Frage.
    """
    html = render.jobs_screen(
        _zeilen(local=[_md("EngineCI")],
                scheduler=[{"slug": "EngineCI", "status": "complete",
                            "schedule": "0 * * * *", "runtime_p90": 231.9}]),
        now=NOW)
    assert "<td data-nodiff>3m 51s</td>" in _zeile_von(html, "EngineCI")


def test_runtime_stays_empty_without_a_p90():
    """Die Gegenprobe: die alte Quelle darf nicht mehr durchschlagen.

    Ohne sie waere ein `or l.get("exec_runtime")` genauso gruen — und die Zelle
    zeigte weiter die Dauer des letzten lokalen Laufs, nur seltener. Unter fuenf
    Laeufen liefert der Scheduler bewusst nichts; dann steht dort ein Strich und
    keine Zahl, die niemand tragen kann.
    """
    zeile = _zeile_von(
        render.jobs_screen(
            _zeilen(local=[_md("EngineCI")],
                    scheduler=[{"slug": "EngineCI", "status": "complete",
                                "schedule": "0 * * * *"}],
                    local_runs={"EngineCI": {"status": "error", "exec_runtime": 231.9}}),
            now=NOW),
        "EngineCI")
    assert "3m 51s" not in zeile
    assert "<td>error</td>" in zeile, "der lokale Zustand bleibt, nur seine Dauer geht"


# ── Die Blockordnung: erst der Job, dann der Scheduler, dann der Client ────
#
# **#135 dreht um, was m.rau/bibi#147 am 2026-08-05 festgelegt hatte**, und das
# gehört benannt, weil der Code die alte Regel an drei Stellen zitiert. Sie
# lautete *„Client links, Scheduler rechts — in jedem Screen"*, begründet mit
# dem Ausfall: was wegfallen kann, steht rechts.
#
# Die neue Ordnung sortiert nach einer anderen Frage — **worüber macht diese
# Spalte eine Aussage?** Erst was den Job beschreibt (Slug, Typ, seine Laufzeit,
# seine Verlässlichkeit), dann der Scheduler, der ihn führt, dann der Client,
# der zeigt, was hier ankam. Beide Ordnungen sind begründbar; die jüngere ist
# entschieden (m.rau, 2026-08-11, Originalnotizen im Akzeptanz-Memo zu `v0.8.1`:
# *„dann Scheduler Spalten (zuerst) … dann erst Client Spalten"*).


def _kopfzeilen(html: str) -> tuple[list[str], list[str]]:
    """Gruppenzeile und Spaltenköpfe als **Listen**.

    Die Listenform ist der Gegenstand: ein Test auf das Vorhandensein einzelner
    Zellen wäre auch bei falscher Reihenfolge grün, und geprüft wird hier genau
    die Reihenfolge.
    """
    thead = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
    zeilen = re.findall(r"<tr[^>]*>(.*?)</tr>", thead, re.S)

    def zellen(roh: str) -> list[str]:
        return [re.sub(r"<[^>]+>", "", z).replace("↑", "").replace("↓", "").strip()
                for z in re.findall(r"<th[^>]*>(.*?)</th>", roh, re.S)]

    return [z for z in zellen(zeilen[0]) if z], zellen(zeilen[1])


#: Die Spaltenfolge des Jobs-Screens nach #135. `NEXT` ist die einzige Spalte,
#: die dem Journal fehlt — dort gibt es keine Zukunft mehr (#130).
_JOBS_SPALTEN = ["SLUG", "TYPE", "P90 RUNTIME", "RELIABILITY",
                 "STATUS", "LAST", "NEXT", "STATUS", "LAST"]


def test_the_columns_read_job_then_scheduler_then_client():
    """#135: der Scheduler führt den Job, der Client zeigt, was hier ankam."""
    gruppen, spalten = _kopfzeilen(
        render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW))
    assert gruppen == ["SCHEDULER", "CLIENT"]
    assert spalten == _JOBS_SPALTEN


def test_the_journal_carries_the_same_order_minus_next():
    """Beide Screens tragen dieselbe Ordnung — das ist die Zusage von #135.

    Verglichen werden die Kopfzeilen **gegeneinander**, nicht jede für sich:
    zwei Screens, die einzeln stimmen und voneinander abweichen, sind genau der
    Fall, den dieses Ticket meint (*„Eine Reihenfolge, die nur an einer Stelle
    gilt, ist keine."*).
    """
    from tests.test_journal_screen import _historie

    gruppen, spalten = _kopfzeilen(
        render.journal_screen(_zeilen(journal=[_historie("alt")]), now=NOW))
    assert gruppen == ["SCHEDULER", "CLIENT"]
    assert spalten == [s for s in _JOBS_SPALTEN if s != "NEXT"]


# ── Die Baenderung ist abschaltbar, das `@` macht die Zeile selbsttragend ──
#
# **Wunsch m.rau (m.rau/bibi#134):** *„Ich haette schon gerne das Grouping. Mit
# Grouping aus wird eine Liste ohne Unterteilung produziert. […] Eine
# Unterscheidung zwischen Schedule (next) und At (`@`) sowie zwischen ad-hoc
# (kein Next und kein `@`) ist sogar immer noch moeglich."*
#
# Das Handle war gestrichen worden, weil die Baender als **Klassifikation**
# gedacht sind und nicht als Sortierordnung (FE §4.6). Der Vorschlag dreht das
# um, und er ist staerker als die alte Begruendung: traegt die Zeile ihre Gruppe
# selbst, ist die Baenderung nur noch eine Darstellungsform — und dann darf man
# sie abschalten, ohne Information zu verlieren.


def _oneshot(slug: str, **kw) -> dict:
    return {"slug": slug, "schedule": None, "at": "2026-08-05T15:07:00",
            "payload": "claude: erzaehl was", "repo_path": f"case/x/{slug}.md", **kw}


def test_a_oneshot_says_so_in_its_type_column():
    """Das `@` ist der ganze Unterschied zwischen „Gruppierung entfernen" und
    „Gruppierung ausblenden"."""
    html = render.jobs_screen(_zeilen(local=[_oneshot("20260805.at-150738-81ec")]),
                              now=NOW)
    assert "<td>@claude</td>" in _zeile_von(html, "20260805.at-150738-81ec")


def test_a_recurring_job_carries_no_at():
    """Die Gegenprobe. Ein `@` an jeder Zeile unterschiede nichts mehr."""
    assert "<td>job</td>" in _zeile_von(
        render.jobs_screen(_zeilen(local=[_md("EngineCI")]), now=NOW), "EngineCI")


def test_grouping_off_produces_one_list_without_bands():
    html = render.jobs_screen(_zeilen(local=[_md("a"), _md("b", schedule="adhoc")]),
                              now=NOW, group=False)
    for band in ("SCHEDULE", "ADHOC", "JOURNAL"):
        assert f">{band} " not in html, band
    assert 'class="band"' not in html


def test_grouping_off_loses_no_row():
    """Die Gegenprobe zur Gegenprobe: eine Liste ohne Unterteilung ist keine
    Liste ohne Zeilen. Ohne diese Haelfte waere ein leerer Screen genauso
    gruen — und die Baender sind bisher die Einzigen, die Zeilen ausgeben."""
    zeilen = _zeilen(local=[_md("a"), _md("b", schedule="adhoc"), _md("c")])
    html = render.jobs_screen(zeilen, now=NOW, group=False)
    for slug in ("a", "b", "c"):
        assert _zeile_von(html, slug)


def test_grouping_is_on_by_default():
    assert 'class="band"' in render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)


def test_the_bar_carries_the_group_handle():
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    assert 'data-group=' in html


def test_the_route_takes_the_handle(app_with, team_repo: Path):
    _mit_job_md(team_repo, "flach")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        html = c.get("/-/jobs?group=off", headers={"accept": "text/html"}).text
    assert 'class="band"' not in html
    assert _zeile_von(html, "flach")


# ── Filter und Sortierung überleben (m.rau/bibi#156) ───────────────────────
#
# Der Befund m.rau: „Der Anwender ist genervt, wenn die Einstellungen immer
# wieder zurück gesetzt werden." Zwei getrennte Wege gehen sie heute verloren,
# und jeder braucht seine eigene Antwort:
#
#   1. Der **Bus-Refetch** holt `/-/jobs/list` ohne Query — bei jedem
#      Statuswechsel kommt die ungefilterte Liste zurück. Antwort: die
#      Refetch-URL trägt die Query mit.
#   2. Die **Wiederkehr** (Tab-Link, Bookmark, neuer Browser-Start) landet auf
#      `/-/jobs` ohne Query. Antwort: ein Cookie erinnert die letzte Wahl.
#
# `#66` hat das für den alten Screen schon einmal gelöst und den Grund
# aufgeschrieben: „Ein Sortierzustand, der bei jedem Bus-Refetch zurückspränge,
# wäre ärgerlicher als keiner." Der bibi5-Umbau hat die Mechanik nicht
# mitgenommen — ihre sechs Symbole standen bis zu diesem Ticket ohne Aufrufer
# in `controller/__init__.py`.


def test_the_refetch_url_carries_the_current_filter():
    """Weg 1: die nachgeladene Liste kommt gefiltert zurück, nicht roh.

    Ohne dies ist jede Filterwahl nur so lange gültig, bis irgendein Job seinen
    Status wechselt — und das ist der Normalbetrieb, nicht der Randfall.
    """
    html = render.jobs_page_v5(_zeilen(local=[_md("a")]), now=NOW,
                               typ=["job"], sort="next", direction="desc")
    # Gezielt der Wrapper der Jobs-Liste: die Seite trägt mehrere Bus-Regionen
    # (u. a. den Feed-Status), und die erste ist nicht diese.
    treffer = re.search(r'<div id="jobs"[^>]*data-bus-refetch="([^"]+)"', html)
    assert treffer, "keine Refetch-URL am Jobs-Wrapper"
    url = treffer.group(1)
    assert "typ=job" in url, url
    assert "sort=next" in url, url
    assert "dir=desc" in url, url


def test_a_bare_visit_gets_the_last_choice_back(app_with, team_repo: Path):
    """Weg 2: wer gefiltert hat und später ohne Query wiederkommt, sieht seine
    Wahl — nicht den Grundzustand."""
    _mit_job_md(team_repo, "flach")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        c.get("/-/jobs?f=1&group=off", headers={"accept": "text/html"})
        html = c.get("/-/jobs", headers={"accept": "text/html"}).text
    assert 'class="band"' not in html, "die Bänderung kam trotz Cookie zurück"


def test_an_explicit_url_beats_the_remembered_one(app_with, team_repo: Path):
    """Der Query-Parameter gewinnt immer — sonst wäre eine geteilte URL nicht
    teilbar, weil der Empfänger seine eigene Erinnerung darübergelegt bekäme.
    Das war schon bei #66 die Regel und ist der Grund, warum es ein Rückfall
    ist und keine Vorbelegung."""
    _mit_job_md(team_repo, "flach")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        c.get("/-/jobs?f=1&group=off", headers={"accept": "text/html"})
        html = c.get("/-/jobs?f=1", headers={"accept": "text/html"}).text
    assert 'class="band"' in html, "die gemerkte Wahl hat die URL überstimmt"


def test_clearing_a_filter_is_not_undone_by_the_cookie(app_with, team_repo: Path):
    """Den letzten Filter abzuwählen muss möglich bleiben.

    Der Randfall, an dem eine naive Cookie-Lösung scheitert: das Skript
    entfernt den Parameter beim Abwählen aus der URL, und eine URL ohne `typ`
    ist von einer nie gesetzten nicht zu unterscheiden. Der Cookie brächte den
    eben gelöschten Filter sofort zurück — der Knopf wäre tot. Deshalb trägt
    jede vom Skript gebaute URL das Zeichen `f=1`: *diese Query ist die
    Antwort, auch wo sie schweigt.*
    """
    _mit_job_md(team_repo, "flach")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        gefiltert = c.get("/-/jobs?f=1&typ=app", headers={"accept": "text/html"}).text
        # `_zeile_von` wirft, wenn nichts da ist — für die Abwesenheit taugt
        # nur die rohe Suche.
        assert 'title="flach"' not in gefiltert, "Filter griff gar nicht"
        html = c.get("/-/jobs?f=1", headers={"accept": "text/html"}).text
    assert _zeile_von(html, "flach"), "der abgewählte Filter kam zurück"


def test_the_filter_script_marks_every_url_it_builds():
    """Das Gegenstück im Browser: ohne `f=1` an *jeder* gebauten URL ist die
    Regel oben nur die halbe Miete."""
    assert "'f', '1'" in render._JOBS_JS or '"f", "1"' in render._JOBS_JS


def test_the_dead_cookie_helpers_from_the_old_screen_are_gone():
    """Die Mechanik aus #66 lag seit dem bibi5-Umbau ohne einen einzigen
    Aufrufer im Modul — eine tote Kette, deren Spitze fehlte (Umbauplan §1).
    Sie wird durch diesen Fix ersetzt, nicht wiederbelebt: ihre Namen und
    Cookie-Schlüssel gehören dem alten Schedules-Screen und tragen die
    Mehrfachauswahl-Achsen des neuen nicht."""
    from bibi import controller
    quelle = Path(controller.__file__).read_text(encoding="utf-8")
    for name in ("_effective_filter", "_effective_sort", "_set_sort_cookies",
                 "_set_filter_cookies", "_set_resolution_cookie",
                 "_FILTER_COOKIE_MAX_AGE"):
        assert name not in quelle, name


# ── Scheduler zuerst, dann der Client — überall (#135) ─────────────────────
#
# **Diese Überschrift stand bis `v0.8.4` andersherum hier**, und die Umkehr
# gehört mit ihrer Vorgeschichte notiert, sonst liest sie sich später wie ein
# versehentlicher Rückschritt. m.rau/bibi#147 hatte am 2026-08-05 festgelegt:
# *„Header: client links, scheduler rechts, Jobs: Scheduler links, Client
# rechts. Einheitlich den Client links."* Begründet mit dem Ausfall — fällt der
# Host weg, verlieren genau die rechten Werte ihre Gültigkeit, und was wegfallen
# kann, steht rechts.
#
# **#135 sortiert nach einer anderen Frage** (m.rau, 2026-08-11): worüber macht
# diese Spalte eine Aussage? Erst der Job selbst, dann die Instanz, die ihn
# führt, dann die, die zeigt, was hier ankam. Beide Ordnungen sind begründbar;
# die jüngere ist entschieden — und sie gilt genauso einheitlich, in Tabelle,
# Header und den Kacheln des Job-Details.
#
# **Was aus #147 unverändert weitergilt:** `CLIENT`, nicht `LOCAL`. Ein Wort für
# eine Sache; die Umkehr betrifft die Reihenfolge, nicht die Benennung.


def test_the_scheduler_status_cell_comes_before_the_client_one():
    """Nicht nur die Beschriftung dreht sich, sondern die Spalte selbst.

    Ohne diesen Test wäre eine Kopfzeile denkbar, die `SCHEDULER` zuerst
    behauptet, während die Zellen darunter in der alten Ordnung stehen — die
    Beschriftung stünde dann über der falschen Spalte, und das ist schlimmer als
    jede der beiden Reihenfolgen für sich.
    """
    zeile = _zeile_von(render.jobs_screen(
        _zeilen(local=[_md("EngineCI")],
                scheduler=[{"slug": "EngineCI", "status": "complete",
                            "schedule": "0 * * * *"}],
                local_runs={"EngineCI": {"status": "error"}}),
        now=NOW), "EngineCI")
    assert zeile.index("complete") < zeile.index("error")


def test_next_stays_with_the_scheduler_who_alone_knows_it():
    """Die Gegenprobe zum Dreh: `LAST` und `NEXT` bleiben beim Scheduler.

    Wer Spalten umordnet, könnte die Zuordnung mitverschieben — dann stünde
    `NEXT` über dem Client, der gar nicht weiß, wann es wieder läuft.

    **`P90 RUNTIME` ist mit #135 bewusst aus diesem Block herausgewandert** und
    steht jetzt beim Job. Bis dahin galt FE §4.3 (*„der Scheduler weiss, wann es
    wieder laeuft, und wie lange es dauert"*); die Zahl entsteht weiterhin aus
    seinen Läufen, sagt aber etwas über den Job aus, nicht über ihn.
    """
    _, spalten = _kopfzeilen(render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW))
    assert spalten[4:7] == ["STATUS", "LAST", "NEXT"], "der Scheduler-Block"
    assert spalten[7:] == ["STATUS", "LAST"], "der Client-Block"


# ── Der Faltzustand überlebt einen Bus-Refetch (#44) ───────────────────────
#
# **Befund:** ein Bus-Refetch der Lauf-Liste wirft den aufgeklappten Output weg,
# weil der Faltzustand nur im DOM lebt — `zeile.hidden`, der Knopftext und der
# geladene Text im `.out-body`. Wird `#runs` getauscht, ist alles davon fort.
#
# Das war ein Ärgernis, solange die Liste selten neu geladen wurde. Mit #43
# refetcht sie bei jedem Slot-Zustandswechsel, und damit klappt der Bereich
# genau während des Mitlesens eines laufenden Jobs zu — aus dem Ärgernis wird
# ein Ausschlusskriterium. Deshalb ist die Reihenfolge erzwungen.
#
# Das Muster gibt es im Haus bereits: `_SCROLL_JS` rettet die Scroll-Position
# über `htmx:beforeSwap`/`htmx:afterSettle`. Für den Faltzustand fehlte
# derselbe Griff.


def test_the_expanded_output_survives_a_swap():
    js = render._JOB_DETAIL_JS
    assert "htmx:beforeSwap" in js, "der Faltzustand wird vor dem Swap nicht gesichert"
    assert "htmx:afterSettle" in js, "nach dem Swap wird nichts wiederhergestellt"


def test_the_saved_state_is_keyed_by_run_not_by_position():
    """Der Ausklappbereich gehört zum **Lauf**, nicht zur Zeilenposition — nach
    einem Refetch kann eine neue Zeile oben dazugekommen sein."""
    js = render._JOB_DETAIL_JS
    kopf = js[js.index("htmx:beforeSwap"):]
    assert "dataset.run" in kopf or "data-run" in kopf or "id.slice" in kopf, kopf[:400]


def test_the_restore_only_touches_the_runs_region():
    """`_SCROLL_JS` prüft aus demselben Grund `ev.detail.target` — ein globaler
    Handler, der bei jedem Swap irgendwo etwas aufklappt, ist schlimmer als der
    Fehler, den er behebt."""
    js = render._JOB_DETAIL_JS
    kopf = js[js.index("htmx:beforeSwap"):]
    assert "runs" in kopf, "der Handler unterscheidet die Zielregion nicht"


def test_the_reloaded_output_is_not_fetched_again():
    """Der Text ist schon da; ihn nach jedem Swap neu zu holen ersetzte das
    Flackern durch einen Roundtrip je Refetch — bei einem laufenden Job also
    im Sekundentakt."""
    js = render._JOB_DETAIL_JS
    kopf = js[js.index("htmx:beforeSwap"):]
    assert "out-body" in kopf, "der geladene Text wird nicht mitgerettet"


# ── Die Lauf-Liste bekommt Slot-Zustandswechsel (#43) ──────────────────────
#
# **Befund:** die Kachel springt auf `complete`, die Zeile darunter zeigt weiter
# `starting`. Die Liste hing an `data-bus="archived"`, und das wird an genau
# einer Stelle publiziert: beim Journal-INSERT. Ein Slot-Zustandswechsel feuert
# `live:<slug>` und `journal:<slug>` — aber nicht `archived`.
#
# **Entscheidung m.rau, 2026-08-07:** `journal:<slug>`. Es wird bereits auf
# **beiden** Wegen publiziert — bei jedem Slot-Zustandswechsel und bei jedem
# Journal-INSERT — und ist damit genau das, was die Liste zeigt: beide Quellen.
# Kein neuer `publish_state()`-Aufruf nötig.
#
# Die beiden anderen Wege aus dem Ticket sind verworfen: `archived` zusätzlich
# aus `_publish_live()` zu senden hiesse, ein Ziel zu feuern, wenn nichts
# archiviert wurde — genau der Namensverfall, vor dem `bus.py` warnt. Und
# `#runs` an `live:<slug>` zu haengen verlangte einen zusaetzlichen publish
# beim Journal-INSERT.


def _leere_liste():
    """Eine `RunList` ohne Inhalt — fuer diese Tests zaehlt nur der Wrapper."""
    from bibi.controller.jobs_view import RunList
    return RunList(tiles=[], runs=[], counts={})


def test_the_runs_list_listens_for_this_jobs_journal():
    html = render.job_runs_fragment(
        _leere_liste(), now=NOW, slug="laeuft", job_uid="u1")
    assert 'data-bus="journal:laeuft"' in html, html[:300]


def test_the_runs_list_no_longer_waits_for_an_archival():
    """`archived` feuert nur beim Journal-INSERT — ein Slot, der von `starting`
    auf `running` geht, archiviert nichts und blieb deshalb unsichtbar."""
    html = render.job_runs_fragment(
        _leere_liste(), now=NOW, slug="laeuft", job_uid="u1")
    assert 'data-bus="archived"' not in html


def test_a_slot_state_change_reaches_the_runs_target():
    """Der Nachweis auf der Bus-Seite: derselbe Tick, der die Kachel dreckig
    macht, macht auch die Liste dreckig — sonst widersprechen sich beide."""
    from bibi.daemon.bus import Collector

    class _Bus:
        def __init__(self):
            self.published = []

        def publish_state(self, ziel, wert=None):
            self.published.append(ziel)

    bus = _Bus()
    c = Collector(bus, registry=None)
    c._publish_live("laeuft", None)
    c._publish_journal("laeuft", None)
    assert "live:laeuft" in bus.published, "die Kachel bekommt nichts"
    assert "journal:laeuft" in bus.published, "die Liste bekommt nichts"


# ── #83: die Wahl überlebt einen Tab-Wechsel ────────────────────────────────


def test_a_filter_set_on_the_fragment_survives_leaving_and_returning(
        app_with, team_repo: Path):
    """Befund m.rau, 2026-08-08: *„er muss auch greifen, wenn ich über Tabs
    navigiere. Wenn ich zurück komme, erwarte ich gleiche Filter."*

    Zwei Routen bedienen den Screen, und bis `#83` merkte sich nur eine die
    Ansicht: `/-/jobs` las **und** schrieb den Cookie, `/-/jobs/list` las ihn
    nur. Ein Filter-Klick geht aber auf das Fragment — die Wahl überlebte
    damit jeden Bus-Refetch (dafür trägt die Refetch-URL seit `m.rau/bibi#156`
    alle Parameter mit) und keinen einzigen Seitenwechsel.

    Das ist die zweite Hälfte von `#156`: dort standen beide Verlustwege, und
    geschlossen wurde nur der erste."""
    _mit_job_md(team_repo, "flach")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        # 1. Filter setzen — wie der Klick im Browser: aufs Fragment.
        c.get("/-/jobs/list?f=1&typ=app", headers={"accept": "text/html"})
        # 2. Weg und zurück — die volle Seite, ohne Query.
        html = c.get("/-/jobs", headers={"accept": "text/html"}).text
    assert 'title="flach"' not in html, (
        "die auf dem Fragment getroffene Filterwahl war nach dem Seitenwechsel weg")


def test_the_fragment_remembers_the_view(app_with, team_repo: Path):
    """Dieselbe Zusage, an der Naht statt am Verhalten gemessen."""
    _mit_job_md(team_repo, "flach")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        r = c.get("/-/jobs/list?f=1&typ=app", headers={"accept": "text/html"})
    assert "bibi_jobs_typ" in r.headers.get("set-cookie", ""), \
        "das Fragment merkt sich die Ansicht nicht"


def test_the_url_still_beats_the_cookie_on_the_fragment(app_with, team_repo: Path):
    """Die Gegenrichtung, und die Bedingung, unter der der Fix harmlos ist.

    Ein Deep-Link muss stärker sein als die Erinnerung — sonst wäre eine
    geteilte Ansicht nicht mehr teilbar. Und das Abwählen des letzten Filters
    darf der Cookie nicht rückgängig machen (`f=1`, s. oben)."""
    _mit_job_md(team_repo, "flach")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        c.get("/-/jobs/list?f=1&typ=app", headers={"accept": "text/html"})
        html = c.get("/-/jobs/list?f=1", headers={"accept": "text/html"}).text
    assert _zeile_von(html, "flach"), "der abgewählte Filter kam über den Cookie zurück"


# ── #85: die Handles überleben einen Bus-Refetch ────────────────────────────


def test_the_view_handles_are_delegated_not_bound_per_element():
    """**Ein struktureller Wächter, und er weiß, was er nicht zeigt.**

    Der Nachweis für `#85` ist der Browser-Test (`tests/browser/
    test_jobs_view.py::test_the_filter_handles_survive_a_bus_refetch`) — er
    klickt nach einem echten Refetch und sieht, ob etwas passiert. Nur läuft
    die Browser-Ebene im Engine-CI **nicht** mit; sie hat ihren eigenen,
    selteneren Job. Ohne eine Zeile hier fiele ein Rückfall erst dort auf,
    also womöglich nach dem Release.

    Was diese Prüfung leistet: sie hält die Eigenschaft fest, an der der
    Fehler hing — die Handles hängen an `document.body` und nicht an
    Elementen, die der `outerHTML`-Swap ersetzt. Was sie **nicht** leistet:
    zu zeigen, dass ein Klick wirkt. Genau dieser Abstand ist der Grund für
    `#84`, und er gehört benannt statt kaschiert.
    """
    js = render._JOBS_JS
    assert "document.body.addEventListener('click'" in js, (
        "die Ansichts-Handles hängen nicht mehr am Body — nach dem ersten "
        "Bus-Refetch wären sie tot (#85)")
    for tot in ("document.querySelectorAll('.fltr[data-filter]')",
                "document.querySelectorAll('th[data-sort]')"):
        assert tot not in js, (
            f"{tot} bindet an Elemente, die der Swap ersetzt — das war #85")


# ── Die Sortier-Whitelist kennt dieselben Spalten wie die Köpfe (#95) ──────


def test_the_24h_column_actually_sorts(app_with, team_repo: Path):
    """**Ein klickbarer Spaltenkopf, dessen Schlüssel die Route wegwirft.**

    `24H` steht in `_SORTIERBAR` und in `jobs_view._sortwert()`, aber nicht in
    `render._SORT_KEYS` — und genau die fragt `controller/__init__.py` ab,
    bevor irgendetwas sortiert wird. Ein Klick auf die Spalte setzt `sort=24h`,
    die Whitelist verwirft ihn, `_sort_kopf()` markiert nichts. Live gefunden
    am 2026-08-09 gegen den Client-Daemon.

    Die Konstante gehört zum toten Pfad `render.sort_rows()`, der keinen
    Aufrufer mehr hat. Sie blieb als Whitelist zurück und ist mit den Spalten
    nicht mitgewachsen.

    Die zweite Zusicherung ist die Absicherung, ohne die ein Fehlschlag
    nichts bewiese: **kam die Route überhaupt so weit?** Bricht sie vorher ab,
    fehlt auch `slug`, und der erste Assert wäre aus dem falschen Grund rot.
    """
    _mit_job_md(team_repo, "a")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        h24 = c.get("/-/jobs?sort=24h&dir=desc", headers={"accept": "text/html"}).text
        hslug = c.get("/-/jobs?sort=slug&dir=desc", headers={"accept": "text/html"}).text

    assert 'class="sortiert" data-sort="slug"' in hslug, (
        "Absicherung: die Route rendert die Tabelle überhaupt — ohne das "
        "prüft der Assert unten nichts")
    assert 'class="sortiert" data-sort="24h"' in h24, (
        "sort=24h erreicht den Renderer nicht — die Whitelist in "
        "controller/__init__.py kennt den Schlüssel nicht (#95)")


def test_every_clickable_column_survives_the_whitelist(app_with, team_repo: Path):
    """Die Verallgemeinerung: **jeder** Kopf aus `_SORTIERBAR` muss durch.

    Der Test darüber fängt den heutigen Fall. Dieser hier fängt den nächsten —
    eine siebte klickbare Spalte, deren Schlüssel wieder an einer zweiten
    Liste vorbeigeht. Das ist der eigentliche Fehler: zwei Wahrheiten über
    dieselbe Menge.
    """
    _mit_job_md(team_repo, "a")
    app = app_with({"roles": ["controller"]})
    with TestClient(app) as c:
        for schluessel, _label in render._SORTIERBAR:
            html = c.get(f"/-/jobs?sort={schluessel}&dir=asc",
                         headers={"accept": "text/html"}).text
            assert f'class="sortiert" data-sort="{schluessel}"' in html, (
                f"{schluessel} ist klickbar, kommt aber nicht durch die "
                f"Whitelist (#95)")


# ── Der App-Link in der Type-Zelle (#96) ───────────────────────────────────


def test_an_app_row_names_its_port_without_linking_it():
    """`#96` hat hier einen Link eingesetzt, `#104` hat ihn wieder entfernt —
    und beide hatten recht.

    `#96` war richtig darin, dass der **Typ** samt Port sichtbar gehört; er
    war seit dem v5-Umbau auf `job` zurückgefallen. Falsch war die **Adresse**:
    sie entstand aus `public_host()`, dem Knoten des Betrachters, und zeigte
    im Mac-FE auf `localhost:91xx`, wo nichts läuft (`#104`).

    Der Port ist eine Job-Eigenschaft und darf hier stehen. Die Adresse
    braucht den ausführenden Knoten und lebt in den Slot-Kacheln, die ihn über
    `Tile.host` kennen.
    """
    zeilen = _zeilen(local=[_md("app1", app_port=9100)])
    html = render.jobs_screen(zeilen, now=NOW, public_host="Mac.fritz.box")
    assert "app :9100" in html, "der Port gehört sichtbar an den Typ (#96)"
    assert "Mac.fritz.box:9100" not in html, (
        "die Jobs-Tabelle verlinkt die App auf den Betrachter-Host (#104)")


def test_a_plain_row_keeps_its_bare_type():
    """Die Gegenprobe: ohne Port bleibt der Typ ein Wort."""
    zeilen = _zeilen(local=[_md("job1")])
    html = render.jobs_screen(zeilen, now=NOW, public_host="Mac.fritz.box")
    assert "Mac.fritz.box" not in html, "ein Job ohne Port bekommt keinen Link"
    assert "app :" not in html, "ein Job ohne Port ist keine App"


# ── Der Output eines claude-Laufs geht am Formatter vorbei (#99) ────────────


def test_the_run_output_route_uses_the_formatter(app_with, team_repo: Path,
                                                 seed_journal_row):
    """**Der Rot-Schritt von `#99` auf Routen-Ebene — und der einzige, der ihn
    findet.**

    Die Bausteine sind alle gebaut und für sich grün: `format_events()`
    typisiert die Token-Deltas, `_merge_deltas()` fügt sie zusammen,
    `_event_line()` setzt `thinking` ab, `output_block()` verbindet die drei.
    **`screen_job_run_output()` ruft keinen davon auf** — sie baut ihr eigenes
    `"\\n".join(...)` über die Roh-Events.

    Ein Test auf Bausteinebene hätte das nie gesehen: er prüft die Fähigkeit,
    nicht ihren Aufruf. Genau die Konstellation aus `#95`, `#96` und `#102`.

    Live am 2026-08-09 im aufgeklappten `Witz`-Lauf: `Der Benut` / `zer möchte`
    — Umbruch mitten im Wort.
    """
    import json as _json
    from bibi.daemon import job_db
    from bibi.schedule.models import job_uid

    _mit_job_md(team_repo, "w")
    ref = "data/job/w/output.jsonl"
    (team_repo / "data" / "job" / "w").mkdir(parents=True, exist_ok=True)
    roh = [{"t": 1.0, "s": "out", "line": _json.dumps(
        {"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking"}}})}]
    for i, c in enumerate(("Der Benut", "zer möchte")):
        roh.append({"t": 2.0 + i, "s": "out", "line": _json.dumps(
            {"type": "stream_event", "event": {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "thinking_delta", "thinking": c}}})})
    (team_repo / ref).write_text(
        "\n".join(_json.dumps(e) for e in roh) + "\n", encoding="utf-8")

    app = app_with({"roles": ["controller"]})
    conn = job_db.connect()
    try:
        seed_journal_row(conn, run_id="r1", slug="w", kind="job",
                         status="complete", exit_code=0, output_ref=ref,
                         host="h", worker="h", started_at=1.0, finished_at=3.0,
                         payload="claude: hi")
        conn.commit()
        jid = conn.execute("SELECT id FROM journal WHERE slug='w'").fetchone()["id"]
    finally:
        conn.close()

    with TestClient(app) as c:
        r = c.get(f"/-/jobs/{job_uid('w')}/runs/{jid}/output")

    assert r.status_code == 200, r.text[:200]
    assert "Der Benutzer möchte" in r.text, (
        "die Route umgeht den Formatter — die Token-Deltas bleiben zerrissen (#99)")


# ── Zugezogen aus `test_controller_jobs.py` (#100) ──────────────────────────
#
# Die Datei trug den bibi4-Jobs-Screen und ist mit ihm entfallen. Diese sechs
# Tests prüften Bausteine, die den Umbau überlebt haben: `_human_duration()`
# rendert die RUNTIME-Spalte dieses Screens, `_local_run_status_aus()` seine
# Client-Seite, `_Backoff` seinen Scheduler-Abruf. Sie stehen jetzt dort, wo
# das Geprüfte wirkt — genau die Umzugsregel, die `#100` verlangt.


def test_human_duration_thresholds():
    # Bibi4-Iteration, User-Fund: "Laufzeit soll human-readable sein ... je
    # nach Dauer ein angepasstes Delta" — zwei Einheiten je Stufe.
    assert render._human_duration(None) == "—"
    assert render._human_duration(45) == "45s"
    assert render._human_duration(192) == "3m 12s"
    assert render._human_duration(5400) == "1h 30m"
    assert render._human_duration(90000) == "1d 1h"


def test_human_duration_keeps_one_decimal_below_ten_seconds():
    """Die meisten Laeufe dauern zwei bis acht Sekunden — als ganze Zahl sehen
    sie alle gleich aus. Ab zehn Sekunden traegt die Stelle nichts mehr, und
    die Schwellen darueber bleiben unveraendert."""
    assert render._human_duration(2.8007938861846924) == "2.8s"
    assert render._human_duration(0.4) == "0.4s"
    assert render._human_duration(9.9) == "9.9s"
    assert render._human_duration(45) == "45s"


def test_local_run_status_takes_the_newest_run_per_slug():
    """Die Client-Spalte zeigt den **neuesten** lokalen Lauf, nicht irgendeinen.

    Befund m.rau: *„wieso `6d 1h` bei gmail-transfer? Das muss ein Rechenfehler
    sein."* In der echten DB nachgesehen: die Zahl stammte aus
    `gmail-transfer-d03e0d2e` — einem gepinnten Lauf vom 14.07., der beim
    Aufraeumen am 20.07. terminal gesetzt wurde. Die Rueckrechnung des Suffix
    war da, aber `setdefault()` behielt den **zuerst gefundenen** Eintrag; die
    Journal-Reihenfolge ist nicht die Zeitreihenfolge.

    Rot war: `exec_runtime == 522318.5` statt `2.8`.
    """
    from bibi.controller import _local_run_status_aus  # reine Funktion
    eintraege = [
        {"slug": "gmail-transfer-d03e0d2e", "finished_at": 1_784_543_069.0,
         "exec_runtime": 522_318.5, "status": "error"},
        {"slug": "gmail-transfer", "finished_at": 1_785_833_522.0,
         "exec_runtime": 2.8, "status": "complete"},
    ]
    aus = _local_run_status_aus(eintraege)
    assert aus["gmail-transfer"]["exec_runtime"] == 2.8
    assert aus["gmail-transfer"]["status"] == "complete"


def test_local_run_status_folds_pinned_suffix_but_not_a_real_slug():
    from bibi.controller import _local_run_status_aus
    aus = _local_run_status_aus([
        {"slug": "EngineCI-46ec57c7", "finished_at": 100.0, "exec_runtime": 1.0},
        {"slug": "20260728.at-150738-81ec", "finished_at": 200.0, "exec_runtime": 2.0},
    ])
    assert "EngineCI" in aus
    # Ein echter Slug darf auf acht Hex-Zeichen enden — hier wird nur
    # zurueckgerechnet, wenn die Basis auch vorkommt.
    assert "20260728.at-150738-81ec" in aus


def test_scheduler_probe_backs_off_after_a_failure():
    """Ein abwesender Scheduler darf den Seitenaufbau nicht blockieren.

    Befund m.rau: *„Aber die Abfrage dauert lange. Der Disconnected Status
    muss irgendwie geprueft werden, ja. Aber er darf die UX nicht stoeren."*
    Der Client-Timeout steht auf 5 s, und die wartet **jeder** Seitenaufbau ab.

    Nach dem ersten Fehlschlag wird deshalb eine Weile gar nicht erst
    probiert — der Screen ist bei offline dann schneller als bei online, was
    richtig ist: es gibt nichts zu holen.

    Rot war: `versuche == 3` statt `1`.
    """
    from bibi.controller import _Backoff

    b = _Backoff(pause=15.0)
    assert b.darf(now=100.0), "der erste Versuch muss laufen"
    b.fehlschlag(now=100.0)
    assert not b.darf(now=101.0), "direkt danach wird nicht erneut probiert"
    assert not b.darf(now=114.9)
    assert b.darf(now=115.1), "nach der Pause wieder"


def test_scheduler_probe_resets_after_success():
    from bibi.controller import _Backoff

    b = _Backoff(pause=15.0)
    b.fehlschlag(now=100.0)
    b.erfolg()
    assert b.darf(now=101.0), "nach einer geglueckten Antwort keine Pause mehr"


# ── #31: die Filter ziehen an die Spalten, die sie einschränken ─────────────
#
# **Befund m.rau:** *„Der Filter nimmt sehr viel Platz ein. Unnötig viel
# Platz."* — und der Grund dafür ist nicht die Größe der Knöpfe, sondern eine
# Doppelung: `TYPE` und `STATUS` stehen als Spaltenkopf **und** als Gruppenlabel
# der Filterleiste. Dasselbe Wort zweimal auf demselben Screen, einmal als
# Überschrift und einmal als Beschriftung.
#
# **Der Kopf behält die Sortierung.** Er trägt danach beides: Klick sortiert,
# die Werte darunter filtern. Das ist der ganze Umbau — kein neues Konzept,
# sondern zwei Dinge an einen Ort, die schon immer über dieselbe Spalte
# sprachen.


def _kopf(html: str) -> str:
    """Nur der Tabellenkopf. Die Abgrenzung ist hier der Prüfgegenstand: es
    geht ausdrücklich darum, wo die Knöpfe stehen, nicht ob es sie gibt."""
    i, j = html.index("<thead>"), html.index("</thead>")
    return html[i:j]


def _vor_der_tabelle(html: str) -> str:
    """Alles vor der Tabelle — dort stand die Filterleiste bisher."""
    return html[:html.index("<table")]


def test_the_type_filters_live_under_their_column():
    """`TYPE` filtert die TYPE-Spalte — also gehören seine Werte an sie.

    Rot vor `#31`: die Knöpfe stehen in der Leiste über der Tabelle, der Kopf
    kennt sie nicht."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    kopf = _kopf(html)
    for wert in ("job", "claude", "app"):
        assert f'data-filter="{wert}"' in kopf, (
            f"der TYPE-Filter {wert!r} steht nicht im Tabellenkopf")


def test_the_status_filters_live_under_their_column():
    """Dieselbe Zusage für `STATUS` — und sie wirkt weiterhin ausschließlich
    auf den Scheduler-Zustand. Der Client-Zustand ist Anzeige, kein
    Filterkriterium (Klarstellung m.rau)."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    kopf = _kopf(html)
    for wert in ("waiting", "running", "stopped"):
        assert f'data-filter="{wert}"' in kopf, (
            f"der STATUS-Filter {wert!r} steht nicht im Tabellenkopf")


def test_the_column_name_is_no_longer_printed_twice():
    """Der eigentliche Befund: `TYPE` und `STATUS` standen zweimal da.

    Der Gruppenlabel der Filterleiste entfällt — die Spalte, unter der die
    Werte jetzt hängen, beschriftet sie bereits."""
    vorne = _vor_der_tabelle(render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW))
    assert 'class="fltr-grp"' not in vorne, (
        "über der Tabelle stehen weiterhin Gruppenlabels — dieselben Wörter, "
        "die einen Zentimeter tiefer als Spaltenkopf stehen")


def test_the_head_still_sorts_after_the_filters_moved_in():
    """Die Gegenprobe, ohne die der Umbau still etwas kaputt macht: der Kopf
    trägt jetzt zwei Bedeutungen, und die ältere muss die neue überleben."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW,
                              sort="next", direction="desc")
    kopf = _kopf(html)
    assert 'data-sort="next"' in kopf, "die Spalte sortiert nicht mehr"
    assert "↓" in kopf, "die Sortierrichtung wird nicht mehr angezeigt"
    for schluessel in ("slug", "type", "status", "last", "next", "24h"):
        assert f'data-sort="{schluessel}"' in kopf, schluessel


def test_the_count_stays_out_of_the_table_head():
    """Die Kennzahl bleibt, wo sie ist — sie beschreibt die Auswahl, nicht eine
    Spalte. Vorschlag 1 der Design-Studie setzt sie rechts in die Toolbar-Zeile,
    und dort ist sie eine Aussage über das Ganze."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    assert 'class="fltr-zahl"' in _vor_der_tabelle(html)
    assert 'class="fltr-zahl"' not in _kopf(html)


# ── #31: die dritte Achse wirkt über alle Bänder ───────────────────────────
#
# Sie hing am Journal-Band und filterte nur dort. Zwei ihrer drei Werte
# beschreiben aber Eigenschaften, die ein Job in **jedem** Band haben kann:
# `local` trifft jeden Job mit lokalen Läufen — `EngineCI` steht im
# SCHEDULE-Band und hat neun —, `1shot` jeden `at`-Job. Nur `gone` ist
# journal-eigen.
#
# **Ein Filter, der nur auf einem Drittel der Zeilen wirkt, aber über allen
# steht, ist keine Achse, sondern eine Falle.**


def _mit_lauf(slug):
    """Ein Schedule-Job, der lokal schon gelaufen ist — der Fall `EngineCI`."""
    return {slug: {"status": "complete", "started_at": NOW - 600}}


def test_local_selects_within_the_schedule_band():
    """`local` ist eine Eigenschaft des Jobs, nicht des Bandes.

    **Geprüft wird, was verschwindet, nicht was bleibt.** Ein Filter, der auf
    ein Band gar nicht wirkt, lässt dort ebenfalls jede Zeile stehen — ein Test
    auf „der Treffer ist noch da" wäre also auch dann grün, wenn nichts
    filtert. Erst der Nicht-Treffer daneben macht die Aussage prüfbar."""
    zeilen = _zeilen(local=[_md("mit-lauf"), _md("ohne-lauf")],
                     local_runs=_mit_lauf("mit-lauf"))
    assert all(z.segment is Segment.SCHEDULE for z in zeilen), \
        "Testdatum im falschen Band"
    html = render.jobs_screen(zeilen, now=NOW, journal=["local"])
    assert "mit-lauf" in html, "`local` lässt den Job mit lokalen Läufen fallen"
    assert "ohne-lauf" not in html, (
        "`local` behält einen Job ohne lokale Läufe — der Filter wirkt im "
        "SCHEDULE-Band nicht, er lässt dort nur alles durch")


def test_oneshot_selects_within_the_schedule_band():
    """Dieselbe Bauart für `1shot`: der at-Job bleibt, der Cron-Job geht."""
    zeilen = _zeilen(local=[_md("einmalig", schedule=None, at="2026-09-01 10:00"),
                            _md("stuendlich")])
    html = render.jobs_screen(zeilen, now=NOW, journal=["1shot"])
    assert "einmalig" in html, "`1shot` trifft den at-Job nicht"
    assert "stuendlich" not in html, "`1shot` behält einen Cron-Job"


def test_gone_stays_a_journal_matter():
    """Die Gegenprobe: `gone` beschreibt ein Verhältnis zum Vault, das ein
    aktiver Job per Definition nicht hat. Es darf nicht plötzlich alles
    treffen, nur weil die Achse jetzt überall wirkt."""
    zeilen = _zeilen(local=[_md("da")])
    html = render.jobs_screen(zeilen, now=NOW, journal=["gone"])
    assert "da</a>" not in html, "`gone` trifft einen Job, den es noch gibt"


# ── #31/Vorschlag 1: Beziehungslabels als Chips ────────────────────────────
#
# **Befund m.rau:** *„Aktuell ist die Visualisierung in `(...)`. Das folgt dem
# Terminal-Ansatz. Aber gerade hier wollen wir Aufmerksamkeit lenken. Deshalb
# sind Chips geeignet."*
#
# Die Abstufung ist der Inhalt, nicht die Form: `new`/`modified`/`deleted`/
# `dropped` beschreiben ein Verhältnis zwischen zwei Speichern und verlangen
# Kenntnis — vier ruhige Chips. `duplicate` meldet einen Fehler im Vault und
# verlangt Handeln. **Sind alle fünf gleich laut, ist keiner mehr laut.**


def test_the_relation_is_a_chip_not_a_parenthesis():
    """Die Klammern waren ein Wireframe-Zeichen und wurden wörtlich gebaut."""
    html = render.jobs_screen(_zeilen(local=[_md("neu")]), now=NOW)
    assert '<span class="chip' in html, "die Beziehung steht nicht als Chip da"
    assert "(new)" not in html, "die Klammer-Schreibweise steht noch da"


def test_only_duplicate_shouts():
    """Der einzige rote Chip — und die Gegenprobe dazu in einem Test, weil die
    Aussage eine über den *Unterschied* ist: ein Fehler im Vault fällt nur auf,
    solange die vier ruhigen daneben ruhig bleiben."""
    laut = render.jobs_screen(
        _zeilen(local=[_md("doppelt", repo_path="case/a/doppelt.md"),
                       _md("doppelt", repo_path="case/b/doppelt.md")]), now=NOW)
    assert 'class="chip bad"' in laut, "`duplicate` ist nicht als Fehler markiert"

    leise = render.jobs_screen(_zeilen(local=[_md("neu")]), now=NOW)
    assert 'class="chip bad"' not in leise, (
        "ein gewöhnliches `new` trägt dieselbe Warnfarbe wie ein Vault-Fehler")


# ── #11: `fällig` sieht aus wie `geplant` ──────────────────────────────────
#
# Ein `pending`-Job, dessen Termin verstrichen ist, wird vom Scheduler beim
# nächsten Tick geholt — der Termin gehört ihm zu Recht. Er ist keine
# Karteileiche, sondern eine Verspätung: der Knoten war offline, der Scheduler
# stand, der Backoff lief.
#
# Die Spalte zeigt darüber heute nur einen Zeitpunkt und überlässt dem Leser
# die Subtraktion gegen das heutige Datum.
#
# **Ergänzen statt ersetzen.** `asap` *statt* `02/07 08:05` wirft weg, wie
# lange etwas überfällig ist — bei zwei Sekunden egal, bei 38 Tagen nicht. Vor
# allem aber wäre `asap` allein wieder eine Relativangabe und verlöre die
# Eigenschaft, um derentwillen die Entscheidung vom 2026-08-03 fiel: **ein
# absoluter Zeitpunkt bleibt nach einem Screenshot wahr.**


def _wartend_mit(next_fire_at):
    return _zeilen(local=[_md("wartet")],
                   scheduler=[{"slug": "wartet", "active": 1,
                               "schedule": "0 * * * *", "row_status": "pending",
                               "next_fire_at": next_fire_at}])


def test_an_overdue_job_is_marked_as_due():
    """Rot vor `#11`: die Zelle ist von der eines künftigen Termins nicht zu
    unterscheiden, außer man rechnet gegen heute."""
    html = render.jobs_screen(_wartend_mit(NOW - 3600), now=NOW)
    zelle = _zelle(html, "next")
    assert "due" in zelle, (
        f"NEXT zeigt {zelle!r} — ein verstrichener Termin sieht aus wie ein "
        "geplanter")


def test_the_timestamp_survives_the_marking():
    """Der Kern der Entscheidung: die Kennzeichnung **ergänzt**, sie ersetzt
    nicht. Sonst wäre nicht mehr abzulesen, ob es zwei Sekunden oder 38 Tage
    sind."""
    html = render.jobs_screen(_wartend_mit(NOW - 40 * 86400), now=NOW)
    zelle = _zelle(html, "next")
    assert any(z.isdigit() for z in zelle), (
        f"NEXT zeigt {zelle!r} — der Zeitpunkt ist der Kennzeichnung gewichen")


def test_a_future_appointment_looks_unchanged():
    """Die Gegenprobe. Ohne sie wäre auch ein Screen grün, der alles markiert."""
    html = render.jobs_screen(_wartend_mit(NOW + 3600), now=NOW)
    assert "due" not in _zelle(html, "next"), "ein künftiger Termin ist markiert"


def test_a_terminal_job_with_a_stale_date_is_not_touched():
    """Ausdrücklich nicht Teil von `#11`: ein terminaler Job mit stehen-
    gebliebenem Termin ist `#97` — ein **Datenfehler**, der nicht dadurch
    heilt, dass man ihn hübscher rendert. Er darf `due` nicht tragen: das
    hieße zu behaupten, er komme noch."""
    zeilen = _zeilen(local=[_md("fertig")],
                     scheduler=[{"slug": "fertig", "active": 1,
                                 "schedule": "0 * * * *", "row_status": "error",
                                 "next_fire_at": NOW - 3600}])
    assert "due" not in _zelle(render.jobs_screen(zeilen, now=NOW), "next")


#: Die Spalten der Jobs-Tabelle in ihrer Reihenfolge. Die Zellen tragen **keine
#: Klassen** — bis auf `slug` sind es nackte `<td>`. Ein Test, der nach Position
#: greift, ist deshalb hier keine Nachlässigkeit, sondern die einzige ehrliche
#: Möglichkeit: eine Klasse zu erfinden, damit der Test hübscher wird, hieße den
#: Prüfgegenstand für die Prüfung zu ändern.
#:
#: **Seit #135 neun statt acht**, in der Ordnung Job → Scheduler → Client. Der
#: Helfer hat den Umbau beim ersten Lauf gefangen und dabei genau das getan,
#: wofür seine Zusicherung da ist: er ist nicht still falsch geworden, sondern
#: hat gesagt, dass er nachziehen muss. `client_last` ist neu.
_SPALTEN = ("slug", "type", "runtime", "24h",
            "scheduler", "last", "next",
            "client", "client_last")


def _zelle(html: str, spalte: str) -> str:
    """Der Text der Zelle `spalte` aus der ersten Datenzeile."""
    import re
    zeilen = re.findall(r"<tr[ >](?:(?!</tr>).)*</tr>", html, re.S)
    daten = [z for z in zeilen if 'class="slug"' in z]
    assert daten, f"keine Datenzeile im HTML gefunden (Spalte {spalte})"
    zellen = re.findall(r"<td[^>]*>(.*?)</td>", daten[0], re.S)
    assert len(zellen) == len(_SPALTEN), (
        f"{len(zellen)} Zellen, erwartet {len(_SPALTEN)} — die Tabelle hat "
        f"ihre Spalten geändert, dieser Helfer muss nach: {zellen}")
    roh = zellen[_SPALTEN.index(spalte)]
    return re.sub(r"<[^>]+>", "", roh).strip()




# ── #39: die Slot-Kachel sagt zu wenig ─────────────────────────────────────
#
# **Befund m.rau:** *„sei in der Job Kachel Client und Scheduler ruhig etwas
# informativer. … steht nur die Uhrzeit bei idle - last. Warum nicht das
# Datum?"*
#
# `_abs_time()` liefert nur `HH:MM`. Bei einem Job, der zuletzt vor drei Tagen
# lief, steht dort `last 14:03` — eine Angabe, die falsch gelesen wird, weil
# sie „heute" suggeriert.
#
# **Der Header macht es an dieser Stelle bereits richtig:** `_uhrzeit()` nimmt
# unter 24 Stunden die Uhrzeit allein und darüber Datum plus Uhrzeit (FE §2).
# Die Kachel benutzte diese Regel nicht — ein Funktionstausch, kein neues
# Konzept, und er behebt eine echte Fehllesung.


def _kachel(**kw):
    from bibi.controller.jobs_view import Tile
    # **Client-Seite und ein Zustand ohne eigenen Lauf** — nur dort steht
    # `last` überhaupt. Der Scheduler-Slot zeigt nach vorn (`next`), der
    # Client-Slot zurück; die Angabe, um die es in #39 geht, gibt es also
    # genau in dieser Konstellation.
    grund = {"quelle": "client", "host": "h", "slot": {}, "status": "pending",
             "aktionen": frozenset()}
    return Tile(**{**grund, **kw})


def _last_wert(html: str) -> str:
    """Nur der Wert hinter `last` — bis zum nächsten Tag oder Trenner.

    Ohne diese Abgrenzung greift ein naives Slicing das `/` aus `</div>` mit
    und der Test ist grün, gleich was die Kachel sagt."""
    import re
    m = re.search(r"last ([^<·]+)", html)
    assert m, f"die Kachel nennt kein `last`: {html[:200]}"
    return m.group(1).strip()


def test_a_last_run_older_than_a_day_shows_its_date():
    """Rot vor `#39`: `last 14:03` für einen Lauf von vorgestern."""
    wert = _last_wert(render.job_tiles_fragment(
        [_kachel(last_at=NOW - 3 * 86400)], now=NOW, slug="j", job_uid="u"))
    assert "/" in wert, f"die Kachel nennt kein Datum: {wert!r}"


def test_a_last_run_today_stays_short():
    """Die Gegenprobe — und der Grund, warum es die 24-Stunden-Regel ist und
    nicht „immer Datum": in der Kachel eines Jobs, der vor zehn Minuten lief,
    wäre das Datum Lärm."""
    wert = _last_wert(render.job_tiles_fragment(
        [_kachel(last_at=NOW - 600)], now=NOW, slug="j", job_uid="u"))
    assert "/" not in wert, f"unnötiges Datum bei einem Lauf von heute: {wert!r}"


def test_the_tile_names_the_runtime_of_the_last_run():
    """Punkt 2 von `#39`: die Dauer liegt im Journal (`exec_runtime`) und stand
    auf der Kachel nicht — obwohl sie die Frage beantwortet, die man vor einem
    Klick auf START stellt."""
    wert = render.job_tiles_fragment(
        [_kachel(last_at=NOW - 3 * 86400, slot={"exec_runtime": 92.0})],
        now=NOW, slug="j", job_uid="u")
    assert "1m 32s" in wert, f"die Runtime fehlt: {wert[:400]}"


def test_the_tile_names_the_commit_when_there_is_one():
    """Punkt 3. Die Zelle bleibt oft leer — heute in 93 % der Läufe —, und das
    ist in Ordnung: sie ist die Verbindung Lauf ↔ Vault-Wirkung, **und ihre
    Leere ist selbst eine Auskunft.**"""
    wert = render.job_tiles_fragment(
        [_kachel(last_at=NOW - 3 * 86400, slot={"commit_sha": "a1b2c3d4e5f6"})],
        now=NOW, slug="j", job_uid="u")
    assert "a1b2c3d" in wert, f"der Commit fehlt: {wert[:400]}"


def test_a_tile_without_a_commit_says_nothing_about_it():
    """Die Gegenprobe: ein leeres `commit —` sähe aus wie ein Fehler, wo nur
    nichts zu sagen ist. Dieselbe Erwägung, aus der `last` ohne lokalen Lauf
    ganz entfällt."""
    wert = render.job_tiles_fragment(
        [_kachel(last_at=NOW - 3 * 86400, slot={})], now=NOW, slug="j", job_uid="u")
    assert "commit" not in wert, f"leere Commit-Angabe: {wert[:400]}"


def test_the_tile_offers_a_way_to_the_run():
    """Punkt 4, und der eigentliche Befund: *„Warum kein Link runter zu den
    Details, wo ich auch den Output öffnen kann!?"* — heute ist die Kachel eine
    Sackgasse, sie nennt einen Lauf und bietet keinen Weg dorthin."""
    wert = render.job_tiles_fragment(
        [_kachel(last_at=NOW - 3 * 86400, slot={})], now=NOW, slug="j", job_uid="u")
    assert 'href="#runs"' in wert, f"kein Weg zum Lauf: {wert[:400]}"
