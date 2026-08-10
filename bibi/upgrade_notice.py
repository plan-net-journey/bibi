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
        # **``st["running"]`` trägt das seit m.rau/bibi#125.** Bis dahin las
        # diese Funktion die Portdatei ein drittes Mal selbst — der Wert stand
        # oben ohnehin schon im ``entry``, und die Fallunterscheidung „mit oder
        # ohne abgelegten Startstand" war hier nachgebaut. Sie ist entfallen,
        # ohne das Urteil zu ändern: fehlt der Startstand, ist ``running``
        # das venv, und das war vorher der zweite Zweig.
        #
        # **Nur dort, wo ein Tag-Vergleich überhaupt trägt.** ``update_status()``
        # fällt vorher schon Urteile, die nichts mit dem Rückstand zu tun haben:
        # ``editable`` ist eine Absicht, ``branch`` und ``unknown`` sind
        # Datenlücken, ``local`` ist eine Kopie. In all diesen Fällen bleibt es
        # still, und die laufende Version ändert daran nichts — sonst bekäme ein
        # Arbeits-Checkout eine Aufforderung, die er nie einlösen kann.
        if st.get("verdict") not in ("current", "outdated"):
            return None
        erwartet, laufend = st.get("expected"), st.get("running")
        if not erwartet or not laufend:
            return None
        if deploy._norm(erwartet) == deploy._norm(laufend):
            return None
        return {"expected": erwartet, "running": laufend}
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
