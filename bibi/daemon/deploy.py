"""Die erwartete Engine-Version setzen (m.rau/bibi#39).

**Das Ändern der erwarteten Version _ist_ der Deploy** — es entsteht kein
zweites Soll-Feld neben ``uv.lock``. Der Grund steht im Case
``20260729.ReleaseManagement-f73f7220``: ``pyproject.toml`` trägt die *Absicht*
(„ich will ``bibi@v0.2.3``"), ``uv.lock`` das *aufgelöste Ergebnis* (welcher
Commit, welche 19 weiteren Pakete mit welchen Hashes). Die beiden konkurrieren
nicht, das Ergebnis wird erzeugt. Ein UI-Feld daneben wäre eine dritte Ebene und
würde garantiert divergieren — die Lock gewinnt immer, weil sie committet ist
und über git auf jeden Knoten kommt.

Dass der Controller dafür ins Repo schreiben darf, ist eine ausdrückliche
Entscheidung von m.rau (2026-07-30).

Der Rollback ist derselbe Vorgang mit einem älteren Tag. Kein zweiter
Mechanismus, kein Force-Push, ``master`` und Tags bleiben unangetastet — genau
der Weg, der am 2026-07-30 in beide Richtungen erprobt wurde.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from bibi import repo
from bibi.daemon import activity

log = logging.getLogger("bibi.deploy")

#: ``bibi[daemon] @ git+<url>@<ref>`` in der Abhängigkeitszeile.
_DEP_RE = re.compile(r'(bibi\[daemon\]\s*@\s*git\+[^@"\']*)@([^"\']*)')

#: Was als Ref durchgeht. Bewusst eng: hier wird eine Zeile in einer Datei
#: ersetzt, die anschließend committet und auf jeden Knoten verteilt wird.
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")


def current_ref(root: Path | None = None) -> str | None:
    """Welcher Ref steht gerade in ``pyproject.toml``?"""
    root = root or repo.root()
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = _DEP_RE.search(text)
    return m.group(2) if m else None


def set_expected_version(ref: str, root: Path | None = None,
                         *, push: bool = True) -> dict:
    """``pyproject.toml`` auf ``ref`` setzen, ``uv.lock`` regenerieren,
    committen und pushen.

    Reihenfolge und Abbruchbedingungen sind das Wesentliche:

    1. **Ref prüfen**, bevor irgendetwas geschrieben wird.
    2. **Schreiben und ``uv lock``.** Schlägt das fehl (Tag existiert nicht,
       Remote nicht erreichbar), wird die Datei **zurückgesetzt** — ein
       ``pyproject.toml``, das auf einen unauflösbaren Ref zeigt, würde jeden
       weiteren ``uv run`` auf diesem Knoten scheitern lassen, also auch den
       Daemon-Start. Das wäre der teuerste Fehlerfall.
    3. **Committen und pushen.** Erst damit erreicht die Absicht die anderen
       Knoten; ohne Push bliebe sie lokal und der Deploy liefe ins Leere.

    Der eigentliche Rollout (Neustart der Knoten) passiert **nicht** hier — er
    ist ein eigener Schritt, damit der Aufrufer entscheiden kann, ob sofort
    ausgerollt wird oder erst nach einem Blick auf das Ergebnis.
    """
    root = root or repo.root()
    ref = (ref or "").strip()
    if not _REF_RE.match(ref):
        return {"ok": False, "error": f"unzulässiger Ref: {ref!r}"}

    pyproject = root / "pyproject.toml"
    try:
        before = pyproject.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"pyproject.toml nicht lesbar: {exc}"}

    m = _DEP_RE.search(before)
    if not m:
        return {"ok": False, "error": "keine bibi[daemon]-Abhängigkeit gefunden"}
    old_ref = m.group(2)
    if old_ref == ref:
        return {"ok": True, "changed": False, "ref": ref,
                "note": "unverändert — schon auf diesem Ref"}

    after = _DEP_RE.sub(lambda mm: f"{mm.group(1)}@{ref}", before, count=1)
    pyproject.write_text(after, encoding="utf-8")

    proc = subprocess.run(["uv", "lock"], cwd=root, capture_output=True,
                          text=True, check=False, timeout=300)
    if proc.returncode != 0:
        # Zurückrollen: eine Datei, die auf einen unauflösbaren Ref zeigt, macht
        # jeden `uv run` auf diesem Knoten kaputt — einschließlich des
        # Daemon-Starts.
        pyproject.write_text(before, encoding="utf-8")
        subprocess.run(["git", "checkout", "--", "uv.lock"], cwd=root,
                       capture_output=True, check=False)
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        activity.emit(log, logging.WARNING, "deploy.lock_failed",
                      "Version nicht auflösbar — zurückgesetzt",
                      role="controller", ref=ref)
        return {"ok": False, "error": f"uv lock fehlgeschlagen für {ref}",
                "detail": err[-1] if err else ""}

    from bibi import git_ops
    committed = git_ops.stage_and_commit_paths(
        ["pyproject.toml", "uv.lock"], f"deploy: Engine auf {ref}")
    pushed = False
    if committed and push:
        ok, _out, _kind = git_ops.push(git_ops.current_branch() or "trunk")
        pushed = bool(ok)

    activity.emit(log, logging.INFO, "deploy.version_set",
                  "Erwartete Engine-Version gesetzt", role="controller",
                  ref=ref, was=old_ref, pushed=str(pushed).lower())
    return {"ok": True, "changed": True, "ref": ref, "was": old_ref,
            "committed": committed, "pushed": pushed}
