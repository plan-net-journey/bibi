---
name: open
description: Open a new case or reactivate an existing one. Substring-match against existing folders.
argument-hint: <topic>
allowed-tools:
  - Bash
---

# /open — open or reactivate a case

```bash
bibi-ctrl open "<topic>"
```

Then **park the shell** by `cd`-ing into the folder the command prints on its
`cd:` line — this is what makes the case active:

```bash
cd "<path from the cd: line>"
```

`bibi-ctrl open` also writes a **park marker** for this Claude Code session
(`data/park/<session_id>`), so the active case no longer depends on the cwd
holding still. `save/close/done` prefer the cwd when it points into a case, and
fall back to the marker otherwise — parallel sessions stay isolated either way,
each writing its own marker.

Still `cd` into the folder: it keeps relative paths working and makes the active
case obvious in the shell. But you no longer lose the case when a later command
`cd`s elsewhere, or when two parallel Bash calls overwrite each other's cwd.
`bibi-ctrl status` prints where the case is coming from (`cwd` or `session`).

**A reconnect does lose it, and that is worth knowing** (m.rau/bibi#97). The
marker is keyed to `CLAUDE_CODE_SESSION_ID`, and that id changes when a session
reconnects — the old marker stays behind and belongs to nobody. `bibi-ctrl
status` then shows `path: (none)` plus a `park_foreign:` line naming the case,
and `save` refuses the repo scope rather than guessing it (exit code 2). The way
back is this command: run `/open` on the same case again and it parks under the
new id.

## Effect

1. **Substring match** in the case directory (`vault/case/` by default,
   configurable via `[tool.bibi] case_dir` in the repo's `pyproject.toml`): the
   topic is slugified and matched as a substring against folder names.
2. **Exactly one match** ⇒ reactivate: frontmatter `status: open` (also from
   `paused`). If `status: closed`: re-invoke with `--force`.
3. **Multiple matches** ⇒ list them, ask for a more specific topic.
4. **No match** ⇒ create: `vault/<case_dir>/YYYYmmdd.<slug>-<short>/` with
   `README.md` (frontmatter `status: open`).
5. In all open/create cases the command prints a `cd:` line — **cd into it.** It
   also writes the session's park marker — the only store for the active case.

## When

- You start a new topic.
- You resume a paused topic.
- You want to do more on a `closed` topic (with `--force`).

## Note

Switch cases mid-session by running `/open` on the other one (after `/save` on
the current one, so its README state is current) — that rewrites the session's
park marker. A bare `cd` into another case folder also switches, since the cwd
takes precedence while it points into a case. Each session / machine / user
parks independently, so several cases can be active in parallel.

## Configuration

The case directory defaults to `case`. A bibi3-style repo stays compatible by
setting it back to `project`:

```toml
# pyproject.toml (team repo)
[tool.bibi]
case_dir = "project"
```

`BIBI_CASE_DIR` overrides both (mainly for tests).
