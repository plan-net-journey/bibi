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


def _traefik_labels(job_id: str, *, app_port: int | None, app_prefix: str | None) -> list[str]:
    """Docker ``-l``-Argumente für Traefik-Routing (PLAN-9 §2, Slice 9.0; bereinigt
    PLAN-11.5): nur noch der App-Content-Router ``bibi-<id>-app``. Der frühere
    ``bibi-<id>-wrapper``-Router (``/-/job/<id>/*`` → Port 8080) entfällt — der
    Wrapper hat seit 11.3 keinen HTTP-Server mehr, die ``/-/job/{id}/…``-Endpunkte
    serviert der Worker-Daemon direkt (kein per-Container-Routing nötig).

    Statisch nur, solange ``app_port``/``app_prefix`` schon beim Spawn feststehen;
    der allgemeine Fall (Port erst zur Laufzeit bekannt) läuft über
    ``_register_app_route`` (dynamisch, File-Provider, PLAN-11.4)."""
    if not (app_port and app_prefix):
        return []
    n = f"bibi-{job_id}"
    pairs: list[tuple[str, str]] = [
        ("traefik.enable", "true"),
        (f"traefik.http.routers.{n}-app.rule", f"PathPrefix(`{app_prefix}/`)"),
        (f"traefik.http.routers.{n}-app.service", f"{n}-app"),
        (f"traefik.http.services.{n}-app.loadbalancer.server.port", str(app_port)),
    ]
    result: list[str] = []
    for k, v in pairs:
        result += ["-l", f"{k}={v}"]
    return result


def build_exec(child_argv: list[str], env: dict[str, str]) -> ExecSpec:
    """Child-argv + env → konkrete Popen-Spezifikation, je nach ``BIBI_EXEC_MODE``.

    - ``host`` (Default): das Child direkt, cwd = ``BIBI_JOB_CWD`` (Verzeichnis der
      Schedule-MD innerhalb des Worktrees), Fallback Worktree-Root ohne ``BIBI_JOB_CWD``.
    - ``container``: ``docker run --rm --name bibi-<id> -v <worktree>:/workspace
      -w /workspace[/<md-relativ>] [-e KEY…] <image> <child-argv>``; der ganze
      Worktree bleibt gemountet (Zugriff auf andere Repo-Verzeichnisse bleibt
      möglich), nur der Arbeitsordner (``-w``) zeigt auf den ``BIBI_JOB_CWD``-Unterpfad;
      PATH um das docker-bin-Dir ergänzt (Cred-Helper).
    - ``container`` + ``app``-Typ: zusätzlich ``--network bibi-net`` + statisches
      App-Content-Traefik-Label, falls ``app_port``/``app_prefix`` beim Spawn schon
      feststehen (PLAN-9 §2, Slice 9.0; bereinigt PLAN-11.5 — kein Wrapper-Routing
      mehr, der Wrapper hat keinen HTTP-Server)."""
    mode = (env.get("BIBI_EXEC_MODE") or "host").strip().lower()
    if mode != "container":
        cwd = env.get("BIBI_JOB_CWD") or env.get("BIBI_WORKTREE") or None
        return ExecSpec(argv=list(child_argv), cwd=cwd, env=dict(env))

    worktree = env["BIBI_WORKTREE"]
    job_cwd = env.get("BIBI_JOB_CWD") or worktree
    md_rel = os.path.relpath(job_cwd, worktree)
    workdir = WORKSPACE if md_rel in (".", "") else f"{WORKSPACE}/{md_rel}"
    image = env.get("BIBI_JOB_IMAGE") or DEFAULT_IMAGE
    docker_bin = resolve_docker_bin(env)
    job_id = env.get("BIBI_JOB_ID", "job")
    name = container_name(job_id)
    argv = [docker_bin, "run", "--rm", "--name", name,
            "-v", f"{worktree}:{WORKSPACE}", "-w", workdir]

    app_port_str = env.get("BIBI_APP_PORT")
    if app_port_str:
        argv += ["-p", f"{app_port_str}:{app_port_str}"]
        # host.docker.internal → Host-Loopback vom Container aus erreichbar.
        # Docker Desktop (Mac/Win) liefert das automatisch; auf Linux braucht es --add-host.
        argv += ["--add-host=host.docker.internal:host-gateway",
                 "-e", "BIBI_WRAPPER_HOST=host.docker.internal"]

    job_type = (env.get("BIBI_JOB_TYPE") or "").strip().lower()
    if job_type == "app":
        app_port = int(app_port_str) if app_port_str else None
        app_prefix = env.get("BIBI_APP_PREFIX") or None
        argv += ["--network", BIBI_NETWORK]
        argv += _traefik_labels(job_id, app_port=app_port, app_prefix=app_prefix)

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
