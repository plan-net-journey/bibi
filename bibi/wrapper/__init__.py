"""Generischer Worker-Wrapper (DESIGN §7.5; PLAN-3 §1.2/§3.3).

Ein env-konfigurierter Entrypoint, der den Job-Prozess als **Child** spawnt,
stdout/stderr via Pipe liest und nach ``data/job/{id}/output.jsonl`` appendet.
Der Typ wird aus einer **Registry** (datengetriebenes ``type → TypeHandler``-
Mapping, keine if/else-Kette) bestimmt — neue Typen (``app``, ``openai-sdk`` …)
docken ohne Umbau an.

Aufruf als eigener Prozess: ``python -m bibi.wrapper``. Env (vom Worker gesetzt):

- ``BIBI_JOB_TYPE``   — Registry-Schlüssel (``job``/``claude``).
- ``BIBI_JOB_ID``     — stabile Job-Hash-ID (Container-Name ``bibi-<id>``, Identität).
- ``BIBI_OUTPUT_PATH``— absoluter Pfad der ``output.jsonl`` (vom Worker je run_id gesetzt).
- ``BIBI_WORKTREE``   — Arbeitsverzeichnis des Childs (Worktree).
- typ-spezifisch: ``BIBI_JOB_CMD`` (job), ``BIBI_JOB_PROMPT``/``BIBI_JOB_MODEL`` (claude).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bibi.schedule.models import DEFAULT_CLAUDE_MODEL
from bibi.wrapper import exec_backend, output


@dataclass(frozen=True)
class TypeHandler:
    """Wie ein Typ zu einem Child-Prozess wird (§7.5)."""

    build_command: Callable[[dict[str, str]], list[str]]  # env → argv des Childs
    long_lived: bool = False     # app: kein Silence-Zombie, Wrapper bleibt
    supports_hitl: bool = False  # nur app


def _claude_argv(env: dict[str, str]) -> list[str]:
    container = (env.get("BIBI_EXEC_MODE") or "").strip().lower() == "container"
    # Host: BIBI_CLAUDE_BIN überschreibt das Binary (Tests/Stubs, abs. Pfad bei
    # eingeschränktem PATH). Container: claude liegt im Image auf dem PATH — der
    # Host-Pfad wäre dort sinnlos (Cannot find module), also immer ``claude``.
    binary = "claude" if container else (env.get("BIBI_CLAUDE_BIN") or "claude")
    argv = [binary, "-p", env.get("BIBI_JOB_PROMPT", "")]
    argv += ["--model", env.get("BIBI_JOB_MODEL") or DEFAULT_CLAUDE_MODEL]
    # Container ohne ~/.claude-Settings: claude würde bei Tool-Nutzung (Datei
    # schreiben) nachfragen und headless hängen. ``acceptEdits`` erlaubt Datei-Edits
    # ohne Prompt und funktioniert als root (``--dangerously-skip-permissions`` ist
    # als root verboten). Deckt vault-schreibende Jobs. Host-Modus unverändert
    # (Nutzer-Settings gelten). Volle Autonomie (bash etc.) bräuchte einen non-root
    # Container — späterer Ausbau (PLAN-8 D9).
    if (env.get("BIBI_EXEC_MODE") or "").strip().lower() == "container":
        argv += ["--permission-mode", "acceptEdits"]
    session = env.get("BIBI_JOB_SESSION")
    if session:  # Dialog fortsetzen (§5.3)
        argv += ["--resume", session]
    return argv


#: Das Registry-Mapping. Frontmatter-Key == Typ == Schlüssel (§1.2). ``app`` folgt
#: in Phase 6 ohne Umbau (``long_lived``/``supports_hitl`` sind die Achsen).
REGISTRY: dict[str, TypeHandler] = {
    "job": TypeHandler(build_command=lambda env: ["bash", "-c", env.get("BIBI_JOB_CMD", "")]),
    "claude": TypeHandler(build_command=_claude_argv),
}


def run_job(env: dict[str, str]) -> int:
    """Child gemäß Registry spawnen, Output pumpen, Exit-Code zurückgeben.

    stdout und stderr werden **nebenläufig** gepumpt (sonst Pipe-Deadlock bei viel
    Output); ein Lock hält die chronologische Reihenfolge in ``output.jsonl``."""
    kind = env["BIBI_JOB_TYPE"]
    handler = REGISTRY.get(kind)
    if handler is None:
        raise KeyError(f"unbekannter Job-Typ: {kind!r} (Registry: {sorted(REGISTRY)})")
    child_argv = handler.build_command(env)
    out_path = Path(env["BIBI_OUTPUT_PATH"])
    # Exec-Backend (PLAN-8): Host-Prozess ODER ``docker run …`` um das Child.
    # Output-Pumping/Monitoring bleibt identisch (wir pumpen die Pipes des Spawns).
    spec = exec_backend.build_exec(child_argv, env)

    proc = subprocess.Popen(
        spec.argv, cwd=spec.cwd, env=spec.env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    lock = threading.Lock()

    def pump(pipe, tag: str) -> None:
        assert pipe is not None
        for line in pipe:
            with lock:
                output.append(out_path, tag, line.rstrip("\n"))

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out")),
        threading.Thread(target=pump, args=(proc.stderr, "err")),
    ]
    for t in threads:
        t.start()
    proc.wait()
    for t in threads:
        t.join()
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    env = dict(os.environ)
    code = run_job(env)
    return code


if __name__ == "__main__":
    sys.exit(main())
