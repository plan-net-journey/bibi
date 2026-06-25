---
name: delete
description: Remove the active case completely. Folder gone, commit + push. Destructive — user confirmation required.
argument-hint:
allowed-tools:
  - Bash
---

# /delete — wipe the case

```bash
bibi-ctrl delete
cd "<path from the cd: line>"   # MUST un-park: the parked cwd was just deleted
```

The shell is parked inside the folder being deleted, so **immediately `cd` to the
repo root** the command prints on its `cd:` line — otherwise the next command runs
from a vanished directory.

## Mandatory confirm before invoking

**Before calling `bibi-ctrl delete`, ask the user for explicit confirmation.**
Name the full folder so it is clear what disappears. Example:

> Soll ich `vault/case/20260517.TestCase-96cdc4d4` jetzt **komplett löschen**
> (Folder weg, commit + push)? Das ist destruktiv und nicht umkehrbar.

Only execute on an explicit »yes«. On anything else (»wait«, »later«, »let me
think«, silence) — abort.

## Effect

1. Derives the active case from the parked cwd (no active case → refuses with a
   pointer to `/open`).
2. `git rm -rf vault/<case_dir>/<folder>/` (gone in working tree and index;
   untracked leftovers cleaned too).
3. Commits `delete: <folder>`, integrates, pushes per the sync matrix (`--push`
   forces when `auto_sync` is off). A never-saved (untracked) case is removed with
   nothing to commit.
4. Clears the `path` display mirror and prints a `cd:` line (repo root) to un-park.

## When

- Case created by mistake. Complete restart of a topic.

## Refuse

- No active case → refuse with a pointer to `/open`.
