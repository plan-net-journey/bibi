# bibi

Deploybare Engine für Markdown-geführte Team-Repos: stellt `bibi-ctrl` (CLI) sowie
die Skill- und Agent-Quellen bereit, aus denen Team-Repos ihre Bausteine beziehen.

Teil des **Bibi4**-Vorhabens — die generalisierte, deploybare Weiterentwicklung von
bibi3 (Team-Repository + verteilte Daemon-Laufzeit). Das maßgebliche Design liegt
im Projekt-Vault (`DESIGN.md`).

## Struktur

```
bibi/
├── pyproject.toml      Python-Paket `bibi`, Entry-Point `bibi-ctrl`
├── bibi/
│   ├── __init__.py
│   └── ctrl/           CLI (Phase 0: init)
├── skills/             SKILL.md-Quellen (Phase 1+)
└── agents/             AGENT.md-Quellen (spätere Phasen)
```

## Sync-Strategie

`git_ops.integrate()` unterstützt bei echter Divergenz zwei Strategien
(`strategy="rebase"|"merge"`, Default `"rebase"`): Rebase für den
interaktiven `/sync`-Pfad (lineare Historie, ein Mensch löst einen Konflikt
tatsächlich auf); Merge für den unbeaufsichtigten Hintergrund-Pull des
Synchronizers (`daemon/synchronizer.py::_default_pull`) — robuster gegen
botgenerierte Commit-Historie, bei der ein Rebase an einem Zwischenschritt
scheitern kann, obwohl der Endstand konfliktfrei mergen würde.

## Entwicklung

```bash
uv pip install -e .
bibi-ctrl init
```

Default-Branch: `trunk`.
