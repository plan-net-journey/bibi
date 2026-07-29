---
name: save
description: Capture status in the active case README (if any), then commit + push per the sync matrix.
argument-hint:
allowed-tools:
  - Bash
  - Read
  - Edit
---

# /save — capture status + commit + (push)

## Scope (A10)

`bibi-ctrl save` has two scopes:

- **Active case** (cwd inside a case folder, or the session's park marker from
  `/open`) → commits only the case-related changes.
- **No active case** → checks the *whole repository* for changes. `--repo`
  forces this scope even while a case is active.

Check which one applies with `bibi-ctrl status` — its `path:` line names the
case and where it comes from (`cwd` or `session`).

## Steps

1. **If a case is active:** read its `README.md` and append two tight sections
   based on the conversation — bullets, no essays:

   ```markdown
   ## Status (YYYY-MM-DD)
   - what was accomplished

   ## Next steps
   - what's still open
   ```

   If nothing substantial happened, shorten or omit instead of padding. With no
   active case, skip the README step.

2. **Commit + push:**

   ```bash
   bibi-ctrl save
   ```

   Runs `commit → integrate (rebase/merge) → push`, honoring the sync matrix:

   | `auto_sync` | behavior |
   |---|---|
   | **on** | commit + integrate + **push** |
   | **off** | commit + integrate, **does not push** |

3. **Ask to push (auto_sync off):** when the engine reports it committed but did
   not push, ask the user whether to push now. If yes:

   ```bash
   bibi-ctrl save --push
   ```

   `--push` forces the push regardless of the `auto_sync` flag.

## Conflicts (A8/A11)

If `bibi-ctrl save` reports a **merge conflict**, it has already aborted the
rebase cleanly and raised `sync_conflict`. Resolve it the shared way (same as
`/sync`): read the conflicted files, reconcile both sides, then commit and push
the resolution. Do not force-push over the remote.

## When

- After each self-contained step.
- Before `/close` or `/done`, so the last README state is current.
- Before a break, so the status is visible on resume.
