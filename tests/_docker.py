"""Voraussetzungen der Container-Tests — gemessen, nicht geraten (m.rau/bibi#86).

Die beiden Container-Tests (``test_container_claude.py`` und der Smoke-Test in
``test_exec_backend.py``) starten den Container mit ``--user <host-uid>:0`` und
schreiben in einen Bind-Mount. Das setzt voraus, dass der Docker-Server die
Host-UID **echt durchreicht** — unter Linux mit lokalem Daemon der Normalfall,
bei Docker Desktop je nach Dateisystem-Backend (VirtioFS, gRPC-FUSE) nicht
garantiert.

Bisher prüfte ihr ``skipif`` nur, *ob* Docker läuft. Damit hing das Ergebnis an
einer Eigenschaft, die niemand im Repo kontrolliert: am 2026-07-31 scheiterten
beide Tests auf dem Mac, am 2026-08-01 waren sie grün — ohne einen einzigen
Commit an den Tests oder an ``exec_backend``. Ein Test, dessen Voraussetzung
sich unbemerkt ändert, ist in beiden Zuständen ohne Aussage.

Deshalb wird die Fähigkeit hier einmal je Sitzung gemessen. Ergebnis ist ein
**Grund zum Überspringen oder ``None``** — kein Wahrheitswert, damit im
Übersprungen-Fall dasteht, woran es lag.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from bibi.wrapper import exec_backend

IMAGE = "bibi-base:dev"
_TIMEOUT_S = 20

_cache: str | None | object = ...     # ... = noch nicht gemessen


def _env_for(docker_bin: str) -> dict:
    """PATH um das docker-Verzeichnis ergänzen — der Cred-Helper liegt daneben."""
    return {**os.environ,
            "PATH": str(Path(docker_bin).parent) + os.pathsep + os.environ.get("PATH", "")}


def _run(docker_bin: str, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run([docker_bin, *args], capture_output=True, text=True,
                              env=_env_for(docker_bin), timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None


def _probe_docker(docker_bin: str) -> bool:
    """Läuft ein Docker-Server?"""
    p = _run(docker_bin, "version", "--format", "{{.Server.Version}}")
    return bool(p and p.returncode == 0 and p.stdout.strip())


def _probe_image(docker_bin: str) -> bool:
    p = _run(docker_bin, "image", "inspect", IMAGE)
    return bool(p and p.returncode == 0)


def _probe_uid_passthrough(docker_bin: str) -> bool:
    """Kann der Container als ``--user <host-uid>:0`` in den Bind-Mount schreiben?

    Exakt die Fähigkeit, an der die beiden Container-Tests hängen — gemessen
    mit derselben Aufrufform, aber ohne deren Laufzeit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = _run(docker_bin, "run", "--rm",
                 "--user", f"{os.getuid()}:0",
                 "-v", f"{tmp}:/probe", "-w", "/probe",
                 IMAGE, "bash", "-c", "touch schreibprobe")
        if not (p and p.returncode == 0):
            return False
        return (Path(tmp) / "schreibprobe").exists()


def container_skip_reason(docker_bin: str | None = None, *, cached: bool = True) -> str | None:
    """``None``, wenn die Container-Tests hier laufen können — sonst der Grund.

    Wird beim Einsammeln ausgewertet und darf deshalb **nie** werfen: eine
    Exception im ``skipif``-Ausdruck macht nicht einen Test rot, sondern die
    ganze Datei nicht einsammelbar.
    """
    global _cache
    if cached and docker_bin is None and _cache is not ...:
        return _cache            # type: ignore[return-value]

    try:
        binary = docker_bin or exec_backend.resolve_docker_bin(dict(os.environ))
        if not binary or not Path(binary).exists():
            reason = f"kein docker-Binary gefunden ({binary or 'nicht aufloesbar'})"
        elif not _probe_docker(binary):
            reason = "kein laufender Docker-Server"
        elif not _probe_image(binary):
            reason = f"Image {IMAGE} fehlt"
        elif not _probe_uid_passthrough(binary):
            reason = (f"Docker reicht die Host-UID nicht durch: ein Container mit "
                      f"--user {os.getuid()}:0 kann im Bind-Mount nicht schreiben "
                      f"(Docker Desktop mit VirtioFS/gRPC-FUSE, m.rau/bibi#86)")
        else:
            reason = None
    except Exception as exc:      # noqa: BLE001 — Sammlung darf nie scheitern
        reason = f"Docker-Voraussetzung nicht pruefbar: {type(exc).__name__}: {exc}"

    if cached and docker_bin is None:
        _cache = reason
    return reason
