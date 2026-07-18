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

## Refuse

No refuse — `/run` is always available.
