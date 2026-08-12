"""Gemeinsame Navigationsleiste (``_header()``): Tab-Leiste + FOLLOW- +
THEME-Toggle + Ops-Handles (RESCAN/MAINT) auf jedem Screen — inkl. Live-Log
(User-Feedback 2026-07-04: "ziehe Rescan und Maintenance CTA auf die obere
Navigationsleiste mit FOLLOW on/off"). Der Feed-Screen war zwischenzeitlich
entfernt (2026-07-04: "entferne den Feed, den will ich nicht mehr sehen"),
kam aber mit PLAN-18 (2026-07-06, Client-Umbau) als **Home-Screen** zurück —
Der Schedules-Screen ist seit dem bibi5-Umbau gestrichen, sein Apparat mit
m.rau/bibi#159 zurückgebaut; hier stand, er bleibe unter
``/-/ui/schedules`` erreichbar — die Route antwortet 404
(``tests/test_jobs_screen.py`` hält das fest)."""

from __future__ import annotations

import re
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


# --- MAINT: disabled statt ausgeblendet, wenn es nichts zu schalten gibt
# --- (Bibi4-Iteration, User-Fund "eine App", revidiert PLAN-25 Befund 1;
# --- Bedingung umgestellt mit #69) --------------------------------------------
#
# Hier stand bis v0.7.5 `test_ops_handles_disables_maint_without_scheduler_role`
# und prüfte das Gegenteil: ein Client mit `connect`-Rolle bekam den Knopf
# **gesperrt**, „MAINT bleibt funktional dem Scheduler vorbehalten". Das war
# richtig, solange das Profil `scheduler` ein `controller` trug. Seit dem
# 2026-08-06 tut es das ausdrücklich nicht mehr — und damit sperrte die Regel
# genau die Knoten, die eine Oberfläche haben (#69).
#
# ⚠ Der alte Test hätte den Fix überlebt, ohne ihn zu bemerken: sein Payload
# trug die `connect`-**Rolle**, aber kein `connect`-**Dict**. Diesen Zustand
# erzeugt die Engine nie — `app.py` legt das Dict genau dann an, wenn es den
# Heartbeat gibt, und den gibt es genau bei aktiver `connect`-Rolle
# (`daemon_cmd.py`). Ein Test gegen einen unmöglichen Zustand kann grün werden,
# ohne etwas zu belegen; deshalb steht unten ein vollständiger Payload.


def test_ops_handles_enables_maint_on_a_client_with_a_scheduler():
    """#69: wer einen Scheduler hat, muss ihn auch schalten können."""
    html = render._ops_handles({
        "roles": ["synchronizer", "controller", "connect"],
        "connect": {"ok": True, "last_at": 1.0},
    })
    assert 'id="maint"' in html and "disabled" not in html
    assert 'id="rescan"' in html and html.index('id="rescan"') < html.index('id="maint"')


def test_ops_handles_disables_maint_without_any_scheduler():
    """Sichtbar bleiben, nur gesperrt — „eine App" heißt: nicht verfügbare
    Funktionen ausgegraut statt ausgeblendet. Ohne Scheduler gibt es hier
    tatsächlich nichts zu schalten."""
    html = render._ops_handles({"roles": ["synchronizer", "controller"]})
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
    html = render._screen_nav("Log")
    assert '<span class="tab-active">Log</span>' in html
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
# es einen Screen je Aufgabe, und jeder Knoten zeigt dieselben fünf.


def test_screen_nav_shows_the_same_five_tabs_on_every_node():
    """Fünf Screens, feste Reihenfolge, unabhängig von der Rolle.

    Die Reihenfolge ist nicht beliebig: Feed und Jobs sind die täglichen,
    Journal steht neben Jobs, Nodes ist Betrieb, Log ist Diagnose.

    `Archive` ist seit m.rau/bibi#130 nicht mehr dabei — die Frage „was lief"
    beantwortet die `RELIABILITY`-Spalte im Jobs-Screen schneller. `Live` ist
    seit `#162` nicht mehr dabei; er war ein Zwilling von `Log`.

    **Diese Liste stand bis dahin auf `Feed, Jobs, Nodes, Live, Log` — also auf
    fünf Einträgen, in denen `Journal` fehlte, während die Leiste sechs Tabs
    führte.** Der Test hieß „five tabs" und hat trotzdem nie fünf geprüft: er
    lief über seine eigene Liste, nicht über die der App-Bar, und ein Tab, den
    er nicht kennt, kann ihm nicht fehlen. Genau deshalb misst
    ``test_the_app_bar_has_one_tab_per_standalone_screen`` gegen
    ``render.SCREENS`` statt gegen eine zweite Aufzählung.
    """
    erwartet = [("Feed", "/-/"), ("Jobs", "/-/jobs"),
                ("Journal", "/-/jobs/journal"), ("Nodes", "/-/nodes"),
                ("Log", "/-/log")]
    assert len(render.SCREENS) == len(erwartet)
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


def test_screen_nav_active_tab_stays_clickable_on_a_subpage():
    """Der aktive Tab ist auf einer **Unterseite** ein Link (m.rau/bibi#148).

    „Der aktive Tab ist kein Link" stimmt auf dem Screen selbst — dorthin zu
    verlinken, wo man steht, ist eine Sackgasse. Auf seinen Unterseiten stimmt
    es nicht: dort ist der Tab die natürliche Zurück-Geste, und genau so wird er
    benutzt. Der Unterschied ist nicht *aktiv gegen inaktiv*, sondern **auf dem
    Screen gegen unterhalb davon**.

    Die Hervorhebung bleibt in beiden Fällen — man ist ja weiterhin in Jobs.
    """
    auf_dem_screen = render._screen_nav("Jobs")
    assert '<span class="tab-active">Jobs</span>' in auf_dem_screen

    darunter = render._screen_nav("Jobs", sub=True)
    assert '<a class="tab-active" href="/-/jobs">Jobs</a>' in darunter
    assert '<span class="tab-active">' not in darunter


def test_job_subpages_link_their_own_tab_back_to_the_screen(monkeypatch):
    """Beide Jobs-Unterseiten führen den Tab als Rückweg (m.rau/bibi#148).

    Am Renderer geprüft und nicht nur an ``_screen_nav()``: der Fehler saß
    nicht in der Nav-Funktion allein, sondern darin, dass ihr niemand sagte,
    wo sie steht. Ein Test auf die Funktion allein wäre grün gewesen, während
    die Seiten weiter tote Tabs zeigen.
    """
    spec = {"slug": "a", "kind": "job"}
    detail = render.job_detail_page_v5(slug="a", spec=spec, now=0.0)
    attrs = render.job_attrs_page_v5(slug="a", spec=spec, defaults={}, now=0.0)
    for name, html in (("detail", detail), ("attrs", attrs)):
        assert '<a class="tab-active" href="/-/jobs">Jobs</a>' in html, name


def test_screen_nav_separates_live_from_log():
    """`Live Log` war ein Screen für zwei Dinge, und der Unterschied sollte das
    Gedächtnis sein (FE-Spezifikation §7). Gebaut wurde er nie.

    **Die Absicht ist am 2026-08-12 zurückgenommen** (`#162`): `Live` fällt aus
    der Leiste, `/-/live` leitet auf `/-/log` um. Was hier bleibt, ist die eine
    Hälfte, die nie strittig war — `Live Log` ist kein Tab mehr.
    """
    html = render._screen_nav("Feed", roles=["scheduler"])
    assert "Live Log" not in html
    assert ">Live<" not in html
    assert 'href="/-/log">Log' in html


def test_the_app_bar_has_one_tab_per_standalone_screen(app_with):
    """Jeder Tab führt auf einen Screen, den es nur einmal gibt (`#162`).

    **Der Rot-Schritt dieses Tickets, und er zählt zwei Mengen gegeneinander:**
    sechs Tabs, fünf Ziele. `GET /-/live` gab wörtlich `logs_page()` zurück —
    denselben Inhalt wie `/-/log`, seit dem v5-Umbau. Zwei Tabs auf dieselbe
    Seite sind kein Screen mehr, sondern ein Zwilling.

    **Gemessen wird am ``<title>``, nicht am ganzen Rumpf.** Zwei Aufrufe
    derselben Seite unterscheiden sich in jeder Uhrzeit, die darin steht; der
    Titel ist das, woran ein Screen sich selbst benennt. Ein doppelter Titel
    heißt: zwei Tabs, ein Screen.

    **Der ``Accept``-Header gehört dazu und ist keine Formalie:** ``/-/``
    verzweigt danach — JSON für ``curl``, der Feed-Screen für den Browser. Ohne
    ihn misst der Test den Service-Deskriptor und hält ihn für einen Screen.
    """
    app = app_with({"roles": ["scheduler", "connect"]})
    browser = {"Accept": "text/html,application/xhtml+xml"}
    with TestClient(app) as c:
        titel: dict[str, list[str]] = {}
        for label, href in render.SCREENS:
            r = c.get(href, headers=browser)
            assert r.status_code == 200, f"{label} ({href}) → {r.status_code}"
            m = re.search(r"<title>(.*?)</title>", r.text, re.S)
            assert m, f"{label} ({href}) hat keinen Titel"
            titel.setdefault(m.group(1), []).append(label)
        doppelt = {t: ls for t, ls in titel.items() if len(ls) > 1}
        assert not doppelt, f"zwei Tabs auf denselben Screen: {doppelt}"


def test_the_live_address_still_leads_somewhere(app_with):
    """Gespeicherte Adressen zeigen nicht ins Leere (`#162`).

    Eine Umleitung ist besser als ein `404`: der Tab ist weg, die Adresse war
    aber lange sichtbar und steht in Lesezeichen.
    """
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        r = c.get("/-/live", follow_redirects=False)
        assert r.status_code in (307, 308), r.status_code
        assert r.headers["location"] == "/-/log"


def test_the_log_screen_marks_its_own_tab(app_with):
    """Der Log-Screen hebt den Tab hervor, auf dem man steht (`#162`).

    **Gefunden beim Rückbau, und es ist dieselbe Fehlerform:** ``log_page()``
    rief ``_header('Live Log')`` auf — einen Screen-Namen, den die Tab-Liste
    nicht kennt. Kein Tab passte, also war auf diesem Screen **keiner** aktiv,
    und der Log-Tab blieb ein Link auf die Seite, auf der man schon stand.

    Das ist wörtlich, was das Ticket verlangt: *keine Codestelle nennt einen
    Live-Screen mehr, den es nicht gibt.* Der Name stand hier am längsten.
    """
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        html = c.get("/-/log").text
    assert '<span class="tab-active">Log</span>' in html
    assert "Live Log" not in html


def test_the_log_screen_stays_whole(app_with):
    """Die Gegenprobe zu `#162`: ohne sie wäre auch ein Fix grün, der **beide**
    Routen entfernt.

    `/-/log` bleibt vollständig erreichbar — mit dem Panel, das den Screen
    ausmacht, nicht nur mit einem `200`.
    """
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        r = c.get("/-/log")
        assert r.status_code == 200
        assert "logbox" in r.text, r.text[:400]


# --- bibi5: die fünf Screens haben eigene Routen -----------------------------


def test_every_screen_in_the_app_bar_is_reachable(app_with):
    """Kein Tab zeigt ins Leere.

    Die App-Bar steht auf jedem Screen und nennt fünf Ziele; existiert eines
    davon nicht, ist die Leiste selbst der Fehler — man klickt und landet im
    404. Vorher konnte das nicht passieren, weil die Leiste nur zeigte, was die
    Rolle hergab; jetzt zeigt sie immer alles und muss es auch halten.
    """
    app = app_with({"roles": ["scheduler", "connect"]})
    with TestClient(app) as c:
        for label, href in render.SCREENS:
            r = c.get(href)
            assert r.status_code == 200, f"{label} ({href}) → {r.status_code}"
