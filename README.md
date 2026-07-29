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

## `skills/` ist eine Vendoring-Quelle, keine Begleitdoku

Die `SKILL.md`-Dateien unter `skills/` sind nicht die Beschreibung eines
Verhaltens, sondern die **kanonische Quelle**, aus der jedes Team-Repo seine
`.claude/skills/` zieht (`/library use`, `/library sync` — Vendoring-Modell,
siehe `library.yaml` im Team-Repo). Ein veralteter Skill-Text bleibt deshalb
nicht in diesem Repo: er wird beim nächsten Sync in alle Instanzen kopiert und
gilt dort als der aktuelle Stand.

**Wer das Verhalten eines Kommandos ändert, zieht seinen `SKILL.md`-Text im
selben Commit mit.** Fällt das auseinander, entsteht kein Doku-Rückstand,
sondern eine Regression mit Zeitzünder — sie schlägt erst zu, wenn jemand
guten Glaubens vendort.

Präzedenzfall: PLAN-38 (`3a2daea`, 27.07.2026) stellte `/run` auf in-place
gegen den Live-Checkout um und machte es Client-only, ließ
`skills/bibi-run/SKILL.md` aber auf dem Stand vom 24.07. Der bibi-team-Backport
`4932b6b` hob den Blueprint einen Tag später „auf kanonischen Stand" — und trug
damit die abgeschaffte Worktree-Isolation dorthin zurück, wo sie neuen Teams als
gültig erklärt wurde. Repariert mit `f500543`.

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

## Issue-Tracking

Bugs und Change Requests werden im Issue-Board **dieses** Repos geführt — und
zwar nicht nur für die Engine, sondern gebündelt auch für `bibi-team` und die
Team-Instanz `bibi-notes`. Welchem der drei Repos eine Änderung gehört, ist ein
Label (`repo:engine`, `repo:team`, `repo:notes`), kein Ablageort: die Zuordnung
ergibt sich häufig erst aus der Analyse und darf offen bleiben oder mehrfach
gesetzt werden.

Vorlagen liegen unter `.gitea/ISSUE_TEMPLATE/` (`bug.yaml`, `feat.yaml`). Die
Bug-Vorlage folgt der Struktur Symptom → Root Cause → Live-Befund, die sich in
den früheren Vault-Dossiers bewährt hat; die Root-Cause-Angabe nennt Datei und
Zeilenbereich, nicht nur den Modulnamen.
