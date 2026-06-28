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

#: Docker-Network, in dem Traefik und alle App-Container laufen (PLAN-9 §2).
BIBI_NETWORK = "bibi-net"
#: Wrapper-Port (intern im Container; Traefik routet dorthin für /-/job/{id}/*).
WRAPPER_PORT = 8080


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


def _traefik_labels(job_id: str, *, app_port: int | None, app_prefix: str | None,
                    wrapper_port: int = WRAPPER_PORT) -> list[str]:
    """Docker ``-l``-Argumente für Traefik-Routing (PLAN-9 §2, Slice 9.0).

    Zwei Router je App-Container:
    - ``bibi-<id>-wrapper``: ``/-/job/<id>/*`` → Wrapper Port (8080)
    - ``bibi-<id>-app``:     ``<prefix>/*``    → App-Port (z. B. 8081)
    """
    n = f"bibi-{job_id}"
    pairs: list[tuple[str, str]] = [
        ("traefik.enable", "true"),
        (f"traefik.http.routers.{n}-wrapper.rule",
         f"PathPrefix(`/-/job/{job_id}/`)"),
        (f"traefik.http.routers.{n}-wrapper.service", f"{n}-wrapper"),
        (f"traefik.http.services.{n}-wrapper.loadbalancer.server.port",
         str(wrapper_port)),
    ]
    if app_port and app_prefix:
        pairs += [
            (f"traefik.http.routers.{n}-app.rule",
             f"PathPrefix(`{app_prefix}/`)"),
            (f"traefik.http.routers.{n}-app.service", f"{n}-app"),
            (f"traefik.http.services.{n}-app.loadbalancer.server.port",
             str(app_port)),
        ]
    result: list[str] = []
    for k, v in pairs:
        result += ["-l", f"{k}={v}"]
    return result


def build_exec(child_argv: list[str], env: dict[str, str]) -> ExecSpec:
    """Child-argv + env → konkrete Popen-Spezifikation, je nach ``BIBI_EXEC_MODE``.

    - ``host`` (Default): das Child direkt, cwd = Worktree.
    - ``container``: ``docker run --rm --name bibi-<id> -v <worktree>:/workspace
      -w /workspace [-e KEY…] <image> <child-argv>``; PATH um das docker-bin-Dir
      ergänzt (Cred-Helper).
    - ``container`` + ``app``-Typ: zusätzlich ``--network bibi-net`` + Traefik-Labels
      (PLAN-9 §2, Slice 9.0)."""
    mode = (env.get("BIBI_EXEC_MODE") or "host").strip().lower()
    if mode != "container":
        return ExecSpec(argv=list(child_argv), cwd=env.get("BIBI_WORKTREE") or None,
                        env=dict(env))

    worktree = env["BIBI_WORKTREE"]
    image = env.get("BIBI_JOB_IMAGE") or DEFAULT_IMAGE
    docker_bin = resolve_docker_bin(env)
    job_id = env.get("BIBI_JOB_ID", "job")
    name = container_name(job_id)
    argv = [docker_bin, "run", "--rm", "--name", name,
            "-v", f"{worktree}:{WORKSPACE}", "-w", WORKSPACE]

    job_type = (env.get("BIBI_JOB_TYPE") or "").strip().lower()
    if job_type == "app":
        wrapper_port = int(env.get("BIBI_WRAPPER_PORT") or str(WRAPPER_PORT))
        app_port_str = env.get("BIBI_APP_PORT")
        app_port = int(app_port_str) if app_port_str else None
        app_prefix = env.get("BIBI_APP_PREFIX") or None
        argv += ["--network", BIBI_NETWORK]
        argv += _traefik_labels(job_id, app_port=app_port, app_prefix=app_prefix,
                                wrapper_port=wrapper_port)

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
