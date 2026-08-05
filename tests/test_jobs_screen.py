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


def test_all_three_bands_are_always_there():
    """Auch leer — sonst verschiebt sich das Layout, je nachdem was gerade
    existiert, und man sucht ein Band, das nur gerade nichts enthält."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    for band in ("SCHEDULE", "ADHOC", "JOURNAL"):
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
    und die beiden zeigen regelmäßig Verschiedenes."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    assert "SCHEDULER" in html and "LOCAL" in html


# ── Die LOCAL-Spalte liest beide Speicher ──────────────────────────────────


def _zeile_von(html: str, slug: str) -> str:
    """Die eine ``<tr>`` dieses Slugs — nicht die ganze Seite.

    Die vierte Lehre aus m.rau/bibi#131: ein ``"X" in html`` prüft die Seite und
    nicht das Element. ``running`` steht auch im Filter-Knopf der Kopfleiste,
    ein Test darauf wäre grün, ohne dass die Zelle je gefüllt würde.
    """
    for tr in re.findall(r"<tr>.*?</tr>", html, re.S):
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
    html = render.jobs_screen(_zeilen(local=[_md("frisch")]), now=NOW)
    assert "(new)" in html


def test_duplicate_is_the_only_red_label():
    """Als einziges meldet es ein Problem im Vault statt eines Verhältnisses
    zwischen zwei Speichern — es verlangt eine Umbenennung, keinen Sync."""
    zeilen = _zeilen(local=[_md("Backup", repo_path="case/eins/Backup.md"),
                            _md("Backup", repo_path="case/zwei/Backup.md")])
    html = render.jobs_screen(zeilen, now=NOW)
    assert "(duplicate)" in html
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


def test_the_journal_filters_sit_at_their_band():
    """Die Staffelung ist der Grund für Bänder: eine gestaffelte Filtermenge
    braucht einen Ort je Staffel. Die drei wirken nur im dritten Band, also
    stehen sie dort — nicht oben bei den anderen."""
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    oben = html.split("JOURNAL", 1)[0]
    for wert in ("dropped", "oneshot", "local"):
        assert f'data-filter="{wert}"' not in oben, wert
        assert f'data-filter="{wert}"' in html, wert


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


def test_nothing_links_to_the_old_routes_anymore():
    """Ein toter Link ist schlimmer als eine fehlende Route: er sieht aus wie
    ein Weg."""
    quelle = (Path(render.__file__)).read_text()
    assert '"/-/ui/jobs"' not in quelle
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
    zeile = [z for z in html.split("<tr>") if "nie-gelaufen" in z][0]
    assert zeile.count("<td>—</td>") >= 2, "STATUS und RUNTIME der lokalen Seite"


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
    assert 'data-bus-refetch="/-/jobs/list"' in html


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
    assert 'data-bus-refetch="/-/jobs/list"' in frag


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
    assert "<td>3m 51s</td>" in _zeile_von(html, "EngineCI")


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


def test_runtime_sits_on_the_scheduler_side_of_the_row():
    """FE §4.3: *„der Scheduler weiss, wann es wieder laeuft, und wie lange es
    dauert. Beide Angaben sind gefragt, beide gehoeren ihm."* Also steht die
    Spalte unter `SCHEDULER` neben `NEXT` — nicht unter `LOCAL`, wo die
    ASCII-Skizze aus §4.2 sie noch fuehrte.
    """
    html = render.jobs_screen(_zeilen(local=[_md("a")]), now=NOW)
    assert '<th colspan="4" class="grp">SCHEDULER</th>' in html
    assert '<th colspan="1" class="grp">LOCAL</th>' in html


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
