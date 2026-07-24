"""Exec-Backend (PLAN-8 D1): **wie** das Child gestartet wird — Host-Prozess oder
Container. Der Wrapper bleibt die universelle Ausführungs-/Monitor-Einheit; im
Container-Modus wird das Child-argv in ein ``docker run …`` gehüllt — Output-Pumping,
Silence-/Wall-Time-Monitoring und Format bleiben unverändert (der Wrapper pumpt die
docker-Pipes).

Reine Funktion (``build_exec``) — testbar ohne Docker.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
#: BIBI_JOB_ID (User-Fund 2026-07-21, Bibi4 Batch 6): "Reset Test (Container)"
#: setzte im Container nie zurück, weil BIBI_* standardmäßig NICHT durchgereicht
#: wird (Zeile ~262 unten) — jedes Skript, das der External-job-data-Konvention
#: folgt (bibi.job.data_dir() oder eigener BIBI_JOB_ID-Handbau, s.
#: reset-test.py), sah im Container immer den "adhoc"-Fallback statt der
#: echten, stabilen Job-ID. Betraf nicht nur RESET: JEDES container-exec_mode-
#: Skript, das sich per BIBI_JOB_ID scopen wollte, landete faktisch in einem
#: einzigen geteilten "adhoc"-Ordner. Host-Modus war nie betroffen (dort läuft
#: das Kind direkt mit dem vollen env-dict, keine Docker-"-e"-Filterung).
_CONTAINER_ENV = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "BIBI_JOB_ID",
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


def _host_uid() -> int:
    """Eigene Funktion (statt inline ``os.getuid()``) — testbar per monkeypatch,
    UID variiert pro Host/Aufruf (PLAN-24 Befund 5, "arbitrary UID"-Konvention)."""
    return os.getuid()


_TAG_UNSAFE_RE = re.compile(r"[^a-z0-9._-]+")


def job_image_tag(slug: str) -> str:
    """Docker-Image-Tag für das per-Job evolvierende Image (PLAN-24 Befund 5:
    Nachinstalliertes persistiert über Läufe hinweg, statt bei jedem ``--rm``
    verlorenzugehen). Slugs können Zeichen enthalten, die in Docker-Repo-Namen
    ungültig sind (nur lowercase + ``[a-z0-9._-]``) — sicherheitshalber
    normalisieren, statt der Slug-Validierung ein neues Format aufzuzwingen."""
    safe = _TAG_UNSAFE_RE.sub("-", slug.lower()).strip("-") or "job"
    return f"bibi-job-{safe}:latest"


def finalize_container(env: dict[str, str]) -> None:
    """Nach einem Container-Lauf mit Job-Image-Persistenz (PLAN-24 Befund 5):
    Container-Zustand in das per-Job-Image committen, bevor der (ohne
    ``--rm`` gestartete, s. ``build_exec``) Container aufgeräumt wird — genau
    dieser Zeitpunkt ist der einzige, an dem der Container noch existiert.
    Best-effort: ein Fehler hier darf den Job-Report nie verhindern. No-op,
    wenn keine Job-Image-Persistenz aktiv war (Host-Modus oder expliziter
    ``image:``-Override, s. ``worker._run_wrapper``)."""
    if (env.get("BIBI_JOB_IMAGE_PERSIST") or "").strip() != "1":
        return
    job_id = env.get("BIBI_JOB_ID")
    slug = env.get("BIBI_JOB_SLUG")
    if not job_id or not slug:
        return
    docker_bin = resolve_docker_bin(env)
    name = container_name(job_id)
    try:
        subprocess.run([docker_bin, "commit", name, job_image_tag(slug)],
                       capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        subprocess.run([docker_bin, "rm", "-f", name], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def stop_container(env: dict[str, str]) -> None:
    """Best-effort ``docker stop`` für den Job-Container — das Gegenstück zum
    daemon-seitigen ``worker._docker(["stop", ...])`` (User-KILL-Pfad), hier
    aber für Terminierungen, die der **Wrapper selbst** entscheidet (silence,
    wall_time, deferred, eingehendes SIGTERM). Der von diesen Pfaden über
    ``_terminate_proc()`` überwachte ``proc`` ist im Container-Modus nur der
    attached ``docker run``-CLI-Prozess (s. ``build_exec``, kein ``-d``) —
    SIGKILL auf dessen Prozessgruppe beendet nicht den vom Docker-Daemon
    separat verwalteten Container, der sonst verwaist weiterläuft (ZOMBIE-
    Befund, bibi-notes ``FeedbackOnJobManagement.md``). No-op außerhalb von
    ``exec_mode: container`` oder ohne Job-ID."""
    mode = (env.get("BIBI_EXEC_MODE") or "host").strip().lower()
    if mode != "container":
        return
    job_id = env.get("BIBI_JOB_ID")
    if not job_id:
        return
    docker_bin = resolve_docker_bin(env)
    try:
        subprocess.run([docker_bin, "stop", container_name(job_id)],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


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


def _data_home(env: dict[str, str]) -> Path:
    """Externer Job-Daten-Root, generisch in jeden Container gemountet (User-
    Fund 2026-07-14: gmail-transfer bootstrappte bei jedem Container-Fire neu,
    weil sein externes ``items.ndjson`` — außerhalb des Worktrees, damit es
    den Worktree-Wipe pro Fire überlebt, s. ``worktree.prepare()`` — im
    Container unsichtbar war, nur der Worktree wird gemountet).

    Vault-Konvention (mehrfach etabliert: ``gmail``/``calendar``-Collector,
    ``ticker/_paths.py`` TopAktienScreening): externe Job-DATEN liegen unter
    ``Path.home()/".local/share/bibi"/<subsystem>``, analog XDG
    ``~/.local/share``. Ein einziger Mount des gemeinsamen ``bibi``-Roots
    deckt jedes Subsystem ab, das dieser Konvention folgt — kein Job-
    spezifisches Wissen im Engine-Code nötig.

    Bewusst NICHT für Secrets (``Path.home()/".config"/"bibi-<name>"``) — die
    laufen über den bestehenden ``BIBI_JOB_ENV_*``-Mechanismus (Team-
    Vertrauen-Modell, s. Vault ``gmail/README.md`` "Container-Auth"): ein
    Datei-Mount wäre pro Secret-Verzeichnis nötig, ein Env-Var ist es schon.

    ``BIBI_DATA_HOME``-Override nur für Tests/Sonderfälle gedacht (kein
    Schedule-Override wie ``exec_mode`` — dieselbe Konvention gilt für jeden
    Job gleich)."""
    return Path(env.get("BIBI_DATA_HOME") or (Path.home() / ".local" / "share" / "bibi"))


def build_exec(child_argv: list[str], env: dict[str, str]) -> ExecSpec:
    """Child-argv + env → konkrete Popen-Spezifikation, je nach ``BIBI_EXEC_MODE``.

    - ``host`` (Default): das Child direkt, cwd = ``BIBI_JOB_CWD`` (Verzeichnis der
      Schedule-MD innerhalb des Worktrees), Fallback Worktree-Root ohne ``BIBI_JOB_CWD``.
    - ``container``: ``docker run --rm --name bibi-<id> --user <host-uid>:0
      -v <worktree>:/workspace -w /workspace[/<md-relativ>] -e HOME=/root
      -v <data-home>:/root/.local/share/bibi [-e KEY…] <image> <child-argv>``;
      der ganze Worktree bleibt gemountet (Zugriff auf andere Repo-Verzeichnisse
      bleibt möglich), nur der Arbeitsordner (``-w``) zeigt auf den
      ``BIBI_JOB_CWD``-Unterpfad; PATH um das docker-bin-Dir ergänzt
      (Cred-Helper). ``--user <host-uid>:0`` + ``HOME=/root`` (PLAN-24 Befund 5,
      "arbitrary UID"-Konvention): im Bind-Mount geschriebene Dateien gehören
      exakt dem Host-User, GID bleibt immer 0 ("root"-Gruppe) für eine feste
      Identität + passwortloses sudo — gilt genauso für den Daten-Mount
      (``_data_home()``), damit dort geschriebene Dateien nicht root-only werden.
    - ``container`` + ``app``-Typ: zusätzlich ``--network bibi-net`` + statisches
      App-Content-Traefik-Label, falls ``app_port``/``app_prefix`` beim Spawn schon
      feststehen (PLAN-9 §2, Slice 9.0; bereinigt PLAN-11.5 — kein Wrapper-Routing
      mehr, der Wrapper hat keinen HTTP-Server).
    - ``docker_args`` (§7.6a): rohe, unvalidierte zusätzliche ``docker run``-
      Argumente aus dem Job-MD, ganz am Ende der Optionen angehängt (kann
      Vorheriges gezielt überschreiben — Docker nimmt den letzten von
      doppelten Flags). Generischer Escape-Hatch, kein Sicherheitsnetz
      (``--privileged``, zusätzliche Host-Mounts etc. möglich) — s.
      ``vault/CONVENTIONS.md``-Warnung."""
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
    # PLAN-24 Befund 5: "arbitrary UID"-Konvention (OpenShift-Stil) statt
    # dauerhaft als root laufen — --user <host-uid>:0 (GID immer 0/"root",
    # UID variiert pro Host), damit im Bind-Mount geschriebene Dateien exakt
    # dem Host-User gehören (kein chown vor `git commit` nötig) und trotzdem
    # eine feste, /etc/passwd-unabhängige Identität existiert. HOME=/root
    # erzwungen, weil eine fremde UID sonst keinen passenden /etc/passwd-
    # Eintrag hat (s. Dockerfile: /root ist für GID 0 gruppen-beschreibbar).
    # PLAN-24 Befund 5: mit aktiver Job-Image-Persistenz kein --rm — der
    # Container muss nach dem Lauf noch existieren, damit `docker commit`
    # ihn snapshotten kann (finalize_container() räumt danach selbst auf).
    persist = (env.get("BIBI_JOB_IMAGE_PERSIST") or "").strip() == "1"
    data_home = _data_home(env)
    data_home.mkdir(parents=True, exist_ok=True)
    argv = [docker_bin, "run"]
    if not persist:
        argv.append("--rm")
    argv += ["--name", name,
             "--user", f"{_host_uid()}:0",
             "-v", f"{worktree}:{WORKSPACE}", "-w", workdir,
             "-e", "HOME=/root",
             "-v", f"{data_home}:/root/.local/share/bibi"]

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

    # Generischer, UNVALIDIERTER Escape-Hatch (§7.6a, Job-MD `docker_args:`) —
    # rohe zusätzliche `docker run`-Argumente, roh durchgereicht. Bewusst ZULETZT
    # eingefuegt (nach allen oben gesetzten Optionen), damit ein Job-Autor bei
    # Bedarf auch etwas oben Gesetztes gezielt uebersteuern kann (Docker nimmt
    # bei doppelten Flags den letzten). Kein Schutz gegen `--privileged`,
    # zusaetzliche Host-Mounts o. Ae. — siehe CONVENTIONS.md-Warnung.
    docker_args_raw = env.get("BIBI_DOCKER_ARGS")
    if docker_args_raw:
        argv += json.loads(docker_args_raw)

    argv += [image, *child_argv]

    # PATH um das docker-bin-Dir ergänzen, sonst findet docker den Cred-Helper
    # (docker-credential-*) nicht und jeder Image-Pull scheitert.
    run_env = dict(env)
    run_env["PATH"] = str(Path(docker_bin).parent) + os.pathsep + run_env.get("PATH", "")
    return ExecSpec(argv=argv, cwd=None, env=run_env)
