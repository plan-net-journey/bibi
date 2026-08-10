"""`doctor` findet Doku, die etwas erklärt, das der Code nicht mehr kennt (`#92`).

**Der Befund ist nicht eine Altlast, sondern wie Altlasten hier behoben
werden:** dort, wo jemand stolpert, nicht dort, wo sie stehen. `#58` reparierte
den `bibi-setup`-Skill, der `BIBI_WORKER_NAME` empfahl — dieselbe Empfehlung
stand zwei Tage später weiter in vier `INSTALL.md`-Stellen, weil der Befund aus
einem Setup-Lauf kam und nicht aus einer Suche.

**Zwei Entscheidungen trennen Nutzen von Rauschen, und beide werden hier
festgehalten.**

*Wo geprüft wird:* aktive Doku. **Nicht** `vault/case/**` und `vault/memo/**` —
dort sind alte Namen Aufzeichnung und gehören hin. Eine Prüfung, die sie
anmahnt, wird nach dem dritten Lauf ignoriert, und dann auch dort, wo sie
recht hat.

*Wie eine bewusste Erwähnung erkannt wird:* ein Treffer entfällt, wenn in
derselben Zeile ein Rückblick-Marker oder der Nachfolger steht. Die beiden
echten Vorbilder stehen im `bibi-setup`-Skill und müssen still bleiben — sonst
meldete die Prüfung ihre eigenen Korrekturen als Fehler.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bibi import hygiene

_TERMS = [
    {"term": "BIBI_CONFIG_PATH", "since": "v0.7.3", "replacement": "config.env_path()"},
    {"term": "BIBI_WORKER_NAME", "since": "PLAN-34", "replacement": "BIBI_NODE_NAME"},
]


def test_a_retired_name_in_active_doc_is_a_finding():
    out = hygiene.check_retired_terms(
        "INSTALL.md", "Setze `BIBI_WORKER_NAME` auf den Knotennamen.\n", _TERMS)
    assert len(out) == 1
    f = out[0]
    assert f.kind == "retired-term" and f.path == "INSTALL.md:1"
    assert "BIBI_NODE_NAME" in f.detail, (
        "ein Befund ohne Nachfolger verschiebt die Arbeit nur zum Leser — er "
        "muesste erst herausfinden, wie es heute heisst")


@pytest.mark.parametrize("zeile", [
    # Die beiden echten Stellen aus skills/bibi-setup/SKILL.md, wortgleich.
    "a backup, and this skill explained an environment variable (`BIBI_CONFIG_PATH`)",
    "`BIBI_CONFIG_PATH`, which no longer exists, and `BIBI_WORKER_NAME`, which was",
    # Deutsche Entsprechungen — der Vault ist deutsch, die Skills sind englisch.
    "`BIBI_CONFIG_PATH` gibt es seit v0.7.3 nicht mehr.",
    "Frueher hiess das `BIBI_WORKER_NAME`.",
])
def test_a_deliberate_look_back_stays_quiet(zeile):
    """**Ohne diese Regel meldet die Pruefung ihre eigenen Korrekturen.**"""
    assert hygiene.check_retired_terms("skills/x/SKILL.md", zeile + "\n", _TERMS) == []


def test_naming_the_successor_in_the_same_line_stays_quiet():
    """Wer beide Namen nennt, erklärt den Übergang — das ist kein Fund."""
    assert hygiene.check_retired_terms(
        "INSTALL.md", "`BIBI_WORKER_NAME` → `BIBI_NODE_NAME`\n", _TERMS) == []


def test_the_check_is_line_precise():
    text = "ok\nnoch ok\nSetze `BIBI_CONFIG_PATH`.\n"
    out = hygiene.check_retired_terms("JOBS.md", text, _TERMS)
    assert [f.path for f in out] == ["JOBS.md:3"]


# ── Wo geprüft wird ─────────────────────────────────────────────────────────


def test_case_and_memo_are_not_scanned(team_repo: Path):
    """`vault/case/**` und `vault/memo/**` bleiben unberührt — **Aufzeichnung
    ist kein Fehler.** Ein Release-Memo, das schreibt „damals hiess es X", ist
    genau richtig und darf nie anschlagen.
    """
    from bibi.ctrl import doctor_cmd

    (team_repo / "vault" / "memo").mkdir(parents=True, exist_ok=True)
    (team_repo / "vault" / "memo" / "alt.md").write_text(
        "Damals: BIBI_WORKER_NAME\n", encoding="utf-8")
    (team_repo / "vault" / "case").mkdir(parents=True, exist_ok=True)
    (team_repo / "vault" / "case" / "alt.md").write_text(
        "Damals: BIBI_WORKER_NAME\n", encoding="utf-8")
    (team_repo / "INSTALL.md").write_text(
        "Setze BIBI_WORKER_NAME.\n", encoding="utf-8")

    orte = [f.path for f in doctor_cmd._retired_term_findings(team_repo, _TERMS)]
    assert orte == ["INSTALL.md:1"], (
        "geprueft wird aktive Doku, nicht das Archiv — sonst wird die Pruefung "
        "nach dem dritten Lauf ueberlesen")


def test_the_real_skill_tree_is_quiet():
    """**Am echten Bestand geeicht, nicht an einem Fixture.**

    Der `bibi-setup`-Skill nennt `BIBI_CONFIG_PATH` zweimal — beide Male als
    Rueckblick. Schlaegt die Pruefung hier an, ist sie falsch kalibriert, und
    das faellt sonst erst auf, wenn jemand sie im Alltag abschaltet.
    """
    from bibi.ctrl import doctor_cmd

    wurzel = Path(hygiene.__file__).resolve().parent.parent
    out = doctor_cmd._retired_term_findings(wurzel, _TERMS)
    assert out == [], (
        "die Pruefung meldet ihre eigenen, korrekt formulierten Rueckblicke: "
        + ", ".join(f"{f.path}" for f in out))
