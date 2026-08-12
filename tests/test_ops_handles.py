"""Die drei Ops-Handles der App-Bar (bibi5, FE-Spezifikation §2).

`⟳` Rescan · `◐` Maintenance · `●` Verbindung.

Der Verbindungspunkt trägt **drei** Zustände statt zwei: grün verbunden,
**orange Maintenance aktiv**, rot getrennt. Das geht auf einen Befund des
Auftraggebers zurück: *„Es kam immer wieder vor, dass wir uns gefragt haben,
warum der Job nicht startet. Wir hatten den Maintenance Mode übersehen."*

Die Antwort darauf ist nicht eine zweite Ampel, sondern eine dritte Farbe an
der vorhandenen. Ein Modus, der die Automatik anhält, gehört dorthin, wo man
ohnehin nachsieht, ob die Verbindung steht.
"""

from __future__ import annotations

import pytest

from bibi.controller import render

CLIENT = {"roles": ["controller", "connect"], "connect": {"ok": True, "last_at": 1.0}}
HOST = {"roles": ["scheduler", "controller"]}


def test_three_handles_in_this_order():
    """Die Reihenfolge steht an den IDs, nicht an den Zeichen.

    **Bis `#159` stand hier ein Vergleich über die Glyphen selbst.** Die sind
    mit dem Icon-Satz gefallen — und das ist der Grund, warum ein Test die
    *Sache* prüfen sollte und nicht ihre momentane Schreibweise: die Reihenfolge
    der Handles hat sich nicht geändert, nur ihre Darstellung.
    """
    html = render._ops_handles(CLIENT)
    assert html.index('id="rescan"') < html.index('id="maint"') \
        < html.index('id="conn-dot"')


def test_maintenance_uses_the_half_filled_circle():
    """Ein halb gefüllter Kreis ist ein Zustand zwischen an und aus — genau das
    ist Maintenance. Ein Zahnrad wäre eine Einstellung, ein Warndreieck ein
    Fehler; beides trifft es nicht.

    **Die Begründung überlebt den Icon-Satz, das Zeichen nicht** (`#159`): aus
    dem Textzeichen ``◐`` (U+25D0) wird lucide ``contrast`` — dieselbe Aussage,
    aber aus derselben Quelle gezeichnet wie seine Nachbarn.
    """
    html = render._ops_handles(CLIENT)
    maint = html[html.index('id="maint"'):]
    maint = maint[:maint.index("</button>")]
    assert 'd="M12 18a6 6 0 0 0 0-12v12z"' in maint, maint
    assert "⚙" not in html and "⚠" not in html


# ── Der Verbindungspunkt ────────────────────────────────────────────────────


def test_connected_is_green():
    html = render._ops_handles(CLIENT, scheduler={"maintenance": False})
    punkt = [z for z in html.split("<") if "conn-dot" in z]
    assert punkt and "ok" in punkt[0]


def test_maintenance_turns_the_dot_amber():
    """Die eigentliche Neuerung: der Modus ist an der Ampel abzulesen, ohne
    dass man den Maintenance-Knopf ansieht."""
    html = render._ops_handles(CLIENT, scheduler={"maintenance": True})
    punkt = [z for z in html.split("<") if "conn-dot" in z]
    assert punkt and "warn" in punkt[0]


def test_disconnected_is_red_and_beats_maintenance():
    """Getrennt schlägt Maintenance: wer nicht verbunden ist, weiß über den
    Modus des Hosts ohnehin nichts Aktuelles."""
    aus = dict(CLIENT)
    aus["connect"] = {"ok": False, "last_at": 1.0}
    html = render._ops_handles(aus, scheduler={"maintenance": True})
    punkt = [z for z in html.split("<") if "conn-dot" in z]
    assert punkt and "bad" in punkt[0]


def test_the_dot_says_what_it_means():
    """Farbe allein reicht nicht — der `title` nennt den Zustand im Klartext,
    für Hover und für Screenreader."""
    for sched, wort in (({"maintenance": False}, "connected"),
                        ({"maintenance": True}, "maintenance")):
        html = render._ops_handles(CLIENT, scheduler=sched)
        assert wort in html.lower()


def test_a_scheduler_node_is_always_connected():
    """Der Host ist mit sich selbst verbunden — dort gibt es keinen Heartbeat,
    und ein roter Punkt wäre schlicht falsch."""
    html = render._ops_handles(HOST, scheduler={"maintenance": False})
    punkt = [z for z in html.split("<") if "conn-dot" in z]
    assert punkt and "ok" in punkt[0]


def test_maintenance_state_comes_from_the_scheduler_not_from_here():
    """Ein Client hat keinen eigenen Maintenance-Modus. Zeigte der Punkt den
    lokalen Wert, stünde dort dauerhaft „aus" — und der Befund, der zu dieser
    Anzeige geführt hat, wäre nicht behoben, sondern verkleidet."""
    html = render._ops_handles(CLIENT, scheduler={"maintenance": True})
    punkt = [z for z in html.split("<") if "conn-dot" in z]
    assert punkt and "warn" in punkt[0], "der Modus des Hosts zählt"


# ── Der Knoten ohne Gegenüber (#70) ─────────────────────────────────────────
#
# **Befund m.rau, 2026-08-07:** „wie kann bei _disconnected_ das Signal im Tab
# rechts **grün** sein?"
#
# Die Bedingung war als „nur ein ausdrückliches `False` heißt getrennt"
# geschrieben:
#
#     ((status or {}).get("connect") or {}).get("ok") is not False
#
# Ein Knoten ohne Scheduler-URL hat gar kein `connect`-Dict — `app.py:1339`
# setzt es nur `if heartbeat is not None`. `{}.get("ok")` liefert `None`, und
# `None is not False` ist `True`. Der Fall „es gibt nichts, womit ich verbunden
# sein könnte" fiel damit auf die **grüne** Seite.
#
# ⚠ Diese Tests lassen den Schlüssel **weg** und setzen ihn nicht auf `None` —
# ein Payload mit `connect: None` ist ein Zustand, den die Engine nie erzeugt.
# Ein Test dagegen kann grün werden, ohne etwas zu belegen.

#: Ein Client ohne konfigurierten Scheduler: keine `connect`-Rolle, also auch
#: kein `connect`-Schlüssel im Payload. Genau der Knoten, an dem m.rau den
#: grünen Punkt gesehen hat.
OHNE_SCHEDULER = {"roles": ["synchronizer", "controller"]}


def test_a_node_without_any_scheduler_is_not_green():
    """Der Kern von #70: kein Gegenüber ist nicht dasselbe wie „verbunden"."""
    html = render._ops_handles(OHNE_SCHEDULER)
    punkt = [z for z in html.split("<") if "conn-dot" in z]
    assert punkt, "kein Verbindungspunkt gerendert"
    assert "ok" not in punkt[0].split('class="')[1], f"grün ohne Gegenüber: {punkt[0]}"
    assert "bad" in punkt[0], f"erwartet rot, bekommen: {punkt[0]}"


def test_the_dot_names_the_case_instead_of_claiming_a_lost_connection():
    """„disconnected" wäre nicht falsch, aber irreführend: es gab nie eine
    Verbindung, die abreißen konnte. Der Titel muss den Fall benennen, sonst
    sucht man den Fehler beim Scheduler statt in der eigenen Konfiguration."""
    html = render._ops_handles(OHNE_SCHEDULER)
    punkt = [z for z in html.split("<") if "conn-dot" in z][0]
    titel = punkt.split('title="')[1].split('"')[0].lower()
    assert "scheduler" in titel, f"der Titel nennt den Fall nicht: {titel!r}"


def test_a_missing_connect_key_does_not_go_amber_either():
    """Auch mit Maintenance am (rein hypothetisch — ohne Scheduler kommt kein
    Wert) bleibt es rot: getrennt schlägt Maintenance, und „nie verbunden"
    erst recht."""
    html = render._ops_handles(OHNE_SCHEDULER, scheduler={"maintenance": True})
    punkt = [z for z in html.split("<") if "conn-dot" in z][0]
    assert "bad" in punkt


def test_a_scheduler_node_stays_green_without_a_connect_key():
    """Die Gegenprobe, damit der Fix nicht über das Ziel hinausschießt: der
    Host hat aus demselben Grund kein `connect` — er befragt sich nicht selbst
    über HTTP. Dort wäre rot schlicht falsch."""
    html = render._ops_handles(HOST, scheduler={"maintenance": False})
    punkt = [z for z in html.split("<") if "conn-dot" in z][0]
    assert "ok" in punkt.split('class="')[1]


# ── Wer den Maintenance-Schalter erreicht (#69) ─────────────────────────────
#
# **Befund m.rau, 2026-08-07:** „es **muss** vom Client aus schaltbar sein. Es
# gibt keinen Controller mehr auf Scheduler oder Worker."
#
# Der Knopf fragte „bin ich der Scheduler". Seit dem 2026-08-06 trägt das
# Profil `scheduler` aber ausdrücklich **kein** `controller` mehr (`roles.py`:
# „der Scheduler ist Backend"). Damit gab es keinen Knoten, auf dem beides
# zugleich wahr war: wer die Oberfläche hatte, war kein Scheduler; wer
# Scheduler war, hatte keine Oberfläche. Eine vollständig gebaute Funktion,
# die niemand auslösen konnte.
#
# Die richtige Frage ist die, die `_ops_ziel()` längst beantwortet: **habe ich
# einen Scheduler** — konfiguriert, oder als der ich selbst laufe. Sie ist
# dieselbe, aus der oben die Farbe des Punktes folgt.

#: Ein Client mit konfiguriertem Scheduler — die `connect`-Rolle gibt es genau
#: dann, und `app.py` legt das `connect`-Dict genau dann an.
CLIENT_MIT_SCHEDULER = {
    "roles": ["synchronizer", "controller", "connect"],
    "connect": {"ok": True, "last_at": 1.0},
}


def _maint_knopf(html: str) -> str:
    return [z for z in html.split("<") if 'id="maint"' in z][0]


def test_maintenance_is_reachable_from_a_client():
    """Der Kern von #69. Ohne diesen Fix ist der Schalter von nirgendwo aus zu
    erreichen — nicht schwer erreichbar, unerreichbar."""
    knopf = _maint_knopf(render._ops_handles(CLIENT_MIT_SCHEDULER))
    assert "disabled" not in knopf, f"der Knopf bleibt gesperrt: {knopf}"


def test_maintenance_stays_locked_without_any_scheduler():
    """Die Gegenprobe, und sie ist keine Formalie: ohne Scheduler gibt es
    nichts zu schalten, und die Sperre ist dann richtig statt falsch."""
    knopf = _maint_knopf(render._ops_handles(OHNE_SCHEDULER))
    assert "disabled" in knopf


def test_the_bootstrap_scheduler_keeps_its_button():
    """⚠ Der einzige Knoten, auf dem der Knopf heute funktioniert, darf ihn
    nicht verlieren: `bibi-ctrl init --profile scheduler --with-ui` hängt einem
    Scheduler doch einen `controller` an — „für den **ersten** Knoten eines
    Teams, der noch keinen Client neben sich hat" (`roles.py`). Dort läuft es
    über den lokalen Zweig von `_ops_ziel()`."""
    knopf = _maint_knopf(render._ops_handles(HOST, scheduler={"maintenance": False}))
    assert "disabled" not in knopf


def test_the_locked_button_no_longer_claims_it_is_a_host_matter():
    """Der Titel sagte „maintenance: host only" — nach dieser Änderung ist das
    die falsche Auskunft. Gesperrt ist er, weil kein Scheduler da ist, nicht
    weil der Knoten der falsche wäre."""
    knopf = _maint_knopf(render._ops_handles(OHNE_SCHEDULER))
    assert "host only" not in knopf, f"veraltete Begründung im Titel: {knopf}"


def test_a_client_toggle_reflects_the_schedulers_state_not_its_own():
    """Der Knopf zeigt den Modus des Schedulers — ein Client hat keinen
    eigenen. Sonst schaltete man einen Zustand, den man nicht sieht."""
    knopf = _maint_knopf(
        render._ops_handles(CLIENT_MIT_SCHEDULER, scheduler={"maintenance": True}))
    assert "warn" in knopf, f"der Modus des Schedulers fehlt am Knopf: {knopf}"


# ── Wohin die Handles greifen (m.rau/bibi#142) ──────────────────────────────
#
# **Befund m.rau, 2026-08-05:** „Das Refresh funktioniert gar nicht, oder? In
# keinem Screen!?"
#
# Beide Handles riefen **relativ** auf und trafen damit den Daemon, der die
# Seite ausgeliefert hat — den eigenen Client. Gemessen am selben Tag:
#
#     POST 127.0.0.1:54824/-/rescan        → 404   (Route haengt an `scheduler`)
#     POST sarasate:8780/-/rescan          → 200   {"inserted":0,"updated":19,…}
#     POST 127.0.0.1:54824/-/maintenance   → 200   {"maintenance":true}   ← lokal!
#
# Der Rescan lief ins Leere und meldete trotzdem `✓`; die Maintenance-Umschaltung
# ist der schlimmere Fall, weil sie **wirkt** — nur am falschen Knoten. FE §2
# verlangt fuer beide ausdruecklich den Scheduler.
#
# Der Weg dorthin ist der Controller, nicht der Browser: der Scheduler sendet
# keine CORS-Header (gemessen), ein direkter Cross-Origin-POST scheiterte also.
# Dasselbe Muster wie bei den Job-Verben (`/-/ui/jobs/verb/...`).


def test_rescan_does_not_call_this_node_directly():
    """Eine relative URL trifft immer den ausliefernden Daemon."""
    js = render._OPS_HANDLES_JS
    assert "'/-/rescan'" not in js and '"/-/rescan"' not in js


def test_rescan_goes_through_the_controller():
    js = render._OPS_HANDLES_JS
    assert "/-/ui/ops/rescan" in js


def test_maintenance_goes_through_the_controller():
    """Der wirksame Fall: er schaltete den lokalen Modus statt den des Hosts."""
    js = render._OPS_HANDLES_JS
    assert "'/-/maintenance'" not in js and '"/-/maintenance"' not in js
    assert "/-/ui/ops/maintenance" in js


def test_rescan_checks_the_answer_before_claiming_success():
    """`catch(_){}` fing jeden Fehler weg, und der Haken stand **ausserhalb**
    des `try` — er hing damit an keiner Bedingung.

    Ein Knopf, der Erfolg behauptet, den es nicht gab, ist teurer als einer,
    der schweigt: er laedt nicht zum Nachsehen ein. Genau deshalb blieb der
    Fehler so lange unbemerkt.
    """
    js = render._OPS_HANDLES_JS
    kopf = js[js.index("const rescan"):js.index("const maint")]
    assert "r.ok" in kopf or "res.ok" in kopf, "die Antwort wird nicht geprueft"
    assert "catch(_){}" not in kopf, "der Fehler wird weiterhin verschluckt"


def test_rescan_shows_a_failure():
    """Sonst ist „ging nicht" von „ging" nicht zu unterscheiden.

    **Geprüft wird die Verzweigung, nicht das Zeichen** (`#159`). Hier stand
    ``"✕" in kopf`` — richtig, solange das JS die Glyphe selbst schrieb. Seit
    dem Icon-Satz trägt der Knopf alle drei Zeichen im Markup und das JS schaltet
    nur eine Klasse; dass die Fehlerklasse eine andere ist als die Erfolgsklasse,
    ist die Aussage, die hier zählt.

    Dass am Ende ein Kreuz **zu sehen** ist, prüft
    ``tests/browser/test_rescan_button.py`` — dort, wo man es sehen kann.
    """
    kopf = render._OPS_HANDLES_JS
    kopf = kopf[kopf.index("const rescan"):kopf.index("const maint")]
    assert "'quittung-'" in kopf, kopf
    assert "ok ? 'ok' : 'bad'" in kopf, kopf


# ── Und er landet auch wirklich beim Scheduler (#69) ────────────────────────
#
# Der Knopf nicht mehr `disabled` zu rendern ist die halbe Auskunft. „Schaltbar
# vom Client aus" heisst, dass die Umschaltung beim **Scheduler** ankommt — der
# Weg dorthin (`_ops_ziel()`) ist am 2026-08-05 gebaut worden, hatte fuer
# Maintenance aber keinen Test, weil ihn kein Knoten erreichen konnte.


def test_a_client_switches_maintenance_at_the_scheduler_not_at_itself(
        team_repo, monkeypatch):
    """Der Fall, den #69 ueberhaupt erst moeglich macht — und zugleich der, den
    m.rau/bibi#142 als den gefaehrlicheren beschreibt: die Umschaltung *wirkt*,
    nur am falschen Knoten, und das ist schwerer zu bemerken als ein Knopf, der
    gar nichts tut."""
    from fastapi.testclient import TestClient

    from bibi.controller.client import ControllerClient
    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    gerufen: list[tuple[str, str, str]] = []

    def _fake_request(self, method, pfad, **kw):
        gerufen.append((self.base, method, pfad))
        return {"maintenance": method == "POST"}

    monkeypatch.setattr(ControllerClient, "_request", _fake_request)
    monkeypatch.setenv("BIBI_SCHEDULER_URL", "http://scheduler.example:8780")

    class _Fake:
        def status(self):
            return {"roles": ["controller", "connect"],
                    "connect": {"ok": True, "last_at": 1.0}}

    app = create_app(roles.resolve({"controller"}), controller_client=_Fake())
    with TestClient(app) as c:
        antwort = c.post("/-/ui/ops/maintenance")

    assert antwort.status_code == 200, antwort.text
    assert gerufen, "die Route hat gar nichts weitergeleitet"
    ziel, methode, pfad = gerufen[-1]
    assert ziel == "http://scheduler.example:8780", f"lokal geschaltet statt beim Host: {ziel}"
    assert (methode, pfad) == ("POST", "/-/maintenance")


def test_without_a_scheduler_the_switch_stays_local(team_repo, monkeypatch):
    """Der Bootstrap-Knoten: dort *ist* dieser Daemon der Scheduler, und der
    lokale Aufruf war nie falsch."""
    from fastapi.testclient import TestClient

    from bibi.controller.client import ControllerClient
    from bibi.daemon import roles
    from bibi.daemon.app import create_app

    gerufen: list[str] = []

    def _fake_request(self, method, pfad, **kw):
        gerufen.append(self.base)
        return {"maintenance": True}

    monkeypatch.setattr(ControllerClient, "_request", _fake_request)
    monkeypatch.delenv("BIBI_SCHEDULER_URL", raising=False)

    class _Fake:
        def status(self):
            return {"roles": ["scheduler", "controller"]}

    app = create_app(roles.resolve({"controller"}), controller_client=_Fake())
    with TestClient(app) as c:
        c.post("/-/ui/ops/maintenance")

    assert gerufen and "127.0.0.1" in gerufen[-1], gerufen
