"""``bibi-ctrl soul --hook`` — die Soul wird wirksam (#75 Teil B).

**Teil A hat den Zustand sichtbar gemacht, nicht wirksam**, und das war
ausdrücklich die halbe Antwort: eine Sitzung ohne expliziten `/soul` lief
weiter ohne Persona. Der Befund von m.rau dazu — *„Sie vermittelt den
Eindruck, als würde die Soul gar nicht richtig greifen, weil gar nix im
Kontext ist außer die Information."*

**Entscheidung m.rau, 2026-08-11: Variante 3** — der `SessionStart`-Hook
injiziert die Soul. Sie gewann, weil sie als einzige keinen Bruch kauft: sie
*stellt wieder her*, statt zu *ersetzen*. `/soul` mitten in der Sitzung bleibt
sofort wirksam, und weil `SessionStart` auch bei `compact` feuert, schließt
sich die Kompaktierungslücke ohne Zutun des Modells — der eigentliche Anlass
des Befunds.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from bibi import state
from bibi.ctrl import main

TEXT = "# Data\n\nDu bist Data. Präzise, freundlich, ohne Floskeln.\n"


def _write_soul(root: Path, filename: str, text: str = TEXT) -> None:
    d = root / ".claude" / "souls"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(text, encoding="utf-8")


def _hook(monkeypatch, ereignis: str = "SessionStart", quelle: str = "startup"):
    """Der Hook-Aufruf, wie Claude Code ihn macht: Eingabe als JSON auf stdin.

    **Ein Flag, kein Unterkommando** — anders als bei `sync hook-start`. Der
    Grund steht im ersten Rot-Lauf: `soul` trägt ein optionales Positional,
    und `soul hook-start` landete darin. Die Meldung lautete *„unbekannte
    Soul: hook-start"*. Ein Unterkommando hätte damit einen Namen belegt, den
    eine Persona tragen könnte — genau die Doppelbelegung, die dieses Repo
    sonst überall auflöst.
    """
    eingabe = json.dumps({"hook_event_name": ereignis, "source": quelle,
                          "session_id": "s1", "cwd": "."})
    monkeypatch.setattr("sys.stdin", io.StringIO(eingabe))
    return main(["soul", "--hook"])


def test_a_session_with_a_soul_gets_it_injected(team_repo: Path, capsys, monkeypatch):
    """Der Kern von Teil B: die Sitzung startet **mit** der Persona im Kontext,
    ohne dass jemand `/soul` tippt."""
    _write_soul(team_repo, "12.Data.SOUL.md")
    state.set_soul("Data")
    assert _hook(monkeypatch) == 0
    aus = json.loads(capsys.readouterr().out)
    spezifisch = aus["hookSpecificOutput"]
    assert spezifisch["hookEventName"] == "SessionStart"
    assert "Du bist Data." in spezifisch["additionalContext"]


def test_without_a_soul_nothing_conspicuous_happens(team_repo: Path, capsys,
                                                    monkeypatch):
    """*„Es sollte immer eine Soul aktiv sein. Ansonsten neutral, d.h. der Weg
    ohne weiteren Input zur Soul."* — kein Fehler, keine Meldung, kein
    Kontext."""
    assert _hook(monkeypatch) == 0
    assert capsys.readouterr().out.strip() == ""


def test_a_soul_whose_file_is_gone_stays_silent(team_repo: Path, capsys, monkeypatch):
    """Ein Zustand, der auf eine gelöschte Datei zeigt, darf keine Sitzung
    kosten. Der Hook läuft **vor** dem ersten Prompt; wer hier scheitert,
    scheitert an einer Stelle, an der noch niemand etwas tun konnte."""
    state.set_soul("Verschwunden")
    assert _hook(monkeypatch) == 0
    assert capsys.readouterr().out.strip() == ""


def test_the_compaction_gap_closes_without_the_model_doing_anything(
        team_repo: Path, capsys, monkeypatch):
    """**Der Fall, der die Variante gewonnen hat.** `SessionStart` feuert auch
    bei `compact` — die Persona kommt danach von selbst zurück, statt dass
    jemand sie vermisst und nachlädt."""
    _write_soul(team_repo, "12.Data.SOUL.md")
    state.set_soul("Data")
    assert _hook(monkeypatch, quelle="compact") == 0
    aus = json.loads(capsys.readouterr().out)
    assert "Du bist Data." in aus["hookSpecificOutput"]["additionalContext"]


def test_a_subagent_carries_the_soul_too(team_repo: Path, capsys, monkeypatch):
    """*„Gilt die Soul auch für Subagenten? — ich sage JA."*

    Strukturell über `SubagentStart`, nicht als Bitte an das Modell, sie
    weiterzureichen: ein Subagent, der die Persona nur bekommt, wenn jemand
    daran denkt, bekommt sie irgendwann nicht.

    Das Ereignis kommt aus der Eingabe und wird zurückgegeben — dasselbe
    Kommando bedient beide Registrierungen. Ein fest verdrahteter Name wäre im
    Subagenten der falsche und würde still verworfen."""
    _write_soul(team_repo, "12.Data.SOUL.md")
    state.set_soul("Data")
    assert _hook(monkeypatch, ereignis="SubagentStart") == 0
    aus = json.loads(capsys.readouterr().out)
    assert aus["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert "Du bist Data." in aus["hookSpecificOutput"]["additionalContext"]


def test_a_broken_stdin_does_not_cost_the_session(team_repo: Path, capsys,
                                                  monkeypatch):
    """Ohne lesbare Eingabe bleibt es beim Vorgabe-Ereignis statt beim
    Abbruch — derselbe Grundsatz wie oben: der Hook läuft vor dem ersten
    Prompt."""
    _write_soul(team_repo, "12.Data.SOUL.md")
    state.set_soul("Data")
    monkeypatch.setattr("sys.stdin", io.StringIO("kein json"))
    assert main(["soul", "--hook"]) == 0
    aus = json.loads(capsys.readouterr().out)
    assert aus["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_showing_the_soul_still_works(team_repo: Path, capsys, monkeypatch):
    """Die Gegenprobe: das Flag darf `soul` und `soul <name>` nicht
    verschlucken."""
    _write_soul(team_repo, "12.Data.SOUL.md")
    assert main(["soul", "Data"]) == 0
    assert capsys.readouterr().out.strip() == "Data"
    assert main(["soul"]) == 0
    assert capsys.readouterr().out.strip() == "Data"
