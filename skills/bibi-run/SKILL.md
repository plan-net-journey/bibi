---
name: run
description: Run a job once locally, right now — bypasses the scheduler (no queue entry), journal + output stay local. Wraps `bibi-ctrl run`.
argument-hint: '<slug> | --cmd "<command>"'
allowed-tools:
  - Bash
---

# /run — local on-demand execution

Thin wrapper around `bibi-ctrl run`. Executes a job **immediately on the local
host**, bypassing the scheduler: no `jobs` entry, no status report — journal
(`domain: local`) and `output.jsonl` stay on this node (DESIGN §1.4, §3.3b).
**No standing `--worker` daemon needed** — it runs in-process.

## Usage

```bash
bibi-ctrl run <slug>             # run a captured schedule MD by slug (one-off, local)
bibi-ctrl run --cmd "echo hi"    # ad-hoc shell command (purely local)
```

The job runs in a fresh `agent/<slug>` worktree, streams to `output.jsonl`, and
the captured output is printed when it finishes. Exit status maps to
`complete` / `failed`.

## When

- A quick, ad-hoc or **sensitive** run that must not enter the central queue.
- Testing a schedule MD locally before letting the scheduler fire it.

## Observe

```bash
bibi-ctrl job list               # NOTE: /run does not appear here (it is not queued)
# the run is recorded only in the local journal (GET /-/journal, domain: local)
```

## Refuse

No refuse — `/run` is always available.
