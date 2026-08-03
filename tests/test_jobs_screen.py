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
    def __init__(self, status: dict) -> None:
        self._status = status

    def status(self) -> dict:
        return self._status

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def run_journal(self, **_):
        return []

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(status: dict):
        return create_app(roles.resolve({"controller"}), controller_client=_FakeClient(status))
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
