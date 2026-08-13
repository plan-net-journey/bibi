"""Ein Setup-Fehler respektiert das Retry-Limit (`#128`).

**Live am 2026-08-10:** `Witz` stand bei `attempt: 488` — mit `attempts: 0` im
Frontmatter, also *ein Versuch, kein Retry*. 24 Stunden lang alle drei Minuten
derselbe deterministische Setup-Fehler (`git worktree prepare` gegen einen
Branch in Merge-Quarantäne).

**Die Ursache ist eine Zusicherung, die drei Stellen zitieren und ein vierter
Pfad nicht gibt.** `worker._retry_fields()` schrieb `failed` + `attempt++` +
neuen Termin, **ohne ``attempts`` je anzusehen** — es konnte das auch nicht,
denn die Prüfung sitzt in ``wrapper._finish()``, und der Wrapper wurde nie
gestartet. Beide Stellen, die es hätten auffangen können, sind bewusst
entfernt worden, beide unter Berufung auf dieselbe Konstruktion:

``job_db.reserve_next()``:
    *„ein Job landet also PER KONSTRUKTION nur dann als 'failed' in der DB,
    wenn der zuletzt gewährte Retry noch aussteht, nie wenn er erschöpft ist."*

``job_db.sweep()``:
    *„Eine Zeile mit ``status='failed'`` schuldet also per Konstruktion IMMER
    noch einen Dispatch."*

**Beide Aufräumarbeiten waren für sich richtig** — sie haben eine echte
Off-by-one behoben. Sie haben nur den Pfad nicht mitgeprüft, der den Wrapper
gar nicht erreicht.

**Der Anlass für die Merge-Quarantäne war wörtlich dieselbe Verschwendung, ein
Stockwerk tiefer** (`merge_quarantine.py`): *„genau das, was
``agent/Witz-83837197`` real getan hat (1440+ Versuche, alle 60s, derselbe
Fehlschlag)"*. Für den Merge-Sweep wurde daraufhin eine Bremse gebaut; für den
Job-Dispatch gibt es sie nicht, und derselbe Job hat es wieder getan.

Deshalb steht die Erschöpfungsregel jetzt an **einer** Stelle
(``backoff.exhausted()``), und beide Pfade lesen sie dort — nicht ein viertes
Sicherheitsnetz, sondern die Zusage einhalten, die drei Stellen zitieren.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from bibi.schedule import backoff

#: Dieselbe Umgebung wie ``test_worker.py`` — ein git-Repo mit Vault und
#: Job-DB. Importiert statt nachgebaut: zwei Fassungen derselben Fixture
#: laufen auseinander, und der Fall hier ist genau der, den jene Datei schon
#: kennt (``test_execute_reservation_setup_failure_does_not_hang_running``).
from tests.test_worker import gitrepo  # noqa: F401 — Fixture per Import


# ── Die Regel selbst ────────────────────────────────────────────────────────


@pytest.mark.parametrize("attempt,attempts,erschoepft", [
    # ``attempts`` meint **Gesamtversuche** (#168, Freigabe m.rau 2026-08-13).
    # ``attempt`` ist der Zähler der vor diesem Lauf beendeten Versuche; mit ihm
    # sind es ``attempt + 1``. Diese Tabelle stand bis `v0.8.10` auf der alten
    # Bedeutung — N Retries *zusätzlich* zum ersten Lauf —, und genau eine Zeile
    # unterscheidet die beiden: bei ``attempts: 2`` ist nach dem **zweiten** Lauf
    # Schluss, nicht nach dem dritten.
    (0, 0, True),    # „kein Versuch" — reserve_next() dispatcht solche Zeilen gar nicht
    (0, 2, False),   # erster von zwei Versuchen läuft gerade
    (1, 2, True),    # zweiter und letzter — danach `error`
    (2, 2, True),    # beide verbraucht
    (3, 2, True),    # mehr als gewährt (Anomalie) — erst recht erschöpft
])
def test_the_exhaustion_rule(attempt, attempts, erschoepft):
    assert backoff.exhausted(attempt, attempts) is erschoepft


# ── Der Pfad, der sie nicht kannte ──────────────────────────────────────────


def _fehlschlag(gitrepo, monkeypatch, frontmatter: str):
    """Ein Job, dessen Worktree-Vorbereitung scheitert — der `#128`-Fall."""
    import bibi.daemon.worker as W
    from bibi.daemon import job_db
    from bibi.daemon.scheduler_client import LocalScheduler
    from bibi.daemon.worker import execute_reservation
    from tests.test_worker import _seed

    jid = _seed(gitrepo, "boom/README.md", frontmatter)
    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    res = job_db.reserve_next(conn)
    conn.close()

    def boom(**_kwargs):
        raise RuntimeError("worktree prepare kaputt")
    monkeypatch.setattr(W, "_run_wrapper", boom)

    execute_reservation(
        res, repo_root=gitrepo, work_dir=gitrepo / "data" / "worktrees",
        client=LocalScheduler(gitrepo / "data" / "jobs.sqlite"), worker_name="t")

    conn = job_db.connect(gitrepo / "data" / "jobs.sqlite")
    row = conn.execute(
        "SELECT status, attempt, next_fire_at FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    return row


@pytest.mark.slow
def test_a_setup_failure_without_retries_ends_in_error(gitrepo, monkeypatch):
    """`#128`: **der Fall, der 488-mal lief.**

    Kein ``attempts:`` im Frontmatter heißt Default 0 — ein Versuch. Der Job
    darf danach nicht mit einem neuen Termin dastehen, sonst holt ihn
    ``reserve_next()`` in drei Minuten wieder, und übermorgen steht er bei 488.
    """
    row = _fehlschlag(gitrepo, monkeypatch,
                      '---\nschedule: now\njob: "echo hi"\n---\n')
    assert row["status"] == "error", (
        "ein Setup-Fehler umgeht das Retry-Limit — der Job bekommt einen neuen "
        "Termin, obwohl sein Frontmatter einen einzigen Versuch vorsieht (#128)")
    assert row["next_fire_at"] is None, (
        "ein erschoepfter Job mit Termin wird wieder dispatcht — genau so "
        "entstehen 488 Versuche")


@pytest.mark.slow
def test_a_setup_failure_uses_the_retries_it_has(gitrepo, monkeypatch):
    """Gegenprobe: **das Limit einhalten heißt nicht, es zu unterlaufen.**

    Ein Job mit gewährten Retries bekommt sie auch dann, wenn der Fehler vor
    dem Wrapper-Start liegt — sonst hätte der Fix eine Fähigkeit genommen
    statt eine Zusage eingehalten.
    """
    row = _fehlschlag(gitrepo, monkeypatch,
                      '---\nschedule: now\nattempts: 2\njob: "echo hi"\n---\n')
    assert row["status"] == "failed" and row["attempt"] == 1
    assert row["next_fire_at"] is not None


# ── Der Wächter: eine Regel, nicht zwei ─────────────────────────────────────


_PAKET = pathlib.Path(__file__).resolve().parent.parent / "bibi"

#: Wo die Regel wohnt. Alles andere ruft sie.
_QUELLE = "bibi/schedule/backoff.py"


def _baut_die_regel_selbst(pfad: pathlib.Path) -> list[int]:
    """Zeilen, die ``attempt`` und ``attempts`` selbst vergleichen.

    Nicht der Name der Funktion wird geprüft, sondern die **Form des
    Vergleichs** — genau die hat an vier Stellen gestanden und ist an dreien
    entfernt worden, während die vierte sie nie hatte.
    """
    try:
        baum = ast.parse(pfad.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    def namen(k) -> set[str]:
        return {n.id.lower() for n in ast.walk(k) if isinstance(n, ast.Name)} | {
            n.attr.lower() for n in ast.walk(k) if isinstance(n, ast.Attribute)} | {
            n.value.lower() for n in ast.walk(k)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    treffer = []
    for k in ast.walk(baum):
        if not isinstance(k, ast.Compare) or not k.ops:
            continue
        if not isinstance(k.ops[0], (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
            continue
        links, rechts = namen(k.left), namen(k.comparators[0])
        if any("attempt" in n for n in links) and any("attempt" in n for n in rechts):
            treffer.append(k.lineno)
    return treffer


def test_only_one_place_decides_whether_the_retries_are_used_up():
    """Die Zusage, nicht die Fundstelle.

    `#128` ist entstanden, weil drei Stellen sich auf eine Entscheidung
    beriefen, die eine vierte nie traf. Ein Wächter über den Fundstellen hätte
    das nicht gefangen — es fehlte ja gerade eine.
    """
    verstoesse = []
    for pfad in sorted(_PAKET.rglob("*.py")):
        rel = pfad.relative_to(_PAKET.parent).as_posix()
        if rel == _QUELLE:
            continue
        for zeile in _baut_die_regel_selbst(pfad):
            verstoesse.append(f"{rel}:{zeile}")
    assert not verstoesse, (
        "diese Stellen entscheiden selbst, ob die Versuche aufgebraucht sind, "
        f"statt {_QUELLE} zu fragen — so hatte #128 vier Fassungen einer Regel "
        "und eine davon fehlte: " + ", ".join(verstoesse))
