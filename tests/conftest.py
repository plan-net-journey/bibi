"""Gemeinsame Test-Fixtures.

``team_repo`` baut ein echtes, temporäres git-Repo mit der Team-Repo-Struktur
(DESIGN §3.2) und parkt das cwd hinein — so arbeiten ``repo``/``state``/
``case_store`` gegen einen isolierten Baum statt gegen das echte Repo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bibi import repo


def pytest_addoption(parser):
    parser.addoption("--slow", action="store_true", default=False,
                     help="run slow tests (subprocess/Docker)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--slow"):
        skip = pytest.mark.skip(reason="use --slow to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)


# PLAN-30 Ebene 4 (Review-Runde 7, Fund 1/2): mergeback.merge_back()s
# Idle-Fenster-Guard (git_ops.recently_touched_paths(), IDLE_WINDOW_S=120)
# vergleicht die mtime der Konflikt-Datei gegen "jetzt" — eine Datei, die ein
# Test gerade erst geschrieben/committet hat, liegt IMMER innerhalb dieses
# Fensters. Ohne ein now=-Override weit in der Zukunft landet ein Test, der
# einen "echten Konflikt" (Modus B) aufbauen will, stattdessen im Idle-Guard
# ("live_edit") — nicht im eigentlich zu prüfenden Konfliktpfad. Jeder Test,
# der mergeback.merge_back() unmittelbar nach dem Schreiben der Konflikt-
# Datei aufruft UND nicht selbst gezielt den Idle-Guard prüft, muss
# ``now=FAR_FUTURE_TS`` übergeben (oder die HTTP-Route-Variante: die
# betroffene Datei per ``os.utime()`` vor dem Aufruf zurückdatieren, dort
# gibt es keinen now=-Parameter).
FAR_FUTURE_TS = 9_999_999_999.0


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _init_repo(root: Path, branch: str = "trunk") -> None:
    (root / ".claude").mkdir(parents=True)
    (root / "vault" / "case").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    # wie ein echtes Team-Repo: Laufzeit-State gitignored (DESIGN §3.2), damit
    # .state.md den Working Tree nicht "dirty" macht.
    (root / ".gitignore").write_text(".claude/.state.md\ndata/\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", branch)
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")


@pytest.fixture(autouse=True)
def _reset_dispatch_count():
    """``job_db._dispatch_count`` ist ein Prozesslaufzeit-Zähler (PLAN-21
    Befund 11 v2, ``job_stats.running_since_uptime``) — ohne Reset würden sich
    ``reserve_next()``-Aufrufe früherer Tests im selben pytest-Prozess
    aufsummieren und einzelne Zähler-Assertions verfälschen."""
    from bibi.daemon import job_db
    job_db._dispatch_count = 0
    yield


@pytest.fixture(autouse=True)
def _isolate_node_config(tmp_path_factory, monkeypatch: pytest.MonkeyPatch):
    """Tests nie gegen die **echte** ``~/.config/bibi/env`` laufen lassen — sonst
    leaken Knoten-Settings (BIBI_EXEC_MODE=container, Auth-Token, BIBI_CLAUDE_BIN …)
    in die Suite und verfälschen Host-Modus-Tests. ``env_path()`` respektiert
    ``XDG_CONFIG_HOME`` → auf ein leeres Temp-Dir zeigen + Dev-Shell-Übersteuerungen
    der Exec-Variablen neutralisieren."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("bibicfg")))
    for k in ("BIBI_EXEC_MODE", "BIBI_JOB_IMAGE", "BIBI_DOCKER_BIN",
              "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
              # PLAN-22 Befund 6: config.public_host() liest beide — ohne
              # Isolation würde die Shell-Umgebung des Test-Hosts in App-
              # Adress-Tests leaken (analog zur bestehenden Begründung oben).
              "BIBI_SCHEDULER_URL", "BIBI_PUBLIC_HOST"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def seed_journal_row():
    """Test-Helfer: legt eine vollständige ``journal``-Zeile direkt per SQL an —
    Ersatz für die (PLAN-28 Refactor D) entfernte ``job_db.write_local_journal()``,
    deren einzige Aufgabe für die meisten Aufrufer genau das war: eine fertige
    Zeile für Lese-Pfad-Tests seeden, unabhängig von ``domain`` (Default weiterhin
    ``'local'``, wie bisher — Tests, die den Wert wirklich brauchen, geben ihn an)."""
    def _seed(conn, *, run_id: str, slug: str, kind: str, status: str,
              exit_code: int | None, output_ref: str | None, host: str | None,
              worker: str | None, started_at: float, finished_at: float,
              reason: str | None = None, payload: str | None = None,
              domain: str = "local") -> None:
        conn.execute(
            "INSERT INTO journal (run_id, slug, kind, status, reason, started_at, "
            "finished_at, exit_code, exec_runtime, host, worker, output_ref, payload, "
            "snapshot, archived_at, domain) VALUES (:run_id,:slug,:kind,:status,:reason,"
            ":started_at,:finished_at,:exit_code,:exec_runtime,:host,:worker,:output_ref,"
            ":payload,:snapshot,:archived_at,:domain)",
            {
                "run_id": run_id, "slug": slug, "kind": kind, "status": status,
                "reason": reason, "started_at": started_at, "finished_at": finished_at,
                "exit_code": exit_code, "exec_runtime": finished_at - started_at,
                "host": host, "worker": worker, "output_ref": output_ref, "payload": payload,
                "snapshot": json.dumps({"slug": slug, "kind": kind, "status": status,
                                        "exit_code": exit_code}, ensure_ascii=False),
                "archived_at": finished_at, "domain": domain,
            },
        )
    return _seed


@pytest.fixture
def team_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporäres Team-Repo; cwd ist hineingeparkt. Gibt den Repo-Root zurück."""
    root = tmp_path / "team"
    _init_repo(root)

    # cwd parken; keine geleakte BIBI_CASE_DIR-Übersteuerung aus der Umgebung.
    monkeypatch.chdir(root)
    monkeypatch.delenv("BIBI_CASE_DIR", raising=False)
    repo._root_of.cache_clear()
    yield root
    repo._root_of.cache_clear()


@pytest.fixture
def repo_with_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Team-Repo mit echtem bare-Origin (branch `trunk`), Initial-Commit gepusht.

    Erlaubt push/pull/integrate/Konflikt ohne Netz. Gibt (root, origin) zurück;
    cwd ist in `root` geparkt.
    """
    root = tmp_path / "team"
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _init_repo(root)
    _git(origin, "init", "-q", "--bare", "-b", "trunk")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "push", "-q", "-u", "origin", "trunk")

    monkeypatch.chdir(root)
    monkeypatch.delenv("BIBI_CASE_DIR", raising=False)
    repo._root_of.cache_clear()
    yield root, origin
    repo._root_of.cache_clear()
