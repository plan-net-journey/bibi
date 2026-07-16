---
name: state
description: Show the current bibi state — active case, auto_sync, sync_conflict, and protocol mode. Read-only; never mutates state.
argument-hint:
allowed-tools:
  - Bash
---

# /state — show current state

```bash
bibi-ctrl status
```

## Effect

Prints the current state of the team repo and the active case:

- **active case** — derived from the Bash-tool cwd (the parked working dir), the
  single source of truth. Empty if no case is active.
- **auto_sync** — `on`/`off`, the standing push consent (§4.9).
- **sync_conflict** — `true` if a prior pull/rebase left unresolved markers.
- **merge_stuck** — count + branch names of `agent/*` branches that failed to
  merge back into trunk 3+ times in a row and were pulled out of automatic
  retry (PLAN-30 Ebene 2/3); only shown when non-empty. Resolve via `/sync`.
- **protocol** — the active case's logging mode, only shown when set.

## When

- You want a quick read on which case is active and how sync/protocol are set,
  without opening any file.

## What it does not do

- Read-only: it never writes `.state.md`, never commits, never pushes. To change
  state use the lifecycle skills (`/open`, `/save`, `/close`, …) or `/sync`.
