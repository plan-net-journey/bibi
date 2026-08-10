"""Upgrade-Aufforderung für Sitzungs-Knoten (m.rau/bibi#94).

Für einen Knoten **mit** Supervisor ist ein Neustart ein Vorgang an der
Maschine: ``/-/restart`` beendet den Prozess, systemd oder launchd bringt ihn
zurück. Für einen **Sitzungs-Knoten** gibt es diesen Weg nicht — er läuft unter
``bibi`` im Terminal eines Menschen, ohne Supervisor. Ein Neustart ist dort eine
Aufforderung an den Menschen, kein Befehl an ein System.

Damit endet der Deploy-Weg für diesen Knoten bei ihm. Sagt ihm niemand, dass
ein Upgrade bereitliegt, bleibt er beliebig lange auf dem alten Stand;
``release.sh`` verbucht ihn heute mit *„zieht beim nächsten Start nach"*, ohne
dass jemand weiß, wann dieser Start kommt.

**Neu ist hier nur die Aufforderung, nicht das Urteil.** Ob ein Knoten hinter
seinem Soll steht, beantwortet ``deploy.update_status()`` seit m.rau/bibi#43 —
rein lokal, aus ``pyproject.toml`` (Soll) und ``direct_url.json`` (Ist). Dieses
Modul fügt zwei Dinge hinzu: die Abgrenzung auf Sitzungs-Knoten und eine Form,
die man nicht übersieht.

**Im Zweifel still.** Jeder unklare Fall — kein laufender Daemon, ein Daemon
ohne ``session``-Feld, ein Branch-Pin, ein editable install — führt zu ``None``.
Dieselbe Zurückhaltung wie in ``deploy.label_is_outdated()``: eine falsche
Aufforderung schickt jemanden los, etwas neu zu starten, das in Ordnung ist,
und der zweite falsche Alarm kostet die Meldung ihre Wirkung.
"""

from __future__ import annotations

from pathlib import Path

#: Was die Statusleiste kostet. Sie trägt schon Branch, Modell, ctx%, Case und
#: den Sync-Zustand; eine Aufforderung, die sie sprengt, verdrängt genau die
#: Information, neben der sie stehen soll.
_SEG_MAX = 28


def pending(root: Path | None = None, info=None) -> dict | None:
    """Wartet auf diesem Knoten ein Upgrade, das nur ein Mensch einlösen kann?

    ``None``, wenn nichts zu melden ist. Sonst ``{"expected", "running"}`` —
    dieselben zwei Felder, die ``deploy.update_status()`` liefert, auf die
    beiden reduziert, die in einer Aufforderung vorkommen.

    Die Herkunft des Daemons kommt aus der Portdatei und ist ein **abgelegter
    Wert**, keine Heuristik: nur der startende Prozess weiß sicher, ob er einer
    Sitzung gehört (``portfile.write(session=…)``). ``is True`` statt
    ``truthy``, weil ein fehlendes Feld „vor #59 gestartet" heißt und nicht
    „Sitzung" — unbekannt ist kein Ja.
    """
    try:
        from bibi.daemon import deploy, portfile
        entry = portfile.read(root)
        if entry is None or entry.get("session") is not True:
            return None
        st = deploy.update_status(root, info)
        # **Verglichen wird gegen den laufenden Prozess, nicht gegen das venv**
        # (m.rau/bibi#81). Der Stand auf der Platte ist nach einem ``uv sync``
        # aktuell, während der Daemon noch den alten Code fährt — und genau
        # dann erlosch die Aufforderung, obwohl sie da am dringendsten war.
        # Live am 2026-08-08: der Knoten baute weiter 15 Verbindungen pro
        # Minute auf, das Muster des in ``v0.7.7`` behobenen Fehlers, während
        # alle drei Anzeigen übereinstimmend ``current`` meldeten.
        #
        # **Die Bedingung ist auf ``needs_update`` zusammengeschmolzen**
        # (m.rau/bibi#126). Hier stand dreimal nacheinander eine eigene
        # Fallunterscheidung: erst die Portdatei ein drittes Mal gelesen (bis
        # #125), dann die Verdict-Namen aufgezählt und ``expected`` gegen
        # ``running`` verglichen. **Beides war eine zweite Fassung derselben
        # Regel** — und die erste Fassung urteilte über die Platte, während
        # diese hier den Prozess las. Genau daran ist #126 entstanden: die
        # Statusleiste forderte zum Neustart auf, der Screen daneben schwieg.
        #
        # ``needs_update`` sagt jetzt für beide dasselbe: *der laufende Stand
        # ist nicht der erwartete.* Ob dafür ein Neustart genügt
        # (``restart pending``) oder erst der Sync durchmuss (``behind``),
        # steht im Verdict — für die Aufforderung an einen Menschen ist es
        # dasselbe, er tippt ohnehin ``exit`` und ``bibi``.
        if not st.get("needs_update"):
            return None
        return {"expected": st.get("expected"), "running": st.get("running")}
    except Exception:  # noqa: BLE001
        # Die Aufforderung hängt im Sitzungsstart und in der Statusleiste.
        # Beide dürfen an ihr nicht scheitern — dieselbe Regel wie dort.
        return None


def banner(st: dict) -> str:
    """Die Startmeldung: mehrzeilig, abgesetzt, mit dem Weg zurück.

    Eine einzelne Zeile zwischen den übrigen ``bibi:``-Meldungen wäre genau das
    Einreihen, das #94 ausschließt — m.rau: *„diesen Zustand könnte man doch
    HART ÜBER ALLES legen, weil wir ein Upgrade fordern!"*

    Der Weg steht drin, nicht nur der Zustand. Ein Hinweis, der eine Lage
    meldet und offenlässt, was zu tun ist, verschiebt die Arbeit nur zu dem,
    der ihn liest.
    """
    exp, run = st.get("expected") or "?", st.get("running") or "?"
    line = f"UPGRADE WARTET — {run} läuft, {exp} ist gepinnt"
    rule = "─" * len(line)
    return ("\n"
            f"  \033[1;33m{rule}\033[0m\n"
            f"  \033[1;33m{line}\033[0m\n"
            f"  \033[1;33m{rule}\033[0m\n"
            "  Dieser Knoten läuft in deiner Sitzung — ohne Supervisor startet\n"
            "  ihn niemand für dich neu.\n"
            "\n"
            "      \033[1mexit\033[0m, dann \033[1mbibi\033[0m\n")


def segment(st: dict) -> str:
    """Das Statusleisten-Segment: invers, rot, und es steht **vorn**.

    „Hart über alles" heißt hier Vorrang vor jedem anderen Segment, nicht eine
    zweite Zeile: die Leiste ist eine. Voranstellen statt Einreihen erhält
    Branch, Modell und Case — sie ersatzlos zu verdrängen hieße, den Nutzer
    für die Dauer eines wartenden Upgrades blind zu machen.
    """
    exp = st.get("expected") or "?"
    text = f"UPGRADE {exp}"
    hint = "exit+bibi"
    if len(text) + len(hint) + 3 > _SEG_MAX:
        # Lieber die Handlungsanweisung kürzen als die Leiste sprengen; die
        # Startmeldung trägt sie ohnehin ausführlich.
        hint = ""
    tail = f"\033[31m {hint}\033[0m" if hint else ""
    return f"\033[7m\033[31m {text} \033[0m{tail}"
