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
import os
import re
import subprocess
import time
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


def dependency_url(root: Path | None = None) -> str | None:
    """Die git-URL, auf die das Pinning zeigt — dieselbe Zeile, andere Hälfte.

    Sie ist die einzige Stelle im Team-Repo, die weiß, *wo* die Engine
    herkommt. Ohne sie müsste eine Liste verfügbarer Versionen geraten oder
    konfiguriert werden; so kommt sie aus derselben Quelle wie der Soll-Stand
    selbst.
    """
    root = root or repo.root()
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = _DEP_RE.search(text)
    if not m:
        return None
    prefix = m.group(1)
    _, sep, url = prefix.partition("git+")
    return url.strip() if sep else None


#: Wie lange eine einmal geholte Tag-Liste gilt. Der Nodes-Screen rendert bei
#: jedem Heartbeat neu (alle 15 s je Knoten) — ein ``git ls-remote`` pro
#: Durchlauf wäre eine Netzwerkrunde für eine Liste, die sich pro Release
#: einmal ändert.
_REFS_TTL_S = 300.0
_refs_cache: dict = {"at": 0.0, "url": None, "refs": []}

#: Sortierschlüssel: ``v0.10.0`` gehört über ``v0.9.0``, nicht darunter — eine
#: alphabetische Liste hätte genau hier den ersten Fehler gemacht.
_VERSION_PART = re.compile(r"\d+")


def _version_key(tag: str) -> tuple:
    nums = tuple(int(n) for n in _VERSION_PART.findall(tag))
    # Erst nach „ist überhaupt eine Version", dann numerisch: ein Tag ohne
    # Zahlen (``latest``, ``stable``) landet hinten statt die Sortierung zu
    # kippen.
    return (1 if nums else 0, nums, tag)


def available_refs(root: Path | None = None, *, timeout: float = 6.0,
                   now: float | None = None, force: bool = False) -> list[str]:
    """Die Tags des Engine-Repos, neueste zuerst (m.rau/bibi#39-Nachtrag).

    Für die Auswahlliste am Feld „Erwartete Engine-Version": eine Version von
    Hand einzutippen heißt, sie vorher woanders nachgeschlagen zu haben — und
    ein Tippfehler kostet einen ``uv lock``-Fehlschlag, bis er auffällt.

    ``git ls-remote`` statt lokaler Tags: das Team-Repo hat die Tags der Engine
    nicht, es hängt nur per URL an ihr. Bewusst mit ``GIT_TERMINAL_PROMPT=0``
    — ohne das bliebe der Aufruf auf einem Knoten ohne Zugangsdaten an einer
    Passwortabfrage hängen, und der Screen mit ihm.

    Nie eine Exception, im Zweifel eine leere Liste. Die Liste ist Komfort; das
    Feld bleibt ein freies Textfeld, damit ein Branch-Pinning (``dev``) weiter
    möglich ist.
    """
    now = time.time() if now is None else now
    url = dependency_url(root)
    if not url:
        return []
    if (not force and _refs_cache["url"] == url
            and now - _refs_cache["at"] < _REFS_TTL_S):
        return list(_refs_cache["refs"])
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", url],
            capture_output=True, text=True, check=False, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        refs = [ln.split("refs/tags/", 1)[1].strip()
                for ln in proc.stdout.splitlines() if "refs/tags/" in ln]
    except (OSError, subprocess.SubprocessError):
        refs = []
    refs = sorted(set(refs), key=_version_key, reverse=True)
    _refs_cache.update(at=now, url=url, refs=refs)
    return list(refs)


#: Was als *Tag* durchgeht. Nur bei einem Tag ist ein reiner Ref-Vergleich
#: aussagekräftig: ein Tag steht still, ein Branch wandert.
_TAGGISH = re.compile(r"^v?\d+(?:\.\d+)*$")


def _is_tag(ref: str | None) -> bool:
    return bool(ref and _TAGGISH.match(ref.strip()))


def _norm(ref: str) -> str:
    """``v0.3.0`` und ``0.3.0`` sind derselbe Stand — das ``v`` ist Schreibweise,
    kein Unterschied. Ohne diese Normalisierung meldete ein Knoten, der aus
    einem Index statt per VCS-URL installiert wurde, dauerhaft NEED UPDATE:
    ``engine_info().label()`` liefert dort die nackte Version (``0.3.0``),
    ``pyproject.toml`` trägt den Tag (``v0.3.0``)."""
    return ref.strip().lstrip("vV")


def update_status(root: Path | None = None, info=None) -> dict:
    """Liegt dieser Knoten hinter seinem Soll-Stand? (m.rau/bibi#43)

    **Rein lokal.** Beide Angaben liegen ohnehin auf jedem Knoten: das Soll in
    ``pyproject.toml`` (kommt mit dem Repo über den Synchronizer), das Ist in
    ``direct_url.json`` im venv. Kein neues Protokollfeld, keine
    Host-Abhängigkeit — und es funktioniert gerade dann, wenn der Host nicht
    erreichbar ist. Genau das braucht ein hostloses Team, und genau das ist der
    Grund, warum es neben dem nicht-blockierenden Pull des Sitzungsbefehls
    steht: der lässt einen bewusst auf altem Stand starten, hier wird es
    sichtbar.

    **Der Mismatch ist der Normalzustand nach jedem Deploy-Push**, nicht die
    Ausnahme: der Synchronizer zieht die neue ``uv.lock`` binnen 180 s auf jeden
    Knoten, wirksam wird sie aber erst beim Neustart. Zwischen Push und Neustart
    ist jeder Knoten nachweislich zu alt — und dieser Zustand war bisher
    unsichtbar.

    ``verdict`` sagt, *warum* das Ergebnis so lautet, statt ein Ja/Nein zu
    behaupten, das die Datenlage nicht hergibt:

    - ``outdated`` — Soll und Ist sind Tags und verschieden. Der einzige Fall
      mit ``needs_update``.
    - ``current`` — Soll und Ist sind derselbe Tag.
    - ``branch`` — das Pinning zeigt auf einen Branch. Dann ist nur der Commit
      aussagekräftig, und ob der Branch weitergewandert ist, weiß hier lokal
      niemand. Lieber „unbestimmt" sagen als raten.
    - ``editable`` — läuft gegen ein Arbeits-Checkout. Kein Rückstand, sondern
      eine Absicht; der Nodes-Screen kennzeichnet das ohnehin schon.
    - ``unknown`` — eine der beiden Seiten fehlt.
    """
    from bibi.engine_info import engine_info
    info = engine_info() if info is None else info
    expected = current_ref(root)
    out = {"expected": expected, "running": info.label(),
           "needs_update": False, "verdict": "unknown"}
    if info.editable:
        out["verdict"] = "editable"
        return out
    if info.local:
        # Eigenes Urteil statt „unknown" (m.rau/bibi#58): unbekannt sagt zu
        # wenig über einen Zustand, den man kennt — hier läuft nachweislich
        # nicht der gepinnte Stand, sondern eine Kopie eines Verzeichnisses.
        out["verdict"] = "local"
        return out
    if not expected or not info.ref:
        return out
    if not _is_tag(expected):
        out["verdict"] = "branch"
        return out
    if _norm(expected) == _norm(info.ref):
        out["verdict"] = "current"
        return out
    out["verdict"] = "outdated"
    out["needs_update"] = True
    return out


def label_verdict(expected: str | None, label: str | None) -> str:
    """Aktualitäts-Urteil für einen **fremden** Knoten, von dem nur die fertige
    Bezeichnung bekannt ist — dieselbe Frage wie ``update_state()``, nur ohne
    Zugriff auf dessen ``EngineInfo`` (m.rau/bibi#67).

    Die Wörter sind die der Repo-Zelle im Nodes-Screen (``current``/``behind``),
    nicht die internen Verdict-Namen: nebeneinander gelesen sollen Engine- und
    Repo-Zeile dieselbe Sprache sprechen. ``branch`` bleibt, weil es keine
    Entsprechung hat und auch keine braucht — bei einem Branch-Pin weiß hier
    niemand, ob der Branch weitergewandert ist.

    ``unknown`` statt eines Ratens, wenn eine Seite fehlt. Dieselbe
    Zurückhaltung wie in ``label_is_outdated()``: ein falsches Urteil schickt
    jemanden los, etwas zu reparieren, das in Ordnung ist.
    """
    if not expected or not label:
        return "unknown"
    if "(editable)" in label or "(local)" in label:
        # Beide tragen ihren eigenen Chip; ein Aktualitäts-Urteil daneben wäre
        # bedeutungslos — ein Neustart holt keinen gepinnten Stand, wenn das
        # venv aus einem Verzeichnis kommt.
        return "unknown"
    if not _is_tag(expected):
        return "branch"
    # Ein Branch-Pin auf der Ist-Seite („dev @ 86ea20e") ist ebenso unbestimmt,
    # auch wenn der Soll-Stand ein Tag ist: der Commit sagt nichts darüber, ob
    # der Branch inzwischen weiter ist.
    running = label.split()[0]
    if " @ " in label and not _is_tag(running):
        return "branch"
    return "current" if _norm(running) == _norm(expected) else "behind"


def label_is_outdated(expected: str | None, label: str | None) -> bool:
    """Dasselbe Urteil für einen **fremden** Knoten, von dem nur die fertige
    Bezeichnung bekannt ist (``engine``-Feld des Heartbeats).

    Dass der Soll-Stand für alle Knoten derselbe ist, ist keine Annahme,
    sondern folgt aus der geteilten ``uv.lock``: ein Knotennetz fährt ein
    Release. Deshalb genügt hier der Soll-Stand des Hosts.

    Im Zweifel ``False``. Ein falsches NEED UPDATE wäre schlimmer als ein
    fehlendes — es schickt jemanden los, etwas zu reparieren, das in Ordnung ist.
    """
    if not expected or not label or not _is_tag(expected):
        return False
    if "(editable)" in label or "(local)" in label:
        # Beide tragen im Screen ihren eigenen Chip (m.rau/bibi#58). Ein
        # NEED UPDATE daneben wäre irreführend: ein Neustart holt keinen
        # gepinnten Stand, wenn das venv aus einem Verzeichnis kommt.
        return False
    return _norm(label.split()[0]) != _norm(expected)


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
