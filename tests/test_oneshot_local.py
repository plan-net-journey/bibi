"""Ein Oneshot laeuft nie lokal (m.rau/bibi#111, Zustandsmodell §5).

`at` ist der einzige Trigger, der sich verbraucht — und damit die einzige
Ausfuehrungsgarantie "genau einmal" im System. Alle Besonderheiten des
Oneshots folgen aus dieser einen Eigenschaft; es sind keine vier
Einzelentscheidungen.

Hier geht es um die erste davon: **ein lokaler Lauf waere ein zweiter
Verbrauch desselben Termins.** `bibi-ctrl run` umgeht den Scheduler nicht,
sondern legt eine gepinnte `jobs`-Zeile auf diesem Knoten an — der Scheduler
feuert seinen eigenen Termin trotzdem. Der Job liefe zweimal, und die
Garantie waere gebrochen, ohne dass jemand es merkt.

Kein Subprozess: der Abbruch passiert bei der Slug-Aufloesung, lange bevor
ein Wrapper gespawnt wuerde.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bibi.daemon import job_db
from bibi.daemon.worker import run_pinned


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _seed(root: Path, rel: str, body: str) -> None:
    p = root / "vault" / "case" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"seed {rel}")
    conn = job_db.connect(root / "data" / "jobs.sqlite")
    try:
        job_db.rescan(conn, vault_root=root / "vault" / "case")
    finally:
        conn.close()


def test_a_oneshot_refuses_to_run_locally(team_repo: Path):
    """Der Kern: `bibi-ctrl run` auf einem `at`-Job bricht ab, statt einen
    zweiten Verbrauch desselben Termins anzulegen."""
    _seed(team_repo, "once/README.md",
          '---\nslug: OnceOnly\nat: "2026-08-05T10:00:00"\njob: "echo hi"\n---\n')
    with pytest.raises(ValueError, match="OnceOnly"):
        run_pinned(slug="OnceOnly", repo_root=team_repo,
                   db_path=team_repo / "data" / "jobs.sqlite")


def test_the_refusal_says_why_and_what_to_do_instead(team_repo: Path):
    """Die Meldung ist Nutzer-Text, kein Assertion-Trace: sie nennt den Grund
    (genau einmal) und den Ausweg (`adhoc`), statt nur "geht nicht" zu sagen.

    Fertigstellungsbedingung 5 des Umbauplans — jeder Schritt bringt seine
    Fehlertexte aus Nutzersicht mit."""
    _seed(team_repo, "once/README.md",
          '---\nslug: OnceOnly\nat: "2026-08-05T10:00:00"\njob: "echo hi"\n---\n')
    with pytest.raises(ValueError) as exc:
        run_pinned(slug="OnceOnly", repo_root=team_repo,
                   db_path=team_repo / "data" / "jobs.sqlite")
    msg = str(exc.value)
    assert "genau einmal" in msg
    assert "adhoc" in msg


def test_no_row_is_left_behind(team_repo: Path):
    """Der Abbruch passiert vor dem INSERT — sonst bliebe eine gepinnte Zeile
    stehen, die nie laeuft und im Jobs-Screen als Leiche erscheint."""
    _seed(team_repo, "once/README.md",
          '---\nslug: OnceOnly\nat: "2026-08-05T10:00:00"\njob: "echo hi"\n---\n')
    with pytest.raises(ValueError):
        run_pinned(slug="OnceOnly", repo_root=team_repo,
                   db_path=team_repo / "data" / "jobs.sqlite")
    conn = job_db.connect(team_repo / "data" / "jobs.sqlite")
    try:
        pinned = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE pinned_host IS NOT NULL").fetchone()
        assert pinned["n"] == 0
    finally:
        conn.close()


def test_a_recurring_job_still_runs_locally(team_repo: Path):
    """Die Gegenprobe: nur `at` ist betroffen. `schedule`, `startup` und
    `adhoc` geben die Garantie "genau einmal" nicht und duerfen lokal laufen —
    ein zweiter Lauf ist dort kein Wortbruch, sondern der Sinn der Sache."""
    _seed(team_repo, "often/README.md",
          '---\nslug: Often\nschedule: "0 * * * *"\njob: "echo hi"\n---\n')
    res = run_pinned(slug="Often", repo_root=team_repo,
                     db_path=team_repo / "data" / "jobs.sqlite")
    assert res
