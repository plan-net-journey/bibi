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

An explicit `/sync` call is itself the human-in-the-loop consent for
everything below — nothing here needs a separate confirmation:

- **Every unmerged `agent/*` job branch** (PLAN-30 Ebene 3 + its 2026-07-16
  extension) — not just branches that already failed 3 times in a row and
  got escalated. The automatic sweep still waits for trunk to move before
  retrying a not-yet-escalated branch (throttling, avoids hammering a
  standing conflict); an explicit `/sync` call skips that wait and attempts
  it right away. Resolves one branch per call, not all of them
  automatically back-to-back — after a real conflict is opened, `/sync`
  stops there rather than unsupervised cascading into the next one. A quiet
  outcome (the branch is currently untouchable — e.g. it overlaps a file
  you're actively editing right now) does **not** hold up the rest of
  `/sync`; only an actual conflict does.
- **Already-committed work in the active case ("ahead")** — pushed
  unconditionally, regardless of the `auto_sync` flag. An explicit `/sync`
  call is itself the push consent.
- **Origin** — always fetched and integrated (rebase), protected by the same
  idle-window guard as the job-branch merges above: if the incoming pull
  would touch a file that's dirty or was just edited, the *entire* pull
  attempt is skipped this time, not just that one file (a merge is all-or-
  nothing).
- **Dirty changes — active case or any other case** — `/sync` never commits
  them (PLAN-30 Ebene 5; committing is `/save`'s job only). It just lists
  which cases have unfinished work and points you to `/save`.
- **Merge conflict** (job-branch merge above, or the origin pull) — left in
  the working tree, marker files shown. Resolve it (next section), do not
  abort blindly.

## Conflict resolution (A8/A11 — shared)

This is the one shared resolution path; `/save`, `/close`, `/done` route their
conflicts here (they abort cleanly and tell you to run `/sync`). `sync
continue`/`sync abort` detect on their own whether a job-branch merge or an
origin-pull rebase is open — one command for both conflict kinds.

When `bibi-ctrl sync` reports a conflict (or `bibi-ctrl status` shows
`sync_conflict`/a hanging branch count):

1. List the conflicted files:
   ```bash
   git diff --name-only --diff-filter=U
   ```
2. **Resolve each file** by reading it and reconciling both sides — keep both
   intents where possible; never blindly discard the remote or local side.
   Remove all `<<<<<<<`, `=======`, `>>>>>>>` markers.
3. Continue and push:
   ```bash
   bibi-ctrl sync continue
   ```
   (`continue` stages the resolved files, finishes the merge/rebase, pushes,
   and clears the conflict state.) If it still reports conflicts, repeat from
   step 1.

To give up and restore the pre-sync state:

```bash
bibi-ctrl sync abort
```

For a job-branch merge, `abort` only cleans up the working tree — the branch
itself stays unmerged (and, if it was escalated, stays escalated); it does
not resolve anything.

## Refuse

No refuse — `/sync` is always available.
