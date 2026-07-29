---
name: run
description: Run a job once locally, right now — against the live checkout, bypassing the scheduler. Client-only. Wraps `bibi-ctrl run`.
argument-hint: '<slug> | --cmd "<command>"'
allowed-tools:
  - Bash
---

# /run — local on-demand execution

Thin wrapper around `bibi-ctrl run`. Executes a job **immediately on this
node**, bypassing the scheduler: it gets a real, pinned `jobs` row
(`pinned_host` = this node, so no other worker can ever claim it) and runs
through the same lifecycle a scheduler job would, but `output.jsonl` stays on
this node (DESIGN §1.4, §3.3b, PLAN-28). **No standing `--worker` daemon
needed** — it runs in-process.

## Usage

```bash
bibi-ctrl run <slug>             # run a captured schedule MD by slug (one-off, local)
bibi-ctrl run --cmd "echo hi"    # ad-hoc shell command (purely local)
```

The job runs **in place, against the live checkout** — your uncommitted edits
are what it sees, and whatever it writes lands directly in the vault as
`modified`/`untracked`, for you to review and `/save` (PLAN-38). There is no
worktree, no `agent/<slug>` branch, and nothing to merge back.

## Client-only

`/run` is refused on a node that carries the `scheduler` or `worker` role — it
would write into a checkout the Synchronizer is concurrently pulling and
merging, and a regular fire of the same job expects reproducible worktree
isolation. To start something on such a node, use the scheduler instead:
`bibi-ctrl job start <id>` (`/job`).

## When

- A quick, ad-hoc or **sensitive** run that must not enter the central queue.
- Iterating on a schedule MD or the script it runs — no commit needed first,
  since the run sees the working tree as it is right now.

Note that this is exactly what the former `/test` did. That skill is gone:
`/run` absorbed it (PLAN-38), and `bibi-ctrl test` survives only as a
deprecation alias.

## Auto-sync interaction

With `auto_sync: on`, the result does **not** stay uncommitted: the run commits
its own changed paths when it finishes, with job provenance in the subject
(`<slug>: run <run-id>`), and the Synchronizer pushes it. `bibi-ctrl run`
announces this before starting. Run `/sync off` first if you want to look at
the result before it lands. With `auto_sync: off` (the default) nothing is
committed for you.

## Observe

```bash
bibi-ctrl job list               # NOTE: /run does not appear here (it is not queued)
# the run shows up in your own local journal: GET /-/run/journal
```

`bibi-ctrl job list`/`kill`/`show` talk to the **scheduler**
(`BIBI_SCHEDULER_URL`, gated on the scheduler role) — on a Client node they can
never see a `/run`-pinned row at all, since it was never registered there.

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

- On a `scheduler`/`worker` node: refuse and point at `/job` (see Client-only
  above). The CLI enforces this itself; don't work around it.
