"""Gemeinsame Navigationsleiste (``_header()``): Tab-Leiste + FOLLOW- +
THEME-Toggle + Ops-Handles (RESCAN/MAINT) auf jedem Screen — inkl. Live-Log
(User-Feedback 2026-07-04: "ziehe Rescan und Maintenance CTA auf die obere
Navigationsleiste mit FOLLOW on/off"). Der Feed-Screen war zwischenzeitlich
entfernt (2026-07-04: "entferne den Feed, den will ich nicht mehr sehen"),
kam aber mit PLAN-18 (2026-07-06, Client-Umbau) als **Home-Screen** zurück —
Schedules bleibt unter ``/-/ui/schedules`` erreichbar, ist nur nicht mehr
``/-/`` selbst."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app


class _FakeClient:
    """Scheduler-Antworten fuer die Route-Pruefung — leer, aber wohlgeformt."""

    def __init__(self, status: dict) -> None:
        self._status = status

    def status(self) -> dict:
        return self._status

    def schedules(self):
        return []

    def journal(self, **_):
        return []

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(status: dict):
        return create_app(roles.resolve({"controller"}), controller_client=_FakeClient(status))
    return _make


def test_header_has_no_follow_toggle():
    # PLAN-36 Stufe 36.3 (E8): FOLLOW komplett entfernt — Events werden immer
    # angewendet, Lesbarkeit sichert allein die Scroll-Logik (_EVENTS_JS/
    # _SCROLL_JS).
    html = render._header("Schedules")
    assert 'id="follow"' not in html and "bibiToggleFollow" not in html


def test_header_includes_theme_toggle():
    html = render._header("Schedules")
    assert 'id="theme"' in html and "bibiToggleTheme" in html


def test_header_includes_ops_handles():
    # RESCAN/MAINT sitzen jetzt direkt im Header, nicht mehr als separater Aufruf.
    html = render._header("Schedules", {"maintenance": True, "roles": ["scheduler"]})
    assert 'id="rescan"' in html
    assert 'id="maint" class="toggle warn"' in html


# --- RESCAN generisch, keine Sync-Dopplung mehr (PLAN-21 Befund 2, revidiert
# --- PLAN-20 Befund 5: SYNC stand gleichzeitig im Button UND in der Git-Karte)


def test_ops_handles_rescan_is_always_generic():
    html = render._ops_handles({})
    assert 'id="rescan" class="toggle" title="rescan the vault">⟳<' in html


def test_ops_handles_js_restores_idle_icon():
    js = render._OPS_HANDLES_JS
    assert "const idleIcon = rescan.textContent" in js
    assert "rescan.textContent = idleIcon" in js


def test_maint_toggle_dispatches_event_for_mode_card_refresh():
    # Bibi4-Iteration, User-Fund: "ein Klick auf Maintenance muss ein Update
    # der Mode Card nach sich ziehen" — die Mode-Kachel hängt sonst bis zu 30s
    # im separat gepollten #feedstatus-Bundle fest.
    js = render._OPS_HANDLES_JS
    assert "document.body.dispatchEvent(new Event('bibiMaintChanged'))" in js


def test_feedstatus_fragment_also_refreshes_on_maint_changed_event():
    html = render.feed_status_fragment({}, None, None, now=100.0)
    assert "bibiMaintChanged from:body" in html


def test_feed_header_rescan_ignores_git_status():
    # Regressionstest für PLAN-21 Befund 2: git_status darf die RESCAN-
    # Beschriftung nicht mehr beeinflussen, egal wie der Sync-Zustand steht —
    # der lebt jetzt ausschließlich in der Git-Karte.
    feed_data = {"entities": [], "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_page(
        feed_data, git_status={"tree": "clean", "sync": "ahead", "branch": "trunk"}, now=100.0)
    assert 'id="rescan" class="toggle" title="rescan the vault">⟳<' in html
    assert "SYNC: ahead" not in html.split('<div class="statuscards">')[0]  # nicht in der Nav


# --- Kein "Wartungsmodus aktiv"-Banner mehr (PLAN-21 Befund 3) -----------------


def test_ops_handles_has_no_maintenance_banner():
    html = render._ops_handles({"maintenance": True, "roles": ["scheduler"]})
    assert "Wartungsmodus aktiv" not in html
    assert "maintbanner" not in html
    assert 'id="maint" class="toggle warn"' in html  # Toggle bleibt die einzige Anzeige


# --- MAINT: disabled statt ausgeblendet ohne scheduler-Rolle (Bibi4-Iteration,
# --- User-Fund "eine App", revidiert PLAN-25 Befund 1) -------------------------


def test_ops_handles_disables_maint_without_scheduler_role():
    # User-Fund: der Client kennt gar keinen Maintenance-Mode, MAINT bleibt
    # funktional dem Scheduler vorbehalten — aber "eine App" heißt: sichtbar
    # bleiben, nur disabled, statt ganz zu verschwinden. RESCAN bleibt aktiv.
    html = render._ops_handles({"maintenance": True, "roles": ["controller", "connect"]})
    assert 'id="maint"' in html and "disabled" in html
    assert 'id="rescan"' in html and html.index('id="rescan"') < html.index('id="maint"')


def test_ops_handles_shows_maint_with_scheduler_role():
    html = render._ops_handles({"maintenance": False, "roles": ["scheduler"]})
    assert 'id="maint"' in html
    assert "disabled" not in html


def test_ops_handles_disables_maint_when_roles_missing():
    # Kein status/keine roles (ältere Aufrufer, Tests ohne explizite Rolle) —
    # sicherer Default ist "kein Scheduler" (disabled), nicht "zeig's aktiv".
    for html in (render._ops_handles({}), render._ops_handles(None), render._ops_handles()):
        assert 'id="maint"' in html and "disabled" in html


# --- Links/Rechts-Gruppen (Bibi4-Iteration: Tabs links, Toggles rechts,
# --- revidiert PLAN-21 Befund 1) ------------------------------------------


def test_header_splits_left_and_right_nav_groups():
    html = render._header("Schedules", {"maintenance": False})
    left = html.split('<div class="nav-left">')[1].split("</div>")[0]
    right = html.split('<div class="nav-right">')[1].split("</div>")[0]
    assert "bibi" in left
    assert 'id="rescan"' not in left and 'id="theme"' not in left
    assert ('id="rescan"' in right
           and 'id="liveclock"' in right and 'id="theme"' in right)
    assert 'id="follow"' not in html  # PLAN-36 Stufe 36.3 (E8)


def test_theme_toggle_uses_symbol_not_text_label():
    html = render._theme_toggle()
    assert ">THEME<" not in html
    assert 'id="theme"' in html and "bibiToggleTheme" in html


# --- Time-Toggle (Bibi4-Iteration, User-Fund: "Time: abs./rel./both" für die
# --- last/since- und next-Spalten) -----------------------------------------


def test_time_toggle_uses_symbol_not_text_label():
    html = render._time_toggle()
    assert 'id="time"' in html and "bibiToggleTime" in html
    assert "abs" not in html and "rel" not in html and "both" not in html


def test_time_toggle_js_cycles_through_three_states():
    js = render._TIME_JS
    assert "ORDER = ['abs', 'rel', 'both']" in js
    assert "bibiToggleTime" in js
    assert "data-timeformat" in js


def test_header_includes_time_toggle_in_right_group():
    html = render._header("Schedules")
    right = html.split('<div class="nav-right">')[1].split("</div>")[0]
    assert 'id="time"' in right


def test_time_toggle_css_hides_inactive_variants_per_mode():
    css = render._CSS
    assert 'data-timeformat="abs"] .tt-relonly' in css
    assert 'data-timeformat="rel"] .tt-abs' in css
    assert 'data-timeformat="both"] .tt-relonly' in css


def test_live_clock_placeholder_includes_date():
    html = render._live_clock()
    assert "--.--.----" in html  # Datum-Platzhalter, vorher nur Uhrzeit


def test_clock_js_renders_date_and_time():
    js = render._CLOCK_JS
    assert "toLocaleDateString" in js and "toLocaleTimeString" in js


def test_toggles_styled_as_text_links_not_boxed_buttons():
    # PLAN-19 Befund 7, User-Fund: THEME/RESCAN/MAINT sollen wie die
    # Nav-Tabs aussehen (reine Text-Links), keine Buttons mit Box/Rahmen mehr.
    # Bleiben funktional <button>-Elemente (JS-Handler), nur CSS-Klasse ändert
    # sich von "handle" auf "toggle" — kein "handle" mehr irgendwo im Markup.
    html = render._header("Schedules", {"maintenance": True, "roles": ["scheduler"]})
    assert 'class="handle"' not in html and 'class="handle ' not in html
    assert 'class="toggle"' in html  # THEME + RESCAN
    assert 'class="toggle warn"' in html  # MAINT an
    assert ".toggle {" in render._CSS


def test_toggle_icons_use_larger_uniform_font_size():
    # Bibi4-Iteration, User-Fund: "können wir große Icons - alle in gleicher
    # großer Größe - verwenden?" — FOLLOW/RESCAN/MAINT/Time/Theme teilen sich
    # alle dieselbe .toggle-Klasse, eine einzige Zahl reicht für alle.
    assert "font-size: 1.3rem" in render._CSS


def test_logbox_slug_link_has_fixed_theme_independent_color():
    # Bibi4-Iteration, User-Fund: "im Light Mode ist die Schriftfarbe lila
    # schwer zu lesen" — a.slug erbte bisher Chromes color-scheme-abhängige
    # Standard-Linkfarbe, obwohl .logbox immer dunkel bleibt. Scoped auf
    # .logbox, damit die Jobs-/Schedule-Tabellen-Slug-Links (a.slug außerhalb
    # der Log-Box) weiterhin dem Theme folgen.
    assert ".logbox a.slug { color:" in render._CSS


def test_screen_nav_feed_is_home():
    """Feed ist der Home-Screen (``/-/``) und bleibt es — das war schon vor
    bibi5 so und ist der einzige Teil der alten Nav-Logik, der überlebt."""
    html = render._screen_nav("Jobs")
    assert 'href="/-/">Feed' in html


def test_screen_nav_active_tab_has_active_class():
    # PLAN-25 Befund 2, User-Fund: der aktive Tab war bisher nur reiner Text
    # ohne eigene CSS-Klasse — "Hervorhebung" war die zufällige Abwesenheit
    # von .back-Grau, kein bewusstes visuelles Signal.
    html = render._screen_nav("Live")
    assert '<span class="tab-active">Live</span>' in html
    assert ".tab-active {" in render._CSS


def test_ops_handles_has_no_follow_button():
    html = render._ops_handles()
    assert 'id="follow"' not in html



def test_schedule_detail_page_has_header_nav_without_follow():
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_page({"slug": "a", "kind": "job"}, [], job, slug="a")
    assert 'href="/-/"' in html and 'href="/-/log"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' not in html  # PLAN-36 Stufe 36.3 (E8)


def test_schedule_detail_page_has_rescan_and_maint():
    # User-Feedback 2026-07-03: "brauchen den Rescan und Maintenance Button
    # auf Schedule Screen" — auf der Job-Detail-Seite ebenso wie auf der
    # Schedules-Liste (s.u.), außerhalb von #live/#journal (kein 2s-Re-Render).
    html = render.schedule_detail_page(
        {"slug": "a", "kind": "job"}, [], None, slug="a",
        daemon_status={"maintenance": True, "roles": ["scheduler"]})
    assert 'id="rescan"' in html
    assert 'id="maint" class="toggle warn"' in html
    assert render._OPS_HANDLES_JS in html



def test_execution_detail_page_has_header_nav_without_follow():
    entry = {"id": 1, "run_id": "x:1", "slug": "x", "kind": "job", "status": "complete",
             "started_at": 1.0, "finished_at": 2.0, "domain": "scheduled"}
    html = render.execution_detail_page(entry, [], "job")
    assert 'href="/-/"' in html and 'href="/-/log"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' not in html  # PLAN-36 Stufe 36.3 (E8)


def test_execution_detail_page_has_rescan_and_maint():
    # User-Feedback 2026-07-04: Rescan/Maintenance auf der Nav-Leiste, dadurch
    # jetzt auch auf der Execution-Detail-Seite (vorher gar nicht vorhanden).
    entry = {"id": 1, "run_id": "x:1", "slug": "x", "kind": "job", "status": "complete",
             "started_at": 1.0, "finished_at": 2.0, "domain": "scheduled"}
    html = render.execution_detail_page(
        entry, [], "job", daemon_status={"maintenance": True, "roles": ["scheduler"]})
    assert 'id="rescan"' in html
    assert 'id="maint" class="toggle warn"' in html


def test_log_page_has_rescan_and_maint_without_follow():
    # User-Feedback 2026-07-04: "Sie sind damit auch auf Live-Log sichtbar".
    # FOLLOW ist seit PLAN-36 Stufe 36.3 (E8) weg — Autoscroll pausiert im
    # Log-Panel rein ueber die Scroll-Position (paused in _LOG_JS).
    html = render.log_page(daemon_status={"maintenance": True, "roles": ["scheduler"]})
    assert 'id="rescan"' in html
    assert 'id="maint" class="toggle warn"' in html
    assert 'id="follow"' not in html and "bibiToggleFollow" not in html


# --- bibi5: eine App-Bar für alle Knoten (Umbauplan Schritt 1) ---------------
#
# „Es gibt nur noch einen Client" (FE-Spezifikation §1). Damit entfällt der
# Grund für die rollenabhängige Tab-Menge: bisher zeigte ein Scheduler-Knoten
# `/-/ui/schedules`, ein Client `/-/ui/jobs`, und beide hießen „Jobs" — dieselbe
# Beschriftung für zwei Screens, weil zwei Frontends existierten. In bibi5 gibt
# es einen Screen je Aufgabe, und jeder Knoten zeigt dieselben sechs.


def test_screen_nav_shows_the_same_six_tabs_on_every_node():
    """Sechs Screens, feste Reihenfolge, unabhängig von der Rolle.

    Die Reihenfolge ist nicht beliebig: Feed und Jobs sind die täglichen, Nodes
    ist Betrieb, Live und Log sind Diagnose. Sie steht so in der
    FE-Spezifikation §1 und in jedem Wireframe.
    """
    erwartet = [("Feed", "/-/"), ("Jobs", "/-/jobs"), ("Archive", "/-/archive"),
                ("Nodes", "/-/nodes"), ("Live", "/-/live"), ("Log", "/-/log")]
    for rollen in ([], ["scheduler"], ["connect"], ["scheduler", "worker"]):
        html = render._screen_nav("Feed", roles=rollen)
        for label, href in erwartet:
            if label == "Feed":
                continue  # aktiver Tab, absichtlich ohne Link
            assert f'href="{href}">{label}' in html, f"{label} fehlt bei roles={rollen}"


def test_screen_nav_no_longer_branches_on_roles():
    """Zwei Knoten, dieselbe Leiste — das ist der Kern von „ein Client".

    Vorher unterschieden sich Scheduler- und Client-Knoten in Tab-Menge *und*
    Zielen; ein Screenshot war ohne Kenntnis der Rolle nicht einzuordnen.
    """
    assert render._screen_nav("Feed", roles=["scheduler"]) == \
           render._screen_nav("Feed", roles=["connect"])


def test_screen_nav_drops_api_docs_and_the_old_split_routes():
    """`API Docs` war ein Fremdkörper in der Screen-Leiste — es öffnete einen
    neuen Tab auf eine generierte Seite, die kein Screen dieser App ist. Die
    Route bleibt, der Platz in der App-Bar nicht.

    Die alten rollengeteilten Ziele verschwinden mit: sie sind der Host-FE-Rest.
    """
    html = render._screen_nav("Feed", roles=["scheduler", "connect"])
    assert "API Docs" not in html
    assert "/-/ui/schedules" not in html
    assert "/-/ui/jobs" not in html
    assert "/-/ui/clients" not in html


def test_screen_nav_separates_live_from_log():
    """`Live Log` war ein Screen für zwei Dinge. Der Unterschied ist das
    Gedächtnis (FE-Spezifikation §7): Live hat keines und erzählt, was gerade
    geschieht; Log hat Historie und ist zum Nachschlagen da."""
    html = render._screen_nav("Feed", roles=["scheduler"])
    assert "Live Log" not in html
    assert 'href="/-/live">Live' in html
    assert 'href="/-/log">Log' in html


# --- bibi5: die sechs Screens haben eigene Routen ----------------------------


def test_every_screen_in_the_app_bar_is_reachable(app_with):
    """Kein Tab zeigt ins Leere.

    Die App-Bar steht auf jedem Screen und nennt sechs Ziele; existiert eines
    davon nicht, ist die Leiste selbst der Fehler — man klickt und landet im
    404. Vorher konnte das nicht passieren, weil die Leiste nur zeigte, was die
    Rolle hergab; jetzt zeigt sie immer alles und muss es auch halten.
    """
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        for label, href in render.SCREENS:
            r = c.get(href)
            assert r.status_code == 200, f"{label} ({href}) → {r.status_code}"
