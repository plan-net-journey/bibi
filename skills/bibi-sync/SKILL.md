---
name: sync
description: Synchronize the repo with origin. `/sync on|off` toggles auto-push; `/sync` previews what a sync would do, then applies it after confirmation.
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

## /sync (no argument) — preview, then apply (2026-07-16)

```bash
bibi-ctrl sync           # preview only — no mutation, nothing written
bibi-ctrl sync --apply   # actually do it
```

Same convention as `bibi-ctrl mergeback`: bare = preview/list, `--apply` =
execute. **Agent flow: run the bare command first, show the human what it
found, wait for their go-ahead in conversation, then run `--apply`.** Do not
run `--apply` without that explicit go-ahead — the confirmation happens in
the chat, not inside the CLI (it has no interactive prompt and isn't run
against a live terminal). The one exception: if the preview reports "nichts
zu tun" (exit 0, nothing pending), there's nothing to confirm — no need to
ask, just report that sync is clean.

The preview predicts, without touching anything, exactly what `--apply`
would do:

- **The first unmerged `agent/*` job branch** (PLAN-30 Ebene 3 + its
  2026-07-16 extension) — not just branches that already failed 3 times in a
  row and got escalated. The automatic sweep still waits for trunk to move
  before retrying a not-yet-escalated branch (throttling, avoids hammering a
  standing conflict); an explicit `/sync --apply` skips that wait and
  attempts it right away. Resolves one branch per call, not all of them
  automatically back-to-back — after a real conflict is opened, `--apply`
  stops there rather than unsupervised cascading into the next one. A quiet
  outcome (the branch is currently untouchable — e.g. it overlaps a file
  you're actively editing right now) does **not** hold up the rest of
  `--apply`; only an actual conflict does. The preview shows exactly this:
  "would merge cleanly" / "would conflict on: …" / a quiet status.
- **Already-committed work in the active case ("ahead")** — `--apply` pushes
  it unconditionally, regardless of the `auto_sync` flag (an explicit
  `/sync --apply` is itself the push consent). The preview reports how many
  commits would be pushed.
- **Origin** — always fetched (the preview genuinely fetches — updates
  remote-tracking refs only, no working-tree mutation) and, in `--apply`,
  integrated (rebase), protected by the same idle-window guard as the
  job-branch merge above: if the incoming pull would touch a file that's
  dirty or was just edited, the *entire* pull attempt is skipped this time,
  not just that one file (a merge is all-or-nothing). The preview predicts
  whether the pull would succeed, conflict, or be skipped.
- **Dirty changes — active case or any other case** — neither preview nor
  `--apply` ever commits them (PLAN-30 Ebene 5; committing is `/save`'s job
  only). Both just list which cases have unfinished work and point to
  `/save`.
- **Merge conflict** (job-branch merge above, or the origin pull) — only
  possible under `--apply`; the preview only ever predicts one, never opens
  it. When `--apply` hits a real one, it's left in the working tree, marker
  files shown. Resolve it (next section), do not abort blindly.

A rebase/merge already open from an earlier `--apply` run is not something
the preview can predict around — both bare `sync` and `sync --apply` report
it immediately and point to `sync continue`/`sync abort`.

## Orphaned `agent/*` branches after a rebase

After a successful rebase — in `--apply` as well as in `sync continue` — the
engine reports which `agent/*` branches now point at replaced commits. The
rebase itself causes this: a branch whose commits sat literally on trunk before
now points at SHAs that are gone from trunk, although their content is not.

**It reports, it does not repair**, and the report is not a to-do list to work
off. Only a human can decide whether a branch still carries work worth keeping.
Show the list, name the handle the engine printed (`git branch -f <branch>
trunk`), and let the user decide per branch. Listed are only branches where a
reset provably loses nothing — every commit is already in trunk under a
different SHA. A branch with a partial rewrite stays silent, because there a
reset would throw work away.

## Conflict resolution (A8/A11 — shared)

This is the one shared resolution path; `/save`, `/close`, `/done` route their
conflicts here (they abort cleanly and tell you to run `/sync`). `sync
continue`/`sync abort` detect on their own whether a job-branch merge or an
origin-pull rebase is open — one command for both conflict kinds.

When `bibi-ctrl sync --apply` reports a conflict (or `bibi-ctrl status` shows
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

No refuse — `/sync` (preview) is always available. `--apply` needs the
human's go-ahead from the preview, per the agent flow above.
