"""Worker: führt zugeteilte Jobs aus (DESIGN §4.5/§7.5; PLAN-3 §3.3).

Ablauf je Run: Worktree ``agent/<slug>`` vorbereiten → Wrapper als **eigenen
Prozess** spawnen (``python -m bibi.wrapper``, env-konfiguriert, §7.5) → auf Exit
warten → Worktree committen (Bibi) → Lifecycle melden. Der Wrapper schreibt
``data/job/{id}/output.jsonl``; der Worker-Daemon serviert daraus die
Stream-Endpunkte (host-process-Phase §7.7 — kein per-Job-HTTP wie im Docker-Bild).

Single-Node: der Worker reserviert **lokal** über ``job_db.reserve_next`` (genau
1 Scheduler im selben Prozess). Der HTTP-Pull (``/-/scheduler/next``) ist der
*Remote*-Pfad und kommt mit ``--connect`` (Stufe 3.6).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

from bibi import config, repo, state
from bibi.daemon import activity, job_db, worktree
from bibi.wrapper import exec_backend, output
from bibi.schedule import backoff, discovery
from bibi.schedule.models import CLAUDE_PAYLOAD_RE as _CLAUDE_RE
from bibi.schedule.models import Status

log = logging.getLogger("bibi.worker")


def _output_path(repo_root: Path, job_id: str) -> Path:
    return repo_root / "data" / "job" / job_id / "output.jsonl"


def _last_activity(out_path: Path, default: float) -> float:
    """Zeitpunkt der jüngsten Output-Zeile (mtime), **nie vor Lauf-Start** (``default``).

    Seit per-Run-Output (``data/job/<slug:fire>/``) startet jeder Lauf mit frischer
    Datei — eine veraltete mtime aus einem Vorlauf kann nicht mehr durchschlagen.
    Der ``default``-Floor bleibt als Robustheit: existiert die Datei noch nicht
    (langsam startender Container), liefert ``stat`` einen Fehler ⇒ Lauf-Start, und
    der ``max`` deckt einen evtl. wiederverwendeten run_id-Pfad mit ab."""
    try:
        return max(out_path.stat().st_mtime, default)
    except OSError:
        return default


def _monitored_wait(
    proc: subprocess.Popen, *, out_path: Path, started: float,
    wall_time: int | None, silence_timeout: int | None, poll: float = 0.1,
    job_id: str | None = None,
) -> tuple[int, str]:
    """Auf den Child warten und dabei wall_time/silence überwachen (§5.5).

    Gibt ``(exit_code, outcome)`` mit ``outcome`` ∈ {``normal``, ``wall_time``,
    ``silence``}. Bei wall_time/silence wird der Lauf terminiert (container-aware)."""
    while proc.poll() is None:
        now = time.time()
        if wall_time and now - started > wall_time:
            _terminate(proc, job_id=job_id)
            return proc.wait(), "wall_time"
        if silence_timeout and now - _last_activity(out_path, started) > silence_timeout:
            _terminate(proc, job_id=job_id)
            return proc.wait(), "silence"
        time.sleep(poll)
    return proc.returncode, "normal"


# ── Container-Exec-Konfig + Terminierung (PLAN-8 Slice B) ────────────────────

def _exec_config() -> dict[str, str]:
    """Container-Exec-Env aus Prozess-Env > Knoten-Config (an den Wrapper gereicht).
    Leer/`host` ⇒ Host-Modus. Inkl. ANTHROPIC_API_KEY für claude-im-Container (D5).
    Alle Einträge aus ~/.config/bibi/env mit Prefix ``BIBI_JOB_ENV_`` werden
    ohne Prefix weitergereicht — damit können beliebige Credentials ohne
    Engine-Änderung in Jobs verfügbar gemacht werden."""
    cfg = config.read_env()
    out: dict[str, str] = {}
    for key in ("BIBI_EXEC_MODE", "BIBI_JOB_IMAGE", "BIBI_DOCKER_BIN",
                "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        val = os.environ.get(key) or cfg.get(key)
        if val:
            out[key] = val
    # Dynamische Job-Env-Vars: BIBI_JOB_ENV_FOO → FOO im Container
    prefix = "BIBI_JOB_ENV_"
    for raw_key, val in {**cfg, **os.environ}.items():
        if raw_key.startswith(prefix) and val:
            out[raw_key[len(prefix):]] = val
    return out


def _is_container() -> bool:
    return (_exec_config().get("BIBI_EXEC_MODE") or "host").strip().lower() == "container"


def _job_is_container(db_path: Path | None, job_id: str) -> bool:
    """Tatsächlicher Exec-Mode dieses Jobs — Schedule-Override, falls in der
    DB gesetzt, sonst der Knoten-Default (``_is_container()``). Bug gefunden
    2026-07-12 (User-Fund, live reproduziert): ``kill()``/``_terminate()``
    prüften bisher nur den Knoten-Default, nie den Job-eigenen ``exec_mode``
    — ein Job mit ``exec_mode: container`` auf einem Host-Default-Knoten
    (z. B. sarasate, kein ``BIBI_EXEC_MODE`` gesetzt) bekam beim KILL nie
    seinen ``docker stop``/``kill``, der Container blieb verwaist laufen
    (verifiziert: docker-run-CLI-Prozess tot, Container weiterhin "Up")."""
    conn = job_db.connect(db_path)
    try:
        info = job_db.get_job_exec_mode(conn, job_id)
    finally:
        conn.close()
    exec_mode = info[1] if info else None
    if exec_mode:
        return exec_mode.strip().lower() == "container"
    return _is_container()


def _docker_env() -> dict[str, str]:
    # docker-bin-Dir in den PATH (Cred-Helper docker-credential-*).
    bin_ = exec_backend.resolve_docker_bin({**os.environ, **config.read_env()})
    env = os.environ.copy()
    env["PATH"] = str(Path(bin_).parent) + os.pathsep + env.get("PATH", "")
    return env


def _docker(args: list[str]) -> None:
    """Best-effort docker-Subkommando (stop/kill) — Fehler dürfen nie hochpropagieren."""
    bin_ = exec_backend.resolve_docker_bin({**os.environ, **config.read_env()})
    try:
        subprocess.run([bin_, *args], capture_output=True, env=_docker_env(),
                       timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _docker_image_exists(bin_: str, image: str) -> bool:
    """Best-effort ``docker image inspect`` — jeder Fehler zählt als "existiert nicht"."""
    try:
        check = subprocess.run(
            [bin_, "image", "inspect", image],
            capture_output=True, env=_docker_env(), timeout=30,
        )
        return check.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_default_image_built(out_path: Path) -> None:
    """PLAN-24 Befund 1: ``bibi-base:dev`` wird nirgends automatisch gebaut oder
    verteilt (bisher ein rein manueller Schritt pro Knoten) — vor dem ersten
    Container-Lauf best-effort prüfen und bei Bedarf synchron bauen, mit
    Feedback-Zeile im Output (User-Auflage: "wir brauchen dann im GUI dringend
    eine Feedback-Zeile im Output, der signalisiert, dass gerade gebaut wird").

    Nur fürs Default-Image — ein Schedule-eigenes ``image:`` bleibt Autors-
    Verantwortung, Auto-Build kennt kein beliebiges fremdes Dockerfile."""
    bin_ = exec_backend.resolve_docker_bin({**os.environ, **config.read_env()})
    if _docker_image_exists(bin_, exec_backend.DEFAULT_IMAGE):
        return
    output.append(out_path, "phase",
                   f"image: {exec_backend.DEFAULT_IMAGE} wird gebaut "
                   f"(kann einige Minuten dauern) …")
    dockerfile = Path(__file__).resolve().parent.parent / "docker" / "bibi-base" / "Dockerfile"
    try:
        build = subprocess.run(
            [bin_, "build", "-t", exec_backend.DEFAULT_IMAGE,
             "-f", str(dockerfile), str(dockerfile.parent)],
            capture_output=True, text=True, env=_docker_env(), timeout=600,
        )
        if build.returncode == 0:
            output.append(out_path, "phase", f"image: {exec_backend.DEFAULT_IMAGE} gebaut.")
        else:
            stderr_tail = (build.stderr or "").strip()[-500:]
            output.append(out_path, "phase",
                           f"image: Bau fehlgeschlagen ({build.returncode}): {stderr_tail}")
    except (OSError, subprocess.SubprocessError) as exc:
        output.append(out_path, "phase", f"image: Auto-Provisioning übersprungen ({exc}).")


def _ensure_job_image(out_path: Path, env: dict[str, str], slug: str) -> None:
    """PLAN-24 Befund 5: ohne expliziten Override (Schedule- oder Knoten-Config-
    ``image:``) bevorzugt jeder Job sein eigenes, über frühere Läufe
    evolvierendes Image (``bibi-job-<slug>:latest``, s.
    ``exec_backend.job_image_tag``/``finalize_container``) — existiert es
    noch nicht (Erstlauf), gilt weiter das Default-Image samt Auto-Build
    (Befund 1). ``BIBI_JOB_IMAGE_PERSIST=1`` signalisiert dem Wrapper, den
    Container ohne ``--rm`` zu starten und nach dem Lauf zu committen."""
    if env.get("BIBI_JOB_IMAGE"):
        return  # expliziter Override (Schedule/Knoten-Config) — Autors-Verantwortung
    env["BIBI_JOB_IMAGE_PERSIST"] = "1"
    bin_ = exec_backend.resolve_docker_bin({**os.environ, **config.read_env()})
    job_tag = exec_backend.job_image_tag(slug)
    if _docker_image_exists(bin_, job_tag):
        env["BIBI_JOB_IMAGE"] = job_tag
    else:
        _ensure_default_image_built(out_path)


def _traefik_dynamic_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "traefik" / "dynamic"


def _register_app_route(job_id: str, port: int) -> None:
    """Traefik-Route für die App eines Jobs registrieren (PLAN-11.4, §7.5/§7.7).

    File-Provider statt Docker-Labels: der App-Port ist erst zur Laufzeit bekannt
    (``app_register``-Signal, ``bibi.job``), Docker-Labels lassen sich an einem
    laufenden Container nicht mehr nachträglich setzen. Host-Modus: Ziel ist der
    lokale Loopback (Worker und App teilen den Host). Container-Modus: Ziel ist
    der Container selbst (``bibi-<id>``), da Traefik im selben Docker-Netz läuft
    (``bibi-net``, PLAN-9 §2)."""
    target = f"bibi-{job_id}:{port}" if _is_container() else f"127.0.0.1:{port}"
    name = f"job-{job_id}-app"
    cfg = {
        "http": {
            "routers": {name: {"rule": f"PathPrefix(`/-/job/{job_id}/app`)", "service": name}},
            "services": {name: {"loadBalancer": {"servers": [{"url": f"http://{target}"}]}}},
        },
    }
    path = _traefik_dynamic_dir(repo.root()) / f"{job_id}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _deregister_app_route(job_id: str) -> None:
    """Traefik-Route beim Job-Ende entfernen (Gegenstück zu ``_register_app_route``)."""
    (_traefik_dynamic_dir(repo.root()) / f"{job_id}.yml").unlink(missing_ok=True)


def _port_holder_pids(port: int) -> list[int]:
    """PIDs, die aktuell auf ``port`` lauschen (best-effort via ``lsof``, leer
    wenn ``lsof`` fehlt/nichts findet/fehlschlägt — nie hart scheitern, der
    eigentliche ``bind()``-Versuch des neuen Prozesses bleibt die Wahrheit)."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for tok in result.stdout.split():
        try:
            pids.append(int(tok))
        except ValueError:
            pass
    return pids


def _free_app_port_host(port: int, out_path: Path) -> None:
    """Host-Mode-Pendant zu ``docker rm -f`` (Container-Cleanup, s.
    ``_run_wrapper``): ein noch auf ``port`` bindender Vorgänger-Prozess (z. B.
    ein ``start_new_session=True``-Wrapper-Kind, das einen Daemon-Neustart
    überlebt hat) blockiert sonst den nächsten Start mit ``OSError: Address
    already in use`` (live beobachtet, PLAN-22 Befund 4). Best-effort: SIGTERM
    an alle Halter, kurz auf Freiwerden warten, dann weiter — kein Backstop-
    SIGKILL nötig, ein neuer ``bind()``-Fehlschlag bleibt für den Aufrufer
    ohnehin sichtbar (Job-Output/Exit-Code)."""
    pids = _port_holder_pids(port)
    if not pids:
        return
    output.append(out_path, "phase", f"port {port}: Vorgänger-Prozess wird beendet …")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
    for _ in range(20):  # bis zu ~2s auf Freiwerden warten
        if not _port_holder_pids(port):
            return
        time.sleep(0.1)


def _terminate(proc: subprocess.Popen, *, job_id: str | None = None,
               is_container: bool | None = None, out_path: Path | None = None) -> None:
    """Lauf beenden. Container (D7): ``docker stop bibi-<id>`` gibt dem Job graceful
    SIGTERM + Frist (eskaliert selbst auf SIGKILL); zusätzlich die Host-Wrapper-Gruppe
    terminieren. Host: SIGTERM → der Wrapper propagiert an den Child (dessen SIGTERM-
    Handler killt die Child-Prozessgruppe). Backstop nach 5 s: SIGKILL an Wrapper.

    ``is_container``: vom Aufrufer explizit aufgelöster Wert (Job-eigener
    ``exec_mode``, s. ``_job_is_container()``) — ``None`` fällt auf den
    Knoten-Default (``_is_container()``) zurück, für Aufrufer ohne Zugriff
    auf den DB-Pfad. Reines Verlassen auf den Knoten-Default hier war der Bug
    (s. ``_job_is_container()``-Docstring): ein Container-Job auf einem
    Host-Default-Knoten bekam nie sein ``docker stop``.

    ``out_path``: User-Fund 2026-07-12 ("ich sehe beim Kill gar nichts im
    Output/Log/Fortschritt") — Start hat Phase-Zeilen (worktree/container/
    wrapper), Teardown bisher keine einzige. Wird ``out_path`` übergeben,
    schreibt diese Funktion symmetrisch dazu Phase-Zeilen für Kill-Start und
    SIGKILL-Eskalation."""
    if is_container is None:
        is_container = _is_container()
    if out_path is not None:
        output.append(out_path, "phase", "kill: wird beendet …")
    if job_id is not None and is_container:
        _docker(["stop", exec_backend.container_name(job_id)])
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    # Backstop: wenn Wrapper nach 5 s noch lebt → SIGKILL (Daemon-Thread, kein Blockieren).
    def _escalate() -> None:
        import time as _t
        _t.sleep(5.0)
        if proc.poll() is not None:
            return
        if out_path is not None:
            output.append(out_path, "phase", "kill: Zeitlimit überschritten — SIGKILL")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    threading.Thread(target=_escalate, daemon=True, name="kill-escalate").start()


def _run_wrapper(
    *, job_id: str, slug: str, kind: str, payload: str, model: str | None = None,
    schedule_ref: str | None = None,
    soul: str | None = None, session: str | None = None,
    wall_time: int | None = None, silence_timeout: int | None = None,
    app_port: int | None = None, app_prefix: str | None = None,
    exec_mode: str | None = None, image: str | None = None,
    defer_time: int | None = None,
    repo_root: Path, work_dir: Path, register=None, ephemeral: bool = False,
    run_id: str | None = None,
    # Detach-Modus: Wrapper-Prozess meldet selbst Terminal-Status + Commit (§9).
    detach: bool = False,
    worker_name: str | None = None, host: str | None = None,
    attempt: int = 0, attempts: int = 1,
    backoff_type: str | None = None,
    scheduler_db_path: str | None = None,  # Direkter DB-Zugriff (kein HTTP)
    scheduler_url: str | None = None,      # HTTP-Reporting (App-Typ / Remote)
) -> tuple[int, str | None, Path, str, int | None]:
    """Worktree → Wrapper-Subprozess → Commit/Report.

    ``detach=True`` (disponierte Jobs): Wrapper läuft eigenständig — kein Wait,
    kein Commit, kein Report im Worker. Wrapper übernimmt alles.
    ``detach=False`` (``run_local``, ephemeral): bisheriger blockierender Pfad.
    ``run_id`` bestimmt den ``output.jsonl``-Pfad (pro Run eindeutig).
    Der Container-Name bleibt an ``job_id`` (Docker-Namensregel, §3.3b).
    5. Rückgabewert: Wrapper-PID (detach) oder ``None`` (nicht-detach)."""
    out_run_id = run_id or job_id
    # out_path zuerst (reine Pfad-Arithmetik, kein I/O außer mkdir) — Startup-
    # Phasen (User-Feedback 2026-07-03: "verschiedene Startup Phasen ... auch
    # wenn der Worker sie produziert") landen so als erste Zeilen im selben
    # output.jsonl, das der Wrapper gleich weiterschreibt. Schlägt eine Phase
    # fehl, existiert die Datei trotzdem schon — execute_reservation()s
    # except-Block kann den Fehler hineinschreiben statt output_ref=None.
    out_path = _output_path(repo_root, out_run_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.append(out_path, "phase", "worktree: wird vorbereitet …")
    wt_path = worktree.prepare(repo_root=repo_root, work_dir=work_dir, slug=slug)
    output.append(out_path, "phase", f"worktree: bereit ({wt_path})")
    activity.emit(log, logging.DEBUG, "worktree.prepare", role="worker",
                  slug=slug, run_id=out_run_id, path=str(wt_path))

    env = os.environ.copy()
    env["BIBI_JOB_ID"] = job_id
    # PLAN-24 Befund 5: der Wrapper-Prozess braucht den Slug fürs per-Job-Image
    # (exec_backend.finalize_container()/job_image_tag()) — vorher stand
    # BIBI_JOB_SLUG nur im Detach-Zweig, für run_local()/ephemeral fehlte es.
    env["BIBI_JOB_SLUG"] = slug
    env["BIBI_OUTPUT_PATH"] = str(out_path)
    env["BIBI_WORKTREE"] = str(wt_path)
    # Job-cwd = Verzeichnis der Schedule-MD (User-Feedback 2026-07-05: ein Job
    # soll dort laufen, wo seine MD liegt, nicht im Worktree-Root — verhindert,
    # dass versehentliche relative Schreibzugriffe im ganzen Repo landen).
    # Zugriff auf andere Repo-Verzeichnisse bleibt möglich, nur der Default
    # ändert sich. ``schedule_ref`` ist relativ zu ``vault/<case_dir>``
    # (§ ``repo.case_dir()`` / ``job_db.rescan``s Default).
    job_cwd = wt_path
    if schedule_ref:
        job_cwd = wt_path / "vault" / repo.case_dir_name() / Path(schedule_ref).parent
    env["BIBI_JOB_CWD"] = str(job_cwd)

    # PLAN-10 Stufe 10.0: claude:-Prefix-Expansion beim Spawn.
    # kind aus DB ist immer "job"; effective_type steuert den Wrapper-Dispatch.
    _claude_m = _CLAUDE_RE.match(payload.strip()) if payload else None
    if _claude_m:
        effective_type = "claude"
        env["BIBI_JOB_TYPE"] = "claude"
        env["BIBI_JOB_PROMPT"] = _claude_m.group(1).strip()
        env["BIBI_CLAUDE_BIN"] = (os.environ.get("BIBI_CLAUDE_BIN")
                                  or config.read_env().get("BIBI_CLAUDE_BIN") or "claude")
        if model:
            env["BIBI_JOB_MODEL"] = model
        if soul:
            env["BIBI_JOB_SOUL"] = soul
        if session:
            env["BIBI_JOB_SESSION"] = session
    else:
        effective_type = "job"
        env["BIBI_JOB_TYPE"] = "job"
        env["BIBI_JOB_CMD"] = payload
        if app_port:
            env["BIBI_APP_PORT"] = str(app_port)
        if app_prefix:
            env["BIBI_APP_PREFIX"] = app_prefix
        if defer_time is not None:
            env["BIBI_DEFER_TIME"] = str(defer_time)

    # Detach-Modus: Commit + Report im Wrapper-Prozess.
    if detach:
        env["BIBI_REPO_ROOT"] = str(repo_root)
        env["BIBI_RUN_ID"] = out_run_id
        if ephemeral:
            env["BIBI_EPHEMERAL"] = "1"
        if wall_time is not None:
            env["BIBI_WALL_TIME"] = str(wall_time)
        if silence_timeout is not None:
            env["BIBI_SILENCE_TIMEOUT"] = str(silence_timeout)
        if worker_name:
            env["BIBI_WORKER_NAME"] = worker_name
        if host:
            env["BIBI_HOST"] = host
        env["BIBI_ATTEMPT"] = str(attempt)
        env["BIBI_ATTEMPTS"] = str(attempts)
        if backoff_type:
            env["BIBI_BACKOFF"] = backoff_type
        # Reporting-Ziel: explizit gesetzt > lokale DB > HTTP-Daemon.
        if scheduler_db_path:
            env["BIBI_SCHEDULER_DB_PATH"] = scheduler_db_path
        elif scheduler_url:
            env["BIBI_SCHEDULER_URL"] = scheduler_url
        elif not env.get("BIBI_SCHEDULER_URL"):
            env["BIBI_SCHEDULER_URL"] = "http://127.0.0.1:8769"

    env.update(_exec_config())
    if exec_mode:
        env["BIBI_EXEC_MODE"] = exec_mode.strip().lower()
    # PLAN-24 Befund 1: ein Schedule-eigenes image: übersteuert die Knoten-
    # weite Default-Konfiguration, analog zum exec_mode-Override direkt
    # darüber — vorher komplett totes Feld (nur in der DB, nie hier gelesen).
    if image:
        env["BIBI_JOB_IMAGE"] = image
    # PLAN-22 Befund 3: _is_container() liest _exec_config() (Knoten-Config)
    # frisch neu und würde einen gerade gesetzten Schedule-Override (Zeile
    # zuvor) ignorieren — hier stattdessen den bereits aufgelösten env-Wert
    # direkt prüfen, damit exec_mode: host im Schedule-MD auch dann gilt,
    # wenn der Knoten global auf container steht.
    if (env.get("BIBI_EXEC_MODE") or "host").strip().lower() == "container":
        output.append(out_path, "phase", "container: alte Instanz wird entfernt …")
        _docker(["rm", "-f", exec_backend.container_name(job_id)])
        _ensure_job_image(out_path, env, slug)
    elif app_port:
        _free_app_port_host(int(app_port), out_path)

    output.append(out_path, "phase", "wrapper: wird gestartet …")
    started = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-m", "bibi.wrapper"],
        env=env, cwd=str(repo_root), start_new_session=True,
    )
    if register is not None:
        register(job_id, proc)

    if detach:
        # Wrapper-Prozess läuft eigenständig — sofort zurückkehren.
        return 0, None, out_path, "detached", proc.pid

    # Blockierender Pfad (run_local / ephemeral): warten, committen, zurückgeben.
    code, outcome = _monitored_wait(
        proc, out_path=out_path, started=started,
        wall_time=wall_time, silence_timeout=silence_timeout, job_id=job_id,
    )
    if register is not None:
        register(job_id, None)

    commit_sha = worktree.commit(worktree=wt_path, message=f"{slug}: run {out_run_id}", slug=slug)
    activity.emit(log, logging.DEBUG, "worktree.commit", role="worker",
                  slug=slug, run_id=out_run_id, commit=(commit_sha or None))
    if ephemeral:
        worktree.remove(repo_root=repo_root, worktree=wt_path)
        activity.emit(log, logging.DEBUG, "worktree.remove", role="worker",
                      slug=slug, run_id=out_run_id)
    return code, commit_sha, out_path, outcome, None


def _retry_fields(reservation: dict) -> dict:
    """``failed``-Statusfelder mit Backoff/attempt++ (Retry; Dauerfehler exhaust→error, §5.5)."""
    attempt = (reservation.get("attempt") or 0) + 1
    base = float(os.environ.get("BIBI_RETRY_BASE") or backoff.DEFAULT_BASE)
    nf = time.time() + backoff.delay(reservation.get("backoff") or "fixed", attempt, base=base)
    return {"status": "failed", "attempt": attempt, "next_fire_at": nf}


def _report_level(status: str) -> int:
    """Log-Level für ein terminales Outcome: Fehlschläge fallen nicht im INFO-Strom unter."""
    if status == "error":
        return logging.ERROR
    if status in ("failed", "killed", "zombie"):
        return logging.WARNING
    return logging.INFO


def execute_reservation(
    reservation: dict, *, repo_root: Path, work_dir: Path, client,
    worker_name: str | None = None, host: str | None = None, register=None,
) -> dict:
    """Einen **disponierten** (reservierten) Job ausführen + via ``client`` melden,
    inkl. Lifecycle-Kanten (§5.5): wall_time→killed, silence→zombie, exit≠0→failed
    (mit Backoff, attempt++). Alle Ausführungs-/Retry-Parameter kommen aus der
    **Reservierung** (so braucht ein Remote-Worker keine lokale DB, §3.6).

    Der Status wird über ``client.report`` gesetzt; ist der Job bereits terminal
    (z. B. ``killed`` per ``/-/job/{id}/kill``), lehnt der Scheduler den Übergang ab
    (``invalid``) und der Worker überschreibt nichts."""
    jid = reservation["id"]
    run_id = job_db.run_id_for(reservation["slug"], jid, reservation.get("fire", 0))
    host = host or socket.gethostname()
    attempt = reservation.get("attempt") or 0
    attempts = reservation.get("attempts") or 1
    # Lokaler DB-Pfad (LocalScheduler): Wrapper kann direkt schreiben statt HTTP.
    # client.db_path kann None sein (kein expliziter Pfad) → Default auflösen.
    # App-Jobs nutzen immer HTTP, damit wrapper_url beim Relay registriert wird.
    from bibi.daemon.scheduler_client import LocalScheduler as _LocalScheduler
    if isinstance(client, _LocalScheduler):
        _resolved_db: str = str(job_db.db_path(client.db_path))
    else:
        _resolved_db = str(client.db_path) if getattr(client, "db_path", None) else ""
    try:
        kind = reservation["kind"]
        silence_timeout = reservation.get("silence_timeout")
        # PLAN-10 Stufe 10.0: SQLite-Direct wenn verfügbar; sonst HTTP.
        _daemon_port = int(os.environ.get("BIBI_DAEMON_PORT", "8769"))
        _sched_db_path: str | None = _resolved_db or None
        _sched_url: str | None = None if _sched_db_path else f"http://127.0.0.1:{_daemon_port}"
        _, _, out_path, outcome, proc_pid = _run_wrapper(
            job_id=jid, slug=reservation["slug"], kind=kind,
            payload=reservation["payload"], model=reservation.get("model"),
            schedule_ref=reservation.get("schedule_ref"),
            soul=reservation.get("soul"), session=reservation.get("session"),
            wall_time=reservation.get("wall_time"),
            silence_timeout=silence_timeout,
            app_port=reservation.get("app_port"),
            app_prefix=reservation.get("app_prefix"),
            exec_mode=reservation.get("exec_mode"),
            image=reservation.get("image"),
            defer_time=reservation.get("defer_time"),
            repo_root=repo_root, work_dir=work_dir, register=register, ephemeral=False,
            run_id=run_id, detach=True,
            worker_name=worker_name, host=host,
            attempt=attempt, attempts=attempts,
            backoff_type=reservation.get("backoff"),
            scheduler_db_path=_sched_db_path,
            scheduler_url=_sched_url,
        )
        if proc_pid is not None and _sched_db_path:
            _pid_conn = job_db.connect(Path(_sched_db_path))
            try:
                job_db.report_pid(
                    _pid_conn, jid, proc_pid, job_db.proc_started_at(proc_pid),
                )
            finally:
                _pid_conn.close()
    except Exception as exc:
        # Setup-Fehler vor Wrapper-Start: Job nicht in `running` hängen lassen.
        activity.emit(log, logging.ERROR, "worker.setup_error",
                      "Setup vor Wrapper-Start fehlgeschlagen", role="worker",
                      slug=reservation.get("slug"), run_id=jid, error=str(exc))
        log.exception("Setup fehlgeschlagen: %s", jid)
        # User-Feedback 2026-07-03: der Fehler soll im Job-Output landen, nicht
        # nur im Daemon-eigenen Log — out_path ist reine Pfad-Arithmetik, hier
        # unabhängig von _run_wrapper() (das ja gerade fehlgeschlagen sein kann,
        # noch bevor es die Datei selbst angelegt hat) neu berechenbar.
        output_ref: str | None = None
        try:
            out_path = _output_path(repo_root, run_id)
            output.append(out_path, "phase", f"setup fehlgeschlagen: {exc}")
            output_ref = out_path.relative_to(repo_root).as_posix()
        except Exception:  # noqa: BLE001 — defensiv, darf das Reporting nicht verhindern
            output_ref = None
        fields = {**_retry_fields(reservation), "exit_code": -1, "output_ref": output_ref,
                  "worker": worker_name, "host": host, "commit_sha": None, "branch": None}
        res = client.report(jid, **fields)
        return {"id": jid, "exit_code": -1, "commit": None,
                "status": fields["status"] if res == "ok" else None,
                "outcome": "setup_error"}

    # Detach: Wrapper läuft selbstständig weiter. Worker kehrt sofort zurück.
    activity.emit(log, logging.INFO, "worker.spawned", role="worker",
                  slug=reservation.get("slug"), run_id=jid, outcome=outcome)
    return {"id": jid, "exit_code": None, "commit": None,
            "status": "running", "outcome": "detached"}


def _resolve_spec(repo_root: Path, slug: str):
    """Eine erfasste Schedule-MD per Slug finden (für ``/run <slug>``).

    Gleicher ``vault_root`` wie ``job_db.rescan()``s Default (§ ``repo.case_dir()``)
    — sonst trägt die zurückgegebene ``ParseResult.schedule_ref`` ein anderes
    Präfix als die von der DB gemeldete (fehlender ``case/``-Teil vs. vorhandener),
    und ein daraus abgeleitetes Job-cwd (§ ``_run_wrapper``) würde für ``/run``
    einen anderen Ort treffen als für den regulären Scheduler-Dispatch."""
    res = discovery.discover(repo_root / "vault" / repo.case_dir_name())
    return res.found.get(slug)


#: Prozess-Handle je laufendem gepinnten ``/run``, keyed nach dem
#: **Bucket-Slug** (dem MD-/Cmd-Slug, nicht dem eindeutigen ``jobs.slug`` je
#: Lauf) — nur für ``local_run_kill()`` gebraucht (User-Fund 2026-07-10:
#: "Da müssen wir dann aber wohl nochmal ran! Natürlich müssen wir kill
#: können"). Ein ``subprocess.Popen`` ist nicht JSON-serialisierbar, darum
#: getrennt von den unten jobs-tabellen-basierten Metadaten-Funktionen.
#:
#: PLAN-28: die eigentliche "läuft gerade?"-Quelle ist jetzt die ``jobs``-
#: Tabelle (``pinned_host``, s. run_pinned()) — der frühere In-Memory-Dict-
#: Ansatz (``_local_runs_live``) hatte einen Prozessgrenzen-Bug: mit
#: ``execute_reservation()``/``detach=True`` meldet der Wrapper-Subprozess
#: seinen Terminal-Status jetzt **selbständig** (SQLite-Direct) — niemand im
#: Daemon-Prozess selbst beobachtet mehr "Lauf fertig", also konnte auch
#: niemand mehr zuverlässig ``local_run_end()`` aufrufen. Ein Slug wäre sonst
#: für immer als "läuft" hängen geblieben. Dieser Proc-Registry hier bleibt
#: bewusst ungereinigt (derselbe Grund) — harmlos: ``proc.poll()`` erkennt
#: einen längst beendeten Prozess trotzdem korrekt, ``local_run_kill()``
#: bricht dann einfach früh ab, statt fälschlich zu killen.
_local_runs_procs: dict[str, subprocess.Popen] = {}

#: Live-Status-Werte (kein Terminalzustand) — deckungsgleich mit dem, was ein
#: gerade tatsächlich laufender Wrapper-Subprozess haben kann.
_PINNED_LIVE_STATUSES = ("running", "awaiting")


def local_run_start(slug: str, job_id: str, output_ref: str, kind: str, payload: str,
                    proc: subprocess.Popen | None = None) -> None:
    if proc is not None:
        _local_runs_procs[slug] = proc


def local_run_end(slug: str) -> None:
    _local_runs_procs.pop(slug, None)


def _pinned_live_row(slug: str, *, db_path: Path | None = None,
                     host: str | None = None) -> sqlite3.Row | None:
    """Die jüngste laufende ``jobs``-Zeile für den **Bucket-Slug** ``slug`` an
    diesem Host, oder ``None`` — Query-Basis für ``local_run_live()``/
    ``local_runs_live()`` (PLAN-28: reale ``jobs``-Zeile statt In-Memory-Dict,
    s. Modul-Kommentar oben). ``jobs.slug`` ist pro Lauf eindeutig
    (``f"{bucket_slug}:{token}"``, s. ``run_pinned()``) — ``LIKE``-Präfix
    matcht alle Läufe desselben Buckets, ``:`` verhindert Fehltreffer wie
    ``"job"`` vs. ``"job2"``."""
    host = host or socket.gethostname()
    conn = job_db.connect(db_path)
    try:
        placeholders = ",".join("?" * len(_PINNED_LIVE_STATUSES))
        return conn.execute(
            f"SELECT * FROM jobs WHERE pinned_host=? AND slug LIKE ? "
            f"AND status IN ({placeholders}) ORDER BY enqueued_at DESC LIMIT 1",
            (host, f"{slug}:%", *_PINNED_LIVE_STATUSES),
        ).fetchone()
    finally:
        conn.close()


def local_run_live(slug: str, *, db_path: Path | None = None,
                   host: str | None = None) -> dict | None:
    """Metadaten des gerade laufenden gepinnten Runs für den Bucket-Slug
    ``slug``, oder ``None`` (PLAN-28: jobs-tabellen-basiert, s. oben)."""
    row = _pinned_live_row(slug, db_path=db_path, host=host)
    if row is None:
        return None
    return {"id": row["id"], "output_ref": row["output_ref"], "kind": row["kind"],
            "payload": row["payload"], "started_at": row["started_at"]}


def local_runs_live(*, db_path: Path | None = None, host: str | None = None) -> dict[str, dict]:
    """Alle aktuell laufenden gepinnten Runs dieses Hosts, ``{bucket_slug:
    {id, started_at, status}}`` (PLAN-28: jobs-tabellen-basiert — löst den
    früheren Prozessgrenzen-Bug des In-Memory-Dicts, s. Modul-Kommentar oben;
    ``status`` kommt jetzt direkt aus der DB-Zeile, kein extra Output-Read
    mehr nötig, anders als das frühere ``local_run_signal_state()``-basierte
    Verfahren)."""
    host = host or socket.gethostname()
    conn = job_db.connect(db_path)
    try:
        placeholders = ",".join("?" * len(_PINNED_LIVE_STATUSES))
        rows = conn.execute(
            f"SELECT slug, id, started_at, status FROM jobs "
            f"WHERE pinned_host=? AND status IN ({placeholders})",
            (host, *_PINNED_LIVE_STATUSES),
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        bucket_slug = r["slug"].rsplit(":", 1)[0]
        out[bucket_slug] = {"id": r["id"], "started_at": r["started_at"], "status": r["status"]}
    return out


def local_run_kill(slug: str) -> bool:
    """Einen laufenden gepinnten Run beenden — dieselbe ``_terminate()`` wie
    ``Worker.kill()`` (container-aware: ``docker stop`` + Host-Signalgruppe,
    SIGKILL-Backstop nach 5s). ``False``, wenn gerade nichts läuft oder kein
    Prozess-Handle vorliegt (Callback nie erreicht, s. app.py::run()).

    PLAN-28: kein ``_local_runs_killed``-Flag mehr nötig — der Wrapper-
    Subprozess meldet "killed"/"by_user" jetzt selbständig via
    ``report_status()`` (derselbe Mechanismus wie beim Scheduler-seitigen
    ``Worker.kill()``, das dieses Flag nie brauchte)."""
    live = local_run_live(slug)
    proc = _local_runs_procs.get(slug)
    if live is None or proc is None or proc.poll() is not None:
        return False
    out_path = repo.root() / live["output_ref"]
    _terminate(proc, job_id=live["id"], out_path=out_path)
    activity.emit(log, logging.INFO, "worker.local_kill", "Lokaler Lauf beendet (graceful)",
                  role="worker", slug=slug, run_id=live["id"])
    return True


def local_run_signal_state(events: list[dict]) -> dict:
    """Leitet HITL-Status/Demand/app_url aus den ``signal``-Event-Zeilen in
    ``output.jsonl`` ab (Ausbau User-Fund 2026-07-10: lokale ``/run``-App-Jobs
    verwarfen awaiting/app_register bisher spurlos, weil ihnen — anders als
    scheduler-dispatchten Jobs — keine ``jobs``-DB-Zeile zum Melden zur
    Verfügung steht, s. ``wrapper/__init__.py::pump()``). Reine Funktion ohne
    eigenen State: jeder Poll wertet die volle bisherige Event-Historie neu
    aus, genau wie ``output_block()`` es für die reine Textausgabe schon tut.
    ``app_url`` bleibt einmal bekannt (awaiting oder app_register) über einen
    running-Übergang hinweg gültig — der Port ändert sich für die Lebensdauer
    des Prozesses nicht; nur ``demand`` wird bei ``running`` wieder geleert."""
    status = "running"
    app_url: str | None = None
    demand: dict | None = None
    for ev in events:
        if ev.get("s") != "signal":
            continue
        try:
            sig = json.loads(ev.get("line", ""))
        except (TypeError, ValueError):
            continue
        name = sig.get("name")
        if name == "running":
            status = "running"
            demand = None
        elif name == "awaiting":
            status = "awaiting"
            demand = {k: v for k, v in sig.items() if k != "name"}
            port = sig.get("port")
            if port:
                app_url = f"http://{config.public_host()}:{port}/"
        elif name == "app_register":
            port = sig.get("port")
            if port:
                app_url = f"http://{config.public_host()}:{port}/"
    return {"status": status, "app_url": app_url, "demand": demand}


def run_local(
    *, slug: str | None = None, cmd: str | None = None, kind: str = "job",
    model: str | None = None, repo_root: Path | None = None,
    work_dir: Path | None = None, db_path: Path | None = None,
    worker_name: str = "local", register=None,
) -> dict:
    """**Lokale** On-Demand-Ausführung (§3.3b). Umgeht den Scheduler vollständig:
    **kein** ``jobs``-Eintrag, **kein** ``/-/scheduler/status`` — nur die lokale
    Journal-Zeile (``domain='local'``) + ``output.jsonl`` bleiben am Knoten.

    Entweder ``slug`` (erfasste MD) **oder** ``cmd`` (ad-hoc, rein lokal)."""
    repo_root = repo_root or repo.root()
    work_dir = work_dir or (repo_root / "data" / "worktrees")
    eff_soul = eff_session = None
    eff_schedule_ref: str | None = None
    eff_app_port = eff_app_prefix = eff_exec_mode = eff_image = None
    if cmd is not None:
        eff_slug, payload, eff_kind, eff_model = slug or "adhoc", cmd, kind, model
    else:
        if not slug:
            raise ValueError("run_local braucht entweder slug oder cmd")
        pr = _resolve_spec(repo_root, slug)
        if pr is None or pr.spec is None:
            raise LookupError(f"kein Schedule mit Slug {slug!r}")
        s = pr.spec
        eff_slug, payload, eff_kind, eff_model = s.slug, s.payload, s.kind.value, s.model
        eff_soul, eff_session = s.soul, s.session
        eff_schedule_ref = pr.schedule_ref
        # Bug gefunden 2026-07-10 (HITL-Test-App-Migration): app_port/
        # app_prefix/exec_mode aus dem Schedule-MD gingen bisher spurlos
        # verloren — run_local() reichte sie nie an _run_wrapper() durch (im
        # Unterschied zu execute_reservation(), dem Scheduler-Dispatch-Pfad,
        # der reservation.get("app_port"/"app_prefix"/"exec_mode") längst
        # korrekt weiterreicht). Betraf jeden App-Typ-Job, der lokal per
        # /run statt über den Scheduler gestartet wurde: kein App-Port im
        # Wrapper-Env, kein Docker-Port-Mapping, kein Traefik-Routing, und
        # ein exec_mode:-Override im MD hatte keine Wirkung (nur die
        # globale Knoten-Config zählte).
        eff_app_port, eff_app_prefix, eff_exec_mode = s.app_port, s.app_prefix, s.exec_mode
        # PLAN-24 Befund 1: image: gleiches Bug-Muster wie app_port/app_prefix/
        # exec_mode oben — ging bislang nur execute_reservation() (Scheduler-
        # Dispatch) mit, run_local() (/run) verlor es spurlos.
        eff_image = s.image

    jid = secrets.token_hex(4)
    started = time.time()
    code, commit_sha, out_path, outcome, _ = _run_wrapper(
        job_id=jid, slug=eff_slug, kind=eff_kind, payload=payload, model=eff_model,
        schedule_ref=eff_schedule_ref,
        soul=eff_soul, session=eff_session,
        app_port=eff_app_port, app_prefix=eff_app_prefix, exec_mode=eff_exec_mode,
        image=eff_image,
        repo_root=repo_root, work_dir=work_dir, register=register, ephemeral=True,
    )
    finished = time.time()
    # PLAN-28: die frühere "wurde per local_run_kill() beendet"-Sonderbehandlung
    # (Flag _local_runs_killed) galt nur, solange die HTTP-Route /-/run selbst
    # run_local() aufrief und dabei local_run_start()/local_run_kill()
    # verdrahtete — das übernimmt inzwischen run_pinned(). run_local() bleibt
    # nur noch der CLI-Pfad (bibi-ctrl run, kein register/Kill-Tracking).
    status, reason = ("complete" if code == 0 else "failed"), None
    rel = out_path.relative_to(repo_root).as_posix()

    conn = job_db.connect(db_path)
    try:
        job_db.write_local_journal(
            conn, run_id=f"{eff_slug}:{jid}", slug=eff_slug, kind=eff_kind,
            status=status, exit_code=code, output_ref=rel, reason=reason,
            host=socket.gethostname(), worker=worker_name,
            started_at=started, finished_at=finished, payload=payload,
        )
    finally:
        conn.close()
    return {"id": jid, "slug": eff_slug, "kind": eff_kind, "status": status,
            "exit_code": code, "output_ref": rel, "commit": commit_sha}


def run_pinned(
    *, slug: str | None = None, cmd: str | None = None, kind: str = "job",
    model: str | None = None, repo_root: Path | None = None,
    work_dir: Path | None = None, db_path: Path | None = None,
    worker_name: str | None = None, host: str | None = None,
    attempts: int = 1, register=None,
) -> dict:
    """**Lokale** On-Demand-Ausführung mit voller Scheduler-Lifecycle (PLAN-28).

    Nachfolger von ``run_local()``s Einstiegspunkt für ``/-/run`` — anders als
    dort bekommt der Lauf jetzt eine echte ``jobs``-Zeile (``pinned_host`` =
    dieser Host, s. ``reserve_next()``s ``pinned_only``-Filter), läuft also
    durch dieselbe Retry/Error/Deferred/Zombie-Maschine wie ein Scheduler-Job
    (``report_status()``, ``Sweeper``/``LocalPinnedLoop``). Beide bisherigen
    ``/run``-Garantien bleiben erhalten: **hier** (``pinned_host`` erzwingt
    genau diesen Knoten, kein anderer Worker kann die Zeile je reservieren)
    und **sofort** (kein Warten auf einen Poll-Tick — die Zeile wird synchron
    im selben Aufruf reserviert + über ``execute_reservation()`` dispatcht,
    das mit ``detach=True`` fast augenblicklich zurückkehrt, während der
    Wrapper-Subprozess eigenständig weiterläuft und terminale Status via
    SQLite-Direct selbst meldet — kein Netz nötig, funktioniert offline).

    Entweder ``slug`` (erfasste MD) **oder** ``cmd`` (ad-hoc, rein lokal).
    ``attempts`` (Default 1, wie das bisherige ``/run``-Verhalten): >1 aktiviert
    echtes Retry-mit-Backoff, gefangen vom ``LocalPinnedLoop`` (nicht von
    diesem Aufruf selbst — der deckt nur den ersten Versuch ab)."""
    repo_root = repo_root or repo.root()
    work_dir = work_dir or (repo_root / "data" / "worktrees")
    host = host or socket.gethostname()
    worker_name = worker_name or host
    eff_soul = eff_session = None
    eff_schedule_ref: str | None = None
    eff_app_port = eff_app_prefix = eff_exec_mode = eff_image = None
    if cmd is not None:
        eff_slug, payload, eff_kind, eff_model = slug or "adhoc", cmd, kind, model
    else:
        if not slug:
            raise ValueError("run_pinned braucht entweder slug oder cmd")
        pr = _resolve_spec(repo_root, slug)
        if pr is None or pr.spec is None:
            raise LookupError(f"kein Schedule mit Slug {slug!r}")
        s = pr.spec
        eff_slug, payload, eff_kind, eff_model = s.slug, s.payload, s.kind.value, s.model
        eff_soul, eff_session = s.soul, s.session
        eff_schedule_ref = pr.schedule_ref
        eff_app_port, eff_app_prefix, eff_exec_mode = s.app_port, s.app_prefix, s.exec_mode
        eff_image = s.image

    # Eindeutiger jobs.slug (UNIQUE-Constraint) — unabhängig vom MD-/Cmd-Slug,
    # sonst kollidiert ein zweiter ▶ Start mit der noch nicht aufgeräumten
    # Zeile des ersten Laufs (analog zur run_id-Konvention in
    # write_local_journal(), nur hier als eigenständige Job-Identität).
    unique_slug = f"{eff_slug}:{secrets.token_hex(4)}"
    now = time.time()
    jid = secrets.token_hex(4)
    conn = job_db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, schedule_ref, kind, payload, model, soul, "
            "session, app_port, app_prefix, exec_mode, image, schedule, next_fire_at, "
            "attempts, pinned_host, status, enqueued_at) VALUES "
            "(:id, :slug, :schedule_ref, :kind, :payload, :model, :soul, :session, "
            ":app_port, :app_prefix, :exec_mode, :image, 'now', :now, "
            ":attempts, :pinned_host, 'pending', :now)",
            {"id": jid, "slug": unique_slug, "schedule_ref": eff_schedule_ref or unique_slug,
             "kind": eff_kind, "payload": payload, "model": eff_model, "soul": eff_soul,
             "session": eff_session, "app_port": eff_app_port, "app_prefix": eff_app_prefix,
             "exec_mode": eff_exec_mode, "image": eff_image, "now": now,
             "attempts": attempts, "pinned_host": host},
        )
        reservation = job_db.reserve_next(conn, host=host, pinned_only=True, now=now)
    finally:
        conn.close()
    if reservation is None:  # unter BEGIN IMMEDIATE eigentlich unerreichbar
        raise RuntimeError(f"gepinnter Job {unique_slug!r} konnte nicht reserviert werden")

    from bibi.daemon.scheduler_client import LocalScheduler
    execute_reservation(
        reservation, repo_root=repo_root, work_dir=work_dir,
        client=LocalScheduler(db_path), worker_name=worker_name, host=host, register=register,
    )
    # Derselbe run_id/Output-Pfad-Aufbau wie execute_reservation() intern
    # nutzt (job_db.run_id_for() + _output_path()) — muss identisch sein,
    # sonst zeigt die Response auf eine Datei, die der Wrapper nie schreibt
    # (run_id_for()s eigener Docstring warnt explizit davor).
    run_id = job_db.run_id_for(unique_slug, reservation["id"], reservation.get("fire", 0))
    output_ref = _output_path(repo_root, run_id).relative_to(repo_root).as_posix()
    return {"id": reservation["id"], "slug": unique_slug, "kind": eff_kind,
            "output_ref": output_ref}


class Worker:
    """Async-Loop, der reservierte Jobs ausführt (im Daemon-Lifespan gestartet)."""

    def __init__(
        self, *, repo_root: Path | None = None, work_dir: Path | None = None,
        db_path: Path | None = None, poll_interval: float = 1.0,
        worker_name: str | None = None, autopoll: bool = True,
        client=None, connect: bool = False, scheduler_url: str | None = None,
        secret: str | None = None,
        max_concurrent: int = 4,
    ) -> None:
        self.repo_root = repo_root
        self.work_dir = work_dir
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.worker_name = worker_name or socket.gethostname()
        self.host = socket.gethostname()
        # autopoll=False: nur die Routen (Streams/kill) bedienen, kein Pull-Loop —
        # für Tests und für reine Stream-Knoten ohne lokale Ausführung.
        self.autopoll = autopoll
        self.max_concurrent = max_concurrent
        # Scheduler-Client: lokal (Single-Node) oder remote (--connect, §3.6).
        if client is not None:
            self.client = client
        elif connect:
            from bibi.daemon.scheduler_client import RemoteScheduler
            self.client = RemoteScheduler(scheduler_url or "http://127.0.0.1:8769", secret=secret)
        else:
            from bibi.daemon.scheduler_client import LocalScheduler
            self.client = LocalScheduler(db_path)
        self._procs: dict[str, subprocess.Popen] = {}
        self._app_routes: dict[str, int] = {}  # job_id → zuletzt registrierter app_port
        self._task: asyncio.Task | None = None
        self._running = False
        self._maint_active = False  # Wartungs-Übergang nur einmal loggen (kein Tick-Spam)

    def _roots(self) -> tuple[Path, Path]:
        root = self.repo_root or repo.root()
        work = self.work_dir or (root / "data" / "worktrees")
        return root, work

    def output_path(self, job_id: str) -> Path:
        """``output.jsonl``-Pfad des **aktuellen** Laufs eines Jobs (Live-Routen).

        Löst die stabile ``job_id`` über die DB auf den laufenden run_id
        (``run_id_for()``) auf — so zeigt ``/-/job/{id}/…`` immer nur den
        jüngsten Lauf (Historie früherer Läufe läuft über ``output_ref`` im
        Journal). Ist die ID unbekannt (z. B. ephemerer ``/run``), bleibt sie
        selbst der Pfad."""
        root, _ = self._roots()
        conn = job_db.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT slug, fire FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        run_id = job_db.run_id_for(row["slug"], job_id, row["fire"]) if row else job_id
        return _output_path(root, run_id)

    def _register(self, job_id: str, proc: subprocess.Popen | None) -> None:
        if proc is None:
            self._procs.pop(job_id, None)
        else:
            self._procs[job_id] = proc

    def _poll_app_routes(self) -> None:
        """``app_port``-Änderungen aktiver Jobs in Traefik-Routen übersetzen
        (PLAN-11.4, §7.5/§7.7) — Gegenstück zum stdout-``app_register``-Signal
        (``bibi.job``, von ``_handle_signal`` in ``job_db.app_port`` geschrieben).
        „Aktiv" = hat einen laufenden Prozess (``running``/``awaiting``), nicht
        bloß „nicht terminal" (``pending``/``failed``/``deferred`` haben keinen).
        Jobs ohne laufenden Prozess deregistrieren ihre Route wieder."""
        conn = job_db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, app_port, status FROM jobs WHERE app_port IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        # Nicht terminal ≠ hat einen laufenden Prozess: pending (noch nicht
        # gestartet) und failed/deferred (Prozess schon beendet, wartet auf
        # Retry) sind ebenfalls nicht-terminal, haben aber keinen App-Server.
        live = {row["id"]: row["app_port"] for row in rows
                if Status(row["status"]) in (Status.RUNNING, Status.AWAITING)}
        for jid, port in live.items():
            if self._app_routes.get(jid) != port:
                _register_app_route(jid, port)
                self._app_routes[jid] = port
        for jid in [j for j in self._app_routes if j not in live]:
            _deregister_app_route(jid)
            self._app_routes.pop(jid, None)

    def tick_once(self) -> bool:
        """Einen Job über den Client reservieren + ausführen. ``False`` = nichts zu tun.

        Wartungsmodus (§ daemon-weit): pausiert das Reservieren neuer Jobs. Der
        Übergang wird **einmal** geloggt (nicht je Tick → kein Spam)."""
        self._poll_app_routes()
        if state.get_maintenance():
            if not self._maint_active:
                self._maint_active = True
                activity.emit(log, logging.INFO, "worker.maintenance",
                              "Wartungsmodus aktiv — keine neuen Jobs reserviert",
                              role="worker")
            return False
        if self._maint_active:
            self._maint_active = False
            activity.emit(log, logging.INFO, "worker.resumed",
                          "Wartungsmodus beendet — Dispatch wieder aktiv", role="worker")
        # Beendete Wrapper-Procs aus _procs entfernen (Slot-Freigabe).
        for jid in [jid for jid, p in list(self._procs.items()) if p.poll() is not None]:
            self._procs.pop(jid, None)
        if len(self._procs) >= self.max_concurrent:
            return False  # Slot voll — nächster Tick versucht es erneut
        res = self.client.next(worker=self.worker_name, host=self.host)
        if res is None:
            return False
        root, work = self._roots()
        activity.emit(log, logging.INFO, "worker.pickup", role="worker",
                      slug=res.get("slug"), run_id=res.get("id"), kind=res.get("kind"))
        try:
            execute_reservation(
                res, repo_root=root, work_dir=work, client=self.client,
                worker_name=self.worker_name, host=self.host, register=self._register,
            )  # Detach: kehrt sofort zurück; Wrapper läuft eigenständig.
        except Exception:
            activity.emit(log, logging.ERROR, "worker.error",
                          "Job-Setup fehlgeschlagen", role="worker",
                          slug=res.get("slug"), run_id=res.get("id"))
            log.exception("Job-Setup fehlgeschlagen: %s", res.get("id"))
        return True

    def kill(self, job_id: str) -> bool:
        """Lauf beenden — container-aware (PLAN-8 D7): ``docker stop`` (graceful) +
        Host-Wrapper-Gruppe; im Container-Modus auch dann ``docker kill`` als Backstop,
        wenn der Wrapper schon weg ist (Container könnte verwaist weiterlaufen).

        Container-Erkennung nutzt den Job-eigenen ``exec_mode`` (Schedule-
        Override), nicht nur den Knoten-Default — Bug gefunden 2026-07-12
        (s. ``_job_is_container()``): auf einem Host-Default-Knoten (z. B.
        sarasate) bekam ein Container-Job beim KILL nie sein ``docker
        stop``/``kill``, der Container blieb verwaist laufen."""
        is_container = _job_is_container(self.db_path, job_id)
        out_path = self.output_path(job_id)
        proc = self._procs.get(job_id)
        if proc is not None and proc.poll() is None:
            _terminate(proc, job_id=job_id, is_container=is_container, out_path=out_path)
            activity.emit(log, logging.INFO, "worker.kill", "Lauf beendet (graceful)",
                          role="worker", run_id=job_id)
            return True
        if is_container:  # Wrapper weg, Container evtl. noch da → einsammeln
            output.append(out_path, "phase", "kill: verwaister Container wird entfernt …")
            _docker(["kill", exec_backend.container_name(job_id)])
            return True
        # Kein In-Memory-Handle (z. B. Job hat einen Daemon-Neustart überlebt,
        # reconcile_startup_orphans() lässt ihn dann bewusst running) — PID aus
        # der DB reanimieren. Nur SIGTERM, kein SIGKILL-Backstop-Timer wie in
        # _terminate(): dafür fehlt hier der Popen-Handle zum .wait().
        conn = job_db.connect(self.db_path)
        try:
            pid_info = job_db.get_pid(conn, job_id)
        finally:
            conn.close()
        if pid_info is None:
            return False
        pid, pid_started_at = pid_info
        if job_db.proc_started_at(pid) != pid_started_at:
            return False
        output.append(out_path, "phase", "kill: wird beendet (graceful, nach Neustart) …")
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return False
        activity.emit(log, logging.INFO, "worker.kill",
                      "Lauf beendet (graceful, DB-PID nach Neustart)",
                      role="worker", run_id=job_id)
        return True

    def rebuild_job_image(self, slug: str, out_path: Path | None = None) -> bool:
        """PLAN-24 Befund 5, REBUILD-Aktion: das per-Job-Image verwerfen — der
        nächste Container-Lauf dieses Jobs fällt automatisch auf das Default-
        Image zurück (derselbe Fallback wie beim allerersten Lauf, kein
        Sonderfall). Bewusst getrennt von START/RESET (User-Klärung, PLAN-24:
        beide sollen das per-Job-Image NIE implizit antasten). Ein bereits
        fehlendes Tag zählt als Erfolg (Ziel schon erreicht) — ``False`` nur,
        wenn das Docker-Kommando selbst technisch fehlschlägt.

        ``out_path``: User-Fund 2026-07-12 ("ich habe nicht den Eindruck,
        dass REBUILD einen Effekt hat. Es zeigt auch keinen Output/Log/
        Fortschritt") — bisher lieferte die Route nur ein stilles HTTP-200,
        die re-gerenderte Live-Ansicht sah davor/danach identisch aus (REBUILD
        ändert ja keinen Job-Status). Mit ``out_path`` schreibt diese Methode
        Phase-Zeilen in dieselbe ``output.jsonl``, die die Live-/Output-
        Ansicht ohnehin schon zeigt — sichtbare Bestätigung statt Stille,
        inklusive der Unterscheidung "Image existierte gar nicht" (echtes
        No-op) vs. "wurde entfernt"."""
        bin_ = exec_backend.resolve_docker_bin({**os.environ, **config.read_env()})
        tag = exec_backend.job_image_tag(slug)
        existed = _docker_image_exists(bin_, tag)
        if out_path is not None:
            if existed:
                output.append(out_path, "phase", f"REBUILD: {tag} wird verworfen …")
            else:
                output.append(out_path, "phase",
                              f"REBUILD: {tag} existiert nicht — nichts zu tun")
        try:
            subprocess.run([bin_, "rmi", "-f", tag], capture_output=True,
                           env=_docker_env(), timeout=60, check=False)
            if out_path is not None and existed:
                output.append(out_path, "phase",
                              "REBUILD: erledigt — nächster Lauf startet vom Default-Image")
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            if out_path is not None:
                output.append(out_path, "phase", f"REBUILD: docker-Kommando fehlgeschlagen ({exc})")
            return False

    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                did = await loop.run_in_executor(None, self.tick_once)
            except Exception:
                log.exception("Worker-Tick fehlgeschlagen")
                did = False
            if not did:
                await asyncio.sleep(self.poll_interval)

    async def start(self) -> None:
        if not self.autopoll:
            return  # nur Routen bedienen, kein Pull-Loop
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
