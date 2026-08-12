"""Der Slug kommt ausschließlich aus dem Dateistamm (#143).

**Eine Regel statt drei.** Bis `v0.8.2` entstand er in drei Stufen: ein
explizites ``slug:`` gewann, bei ``README.md``/``SCHEDULE.md`` galt der
Ordnername, sonst der Dateistamm. Künftig gilt nur noch der Dateistamm — damit
ist *„wie heißt dieser Job?"* ohne Nachschlagen beantwortet: man sieht die
Datei, man kennt den Slug.

**Beide Sonderregeln hatten null Nutzer**, und das ist am Bestand erhoben, nicht
angenommen. Der ``README.md``-Fall: 0 Schedule-MDs. Der Override: vier Fälle,
deren Vorarbeit am 2026-08-11 erledigt wurde (Dateien umbenannt, ``slug:``
entfernt). **Vor dem Bau erneut nachgezählt** (2026-08-12, 21 Schedule-MDs im
Vault): 0 verbliebene Overrides, 0 doppelte Dateistämme. Ohne diese zweite
Zählung wäre die Umstellung ein Risiko gewesen — sie hätte zwei MDs mit
demselben Stamm auf denselben Slug geworfen und, seit #142, beide blockiert.

## ⚠ ``slug:`` gibt es zweimal, mit verschiedener Bedeutung

Es trägt denselben Feldnamen in derselben Datei-Art, und wer das verwechselt,
macht aus 38 Cases Nicht-Cases:

| Ort | Bedeutung | Leser | hier |
|---|---|---|---|
| Schedule-MD | Job-Slug-Override | ``schedule/parser.py`` | **abgeschafft** |
| Case-``README.md`` | Case-Identität | ``case_store``, ``feed`` | **unangetastet** |

Die letzten beiden Tests sind genau dafür da. Sie sind vor und nach der
Umstellung grün — und wären sie es nicht, verschwänden Cases aus Feed, ``/open``
und der Case-Erkennung.
"""

from __future__ import annotations

from pathlib import Path

from bibi import case_store
from bibi.schedule import parser


def _parse(text: str, *, path: Path):
    return parser.parse_text(text, schedule_ref=path.as_posix(), path=path)


def test_a_slug_override_no_longer_wins():
    r = _parse('---\nslug: andersherum\nschedule: now\njob: "x"\n---\n',
               path=Path("vault/case/report.md"))
    assert r.is_ok, r.error
    assert r.spec.slug == "report"


def test_a_readme_schedule_takes_its_own_stem_not_the_folder():
    r = _parse('---\nschedule: now\njob: "x"\n---\n',
               path=Path("vault/case/hello/README.md"))
    assert r.is_ok, r.error
    assert r.spec.slug == "README"


def test_a_named_file_still_takes_its_stem():
    """Gegenprobe: die Regel, die bleibt, bleibt auch wirklich."""
    r = _parse('---\nschedule: now\njob: "x"\n---\n',
               path=Path("vault/case/daily.md"))
    assert r.is_ok, r.error
    assert r.spec.slug == "daily"


def test_two_files_with_the_same_stem_now_share_a_slug():
    """Die Kehrseite der einen Regel, ausdrücklich festgehalten: gleicher Stamm
    heißt gleicher Slug, auch aus verschiedenen Ordnern. Das ist kein Fehler,
    sondern der Grund, warum #142 vor diesem Ticket gebaut wurde — eine
    Kollision wird gemeldet und blockiert, statt still zu verschwinden."""
    a = _parse('---\nschedule: now\njob: "x"\n---\n', path=Path("vault/case/a/report.md"))
    b = _parse('---\nschedule: now\njob: "x"\n---\n', path=Path("vault/case/b/report.md"))
    assert a.spec.slug == b.spec.slug == "report"


# ── Die andere Bedeutung von `slug:` bleibt unangetastet ─────────────────────


def test_a_case_readme_is_not_a_schedule(tmp_path: Path):
    """Ein Case-README trägt ``slug:``, aber weder ``schedule:`` noch ``at:`` —
    es ist für den Schedule-Parser schlicht kein Kandidat und war es nie."""
    p = tmp_path / "README.md"
    p.write_text("---\nslug: Bibi4\nstatus: open\n---\n\n# Bibi4\n", encoding="utf-8")
    r = parser.parse_file(p, vault_root=tmp_path)
    assert r.is_skip


def test_a_case_keeps_its_identity(tmp_path: Path):
    """Die eigentliche Gegenprobe: die Case-Erkennung liest denselben Feldnamen
    und muss ihn weiter finden. 38 Cases hängen daran."""
    ordner = tmp_path / "20260812.Beispiel"
    ordner.mkdir()
    (ordner / "README.md").write_text(
        "---\nslug: Beispiel\nshort: abcd1234\nstatus: open\n---\n", encoding="utf-8")
    assert case_store._has_case_frontmatter(ordner) is True
