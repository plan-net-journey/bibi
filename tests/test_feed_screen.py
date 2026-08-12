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

def test_reach_states_the_window():
    """**Hier stand bis `#34` zusätzlich der Umfang** (`2 units, 5 changes`),
    mit der Begründung, ein LOAD MORE ohne Ertrag sei sonst von „da war nichts"
    nicht zu unterscheiden. Die Sorge war berechtigt, die Antwort falsch: sie
    stellt vor jedem Klick zwei Zahlen hin, um eine Frage nach dem Klick zu
    beantworten. Befund m.rau: *„nimm die folgende Anzeige komplett aus dem
    Feed Screen raus."* Die Reichweite bleibt und steht am Knopf."""
    html = render.feed_fragment(
        {"entries": [{"unit": "a", "changes": 2}, {"unit": "b", "changes": 3}]},
        days=7)
    assert "showing 7 days" in html
    assert "2 units" not in html

def test_reach_uses_plural_days_beyond_one():
    html = render.feed_fragment(_daten(_entry("a", _T)), days=7)
    assert "showing 7 days" in html


def test_load_more_widens_the_window_by_one_day():
    """Der Knopf öffnet weiterhin einen Tag mehr — seine Beschriftung nennt
    seit `#34` aber die **aktuelle** Reichweite statt der künftigen. Sie
    beantwortet „warum sehe ich nicht mehr?"; die künftige Zahl beantwortete
    nichts, was ein Klick nicht sofort gezeigt hätte."""
    html = render.feed_fragment({"entries": []}, days=2)
    assert 'days=3' in html, "der Knopf öffnet nicht einen Tag mehr"
    assert "LOAD MORE · showing 2 days" in html

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


# ── Eine Gruppen-Kopfzeile für beide Screens (#31, Vorschlag 1) ───────────
#
# **Die Studie verlangt eine Komponente, nicht zwei Nachbauten:** Kapitälchen-
# Label, Anzahl, Haarlinie bis zum rechten Rand — für die Jobs-Bänder *und* die
# Feed-Tagesgruppen. Gebaut waren sie getrennt und darum ungleich: der Feed
# hatte die Haarlinie und keine Anzahl, die Jobs-Bänder die Anzahl und keine
# Haarlinie.
#
# **Die Hüllen bleiben verschieden, und das ist kein Mangel.** Ein Bandkopf ist
# eine Tabellenzeile, eine Tagesgruppe ein `div` — dasselbe Markup zu erzwingen
# hieße, eine der beiden Tabellen aufzugeben. Geteilt wird der *Inhalt* und die
# *Form*, nicht das Element.


def test_both_group_headers_share_one_component():
    """Beide Screens rufen dieselbe Funktion — geprüft an ihrer Signatur.

    Ein Test, der nur zwei HTML-Schnipsel vergleicht, wäre auch grün, wenn
    jemand die Form ein zweites Mal von Hand nachbaut; genau so sind die beiden
    auseinandergelaufen.
    """
    quelle = Path(render.__file__).read_text()
    assert quelle.count("_gruppenkopf(") >= 3, (
        "erwartet: die Definition und je ein Aufruf aus Jobs und Feed")


# ── Chrome gegen Daten: zwei Schriften, eine Aussage (#36) ────────────────
#
# **Vorschlag 2 der Design-Studie**: die Chrome-Ebene (Navigation, Gruppen-
# labels, Filter, Spaltenköpfe, Buttons) trägt eine System-Sans, jeder Wert
# bleibt Monospace. *„Die Zeile soll auf den ersten Blick unterscheidbar
# machen, was Struktur und was Daten ist."*
#
# **Der Ausgangszustand war schärfer, als das Ticket ihn beschreibt:** der
# `body` setzte Monospace per `font:`-Kurzform, 21 weitere Deklarationen
# wiederholten dieselbe Familie lokal — **keine einzige Sans im ganzen
# Renderer.** Die Umstellung ist deshalb keine Ergänzung, sondern eine Umkehr
# der Grundlage.


def _css() -> str:
    return re.search(r'_CSS = """(.*?)"""',
                     Path(render.__file__).read_text(), re.S).group(1)


def test_the_chrome_carries_a_sans_face():
    """Der `body` führt die Chrome-Ebene, weil sie die größere ist."""
    body = re.search(r"body\s*\{[^}]*\}", _css()).group(0)
    assert "system-ui" in body, f"der body traegt keine System-Sans: {body}"
    assert "ui-monospace" not in body, "der body traegt weiterhin Monospace"


def test_a_monospace_face_still_exists_somewhere():
    """Die Gegenprobe zum Test darüber: die **ganze** Seite auf Sans wäre die
    schlimmere Regression — Zeiten, Hashes und Zähler verlören ihre
    Spaltentreue, und zwar unauffällig, weil eine Sans-Tabelle nicht kaputt
    aussieht, sondern nur unruhig.

    **Hier stand bis `v0.8.6` `test_the_values_stay_monospace`, und der Test war
    der Fehler.** Er prüfte, dass `table.jobs td` als Ganzes Monospace trägt —
    also genau die Regel *„Chrome gegen Wert"*, die `v0.8.4` gebaut hat,
    während die Zusage der Klammer *„committet gegen flüchtig"* lautete. Er war
    grün, weil er dieselbe falsche Achse benutzte wie der Code, den er bewachte
    (`#149`).

    **Ein Test, der nur *irgendeinen* Unterschied verlangt, wiederholt diesen
    Fehler.** Deshalb bleibt hier nur die grobe Gegenprobe, und die feldweise
    Zuordnung steht namentlich in `tests/test_schrift_nach_herkunft.py`.
    """
    assert "ui-monospace" in _css(), "keine Monospace mehr im ganzen Renderer"


def test_number_columns_hold_their_width():
    """Die Zusage aus dem Prüfumfang der Klammer: *Zahlenspalten tragen
    `tabular-nums`*. Ohne sie zappelt eine Spalte beim Stellenwechsel — die
    Design-Studie hat 39 % Breitenunterschied zwischen den Ziffern der
    System-Sans gemessen, und mit Vorschlag 2 kommt genau diese Schrift ins
    Spiel."""
    css = _css()
    for regel in ("table.jobs td", ".relia-p", ".dur"):
        block = re.search(rf"{re.escape(regel)}\s*\{{[^}}]*\}}", css)
        assert block and "tabular-nums" in block.group(0), regel


def test_no_wireframe_brackets_in_the_markup():
    """#32: *„Eckige Klammern weg, überall — `[show]`, `[START]`, `[ATTRS]`,
    `[LOAD MORE]`. Das war ein Wireframe-Zeichen für ‚hier ist eine Aktion' und
    wurde wörtlich gebaut; im Browser trägt die Form das schon."*

    **Der Test greift die Quelle und nicht einen gerenderten Screen**, weil die
    Klammern über fünf Screens verteilt sind und der letzte Rest sonst genau
    dort stehen bliebe, wo kein Test hinsieht. Dieselbe Bauart wie der
    Klassen-Wächter darüber, aus demselben Grund.

    Gesucht wird nur **sichtbarer** Text: eckige Klammern in Regexen,
    JS-Arrays, CSS-Selektoren und Typannotationen sind keine Wireframe-Zeichen,
    und ein Test, der sie mitmeldet, wird nach dem zweiten Fehlalarm entschärft
    statt befolgt.
    """
    quelle = Path(render.__file__).read_text()
    #: Ein Wireframe-Zeichen steht als Beschriftung **zwischen** Tags. Die
    #: zweite Form fängt die Fälle, in denen der Text am Zeilenanfang steht und
    #: das öffnende Tag eine Zeile höher — beim ersten Anlauf fehlten dadurch
    #: `[ LOAD MORE ]` und `[attrs]`, und der Test hätte zwei von sechs
    #: Klammern durchgelassen.
    klammer = r"\[\s*[A-Za-z][A-Za-z ]*\s*\]"
    treffer = (re.findall(rf">{klammer}<", quelle)
               + re.findall(rf"{klammer}</", quelle)
               + re.findall(rf"""textContent = ['"]{klammer}['"]""", quelle))
    assert not treffer, (
        f"{len(treffer)} Wireframe-Klammern im Markup: {', '.join(treffer)}")


#: Klassennamen, die es im Stylesheet gibt und die trotzdem nirgends als
#: ``class="…"``-Literal stehen — jede mit ihrem Grund. **Ohne diese Liste
#: waere die Gegenrichtung nicht haltbar**, und ohne Gruende waere sie eine
#: Muellhalde, in der eine tote Regel unbemerkt Unterschlupf faende.
_CSS_OHNE_MARKUP = {
    # Von htmx gesetzt, waehrend ein Request laeuft (s. _BTN_SPINNER).
    "htmx-request",
    # Kommen aus den Daten, nicht aus dem Quelltext: Slot-/Lauf-Zustaende, die
    # per f-String in `class="st {status}"` landen (Zustandsmodell §2).
    "awaiting", "complete", "deferred", "error", "failed", "killed", "new",
    "pending", "running", "starting", "zombie",
    # Git-/Sync-Zustaende, ueber _SYNC_LABEL_CLASS bzw. `tree-{…}` gesetzt.
    # `sync-` stand hier bis zum 2026-08-05 und war nie eine Regel: die Folge
    # `.tree-*/` in einem Kommentar schloss diesen vorzeitig, der Rest des
    # Satzes wurde als CSS gelesen und lieferte `.sync-` als Selektor. Mit dem
    # korrigierten Kommentar (m.rau/bibi#37) ist er weg — gefunden hat ihn die
    # Gegenprobe unten, also genau das, wofuer sie gebaut wurde.
    "ahead", "behind", "diverged", "synced",
    "sync-ahead", "sync-behind", "sync-synced", "tree-clean", "tree-modified",
    # Log-Level: `el.className = 'ln ' + (o.level||'').toLowerCase()` im
    # Live-Log-JS — die Stufe steht im Ereignis, nicht im Markup.
    "ln", "debug", "warning",
    # Per f-String an einen Zustand gehaengt: `"chip chip-on" if …`,
    # `" dimmed" if stale`, `"role-box on"`, `"fltr{an}"`, `"run run-in-slot"`.
    "chip-on", "dimmed", "role-box", "fltr", "run-in-slot", "off", "on", "ok",
    "warn", "conn-dot",
    # Element-qualifizierte Regeln (`td.v`, `.st` …): der Bezeichner steht im
    # Selektor, das Markup traegt ihn an anderer Stelle oder setzt ihn
    # zusammen. `v` und `value` sind am 2026-08-09 ausgetragen — sie gehoerten
    # zu den Kachel-Regeln, die mit `#100` entfallen sind.
    "st", "kind",
}


def test_every_css_rule_has_markup():
    """**Die Gegenrichtung, und sie fehlte** (Rueckbau-Fund vom 2026-08-04).

    Ein toter *Renderer* faellt auf — Tests werden rot. Ein totes *Stylesheet*
    nie: es wird ausgeliefert, kostet Bytes und behauptet, es gaebe das Element
    noch. Nach dem Wegfall der Faltung (`#131`) und des Archive-Screens
    (`#130`) standen so `fold`, `runhist`, `gitsegment`, `banner`, `feed-row`,
    `bandscroll`, `band-row`, `md`, `sched` und `st.overdue` ohne jedes Markup
    im Stylesheet — 33 Zeilen.

    Die Ausnahmeliste ist der Preis dafuer, dass diese Richtung ueberhaupt
    pruefbar ist: viele Klassen entstehen zur Laufzeit (aus einem Status, per
    htmx, per JS) und koennen im Quelltext gar nicht als Literal stehen. Jeder
    Eintrag traegt seinen Grund — wer eine Regel loescht, streicht auch ihn,
    und wer einen ohne Grund hinzufuegt, faellt beim Lesen auf.
    """
    quelle = Path(render.__file__).read_text()
    css = re.search(r'_CSS = """(.*?)"""', quelle, re.S).group(1)
    # Kommentare raus: `m.rau/bibi#68` und `loest .kvgrid2 ab` sind Prosa, kein
    # Selektor — sonst meldet der Test Klassen, die es nie gab.
    css_regeln = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    ohne_css = quelle.replace(css, "")

    statisch = {k for m in re.finditer(r'class="([a-z0-9 _{}-]+)"', ohne_css)
                for k in m.group(1).split() if "{" not in k}
    # Auch jedes String-Literal zaehlt: `"run run-in-slot" if im_slot else "run"`
    # setzt eine Klasse genauso wie ein statisches `class="…"`.
    literale = set(re.findall(r"""['"]([a-z][a-z0-9_-]*)['"]""", ohne_css))
    definiert = set(re.findall(r"\.([a-z][a-z0-9_-]*)", css_regeln))

    ohne_markup = sorted(definiert - statisch - literale - _CSS_OHNE_MARKUP)
    assert not ohne_markup, (
        f"{len(ohne_markup)} CSS-Regeln ohne Markup: {', '.join(ohne_markup)}. "
        "Entweder die Regel ist tot (dann raus) oder die Klasse entsteht zur "
        "Laufzeit (dann mit Begruendung in _CSS_OHNE_MARKUP).")


def test_the_exception_list_has_no_dead_entries():
    """Die Ausnahmeliste selbst darf nicht verwahrlosen: ein Eintrag, dessen
    Regel es nicht mehr gibt, ist genau die Muellhalde, gegen die der Test
    oben antritt. Ohne diese Gegenprobe waere die Liste ein Ort, an dem sich
    Totes sammelt, statt einer, an dem Gruende stehen."""
    quelle = Path(render.__file__).read_text()
    css = re.search(r'_CSS = """(.*?)"""', quelle, re.S).group(1)
    css_regeln = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    definiert = set(re.findall(r"\.([a-z][a-z0-9_-]*)", css_regeln))
    verwaist = sorted(_CSS_OHNE_MARKUP - definiert)
    assert not verwaist, (
        f"{len(verwaist)} Ausnahmen ohne CSS-Regel: {', '.join(verwaist)}")


# ── Der UNCOMMITTED-Block (m.rau/bibi#133) ────────────────────────────────


def _uncommitted(unit="Bibi5", states=("modified",), changes=2, ts=1_000_000.0):
    return [{"unit": unit, "last_changed": ts, "author": "m.rau",
             "states": list(states), "changes": changes}]


def test_uncommitted_stands_above_the_first_day_line():
    """*„Block `UNCOMMITTED` **ueber** der ersten Tagestrennlinie"* — was noch
    nicht gespeichert ist, ist juenger als jeder Commit."""
    html = render._feed_list(
        [{"unit": "Alt", "last_changed": 900_000.0, "changes": 1,
          "authors": ["bob"], "last_commit_sha": "abc1234"}],
        uncommitted=_uncommitted())
    # Nicht `index('class="gkopf"')`: die UNCOMMITTED-Zeile traegt dieselbe
    # Klasse und faende sich selbst. Die vierte Lehre aus m.rau/bibi#131 —
    # gemessen wird die Folge der Trennlinien, nicht die Stelle eines Wortes.
    #
    # `.gkopf` seit #31: beide Screens teilen sich eine Gruppen-Kopfzeile.
    trennlinien = re.findall(r'<span class="gk-label">(.*?)</span>', html)
    assert trennlinien[0] == "UNCOMMITTED"
    assert len(trennlinien) == 2, "die Tagestrennlinie muss darunter stehen bleiben"


def test_uncommitted_names_the_states_and_the_human():
    """*„muss modified, deleted, new erscheinen sowie der Autor"* — der Urheber
    ist fest der Mensch: ein Job committet, was er tut."""
    html = render._feed_list([], uncommitted=_uncommitted(states=("deleted", "new")))
    assert "deleted" in html and "new" in html
    assert "m.rau" in html


def test_uncommitted_carries_no_commit():
    """Es gibt keinen — genau deshalb ist es ein eigener Block."""
    html = render._feed_list([], uncommitted=_uncommitted())
    assert 'class="commit"' not in html


def test_without_uncommitted_the_block_is_absent():
    """Die Gegenprobe: ein leerer Block waere eine dauerhafte Zeile, die nichts
    meldet — und im Normalfall (sauberer Baum) genau das."""
    html = render._feed_list(
        [{"unit": "Alt", "last_changed": 900_000.0, "changes": 1,
          "authors": ["bob"], "last_commit_sha": "abc1234"}], uncommitted=[])
    assert "UNCOMMITTED" not in html


def test_uncommitted_alone_is_not_the_empty_state():
    """Ein Vault, in dem noch nichts committet, aber schon gearbeitet ist, hat
    etwas zu zeigen — die Leermeldung waere hier eine Falschaussage."""
    html = render._feed_list([], days=7, uncommitted=_uncommitted())
    assert "No changes" not in html
    assert "Bibi5" in html


# --- #80: der Feed hängt am Bus wie jede andere Live-Region -------------------


def test_the_feedboard_listens_on_the_bus():
    """`#feedboard` war die einzige Live-Region ohne `data-bus`.

    Ohne das Attribut findet `_EVENTS_JS` das Element nie — der Feed
    aktualisierte deshalb nur beim Seitenaufbau und beim Klick auf LOAD MORE."""
    html = render.feed_fragment(_daten(_entry("case/a", _T)), days=7)
    assert 'data-bus="feed"' in html
    assert 'data-bus-refetch=' in html


def test_the_refetch_keeps_the_window_the_user_opened():
    """Ein per LOAD MORE erweitertes Fenster darf ein Refetch nicht
    zurückdrehen (dieselbe Klasse wie #44).

    Die Reichweite steckt deshalb in der Refetch-URL selbst: das ausgetauschte
    Fragment trägt sein eigenes `days` mit, und der nächste Refetch nimmt
    wieder dasselbe."""
    html = render.feed_fragment(_daten(_entry("case/a", _T)), days=30)
    assert 'data-bus-refetch="/-/ui/feed/board?days=30"' in html


def test_without_a_window_the_refetch_stays_plain():
    """Ohne Fenster keine Fensterangabe — sonst stünde `days=None` in der URL."""
    html = render.feed_fragment(_daten(_entry("case/a", _T)), days=None)
    assert 'data-bus-refetch="/-/ui/feed/board"' in html


# ── #34: das Urheber-Format wird gehaertet ─────────────────────────────────
#
# **Anlass (Fall m.rau):** bei zehn gleich haeufigen Urhebern nennt „die
# haeufigsten" alle zehn — die Spalte laeuft ueber und sagt dabei weniger als
# zwei Namen es taeten.
#
# Hoechstens zwei Namen, dann `+n`, sortiert nach Haeufigkeit und bei
# Gleichstand alphabetisch. Die volle Liste steht im `title`.


def test_at_most_two_authors_are_named():
    html = render._feed_row({"unit": "u", "changes": 1,
                             "authors": ["a", "b", "c", "d"]})
    assert ">a, b +2<" in html, (
        f"das Urheber-Feld nennt nicht zwei Namen plus Rest: {html}")


def test_the_full_list_stays_reachable():
    """Gekuerzt heisst nicht weggeworfen — sonst waere die Spalte nach der
    Haertung weniger wert als vorher."""
    html = render._feed_row({"unit": "u", "changes": 1,
                             "authors": ["a", "b", "c"]})
    assert 'title="a, b, c"' in html, f"die volle Liste fehlt: {html}"


def test_authors_are_ordered_by_frequency_then_alphabetically():
    """**Deterministisch, und das ist der Punkt.** Bei Gleichstand entscheidet
    das Alphabet — sonst haengt die Anzeige an der Reihenfolge, in der die
    Daten ankamen, und springt zwischen zwei Ladevorgaengen."""
    html = render._feed_row({"unit": "u", "changes": 1,
                             "authors": ["zoe", "amy", "zoe", "bob"]})
    assert ">zoe, amy +1<" in html, (
        f"nicht nach Haeufigkeit, dann alphabetisch sortiert: {html}")


def test_two_authors_need_no_counter():
    """Die Gegenprobe: `+0` waere Laerm."""
    html = render._feed_row({"unit": "u", "changes": 1, "authors": ["a", "b"]})
    assert ">a, b<" in html, f"unnoetiger Zaehler: {html}"


# ── #34: die Reichweite wandert an den Knopf ───────────────────────────────
#
# **Befund m.rau:** *„nimm die folgende Anzeige komplett aus dem Feed Screen
# raus"* — gemeint sind die Zaehlungen `128 units, 2533 changes`. Sie
# beantworten keine Frage, die jemand hat: wie viele Einheiten im Fenster
# liegen, sieht man an der Liste, und die Summe der Aenderungen ist eine Zahl
# ohne Handlung.
#
# Die **Reichweite** bleibt — sie beantwortet „warum sehe ich nicht mehr?" —
# und zieht dorthin, wo die Frage entsteht: an den Knopf, der sie aendert.


def test_the_counts_are_gone():
    """Mehrere Eintraege, damit die Zaehlung in ihrer Mehrzahlform auftritt —
    mit einer einzigen Einheit hiesse sie `1 unit, 3 changes`, und ein Test auf
    `units,` waere gruen, ohne etwas zu pruefen."""
    html = render.feed_fragment(
        {"entries": [{"unit": "a", "changes": 3}, {"unit": "b", "changes": 4}]},
        days=7)
    assert "2 units" not in html and "7 changes" not in html, (
        f"die Zaehlungen stehen noch im Feed: {html[:300]}")


def test_the_reach_sits_on_the_button():
    html = render.feed_fragment({"entries": [{"unit": "a", "changes": 3}]}, days=7)
    knopf = html[html.index("<button"):html.index("</button>")]
    assert "showing 7 days" in knopf, (
        f"die Reichweite steht nicht am Knopf: {knopf}")


def test_the_reach_survives_without_a_button():
    """Ohne Fenster gibt es keinen Knopf — die Reichweite darf dann nicht
    stillschweigend mitverschwinden, sonst ist „alles" von „sieben Tage" nicht
    zu unterscheiden."""
    html = render.feed_fragment({"entries": [{"unit": "a", "changes": 3}]}, days=None)
    assert "<button" not in html, "ohne Fenster steht ein Knopf da"


# ── #34: LOAD MORE erweitert um eine Menge, nicht um einen Tag ─────────────
#
# **Befund m.rau:** an einem ruhigen Tag kommt genau eine Zeile dazu — der
# Knopf verspricht „mehr" und liefert „einen Tag weiter".
#
# Neu: Tage dazunehmen, bis **10 neue Einheiten** zusammenkommen **oder** 30
# Tage am Stück nichts brachten; dann aufhören und es sagen.


def _eintraege(tage: list[int], now: float = 1_000_000.0):
    """Je ein Eintrag an den genannten Tagen (Alter in Tagen)."""
    return [{"unit": f"u{i}", "last_changed": now - t * 86400}
            for i, t in enumerate(tage)]


def test_the_window_grows_until_ten_units_are_reached():
    """Der Kern: nicht ein Tag weiter, sondern so weit, bis es sich lohnt."""
    from bibi.controller.render import naechstes_fenster
    # Zehn Einheiten liegen alle am Tag 12 — ein Fenster von 7 Tagen muss also
    # auf 12 springen, nicht auf 8.
    eintraege = _eintraege([12] * 10)
    assert naechstes_fenster(eintraege, aktuell=7, now=1_000_000.0) == 12


def test_the_window_stops_after_thirty_barren_days():
    """Die Obergrenze, und sie ist der Grund, warum der Knopf ehrlich bleiben
    kann: ohne sie liefe die Erweiterung bei einem stillen Vault ins Leere und
    der Nutzer wartete auf etwas, das nicht kommt."""
    from bibi.controller.render import naechstes_fenster
    assert naechstes_fenster([], aktuell=7, now=1_000_000.0) == 37


def test_a_busy_window_grows_by_the_smallest_useful_step():
    """Die Gegenprobe: wo genug liegt, wird nicht weiter aufgerissen als
    nötig. Ein Fenster, das bei jedem Klick um 30 Tage waechst, ist derselbe
    Fehler in die andere Richtung."""
    from bibi.controller.render import naechstes_fenster
    eintraege = _eintraege(list(range(8, 20)))   # ab Tag 8 einer pro Tag
    assert naechstes_fenster(eintraege, aktuell=7, now=1_000_000.0) == 17
