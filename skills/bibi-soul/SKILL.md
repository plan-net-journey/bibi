---
name: soul
description: Switch or show the active persona. Wraps `bibi-ctrl soul`, which reads the team repo's own `.claude/souls/*.SOUL.md` files — no hardcoded persona set.
argument-hint: '[<name>]'
allowed-tools:
  - Bash
  - Read
---

# /soul — switch or show the active persona

Souls are team-owned content, not engine code: `bibi-ctrl soul` discovers
whatever `.claude/souls/*.SOUL.md` files exist in the current team repo and
matches against those — never a hardcoded list. Different teams can carry
different persona sets.

## Forms

```bash
bibi-ctrl soul <name>   # switch — case-insensitive, prints the canonical name
bibi-ctrl soul          # show the currently active persona (or "none active")
```

## Effect

Switching is two steps, both required:

1. **Persist the choice.** `bibi-ctrl soul <name>` matches `<name>`
   case-insensitively against the `.claude/souls/*.SOUL.md` filenames
   (`NN.<Name>.SOUL.md`), writes the canonical name into the repo-global
   `.state.md` (`soul:` field — a plain persistence field, not shown by
   `/state`, which stays read-only and scoped to sync/case status). On an
   unknown name the command aborts (exit 1) and lists the available souls on
   stderr — relay that list back to the user.
2. **Load + adopt the profile.** Read the matching `.claude/souls/NN.<Name>.SOUL.md`
   and adopt that persona for the rest of the conversation, overriding
   whatever was active before. This step is **skill-side, not engine-side** —
   `bibi-ctrl soul` only persists the name, it never reads the SOUL.md's prose.

With no argument: print whichever persona is currently persisted (or "none
active") — a pure read, no file is loaded, no persona is adopted. Use this to
check state, not to reactivate a persona (re-issue `bibi-ctrl soul <name>` to
actually reload the profile into context, e.g. after a compaction).

## Refuse

- No `.claude/souls/` directory in this team repo: `bibi-ctrl soul <name>`
  reports the missing path and exits 1 — there is nothing to switch to.
- Unknown name: report the exact name tried plus the available list from
  stderr; do not guess a close match.
