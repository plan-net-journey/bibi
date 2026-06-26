---
name: sync
description: Synchronize the repo with origin. `/sync on|off` toggles auto-push; `/sync` runs a manual pull/push and resolves conflicts.
argument-hint: on | off
allowed-tools:
  - Bash
  - Read
  - Edit
---

# /sync — synchronize with origin

## /sync on | off

```bash
bibi-ctrl sync on     # auto_sync → on  (writing skills push without asking)
bibi-ctrl sync off    # auto_sync → off (writing skills commit but ask before push)
```

Toggles the `auto_sync` flag (the standing push consent, §4.9). When a daemon
runs, its sync loop reads this on each tick; in a pure interactive setup the
`SessionStart`/`Stop` hooks honor it.

## /sync (no argument) — manual sync

```bash
bibi-ctrl sync
```

Per §4.9 — **`/sync` never commits by itself**:

- **Dirty tree** → it warns and points to `/save`. Make your semantic commits
  with `/save` first.
- **Clean tree** → integrate origin (pull/rebase) → push if ahead.
- **Merge conflict** → the rebase is left in the working tree and `sync_conflict`
  is set. Resolve it (next section), do not abort blindly.

## Conflict resolution (A8/A11 — shared)

This is the one shared resolution path; `/save`, `/close`, `/done` route their
conflicts here (they abort cleanly and tell you to run `/sync`).

When `bibi-ctrl sync` reports a conflict (or `bibi-ctrl status` shows
`sync_conflict`):

1. List the conflicted files:
   ```bash
   git diff --name-only --diff-filter=U
   ```
2. **Resolve each file** by reading it and reconciling both sides — keep both
   intents where possible; never blindly discard the remote or local side.
   Remove all `<<<<<<<`, `=======`, `>>>>>>>` markers.
3. Continue the rebase and push:
   ```bash
   bibi-ctrl sync continue
   ```
   (`continue` stages the resolved files, finishes the rebase, pushes, and clears
   `sync_conflict`.) If it still reports conflicts, repeat from step 1.

To give up and restore the pre-sync state:

```bash
bibi-ctrl sync abort
```

## Refuse

No refuse — `/sync` is always available.
