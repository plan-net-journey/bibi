---
name: job
description: List, inspect, kill, or reset scheduler jobs via the local daemon. Wraps `bibi-ctrl job` (the `/-/job` + `/-/scheduler` endpoints).
argument-hint: '[list | show <id> | start <id> | kill <id> | reset <id> | rescan]'
allowed-tools:
  - Bash
---

# /job — inspect & control scheduler jobs

Thin wrapper around `bibi-ctrl job`, the scheduler view (remote/disposed jobs).
Needs a running **scheduler** daemon (`--scheduler`).

## Forms

```bash
bibi-ctrl job list [--status <s>]   # all jobs: slug, status, kind, id (+reason)
bibi-ctrl job show <id>             # one job, full JSON (status + root cause)
bibi-ctrl job start <id>            # run a PENDING job now, without waiting for its trigger
bibi-ctrl job kill <id>             # stop a RUNNING job → killed (by_user, §5.6)
bibi-ctrl job reset <id>            # reset a TERMINAL job → pending (re-scheduled)
bibi-ctrl job rescan                # re-scan the vault for new/removed schedule MDs
```

The three control verbs map directly onto the lifecycle (DESIGN §5.6):

- **start** — make a `pending` job due **now** (it fires on the next tick); only
  valid from `pending` (else 409).
- **kill** — `running → killed`; only valid while the job runs (else 409).
- **reset** — `<terminal> → pending` (a fresh re-enqueue); only valid from a
  terminal state (complete/error/inactive/zombie/killed).

> Recurring (`cron`) schedules re-arm themselves after each run, so their **live
> status is usually `pending`** between fires — the run history is in `/-/journal`.

## Complement

- `/job` is the **scheduler** view (queued / disposed jobs).
- `/run` is the **local** view (on-demand, never queued).

## Refuse

No refuse — `/job` is always available; individual verbs report 404/409 when the
job is missing or in the wrong state.
