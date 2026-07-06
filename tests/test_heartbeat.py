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

    def register(self, worker: str, host: str, git_status: str | None = None) -> None:
        self.calls.append((worker, host, git_status))
        if self.fail:
            raise ConnectionError("scheduler unreachable")


def test_git_status_reports_branch(gitrepo: Path):
    hb = Heartbeat(client=_FakeClient(), repo_root=gitrepo)
    assert hb._git_status() == "trunk"


def test_git_status_na_outside_git_repo(tmp_path: Path):
    hb = Heartbeat(client=_FakeClient(), repo_root=tmp_path)
    assert hb._git_status() == "n/a"


def test_start_registers_immediately(gitrepo: Path):
    client = _FakeClient()
    hb = Heartbeat(client=client, worker_name="w1", repo_root=gitrepo, interval=60)
    asyncio.run(hb.start())
    assert client.calls == [("w1", hb.host, "trunk")]
    assert hb.last_ok is True
    assert hb.last_at is not None
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
