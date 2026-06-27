"""claude im Container — volle Wrapper→docker→claude-Kette (PLAN-8 Slice C).

Beweist die Plumbing mit einem **Dummy-Key** (kein Secret nötig): claude läuft im
``bibi-base``-Image, der Host-Wrapper fängt den Output. Mit echtem
``ANTHROPIC_API_KEY`` käme statt der Auth-Fehlermeldung die echte Antwort.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bibi.wrapper import exec_backend, output, run_job

_DOCKER = exec_backend.resolve_docker_bin(dict(os.environ))
_ENV = {**os.environ, "PATH": str(Path(_DOCKER).parent) + os.pathsep + os.environ.get("PATH", "")}


def _docker_and_image() -> bool:
    try:
        v = subprocess.run([_DOCKER, "version", "--format", "{{.Server.Version}}"],
                           capture_output=True, text=True, env=_ENV, timeout=15)
        if v.returncode != 0 or not v.stdout.strip():
            return False
        img = subprocess.run([_DOCKER, "image", "inspect", "bibi-base:dev"],
                             capture_output=True, env=_ENV, timeout=15)
        return img.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs = pytest.mark.skipif(not _docker_and_image(),
                           reason="kein Docker oder bibi-base:dev fehlt")


@needs
def test_claude_runs_in_container_output_captured(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    out = tmp_path / "output.jsonl"
    env = {
        **os.environ,
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_TYPE": "claude",
        "BIBI_JOB_PROMPT": "sag kurz hallo",
        "BIBI_JOB_MODEL": "claude-haiku-4-5-20251001",
        "BIBI_JOB_IMAGE": "bibi-base:dev",
        "ANTHROPIC_API_KEY": "sk-ant-bogus",     # Dummy → Auth-Fehler statt Antwort
        "BIBI_WORKTREE": str(wt),
        "BIBI_OUTPUT_PATH": str(out),
        "BIBI_JOB_ID": "claudesmoke" + os.urandom(3).hex(),
    }
    run_job(env)
    # Kette bewiesen: claude lief im Container, der Host-Wrapper fing Output.
    events = output.read_events(out)
    assert events, "kein Output gefangen — Kette gebrochen"
    text = " ".join(e.get("line", "") for e in events).lower()
    assert "api key" in text or "hallo" in text  # Dummy: Auth-Fehler; echt: Antwort
