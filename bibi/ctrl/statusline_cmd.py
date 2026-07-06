"""``bibi-ctrl statusline`` — rendert die Claude-Code-Statusleiste.

Liest Claudes JSON-Payload (model, ctx%) von stdin und kombiniert ihn mit dem
bibi-Repo-State (`.claude/.state.md` + git) zu einer Zeile:

    <tree> · <sync> │ <branch> │ <model> │ ctx:<pct>% [│ proto:<state>] │ sync:<state>

`<tree>` ist clean|modified, `<sync>` ist synced|ahead|behind|conflict — zwei
orthogonale Dimensionen, beide sichtbar; nur der Happy Path `clean · synced`
kollabiert zu `clean`.

Der aktive Case kommt aus dem **Display-Mirror** `path:` in `.state.md`, NICHT
aus dem cwd: die Statusleiste läuft in einem Subprozess ohne Sicht auf das
Bash-Tool-cwd der Session (DESIGN §3.2). Alle Reads sind netzfrei. Robustheit
geht vor: liegt das cwd in keinem bibi-Repo, fallen die repo-abhängigen Segmente
weg, statt die Leiste crashen zu lassen.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bibi import case_store, git_ops, repo, state
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


_TREE_COLOR = {"clean": GREEN, "modified": YELLOW}
_SYNC_COLOR = {"synced": GREEN, "ahead": CYAN, "behind": RED, "conflict": RED}


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


def _proto_state(folder: Path) -> str:
    pf = case_store.read_frontmatter(folder).get("protocol")
    if not pf:
        return "off"
    return "dbg" if str(pf).endswith("+debug") else "on"


def render(payload: dict[str, Any]) -> str:
    parts: list[str] = []

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

    # bibi-State (proto + sync) — über den Mirror, ebenfalls defensiv.
    try:
        s = state.read()
        path = s.get("path")
        if path:
            folder = repo.vault() / path
            if folder.exists():
                proto = _proto_state(folder)
                color = {"on": GREEN, "dbg": YELLOW, "off": GRAY}[proto]
                parts.append(_color(f"proto:{proto}", color))
        if s.get("sync_conflict"):
            parts.append(_color("sync:!conflict", RED))
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
