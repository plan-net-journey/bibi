"""``bibi-ctrl upgrade`` — die Soll-Version setzen (m.rau/bibi#155).

**Was Handarbeit war, ist nur das Setzen der Zahl.** Das Soll steht in
``pyproject.toml`` des Team-Repos, das Ist in der ``direct_url.json`` des venv,
und ``update_status()`` vergleicht beide rein lokal — der Knoten holt sich den
Soll-Stand selbst. Seit `#103` gibt es deshalb keine ``Restart``-Spalte mehr.

**Warum die Zahl trotzdem bleibt: sie trägt den Rückweg.** ``Iterationen.md``
führt ihn als harte Abbruchgrenze — *„Innerhalb einer Minor-Reihe ist ein
Rollback ein Pin"*. Ein Knoten, der von sich aus auf das letzte Release ginge,
hätte diesen Rückweg nicht: er holte sich die kaputte Version binnen 180 s
wieder. **Ein automatisches „immer das letzte" tauscht eine wiederkehrende
Handbewegung gegen den Verlust der einzigen Notbremse** — und der Fall, in dem
sie gebraucht wird, ist derselbe, in dem niemand mehr eingreifen kann.

**Entscheidung m.rau, 2026-08-12: beide Repos folgen derselben Release-Linie.**
Damit ist die Frage beantwortet, die den Umfang schnitt — es braucht keinen
Mehr-Repo-Schalter, sondern ein Kommando, das man je Repo einmal aufruft.

Geprüft wird die **Fassade**, nicht ``set_expected_version()``: das Schreiben,
Locken und Pushen hat seine eigenen Tests und läuft hier gegen einen Doppel.
Was diese Datei prüft, ist die Übersetzung von Argumenten in einen Aufruf — und
genau dort liegen die zwei Zusagen, die leicht verlorengehen: der Rückfall auf
die höchste Version und das Branch-Pinning.
"""

from __future__ import annotations

import argparse

import pytest

from bibi.ctrl import upgrade_cmd


@pytest.fixture
def gerufen(monkeypatch):
    """Was ``set_expected_version()`` zu sehen bekam, statt es wirklich zu tun."""
    aufrufe: list[tuple] = []

    def fake(ref, root=None, *, push=True):
        aufrufe.append((ref, push))
        return {"ok": True, "changed": True, "ref": ref, "was": "v0.0.1",
                "committed": True, "pushed": push}

    monkeypatch.setattr(upgrade_cmd.deploy, "set_expected_version", fake)
    return aufrufe


def _args(**kw) -> argparse.Namespace:
    basis = {"ref": None, "no_push": False}
    return argparse.Namespace(**{**basis, **kw})


def test_ein_tag_wird_durchgereicht(gerufen):
    assert upgrade_cmd.run(_args(ref="v0.8.15")) == 0
    assert gerufen == [("v0.8.15", True)]


def test_ohne_argument_die_hoechste_verfuegbare(monkeypatch, gerufen):
    """*„Ohne Argument die höchste verfügbare Version aus ``available_refs()``."*

    ``available_refs()`` liefert neueste zuerst — der Rückfall ist deshalb das
    erste Element und keine eigene Sortierung. Eine zweite Sortierregel hier
    wäre die dritte Stelle, an der dieses Repo Versionen ordnet.
    """
    monkeypatch.setattr(upgrade_cmd.deploy, "available_refs",
                        lambda *a, **k: ["v0.8.15", "v0.8.14", "v0.8.0"])
    assert upgrade_cmd.run(_args()) == 0
    assert gerufen == [("v0.8.15", True)]


def test_ohne_argument_und_ohne_tags_bricht_ab(monkeypatch, gerufen):
    """Kein Rückfall auf irgendetwas. ``available_refs()`` gibt im Zweifel eine
    leere Liste zurück (kein Remote, keine Zugangsdaten) — daraus einen Ref zu
    raten hieße, eine Version zu pinnen, die niemand gewählt hat."""
    monkeypatch.setattr(upgrade_cmd.deploy, "available_refs", lambda *a, **k: [])
    assert upgrade_cmd.run(_args()) == 1
    assert gerufen == []


def test_ein_branch_bleibt_ein_gueltiger_wert(gerufen):
    """**Das Branch-Pinning muss erhalten bleiben** (#155, wörtlich).

    ``dev`` unterscheidet das Urteil ``branch`` von ``outdated``. Ein Kommando,
    das nur Tags annimmt, nimmt der Engine-Entwicklung ihr Werkzeug — und der
    Fehler wäre nicht sichtbar, weil alles andere weiter funktioniert.
    """
    assert upgrade_cmd.run(_args(ref="dev")) == 0
    assert gerufen == [("dev", True)]


def test_no_push_kommt_an(gerufen):
    """Der Rückweg im Rückweg: eine Version setzen, ohne sie zu verteilen."""
    assert upgrade_cmd.run(_args(ref="v0.8.15", no_push=True)) == 0
    assert gerufen == [("v0.8.15", False)]


def test_ein_fehlschlag_wird_zum_exitcode(monkeypatch):
    """Ohne diesen Test wäre das Kommando auch dann grün, wenn ``uv lock``
    fehlschlägt — und ein Rollout liefe gegen eine Version, die nie gesetzt
    wurde."""
    monkeypatch.setattr(
        upgrade_cmd.deploy, "set_expected_version",
        lambda *a, **k: {"ok": False, "error": "uv lock fehlgeschlagen für v9.9.9"})
    assert upgrade_cmd.run(_args(ref="v9.9.9")) == 1


def test_unveraendert_ist_kein_fehler(monkeypatch):
    """``set_expected_version()`` meldet ``ok`` und ``changed: False``, wenn der
    Ref schon steht. Das ist der Normalfall beim zweiten Aufruf und darf keinen
    Fehler ergeben — sonst kann man das Kommando nicht wiederholen."""
    monkeypatch.setattr(
        upgrade_cmd.deploy, "set_expected_version",
        lambda *a, **k: {"ok": True, "changed": False, "ref": "v0.8.15",
                         "note": "unverändert — schon auf diesem Ref"})
    assert upgrade_cmd.run(_args(ref="v0.8.15")) == 0


def test_das_kommando_ist_registriert():
    """Eine Funktion ohne Parser ist kein Kommando. Der Test steht hier, weil
    genau dieser Schritt beim Anlegen eines Subkommandos vergessen wird — und
    dann melden alle Tests darüber grün, während ``bibi-ctrl upgrade`` sagt,
    es kenne das Verb nicht."""
    from bibi import ctrl
    parser = argparse.ArgumentParser(prog="bibi-ctrl")
    sub = parser.add_subparsers(dest="cmd")
    upgrade_cmd.register(sub)
    args = parser.parse_args(["upgrade", "v0.8.15"])
    assert args.ref == "v0.8.15"
    assert args.func is upgrade_cmd.run
    assert "upgrade_cmd" in dir(ctrl), "in bibi/ctrl/__init__.py nicht importiert"
