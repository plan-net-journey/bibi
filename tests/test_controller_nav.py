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
    html = render._header("Schedules", {"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html


# --- Sync-CTA (PLAN-20 Befund 5) ------------------------------------------------


def test_ops_handles_shows_generic_rescan_without_git_status():
    html = render._ops_handles({}, None)
    assert 'id="rescan" class="toggle">RESCAN<' in html


def test_ops_handles_shows_sync_label_when_git_status_given():
    html = render._ops_handles({}, {"tree": "clean", "sync": "synced", "branch": "trunk"})
    assert 'id="rescan" class="toggle on">SYNC: synced<' in html


def test_ops_handles_sync_ahead_is_warn_colored():
    html = render._ops_handles({}, {"tree": "clean", "sync": "ahead", "branch": "trunk"})
    assert 'id="rescan" class="toggle warn">SYNC: ahead<' in html


def test_ops_handles_sync_behind_is_bad_colored():
    html = render._ops_handles({}, {"tree": "clean", "sync": "behind", "branch": "trunk"})
    assert 'id="rescan" class="toggle bad">SYNC: behind<' in html


def test_ops_handles_js_restores_idle_label_not_hardcoded_rescan():
    # Vorher hardcodete das Klick-Handler-JS "RESCAN" nach der Klick-Animation
    # zurück — hätte ein "SYNC: synced"-Label nach dem ersten Klick dauerhaft
    # auf "RESCAN" zurückgesetzt (Bug, den die neue Sync-Beschriftung sonst
    # eingeführt hätte).
    js = render._OPS_HANDLES_JS
    assert "const idleLabel = rescan.textContent" in js
    assert "rescan.textContent = idleLabel" in js
    assert "rescan.textContent = 'RESCAN';" not in js


def test_feed_header_passes_git_status_to_ops_handles():
    feed_data = {"entities": [], "heatmap": [[[0] * 8 for _ in range(7)] for _ in range(5)]}
    html = render.feed_page(
        feed_data, git_status={"tree": "clean", "sync": "ahead", "branch": "trunk"}, now=100.0)
    assert 'id="rescan" class="toggle warn">SYNC: ahead<' in html


def test_toggles_styled_as_text_links_not_boxed_buttons():
    # PLAN-19 Befund 7, User-Fund: FOLLOW/THEME/RESCAN/MAINT sollen wie die
    # Nav-Tabs aussehen (reine Text-Links), keine Buttons mit Box/Rahmen mehr.
    # Bleiben funktional <button>-Elemente (JS-Handler), nur CSS-Klasse ändert
    # sich von "handle" auf "toggle" — kein "handle" mehr irgendwo im Markup.
    html = render._header("Schedules", {"maintenance": True})
    assert 'class="handle"' not in html and 'class="handle ' not in html
    assert 'class="toggle on"' in html  # FOLLOW startet an
    assert 'class="toggle"' in html  # THEME + RESCAN
    assert 'class="toggle warn"' in html  # MAINT an
    assert ".toggle {" in render._CSS


def test_screen_nav_feed_tab_is_home():
    # PLAN-18 Stufe 18.3: Feed ist zurück und jetzt der Home-Screen (/-/),
    # Schedules zieht auf seine eigene Route um. Schedules nur mit
    # scheduler-Rolle sichtbar (PLAN-20 Befund 6).
    html = render._screen_nav("Live-Log", roles=["scheduler"])
    assert 'href="/-/">Feed' in html
    assert 'href="/-/ui/schedules">Schedules' in html


def test_screen_nav_hides_schedules_without_scheduler_role():
    html = render._screen_nav("Live-Log", roles=["connect"])
    assert 'href="/-/ui/schedules"' not in html


def test_screen_nav_hides_schedules_and_jobs_without_any_role():
    html = render._screen_nav("Live-Log")
    assert 'href="/-/ui/schedules"' not in html
    assert 'href="/-/ui/jobs"' not in html
    # Rollenunabhängige Tabs bleiben immer da.
    assert 'href="/-/">Feed' in html and 'href="/-/ui/logs">Live-Log' not in html
    assert "Live-Log" in html  # aktiver Tab, ohne Link


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
        daemon_status={"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html
    assert render._OPS_HANDLES_JS in html


def test_schedules_page_has_rescan_and_maint():
    html = render.schedules_page([], daemon_status={"maintenance": False})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: aus" in html
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
    html = render.execution_detail_page(entry, [], "job", daemon_status={"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html


def test_log_page_has_rescan_maint_and_follow():
    # User-Feedback 2026-07-04: "Sie sind damit auch auf Live-Log sichtbar" —
    # Live-Log hatte bisher weder Ops-Handles noch ein funktionierendes FOLLOW
    # (_FOLLOW_JS fehlte).
    html = render.log_page(daemon_status={"maintenance": True})
    assert 'id="rescan"' in html
    assert 'id="maint"' in html and "MAINT: AN" in html
    assert 'id="follow"' in html and "bibiToggleFollow" in html
    assert render._FOLLOW_JS in html
