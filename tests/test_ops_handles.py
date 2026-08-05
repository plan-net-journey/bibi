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
    html = render._ops_handles(CLIENT)
    assert html.index("⟳") < html.index("◐") < html.index("●")


def test_maintenance_uses_the_half_moon():
    """`◐` statt `⚙`/`⚠`: ein halb gefüllter Kreis ist ein Zustand zwischen an
    und aus — genau das ist Maintenance. Ein Zahnrad ist eine Einstellung, ein
    Warndreieck ein Fehler; beides trifft es nicht."""
    html = render._ops_handles(CLIENT)
    assert "◐" in html
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
    """Sonst ist „ging nicht" von „ging" nicht zu unterscheiden."""
    kopf = render._OPS_HANDLES_JS
    kopf = kopf[kopf.index("const rescan"):kopf.index("const maint")]
    assert "✕" in kopf or "!" in kopf
