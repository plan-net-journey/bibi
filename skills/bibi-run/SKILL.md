---
name: run
description: Run a job once locally, right now — bypasses the scheduler (no queue entry), journal + output stay local. Wraps `bibi-ctrl run`.
argument-hint: '<slug> | --cmd "<command>"'
allowed-tools:
  - Bash
---

# /run — local on-demand execution

Thin wrapper around `bibi-ctrl run`. Executes a job **immediately on the local
host**, bypassing the scheduler: it gets a real, gepinnt `jobs` row
(`pinned_host` = this node, so no other worker can ever claim it) and runs
through the same lifecycle a scheduler job would, but `output.jsonl` stays on
this node (DESIGN §1.4, §3.3b, PLAN-28). **No standing `--worker` daemon
needed** — it runs in-process.

## Usage

```bash
bibi-ctrl run <slug>             # run a captured schedule MD by slug (one-off, local)
bibi-ctrl run --cmd "echo hi"    # ad-hoc shell command (purely local)
```

The job runs in a fresh `agent/<slug>` worktree, streams to `output.jsonl`, and
the captured output is printed when it finishes. Exit status maps to
`complete` / `failed`.

`--cmd`/schedule-MD payloads must use **relative paths** (`cwd` is already the
worktree, via `$BIBI_JOB_CWD`) — a hardcoded absolute path into the main
checkout bypasses the worktree isolation entirely (see `/at`'s "Working
directory" section for the failure mode).

## When

- A quick, ad-hoc or **sensitive** run that must not enter the central queue.
- Testing a schedule MD locally before letting the scheduler fire it — the MD
  and any scripts it uses must already be committed to `trunk` (this runs
  against a fresh worktree, same as the scheduler would). For iterating on
  uncommitted edits, use `/test` instead — same idea, but in-place against the
  live tree, no commit needed.

## Observe

```bash
bibi-ctrl job list               # NOTE: /run does not appear here (it is not queued)
# the run shows up in your own local journal: GET /-/run/journal
```

`bibi-ctrl job list`/`kill`/`show` talk to the **scheduler** (`BIBI_SCHEDULER_URL`,
gated on the scheduler role) — on a pure Client node (no `--scheduler`), or
whenever `BIBI_SCHEDULER_URL` points at a remote host, they can never see a
`/run`-pinned row at all, since it was never registered there.

## Manage a pinned job (kill/reset/list)

Long-running `/run` jobs (an App with `app_port`, e.g.) don't exit on their
own — `bibi-ctrl run` itself just blocks polling for a terminal status that
never comes. Manage them directly, no daemon/scheduler-role needed
(PLAN-32 Stufe 32.3, User-Fund: a `/run`-created container kept running after
the normal Jobs-Screen KILL had no effect on it):

```bash
bibi-ctrl pinned list             # this host's /run-pinned jobs
bibi-ctrl pinned kill <id>        # stop it (container/process), mark killed
bibi-ctrl pinned reset <id>       # kill (best-effort) + wipe job data + delete the row
```

`reset` deletes the row outright rather than resetting it to `pending` — a
pinned job has a unique, randomly-suffixed slug (`run_pinned()`'s
`unique_slug`) and is never re-dispatched, so there's no meaningful "pending"
state for it the way there is for a scheduler-owned job.

## Refuse

No refuse — `/run` is always available.
