"""Exec-Backend (PLAN-8 D1): **wie** das Child gestartet wird — Host-Prozess oder
Container. Der Wrapper bleibt die universelle Ausführungs-/Monitor-Einheit; im
Container-Modus wird das Child-argv in ein ``docker run …`` gehüllt — Output-Pumping,
Silence-/Wall-Time-Monitoring und Format bleiben unverändert (der Wrapper pumpt die
docker-Pipes).

Reine Funktion (``build_exec``) — testbar ohne Docker.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# Kandidaten, falls weder BIBI_DOCKER_BIN noch PATH die docker-CLI liefern
# (Docker Desktop legt sie z. B. unter ~/.docker/bin ab).
_DOCKER_CANDIDATES = (
    str(Path.home() / ".docker" / "bin" / "docker"),
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
)

#: Statische Env-Variablen, die in den Container durchgereicht werden.
#: Dynamische Job-Credentials kommen via ``BIBI_JOB_ENV_*``-Prefix in worker._exec_config.
_CONTAINER_ENV = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)

DEFAULT_IMAGE = "bibi-base:dev"
WORKSPACE = "/workspace"


def resolve_docker_bin(env: dict[str, str]) -> str:
    """docker-CLI bestimmen: ``BIBI_DOCKER_BIN`` > PATH > bekannte Orte > ``docker``."""
    explicit = env.get("BIBI_DOCKER_BIN")
    if explicit:
        return explicit
    found = shutil.which("docker")
    if found:
        return found
    for cand in _DOCKER_CANDIDATES:
        if Path(cand).exists():
            return cand
    return "docker"


def container_name(job_id: str) -> str:
    return f"bibi-{job_id}"


@dataclass(frozen=True, slots=True)
class ExecSpec:
    argv: list[str]
    cwd: str | None
    env: dict[str, str]


def build_exec(child_argv: list[str], env: dict[str, str]) -> ExecSpec:
    """Child-argv + env → konkrete Popen-Spezifikation, je nach ``BIBI_EXEC_MODE``.

    - ``host`` (Default): das Child direkt, cwd = Worktree.
    - ``container``: ``docker run --rm --name bibi-<id> -v <worktree>:/workspace
      -w /workspace [-e KEY…] <image> <child-argv>``; PATH um das docker-bin-Dir
      ergänzt (Cred-Helper)."""
    mode = (env.get("BIBI_EXEC_MODE") or "host").strip().lower()
    if mode != "container":
        return ExecSpec(argv=list(child_argv), cwd=env.get("BIBI_WORKTREE") or None,
                        env=dict(env))

    worktree = env["BIBI_WORKTREE"]
    image = env.get("BIBI_JOB_IMAGE") or DEFAULT_IMAGE
    docker_bin = resolve_docker_bin(env)
    name = container_name(env.get("BIBI_JOB_ID", "job"))
    argv = [docker_bin, "run", "--rm", "--name", name,
            "-v", f"{worktree}:{WORKSPACE}", "-w", WORKSPACE]
    # Statische Liste + alle dynamisch per BIBI_JOB_ENV_* hinzugefügten Keys.
    # Jeder Key in env, der nicht mit BIBI_ beginnt und nicht PATH/HOME ist,
    # wurde von worker._exec_config aus der Knoten-Config entfaltet und soll rein.
    _INTERNAL = frozenset({"PATH", "HOME", "USER", "SHELL", "TMPDIR"})
    pass_keys = set(_CONTAINER_ENV)
    for key in env:
        if not key.startswith("BIBI_") and key not in _INTERNAL:
            pass_keys.add(key)
    for key in sorted(pass_keys):
        if env.get(key):
            argv += ["-e", key]   # ohne =Wert ⇒ Host-Wert (run_env) wird verwendet
    argv += [image, *child_argv]

    # PATH um das docker-bin-Dir ergänzen, sonst findet docker den Cred-Helper
    # (docker-credential-*) nicht und jeder Image-Pull scheitert.
    run_env = dict(env)
    run_env["PATH"] = str(Path(docker_bin).parent) + os.pathsep + run_env.get("PATH", "")
    return ExecSpec(argv=argv, cwd=None, env=run_env)
