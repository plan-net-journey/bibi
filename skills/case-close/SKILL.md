---
name: close
description: Pause the active case. Append a pause note to its README, set status paused, commit + push, clear path.
argument-hint:
allowed-tools:
  - Bash
  - Read
  - Edit
---

# /close — pause the case

`/close` = `/save` (case scope) **+ set status paused + clear path**. Requires an
active case (parked cwd or the session's park marker).

## Steps

1. **Read the active case README.** If there is no active case
   (`bibi-ctrl status` shows no `path:`), refuse with a pointer to `/open`.

2. **Append a short pause-note** based on the conversation:

   ```markdown
   ## Status on pause (YYYY-MM-DD)
   - what was accomplished, where it stops

   ## Resume
   - what's up next, so a future me/Claude can pick it up directly
   ```

   Keep it tight. The Resume section matters more than Status — it's the
   re-entry point.

3. **Flip status + commit + (push):**

   ```bash
   bibi-ctrl close
   ```

   Sets README frontmatter `status: paused`, commits the case scope, integrates,
   pushes per the sync matrix (add `--push` to force when `auto_sync` is off),
   then un-parks the session.

4. **Un-park the shell.** `cd` into the path printed on the `cd:` line (the repo
   root) — this deactivates the case.

## What it does not do

- No deletion (`/delete`). No final marker (`/done`).

## Refuse

- No active case → refuse with a pointer to `/open`.
