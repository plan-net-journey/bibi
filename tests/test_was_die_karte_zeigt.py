"""Was die Karte zeigt (`v0.8.13`) — wo steht ein Wert, und wem gehört seine
Darstellung?

Sechs Posten, zwei Antworten. Die Job Card bekommt einen **Vorrat** und ein
**Set** daraus; die Attributseite wird der **vollständige Katalog** und liest
dafür eine zweite Quelle. Dazwischen liegen ein Escaping-Fehler, der beide
Seiten betrifft, und ein Umschalter, der an sechs Stellen keinen Anker findet.

**Der erste Posten ist ein Fehler aus der Vorrunde**, und die Lehre daraus
bestimmt die Bauart der Tests hier: `#192` entstand, weil eine Konstruktion,
die einen Fehler verhindern sollte, ihn nur auf dem Weg verhinderte, den sie
kannte. Ein Wächter, der über die Whitelist iteriert, kann eine Spalte nicht
sehen, die **nicht** in der Whitelist steht — er muss aus dem gerenderten
Markup kommen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bibi.controller import render
from bibi.daemon import roles
from bibi.daemon.app import create_app
from bibi.schedule.models import job_uid

NOW = 1_000_000.0


class _FakeClient:
    """Ein Scheduler, der nichts weiß — diese Runde prüft die lokale Seite."""

    def __init__(self, status: dict):
        self._status = status

    def status(self, **_):
        return self._status

    def jobs(self, **_):
        return []


@pytest.fixture
def app_with(team_repo: Path):
    def _make(status: dict):
        return create_app(roles.resolve({"controller"}),
                          controller_client=_FakeClient(status))
    return _make


# ── #192: der Kopf setzt den Parameter, die Route wirft ihn weg ─────────────


def test_die_drei_neuen_schluessel_stehen_in_der_whitelist():
    """Der konkrete Fehler aus dem `v0.8.12`-Durchgang.

    `sortierbare_schluessel()` leitet die erlaubten `sort`-Werte aus
    `_SORTIERBAR` ab, und die Route prüft dagegen. Die drei mit `#179` neu
    klickbar gemachten Spalten stehen dort nicht — `asc` und `desc` lieferten
    im Betrieb dieselbe Reihenfolge.
    """
    erlaubt = render.sortierbare_schluessel()
    for schluessel in ("runtime", "client_status", "client_last"):
        assert schluessel in erlaubt, (
            f"{schluessel} ist klickbar, aber die Route verwirft ihn (#192)")


def test_kein_kopf_im_markup_steht_ausserhalb_der_whitelist():
    """**Der Wächter, und der eigentliche Posten.**

    Der Vorgänger (`test_every_clickable_column_survives_the_whitelist`)
    iteriert über `_SORTIERBAR` und prüft, dass jeder Eintrag durch die Route
    kommt. **Er kann den Fehler nicht sehen, gegen den er gebaut wurde:** eine
    Spalte, die im Markup steht und in der Liste fehlt, kommt in seiner
    Schleife nicht vor.

    Dieser hier geht die andere Richtung — vom **gerenderten Kopf** zur
    Whitelist. Das ist die Prüfung, die die Zusage des Docstrings von
    `sortierbare_schluessel()` tatsächlich trägt: *„Eine neue klickbare Spalte
    kann diesen Fehler nicht wiederholen."*

    Alle vier Kopf-Varianten, weil `mit_next` und `status_filter` je eine
    Spalte ein- und ausblenden — eine Variante ungeprüft zu lassen hieße,
    denselben Fehler an einer Stelle offenzuhalten, an der er schon einmal saß.
    """
    erlaubt = render.sortierbare_schluessel()
    gesehen: set[str] = set()
    for mit_next in (True, False):
        for status_filter in (True, False):
            kopf = render._jobs_kopf(None, "asc", mit_next=mit_next,
                                     status_filter=status_filter)
            gesehen |= set(re.findall(r'data-sort="([^"]+)"', kopf))

    assert gesehen, "kein einziger klickbarer Kopf gefunden — der Test misst nichts"
    ausserhalb = gesehen - erlaubt
    assert not ausserhalb, (
        f"{sorted(ausserhalb)} setzen data-sort, kommen aber nicht durch "
        f"sortierbare_schluessel() — der Kopf verspricht eine Sortierung, "
        f"die die Route wegwirft (#192)")


# ── #183: der Zusammenstoß zweier korrekter Funktionen ─────────────────────


def _snapshot(**kw):
    basis = {"slug": "EngineCI", "kind": "job", "payload": "pytest -q",
             "schedule": "0 * * * *", "attempts": 1, "backoff": "fixed",
             "model": "opus", "silence_timeout": 3600}
    return {**basis, **kw}


def _lauf(**kw):
    basis = {
        "id": 7, "run_id": "EngineCI:3", "slug": "EngineCI", "kind": "job",
        "status": "complete", "reason": None,
        "started_at": NOW - 120, "finished_at": NOW - 60, "archived_at": NOW - 60,
        "exit_code": 0, "exec_runtime": 60.0,
        "host": "sarasate", "worker": "w1",
        "output_ref": "data/x/output.jsonl",
        "commit_sha": "a1b2c3d4e5", "branch": "agent/EngineCI",
        "domain": "scheduled", "pinned_host": None,
        "snapshot": json.dumps(_snapshot()),
    }
    return {**basis, **kw}


def test_ein_zeitwert_der_attributseite_ist_ein_element_kein_text():
    """*„Das HTML bei allen `*_at` Attribute ist seltsam."* (m.rau, 2026-08-13)

    `_uhrzeit()` liefert kein Datum, sondern ein fertiges Markup-Fragment mit
    Anker; `_attr_zeile()` escaped ihren Wert, weil sie einen rohen erwartet.
    **Beide Funktionen tun das Richtige; die Naht zwischen ihnen ist falsch.**

    Die Sorge hinter der Beobachtung trifft übrigens nicht zu, und das gehört
    in den Test statt nur ins Ticket: persistiert wird nichts HTML-artiges. In
    der Datenbank stehen Floats, im `run_snapshot`-JSON ebenfalls. Was zu sehen
    war, entstand beim Rendern.
    """
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)

    assert "started_at" in html, (
        "Absicherung: die Zeile steht überhaupt auf der Seite — ohne das "
        "prüfte der Assert unten nichts")
    assert re.search(r'<span[^>]*\bdata-tp="[^"]*"', html), (
        "started_at steht als escapte Zeichenkette auf der Seite statt als "
        "Element mit Anker (#183)")
    assert "&lt;span" not in html, (
        "ein Markup-Fragment ist escaped worden — genau der Zusammenstoß "
        "aus #183")


def test_der_zeitumschalter_wirkt_auf_der_attributseite():
    """Die zweite Beobachtung, derselbe Fix.

    Weil der Span **Text** war statt Element, war auf der Attributseite auch
    der abs./rel.-Umschalter tot: der Ticker rechnet alle Elemente mit
    `data-tp` um, und ein escapter Span ist keins. **Wer nur das Escaping
    repariert, bekommt beides zurück** — dieser Test hält fest, dass es
    tatsächlich beides war und nicht nur das Aussehen.
    """
    html = render.run_attrs_page_v5(slug="EngineCI", lauf=_lauf(),
                                    job_spec=_snapshot(), now=NOW)
    anker = re.findall(r'<span[^>]*\bdata-tp="([^"]*)"', html)
    assert len(anker) >= 2, (
        f"nur {len(anker)} Zeitanker auf der Seite — started_at, finished_at "
        f"und archived_at tragen je einen (#183)")


# ── #185: der Chip ohne Komma ──────────────────────────────────────────────


def test_der_maintenance_chip_wird_nicht_mit_komma_abgetrennt():
    """*„Der Chip soll nicht mit Komma abgetrennt werden"* (m.rau, 2026-08-13)

    Das Komma stammt aus dem ursprünglichen Vorschlag, wo `up 41 min ago,
    maintenance mode` als **Text** stand — und als Text war es richtig. **Mit
    dem Chip trennt die Kachelform schon; das Komma trennt ein zweites Mal.**

    Der Abstand kommt danach aus einer CSS-Regel und nicht aus einem
    Leerzeichen im Markup: sonst steht er beim nächsten Umbau wieder woanders.
    """
    html = render.status_header(
        {"roles": ["controller"], "connect": {"ok": True, "last_at": 1.0}}, {},
        scheduler={"maintenance": True, "started_at": NOW - 2460}, now=NOW)

    assert "maintenance mode" in html, (
        "Absicherung: der Chip steht überhaupt in der Karte")
    assert not re.search(r',\s*<span class="chip[^"]*">maintenance', html), (
        "der Chip wird mit Komma abgetrennt — die Kachelform trennt schon "
        "(#185)")
    assert re.search(r'\.chip-inline\b[^}]*margin-left', render._CSS), (
        "der Abstand steht nicht als CSS-Regel — als Leerzeichen im Markup "
        "steht er beim nächsten Umbau woanders (#185)")


def test_die_abstandsklasse_ist_nach_ihrer_form_benannt():
    """Der Name sagt, **was das Bauteil tut**, nicht bei welcher Gelegenheit.

    `.chip-inline` ist „ein Chip, der einem Text in derselben Zeile folgt" —
    damit trägt es den nächsten Chip in derselben Lage mit. Ein `.chip-maint`
    wäre eine Regel für genau einen Anlass, und der zweite Anlass bekäme eine
    zweite Regel mit demselben Inhalt.

    **Hier stand zwischenzeitlich eine schärfere Begründung, und sie war
    falsch:** ein `.chip-maint` bräche die Gegenprobe
    `test_the_word_goes_away_with_the_mode`, weil dort dauerhaft `maintenance`
    im Markup stünde. Nachgesehen: jener Test prüft die Ausgabe von
    `status_header()`, und die trägt kein `<style>`. Die Klasse wäre ihm nie
    begegnet. **Eine Begründung, die einen fremden Test als Zeugen aufruft,
    ohne ihn gelesen zu haben, ist eine Vermutung im Gewand eines Belegs.**
    """
    assert re.search(r"\.chip-inline\b[^}]*margin-left", render._CSS), (
        "die Abstandsregel fehlt (#185)")
    assert "chip-inline" in render.status_header(
        {"roles": ["controller"], "connect": {"ok": True, "last_at": 1.0}}, {},
        scheduler={"maintenance": True, "started_at": NOW - 2460}, now=NOW), (
        "die Regel existiert, aber kein Markup trägt sie — eine Klasse ohne "
        "Träger ist der Fehler aus #148 in seiner Umkehrung")


# ── #184: zwei Icons, sechs Anker ──────────────────────────────────────────


def test_jede_zeitpunkt_funktion_liefert_einen_umschaltbaren_anker():
    """**Der Wächter für Teil 2, und er steht vor den beiden Einzelfällen.**

    Der Umschalter wirkt nicht selbst; er setzt `data-tfmt` an der Wurzel, und
    der Ticker rechnet alle Elemente mit `data-tp` um. **Wer keinen Anker
    trägt, wird nie angefasst.**

    `_abs_datetime()` konnte nur absolut und setzte keinen; `_ago()` schickte
    eine schon ausgerechnete Distanz mit `data-dur` — der tickt zwar, folgt
    aber dem Umschalter nicht. Beides sind Zeit**punkte**, und ein Zeitpunkt
    gehört an denselben Anker wie jeder andere.

    Geprüft werden die Funktionen und nicht die zwei Zellen aus der
    Beobachtung: `_ago()` hat drei Aufrufstellen und `_abs_datetime()` drei.
    **Eine Änderung, die an zwei Stellen wirkt und an vier nicht, ist die
    nächste Stelle, an der etwas auseinanderläuft.**
    """
    for name in ("_uhrzeit", "_abs_datetime", "_ago"):
        aus = getattr(render, name)(NOW - 300, NOW)
        assert re.search(r'<span[^>]*\bdata-tp="', aus), (
            f"{name}() liefert eine Zeitangabe ohne Anker — der Umschalter "
            f"fasst sie nie an (#184)")
        assert 'data-abs="' in aus, (
            f"{name}() liefert keinen Rückweg: ohne data-abs müsste der "
            f"Browser eine zweite Datumsregel mitbringen (#184)")


def test_ein_fehlender_zeitpunkt_bleibt_ein_nackter_strich():
    """Die Gegenprobe, und sie bewacht eine fremde Zusage.

    `_sched()` ersetzt einen Strich durch `offline` und prüft dafür auf
    **Gleichheit** mit `—`. Ein umhüllter Strich wäre nie mehr gleich, und
    `#147` fiele stillschweigend aus — der Fall ist im Docstring von
    `_uhrzeit()` festgehalten, weil er beim Bau von `#139` beinahe passiert
    wäre.
    """
    for name in ("_uhrzeit", "_abs_datetime", "_ago"):
        assert getattr(render, name)(None, NOW) == "—", (
            f"{name}(None) trägt eine Hülle — das bricht die "
            f"Gleichheitsprüfung in _sched() (#147)")


def test_connected_since_und_last_heartbeat_schalten_mit():
    """*„Die Anzeige `Connected since` schaltet nicht um … Ebenso die Spalte
    `Heartbeat`. Beide sollen ebenfalls umgeschaltet werden."* (m.rau)

    Die zwei Zellen aus der Beobachtung, am gerenderten Screen statt an der
    Funktion — der Wächter oben sagt, dass die Bauteile es können, dieser sagt,
    dass sie auch benutzt werden.
    """
    html = render._clients_table(
        [{"node_id": "n1", "name": "sarasate", "role": "worker",
          "status": "connected", "approval_status": "approved",
          "connected_at": NOW - 7200, "last_heartbeat": NOW - 12}],
        NOW)
    anker = re.findall(r'<span[^>]*\bdata-tp="([^"]+)"', html)
    assert len(anker) >= 2, (
        f"nur {len(anker)} umschaltbare Zeitangaben in der Nodes-Zeile — "
        f"Connected since und Last heartbeat tragen je einen (#184)")


def test_der_zeitumschalter_zeigt_seinen_zustand_mit_zwei_icons():
    """*„2 Icons als Toggle, eines für absolut und eines für relativ."* (m.rau)

    Heute ist es **ein** Icon mit einer `.on`-Klasse, und was die Klasse
    bedeutet, steht nur im `title`. Ein Betrachter sieht einen hervorgehobenen
    Knopf und weiß nicht, ob das der **aktuelle** Zustand ist oder der, den ein
    Klick **herstellt** — genau die Zweideutigkeit, die der `group`-Schalter
    eine Zeile weiter ausdrücklich anders gelöst hat.

    Lesart, bestätigt m.rau: das Icon zeigt den **aktuellen** Modus, wie eine
    Anzeige, nicht wie ein Versprechen. Das Muster dafür steht schon daneben —
    `conn-dot` trägt zwei Icons und lässt das CSS entscheiden.
    """
    nav = render._ops_handles({"roles": ["controller"]})

    knopf = re.search(r'<button id="tfmt".*?</button>', nav, re.S)
    assert knopf, "der Zeitumschalter fehlt in der App-Bar"
    icons = re.findall(r"<svg", knopf.group(0))
    assert len(icons) == 2, (
        f"der Umschalter trägt {len(icons)} Icon(s) — zwei machen den Zustand "
        f"sichtbar, statt ihn in einer Klasse zu kodieren (#184)")
    assert re.search(r"\.ico-abs\b", render._CSS) and \
        re.search(r"\.ico-rel\b", render._CSS), (
        "die Umschaltregel steht nicht im Stylesheet — zwei Icons ohne Regel "
        "stünden beide da (#148)")


# ── #182: dieselbe Seite, eine zweite Quelle ───────────────────────────────


def test_ein_lauf_im_slot_bietet_seine_attribute_an():
    """*„für einen laufenden Job gibt es keine Möglichkeit, die vollständigen
    Attribute zu sehen"* (m.rau, 2026-08-13).

    **Die Ursache liegt eine Ebene vor der Route:** `_run_zeile()` rendert den
    `attrs`-Link nur für archivierte Läufe. Ihr eigener Docstring sagt warum —
    *„ein Lauf im Slot hat keine Journal-Zeile und damit keinen Snapshot — ein
    Link dorthin wäre ein toter Knopf"* — und nennt die Lücke ausdrücklich als
    eigenes Ticket statt sie stillschweigend zu überbrücken. Das ist dieses.

    Die Werte stehen vollständig in `jobs.run_snapshot`, seit `#129` und seit
    `#164` auch verlässlich genullt. Der Weg dorthin ist derselbe wie beim
    Output: über `slot/{quelle}/{job_id}`, weil dieselbe Job-ID auf beiden
    Seiten einen anderen Job meint.

    **`src` trägt ``C``, nicht ``local``** (#193, `v0.8.14`). Hier stand der
    erfundene Wert, und der gerenderte Link war deshalb im Betrieb ein 404 —
    der Test hat die Wirkung richtig gemessen und die Eingabe erfunden.
    """
    zeile = render._run_zeile(
        {"id": None, "job_id": "j7", "src": "C", "in_slot": True,
         "status": "running", "sort_at": NOW - 60},
        basis="/-/jobs/abc123")

    assert "attrs" in zeile, (
        "ein Lauf im Slot bietet seine Attribute nicht an — der einzige Lauf "
        "ohne diesen Weg ist ausgerechnet der laufende (#182)")
    assert "/slot/client/j7/attrs" in zeile, (
        "der Weg führt über die Journal-ID, die ein Slot-Lauf nicht hat — "
        "er muss über slot/{quelle}/{job_id} gehen wie beim Output (#182)")


def test_ein_archivierter_lauf_behaelt_seinen_weg():
    """Die Gegenprobe: der bestehende Weg bleibt, wie er ist.

    Ohne sie wäre auch ein Fix grün, der **jeden** Lauf über den Slot-Weg
    schickt — und der fände für einen archivierten Lauf nichts, weil dessen
    Slot längst neu belegt oder leer ist.
    """
    zeile = render._run_zeile(
        {"id": 42, "src": "local", "in_slot": False,
         "status": "complete", "sort_at": NOW - 600},
        basis="/-/jobs/abc123")
    assert "/-/jobs/abc123/runs/42/attrs" in zeile, zeile
    assert "/slot/" not in zeile, (
        "ein archivierter Lauf wird über den Slot-Weg geschickt — dort steht "
        "er nicht mehr (#182)")


def test_die_attributseite_eines_laufenden_laufs_zeigt_seine_felder(
        app_with, team_repo: Path):
    """**Der Rot-Schritt aus dem Ticket:** heute ist sie leer.

    `run_attrs_page_v5()` liest den `snapshot` aus der Journal-Zeile, und die
    entsteht unter A2 erst beim Archivieren. Der Renderer bleibt deshalb
    unverändert — **es ist keine zweite Seite, sondern eine zweite Quelle für
    dieselbe** (bestätigt m.rau: *„Genau so habe ich mir das gedacht weil genau
    so habe ich es verstanden."*). Die Route entscheidet, woher der Lauf kommt.
    """
    from bibi.daemon import job_db

    ordner = team_repo / "vault" / "case" / "x"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "laeuft.md").write_text(
        "---\nslug: laeuft\nschedule: adhoc\njob: sleep 9000\n---\n",
        encoding="utf-8")

    app = app_with({"roles": ["controller"]})
    conn = job_db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, fire, status, payload, kind, "
            "schedule_ref, started_at, attempt, run_snapshot) "
            "VALUES ('j7','laeuft','adhoc','running','sleep 9000','job',"
            "'case/x/laeuft.md',?,0,?)",
            # `backoff` und nicht `model`: `model` ist nach `_TYP_GEBUNDEN` an
            # `claude`-Läufe gebunden, und dieser hier ist ein Shell-Job. Ein
            # Test, der darauf prüft, wäre aus dem falschen Grund rot — und
            # nach dem Fix aus dem falschen Grund weiterhin.
            (NOW - 60, json.dumps(_snapshot(slug="laeuft",
                                            payload="sleep 9000",
                                            backoff="exponential"))))
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as c:
        # `client`, nicht `local` (#193, `v0.8.14`): die Route hieß hier als
        # einzige Stelle im System so, und der gerenderte Link traf sie nie.
        r = c.get(f"/-/jobs/{job_uid('laeuft')}/slot/client/j7/attrs")

    assert r.status_code == 200, r.text[:300]
    assert "exponential" in r.text, (
        "die Attributseite eines laufenden Laufs zeigt seine Felder nicht — "
        "sie liest nur das Journal (#182)")
    assert "Nothing recorded" not in r.text, (
        "die Seite meldet Leere, obwohl run_snapshot gefüllt ist (#182)")


# ── #181: Bauteile und benannte Sets, nicht eine Belegung ──────────────────


def _kachel(**kw):
    """**Die Schlüssel sind die einer echten Slot-Zeile** (#194, `v0.8.14`).

    Hier stand ``"run_id": "EngineCI:4"`` — ein Feld, das keine Zeile dieses
    Systems trägt. Der Test war grün, die Kachel im Betrieb leer: `run_id` ist
    aus ``slug``/``id``/``fire`` **berechenbar** und steht in keiner Spalte.
    Genau diese Erfindung hat `#194` durchgelassen.

    Was hier steht, muss deshalb auch in einer echten Zeile stehen — der
    Wächter in `test_wovon_zwei_seiten_reden.py` prüft das von der anderen
    Seite her, an den zwei Quellen, die eine Kachel wirklich speisen.
    """
    from bibi.controller.jobs_view import Tile
    slot = {"id": "j1", "slug": "EngineCI", "fire": 4, "worker": "w1",
            "exec_mode": "host", "schedule_ref": "case/x/EngineCI.md",
            "attempt": 1, "attempts": 3, "reason": None, "exit_code": None,
            "next_fire_at": None, "started_at": NOW - 30, "finished_at": None,
            "kind": "job", "schedule": "0 * * * *"}
    slot.update(kw.pop("slot", {}))
    basis = {"quelle": "SCHEDULER", "host": "sarasate", "slot": slot,
             "status": "running", "aktionen": frozenset(), "last_at": None}
    basis.update(kw)
    return Tile(**basis)


def test_der_vorrat_traegt_die_dreizehn_felder():
    """*„Es wird immer _Sets_ von Attributen geben, die eine fachliche Auswahl
    darstellen."* (m.rau, 2026-08-13) — **der tragende Satz dieses Postens.**

    Er sagt: die Card hat keine richtige Belegung, sondern **mehrere benannte**,
    und welche gilt, hängt daran, was man gerade tut. Der Vorrat ist das, woraus
    sie schöpfen; er steht deshalb als eigene Größe da und nicht verstreut in
    einer Renderfunktion.

    **Alle dreizehn liegen jede Sekunde bereit** — kein Feld muss erhoben
    werden, keins kostet einen zweiten Zugriff. Die Netto-Laufzeit ist das eine,
    das nach m.raus Schnitt (*fachlich relevant gegen nachrangig*) nicht in die
    Card gehört; sie wird trotzdem erfasst. **Erfassen und Zeigen sind zwei
    Entscheidungen, und nur die zweite fällt hier.**
    """
    vorrat = render.KACHEL_VORRAT

    for feld in ("status", "attempts", "reason", "exit_code", "next", "run_id",
                 "worker", "kind", "schedule", "exec_mode", "schedule_ref",
                 "started_at", "finished_at"):
        assert feld in vorrat, f"{feld} fehlt im Vorrat der Kachel (#181)"
    assert "exec_runtime" not in vorrat, (
        "die Netto-Laufzeit ist das eine Feld, das nach dem Schnitt nicht in "
        "die Card gehört — erfasst wird sie trotzdem (#181)")


def test_zwei_sets_aus_demselben_vorrat_keines_teilmenge_des_anderen():
    """*„Wer einen Fehlschlag verfolgt, will `reason`, `exit_code`, `attempts
    n/m`, `next`. Wer einen Lauf zuordnet, will `run_id`, `worker`,
    `exec_mode`, `schedule_ref`."*

    **Zwei Sets aus demselben Vorrat, und keines ist eine Teilmenge des
    anderen** — das ist die Prüfung, die „Set" von „Kurzfassung" unterscheidet.
    Wäre eines enthalten, hätte man keine zwei Auswahlen, sondern eine lange
    und eine kurze.
    """
    sets = render.KACHEL_SETS
    assert len(sets) >= 2, "ein Set ist keine Auswahl (#181)"

    fehlschlag = set(sets["fehlschlag"])
    zuordnung = set(sets["zuordnung"])
    assert {"reason", "exit_code", "attempts", "next"} <= fehlschlag
    assert {"run_id", "worker", "exec_mode", "schedule_ref"} <= zuordnung
    assert not fehlschlag <= zuordnung and not zuordnung <= fehlschlag, (
        "ein Set ist Teilmenge des anderen — dann sind es keine zwei "
        "Auswahlen, sondern eine lange und eine kurze (#181)")

    for name, felder in sets.items():
        unbekannt = set(felder) - set(render.KACHEL_VORRAT)
        assert not unbekannt, (
            f"Set {name!r} nennt {sorted(unbekannt)}, die es im Vorrat nicht "
            f"gibt (#181)")


def test_ein_drittes_set_entsteht_ohne_ein_bauteil_anzufassen():
    """**Die Zusage des Tickets, als Test:** *„der Vorrat muss so gebaut sein,
    dass ein zweites Set kein Umbau ist."*

    Ob die Auswahl später umschaltbar wird oder fest verdrahtet bleibt,
    entscheidet der Varianten-Case. Was hier gebaut wird, ist die
    Voraussetzung dafür — und die ist prüfbar, **bevor** jemand das dritte Set
    braucht. Danach wäre sie es auch, aber dann als Umbau.
    """
    eigenes = ("kind", "schedule", "started_at", "finished_at")
    aus = render.kachel_set(_kachel(), eigenes, now=NOW)

    assert aus, "ein Set aus dem Vorrat rendert nichts (#181)"
    assert "0 * * * *" in aus, (
        "das frei zusammengestellte Set zeigt seine Werte nicht — dann wäre "
        "ein zweites Set doch ein Umbau (#181)")


def test_ein_feld_ohne_wert_steht_nicht_da():
    """**Jede Angabe nur, wenn es sie gibt** — die Regel gilt schon für `commit`
    und `last`, und der Vorrat erbt sie.

    *„Ein leeres `commit —` sähe aus wie ein Fehler, wo nur nichts zu sagen
    ist."* Bei dreizehn möglichen Feldern wiegt das schwerer als bei dreien:
    eine Kachel voller Striche ist keine dichte Information, sondern Lärm.
    """
    leer = _kachel(slot={"reason": None, "exit_code": None, "next_fire_at": None,
                         "run_id": None, "worker": None, "exec_mode": None,
                         "schedule_ref": None, "attempts": None, "attempt": None})
    aus = render.kachel_set(leer, ("reason", "exit_code", "next", "worker"),
                            now=NOW)
    assert "—" not in aus, (
        f"ein Feld ohne Wert steht mit Strich da: {aus!r} (#181)")


def test_attempts_steht_als_n_von_m():
    """*„`attempts` als *n/m*"* — die Form ist die Aussage.

    Eine nackte `3` beantwortet nicht, ob das der dritte von drei Versuchen ist
    oder der dritte von zehn. **Der Unterschied entscheidet, ob man noch wartet
    oder schon nachsieht.**
    """
    aus = render.kachel_set(_kachel(slot={"attempt": 2, "attempts": 3}),
                            ("attempts",), now=NOW)
    assert "2/3" in aus, f"attempts steht nicht als n/m da: {aus!r} (#181)"


def test_die_run_id_steht_klein_und_ohne_beschriftung():
    """*„ja. deshalb ganz klein. deshalb auch layout !?!?!"* (m.rau)

    Ich hatte argumentiert, `run_id` gehöre nicht in die Kachel: er koste eine
    ganze Zeile für einen Wert, den man kopiert statt liest. **Das stimmt nur,
    solange jedes Feld dieselbe Größe hat.** Sobald ein Wert klein, gedimmt und
    ohne Beschriftung dastehen darf, kostet er keine Zeile mehr, sondern einen
    Rand — und die Frage *„passt das noch"* ist keine Auswahlfrage mehr,
    sondern eine Gestaltungsfrage.

    **Das ist der Grund, warum es Bauteile gibt und nicht nur eine Liste.**
    """
    aus = render.kachel_set(_kachel(), ("run_id",), now=NOW)
    assert "EngineCI:4" in aus, aus
    assert "kt-fein" in aus, (
        "die run_id steht in derselben Größe wie ein Zustandsfeld — dann "
        "kostet sie die Zeile, die gegen sie sprach (#181)")
    assert "run_id" not in aus.replace("EngineCI:4", ""), (
        "die run_id trägt eine Beschriftung, obwohl gerade ihre Beschriftungs"
        "losigkeit sie klein macht (#181)")


def test_der_schedule_ref_steht_ohne_pfad_und_endung():
    """*„`schedule_ref` ohne Pfad und Endung, wie Du vorschlägst — das ist die
    einzige Angabe, mit der man die MD wiederfindet."*

    Der volle Pfad `case/x/EngineCI.md` kostet die halbe Kachelbreite für zwei
    Angaben, die man schon kennt: dass es im Vault liegt und dass es Markdown
    ist. **Was man sucht, ist der Name.**
    """
    aus = render.kachel_set(_kachel(), ("schedule_ref",), now=NOW)
    assert "EngineCI" in aus
    assert "case/x/" not in aus and ".md" not in aus, (
        f"der Pfad steht in der Kachel: {aus!r} (#181)")


def test_die_kachel_traegt_ein_benanntes_set():
    """**Der Prüfumfang, wörtlich:** *„Die Kachel trägt ein benanntes **Set**
    aus dem Vorrat der dreizehn Felder; die Attributseite trägt den
    vollständigen Katalog, auch für einen **laufenden** Lauf."*

    Ohne diesen Test wären Vorrat und Sets gebauter, ungenutzter Code — das
    Muster aus Runde 2, fünf von sechs Posten.

    **Welches Set eine Kachel trägt, ist verdrahtet und soll es nicht bleiben.**
    Wer einen Fehlschlag vor sich hat, verfolgt einen Fehlschlag; wer einen
    laufenden Lauf vor sich hat, ordnet ihn zu. Das ist eine Vermutung über den
    Leser aus seinem Zustand heraus, und sie ist an den Varianten zu prüfen —
    nicht hier zu entscheiden.
    """
    laufend = render._slot_kachel(_kachel(status="running"), now=NOW)
    assert "EngineCI:4" in laufend, (
        "die Kachel trägt kein Set — Vorrat und Bauteile hätten keinen "
        "Aufrufer (#181)")
    assert "w1" in laufend, laufend

    gescheitert = render._slot_kachel(
        _kachel(status="failed",
                slot={"reason": "timeout", "exit_code": 124,
                      "next_fire_at": NOW + 300}),
        now=NOW)
    assert "timeout" in gescheitert and "124" in gescheitert, (
        "ein Fehlschlag zeigt nicht, woran er scheiterte (#181)")


def test_jede_klasse_der_bauteile_hat_eine_css_regel():
    """Der Fehler aus `#148`, im Voraus verhindert.

    Eine Klasse ohne Regel ist ein Name ohne Wirkung — und sie fällt niemandem
    auf, weil das Markup sie trägt und die Seite trotzdem aussieht wie vorher.
    `test_offline_marks_the_scheduler_hostname_red` hat fünf Releases lang
    bestätigt, dass ein Hostname `class="bad"` trägt, während es im ganzen
    Stylesheet keine Regel für ein blankes `.bad` gab.
    """
    for klasse in ("kt", "kt-l", "kt-fein"):
        assert re.search(rf"\.{klasse}\b", render._CSS), (
            f".{klasse} steht im Markup, aber in keiner CSS-Regel (#148)")


# ── #187: Rot findet keinen Rückweg ────────────────────────────────────────


_VERBUNDEN = {"roles": ["controller"], "connect": {"ok": True, "last_at": 1.0}}
_GETRENNT = {"roles": ["controller"], "connect": {"ok": False, "last_at": 1.0}}


def test_der_verbindungszustand_hat_eine_quelle():
    """**Zwei Stellen, die denselben Zustand berechnen, laufen auseinander.**

    Der Punkt im Nav-Kopf entscheidet heute selbst, ob er `ok`, `warn` oder
    `bad` trägt. Damit ihn eine erneuerbare Region nachziehen kann, muss sie
    dieselbe Antwort bekommen — und das geht nur, wenn beide dieselbe Funktion
    fragen. Sonst ist der Rückweg aus `bad` eine zweite Meinung darüber, wann
    eine Verbindung steht.
    """
    assert render.verbindungszustand(_VERBUNDEN, scheduler={})[0] == "ok"
    assert render.verbindungszustand(_GETRENNT, scheduler={})[0] == "bad"
    assert render.verbindungszustand(
        _VERBUNDEN, scheduler={"maintenance": True})[0] == "warn"
    # **Kein `connect`-Dict heißt „nie eine Verbindung gehabt"**, und das ist
    # etwas anderes als `scheduler=None`: Letzteres sagt nur, dass diese
    # Render-Stelle keinen Scheduler-Status mitbekommen hat. Der dritte Fall
    # aus #70 hängt an `connect`, nicht am Parameter — die erste Fassung dieses
    # Tests hat beides verwechselt und wäre aus dem falschen Grund rot gewesen.
    ohne_scheduler = {"roles": ["controller"]}
    assert render.verbindungszustand(ohne_scheduler, scheduler=None)[0] == "bad", (
        "ohne konfigurierten Scheduler ist der Punkt rot — es gab nie eine "
        "Verbindung, die abreißen konnte")
    assert render.hat_gegenueber(ohne_scheduler) is False
    assert render.hat_gegenueber(_VERBUNDEN) is True


def test_das_statusbundle_traegt_den_verbindungszustand():
    """*„Der Nav-Kopf steht außerhalb jeder Bus-Region."* — das ist die Ursache.

    Heartbeat und Scheduler-Karte heilen sich, weil sie im `#feedstatus`-Bundle
    hängen, das über den Bus erneuert wird. Der Punkt hängt nirgends. **Er
    bekommt seinen Rückweg deshalb dort, wo die Wahrheit ohnehin ankommt** —
    das Bundle trägt den Zustand, und der Punkt folgt ihm.
    """
    frag = render.feed_status_fragment(_VERBUNDEN, {}, None, NOW, scheduler={})
    assert 'data-conn="ok"' in frag, (
        "das erneuerbare Bundle sagt nicht, ob die Verbindung steht — dann "
        "kann nichts den Punkt zurückschalten (#187)")

    getrennt = render.feed_status_fragment(_GETRENNT, {}, None, NOW, scheduler={})
    assert 'data-conn="bad"' in getrennt, getrennt[:200]


def test_der_punkt_wird_nachgezogen_wenn_das_bundle_kommt():
    """**Der Rückweg selbst.** Die rote Stellung wurde ausschließlich
    serverseitig vergeben, und im Browser gab es keine einzige Stelle, die sie
    wieder entfernt — im Gegenteil: `setMaint()` steigt als Erstes aus, wenn
    sie gesetzt ist.

    **Die Regel dahinter war richtig, ihr Ausgang fehlte.** Sie beantwortet
    *„darf Maintenance Rot überschreiben?"* mit Nein — zutreffend, denn wer
    nicht verbunden ist, weiß über den Modus des Hosts nichts Aktuelles. Sie
    beantwortet nicht *„wer nimmt Rot zurück, wenn die Verbindung wieder
    steht?"*, und die Antwort lautete: niemand außer einem Reload.
    """
    js = render._OPS_HANDLES_JS
    assert "data-conn" in js, (
        "kein Handler liest den Zustand aus dem erneuerten Bundle (#187)")
    assert "htmx:afterSettle" in js, (
        "der Rückweg hängt an keinem Ereignis — dasselbe Ereignis, an dem "
        "seit #160 auch der Zeit-Ticker nachzieht (#187)")
