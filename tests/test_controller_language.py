"""Das FE spricht Englisch, durchgängig (m.rau/bibi#37).

**Warum das ein Test ist und kein Augenschein.** Ein Durchgang durch 4.950
Zeilen findet die Strings, die man sucht; er findet nicht die, an die man nicht
gedacht hat. `#37` ist genau deshalb viermal liegengeblieben — jedes Mal war
`render.py` ohnehin offen, jedes Mal wurde „der Rest" auf später vertagt, und
niemand konnte sagen, wie groß der Rest war. Derselbe Dienst, den bei `#68` die
Aufzählung der 36 Farbwerte geleistet hat: die Vollständigkeit steht in der
Suite, nicht in der Sorgfalt des nächsten Durchgangs.

**Die Grenze verläuft an der Sichtbarkeit, nicht an der Datei.** Deutsch bleibt
richtig für den Code *um* die Strings herum — Kommentare, Bezeichner, Docstrings,
und auch die Kommentare *innerhalb* der ausgelieferten CSS- und JS-Blöcke. Sie
erklären den Code demselben Leser wie jeder andere Kommentar; dass sie im
Seitenquelltext landen, macht sie nicht zur Anzeige. Der Test schneidet sie
deshalb heraus, statt sie über eine Ausnahmeliste zu dulden — eine Liste wäre
bei jedem neuen Kommentar zu pflegen und würde beim ersten Vergessen falsch
Alarm schlagen.

Die Deutsch-Regel aus `CONVENTIONS.md` gilt für Vault und CLI und ist hiervon
unberührt (Klarstellung m.rau).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

#: Die Renderer, die HTML erzeugen. Der Zuschnitt stammt aus dem `v0.7.0`-Plan
#: („kein deutscher String mehr in controller/render.py und
#: controller/jobs_view.py").
FRONTEND_MODULES = ("render.py", "jobs_view.py")

#: Umlaute und ß — das Signal, das ohne Wortliste auskommt.
UMLAUT = re.compile(r"[äöüÄÖÜß]")

#: Wörter, die in einem englischen UI-Text nichts zu suchen haben. Bewusst ohne
#: die Fälle, die in beiden Sprachen vorkommen (``status``, ``trigger``, ``run``,
#: ``start``) — die sind Schlüssel oder Marken, keine Übersetzungslücken.
GERMAN_WORDS = re.compile(
    r"(?<![\w-])("
    r"der|die|das|den|dem|des|ein|eine|einen|einem|einer|kein|keine|keinen|"
    r"und|oder|nicht|noch|nur|auch|aber|sonst|schon|jetzt|hier|beim|vom|zum|zur|"
    r"ist|sind|war|waren|wird|werden|wurde|hat|haben|kann|muss|"
    r"laeuft|laufen|zeigt|steht|gibt|geht|bleibt|fehlt|verfuegbar|"
    r"letzter|letzte|letzten|naechster|naechste|naechsten|aktiver|aktive|"
    r"wartet|warten|beendet|gestartet|gestoppt|abgebrochen|fehlgeschlagen|verwirft|"
    r"Lauf|Laeufe|Laufzeit|Knoten|Auftrag|Auftraege|Fehler|Quelle|Zustand|Dauer|"
    r"Zeile|Spalte|oeffnen|schliessen|loeschen|starten|stoppen|zurueck|"
    r"Sitzung|Knopf|Kachel|Ansicht|Uebersicht|Einstellungen|Verbindung|unbekannt|"
    r"seit|ohne|fuer|Seite|Ziel|Anzahl|Grund|Zeit|"
    # Nachgetragen, nachdem sie beim ersten Durchgang durchgerutscht sind
    # (2026-08-05). Jede Lücke, die einmal auffällt, gehört in die Liste — sonst
    # ist die nächste Erhebung wieder nur so gut wie die Wortliste von damals.
    r"Eingabe|erforderlich|gesetzt|gepusht|verbunden|getrennt|Erwartete|erwartet|"
    r"Setzen|Ausrollen|Neustart|Freigabe|neu|geaendert|unveraendert|konfliktaer"
    r")(?![\w-])",
    re.IGNORECASE,
)

#: Ein reiner Bezeichner ist kein Anzeigetext — ``status``, ``job_uid``, ``typ``.
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

#: Kommentare in ausgeliefertem CSS/JS. ``//`` nur, wenn kein ``:`` davorsteht —
#: sonst fiele jede URL (``http://…``) dem Schnitt zum Opfer.
ASSET_COMMENT = re.compile(r"/\*.*?\*/|(?<!:)//[^\n]*", re.DOTALL)

#: Modulkonstanten, die ein komplettes Skript oder Stylesheet tragen —
#: ``_JOBS_JS``, ``_THEME_JS``, ``_CSS``. Für sie gilt eine engere Prüfung, s.
#: :func:`_asset_display_text`.
ASSET_CONST = re.compile(r"^_[A-Z0-9_]*(JS|CSS)$")

#: String-Literale **innerhalb** eines JS-Assets: das ist dort die Anzeige.
JS_LITERAL = re.compile(r"'([^'\\\n]*)'|\"([^\"\\\n]*)\"|`([^`\\]*)`", re.DOTALL)

#: ``${…}`` in einem Template-Literal ist Code, kein Text — dort stehen
#: Bezeichner wie ``ziel`` und ``SEITE``, die als Anzeige gelesen falsch
#: anschlagen würden.
JS_INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def _controller_dir() -> Path:
    import bibi.controller
    return Path(bibi.controller.__file__).parent


def _asset_display_text(script: str) -> str:
    """Was von einem ausgelieferten Skript **Anzeige** ist: seine String-Literale.

    Der Rest ist Code — ``const zeile``, ``const ziel``, ``const SEITE``. Deutsche
    Bezeichner sind im ganzen Repo üblich und bleiben es; sie wandern nur deshalb
    durch dieses Sieb, weil das Skript als Python-String transportiert wird.
    Ohne diese Trennung müsste die Prüfung entweder Bezeichner umbenennen, die
    niemand sieht, oder die drei größten JS-Blöcke pauschal ausnehmen — und dann
    prüfte sie genau dort nicht, wo Text an den Browser geht.

    **Die Reihenfolge ist nicht beliebig:** Kommentare fallen zuerst. Sonst zieht
    ein ``//``-Kommentar mit Apostroph zwei benachbarte Quotes zu einem
    scheinbaren Literal zusammen, und der Kommentartext steht als Anzeige da.
    """
    body = ASSET_COMMENT.sub(" ", script)
    text = " ".join(m.group(1) or m.group(2) or m.group(3) or ""
                    for m in JS_LITERAL.finditer(body))
    return JS_INTERPOLATION.sub(" ", text)


def _visible_strings(path: Path) -> list[tuple[int, str]]:
    """Alle String-Literale außer Docstrings, mit Zeilennummer.

    Über den AST statt per ``grep``: Kommentare und Docstrings fallen damit von
    selbst weg, statt über eine Regex ausgeschlossen werden zu müssen. Asset-
    Konstanten (``_JOBS_JS``, ``_CSS``) tragen ein ganzes Skript und werden auf
    ihre Anzeigetexte eingedampft, s. :func:`_asset_display_text`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    assets: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if (any(ASSET_CONST.match(n) for n in names)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                assets.add(id(node.value))
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            continue
        if id(n) in docstrings:
            continue
        out.append((n.lineno, _asset_display_text(n.value) if id(n) in assets else n.value))
    return out


def _german_hits(path: Path) -> list[tuple[int, str]]:
    hits = []
    for lineno, raw in _visible_strings(path):
        text = ASSET_COMMENT.sub(" ", raw)
        if len(text.strip()) < 3 or IDENTIFIER.match(text.strip()):
            continue
        if UMLAUT.search(text) or GERMAN_WORDS.search(text):
            hits.append((lineno, raw))
    return hits


@pytest.mark.parametrize("module", FRONTEND_MODULES)
def test_frontend_has_no_german_visible_strings(module: str):
    hits = _german_hits(_controller_dir() / module)
    report = "\n".join(f"  {module}:{ln}  {s[:110]!r}" for ln, s in sorted(hits))
    assert not hits, f"{len(hits)} deutsche Strings im FE:\n{report}"


def test_the_detector_would_notice_a_german_string(tmp_path: Path):
    """Der Wächter über dem Wächter.

    Ein Test, der nur „keine Treffer" behauptet, ist auch dann grün, wenn seine
    Erkennung gar nicht mehr greift — und je enger die Wortliste gefasst ist,
    desto leiser wäre dieser Ausfall. Hier steht deshalb ein Fall, der schlagen
    **muss**, und zwar über beide Wege: Umlaut und Wortliste.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        'def render():\n'
        '    """Ein deutscher Docstring bleibt erlaubt — er ist nicht sichtbar."""\n'
        '    # Ein deutscher Kommentar auch.\n'
        '    css = "/* Grundpalette: die Tokens fuer beide Modi */ .x { color: red; }"\n'
        '    a = "letzter Lauf"\n'
        '    b = "nächster Lauf"\n'
        '    return css + a + b\n',
        encoding="utf-8",
    )
    hits = {s for _, s in _german_hits(probe)}
    assert "letzter Lauf" in hits, "die Wortliste greift nicht mehr"
    assert "nächster Lauf" in hits, "die Umlaut-Erkennung greift nicht mehr"
    assert not any("Grundpalette" in s for s in hits), \
        "ein Kommentar im ausgelieferten CSS ist kein Anzeigetext"
