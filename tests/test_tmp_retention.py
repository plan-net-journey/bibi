"""#101: Wohin die 15,21s gehen — nicht ins Worktree-Setup, sondern in pytests
eigenes tmp_path-Aufräumen.

Live gemessen (2026-08-10, freie Maschine): ``pytest
tests/test_worker.py::test_silence_zombies_job --slow`` allein lief 15,21s,
obwohl der Job selbst (``silence_timeout: 1``) in unter 2s terminal wird.
Profiling (``cProfile`` über den isolierten Testlauf) zeigte den Rest fast
vollständig in ``_pytest/pathlib.py::cleanup_numbered_dir``, aufgerufen von
``pytest_sessionfinish`` — 11 von 14 Sekunden, 180175 einzelne ``rmtree``-
Schritte.

**Der Mechanismus:** pytest hebt pro *Session* einen nummerierten
``pytest-of-<user>/pytest-<N>``-Baum unter ``tmp_path`` auf und löscht am
Sessionende alte, sobald mehr als ``tmp_path_retention_count`` (Default 3)
existieren. Retention zählt pro **Session**, nicht pro Test — eine volle
``--slow``-Suite (Tausende ``tmp_path``-Nutzungen, jede ``gitrepo``-Fixture ein
eigenes ``.git``, jeder Worktree-Test mehrere) sammelt das alles in EINEM
Baum. Aufräumen muss den nicht die Session, die ihn angelegt hat — das erledigt
ein x-beliebiger *späterer* Lauf, der zufällig über die Grenze tritt. Genau das
sah wie „lastabhängig" aus: nicht die Maschine war unter Last, sondern welcher
Lauf gerade die Cleanup-Rechnung eines fremden, vollen Suite-Laufs bezahlte.

**Der Fix:** ``tmp_path_retention_policy = "failed"`` in ``pyproject.toml``.
Laut ``_pytest/tmpdir.py::pytest_sessionfinish`` löscht das den gesamten
Basetemp-Baum sofort, wenn die Session komplett grün war — nichts akkumuliert
über eine Session hinaus mehr. Nur Bäume mit mindestens einem Fehlschlag
bleiben zur Fehlersuche liegen, bis zur bestehenden Retention-Grenze.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_pyproject_sets_failed_retention_policy():
    """Die Konfiguration selbst — ein Revert dieser Zeile waere sonst lautlos."""
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'tmp_path_retention_policy = "failed"' in text, (
        "pyproject.toml setzt tmp_path_retention_policy nicht auf \"failed\" — "
        "eine vollstaendig gruene Suite haeuft ihren tmp_path-Baum wieder fuer "
        "einen spaeteren, unbeteiligten Lauf an (#101)")


@pytest.mark.slow
def test_failed_retention_policy_removes_the_tree_after_an_all_green_session(tmp_path):
    """Der Mechanismus selbst, unabhaengig von unserer Konfiguration geprueft —
    falls pytest sein eigenes Verhalten je aendert, faellt das hier auf, statt
    sich hinter einer bestandenen Config-Zeile zu verstecken.

    Bewusst kein ``pytester.runpytest_subprocess()``: die Fixture setzt selbst
    ein ``--basetemp``, und genau dann ueberspringt ``pytest_sessionfinish``
    das Auto-Loeschen (``_given_basetemp is None`` waere sonst falsch) — der
    Fall, den dieser Test pruefen soll, laesst sich damit gar nicht erzeugen.
    Ein eigener ``TMPDIR`` isoliert stattdessen von der echten
    ``pytest-of-<user>``-Ablage, ohne pytest ein explizites Basetemp zu geben."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntmp_path_retention_policy = "failed"\n',
        encoding="utf-8")
    (project / "test_leaf.py").write_text(
        "def test_uses_tmp_path(tmp_path):\n"
        "    print('BASETEMP=' + str(tmp_path.parent))\n"
        "    assert True\n",
        encoding="utf-8")
    isolated_tmp = tmp_path / "isolated-tmp"
    isolated_tmp.mkdir()
    env = {**os.environ, "TMPDIR": str(isolated_tmp)}

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-s", "-p", "no:cacheprovider", "."],
        cwd=project, capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    lines = [line for line in proc.stdout.splitlines() if "BASETEMP=" in line]
    assert lines, "keine BASETEMP-Zeile in der Ausgabe:\n" + proc.stdout
    basetemp = Path(lines[0].split("BASETEMP=", 1)[1].strip())
    assert not basetemp.exists(), (
        f"{basetemp} ueberlebt eine vollstaendig bestandene Session trotz "
        "tmp_path_retention_policy=\"failed\" — der angenommene Mechanismus "
        "greift nicht (mehr)")
