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

From here on the Bash-tool cwd *is* the active case. `bibi-ctrl save/close/done`
derive the case from this cwd. The cwd persists across calls and survives
context compaction; each session has its own, so parallel sessions never collide.

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
   also updates the `path:` display mirror in `.state.md` (statusline only).

## When

- You start a new topic.
- You resume a paused topic.
- You want to do more on a `closed` topic (with `--force`).

## Note

The active case is the parked cwd. Switch cases mid-session by `cd`-ing into
another folder (after `/save` on the current one, so its README state is
current). Each session / machine / user parks its own cwd independently, so
several cases can be active in parallel — one per shell.

## Configuration

The case directory defaults to `case`. A bibi3-style repo stays compatible by
setting it back to `project`:

```toml
# pyproject.toml (team repo)
[tool.bibi]
case_dir = "project"
```

`BIBI_CASE_DIR` overrides both (mainly for tests).
