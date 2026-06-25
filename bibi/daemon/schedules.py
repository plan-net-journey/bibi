"""Schedule-/At-Einträge aus Markdown listen (PLAN-2 §2.2, DESIGN §4.8).

Phase 2 ist bewusst minimal: scannt ``vault/`` nach MDs mit Trigger-Frontmatter
(``at:``/``cron:``/``every:``) und listet sie. Die Korrelation mit der letzten
Ausführung und die echte Scheduler-Logik kommen mit Phase 3.
"""

from __future__ import annotations

from bibi import frontmatter, repo

_TRIGGER_KEYS = ("at", "cron", "every")


def list_schedules() -> list[dict[str, str]]:
    """Alle Schedule-/At-MDs unter ``vault/`` (Name, Trigger, Pfad)."""
    out: list[dict[str, str]] = []
    vault = repo.vault()
    if not vault.exists():
        return out
    root = repo.root()
    for md in sorted(vault.rglob("*.md")):
        try:
            fm = frontmatter.read(md)
        except Exception:
            continue
        trigger = next((str(fm[k]) for k in _TRIGGER_KEYS if fm.get(k)), None)
        if trigger:
            out.append(
                {"name": md.stem, "trigger": trigger, "path": str(md.relative_to(root))}
            )
    return out
