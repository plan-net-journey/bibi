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

Nearly always does *something* — it is no longer an all-or-nothing gate that
refuses on any dirty tree:

- **Changes outside the active case** (with or without one parked) — grouped
  into sensible commit clusters and committed **without asking again**: one
  commit per other case folder, plus one collective commit for everything
  that belongs to no case (`vault/memo/`, `vault/attach/`, repo-root files,
  …). Running `/sync` at all is the human-in-the-loop approval — steer the
  grouping in conversation beforehand if you want it different, there's no
  separate per-cluster confirmation.
- **Already-committed work in the active case ("ahead")** — pushed
  unconditionally, regardless of the `auto_sync` flag. An explicit `/sync`
  call is itself the push consent.
- **Origin** — always fetched and integrated (rebase), whether or not
  anything local changed.
- **Uncommitted changes in the active case** — the one thing `/sync` leaves
  alone. It lists them and points you to `/save`; nothing else does.
- **Merge conflict** (from either the new cluster commits or pre-existing
  ahead commits) — the rebase is left in the working tree and `sync_conflict`
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
