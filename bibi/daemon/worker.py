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
from bibi.schedule.models import (
    DEFAULT_SILENCE_TIMEOUT,
    DEFAULT_SILENCE_TIMEOUT_JOB,
    Status,
    is_claude_payload,
    job_uid,
)

log = logging.getLogger("bibi.worker")


def _output_path(repo_root: Path, job_id: str) -> Path:
    return repo_root / "data" / "job" / job_id / "output.jsonl"


def output_path_of(row, repo_root: Path) -> Path:
    """``output.jsonl`` einer Job-Zeile: **erst ``output_ref``, dann die
    Neuberechnung** aus ``slug``/``fire``/``id``.

    Die eine Stelle für diese Reihenfolge — beide Richtungen sind je einmal
    falsch gebaut worden, und zwar am selben Tag (2026-08-04):

    * Nur neu rechnen (``Worker.output_path()``) verfehlt jeden Lauf von vor
      ``run_id_for()`` (2026-07-01), dessen Datei unter der blossen ``job_id``
      liegt — die Kachel meldete ``output unavailable``, obwohl die Datei lag.
    * Nur den Verweis lesen (``controller``s Slot-Output) verfehlt jeden
      **laufenden** Lauf, denn dort ist die Spalte immer ``NULL``: der Wrapper
      füllt sie erst beim Terminal-Report. `burndown-app` lief seit einem Tag,
      239 Zeilen Ausgabe lagen da, der Screen sagte ``(no output yet)``.

    ``row`` ist alles, was ``["output_ref"]``/``["slug"]``/``["fire"]``/``["id"]``
    beantwortet — eine ``sqlite3.Row`` ebenso wie ein ``dict``.
    """
    try:
        ref = row["output_ref"]
    except (KeyError, IndexError):  # sqlite3.Row wirft IndexError, dict KeyError
        ref = None
    if ref:
        return repo_root / ref
    run_id = job_db.run_id_for(row["slug"] or "", row["id"], row["fire"] or 0)
    return _output_path(repo_root, run_id)


def _write_inplace_seed(run_dir: Path) -> Path | None:
    """PLAN-38 Stufe 2: Arbeitsstand-Schnappschuss vor einem In-place-Lauf ablegen.

    Nur bei ``auto_sync: on``. Ist Auto-Sync aus, bleibt das Ergebnis ohnehin
    als ``modified``/``untracked`` liegen (die Zusage von PLAN-38) — dann gibt
    es nichts zu committen und der Schnappschuss wäre reine Arbeit. Rückgabe:
    Pfad der Datei, die ``wrapper._commit_worktree()`` liest und wieder löscht,
    sonst ``None``.

    Ein Fehler hier darf einen Lauf nie verhindern (§2.7): ohne Schnappschuss
    läuft der Job normal weiter, das Ergebnis nimmt dann wie bisher der
    Auto-Sync-Debouncer mit (nur ohne Job-Provenienz im Commit).
    """
    try:
        from bibi import git_ops
        if not state.get_auto_sync():
            return None
        path = run_dir / "inplace-seed.json"
        path.write_text(json.dumps(git_ops.snapshot_worktree()), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — nie den Lauf blockieren
        log.warning("in-place-Seed konnte nicht geschrieben werden", exc_info=True)
        return None


# ── Container-Exec-Konfig + Terminierung (PLAN-8 Slice B) ────────────────────

#: PLAN-32 Stufe 32.0 (Config-Restrukturierung): CLAUDE_CODE_OAUTH_TOKEN/
#: ANTHROPIC_API_KEY sind keine hart codierten Sonderfälle mehr, sondern
#: wandern unter dieselbe BIBI_JOB_ENV_-Namenskonvention wie jedes andere
#: Job-Credential — bare Namen bleiben Fallback für bestehende Deployments
#: (sarasate, Mac), s. _exec_config().
_LEGACY_JOB_ENV_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def _exec_config() -> dict[str, str]:
    """Container-Exec-Env aus Prozess-Env > Knoten-Config (an den Wrapper gereicht).
    Leer/`host` ⇒ Host-Modus. Alle Einträge aus ~/.config/bibi/env mit Prefix
    ``BIBI_JOB_ENV_`` werden ohne Prefix weitergereicht — damit können
    beliebige Credentials ohne Engine-Änderung in Jobs verfügbar gemacht
    werden. CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_API_KEY nutzen seit PLAN-32
    Stufe 32.0 genau dasselbe Präfix (``BIBI_JOB_ENV_ANTHROPIC_API_KEY`` etc.)
    — der bare Name ohne Präfix bleibt als Fallback gültig (Migration, s.
    ``hygiene.check_legacy_job_env_names()`` für die zugehörige doctor-Warnung),
    wird aber nicht mehr aktiv beworben. Seit Stufe 32.2 fließen zusätzlich
    vom Host verteilte Werte ein (``config.read_distributed_env()``) — mit
    niedrigster Präzedenz: ein lokal in ``env`` gesetzter oder im
    Prozess-Environment vorhandener gleichnamiger Wert gewinnt immer
    (Entscheidung 4, „lokal gewinnt")."""
    cfg = config.read_env()
    out: dict[str, str] = {}
    for key in ("BIBI_EXEC_MODE", "BIBI_JOB_IMAGE", "BIBI_DOCKER_BIN"):
        val = os.environ.get(key) or cfg.get(key)
        if val:
            out[key] = val
    # Dynamische Job-Env-Vars: BIBI_JOB_ENV_FOO → FOO im Container. Reihenfolge
    # (verteilt < lokale env < Prozess-Env) setzt "lokal gewinnt immer" um.
    prefix = "BIBI_JOB_ENV_"
    merged = {**config.read_distributed_env(), **cfg, **os.environ}
    for raw_key, val in merged.items():
        if raw_key.startswith(prefix) and val:
            out[raw_key[len(prefix):]] = val
    # Fallback für bare CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_API_KEY (Migration) —
    # nur wenn die präfigierte Form nicht schon oben gegriffen hat.
    for legacy_key in _LEGACY_JOB_ENV_KEYS:
        if legacy_key in out:
            continue
        val = merged.get(legacy_key)
        if val:
            out[legacy_key] = val
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


def base_job_env() -> dict[str, str]:
    """Die Grundumgebung jedes Jobs: die Sitzung, abzüglich dessen, was sie
    nicht weitergeben darf (m.rau/bibi#89).

    ``VIRTUAL_ENV`` reiste über ``os.environ.copy()`` mit. Den Zweck aus `#76`
    — dass ein Job das venv der Engine findet — trägt der ``PATH`` allein; die
    Variable war die Zugabe. Sie kostet drei Warnungen pro ``BrowserCI``-Lauf,
    weil jedes ``uv`` in einem *fremden* Checkout sie meldet, und jede sieht
    bei einer Fehlersuche nach einer Spur aus, die keine ist.

    Gestrichen wird eine Variable, nicht eine Klasse davon: ``HOME``, ``PATH``
    und die verteilten ``BIBI_JOB_ENV_*``-Werte bleiben, ein Job braucht sie.
    """
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    return env


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
    docker_args: list[str] | None = None,
    defer_time: int | None = None, error_time: int | None = None,
    repo_root: Path, work_dir: Path, register=None, ephemeral: bool = False,
    in_place: bool = False,
    run_id: str | None = None,
    worker_name: str | None = None, host: str | None = None,
    attempt: int = 0, attempts: int = 1,
    backoff_type: str | None = None,
    scheduler_db_path: str | None = None,  # Direkter DB-Zugriff (kein HTTP)
    scheduler_url: str | None = None,      # HTTP-Reporting (App-Typ / Remote)
) -> tuple[Path, int]:
    """Worktree vorbereiten → Wrapper-Subprozess spawnen → ``(out_path, pid)``.

    **Der Wrapper läuft immer eigenständig** — kein Wait, kein Commit, kein
    Report im Worker; er übernimmt alles selbst (§9).

    Hier stand bis ``v0.7.6`` ein ``detach``-Schalter mit Default ``False`` und
    darunter ein zweiter, blockierender Pfad: warten, ``_monitored_wait()``,
    committen, Worktree entfernen. **Er hatte im ganzen Baum keinen Aufrufer,
    der ihn genommen hätte** — jede Aufrufstelle, Produktion wie Tests, gab
    ``detach=True`` mit. Am Leben hielten ihn nur die Tests, die den Parameter
    pflichtschuldig setzten.

    Gefunden wurde er beim Verdrahten von ``last_ping_at`` (#76): dessen
    Anforderung *„``_last_activity()`` liest die Spalte statt der mtime"* zeigte
    genau dorthin. Sie zu erfüllen hätte geheißen, einen Leser umzustellen, den
    niemand ruft — derselbe Fehler eine Ebene tiefer, den #76 an der Spalte
    selbst anprangert. Mit dem Zweig sind ``_monitored_wait()`` und
    ``_last_activity()`` entfallen; **die mtime von ``output.jsonl`` ist damit
    nirgends mehr ein Aktivitätsmaß**, und die Silence-Frist hat nur noch eine
    Quelle: den Wrapper, der sie sieht, und die Spalte, in die er sie schreibt.

    ``run_id`` bestimmt den ``output.jsonl``-Pfad (pro Run eindeutig).
    Der Container-Name bleibt an ``job_id`` (Docker-Namensregel, §3.3b)."""
    out_run_id = run_id or job_id
    # out_path zuerst (reine Pfad-Arithmetik, kein I/O außer mkdir) — Startup-
    # Phasen (User-Feedback 2026-07-03: "verschiedene Startup Phasen ... auch
    # wenn der Worker sie produziert") landen so als erste Zeilen im selben
    # output.jsonl, das der Wrapper gleich weiterschreibt. Schlägt eine Phase
    # fehl, existiert die Datei trotzdem schon — execute_reservation()s
    # except-Block kann den Fehler hineinschreiben statt output_ref=None.
    out_path = _output_path(repo_root, out_run_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # in_place (User-Fund 2026-07-14, bibi-ctrl test): kein frischer Checkout
    # von trunk — direkt gegen repo_root, dirty tree eingeschlossen. wt_path
    # bleibt dieselbe Variable wie im Normalfall, damit alles Nachgelagerte
    # (BIBI_WORKTREE, job_cwd-Ableitung unten, exec_backend.build_exec()s
    # Container-Mount über BIBI_WORKTREE) unverändert funktioniert — der
    # Unterschied ist nur, WAS wt_path ist, nicht wie es benutzt wird.
    seed_ref: Path | None = None
    if in_place:
        wt_path = repo_root
        output.append(out_path, "phase", "worktree: übersprungen (in-place, lokaler Stand)")
        seed_ref = _write_inplace_seed(out_path.parent)
        if seed_ref is not None:
            output.append(out_path, "phase",
                          "auto_sync: an — Ergebnis wird nach dem Lauf committet")
    else:
        output.append(out_path, "phase", "worktree: wird vorbereitet …")
        wt_path = worktree.prepare(repo_root=repo_root, work_dir=work_dir, slug=slug)
        output.append(out_path, "phase", f"worktree: bereit ({wt_path})")
    activity.emit(log, logging.DEBUG, "worktree.prepare", role="worker",
                  slug=slug, run_id=out_run_id, path=str(wt_path), in_place=in_place)

    env = base_job_env()
    env["BIBI_JOB_ID"] = job_id
    # PLAN-24 Befund 5: der Wrapper-Prozess braucht den Slug fürs per-Job-Image
    # (exec_backend.finalize_container()/job_image_tag()) — vorher stand
    # BIBI_JOB_SLUG nur im Detach-Zweig, im nicht-detachten Pfad fehlte es.
    env["BIBI_JOB_SLUG"] = slug
    env["BIBI_OUTPUT_PATH"] = str(out_path)
    env["BIBI_WORKTREE"] = str(wt_path)
    if in_place:
        env["BIBI_IN_PLACE"] = "1"
        if seed_ref is not None:
            # PLAN-38 Stufe 2: nur gesetzt, wenn auto_sync an ist — der Wrapper
            # committet dann am Ende genau die Pfade, die sich gegenüber diesem
            # Schnappschuss geändert haben (wrapper._commit_worktree()).
            env["BIBI_INPLACE_SEED"] = str(seed_ref)
    # Job-cwd = Verzeichnis der Schedule-MD (User-Feedback 2026-07-05: ein Job
    # soll dort laufen, wo seine MD liegt, nicht im Worktree-Root — verhindert,
    # dass versehentliche relative Schreibzugriffe im ganzen Repo landen).
    # Zugriff auf andere Repo-Verzeichnisse bleibt möglich, nur der Default
    # ändert sich. ``schedule_ref`` ist relativ zu ``vault/<case_dir>``
    # (§ ``repo.case_dir()`` / ``job_db.rescan``s Default).
    #
    # Bug gefunden (2026-07-13, User-Fund: bibi-ctrl run hing erneut, diesmal
    # mit exit_code=1): ``run_pinned()`` braucht für ad-hoc ``cmd=``-Läufe
    # (kein echtes Schedule-MD) trotzdem einen ``schedule_ref``-Wert für die
    # ``jobs``-Zeile (Spalte ``NOT NULL``) und setzt dafür ``unique_slug`` ein
    # (kein echter Vault-Pfad) — dieser synthetische Wert landete unverändert
    # hier und ergab einen ``job_cwd``, den es im frischen Worktree nie gibt
    # (``vault/case/`` selbst ist ohnehin nie committet, git kennt keine
    # leeren Verzeichnisse). ``exec_backend`` reichte das direkt als
    # Subprozess-``cwd`` durch → ``FileNotFoundError`` im Wrapper, der Job
    # blieb ohne Terminal-Report auf ``failed`` hängen. Fix: nur einen
    # tatsächlich existierenden Pfad verwenden, sonst Worktree-Root (wie
    # zuvor für ``schedule_ref=None``).
    job_cwd = wt_path
    if schedule_ref:
        candidate = wt_path / "vault" / repo.case_dir_name() / Path(schedule_ref).parent
        if candidate.is_dir():
            job_cwd = candidate
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
        if error_time is not None:
            env["BIBI_ERROR_TIME"] = str(error_time)

    # Commit + Report macht der Wrapper-Prozess selbst (§9).
    env["BIBI_REPO_ROOT"] = str(repo_root)
    env["BIBI_RUN_ID"] = out_run_id
    # Wohin der Aktivitäts-Reporter des Wrappers `last_ping_at` schreibt (#76).
    # Bewusst eine eigene Variable neben BIBI_SCHEDULER_DB_PATH, obwohl beide
    # auf dieselbe Datei zeigen: die trägt den **Terminal-Report**, und die
    # beiden Bedeutungen zusammenzulegen hieße, sie nie wieder trennen zu
    # können (s. wrapper._ping_monitors()).
    if scheduler_db_path:
        env["BIBI_PING_DB_PATH"] = scheduler_db_path
    # Eigene, von "ephemeral" unabhängige Absicherung (User-Fund 2026-07-14,
    # Plan-Review zu bibi-ctrl test): in_place heißt wt_path is repo_root —
    # BIBI_EPHEMERAL darf hier NIE gesetzt werden, egal was der Aufrufer für
    # ephemeral übergibt (run_pinned() erzwingt zwar schon ephemeral=False
    # bei in_place, aber diese Zeile verlässt sich nicht darauf, dass jeder
    # künftige Aufrufer das korrekt macht — dritte, unabhängige Schicht
    # neben run_pinned()s ephemeral=not in_place und worktree.remove()s
    # eigenem worktree==repo_root-Guard).
    if ephemeral and not in_place:
        env["BIBI_EPHEMERAL"] = "1"
    if wall_time is not None:
        env["BIBI_WALL_TIME"] = str(wall_time)
    if silence_timeout is not None:
        env["BIBI_SILENCE_TIMEOUT"] = str(silence_timeout)
    if worker_name:
        # **``BIBI_NODE_NAME``, nicht ``BIBI_WORKER_NAME``** (m.rau/bibi#90).
        # PLAN-34 hat den Konfigurations-Schlüssel umbenannt und die
        # Laufzeit-Variable unbenannt gelassen — die Engine schrieb damit in
        # jeden Job denselben Namen, den ``doctor`` als ``legacy-node-name``
        # anmahnt, sobald ein Mensch ihn in seine Config setzt.
        #
        # Das Paar ist geschlossen: hier geschrieben, in ``wrapper`` gelesen,
        # beide Seiten aus demselben Release. Die drei Fallback-Stellen in
        # ``hygiene.py``, ``node_info.py`` und ``daemon_cmd.py`` bleiben —
        # sie lesen, was ein *Mensch* gesetzt hat, und das ist
        # Bestandskompatibilität, keine Altlast.
        env["BIBI_NODE_NAME"] = worker_name
    if host:
        env["BIBI_HOST"] = host
    env["BIBI_ATTEMPT"] = str(attempt)
    env["BIBI_ATTEMPTS"] = str(attempts)
    if backoff_type:
        env["BIBI_BACKOFF"] = backoff_type
    # Reporting-Ziel für den eigentlichen Statusübergang: explizit gesetzt >
    # lokale DB > HTTP-Daemon (PLAN-9 §8 E2 — Zuverlässigkeit, unverändert).
    # BIBI_SCHEDULER_URL wird zusätzlich IMMER gesetzt, auch wenn eine lokale
    # DB verfügbar ist (bisher gegenseitig ausschließende elif-Kette) — der
    # Wrapper braucht sie für den zusätzlichen Merge-back-Trigger nach einem
    # SQLite-Report (PLAN-30 Ebene 1 v2, s. wrapper/__init__.py::_finish()).
    if scheduler_db_path:
        env["BIBI_SCHEDULER_DB_PATH"] = scheduler_db_path
    if scheduler_url:
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
    # Generischer, unvalidierter `docker run`-Escape-Hatch (§7.6a) — nur
    # exec_backend.build_exec() interpretiert das (container-Modus), hier nur
    # transportiert (JSON, damit der env-Wert ein flacher String bleibt).
    if docker_args:
        env["BIBI_DOCKER_ARGS"] = json.dumps(docker_args)
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
    proc = subprocess.Popen(
        [sys.executable, "-m", "bibi.wrapper"],
        env=env, cwd=str(repo_root), start_new_session=True,
    )
    if register is not None:
        register(job_id, proc)
    # Der Wrapper läuft eigenständig weiter — sofort zurückkehren.
    return out_path, proc.pid


def _retry_fields(reservation: dict) -> dict:
    """``failed``-Statusfelder mit Backoff/attempt++ (Retry; Dauerfehler exhaust→error, §5.5).

    Basis-Präzedenz analog zum Wrapper-Pfad (``_finish()``, §5.5 error_time):
    Schedule-Frontmatter (``error_time``) > globaler ``BIBI_RETRY_BASE`` >
    ``backoff.DEFAULT_BASE``. Ein per Job-Exception explizit übergebenes
    ``seconds=N`` (``bibi.job.Failed``) wirkt nur im detachten Wrapper-Pfad —
    dieser blockierende Pfad (``bibi-ctrl run``/``test``) sieht keine
    BIBI-Signale des Kindprozesses.

    **Seit m.rau/bibi#128 fragt diese Funktion, ob überhaupt noch ein Versuch
    zusteht.** Sie tat es nie — und war damit der eine Pfad, der die Zusicherung
    brach, auf die sich ``reserve_next()`` und ``sweep()`` ausdrücklich berufen
    (*„eine Zeile mit ``status='failed'`` schuldet per Konstruktion IMMER noch
    einen Dispatch"*). Ein Job mit ``attempts: 0`` stand am 2026-08-10 bei
    **488** Versuchen, weil jeder Setup-Fehler pflichtschuldig einen neuen
    Termin schrieb.

    Bei Erschöpfung bleibt es hier bei ``failed`` **ohne** Termin: ``error`` ist
    von ``starting`` aus keine gültige Kante (``lifecycle.py`` kennt nur
    ``failed --exhaust--> error``). Der Aufrufer schiebt es synchron nach —
    dieselbe zweistufige Bewegung, die ``_finish()`` für den Wrapper-Pfad
    macht."""
    attempt_cur = reservation.get("attempt") or 0
    if backoff.exhausted(attempt_cur, reservation.get("attempts") or 0):
        return {"status": "failed", "attempt": attempt_cur, "next_fire_at": None}
    attempt = attempt_cur + 1
    error_time = reservation.get("error_time")
    base = float(error_time if error_time is not None
                 else os.environ.get("BIBI_RETRY_BASE") or backoff.DEFAULT_BASE)
    nf = time.time() + backoff.delay(reservation.get("backoff") or "fixed", attempt, base=base)
    return {"status": "failed", "attempt": attempt, "next_fire_at": nf}


def _report_pid_once(sched_db_path: str, jid: str, proc_pid: int) -> None:
    """Ein einzelner Versuch, die Wrapper-PID zu melden — frischer `connect()`
    je Aufruf (PLAN-31 Baustein B), damit ein Retry nach einem Lock-Fehler
    nicht auf einer möglicherweise beschädigten Connection aufsetzt.

    Schaltet zugleich ``starting`` → ``running`` (m.rau/bibi#38). Bleibt der
    Übergang aus, war der Job schon fertig, bevor wir seine PID notieren
    konnten — bei sehr kurzen Läufen der Normalfall, kein Fehler. Sein
    gemeldeter Terminalzustand hat Vorrang und bleibt unangetastet.
    """
    conn = job_db.connect(Path(sched_db_path))
    try:
        moved = job_db.report_pid(conn, jid, proc_pid, job_db.proc_started_at(proc_pid))
        if not moved:
            activity.emit(log, logging.DEBUG, "worker.pid_late",
                          "Job war vor dem PID-Report schon terminal",
                          role="worker", run_id=jid)
    finally:
        conn.close()


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
    ephemeral: bool = False, in_place: bool = False,
) -> dict:
    """Einen **disponierten** (reservierten) Job ausführen + via ``client`` melden,
    inkl. Lifecycle-Kanten (§5.5): wall_time→killed, silence→zombie, exit≠0→failed
    (mit Backoff, attempt++). Alle Ausführungs-/Retry-Parameter kommen aus der
    **Reservierung** (so braucht ein Remote-Worker keine lokale DB, §3.6).

    ``ephemeral`` (Default False, an echte Scheduler-Jobs mit wiederverwendetem
    Worktree gedacht): ``True`` lässt den (detachten) Wrapper-Subprozess seinen
    Worktree nach dem Commit selbst entfernen (``BIBI_EPHEMERAL=1``, s.
    ``bibi/wrapper/__init__.py::_commit_worktree()``) — nötig für
    ``run_pinned()``, dessen ``unique_slug`` pro Aufruf einen frischen,
    nie wiederverwendeten Worktree anlegt (sonst Leak, Fund PLAN-28 Refactor D).

    ``in_place`` (User-Fund 2026-07-14, ``bibi-ctrl test``): kein Worktree,
    lief direkt gegen ``repo_root`` — ``run_pinned()`` erzwingt dafür
    ``ephemeral=False`` (kein Worktree, den man entfernen dürfte/müsste),
    hier nur durchgereicht an ``_run_wrapper()``.

    Der Status wird über ``client.report`` gesetzt; ist der Job bereits terminal
    (z. B. ``killed`` per ``/-/job/{id}/kill``), lehnt der Scheduler den Übergang ab
    (``invalid``) und der Worker überschreibt nichts."""
    jid = reservation["id"]
    run_id = job_db.run_id_for(reservation["slug"], jid, reservation.get("fire", 0))
    host = host or socket.gethostname()
    # ``or 0``/``or 1`` wären hier falsch: ``attempts=0`` ist ein gültiger,
    # bewusst gesetzter Wert (run_pinned()s "kein Retry", s. dessen
    # Docstring) — ``0 or 1`` liefert in Python fälschlich ``1`` und hebelt
    # ihn aus. User-Fund 2026-07-14 (gmail-transfer via /run): der Wrapper sah
    # dadurch attempts_max=1 statt 0, nahm bei Fehlschlag den Retry-Zweig
    # (failed + next_fire_at) statt sofort zu erschöpfen (failed→error) — ohne
    # laufenden Scheduler-Loop (CLI/`/-/run`) wird der Retry nie bedient: der
    # Job blieb für immer "failed" hängen (lifecycle.TERMINAL schließt failed
    # bewusst aus), landete nie im Journal, ``bibi-ctrl run`` hing in
    # ``_wait_until_terminal()`` endlos.
    attempt = reservation.get("attempt")
    attempt = 0 if attempt is None else attempt
    attempts = reservation.get("attempts")
    attempts = 1 if attempts is None else attempts
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
        # PLAN-10 Stufe 10.0: SQLite-Direct wenn verfügbar für den eigentlichen
        # Statusübergang; die lokale Daemon-URL wird IMMER mitgegeben (PLAN-30
        # Ebene 1 v2) — der Wrapper nutzt sie zusätzlich für den Merge-back-
        # Trigger, unabhängig davon, ob SQLite-Direct verfügbar ist.
        _daemon_port = int(os.environ.get("BIBI_DAEMON_PORT", "8769"))
        _sched_db_path: str | None = _resolved_db or None
        _sched_url: str | None = f"http://127.0.0.1:{_daemon_port}"
        out_path, proc_pid = _run_wrapper(
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
            docker_args=reservation.get("docker_args"),
            defer_time=reservation.get("defer_time"),
            error_time=reservation.get("error_time"),
            repo_root=repo_root, work_dir=work_dir, register=register, ephemeral=ephemeral,
            in_place=in_place,
            run_id=run_id,
            worker_name=worker_name, host=host,
            attempt=attempt, attempts=attempts,
            backoff_type=reservation.get("backoff"),
            scheduler_db_path=_sched_db_path,
            scheduler_url=_sched_url,
        )
        if proc_pid is not None and _sched_db_path:
            # PLAN-31 Baustein B: der Wrapper läuft bereits, ein kurzer Lock
            # (z. B. durch einen gleichzeitigen anderen Report) soll den Job
            # nicht als Setup-Fehler markieren — bis zu zwei Retries statt
            # sofort in den except-Block unten zu fallen.
            job_db.call_with_lock_retry(lambda: _report_pid_once(_sched_db_path, jid, proc_pid))
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
        # m.rau/bibi#128: sind die Versuche aufgebraucht, folgt sofort das
        # ``error``. Zwei Meldungen statt einer, weil ``starting → error`` keine
        # gültige Kante ist — erst der Zwischenschritt nach ``failed`` (ohne
        # Termin, also ohne weiteren Dispatch), dann die EXHAUST-Kante. Genau
        # die Bewegung, die ``_finish()`` für den Wrapper-Pfad macht; **ohne
        # sie bliebe der Job als ``failed`` liegen und der Sweeper müsste ihn
        # holen** — der prüft seit seiner Bereinigung aber nur noch ``failed``
        # OHNE Termin, und das wäre hier zufällig richtig statt begründet.
        status = fields["status"] if res == "ok" else None
        if res == "ok" and fields.get("next_fire_at") is None:
            if client.report(jid, status="error", exit_code=-1,
                             output_ref=output_ref, worker=worker_name,
                             host=host) == "ok":
                status = "error"
        return {"id": jid, "exit_code": -1, "commit": None,
                "status": status, "outcome": "setup_error"}

    # Detach: Wrapper läuft selbstständig weiter. Worker kehrt sofort zurück.
    activity.emit(log, logging.INFO, "worker.spawned", role="worker",
                  slug=reservation.get("slug"), run_id=jid, outcome="detached")
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


def local_schedule_exec_mode(slug: str, *, repo_root: Path | None = None) -> str | None:
    """``exec_mode``-Override direkt aus der Schedule-MD — unabhängig von
    irgendeiner ``jobs``-Zeile. Grundlage für REBUILD auf dem Client (User-
    Fund 2026-07-13: "REBUILD müsste doch auch beim Client notwendig sein,
    oder?") — anders als START/RESET/KILL hängt REBUILD an keinem bestimmten
    Lauf, sondern rein am *Schedule*: das per-Job-Image existiert (oder auch
    nicht) unabhängig davon, ob gerade etwas läuft oder je gelaufen ist.

    Wirft ``LookupError`` für einen unbekannten Slug — dieselbe Konvention
    wie ``run_pinned()``s eigene Slug-Resolution."""
    pr = _resolve_spec(repo_root or repo.root(), slug)
    if pr is None:
        raise LookupError(f"kein Schedule mit Slug {slug!r}")
    return pr.spec.exec_mode


#: Live-Status-Werte (kein Terminalzustand) — deckungsgleich mit dem, was ein
#: gerade tatsächlich laufender Wrapper-Subprozess haben kann. "deferred" UND
#: "failed" gehören dazu, obwohl in diesem Fenster gerade KEIN Subprozess läuft
#: (Bugfix, User-Fund: ein gepinnter Lauf in einem dieser beiden Zustände fiel
#: aus dieser Query komplett heraus — die Job-Detail-Seite zeigte für die
#: gesamte Wartephase zwischen zwei Versuchen "noch keine Läufe", obwohl
#: next_fire_at bereits einen Retry vorsah. "failed" fehlte hier ursprünglich
#: genauso wie "deferred" — beim ersten Fix übersehen, obwohl _live_panel()
#: (render.py) "failed" schon länger wie einen Quasi-Terminalzustand behandelt;
#: nur die Datenquelle hier hinkte hinterher).
_PINNED_LIVE_STATUSES = ("running", "awaiting", "deferred", "failed")


def pin_identity() -> str:
    """Unter welchem Namen dieser Knoten seine ``/run``-Läufe pinnt (#88).

    ``jobs.pinned_host`` ist die Zusage „diese Zeile gehört genau diesem
    Knoten" — sie entscheidet, wer den Lauf reservieren darf und wessen Läufe
    die Detailseite zeigt. Gespeichert wurde dafür ``socket.gethostname()``,
    also ein **Anzeigename**.

    Dieser Mac wechselt ihn im Betrieb (``Air2024.local`` gegen
    ``Mac.fritz.box``, gemessen sogar während eines einzelnen Laufs). Mit dem
    Namen wechselt der Schlüssel, und die eigenen Läufe werden unsichtbar —
    keine Kachel, keine Zeile, keine Ausgabe, obwohl alles in der Datenbank
    steht.

    ``config.node_id()`` ist die stabile Identität: eine generierte UUID in der
    ``env``-Datei, self-healing und vom Hostnamen unabhängig. Sie existierte
    längst und wurde an dieser Stelle nur nicht benutzt.

    Der Rückfall auf den Hostnamen greift nur, wenn die ``env`` unlesbar ist —
    dann ist das bisherige Verhalten immer noch besser als ein Abbruch.
    """
    from bibi import config
    try:
        return config.node_id() or socket.gethostname()
    except Exception:  # noqa: BLE001 — defensiv (§2.7)
        return socket.gethostname()


def pin_lookup_ids(host: str | None = None) -> tuple[str, ...]:
    """Unter welchen Namen eigene gepinnte Zeilen liegen können (#88).

    **Zwei, und der zweite ist der Bestand.** Neue Zeilen tragen
    :func:`pin_identity`; die rund 130 bereits vorhandenen auf diesem Mac
    tragen einen Hostnamen. Ein harter Tausch machte sie auf einen Schlag
    unauffindbar — die Historie wäre da und unerreichbar.

    ``host`` ist der Name, unter dem gefragt wird (``WorkerLoop.host``, oder
    ein Testwert). Er kommt **zusätzlich** zur Identität in den Vergleich, denn
    ein Knoten fragt unter seinem Anzeigenamen an, während seine Zeilen die ID
    tragen. Ohne beides schriebe ``run_pinned()`` eine Zeile, die anschließend
    niemand reservieren kann — der Lauf bliebe für immer ``pending``.

    Die Pin-Zusage bleibt in beide Richtungen gültig: was einem anderen Knoten
    gehört, steht unter dessen Namen und ist in keiner der beiden Angaben
    enthalten.
    """
    ids = [host or "", pin_identity(), socket.gethostname()]
    # ``dict.fromkeys`` statt ``set``: die Reihenfolge bleibt stabil, und damit
    # bleiben es auch die SQL-Parameter — sonst wanderten sie zwischen zwei
    # Läufen, ohne dass sich etwas geändert hätte.
    return tuple(dict.fromkeys(i for i in ids if i))


def _pinned_row(slug: str, *, db_path: Path | None = None, host: str | None = None,
                statuses: tuple[str, ...] | None = None) -> sqlite3.Row | None:
    """Die jüngste ``jobs``-Zeile für den **Bucket-Slug** ``slug`` an diesem
    Host, oder ``None`` — Query-Basis für ``local_run_live()``/
    ``local_runs_live()`` (PLAN-28: reale ``jobs``-Zeile statt In-Memory-Dict,
    s. Modul-Kommentar oben). ``jobs.slug`` ist pro Lauf eindeutig
    (``f"{bucket_slug}-{token}"``, s. ``run_pinned()`` — ``-`` statt dem
    ursprünglichen ``:``, da Git-Refs keinen Doppelpunkt erlauben).
    ``statuses=None`` (Bibi4-Iteration, Client-RESET-Lücke): die jüngste
    Zeile unabhängig vom Status — auch bereits terminale, für die
    ``_pinned_live_row()`` nichts mehr findet.

    Bug gefunden (2026-07-13, User-Fund: "hitl-test-app-container und
    hitl-test-app geraten beim Output durcheinander"): ein offenes
    ``LIKE '<slug>-%'`` matcht nicht nur die eigenen Läufe, sondern auch jeden
    Lauf eines längeren Geschwister-Slugs, der ``slug`` als echtes Präfix hat
    (``"hitl-test-app-container-<token>"`` beginnt mit ``"hitl-test-app-"``).
    ``token`` ist immer ``secrets.token_hex(4)`` (8 Hex-Zeichen, s.
    ``run_pinned()``) — das feste 8-Zeichen-Muster (dieselbe Konvention wie
    ``job_db.list_journal()``s Slug-Filter) schließt so etwas per Länge aus,
    ein offenes ``%`` nicht."""
    # Beide Namen (m.rau/bibi#88): neue Zeilen tragen die stabile Identitaet,
    # der Bestand einen Hostnamen. Wer nur einen vergleicht, verliert die eine
    # oder die andere Haelfte — und zwar unsichtbar.
    ids = pin_lookup_ids(host)
    conn = job_db.connect(db_path)
    try:
        platzhalter = ",".join("?" * len(ids))
        sql = f"SELECT * FROM jobs WHERE pinned_host IN ({platzhalter}) AND slug LIKE ?"
        params: list = [*ids, f"{slug}-________"]
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY enqueued_at DESC LIMIT 1"
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _pinned_live_row(slug: str, *, db_path: Path | None = None,
                     host: str | None = None) -> sqlite3.Row | None:
    return _pinned_row(slug, db_path=db_path, host=host, statuses=_PINNED_LIVE_STATUSES)


def _pinned_last_row(slug: str, *, db_path: Path | None = None,
                     host: str | None = None) -> sqlite3.Row | None:
    """Die jüngste ``jobs``-Zeile für ``slug``, **unabhängig vom Status** —
    im Unterschied zu ``_pinned_live_row()`` findet das auch einen bereits
    terminalen (z. B. ``killed``) Lauf. Query-Basis für den Client-RESET-Pfad
    (``run_live_reset()``, ``app.py``): ein Lauf, der schon terminal ist, hat
    keine "live"-Zeile mehr, RESET soll dessen Job-Daten aber trotzdem wischen
    können (Bibi4-Iteration, User-Fund "Reset Test Container: Laufzahl nach
    RESET nicht zurückgesetzt")."""
    return _pinned_row(slug, db_path=db_path, host=host, statuses=None)


def local_run_live(slug: str, *, db_path: Path | None = None,
                   host: str | None = None, repo_root: Path | None = None) -> dict | None:
    """Metadaten des gerade laufenden gepinnten Runs für den Bucket-Slug
    ``slug``, oder ``None`` (PLAN-28: jobs-tabellen-basiert, s. oben).

    Bug gefunden (2026-07-13, User-Fund: echter Client-Test auf localhost,
    ``TypeError`` in ``app.py::run_live_detail()``): ``jobs.output_ref`` wird
    von ``run_pinned()``s INSERT nie gesetzt — erst der Wrapper füllt die
    Spalte beim Terminal-Report (§ ``execute_reservation()``/``_finish()``).
    Während ``running``/``awaiting`` (genau das Zeitfenster, für das diese
    Funktion existiert) ist die Spalte also **immer** ``NULL``. Kein bisheriger
    Test bemerkte das, weil der Seed-Helper stets einen Wert vorgab. Fix:
    denselben Pfad wie ``run_pinned()``/``execute_reservation()`` selbst
    berechnen (``job_db.run_id_for()`` + ``_output_path()``), statt die
    (garantiert leere) Spalte zu lesen."""
    row = _pinned_live_row(slug, db_path=db_path, host=host)
    if row is None:
        return None
    repo_root = repo_root or repo.root()
    run_id = job_db.run_id_for(row["slug"], row["id"], row["fire"])
    output_ref = _output_path(repo_root, run_id).relative_to(repo_root).as_posix()
    # Bugfix (User-Fund: "angezeigt wird RUNNING, nicht FAILED" / "DEFERRED nie
    # im Dashboard gesehen"): status fehlte hier komplett — Aufrufer (app.py
    # run_live_detail()) griffen stattdessen auf local_run_signal_state()
    # zurück, das aus den BIBI:-Signal-Events abgeleitet wird und "deferred"/
    # "failed" strukturell nie erkennen kann (diese beiden Signale werden im
    # Wrapper-pump() als current_status/env behandelt, nie als "signal"-Event
    # in output.jsonl geschrieben, s. wrapper/__init__.py::pump()) — der
    # Default dort blieb deshalb immer "running". Die DB-Spalte (row["status"],
    # dank _PINNED_LIVE_STATUSES jetzt auch deferred/failed) trägt den echten
    # Wert längst.
    return {"id": row["id"], "output_ref": output_ref, "kind": row["kind"],
            "payload": row["payload"], "started_at": row["started_at"],
            "status": row["status"]}


def local_runs_live(*, db_path: Path | None = None, host: str | None = None) -> dict[str, dict]:
    """Alle aktuell laufenden gepinnten Runs dieses Hosts, ``{bucket_slug:
    {id, started_at, status}}`` (PLAN-28: jobs-tabellen-basiert — löst den
    früheren Prozessgrenzen-Bug des In-Memory-Dicts, s. Modul-Kommentar oben;
    ``status`` kommt jetzt direkt aus der DB-Zeile, kein extra Output-Read
    mehr nötig, anders als das frühere ``local_run_signal_state()``-basierte
    Verfahren)."""
    ids = pin_lookup_ids(host)   # beide Namen, s. pin_lookup_ids() (#88)
    conn = job_db.connect(db_path)
    try:
        placeholders = ",".join("?" * len(_PINNED_LIVE_STATUSES))
        id_platzhalter = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT slug, id, started_at, status FROM jobs "
            f"WHERE pinned_host IN ({id_platzhalter}) AND status IN ({placeholders})",
            (*ids, *_PINNED_LIVE_STATUSES),
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        bucket_slug = r["slug"].rsplit("-", 1)[0]
        out[bucket_slug] = {"id": r["id"], "started_at": r["started_at"], "status": r["status"]}
    return out


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


def run_pinned(
    *, slug: str | None = None, cmd: str | None = None, kind: str = "job",
    model: str | None = None, repo_root: Path | None = None,
    work_dir: Path | None = None, db_path: Path | None = None,
    worker_name: str | None = None, host: str | None = None,
    attempts: int = 0, register=None, in_place: bool = False,
    use_schedule_retry: bool = False,
) -> dict:
    """**Lokale** On-Demand-Ausführung mit voller Scheduler-Lifecycle (PLAN-28).

    Ersetzt den früheren, rein lokalen ``/run``-Pfad (vor PLAN-28: ``run_local()``,
    seither entfernt) — anders als dort bekommt der Lauf jetzt eine echte
    ``jobs``-Zeile (``pinned_host`` = dieser Host, s. ``reserve_next()``s
    ``pinned_only``-Filter), läuft also
    durch dieselbe Retry/Error/Deferred/Zombie-Maschine wie ein Scheduler-Job
    (``report_status()``, der zweite, gepinnte ``Worker`` aus ``create_app()``).
    Beide bisherigen ``/run``-Garantien bleiben erhalten: **hier**
    (``pinned_host`` erzwingt genau diesen Knoten, kein anderer Worker kann
    die Zeile je reservieren) und **sofort** (kein Warten auf einen
    Poll-Tick — die Zeile wird synchron im selben Aufruf reserviert + über
    ``execute_reservation()`` dispatcht, das fast
    augenblicklich zurückkehrt, während der Wrapper-Subprozess eigenständig
    weiterläuft und terminale Status via SQLite-Direct selbst meldet — kein
    Netz nötig, funktioniert offline).

    Entweder ``slug`` (erfasste MD) **oder** ``cmd`` (ad-hoc, rein lokal).

    ``attempts`` (Default **0** — bewusst *nicht* der Scheduler-Default 1):
    der Wrapper selbst prüft ``attempt_cur < attempts_max`` (``bibi/wrapper/
    __init__.py::_finish()``) — bei einem frischen Job ist ``attempt_cur=0``,
    ``attempts=1`` würde also **einen Retry auslösen**, kein "kein Retry" wie
    ein früherer Docstring hier fälschlich behauptete. ``attempts=0`` (``0 <
    0`` ist falsch) meldet bei Fehlschlag sofort "error", ohne Backoff-Wartezeit
    — deckungsgleich mit dem historischen ``/run``-Verhalten (ein Versuch,
    sofortiger Fehlschlag), das insbesondere die CLI (``bibi-ctrl run``,
    **ohne** laufenden Daemon/gepinnten Worker) braucht: ein echter Retry
    bräuchte den gepinnten ``Worker``-Loop aus ``create_app()``, um den
    fälligen Backoff-Redispatch zu bedienen — ohne laufenden Daemon bliebe
    ein wartender Retry für immer unbedient hängen. Aufrufer mit laufendem
    Daemon (die HTTP-Route ``/-/run``) können bei Bedarf explizit
    ``attempts>0`` übergeben, um echtes Retry-mit-Backoff zu aktivieren.

    ``use_schedule_retry`` (Default **False**, Bugfix — User-Fund: ein über
    den START-Button gepinnter Lauf von ``Runner 5`` mit ``attempts: 2`` in
    der Frontmatter exhaustierte trotzdem beim ERSTEN Fehlschlag sofort zu
    ``error`` statt zweimal zu retryen): bei ``True`` **und** einer
    ``slug``-Auflösung (kein ``cmd=``-Ad-hoc-Lauf) übernimmt der Pin
    ``attempts``/``backoff``/``defer_time``/``error_time`` direkt aus der
    Schedule-MD (``s.attempts`` usw.) statt der No-Retry-Defaults oben —
    genau das im Docstring seit PLAN-28 schon angekündigte, aber nie an der
    ``/-/run``-Route umgesetzte Verhalten. **Nur** setzen, wenn ein
    laufender Daemon mit gepinntem Worker-Loop einen fälligen Retry auch
    tatsächlich bedient (die HTTP-Route ``/-/run``) — der
    CLI-Pfad (``bibi-ctrl run``/``test``, kein Daemon) darf das NIE setzen,
    sonst hängt ``_wait_until_terminal()`` für immer auf einem nie
    bedienten Retry (s. ``ctrl/run_cmd.py``-Moduldocstring, derselbe
    Deadlock, den der ``attempts=0``-Default oben verhindert).

    ``in_place`` (User-Fund 2026-07-14, ``bibi-ctrl test``): läuft **ohne**
    frischen Worktree direkt gegen ``repo_root`` (dirty tree erlaubt, kein
    Commit vorher nötig) und committet **nie** danach — Gegenstück zu ``run``,
    für schnelle lokale Iteration statt reproduzierbarer Dispatch. Erzwingt
    ``ephemeral=False`` (kein separater Worktree existiert, den man aufräumen
    könnte/dürfte — s. ``_run_wrapper()``s ``BIBI_IN_PLACE``-Zweig und
    ``worktree.remove()``s Guard gegen ``worktree == repo_root``)."""
    repo_root = repo_root or repo.root()
    work_dir = work_dir or (repo_root / "data" / "worktrees")
    host = host or socket.gethostname()
    worker_name = worker_name or host
    # **Was in `pinned_host` landet, ist die Identitaet, nicht der Anzeigename**
    # (m.rau/bibi#88). `host`/`worker_name` bleiben der Hostname — sie stehen im
    # FE und in der Worker-Liste, dort ist der lesbare Name richtig. Der
    # Schluessel, an dem die Zeile wiedergefunden wird, darf sich dagegen nicht
    # aendern, wenn der Rechner seinen Namen wechselt.
    pin_host = pin_identity()
    eff_soul = eff_session = None
    eff_schedule_ref: str | None = None
    eff_app_port = eff_app_prefix = eff_exec_mode = eff_image = None
    eff_backoff = "fixed"
    eff_defer_time = eff_error_time = None
    if cmd is not None:
        eff_slug, payload, eff_kind, eff_model = slug or "adhoc", cmd, kind, model
        # Bug gefunden (2026-07-14, User-Fund: "warum zeigt die Attribute-
        # Seite bei gepinnten Läufen andere Timeouts als beim Scheduler-Job?"):
        # ohne Schedule-MD gibt es keine ScheduleSpec, aus der silence_timeout
        # sich ableiten ließe — denselben Default wie parser.py anwenden
        # (§ nächster Zweig), statt stillschweigend auf den SQL-Spalten-
        # Default (3600s, nur für claude-Payloads richtig) zurückzufallen.
        # PLAN-31 Befund 4: `cmd` (Ad-hoc, kein Schedule-MD) kennt kein
        # `app_port`/`app_prefix` — nur claude: vs. einfacher Job kommen hier
        # überhaupt vor, der App-Fall ist strukturell ausgeschlossen.
        eff_silence_timeout = (DEFAULT_SILENCE_TIMEOUT if is_claude_payload(payload)
                               else DEFAULT_SILENCE_TIMEOUT_JOB)
        eff_wall_time = None
    else:
        if not slug:
            raise ValueError("run_pinned braucht entweder slug oder cmd")
        pr = _resolve_spec(repo_root, slug)
        if pr is None or pr.spec is None:
            raise LookupError(f"kein Schedule mit Slug {slug!r}")
        s = pr.spec
        if s.at is not None:
            # `at` ist der einzige Trigger, der sich verbraucht — und damit die
            # einzige Ausführungsgarantie „genau einmal" im System
            # (m.rau/bibi#111, Zustandsmodell §5). Ein lokaler Lauf wäre ein
            # zweiter Verbrauch desselben Termins: der Scheduler feuert seinen
            # eigenen trotzdem, der Job liefe zweimal und die Garantie wäre
            # gebrochen, ohne dass es jemand merkt. Abbruch **vor** dem INSERT,
            # sonst bliebe eine gepinnte Zeile stehen, die nie läuft.
            raise ValueError(
                f"{s.slug!r} ist ein Oneshot (at: {s.at}) und läuft nur über den "
                f"Scheduler — der garantiert genau einmal, ein lokaler Lauf wäre "
                f"ein zweiter Verbrauch desselben Termins. Soll der Job wiederholt "
                f"auf Zuruf laufen, gehört 'schedule: adhoc' in die MD statt 'at:'.")
        eff_slug, payload, eff_kind, eff_model = s.slug, s.payload, s.kind.value, s.model
        eff_soul, eff_session = s.soul, s.session
        eff_schedule_ref = pr.schedule_ref
        eff_app_port, eff_app_prefix, eff_exec_mode = s.app_port, s.app_prefix, s.exec_mode
        eff_image = s.image
        # Bug gefunden (2026-07-14): silence_timeout/wall_time fehlten bisher
        # in der INSERT-Spaltenliste unten — anders als soul/session/app_port/
        # exec_mode/image wurden sie aus s (der voll aufgelösten ScheduleSpec,
        # inkl. Parser-Defaults) nie gelesen. Ein gepinnter Lauf bekam dadurch
        # den SQL-Spalten-Default (3600s) statt des tatsächlich für diesen Job
        # geltenden Werts (z. B. 48h für Job/App-Payloads) — und jeden
        # expliziten wall_time-Override aus der MD nie.
        eff_silence_timeout, eff_wall_time = s.silence_timeout, s.wall_time
        if use_schedule_retry:
            attempts = s.attempts
            eff_backoff = s.backoff
            eff_defer_time = s.defer_time
            eff_error_time = s.error_time

    # Eindeutiger jobs.slug (UNIQUE-Constraint) — unabhängig vom MD-/Cmd-Slug,
    # sonst kollidiert ein zweiter ▶ Start mit der noch nicht aufgeräumten
    # Zeile des ersten Laufs. Nebeneffekt (und Grund für execute_reservation()s
    # ephemeral=True unten): jeder Aufruf bekommt so einen frischen, nie
    # wiederverwendeten Worktree — anders als ein rekurrierender Scheduler-Job,
    # dessen stabiler Slug denselben Worktree über mehrere Läufe hinweg nutzt.
    #
    # Bug gefunden (2026-07-13, User-Fund: bibi-ctrl run hing endlos): ``:``
    # als Trenner ist in Git-Refs UNGÜLTIG (worktree.branch_name() baut
    # ``agent/<slug>``) — worktree.prepare()s ``git worktree add -B`` schlug
    # deshalb IMMER fehl (nie zuvor real durchlaufen, alle bisherigen Tests
    # mockten _run_wrapper()). execute_reservation() fing das als Setup-Fehler
    # ab und meldete "failed" — nicht TERMINAL, kein Retry-Daemon in der CLI
    # bedient das je, die CLI-Poll-Schleife (_wait_until_terminal()) hing für
    # immer. Fix: ``-`` statt ``:`` (git-ref-sicher, auch für die
    # LIKE-Präfix-Matches/rsplit() in _pinned_live_row()/local_runs_live()
    # unten mitgeändert).
    unique_slug = f"{eff_slug}-{secrets.token_hex(4)}"
    now = time.time()
    jid = secrets.token_hex(4)
    conn = job_db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO jobs (id, slug, job_uid, schedule_ref, kind, payload, model, soul, "
            "session, app_port, app_prefix, exec_mode, image, silence_timeout, wall_time, "
            "schedule, next_fire_at, attempts, backoff, defer_time, error_time, "
            "pinned_host, status, enqueued_at) VALUES "
            "(:id, :slug, :job_uid, :schedule_ref, :kind, :payload, :model, :soul, :session, "
            ":app_port, :app_prefix, :exec_mode, :image, :silence_timeout, :wall_time, "
            "'now', :now, :attempts, :backoff, :defer_time, :error_time, "
            ":pinned_host, 'pending', :now)",
            # `job_uid` kommt aus ``eff_slug``, nicht aus ``unique_slug``: der
            # Zufallssuffix macht die *Zeile* eindeutig, nicht den *Job*. Ein
            # lokaler Lauf von `EngineCI` gehört zu `EngineCI` — hier ist der
            # Basis-Slug bekannt, deshalb wird er hier gesetzt und nirgends
            # später aus dem Suffix zurückgerechnet (models.job_uid()).
            {"id": jid, "slug": unique_slug, "job_uid": job_uid(eff_slug),
             "schedule_ref": eff_schedule_ref or unique_slug,
             "kind": eff_kind, "payload": payload, "model": eff_model, "soul": eff_soul,
             "session": eff_session, "app_port": eff_app_port, "app_prefix": eff_app_prefix,
             "exec_mode": eff_exec_mode, "image": eff_image,
             "silence_timeout": eff_silence_timeout, "wall_time": eff_wall_time, "now": now,
             "attempts": attempts, "backoff": eff_backoff, "defer_time": eff_defer_time,
             "error_time": eff_error_time, "pinned_host": pin_host},
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
        ephemeral=not in_place, in_place=in_place,
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
        # Drain (m.rau/bibi#38): prozess-lokal und bewusst NICHT der
        # Wartungsmodus. Der ist persistenter State in .state.md und würde einen
        # Neustart überdauern — ein Knoten, der nach dem Deploy stumm keine Jobs
        # mehr annimmt, wäre die schlechteste Art von Nebenwirkung. Dieses Flag
        # stirbt mit dem Prozess, und genau das ist gewollt.
        self._draining = False

    def _roots(self) -> tuple[Path, Path]:
        root = self.repo_root or repo.root()
        work = self.work_dir or (root / "data" / "worktrees")
        return root, work

    def output_path(self, job_id: str) -> Path:
        """``output.jsonl``-Pfad des **aktuellen** Laufs eines Jobs (Live-Routen).

        **Erst ``jobs.output_ref``, dann die Neuberechnung** — dieselbe
        Reihenfolge wie ``app.py::_journal_output_path()`` für Journal-Zeilen.
        Der Verweis ist der Pfad, unter dem der Lauf **tatsächlich** geschrieben
        hat; die Neuberechnung ist eine Ableitung aus dem Zustand der Zeile
        *jetzt*. Wo beide auseinanderlaufen, gewinnt die Datei auf Platte.

        Gefunden 2026-08-04 an einem Slot-Lauf auf sarasate (``m.rau/bibi#131``-
        Nachlauf): ``20260702.at-080500-aa2b`` steht seit dem 3. Juli auf
        ``error``, seine Ausgabe liegt unter ``data/job/414d6af0/`` — der blossen
        ``job_id``, wie **alle** Läufe von vor ``run_id_for()`` (2026-07-01, live
        noch sieben Verzeichnisse). Die Neuberechnung ergab
        ``data/job/20260702.at-080500-aa2b:0:414d6af0/``, das es nie gab, und die
        Kachel meldete ``output unavailable``, obwohl die Zeile den richtigen
        Pfad die ganze Zeit mitführte.

        **Der Fallback bleibt der Normalfall und trägt weiter**: während
        ``starting``/``running``/``awaiting`` ist die Spalte immer ``NULL`` (der
        Wrapper füllt sie erst beim Terminal-Report, s. ``local_run_live()``) —
        also genau in dem Fenster, für das die Live-Routen existieren. Ein
        veralteter Verweis kann dabei nicht gewinnen: jeder Weg in einen neuen
        Lauf nullt ihn (``reserve_next()`` beim Lazy Rearm aus ``complete``,
        ``report_status()`` beim Übergang nach ``pending``, ``_rearm_after_kill()``),
        und wo ``fire`` gleich bleibt (Retry aus ``failed``/``deferred``), zeigen
        Verweis und Neuberechnung ohnehin auf dieselbe Datei.

        Ist die ID unbekannt (z. B. ephemerer ``/run``), bleibt sie selbst der
        Pfad."""
        root, _ = self._roots()
        conn = job_db.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT id, slug, fire, output_ref FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return _output_path(root, job_id)
        return output_path_of(row, root)

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
        if self._draining:
            # Drain (#38): keine neue Reservierung mehr. Laufende Jobs bleiben
            # unberührt — sie sind detacht und überleben den Neustart.
            return False
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
        # reconcile_orphans() lässt ihn dann bewusst running) — PID aus
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
        # PLAN-28: erst schlafen, dann ticken, beim allerersten Durchlauf —
        # ein zweiter, rollenunabhängig immer gestarteter Worker (gepinnte
        # Läufe) lief sonst in praktisch jedem Test sofort per
        # run_in_executor() in einem eigenen Thread gegen dieselbe frische
        # jobs.sqlite, mit der der Test selbst synchron arbeitet —
        # "database is locked" (dasselbe Muster wie beim ersten
        # LocalPinnedLoop-Entwurf, PLAN-28 Schritt 3). Ab dem zweiten
        # Durchlauf bleibt das bisherige "sofort weiter, solange Arbeit da
        # ist"-Verhalten (kein Sleep bei ``did=True``) unverändert.
        await asyncio.sleep(self.poll_interval)
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                did = await loop.run_in_executor(None, self.tick_once)
            except Exception:
                log.exception("Worker-Tick fehlgeschlagen")
                did = False
            if not did:
                await asyncio.sleep(self.poll_interval)

    def starting_count(self) -> int:
        """Wie viele Jobs dieses Workers stecken gerade im Setup? (#38)"""
        try:
            conn = job_db.connect(self.db_path)
        except Exception:  # noqa: BLE001 — ein Drain darf nie am Zählen scheitern
            return 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status='starting' AND worker=?",
                (self.worker_name,)).fetchone()
            return int(row["n"]) if row else 0
        except Exception:  # noqa: BLE001
            return 0
        finally:
            conn.close()

    async def drain(self, timeout: float = 120.0) -> dict:
        """Keine neuen Jobs mehr annehmen und die laufende Setup-Phase auswarten
        (m.rau/bibi#38, Design von m.rau).

        **Gewartet wird auf ``starting``, nicht auf Job-Ende.** Das ist der ganze
        Trick: ein Agent-Lauf dauert 30 Minuten und mehr — darauf zu warten hieße,
        keinen Deploy mehr machen zu können. Ein *Setup* dauert Sekunden bis
        wenige Minuten (Worktree, Container, Image-Build). Danach ist jeder
        verbliebene Job detacht, hat eine bekannte PID und überlebt den Neustart
        (``start_new_session=True`` plus ``KillMode=process``).

        Damit wird aus „hoffentlich erwischt der Neustart gerade keinen Job im
        Setup" eine Zusage. Ohne Drain war das ein Würfelwurf, dessen Fenster bei
        einem Container-Job mit Image-Build minutenlang offen steht.
        """
        self._draining = True
        deadline = time.time() + timeout
        n = self.starting_count()
        if n:
            activity.emit(log, logging.INFO, "worker.drain",
                          "Drain: warte auf Jobs im Setup", role="worker", starting=n)
        while n and time.time() < deadline:
            await asyncio.sleep(0.5)
            n = self.starting_count()
        out = {"drained": n == 0, "starting": n}
        if n:
            # Bewusst kein Abbruch: der Aufrufer entscheidet, ob er trotzdem
            # neu startet. Ein hängender Image-Build darf einen Deploy nicht
            # dauerhaft blockieren — aber er darf auch nicht unbemerkt bleiben.
            activity.emit(log, logging.WARNING, "worker.drain",
                          "Drain-Frist abgelaufen — Jobs stecken noch im Setup",
                          role="worker", starting=n)
        else:
            activity.emit(log, logging.INFO, "worker.drain",
                          "Drain abgeschlossen — nur noch detachte Jobs",
                          role="worker")
        return out

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
