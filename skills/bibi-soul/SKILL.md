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

## The persona is active without anyone asking for it

Since m.rau/bibi#75 part B the team repo's `.claude/settings.json` runs
`bibi-ctrl soul --hook` on **`SessionStart`** and on **`SubagentStart`**. It
reads the persisted name, loads the matching `.claude/souls/NN.<Name>.SOUL.md`
and hands its prose back as `additionalContext`. Three consequences worth
knowing:

- **A fresh session already carries the persona.** `/soul` is for *changing*
  it, not for activating it.
- **A compaction no longer loses it.** `SessionStart` fires on `compact` too,
  so the persona returns on its own. Until part B that gap was the whole
  complaint: the state said `soul: Rook` while nothing of Rook was in context.
- **Subagents carry it too**, structurally via `SubagentStart` rather than by
  being asked to pass it along.

With no soul set the hook prints nothing and exits 0 — the neutral path, not
an error. Same for a soul whose file has been deleted: the hook runs *before*
the first prompt, and failing there fails where nobody could have acted yet.

## Effect

Switching is two steps, both required:

1. **Persist the choice.** `bibi-ctrl soul <name>` matches `<name>`
   case-insensitively against the `.claude/souls/*.SOUL.md` filenames
   (`NN.<Name>.SOUL.md`), writes the canonical name into the repo-global
   `.state.md` (`soul:` field). Since m.rau/bibi#75 `/state` shows it, and
   so does the status line (m.rau/bibi#45) — until then the only way to learn
   the active persona was to run this command, which is exactly the wrong tool
   for a question you ask *after* a compaction. On an
   unknown name the command aborts (exit 1) and lists the available souls on
   stderr — relay that list back to the user.
2. **Load + adopt the profile.** Read the matching `.claude/souls/NN.<Name>.SOUL.md`
   and adopt that persona for the rest of the conversation, overriding
   whatever was active before. This is what makes a mid-session `/soul` take
   effect **with the next turn** instead of only in the next session — the
   hook above covers session starts, this step covers switches.

With no argument: print whichever persona is currently persisted (or "none
active") — a pure read, no file is loaded. Since part B you no longer need
this to *reactivate* a persona after a compaction: the hook has already put
it back.

## Refuse

- No `.claude/souls/` directory in this team repo: `bibi-ctrl soul <name>`
  reports the missing path and exits 1 — there is nothing to switch to.
- Unknown name: report the exact name tried plus the available list from
  stderr; do not guess a close match.
