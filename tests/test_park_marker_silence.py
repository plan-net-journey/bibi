"""m.rau/bibi#139 — eine Park-Marke, die nicht entsteht, sagt es.

Zwei Hälften desselben Themas, und sie wollen zusammen gebaut werden: die eine
macht eine **Abwesenheit** hörbar, die andere entfernt ein **Rauschen**, in dem
jede echte Meldung untergeht.

- ``state.set_path()`` gibt ohne Session-ID stumm auf (``if pf is None:
  return``). ``bibi-ctrl open`` meldet trotzdem „reaktiviert: …" und beendet
  mit 0 — der Case *ist* offen, nur nicht geparkt. Am 2026-08-05 blieb das
  acht Stunden und achtzig Kommandos lang unbemerkt; was es schließlich zeigte,
  war ein Nebeneffekt in ``status``, nicht die fehlende Marke selbst.
- ``foreign_parks()`` meldet Marken **anderer** Sessions auf **denselben** Case,
  in dem man gerade arbeitet — die Spur der eigenen Vorgänger, geführt als
  Warnung. Der Docstring von ``foreign_parks()`` sagt diese Folge selbst voraus.

Braucht kein Git-Repo, daher nicht ``slow``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bibi import repo, state
from bibi.ctrl import open_cmd, status_cmd


@pytest.fixture
def ohne_session(monkeypatch):
    """Eine Sitzung ohne jede Session-ID — der Fall aus dem Ticket."""
    monkeypatch.delenv("BIBI_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(state, "_adopted_session", None, raising=False)


def test_open_meldet_wenn_die_marke_nicht_entsteht(team_repo, ohne_session, capsys) -> None:
    """Der Kern des Tickets: Exit bleibt 0, aber es bleibt nicht still."""
    rc = open_cmd.run(argparse.Namespace(topic="Testthema", force=False))

    assert rc == 0, "der Case ist offen — nur nicht geparkt; das ist kein Fehler"
    ausgabe = capsys.readouterr()
    gesamt = ausgabe.out + ausgabe.err
    assert "nicht geparkt" in gesamt or "Session-ID" in gesamt, \
        "eine Marke, die nicht entsteht, muss es sagen"


def test_status_fuehrt_die_fehlende_session_id_als_eigene_zeile(team_repo, ohne_session,
                                                                capsys) -> None:
    """Nicht als *Fehlen* von ``(session)`` — das sieht aus wie „nie geparkt"."""
    status_cmd.run(argparse.Namespace())

    out = capsys.readouterr().out
    assert "session_id:" in out, \
        "die fehlende Session-ID braucht eine eigene, benannte Zeile"


def test_foreign_parks_zaehlt_den_aktiven_case_nicht(team_repo, monkeypatch) -> None:
    """Die zweite Hälfte: die eigene Spur ist keine fremde Warnung.

    Aufbau wie im Betrieb: derselbe Case, mehrere Marken aus früheren
    Sitzungen — genau das, was jede Wiederverbindung hinterlässt."""
    rel = "case/20260806.Test-abc123"
    (repo.vault() / rel).mkdir(parents=True)
    park = repo.data() / "park"
    park.mkdir(parents=True, exist_ok=True)
    for sid in ("alte-session-1", "alte-session-2"):
        (park / sid).write_text(rel, encoding="utf-8")

    monkeypatch.setenv("BIBI_SESSION_ID", "die-jetzige")
    monkeypatch.setattr(state, "_adopted_session", None, raising=False)
    state.set_path(rel)

    assert rel not in state.foreign_parks(), \
        "Marken auf den aktiven Case sind die eigene Spur, keine fremde Warnung"


def test_foreign_parks_meldet_einen_anderen_case_weiterhin(team_repo, monkeypatch) -> None:
    """Gegenprobe: der Fall, für den ``#97`` die Meldung gebaut hat, bleibt.

    Ohne diese Hälfte könnte der Fix ``foreign_parks()`` komplett stilllegen
    und der Test oben wäre trotzdem grün."""
    aktiv = "case/20260806.Aktiv-aaa111"
    fremd = "case/20260806.Fremd-bbb222"
    for rel in (aktiv, fremd):
        (repo.vault() / rel).mkdir(parents=True)
    park = repo.data() / "park"
    park.mkdir(parents=True, exist_ok=True)
    (park / "andere-session").write_text(fremd, encoding="utf-8")

    monkeypatch.setenv("BIBI_SESSION_ID", "die-jetzige")
    monkeypatch.setattr(state, "_adopted_session", None, raising=False)
    state.set_path(aktiv)

    assert state.foreign_parks() == {fremd: 1}
