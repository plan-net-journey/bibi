"""Gemeinsame Navigationsleiste (``_header()``): Tab-Leiste + FOLLOW- +
THEME-Toggle + Ops-Handles (RESCAN/MAINT) auf jedem Screen — inkl. Live-Log
(User-Feedback 2026-07-04: "ziehe Rescan und Maintenance CTA auf die obere
Navigationsleiste mit FOLLOW on/off"). Der Feed-Screen war zwischenzeitlich
entfernt (2026-07-04: "entferne den Feed, den will ich nicht mehr sehen"),
kam aber mit PLAN-18 (2026-07-06, Client-Umbau) als **Home-Screen** zurück —
Schedules bleibt unter ``/-/ui/schedules`` erreichbar, ist nur nicht mehr
``/-/`` selbst."""

from __future__ import annotations

from bibi.controller import render


def test_header_includes_follow_toggle():
    html = render._header("Schedules")
    assert 'id="follow"' in html and "bibiToggleFollow" in html


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
    assert 'id="rescan" class="toggle" title="Rescan auslösen">⟳<' in html


def test_ops_handles_js_restores_idle_icon():
    js = render._OPS_HANDLES_JS
    assert "const idleIcon = rescan.textContent" in js
    assert "rescan.textContent = idleIcon" in js


def test_feed_header_rescan_ignores_git_status():
    # Regressionstest für PLAN-21 Befund 2: git_status darf die RESCAN-
    # Beschriftung nicht mehr beeinflussen, egal wie der Sync-Zustand steht —
    # der lebt jetzt ausschließlich in der Git-Karte.
    feed_data = {"entities": [], "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_page(
        feed_data, git_status={"tree": "clean", "sync": "ahead", "branch": "trunk"}, now=100.0)
    assert 'id="rescan" class="toggle" title="Rescan auslösen">⟳<' in html
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
    assert 'id="follow"' not in left and 'id="rescan"' not in left and 'id="theme"' not in left
    assert ('id="follow"' in right and 'id="rescan"' in right
           and 'id="liveclock"' in right and 'id="theme"' in right)


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
    # PLAN-19 Befund 7, User-Fund: FOLLOW/THEME/RESCAN/MAINT sollen wie die
    # Nav-Tabs aussehen (reine Text-Links), keine Buttons mit Box/Rahmen mehr.
    # Bleiben funktional <button>-Elemente (JS-Handler), nur CSS-Klasse ändert
    # sich von "handle" auf "toggle" — kein "handle" mehr irgendwo im Markup.
    html = render._header("Schedules", {"maintenance": True, "roles": ["scheduler"]})
    assert 'class="handle"' not in html and 'class="handle ' not in html
    assert 'class="toggle on"' in html  # FOLLOW startet an
    assert 'class="toggle"' in html  # THEME + RESCAN
    assert 'class="toggle warn"' in html  # MAINT an
    assert ".toggle {" in render._CSS


def test_screen_nav_feed_tab_is_home():
    # PLAN-18 Stufe 18.3: Feed ist zurück und jetzt der Home-Screen (/-/),
    # Schedules zieht auf seine eigene Route um. Schedules nur mit
    # scheduler-Rolle sichtbar (PLAN-20 Befund 6). Tab-Label seit der
    # Bibi4-Iteration "Jobs" (User-Fund "eine App") statt "Schedules" —
    # die Route bleibt unverändert.
    html = render._screen_nav("Live-Log", roles=["scheduler"])
    assert 'href="/-/">Feed' in html
    assert 'href="/-/ui/schedules">Jobs' in html


def test_screen_nav_hides_schedules_without_scheduler_role():
    html = render._screen_nav("Live-Log", roles=["connect"])
    assert 'href="/-/ui/schedules"' not in html


def test_screen_nav_hides_schedules_and_jobs_without_any_role():
    html = render._screen_nav("Live Log")
    assert 'href="/-/ui/schedules"' not in html
    assert 'href="/-/ui/jobs"' not in html
    # Rollenunabhängige Tabs bleiben immer da.
    assert 'href="/-/">Feed' in html and 'href="/-/ui/logs">Live Log' not in html
    assert "Live Log" in html  # aktiver Tab, ohne Link


def test_screen_nav_shows_archive_tab_with_scheduler_role():
    # Bibi4-Iteration, User-Fund: Archive/Journal auf einen eigenen Screen
    # verschoben — Tab nur mit scheduler-Rolle (Client-Gegenpart ist eine
    # eigene, noch offene Iteration).
    html = render._screen_nav("Live-Log", roles=["scheduler"])
    assert 'href="/-/ui/archive">Archive' in html


def test_screen_nav_hides_archive_without_scheduler_role():
    html = render._screen_nav("Live-Log", roles=["connect"])
    assert 'href="/-/ui/archive"' not in html


def test_screen_nav_active_tab_has_active_class():
    # PLAN-25 Befund 2, User-Fund: der aktive Tab war bisher nur reiner Text
    # ohne eigene CSS-Klasse — "Hervorhebung" war die zufällige Abwesenheit
    # von .back-Grau, kein bewusstes visuelles Signal.
    html = render._screen_nav("Live Log")
    assert '<span class="tab-active">Live Log</span>' in html
    assert ".tab-active {" in render._CSS


def test_ops_handles_no_longer_duplicates_follow_button():
    # FOLLOW sitzt separat im gemeinsamen Header — _ops_handles() bleibt frei davon.
    html = render._ops_handles()
    assert 'id="follow"' not in html


def test_schedules_page_has_exactly_one_follow_and_theme_button():
    html = render.schedules_page([], now=1.0)
    assert html.count('id="follow"') == 1
    assert html.count('id="theme"') == 1


def test_follow_toggle_snaps_output_boxes_to_bottom_on_reenable():
    # User-Feedback: FOLLOW wieder anschalten muss die Live-Boxen (.liveterm auf
    # dem Job-Detail) sofort ans Ende scrollen — sonst bleibt "stick" auf false
    # hängen (atBottom() sah die eingefrorene Scroll-Position) und die Box folgt
    # trotz eingeschaltetem FOLLOW nie wieder.
    js = render._FOLLOW_JS
    on_branch = js.split("if (window.bibiFollow){")[1]
    assert "querySelectorAll('.liveterm')" in on_branch
    assert "box.scrollTop = box.scrollHeight" in on_branch


def test_schedule_detail_page_has_header_nav_and_follow():
    job = {"id": "j", "slug": "a", "status": "running", "started_at": 1.0}
    html = render.schedule_detail_page({"slug": "a", "kind": "job"}, [], job, slug="a")
    assert 'href="/-/"' in html and 'href="/-/ui/logs"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' in html


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


def test_schedules_page_has_rescan_and_maint():
    html = render.schedules_page([], daemon_status={"maintenance": False, "roles": ["scheduler"]})
    assert 'id="rescan"' in html
    assert 'id="maint" class="toggle"' in html and "⚙" in html
    assert render._OPS_HANDLES_JS in html


def test_execution_detail_page_has_header_nav_and_follow():
    entry = {"id": 1, "run_id": "x:1", "slug": "x", "kind": "job", "status": "complete",
             "started_at": 1.0, "finished_at": 2.0, "domain": "scheduled"}
    html = render.execution_detail_page(entry, [], "job")
    assert 'href="/-/"' in html and 'href="/-/ui/logs"' in html
    assert 'id="liveclock"' in html
    assert 'id="follow"' in html


def test_execution_detail_page_has_rescan_and_maint():
    # User-Feedback 2026-07-04: Rescan/Maintenance auf der Nav-Leiste, dadurch
    # jetzt auch auf der Execution-Detail-Seite (vorher gar nicht vorhanden).
    entry = {"id": 1, "run_id": "x:1", "slug": "x", "kind": "job", "status": "complete",
             "started_at": 1.0, "finished_at": 2.0, "domain": "scheduled"}
    html = render.execution_detail_page(
        entry, [], "job", daemon_status={"maintenance": True, "roles": ["scheduler"]})
    assert 'id="rescan"' in html
    assert 'id="maint" class="toggle warn"' in html


def test_log_page_has_rescan_maint_and_follow():
    # User-Feedback 2026-07-04: "Sie sind damit auch auf Live-Log sichtbar" —
    # Live-Log hatte bisher weder Ops-Handles noch ein funktionierendes FOLLOW
    # (_FOLLOW_JS fehlte).
    html = render.log_page(daemon_status={"maintenance": True, "roles": ["scheduler"]})
    assert 'id="rescan"' in html
    assert 'id="maint" class="toggle warn"' in html
    assert 'id="follow"' in html and "bibiToggleFollow" in html
    assert render._FOLLOW_JS in html
