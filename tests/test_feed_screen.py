"""Feed-Screen (bibi5 Schritt 3, FE-Spezifikation §3).

Eine Zeile je geänderter Einheit, tageweise gruppiert, LOAD MORE. Heatmap,
Kategorie SYSTEM und die Filter entfallen ersatzlos.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from bibi.controller import render

_T = datetime.datetime(2026, 8, 3, 21, 37).timestamp()
_T_VORTAG = datetime.datetime(2026, 8, 2, 9, 5).timestamp()


def _entry(unit: str, ts: float, changes: int = 1, *, authors=("m.rau",),
           sha: str = "380ecee1234") -> dict:
    return {"unit": unit, "last_changed": ts, "changes": changes,
            "authors": list(authors), "last_commit_sha": sha}


def _daten(*entries: dict, base_url: str | None = None) -> dict:
    return {"entries": list(entries), "commit_base_url": base_url}


# --- die Zeile ----------------------------------------------------------------

def test_row_carries_time_unit_scope_author_and_commit():
    html = render.feed_fragment(
        _daten(_entry("20260802.Bibi5-4af706e9", _T, 51)), days=1)
    assert "21:37" in html
    assert "20260802.Bibi5-4af706e9" in html
    assert "51 changes" in html
    assert "m.rau" in html
    assert "380ecee" in html


def test_a_single_change_is_singular():
    # "1 changes" liest sich wie ein Fehler und ist einer.
    html = render.feed_fragment(_daten(_entry("memo/Release", _T, 1)), days=1)
    assert "1 change<" in html or "1 change " in html
    assert "1 changes" not in html


def test_the_job_slug_stands_where_the_author_stands():
    # Der Urheber ist der Job, nicht der git-Autor: `news-aggregator` statt
    # `m.rau · automatisiert`.
    html = render.feed_fragment(
        _daten(_entry("memo/News", _T, 12, authors=("news-aggregator",))), days=1)
    assert "news-aggregator" in html
    assert "automatisiert" not in html


def test_commit_links_to_the_remote_when_one_is_configured():
    html = render.feed_fragment(
        _daten(_entry("memo/Release", _T), base_url="http://git.example/m.rau/x"),
        days=1)
    assert 'href="http://git.example/m.rau/x/commit/380ecee1234"' in html
    assert ">380ecee<" in html  # kurzer Hash (7 Zeichen)


def test_commit_stays_plain_text_without_a_remote():
    # Gerettet aus `test_feed_row_commit_hash_plain_without_base_url`: ohne
    # konfiguriertes origin gibt es kein Linkziel, und ein Link ins Leere
    # sieht aus wie ein Weg.
    html = render.feed_fragment(_daten(_entry("memo/Release", _T)), days=1)
    assert "<a " not in html
    assert 'class="commit">380ecee<' in html


def test_time_is_absolute_never_relative():
    # Gerettet aus `test_feed_row_shows_absolute_time_not_relative`. FE §2:
    # ein absoluter Zeitpunkt bleibt nach einem Screenshot wahr und kann nicht
    # einfrieren. Der Time-Toggle des Feed entfaellt damit, nicht die Aussage.
    html = render.feed_fragment(_daten(_entry("memo/Release", _T)), days=1)
    assert "21:37" in html
    for rel in ("vor ", " ago", "tt-relonly", "tt-relboth"):
        assert rel not in html


# --- Tagesgruppierung ---------------------------------------------------------

def test_days_are_separated_and_newest_first():
    html = render.feed_fragment(
        _daten(_entry("memo/Release", _T_VORTAG), _entry("Bibi5", _T)), days=3)
    assert "03/08/2026" in html and "02/08/2026" in html
    assert html.index("03/08/2026") < html.index("02/08/2026")


def test_the_row_shows_the_time_only_because_the_day_stands_above_it():
    # Das Datum in jeder Zeile stuende zweimal da.
    html = render.feed_fragment(_daten(_entry("Bibi5", _T)), days=1)
    assert "21:37" in html
    assert html.count("03/08/2026") == 1


# --- Reichweite und LOAD MORE -------------------------------------------------

def test_reach_states_window_units_and_changes():
    # Ein LOAD MORE, das nichts mehr laedt, muss sich von "da war nichts"
    # unterscheiden lassen.
    html = render.feed_fragment(
        _daten(_entry("a", _T, 3), _entry("b", _T, 2)), days=1)
    assert "showing 1 day" in html
    assert "2 units, 5 changes" in html


def test_reach_uses_plural_days_beyond_one():
    html = render.feed_fragment(_daten(_entry("a", _T)), days=7)
    assert "showing 7 days" in html


def test_load_more_widens_the_window_by_one_day():
    html = render.feed_fragment(_daten(_entry("a", _T)), days=2)
    assert "/-/ui/feed/board?days=3" in html
    assert "LOAD MORE (3 days)" in html


# --- leerer Zustand -----------------------------------------------------------

def test_empty_state_says_what_is_missing_and_what_to_do():
    """Umbauplan §4: jeder leere Zustand ist Einstiegsdokumentation.

    `— keine Änderungen in diesem Zeitraum —` sagt weder, was hier stuende,
    noch was man tun kann.
    """
    html = render.feed_fragment(_daten(), days=1)
    assert "No changes in the last day" in html
    assert "vault/" in html
    assert "LOAD MORE" in html


def test_empty_state_names_the_window_it_looked_at():
    html = render.feed_fragment(_daten(), days=5)
    assert "the last 5 days" in html


def test_empty_state_without_a_window_says_neither_zero_days_nor_load_more():
    # Live gefunden: `?days=0` ergab „No changes in the last 0 days" und riet
    # zu einem LOAD MORE, das dort gar nicht steht.
    for ohne in (None, 0):
        html = render.feed_fragment(_daten(), days=ohne)
        assert "No changes found" in html
        assert "0 days" not in html
        assert "LOAD MORE" not in html


# --- was der Umbau entfernt ---------------------------------------------------

def test_no_heatmap_anywhere_in_the_renderer():
    """Der Nachweis, dass die Heatmap-Kette wirklich weg ist (FE §9).

    Wie bei `m.rau/bibi#120` und `#121` ein Test statt einer Liste von Hand:
    eine Liste veraltet beim ersten Vergessen, dieser Test nicht. Gegen den
    Stand vor dem Schnitt ist er rot — dort steht `_heatmap_html` in
    render.py und `heatmap_buckets` in feed.py.
    """
    from bibi import feed as feed_mod
    from bibi.daemon import app as daemon_app

    for modul in (render, feed_mod, daemon_app):
        quelle = Path(modul.__file__).read_text()
        for wort in ("heatmap", "Heatmap", "hm-cell", "hm2-"):
            assert wort not in quelle, f"{wort!r} lebt noch in {modul.__name__}"

    for name in ("heatmap_buckets", "HEATMAP_WEEKS", "activity_series_by_prefix"):
        assert not hasattr(feed_mod, name), f"feed.{name} lebt noch"


def test_the_chart_leftovers_in_the_stylesheet_are_gone():
    """Dritte Fundstelle derselben toten Kette: beim Entfernen der Charts
    blieben ihre CSS-Regeln stehen, weil sie nicht im Suchmuster lagen —
    `.chart-wrap`, die Zustands-Chips und die Aufloesungswahl."""
    quelle = Path(render.__file__).read_text()
    for regel in (".chart-wrap", ".ts-chips", ".ts-chip", ".res-link", ".ts-head"):
        assert regel not in quelle, f"{regel} lebt noch in render.py"


def test_no_category_filters_and_no_system_category():
    html = render.feed_fragment(_daten(_entry("Bibi5", _T)), days=1)
    for weg in ("filterbar", "feedkind", "feedagent", "bibiApplyFeedFilters",
                "system", "SYSTEM"):
        assert weg not in html, f"{weg!r} steht noch im Feed"


def test_the_feed_no_longer_takes_a_weeks_parameter():
    # `weeks` steuerte ausschliesslich die Heatmap-Zeilenzahl.
    import inspect
    assert "weeks" not in inspect.signature(render.feed_fragment).parameters
    assert "weeks" not in inspect.signature(render.feed_page).parameters


def test_the_default_window_is_a_week():
    """Sieben Tage, nicht einer.

    Der Ein-Tages-Default war reine Vorsicht gegen das 5-s-Timeout des
    Controller-Selbstaufrufs. Seit `agent_slugs()` mit einem git-Aufruf
    auskommt, kosten 30 Tage 0,18 s — und ein Tag sind mit der
    Ordner-Aggregation nur noch rund 14 Zeilen fuer eine Frage, die
    „was ist passiert" heisst.
    """
    from bibi.controller import _FEED_DEFAULT_DAYS
    assert _FEED_DEFAULT_DAYS == 7


def test_every_markup_class_has_a_css_rule():
    """**Der Nachweis, dass kein Screen ohne Stylesheet ausgeliefert wird.**

    Schritt 2 hat Job Detail, Attributes und Archive gebaut und dabei 27
    Klassen eingefuehrt, von denen keine einzige eine CSS-Regel bekam. Ohne
    Regel sind `<span>`s inline ohne Abstand — die Kopfzeile las sich als
    `jobsgmail-billingjob · 0 */4 * * *[ATTRS]`, die Attributseite als
    `attempts3`. Die Live-Abnahme fand es nicht, weil sie per `curl` die
    *Daten* prueft; im Browser hat niemand gesehen.

    Dieser Test ersetzt das Hinsehen nicht, aber er faengt genau den Fall ab,
    der zweimal durchgerutscht ist: eine Klasse, die es nur im Markup gibt.
    """
    quelle = Path(render.__file__).read_text()
    css = re.search(r'_CSS = """(.*?)"""', quelle, re.S).group(1)

    benutzt = {k for m in re.finditer(r'class="([a-z0-9 _-]+)"', quelle)
               for k in m.group(1).split()}
    definiert = set(re.findall(r"\.([a-z][a-z0-9_-]*)", css))
    ohne_regel = sorted(benutzt - definiert)

    assert not ohne_regel, (
        f"{len(ohne_regel)} Klassen ohne CSS-Regel: {', '.join(ohne_regel)}")
