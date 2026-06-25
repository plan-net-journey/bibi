---
name: protocol
description: Toggle turn logging in the active case. Writes prompt + final per turn into protocol.json.
argument-hint: on | off | debug
allowed-tools:
  - Bash
---

# /protocol — turn logging

```bash
bibi-ctrl protocol on
bibi-ctrl protocol off
bibi-ctrl protocol debug
```

## Effect

Sets the `protocol:` field in the active case's README frontmatter:

- **`on`** → `protocol: ./protocol.json` (compact: ts, prompt, final, model,
  usage, stop_reason)
- **`debug`** → `protocol: ./protocol.json+debug` (also: tools_used +
  raw_messages)
- **`off`** → field removed; the hook writes nothing more

This is a **pure frontmatter toggle** — it never touches `settings.json`.

## How the logging works

The team-repo's committed `.claude/settings.json` carries a static `Stop` hook
(`bibi-ctrl on-stop`). It runs at every turn end but is **self-gating**: when the
active case has no `protocol:` field, it does nothing. When the field is set, it
extracts the last turn from Claude Code's session log and appends it to
`protocol.json` in the case folder. Idempotent (skips an already-written turn).

## When

- You want to trace token usage and tool use for a case.
- `debug` for full message history (e.g. stream-pattern analysis).

## Refuse

- No active case → refuse with a pointer to `/open`.

## What it does not do

- No truncation: the file grows per turn. In `debug` mode it can get large.
- No rotation or cleanup: delete `protocol.json` in the case folder by hand to
  reset a case's log.
