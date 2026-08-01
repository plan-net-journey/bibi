"""Heartbeat: eigenständiger --connect-Mechanismus, unabhängig von der Worker-Rolle.

User-Feedback 2026-07-05: --connect war ohne --worker bisher wirkungslos, weil
der Heartbeat-Loop nur in Worker.start() lief (nur gebaut, wenn roles.worker
True ist — ein reiner Client, DESIGN.md A12/Client Requirements.md, "weder
Scheduler noch Worker"), sendete dadurch nie einen Heartbeat. Diese Datei
testet den jetzt herausgelösten, eigenständigen Heartbeat-Mechanismus.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest

from bibi.daemon.heartbeat import Heartbeat


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def gitrepo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _git(root, "config", "user.email", "t@e.x")
    _git(root, "config", "user.name", "t")
    (root / "f").write_text("x", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


class _FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple] = []
        self.last_kwargs: dict = {}

    def register(self, worker: str, host: str, git_status: str | None = None,
                 **kw) -> dict | None:
        # ``**kw`` statt einer exakt gespiegelten Signatur: der Heartbeat bekommt
        # regelmäßig neue Felder (role, port, client_config_version, jetzt
        # engine/git_commit für m.rau/bibi#19). Eine nachzuziehende Parameterliste
        # ließ die Aufrufe hier bei jeder Erweiterung in einen TypeError laufen,
        # den ``Heartbeat._beat()`` als „Scheduler nicht erreichbar" verschluckt —
        # die Tests scheiterten dann mit „0 Aufrufe" statt mit dem echten Grund.
        self.calls.append((worker, host, git_status, kw.get("node_id"),
                           kw.get("git_user"), kw.get("role"), kw.get("port"),
                           kw.get("client_config_version")))
        self.last_kwargs = kw
        if self.fail:
            raise ConnectionError("scheduler unreachable")
        return None


def test_tree_status_reports_branch_tree_and_sync(gitrepo: Path):
    # PLAN-18 Stufe 18.0: A12 verspricht Tree+Sync im Heartbeat, bisher kam nur
    # der Branch-Name hoch — geteilte working_tree_status()-Basis behebt das.
    hb = Heartbeat(client=_FakeClient(), repo_root=gitrepo)
    label, commit = hb._tree_status()
    assert label == "trunk · clean · synced"
    # m.rau/bibi#19: der Commit kommt als zweiter Rückgabewert dazu, nicht im
    # String — „synced" allein sagt nicht, ob zwei Knoten denselben Stand fahren.
    assert commit and len(commit) == 7


def test_tree_status_reflects_modified_tree(gitrepo: Path):
    (gitrepo / "f").write_text("y", encoding="utf-8")
    hb = Heartbeat(client=_FakeClient(), repo_root=gitrepo)
    assert hb._tree_status()[0] == "trunk · modified · synced"


def test_tree_status_na_outside_git_repo(tmp_path: Path):
    hb = Heartbeat(client=_FakeClient(), repo_root=tmp_path)
    assert hb._tree_status() == ("n/a", None)


def test_engine_and_commit_included_in_heartbeat(gitrepo: Path):
    # m.rau/bibi#19: ohne diese Angabe konnte ein Deploy sein eigenes Ergebnis
    # nicht prüfen — der letzte Nachweis lief über ein Verhaltensmerkmal des
    # neuen Codes in einer Logzeile, also über Indizien.
    client = _FakeClient()
    hb = Heartbeat(client=client, repo_root=gitrepo)
    hb._beat()
    assert client.last_kwargs["engine"]        # Label, nie leer (mindestens "n/a")
    assert client.last_kwargs["git_commit"]


def test_start_registers_immediately(gitrepo: Path):
    client = _FakeClient()
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=60)
    asyncio.run(hb.start())
    assert len(client.calls) == 1
    worker, host, git_status, node_id, git_user, role, port, cfg_version = client.calls[0]
    assert (worker, host, git_status) == ("w1", hb.host, "trunk · clean · synced")
    assert node_id == hb.node_id and len(node_id) == 32
    assert git_user == "t"  # gitrepo-Fixture: git config user.name = "t"
    assert role is None  # kein role= übergeben -> nichts zu senden
    assert port is None  # kein BIBI_DAEMON_PORT in der Test-Umgebung gesetzt
    assert cfg_version is None  # noch nie ein Bundle empfangen
    assert hb.last_ok is True
    assert hb.last_at is not None
    asyncio.run(hb.stop())


def test_role_included_in_heartbeat(gitrepo: Path):
    # Bibi4-Iteration, User-Fund: "Client Übersicht braucht die Rollen je
    # Client" — role wird einmal im Konstruktor übergeben (analog worker_name),
    # nicht pro Beat neu ermittelt.
    client = _FakeClient()
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo,
                   interval=60, role="synchronizer,controller")
    asyncio.run(hb.start())
    assert client.calls[0][5] == "synchronizer,controller"
    asyncio.run(hb.stop())


def test_node_id_stable_across_beats(gitrepo: Path):
    # Bibi4-Iteration: node_id wird einmal in __init__ gelesen/generiert und
    # bleibt für die gesamte Prozesslaufzeit stabil, anders als worker_name/
    # host, die sich (laut User-Fund) je nach Netzwerk ändern können.
    client = _FakeClient()
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=0.02)

    async def run():
        await hb.start()
        await asyncio.sleep(0.07)
        await hb.stop()

    asyncio.run(run())
    node_ids = {call[3] for call in client.calls}
    assert node_ids == {hb.node_id}


def test_git_user_included_in_heartbeat(gitrepo: Path):
    from bibi import git_ops
    client = _FakeClient()
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=60)
    asyncio.run(hb.start())
    assert client.calls[0][4] == git_ops.git_user_name(gitrepo) == "t"
    asyncio.run(hb.stop())


def test_loop_sends_periodically(gitrepo: Path):
    client = _FakeClient()
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=0.05)

    async def run():
        await hb.start()
        await asyncio.sleep(0.17)
        await hb.stop()

    asyncio.run(run())
    # 1 sofort beim Start + mind. 2 weitere im 0.05s-Takt binnen 0.17s.
    assert len(client.calls) >= 3


def test_marks_failure_without_raising(gitrepo: Path):
    client = _FakeClient(fail=True)
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=60)
    asyncio.run(hb.start())
    assert hb.last_ok is False
    assert hb.last_at is not None
    asyncio.run(hb.stop())


def test_stop_cancels_loop_cleanly(gitrepo: Path):
    client = _FakeClient()
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=0.02)

    async def run():
        await hb.start()
        await hb.stop()
        n = len(client.calls)
        await asyncio.sleep(0.1)  # nach stop() darf nichts mehr dazukommen
        return n

    n_at_stop = asyncio.run(run())
    assert len(client.calls) == n_at_stop


# ── Config-Bundle-Distribution (PLAN-32 Stufe 32.2) ─────────────────────────


class _BundleClient:
    """Liefert bei register() dieselbe Antwortform wie RemoteScheduler.register()
    (dict mit config_version + optionalem config_bundle)."""

    def __init__(self, resp: dict | None) -> None:
        self._resp = resp

    def register(self, *a, **kw) -> dict | None:
        return self._resp


def test_beat_writes_distributed_env_when_bundle_present(gitrepo: Path):
    from bibi import config
    client = _BundleClient({"config_version": "v1",
                           "config_bundle": {"BIBI_JOB_ENV_FOO": "secret"}})
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=60)
    asyncio.run(hb.start())
    asyncio.run(hb.stop())
    assert config.read_distributed_env()["BIBI_JOB_ENV_FOO"] == "secret"
    assert config.distributed_config_version() == "v1"


def test_beat_does_not_write_when_bundle_absent(gitrepo: Path):
    from bibi import config
    client = _BundleClient({"config_version": "v1"})  # kein config_bundle-Key
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=60)
    asyncio.run(hb.start())
    asyncio.run(hb.stop())
    assert config.read_distributed_env() == {}


def test_beat_does_not_write_when_response_is_none(gitrepo: Path):
    from bibi import config
    client = _BundleClient(None)
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=60)
    asyncio.run(hb.start())
    asyncio.run(hb.stop())
    assert config.read_distributed_env() == {}


def test_heartbeat_reports_whether_it_runs_in_a_session(gitrepo: Path):
    """m.rau/bibi#44: der Host kann von außen nicht sehen, ob ein Knoten einen
    Supervisor hat — nur der startende Prozess weiß es. Also meldet er es."""
    c = _FakeClient()
    hb = Heartbeat(client=c, repo_root=gitrepo, worker_name="w", session=True)
    hb._beat()
    assert c.last_kwargs["session"] is True

    c2 = _FakeClient()
    Heartbeat(client=c2, repo_root=gitrepo, worker_name="w")._beat()
    assert c2.last_kwargs["session"] is False
