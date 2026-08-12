"""Der Header: zwei Blöcke nach Herkunft (bibi5, FE-Spezifikation §2).

Links steht, was dieser Knoten selbst weiß; rechts, was der Scheduler sagt.
Die Trennung ist nicht kosmetisch, sondern folgt dem Ausfall: fällt der Host
weg, verlieren genau die rechten Werte gleichzeitig ihre Gültigkeit — und nur
sie. Vier Status-Kacheln nebeneinander konnten das nicht zeigen, weil sie
Herkunft und Aktualität vermischten.
"""

from __future__ import annotations

import re

import pytest

from bibi.controller import render

NOW = 1_000_000.0

CLIENT_STATUS = {
    "roles": ["synchronizer", "controller", "connect"],
    "hostname": "Mac.fritz.box",
    "auto_sync": False,
    "maintenance": False,
    "started_at": NOW - 48_300,
    "engine": {"running": "v0.6.0", "expected": "v0.6.0", "needs_update": False},
    "connect": {"ok": True, "last_at": NOW - 21},
}
GIT = {"branch": "trunk", "tree": "modified", "sync": "synced", "commit": "4715f43"}
SCHEDULER_STATUS = {
    "hostname": "sarasate.tail9f9173.ts.net",
    "started_at": NOW - 48_300,
    "workers": [{"worker": "sarasate-client"}, {"worker": "mac"}],
    "job_stats": {"counts": {"complete": 52, "killed": 2, "error": 1, "pending": 3}},
    "next_fire_at": NOW + 120,
}


def _header(**kw) -> str:
    # `scheduler_host` kommt immer mit: er stammt aus der Konfiguration dieses
    # Knotens (`config.scheduler_base_url()`), nicht aus der Antwort des Hosts.
    # Genau deshalb steht er auch dann da, wenn nichts mehr antwortet.
    kw.setdefault("scheduler_host", "sarasate.tail9f9173.ts.net")
    return render.status_header(
        CLIENT_STATUS, GIT, scheduler=kw.pop("scheduler", SCHEDULER_STATUS),
        now=kw.pop("now", NOW), **kw)


# ── die beiden Blöcke ───────────────────────────────────────────────────────


def test_two_blocks_named_by_origin():
    """`CLIENT` und `SCHEDULER` — die Titel sagen, woher die Zahlen kommen."""
    html = _header()
    assert "CLIENT" in html and "SCHEDULER" in html


def test_client_block_carries_its_own_hostname():
    html = _header()
    assert "Mac.fritz.box" in html


def test_scheduler_hostname_comes_from_the_client():
    """Er steht auch bei Ausfall da — er ist der Anker, an dem der leere Block
    hängt. Deshalb stammt er aus der Konfiguration dieses Knotens, nicht aus
    der Antwort des Hosts."""
    html = _header(scheduler=None)
    assert "sarasate.tail9f9173.ts.net" in html


def test_client_block_has_its_four_rows():
    # Seit #151 steht die `bibi`-Zeile auf jedem Screen.
    html = _header()
    for label in ("heartbeat", "project", "bibi"):
        assert label in html, label
    # Ohne Doppelpunkt seit #30 — die Beschriftungsfarbe trennt jetzt.
    assert '<span class="hdr-inline">auto-sync</span> off' in html
    assert "trunk" in html and "modified" in html
    assert "v0.6.0" in html


def test_scheduler_block_has_its_four_rows():
    # Seit #151 steht `uptime` auf jedem Screen.
    html = _header()
    for label in ("clients", "next job", "uptime"):
        assert label in html, label
    # Seit der Kuerzung: "2, connected <Uhrzeit>" statt "2 connected".
    assert re.search(r"2, connected \d{2}:\d{2}:\d{2}", html)


# ── Offline: dimmen, nicht leeren ───────────────────────────────────────────


def test_offline_keeps_the_last_values_and_dates_them():
    """Kein achtfaches `offline`. Der Block behält seine Werte, wird gedimmt
    und trägt in der Titelzeile das Alter des Standes — sonst weiß niemand, ob
    „2 connected" von vor einer Minute oder von gestern stammt."""
    html = _header(scheduler=SCHEDULER_STATUS, scheduler_stale_since=NOW - 240)
    assert "no contact for" in html
    # **Seit #30 in der Titelzeile statt in der `clients`-Zeile.** Die Zusage
    # dieses Tests ist unverändert — der Stand bleibt stehen und wird datiert —,
    # nur trug ihn vorher das Wort `connected`, und das behauptete bei Ausfall
    # eine Verbindung, die es nicht mehr gab.
    assert re.search(r"as of \d{2}", html), "das Alter des Standes fehlt"
    assert "connected" not in html, "`connected` behauptet offline eine Verbindung"
    assert re.search(r'<span class="hdr-value">2</span>', html), \
        "die letzten Werte bleiben stehen"
    assert "dimmed" in html or "stale" in html


def test_offline_marks_the_scheduler_hostname_red():
    """Rot trägt genau eine Bedeutung: nicht erreichbar oder nicht in Ordnung.
    Dass der Hostname vom Client stammt und deshalb stehenbleibt, ist kein
    Widerspruch — er bleibt sichtbar **und** wird rot, weil er den Ausfall
    trägt."""
    html = _header(scheduler=SCHEDULER_STATUS, scheduler_stale_since=NOW - 240)
    rot = [z for z in html.splitlines() if "sarasate" in z]
    assert rot and any("bad" in z or "red" in z or "danger" in z for z in rot), \
        "der Scheduler-Hostname muss bei Ausfall rot sein"


def test_auto_sync_off_is_not_an_error():
    """Eine Einstellung, kein Fehler — sie bleibt neutral."""
    html = _header()
    zeile = [z for z in html.splitlines() if "auto-sync" in z]
    assert zeile and not any("bad" in z or "danger" in z for z in zeile)


# ── was der Header nicht mehr trägt ─────────────────────────────────────────


def test_no_job_matrix_and_no_status_cards():
    """Die 3×3-Job-Matrix verlässt den Header; ihre Aussage steht jetzt in der
    `next job`-Zeile als drei Zahlen und ausführlich im Jobs-Screen."""
    html = _header()
    assert "statuscards" not in html
    assert "jobmatrix" not in html and "job-matrix" not in html


# ── die Werte, die live gefehlt haben (Live-Abnahme 2026-08-03) ────────────


def test_project_row_shows_the_short_commit():
    """`synced 4715f43` — der Stand gehört an die Sync-Angabe, sonst sagt
    „synced" nur, dass es *irgendwann* stimmte. Das Feld heißt `oid` und ist
    der volle Hash; angezeigt werden sieben Zeichen.

    Seit #30 ohne Doppelpunkt: `synced` ist die Beschriftung des Hashes und
    trägt deren Farbe.
    """
    git = dict(GIT)
    git.pop("commit", None)
    git["oid"] = "4715f4319ab2c8d7e6f5a4b3c2d1e0f9a8b7c6d5"
    html = render.status_header(CLIENT_STATUS, git, scheduler=SCHEDULER_STATUS, now=NOW,
                                scheduler_host="sarasate")
    assert '<span class="hdr-inline">synced</span> 4715f43' in html
    assert "4715f4319ab2" not in html, "gekürzt, nicht voll"


def test_next_job_reads_the_schedulers_own_field():
    """Der naechste Termin steht in `job_stats.next_due_at`, nicht auf oberster
    Ebene — live stand hier ein Strich, weil ich es eine Ebene zu hoch gesucht
    hatte."""
    sched = {"hostname": "sarasate", "workers": [],
             "job_stats": {"counts": {"complete": 9, "killed": 1},
                           "next_due_at": NOW + 120}}
    html = render.status_header(CLIENT_STATUS, GIT, scheduler=sched, now=NOW,
                                scheduler_host="sarasate")
    # Absolute Uhrzeit statt "in 2 min" — der Wert kommt weiterhin aus
    # `job_stats.next_due_at`, nur die Darstellung hat gewechselt.
    assert re.search(r"\d{2}:\d{2}:\d{2}", html)
    assert "1 stopped, 9 finished" in html


# ── Absolute Zeiten (Entscheidung m.rau, 2026-08-03) ───────────────────────
#
# Befund: „mir faellt auf, dass der heartbeat nicht aktualisiert, ausser beim
# reload." Der Bus feuert auf Ereignisse — Job-Wechsel, Flag-Wechsel,
# Node-Wechsel —, und Zeitablauf ist keins. Drei der acht Header-Werte waren
# aber genau das.
#
# Erste Antwort war ein Ticker im Browser. Dagegen der Einwand: „nicht dass wir
# uns das Leben unnoetig verkomplizieren, weil wir an der fachlichen Bedeutung
# haengen. Datum und Uhrzeiten moeglichst einheitlich verarbeiten und
# persistieren." — und dann die Frage „nicht besser nur noch absolute Zeiten?"
#
# Ja. Eine absolute Zeit bleibt nach einem Screenshot wahr, sie kann nicht
# einfrieren, und es gibt die Zeitlogik nur noch einmal (Epoch bis zur Anzeige).
# Das weicht von FE-Spezifikation §2 ab, die `21s ago` und `in 2 min` zeigt.


def test_header_shows_absolute_times_not_relative_ones():
    """Kein `ago`, kein `in …` — was einfrieren koennte, gibt es nicht mehr."""
    html = _header()
    assert " ago" not in html
    assert "in 2 min" not in html and "in 3 min" not in html


def test_no_ticker_script_is_needed():
    """Die Vereinfachung, um die es ging: keine zweite Zeitlogik im Browser."""
    assert not hasattr(render, "_REL_TIME_JS")
    assert not hasattr(render, "_rel")


def test_times_within_a_day_show_the_clock_only():
    """Das Datum waere fast immer „heute" und macht jede Zeile doppelt so lang."""
    html = _header()
    assert re.search(r"\d{2}:\d{2}:\d{2}", html), "keine Uhrzeit gefunden"


def test_times_older_than_a_day_carry_their_date():
    """Ohne Datum waere die Uhrzeit mehrdeutig — 08:15 von wann?"""
    alt = dict(SCHEDULER_STATUS)
    alt["started_at"] = NOW - 3 * 86400
    # Der alte Zeitstempel steckt in `uptime` — nur in der vollen Form.
    html = _header(scheduler=alt)
    assert re.search(r"\d{2}/\d{2} \d{2}:\d{2}", html), "kein Datum bei altem Zeitstempel"




# ── Abnahme am Bild (m.rau, 2026-08-03) ────────────────────────────────────


def test_both_blocks_carry_a_bullet():
    """„ein Kreis/Bullet vor Client dann genau so wie vor Scheduler."

    Zwei Titelzeilen, die dasselbe sind, müssen auch gleich aussehen. Der
    Punkt trägt links denselben Sinn wie rechts: ist diese Seite in Ordnung?
    Für den Client heißt das, ob sein Heartbeat durchkommt.
    """
    html = _header()
    # **Scheduler links seit #135/#30**, vorher stand der Client dort (#147).
    links = html.split("SCHEDULER", 1)[0]
    mitte = html.split("SCHEDULER", 1)[1].split("CLIENT", 1)[0]
    assert "●" in links, "der SCHEDULER-Block braucht denselben Punkt"
    assert "●" in mitte, "der CLIENT-Punkt steht weiterhin vor seinem Wort"


def test_the_header_leads_with_the_scheduler():
    """#30: *„Scheduler links, Client rechts — einheitlich in Header und
    Jobs-Tabelle."*

    Der Header war die Stelle, an der #147 die alte Regel zuerst festgelegt
    hatte (FE §2: links, was dieser Knoten selbst weiß). **Ihn stehenzulassen,
    während die Tabelle dreht, wäre der schlechteste der drei Zustände** — dann
    stünde dieselbe Frage auf einem Screen zweimal verschieden beantwortet.
    """
    html = _header()
    assert html.index("SCHEDULER") < html.index("CLIENT")


def test_the_client_bullet_reports_the_heartbeat():
    """Er ist kein Ornament: bricht der Heartbeat ab, wird er rot — dieselbe
    Bedeutung wie rechts, nur für die andere Seite."""
    aus = dict(CLIENT_STATUS)
    aus["connect"] = {"ok": False, "last_at": NOW - 300}
    html = render.status_header(aus, GIT, scheduler=SCHEDULER_STATUS, now=NOW,
                                scheduler_host="sarasate")
    # **Der Block wird gegriffen, nicht die Seite** — seit #135 steht der Client
    # rechts, und ein Test, der eine Hälfte abschneidet, prüft nach jedem Dreh
    # den anderen Block. Sein Punkt steht zudem *vor* dem Wort `CLIENT`, ein
    # Schnitt am eigenen Titel verlöre ihn also ohnehin.
    client = next(b for b in html.split('<div class="hdr-block') if "CLIENT" in b)
    assert "bad" in client


def test_labels_are_tinted():
    """„ein bisschen Farbe wäre schön! Die Keys farbig, dezent?" — die
    Beschriftungen bekommen einen eigenen Ton, nicht die Werte: sie sind das
    Gerüst, an dem das Auge die Zeile findet."""
    assert ".hdr-label" in render._CSS
    assert "--hdr-key" in render._CSS


def test_no_colons_inside_header_values():
    """#30: *„Doppelpunkte weg: `auto-sync off` statt `auto-sync: off`, `synced
    4257a7b` statt `synced: 4257a7b`."*

    **Der Doppelpunkt war die Ersatzform für eine Auszeichnung, die es nicht
    gab.** Er trennte eine Beschriftung von ihrem Wert, weil beide dieselbe
    Farbe trugen — sobald die Beschriftung ihren eigenen Ton hat, trennt die
    Farbe, und das Zeichen ist doppelt gemoppelt. Die Zeile links tut es seit
    jeher ohne: dort steht `heartbeat` neben seinem Wert, nicht `heartbeat:`.
    """
    # **Uhrzeiten sind ausgenommen, und der Rot-Schritt hat das erzwungen:** der
    # erste Anlauf dieses Tests meldete `2, connected 01:21:40` als Befund.
    # Gesucht ist der Doppelpunkt als *Trennzeichen* — einer zwischen Ziffern
    # gehört zu einem Zeitwert und trennt gar nichts.
    trenner = re.compile(r"(?<!\d):(?!\d)")
    werte = re.findall(r'<span class="hdr-value"[^>]*>(.*?)</span>', _header())
    assert werte, "keine Header-Werte gefunden"
    for wert in werte:
        ohne_tags = re.sub(r"<[^>]+>", "", wert)
        assert not trenner.search(ohne_tags), f"Doppelpunkt in {wert!r}"


def _labels(html: str) -> list[str]:
    return re.findall(r'<span class="hdr-label">([^<]+)</span>', html)


def test_one_header_on_every_screen():
    """Entscheidung m.rau (2026-08-12, `#151`): *„es gibt nicht 2 Header
    Ansichten. Überall wird immer der vollständige Header gezeigt."*

    **Hier stand bis `v0.8.6` das Gegenteil**, aus `#30`: kompakt ab Screen 2,
    voll nur im Feed, begründet mit *„vier Zeilen über einer Liste, die man
    scrollt, sind zu viel"*.

    **Die zweite Fassung hat eine eigene Fehlerklasse erzeugt**, und das ist
    der Grund, warum sie ersatzlos fällt statt umgedreht zu werden: weil das
    Feed-Fragment seinen Screen nicht kennt, musste die Form als `?full=1` in
    seine Refetch-URL. Fehlte sie, fiel der Header beim ersten Job-Wechsel
    still auf die kompakte Fassung zurück — ein Fehler, der wie ein
    Rendering-Zufall aussieht und nur nach einem Ereignis auftritt.
    """
    import inspect

    assert _labels(_header()) == ["clients", "next job", "uptime",
                                  "heartbeat", "project", "bibi"]
    assert "voll" not in inspect.signature(render.status_header).parameters, \
        "die zweite Fassung lebt noch in der Signatur"
    assert "voll" not in inspect.signature(render.feed_status_fragment).parameters


def test_the_refetch_url_carries_no_form():
    """Der Query-Parameter fällt mit der zweiten Fassung — er war ihr einziger
    Zweck."""
    html = render.feed_status_fragment(CLIENT_STATUS, GIT, None, NOW)
    assert "full=1" not in html, "die Refetch-URL trägt weiterhin eine Form"


def test_the_upgrade_warning_survives_the_merge():
    """**Die Gegenprobe, und sie ist der Grund, warum die Kürzung überhaupt
    eine Ausnahme brauchte.**

    Die Versionszeile blieb in der kompakten Form stehen, *wenn* sie `requires
    upgrade` trug — eine Warnung, die man nur auf einem von sechs Screens
    sieht, ist keine. Die Ausnahme wird jetzt gegenstandslos; **die Warnung
    darf mit ihr nicht verschwinden.**
    """
    aus = dict(CLIENT_STATUS)
    aus["engine"] = {"running": "v0.6.0", "expected": "v0.7.0",
                     "needs_update": True}
    html = render.status_header(aus, GIT, scheduler=SCHEDULER_STATUS, now=NOW,
                                scheduler_host="sarasate")
    assert "requires upgrade" in html


def test_inline_labels_carry_the_label_colour():
    """#30: *„`auto-sync` und `synced` in der Beschriftungsfarbe — blau ist die
    Farbe der Beschriftung, nicht des Wertes; beide sind Beschriftungen mitten
    im Wert."*

    Geprüft wird die Auszeichnung, nicht die Farbe selbst: welche Farbe
    `--hdr-key` trägt, entscheidet das Theme, und ein Test darauf prüfte die
    Palette statt der Zuordnung.
    """
    html = _header()
    for wort in ("auto-sync", "synced"):
        assert f'<span class="hdr-inline">{wort}</span>' in html, wort


def test_uptime_and_clients_are_terse():
    """„bei uptime schreib kürzer: up 01/08 23:32" und „bei clients: 2,
    connected 14:19:23". Beide Zeilen brachen vorher um."""
    html = _header()
    # Unter 24 h nur die Uhrzeit, darueber mit Datum — hier liegt der Start
    # 13,4 h zurueck.
    assert re.search(r"up \d{2}:\d{2}:\d{2}", html)
    assert "up since" not in html
    assert re.search(r"2, connected \d{2}:\d{2}:\d{2}", html)
    assert "2 connected" not in html


def test_no_clock_anywhere_in_the_header():
    html = _header()
    assert "hdr-clock" not in html


def test_the_app_bar_clock_shows_the_schedulers_time():
    """„Am liebsten hätte ich die scheduler Uhrzeit!" — und zwar „rechts oben
    mit Ticker, und sonst nirgends".

    Die lokale Zeit hat jeder in seiner Menüleiste; die des Schedulers steht
    nirgends. In einem verteilten System ist sie die interessantere: sie ist
    der Bezugspunkt für alles, was der rechte Block zeigt, und ein
    Auseinanderlaufen der Uhren wird genau hier sichtbar.
    """
    html = render._live_clock(scheduler_now=NOW, now=NOW - 3)
    assert 'id="liveclock"' in html
    # Der Versatz zur eigenen Uhr reist mit, damit der Ticker die *fremde*
    # Zeit hochzählt statt der eigenen.
    assert re.search(r'data-offset="3(\.0)?"', html)


def test_the_clock_falls_back_to_local_time_without_a_scheduler():
    """Ohne erreichbaren Host gibt es keine fremde Zeit — dann zeigt die Uhr
    wieder die eigene, statt stehenzubleiben. Eine stehende Uhr sieht aus wie
    eine Zeit und ist keine."""
    html = render._live_clock(scheduler_now=None, now=NOW)
    assert 'id="liveclock"' in html
    assert re.search(r'data-offset="0(\.0)?"', html)


def test_the_clock_js_applies_the_offset():
    """Ohne den Versatz zeigte die Uhr die lokale Zeit unter fremdem Namen."""
    # Im Skript heisst das Attribut `dataset.offset`, im HTML `data-offset`.
    assert "dataset.offset" in render._CLOCK_JS


# ── #148: der Header kann Alarm zeigen ──────────────────────────────────────
#
# **Die beiden Tests darüber prüfen den Klassennamen, nicht seine Wirkung** —
# `test_offline_marks_the_scheduler_hostname_red` und
# `test_the_client_bullet_reports_the_heartbeat` suchen `bad` im Markup. Genau
# das war fünf Releases lang grün, während es im ganzen Stylesheet keine Regel
# für ein blankes `.bad` gab: definiert waren nur `.conn-dot.bad`,
# `.toggle.bad` und `.chip.bad`, alle mit einem Element-Präfix, das der Header
# nicht setzt. Der Name fiel ins Leere, der Punkt blieb schwarz.
#
# **Warum es niemandem auffiel:** `ok` war ebenso wirkungslos wie `bad`, und
# grün war der Punkt im Header nie. Es gab also keinen Zustand, dessen Fehlen
# aufgefallen wäre — eine Regel, die nur im seltenen Fall greift, wird nur im
# seltenen Fall geprüft.
#
# Die Tests hier rechnen deshalb die echte Kaskade über `render._CSS` (siehe
# `tests/_css.py`) statt eine Zeichenkette zu suchen.


def _alarm_ketten(html: str):
    """Jedes Element des Fragments, das `bad` oder `ok` trägt."""
    from tests import _css
    return [(kette, merkmale)
            for kette, merkmale in _css.ketten(html, _css.BODY)
            if {"bad", "ok"} & set((merkmale.get("class") or "").split())]


def _alarm_html() -> str:
    """Ein Header, in dem **jeder** der fünf Alarme zugleich ansteht.

    Einer nach dem anderen zu prüfen hieße, die Liste im Test zu führen — und
    dann fehlt der sechste, sobald er dazukommt. Hier wird der Zustand
    hergestellt und das Ergebnis abgezählt.
    """
    aus = dict(CLIENT_STATUS)
    aus["connect"] = {"ok": False, "last_at": NOW - 300}     # Punkt + heartbeat-Zeile
    aus["engine"] = {"running": "v0.6.0", "expected": "v0.7.0",
                     "needs_update": True}                    # requires upgrade
    git = dict(GIT, sync="conflict", conflict=True)           # project-Zeile
    return render.status_header(aus, git, scheduler=SCHEDULER_STATUS, now=NOW,
                                scheduler_host="sarasate.tail9f9173.ts.net",
                                scheduler_stale_since=NOW - 240)  # Punkt + Host


def test_every_alarm_in_the_header_resolves_to_the_alarm_colour():
    """**Der Befund aus #148, und er zählt statt aufzuzählen.**

    Jedes Element, das `bad` trägt, muss die Warnfarbe **bekommen** — nicht
    bloß irgendeine Regel treffen.

    **Der Unterschied ist hier kein Feinschliff, er ist der halbe Fehler.** Ein
    erster Entwurf fragte nur, ob überhaupt eine Regel greift, und fand fünf
    von sechs Stellen. Die sechste — `heartbeat` und `project` — trifft
    `.hdr-row .hdr-value` und bekommt von dort eine Farbe zugewiesen. **Sie
    fällt also nicht durch, sie bekäme die falsche**, und mit Spezifität 20
    schlüge diese Regel jedes blanke `.bad`. Ein Fix, der nur eine
    `.bad`-Zeile ergänzt, wäre gegen den lockeren Test grün und ließe zwei der
    sechs Alarme stumm.
    """
    from tests import _css

    falsch = [(kette[-1], _css.aufgeloest(kette, "color"))
              for kette, _ in _alarm_ketten(_alarm_html())
              if "bad" in kette[-1][1]
              and _css.aufgeloest(kette, "color") != "var(--red)"]
    assert not falsch, f"{len(falsch)} Alarm(e) ohne Warnfarbe: {falsch}"


def test_the_header_carries_five_alarms_at_once():
    """Die Gegenprobe zur Zählung: der hergestellte Zustand muss alle fünf
    Stellen treffen, sonst prüft der Test darüber weniger, als er behauptet.

    Scheduler-Punkt, Scheduler-Hostname, Client-Punkt, `requires upgrade`,
    `project`-Zeile und `heartbeat`-Zeile — sechs Elemente, weil der
    Scheduler-Ausfall zwei davon trägt.
    """
    treffer = [k for k, _ in _alarm_ketten(_alarm_html()) if "bad" in k[-1][1]]
    assert len(treffer) >= 6, f"nur {len(treffer)} Alarme im Testzustand"


def test_the_calm_state_resolves_too():
    """**Die Gegenprobe, und sie ist der eigentliche Test.**

    Ein Fix, der nur `bad` bedient, ließe `ok` weiter stumm — und das fiele
    niemandem auf, weil ein grüner Punkt, den es nie gab, nicht vermisst wird.
    Genau so ist der Fehler entstanden.
    """
    from tests import _css

    gruen = [k for k, _ in _alarm_ketten(_header()) if "ok" in k[-1][1]]
    assert gruen, "der ruhige Header trägt keinen `ok`-Zustand"
    falsch = [(k[-1], _css.aufgeloest(k, "color")) for k in gruen
              if _css.aufgeloest(k, "color") != "var(--green)"]
    assert not falsch, f"`ok` ohne Ruhefarbe: {falsch}"


def test_the_hostname_carries_failure_only():
    """Der Punkt trägt den Zustand, der Hostname nur den **Ausfall**.

    Beide `ok` zu geben hieße, den Hostnamen im Normalbetrieb grün zu färben —
    eine Farbe, die dann dauerhaft anliegt und damit nichts mehr sagt. Rot
    dagegen ist eine Nachricht: *dieser Anker hält gerade nichts mehr.*
    """
    ruhig = _header()
    titel = ruhig.split('<div class="hdr-title">', 1)[1]
    assert 'class="hdr-host"' in titel, "der ruhige Hostname trägt keine Zustandsklasse"
    assert "hdr-host ok" not in ruhig, "der Hostname ist im Normalbetrieb neutral"
    assert "hdr-host bad" in _alarm_html(), "der Hostname trägt den Ausfall nicht"
