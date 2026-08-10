"""``bibi-ctrl statusline`` — rendert die Claude-Code-Statusleiste.

Liest Claudes JSON-Payload (model, ctx%) von stdin und kombiniert ihn mit dem
bibi-Repo-State (`.claude/.state.md` + git) zu einer Zeile:

    [<upgrade> │] <tree> · <sync> │ <branch> │ <model> │ ctx:<pct>% [│ <case> │ proto:<state>] │ sync:<state>

`<upgrade>` steht ganz vorn und nur dann, wenn auf einem **Sitzungs**-Knoten
ein Upgrade wartet (m.rau/bibi#94). Es ist das einzige Segment, das eine
Aufforderung ist und keine Information — deshalb der Vorrang und die inverse
Darstellung, und deshalb verschwindet es wieder, sobald der Neustart gelaufen
ist. Ein Knoten mit Supervisor sieht es nie: dort ist der Restart-Knopf der Weg.

`<tree>` ist clean|modified, `<sync>` ist synced|ahead|behind|diverged — zwei
orthogonale Dimensionen, beide sichtbar; nur der Happy Path `clean · synced`
kollabiert zu `clean`. `diverged` (bis Batch 7 Stufe 3 `conflict` genannt —
umbenannt, User-Fund: "ich verstehe die Bedeutung von conflict und sync:
!conflict nicht") heißt ahead UND behind zugleich > 0, kein echter,
blockierender Merge-Konflikt (`bibi.git_status.working_tree_status()`).

Der letzte `sync:<state>`-Segment ist `!conflict` (aktiver Pull-Konflikt, aus
`.state.md`s `sync_conflict`-Flag — ein davon komplett unabhängiger dritter
Begriff, absichtlich weiterhin "conflict" genannt, weil hier wirklich ein
echter, blockierender `<<<<<<<`-Merge ansteht) > `!stuck(N)` (PLAN-30 Ebene 3:
N Job-Branches nach 3 Fehlschlägen aus dem automatischen Merge-back-Retry
eskaliert, `bibi/daemon/merge_quarantine.py`) > `on`/`off` (stehende Push-
Zustimmung) — in dieser Priorität, nur einer sichtbar.

Der aktive Case kommt aus der **Park-Marke der Session** (`session_id` aus dem
Payload → `state.get_path()`): die Statusleiste läuft in einem Subprozess ohne
Sicht auf das Bash-Tool-cwd der Session (DESIGN §3.2), die Session-ID ist ihr
einziger Zugang. Ohne `session_id` bleibt die Leiste ohne Case-Segment — Claude
Code liefert das Feld immer (m.rau/bibi#99), und ein Fallback auf den geteilten
`.state.md`-Mirror zeigte sonst bei parallelen Sessions den Case einer anderen.
Alle Reads sind netzfrei. Robustheit
geht vor: liegt das cwd in keinem bibi-Repo, fallen die repo-abhängigen Segmente
weg, statt die Leiste crashen zu lassen.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bibi import case_store, git_ops, repo, state, upgrade_notice
from bibi.git_status import working_tree_status

R = "\033[0m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
RED = "\033[31m"
GRAY = "\033[90m"
CYAN = "\033[36m"


def _color(text: str, code: str) -> str:
    return f"{code}{text}{R}"


_TREE_COLOR = {"clean": GREEN, "modified": YELLOW, "conflict": RED}
_SYNC_COLOR = {"synced": GREEN, "ahead": CYAN, "behind": RED, "diverged": RED}


def _git_segment() -> str:
    """Ein billiger, netzfreier Read des Working Tree → gerendertes git-Segment.

    `<tree> · <sync>` mit unabhängigen Farben. Kollabiert zu `clean` (grün),
    wenn beide Dimensionen am Happy Path sind.
    """
    s = working_tree_status(repo.root())
    if s is None:
        return ""
    if s.tree == "clean" and s.sync == "synced":
        return _color("clean", GREEN)
    return (_color(s.tree, _TREE_COLOR[s.tree]) + _color(" · ", GRAY)
            + _color(s.sync, _SYNC_COLOR[s.sync]))


def _case_label(folder: Path) -> str:
    """Sprechender Kurzname: '20260621.Bibi4-870bd9db' → 'Bibi4'.

    Datum und Kurz-Hash sind in der Leiste nur Rauschen; wer sie braucht, sieht
    sie im Ordnernamen (``bibi-ctrl status``).
    """
    name = folder.name
    if "." in name:
        name = name.split(".", 1)[1]
    if "-" in name:
        name = name.rsplit("-", 1)[0]
    return name or folder.name


def _proto_state(folder: Path) -> str:
    pf = case_store.read_frontmatter(folder).get("protocol")
    if not pf:
        return "off"
    return "dbg" if str(pf).endswith("+debug") else "on"


def render(payload: dict[str, Any]) -> str:
    parts: list[str] = []

    # Ein wartendes Upgrade steht VOR allem anderen (m.rau/bibi#94) — es ist
    # eine Aufforderung, keine Information, und eingereiht zwischen Branch und
    # ctx% wäre es ein Segment unter sechsen. Voranstellen statt Verdrängen:
    # die übrigen Segmente bleiben, sonst wäre der Nutzer für die Dauer eines
    # wartenden Upgrades blind.
    try:
        up = upgrade_notice.pending()
        if up:
            parts.append(upgrade_notice.segment(up))
    except (Exception, SystemExit):
        pass

    # git-Segmente — repo-abhängig, defensiv (Statusleiste darf nie crashen;
    # repo.root() beendet außerhalb eines git-Repos mit SystemExit).
    try:
        seg = _git_segment()
        if seg:
            parts.append(seg)
        branch = git_ops.current_branch()
        if branch:
            parts.append(_color(branch, YELLOW))
    except (Exception, SystemExit):
        pass

    model = (payload.get("model") or {}).get("display_name") or ""
    if model:
        parts.append(_color(model, GREEN))

    used_pct = (payload.get("context_window") or {}).get("used_percentage")
    if used_pct is not None:
        parts.append(_color(f"ctx:{used_pct:.0f}%", MAGENTA))

    # bibi-State (Case + proto + sync) — ebenfalls defensiv.
    try:
        # Die Leiste läuft als eigener Prozess ohne Sicht aufs Bash-cwd; die
        # session_id aus dem Payload ist ihr einziger Zugang zum Park-Zustand
        # der Session. Fehlt sie, bleibt die Leiste ohne Case — das ist die
        # ehrliche Auskunft und nicht der Case irgendeiner anderen Session
        # (m.rau/bibi#99).
        state.adopt_session(payload.get("session_id"))
        s = state.read()
        path = state.get_path()
        if path:
            folder = repo.vault() / path
            if folder.exists():
                parts.append(_color(_case_label(folder), CYAN))
                proto = _proto_state(folder)
                color = {"on": GREEN, "dbg": YELLOW, "off": GRAY}[proto]
                parts.append(_color(f"proto:{proto}", color))
        # m.rau/bibi#45: die aktive Persona. Sie verändert das Verhalten des
        # Agenten spürbar und war bis hierhin nur durch einen expliziten
        # ``bibi-ctrl soul`` sichtbar — **jeder andere Zustand dieser Art
        # (Case, proto, sync) steht längst in der Leiste, die Soul war die
        # Ausnahme.** Der Datenzugriff kostet nichts, ``s`` ist oben schon
        # gelesen. Ohne gesetzte Soul bleibt das Segment weg statt „none" zu
        # behaupten: eine Leiste mit sechs Segmenten verträgt keinen Platz für
        # eine Abwesenheit.
        if s.get("soul"):
            parts.append(_color(f"soul:{s['soul']}", CYAN))
        if s.get("sync_conflict"):
            parts.append(_color("sync:!conflict", RED))
        else:
            # PLAN-30 Ebene 3: dieselbe Quarantäne-Liste aus Ebene 2 — ein
            # aktiver Pull-Konflikt (oben) ist dringlicher und gewinnt, sonst
            # fällt ins Auge, wenn Job-Branches auf manuelle Klärung warten.
            from bibi.daemon import merge_quarantine
            stuck = merge_quarantine.escalated(repo.root())
            if stuck:
                parts.append(_color(f"sync:!stuck({len(stuck)})", RED))
            elif s.get("auto_sync") == "on":
                parts.append(_color("sync:on", GREEN))
            else:
                parts.append(_color("sync:off", GRAY))
    except (Exception, SystemExit):
        pass

    return _color(" │ ", GRAY).join(parts)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("statusline", help="intern: Claude-Code-Statusleiste rendern")
    p.set_defaults(func=run)


def run(_: argparse.Namespace) -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    print(render(payload))
    return 0
