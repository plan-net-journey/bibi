---
name: at
description: Create a one-shot schedule that runs once at a given time. Wraps `bibi-ctrl at`, which writes an `at:` MD into the case dir and triggers a daemon rescan.
argument-hint: '"<when>" "<payload>"'
allowed-tools:
  - Bash
---

# /at — one-shot schedule

Thin wrapper around `bibi-ctrl at`. It writes a flat `at:` MD into the case
directory and triggers a daemon rescan; the scheduler fires it **once**, at the
given time (DESIGN §5.2).

## Usage

```bash
bibi-ctrl at "<when>" "<payload>"          # claude job (AI prompt) — default
bibi-ctrl at "<when>" "<command>" --job    # job type (shell command)
```

`<when>` is one of:

- ISO 8601 — `2026-07-01T09:00:00`
- relative — `+30s`, `+5min`, `+2h`, `+1d`
- natural language (best-effort) — `tomorrow 09:00`

Examples:

```bash
bibi-ctrl at "+5min"             "Write a short note about topic X"
bibi-ctrl at "2026-07-01T18:00"  "echo deadline reached" --job
```

On an unparseable time `bibi-ctrl at` exits non-zero — fix the argument, don't
guess.

## Daemon

Firing needs a running **scheduler + worker** daemon. Without one the MD is still
written and is picked up on the next rescan / daemon start (`bibi-ctrl at` reports
whether the rescan reached a daemon).

## Observe

```bash
bibi-ctrl job list           # the new one-shot appears as pending until due
bibi-ctrl job show <id>      # status + details once it has run
```

## Refuse

No refuse — `/at` is always available (it only writes a file + best-effort rescan).
