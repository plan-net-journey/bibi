"""Aktiver Zustand (DESIGN §3.2, A-Konventionen).

Zwei Geltungsbereiche, zwei Speicher:

- **Aktiver Case (`path`)** — pro Claude-Code-Session in einer **Park-Marke**
  ``data/park/<session_id>`` festgehalten, die ``/open`` schreibt. Das Bash-cwd
  bleibt vorrangige Quelle (wer bewusst in einen Case wechselt, meint das auch),
  die Marke fängt aber alles ab, was das cwd *nicht* überlebt: parallele
  Bash-Calls, die sich gegenseitig überschreiben, Hintergrund-Shells,
  Session-Neustarts und Subprozesse ohne Sicht aufs cwd (Hooks, Statusleiste).
  Die Session-ID isoliert parallele Sessions genauso zuverlässig wie früher das
  cwd — jede schreibt ihre eigene Marke. Ein ``path:``-Mirror in
  ``.claude/.state.md`` existierte bis m.rau/bibi#99 als Fallback und ist
  entfallen: pro-Session-Zustand gehört nicht in eine geteilte Datei.
- **Repo-globale Felder** (``auto_sync``, ``sync_conflict``) — liegen in der
  geteilten, gitignoreten ``.claude/.state.md``.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from bibi import frontmatter, repo

DEFAULT_STATE: dict[str, Any] = {
    "auto_sync": "off",
    "sync_conflict": False,
}

#: Alles außerhalb dieser Zeichen wird im Marken-Dateinamen ersetzt. Die
#: Session-ID kommt aus der Umgebung bzw. einem Hook-Payload — sie darf nicht
#: als Pfad-Traversal (``../``) im Marken-Verzeichnis landen.
_UNSAFE_SESSION_CHARS = re.compile(r"[^A-Za-z0-9._-]")

#: Sessions melden ihr Ende nie — verwaiste Marken werden nach dieser Frist beim
#: nächsten Schreibvorgang weggeräumt, damit ``data/park/`` nicht volläuft.
PARK_TTL_S = 30 * 24 * 3600

_adopted_session: str | None = None


def read() -> dict[str, Any]:
    sp = repo.state_path()
    if not sp.exists():
        return dict(DEFAULT_STATE)
    return {**DEFAULT_STATE, **frontmatter.read(sp)}


def patch(**updates: Any) -> None:
    sp = repo.state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    if not sp.exists():
        sp.write_text(frontmatter.join(DEFAULT_STATE, ""), encoding="utf-8")
    frontmatter.patch(sp, **updates)


def adopt_session(sid: str | None) -> None:
    """Diesen Prozess an eine Session binden.

    Für Subprozesse, die ihre ``session_id`` aus einem stdin-Payload bekommen
    statt aus der Umgebung — Hook-Handler und die Statusleiste. Ein Prozess
    gehört immer zu genau einer Session, deshalb modul-global statt durch jede
    Aufrufebene gereicht.
    """
    global _adopted_session
    sid = (sid or "").strip()
    _adopted_session = sid or None


def session_id() -> str | None:
    """ID der Claude-Code-Session, zu der dieser Prozess gehört.

    ``adopt_session()`` (Hook/Statusleiste) > ``BIBI_SESSION_ID`` (Tests,
    manuelles Übersteuern) > ``CLAUDE_CODE_SESSION_ID`` (vom Bash-Tool gesetzt).
    """
    if _adopted_session:
        return _adopted_session
    for var in ("BIBI_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        raw = os.environ.get(var, "").strip()
        if raw:
            return raw
    return None


def park_file(sid: str | None = None) -> Path | None:
    """Pfad der Park-Marke dieser Session, oder None ohne Session-ID."""
    sid = sid or session_id()
    if not sid:
        return None
    return repo.data() / "park" / _UNSAFE_SESSION_CHARS.sub("_", sid)[:128]


def _path_from_cwd() -> str | None:
    try:
        rel = Path.cwd().resolve().relative_to(repo.vault().resolve())
    except ValueError:
        return None
    rel_str = str(rel)
    return rel_str if rel_str != "." else None


def _path_from_park() -> str | None:
    """Gelesene Park-Marke, aber nur wenn der Case-Ordner wirklich (noch) existiert.

    Ein von Hand gelöschter oder auf einem anderen Knoten entfernter Case darf
    nicht als aktiv weitergemeldet werden — sonst committet ``save`` in einen
    Pfad, den es gar nicht mehr gibt.
    """
    pf = park_file()
    if pf is None:
        return None
    try:
        rel = pf.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not rel or not (repo.vault() / rel).is_dir():
        return None
    return rel


def foreign_parks() -> dict[str, int]:
    """Case-Pfade, auf die Park-Marken **anderer** Sessions zeigen — je mit der
    Zahl der Marken (m.rau/bibi#97).

    Der Unterschied, den ``get_path()`` allein nicht ausdrücken kann: es liefert
    ``None`` sowohl für *„nie geparkt"* (kein Case gemeint, Repo-Scope ist
    richtig) als auch für *„geparkt, aber unter einer anderen Session-ID"* (ein
    Case ist gemeint, wird nur nicht gefunden). Die zweite Lage ist der
    Normalfall nach jeder Wiederverbindung — ``CLAUDE_CODE_SESSION_ID`` wechselt
    dabei, die alte Marke bleibt liegen. Am 2026-08-01 zeigten in ``bibi-notes``
    vier Marken auf denselben Case.

    Die Zahl steht bewusst mit im Ergebnis: eine einzelne fremde Marke könnte
    eine parallel laufende Sitzung sein, vier hintereinander sind die Spur einer
    einzigen, mehrfach neu verbundenen. Ein Pfad, dessen Ordner nicht (mehr)
    existiert, zählt nicht — dieselbe Vorsicht wie in ``_path_from_park()``.
    """
    own = park_file()
    # m.rau/bibi#139, zweite Hälfte: der Case, in dem gerade gearbeitet wird,
    # ist keine fremde Warnung — die Marken darauf sind die Spur der eigenen
    # Vorgänger, die jede Wiederverbindung hinterlässt. Sie mitzuzählen erzeugt
    # ein Rauschen, in dem die eine echte Meldung untergeht, für die #97 diese
    # Funktion überhaupt gebaut hat. Der Ausschluss greift unabhängig davon, ob
    # der aktive Case aus dem cwd oder aus der eigenen Marke kommt.
    aktiv = get_path()
    try:
        entries = sorted((repo.data() / "park").iterdir())
    except OSError:
        return {}
    out: dict[str, int] = {}
    for p in entries:
        if p == own or not p.is_file():
            continue
        try:
            rel = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not rel or rel == aktiv or not (repo.vault() / rel).is_dir():
            continue
        out[rel] = out.get(rel, 0) + 1
    return out


def _forget_case_markers(rel: str) -> None:
    """Alle Marken löschen, die auf ``rel`` zeigen — nicht nur die eigene.

    Ein Case wird von ``/close``/``/done``/``/delete`` für **alle** Sessions
    beendet, nicht nur für die zufällig gerade laufende. Bliebe die Marke einer
    früheren, längst getrennten Sitzung liegen, meldete ``save`` den Case danach
    für immer als „fremd geparkt" (die Marken sterben erst nach ``PARK_TTL_S``,
    30 Tagen) — und ein Warnhinweis, der nie mehr weggeht, wird nach dem zweiten
    Mal überlesen. Genau daran wäre die Meldung aus #97 gescheitert.

    Der Preis ist benannt und in Kauf genommen: arbeitet eine wirklich parallele
    Sitzung am selben Case, verliert sie ihre Marke, wenn hier jemand schließt.
    Sie steht dann da, wo jede Sitzung ohne Marke steht — das cwd trägt sie
    weiter, und ein erneutes ``/open`` parkt neu. Ein Case, den jemand gerade
    geschlossen hat, soll ohnehin nicht anderswo als aktiv weiterlaufen.
    """
    try:
        entries = list((repo.data() / "park").iterdir())
    except OSError:
        return
    for p in entries:
        try:
            if p.is_file() and p.read_text(encoding="utf-8").strip() == rel:
                p.unlink()
        except OSError:
            pass


def get_path() -> str | None:
    """Vault-relativer Pfad des aktiven Case, z. B. 'case/20260517.foo-abc'.

    Zwei Quellen, in dieser Reihenfolge:

    1. **Das Bash-cwd**, wenn es in ``vault/`` liegt — die explizite Geste
       gewinnt (jemand ist bewusst in einen Case gewechselt) und funktioniert
       auch ohne Session-ID.
    2. Die **Park-Marke der Session**, die ``/open`` geschrieben hat.

    None, wenn beides fehlt — dann ist wirklich kein Case aktiv.
    """
    return _path_from_cwd() or _path_from_park()


def path_source() -> str | None:
    """Woher ``get_path()`` seinen Wert nimmt: 'cwd', 'session' oder None.

    Rein diagnostisch (``bibi-ctrl status``) — macht sichtbar, ob die Shell noch
    geparkt ist oder nur noch die Session-Marke trägt.
    """
    if _path_from_cwd():
        return "cwd"
    return "session" if _path_from_park() else None


def set_path(value: str | None) -> bool:
    """Aktiven Case dieser Session setzen; ``None`` un-parkt sie.

    Schreibt die **Park-Marke** ``data/park/<session_id>`` — die einzige Quelle
    (s. ``get_path``). Ohne Session-ID passiert nichts: der aktive Case gehört
    einer Session, und ohne sie ist keine gemeint. Bis m.rau/bibi#99 wurde er
    zusätzlich als ``path:`` nach ``.state.md`` gespiegelt; dieser Mirror hatte
    zuletzt einen einzigen Leser — die Statusleiste ohne ``session_id``, ein
    Fall, der im Betrieb nie eintritt — und beantwortete als **geteilte** Datei
    die Frage nach dem Case einer Session mit dem einer beliebigen anderen.

    Gibt ``True`` zurück, wenn die Marke tatsächlich geschrieben bzw. entfernt
    wurde, und ``False``, wenn es keine Session-ID gab (m.rau/bibi#139). Bis
    dahin war der Ausgang stumm: der Aufrufer meldete „reaktiviert: …" und
    beendete mit 0, während der Case in Wahrheit nur am cwd hing — und dass der
    cwd sich in einer Sitzung mehrfach von selbst zurücksetzt, ist in diesem
    Repo belegt. **Ein Fehler meldet sich, eine ausgebliebene Wirkung nicht;**
    deshalb ist der Rückgabewert kein Komfort, sondern die eigentliche Behebung.
    """
    # Welcher Case gerade verlassen wird, muss VOR dem Schreiben feststehen —
    # danach ist die Marke nicht mehr da (m.rau/bibi#97).
    leaving = get_path() if value is None else None
    pf = park_file()
    if value is None:
        if pf is not None:
            pf.unlink(missing_ok=True)
        if leaving:
            _forget_case_markers(leaving)
        return pf is not None
    if pf is None:
        return False
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(value, encoding="utf-8")
    _prune_park(pf)
    return True


def _prune_park(keep: Path) -> None:
    """Marken toter Sessions wegräumen (Best effort, nie fatal)."""
    cutoff = time.time() - PARK_TTL_S
    try:
        entries = list(keep.parent.iterdir())
    except OSError:
        return
    for p in entries:
        if p == keep:
            continue
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def get_auto_sync() -> bool:
    return read().get("auto_sync", "off") == "on"


def set_auto_sync(value: bool) -> None:
    patch(auto_sync="on" if value else "off")


def auto_sync_was_never_set() -> bool:
    """True, wenn ``auto_sync`` noch nie explizit geschrieben wurde (weder per
    ``set_auto_sync()`` noch von Hand in ``.state.md``) — im Unterschied zu
    ``get_auto_sync()``, das immer den (ggf. defaulteten) Wert liefert. Für den
    scheduler-Default (``daemon_cmd.py``, User-Fund 2026-07-07): der Default
    soll nur greifen, solange niemand je bewusst umgeschaltet hat."""
    return "auto_sync" not in frontmatter.read(repo.state_path())


def get_sync_conflict() -> bool:
    return bool(read().get("sync_conflict", False))


def set_sync_conflict(value: bool) -> None:
    patch(sync_conflict=value)


def get_maintenance() -> bool:
    return bool(read().get("maintenance", False))


def set_maintenance(value: bool) -> None:
    patch(maintenance=value)


def get_soul() -> str | None:
    return read().get("soul") or None


def set_soul(value: str) -> None:
    patch(soul=value)
