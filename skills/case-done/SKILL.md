---
name: done
description: Close the active case as final. Append a wrap-up to its README, set status closed, commit + push, clear path.
argument-hint:
allowed-tools:
  - Bash
  - Read
  - Edit
---

# /done — close the case (final)

`/done` = `/close`, but final: `status: closed` and a closing wrap-up instead of
a resume note. Requires an active case (parked cwd).

## Steps

1. **Read the active case README.** No active case (`bibi-ctrl status` shows no
   `path:`) → refuse with a pointer to `/open`.

2. **Append a closing wrap-up** based on the conversation:

   ```markdown
   ## Wrap-up (YYYY-MM-DD)
   - what was accomplished
   - the outcome / what remains
   ```

   No "Next steps" — the case is final. Keep it tight.

3. **Flip status + commit + (push):**

   ```bash
   bibi-ctrl done
   ```

   Sets `status: closed`, commits the case scope, integrates, pushes per the sync
   matrix (`--push` forces when `auto_sync` is off), clears the `path` mirror.

4. **Un-park the shell.** `cd` into the path printed on the `cd:` line (repo root).

## What it does not do

- No merge into `master` (the release line; the maintainer pulls from the working
  branch separately). No deletion (`/delete`).
- Reactivation later is possible via `/open <slug> --force`.

## Refuse

- No active case → refuse with a pointer to `/open`.
