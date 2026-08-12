"""Die beiden Attributseiten sagen dasselbe über dieselbe Frage (#141, #132).

Sie beantworten verwandte Fragen — *„wie ist dieser Job konfiguriert"* und
*„womit lief dieser Lauf"* — und liefen dabei auseinander:

**#141: die Job-Seite verschwieg die Hälfte.** Ihr Docstring versprach *„alle
Konfigurationswerte"*, ihre Feldliste trug nur den Zeitplan. Es fehlten unter
anderem ``slug`` und ``schedule_ref`` — und der Slug ist die Identität des Jobs
und die einzige Angabe, die man sonst nirgends verlässlich abliest: in der
Tabelle steht er gekürzt, die URL trägt ``md5(slug)``. Wer ihn sicher wissen
wollte, musste die Markdown-Datei öffnen. Der ältere Renderer zeigte ihn längst;
es war ein Rückschritt, kein Auslassen.

**#132: die Lauf-Seite unterschlug zwei Unterscheidungen**, die die Job-Seite
schon macht. Ein Vorgabewert sah aus wie eine Entscheidung, und Felder, die für
den Typ des Laufs nichts bedeuten — das Modell eines Shell-Jobs —, standen
mitten in der Tabelle. Die Spalte ``SOURCE`` beantwortet eine **andere** Frage
(Lauf gegen Job) als *gesetzt gegen Vorgabe*; beide gehören nebeneinander, nicht
ineinander.

**Und der Vorbehalt der Lauf-Seite fällt** (Rest aus #129): Er stand dort, weil
``journal.snapshot`` beim **Archivieren** entstand und ein terminaler Lauf unter
Regel A2 beliebig lange im Slot wartet. Seit die Lauf-Attribute bei START
einfrieren, ist ``run`` wieder eine Aussage über den Lauf und braucht keine
Einschränkung mehr.
"""

from __future__ import annotations

import json

from bibi.controller import render

NOW = 1_000_000.0
VORGABE = {"attempts": 1, "backoff": "fixed", "silence_timeout": 3600}


def _job_seite(spec: dict, defaults: dict | None = None) -> str:
    return render.job_attrs_page_v5(
        slug=spec.get("slug", "x"), spec=spec,
        defaults=VORGABE if defaults is None else defaults, now=NOW)


def _lauf_seite(snapshot: dict, job_spec: dict | None = None) -> str:
    return render.run_attrs_page_v5(
        slug=snapshot.get("slug", "x"),
        lauf={"run_id": "x:0", "status": "complete", "domain": "scheduled",
              "snapshot": json.dumps(snapshot)},
        job_spec=job_spec if job_spec is not None else dict(snapshot),
        defaults=VORGABE, now=NOW)


# ── #141: alle Konfigurationswerte heißt alle ────────────────────────────────


def test_the_job_page_shows_the_slug_and_where_it_comes_from():
    html = _job_seite({"slug": "calendar-transfer", "payload": "echo hi",
                       "schedule_ref": "calendar/calendar-transfer.md",
                       "schedule": "0 * * * *"})
    assert "calendar-transfer" in html
    assert "calendar/calendar-transfer.md" in html


def test_the_job_page_shows_the_remaining_configuration_fields():
    html = _job_seite({"slug": "x", "payload": "claude: tu was", "kind": "job",
                       "model": "claude-opus-5", "soul": "Data", "priority": 5,
                       "exec_mode": "container", "image": "eigenes:latest",
                       "schedule": "0 * * * *"})
    for wert in ("claude-opus-5", "Data", "container", "eigenes:latest"):
        assert wert in html, wert


def test_the_job_page_leaves_out_what_the_job_never_set():
    """Gegenprobe: ein nicht gesetztes Feld erzeugt keine leere Zeile — sonst
    entstünde neben *gesetzt* und *geerbt* eine dritte, bedeutungslose
    Kategorie."""
    html = _job_seite({"slug": "x", "payload": "echo hi", "schedule": "0 * * * *"})
    assert "image" not in html


# ── #132: Vorgabe von Entscheidung unterscheiden ─────────────────────────────


def test_the_run_page_marks_an_inherited_value_as_inherited():
    """Zwei Signale wie auf der Job-Seite: Klammern **und** Dimmung. Ein Signal
    allein geht in hellen Themes verloren."""
    html = _lauf_seite({"slug": "x", "payload": "echo hi", "kind": "job",
                        "attempts": 9, "silence_timeout": 3600})
    assert "(3600)" in html      # geerbt — steht in Klammern
    assert "(9)" not in html     # gesetzt — steht normal


def test_the_run_page_omits_fields_that_mean_nothing_for_this_run():
    """`EngineCI` ist ein Shell-Job. Das Modell kommt aus der Job-Zeile und
    spielt für diesen Lauf keine Rolle — für die Frage, die die Seite
    beantwortet, ist es Rauschen."""
    html = _lauf_seite({"slug": "x", "payload": "bash engine-ci.sh", "kind": "job",
                        "model": "claude-sonnet-4-6", "attempts": 9})
    assert "claude-sonnet-4-6" not in html


def test_the_run_page_keeps_the_model_where_it_decides_something():
    """Gegenprobe: bei einem Claude-Job erklärt das Modell zwei Läufe, die
    verschieden ausgehen, oft allein — es bleibt."""
    html = _lauf_seite({"slug": "x", "payload": "claude: tu was", "kind": "job",
                        "model": "claude-sonnet-4-6", "attempts": 9})
    assert "claude-sonnet-4-6" in html


# ── Rest aus #129: der Vorbehalt ist gegenstandslos ──────────────────────────


def test_the_run_page_no_longer_hedges_about_the_snapshot():
    html = _lauf_seite({"slug": "x", "payload": "echo hi", "kind": "job",
                        "attempts": 9})
    assert "frozen when the run is archived" not in html
