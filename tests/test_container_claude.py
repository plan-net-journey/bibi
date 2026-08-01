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
from tests._docker import container_skip_reason

_DOCKER = exec_backend.resolve_docker_bin(dict(os.environ))
_ENV = {**os.environ, "PATH": str(Path(_DOCKER).parent) + os.pathsep + os.environ.get("PATH", "")}


# Die Voraussetzung wird **gemessen**, nicht aus „läuft Docker?" geschlossen
# (m.rau/bibi#86): beide Tests unten starten mit ``--user <host-uid>:0`` und
# schreiben in einen Bind-Mount. Reicht der Docker-Server die Host-UID nicht
# durch, scheitern sie an der Umgebung statt am Code — und der Grund steht
# dann im Skip-Text statt in einem verwirrenden „Permission denied".
_SKIP = container_skip_reason()

needs = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


@needs
@pytest.mark.slow
def test_container_runs_as_mapped_host_user_with_working_sudo(tmp_path: Path):
    """PLAN-24 Befund 5: --user <host-uid>:0 + Entrypoint (passwd/shadow-Eintrag
    zur Laufzeit) + gruppenbasiertes NOPASSWD-sudo (%root). Ohne den Entrypoint
    lehnt sudo eine fremde UID per PAM/NSS ab ("account validation failure" /
    "you do not exist in the passwd database") — live gegen bibi-base:dev
    verifiziert, bevor dieser Test geschrieben wurde."""
    wt = tmp_path / "wt"
    wt.mkdir()
    env = {
        **os.environ,
        "BIBI_EXEC_MODE": "container",
        "BIBI_JOB_IMAGE": "bibi-base:dev",
        "BIBI_WORKTREE": str(wt),
        "BIBI_JOB_ID": "sudosmoke" + os.urandom(3).hex(),
        "BIBI_DATA_HOME": str(tmp_path / "data-home"),
    }
    spec = exec_backend.build_exec(["bash", "-c", "sudo whoami > sudo_out.txt"], env)
    r = subprocess.run(spec.argv, capture_output=True, text=True, env=spec.env, timeout=60)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (wt / "sudo_out.txt").read_text().strip() == "root"
    # Bind-Mount-Schreibzugriff gehört exakt dem Host-User (kein chown nötig).
    assert (wt / "sudo_out.txt").stat().st_uid == os.getuid()


@needs
@pytest.mark.slow
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
        "BIBI_DATA_HOME": str(tmp_path / "data-home"),
    }
    run_job(env)
    # Kette bewiesen: claude lief im Container, der Host-Wrapper fing Output.
    events = output.read_events(out)
    assert events, "kein Output gefangen — Kette gebrochen"
    text = " ".join(e.get("line", "") for e in events).lower()
    # Drei legitime Ausgänge, alle beweisen die Kette (darum geht es hier —
    # nicht um die API-Antwort selbst): Dummy-Key → Auth-Fehler ("api key");
    # echter Key → Antwort ("hallo"); API technisch unerreichbar → "api error:
    # unable to connect …" (m.raus --slow-Lauf 2026-07-27: transienter Docker-
    # Netz-Hiccup, ~3 min Connection-Retries — claude lief, Output kam an,
    # nur der Assert kannte diesen dritten Fall nicht).
    assert "api key" in text or "api error" in text or "hallo" in text
