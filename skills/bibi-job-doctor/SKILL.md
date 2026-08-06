---
name: job-doctor
description: Consistency diagnosis for bibi4 schedules — a thin aggregator over already-existing bibi-ctrl commands (rescan, mergeback, status, doctor), not a separate diagnostic backend.
argument-hint:
allowed-tools:
  - Bash
---

# /job doctor — consistency diagnosis

Ported from bibi3 (PLAN-13 Stufe 13.3) — but bibi4 already solves most of
what bibi3's `/-/doctor` endpoint existed for, just scattered across several
commands instead of bundled under one. This skill **aggregates existing
output**, it does not call a dedicated `/-/doctor` route (there isn't one,
and building one would duplicate logic that already lives elsewhere).

## What it runs, and why each one

Run all four, in this order, and fold the results into one report:

1. **`bibi-ctrl rescan`** — re-scans the vault, reports:
   - slug collisions (two MDs claiming the same slug)
   - parse errors (also visible via `bibi-ctrl doctor`'s `invalid-schedule`
     finding, PLAN-13 Stufe 13.3a — either surfaces them)
2. **`bibi-ctrl mergeback`** — lists `agent/*` branches that failed to merge
   back into trunk and were pulled out of automatic retry (PLAN-30 Ebene 2/3).
   bibi3's `orphan_branch` finding, structurally the same problem.
3. **`bibi-ctrl doctor`** — vault/repo hygiene, including (since Stufe
   13.3a) `orphan-worktree` (a `data/worktrees/<slug>/` directory whose
   schedule no longer exists) and `invalid-schedule` (frontmatter the parser
   rejects) — the two genuinely new checks bibi3 had that bibi4 didn't.
4. **`bibi-ctrl job list --status zombie`** and **`bibi-ctrl job list
   --status error`** — bibi3's `stale_lock`/`heartbeat_stale` findings are
   handled automatically today via `silence_timeout` (PLAN-31) and the
   worker-registry `stale` field (already visible in `/-/status`'s
   `workers` array) — no separate check needed, just surface what's
   currently in these two states so the user sees them in one place instead
   of having to know to look.

## Render

If every command above comes back clean: one friendly line.

```
🩺 bibi-doctor — all consistent (rescan clean, no stuck merges, no hygiene
   findings, no zombie/error jobs).
```

Otherwise: one section per source, only the non-empty ones.

```
🩺 bibi-doctor — 3 findings across 2 sources.

[rescan] 1 collision:
  slug 'daily-digest' claimed by 2 MDs — rename one or set an explicit slug:

[doctor] 2 findings:
  orphan-worktree  data/worktrees/old-experiment — no matching job, safe to
                   remove: `git worktree remove data/worktrees/old-experiment`
  invalid-schedule vault/case/x/Broken.md — Frontmatter braucht `job:`

No zombie/error jobs. No stuck merge branches.
```

Spacing/columns are illustrative — adjust to the real findings. Always name
the concrete next step (a command to run, a file to edit) — a bare finding
without a fix path is not actionable.

## Fixing

There is **no `--fix` flag** for this skill (unlike bibi3's `/job doctor
--fix`) — every finding above already has an established, explicit remediation
that the user runs themselves:

- **Collision** → rename one MD, or set an explicit `slug:` in one of them.
- **orphan-worktree** → `git worktree remove data/worktrees/<slug>` (safe:
  the directory is confirmed to have no matching job).
- **invalid-schedule** → fix the frontmatter at the reported path.
- **Stuck merge branch** → `/sync` (resolves or shows why it's still stuck).
- **Zombie/error job** → usually self-explanatory from `bibi-ctrl job show
  <id>`; reset via `bibi-ctrl job reset <id>` once the underlying cause
  is addressed.

Auto-applying any of these without the user looking at it first was
deliberately descoped (PLAN-13 Stufe 13.3, user decision, 2026-07-17) — a
thin aggregator over commands the user can already run individually, not a
second automated actor touching the repo.

## Refuse

- Scheduler unreachable (`bibi-ctrl rescan`/`job list` fail): report which
  commands succeeded and which didn't, don't fail the whole report over one
  unreachable piece.
