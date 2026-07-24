---
name: bibi-setup
description: Interview-guided node onboarding — installs bibi if needed, configures it non-interactively, starts the daemon (environment-aware), and opens the web UI. Wraps `bibi-ctrl init --non-interactive` + `bibi-ctrl daemon install/run`.
argument-hint:
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

# /bibi-setup — interview-guided node onboarding

Replaces the manual setup path (`uv venv` → `uv pip install .` → `bibi-ctrl
init` → start the daemon by hand → open the browser yourself) with a single,
safely re-runnable skill: a guided interview instead of a copy-paste runbook,
known values prefilled, the daemon running by the end.

**Scope, first wave (PLAN-33): client role only.** Sets up
`synchronizer,controller` + `--connect` — enough to see the team federation
(job lists, git/sync status of other nodes) and serve this node's own
dashboard. Worker/scheduler roles bring their own complexity (job dispatch,
wall-clock ownership) this skill doesn't cover yet — say so if asked, don't
quietly add them.

Every step below is idempotent — re-running this skill on an already
configured node is safe, it just confirms the current state instead of
redoing work.

## 1. Resolve `bibi-ctrl`

```bash
command -v bibi-ctrl
test -x .venv/bin/bibi-ctrl && echo ".venv/bin/bibi-ctrl"
```

If neither resolves, bibi itself isn't installed yet:

```bash
uv venv
uv pip install .
```

(Already done automatically during a ttyd-container onboarding — this step
then just no-ops, `.venv/bin/bibi-ctrl` already exists.)

**Use the same resolved command for every `bibi-ctrl` invocation below.** A
fresh `Bash` tool call does not inherit `source`-based PATH changes from an
earlier one, so don't activate the venv once and assume it stays active —
either the explicit `.venv/bin/bibi-ctrl` path (most reliable inside a
container, where nothing puts `.venv/bin` on `PATH`) or plain `bibi-ctrl`
(once it genuinely resolves via `command -v`, e.g. most native-host setups).

## 2. Already configured?

```bash
<bibi-ctrl> status
```

Shows the current `Scheduler-URL`/`Rollen` if `~/.config/bibi/env` already
exists — use these as the pre-filled defaults in the interview below rather
than asking blind. If this also shows an active daemon, mention it; step 5
checks again before deciding whether to (re)start anything.

## 3. Interview

One question at a time via `AskUserQuestion`, not all at once.

- **Scheduler URL** (`--scheduler-url`) — no safe generic default (team-
  private). Suggest one, don't guess silently:
  - Already set (step 2) → offer it as the default.
  - Otherwise, check whether this team repo has a `vault/TOPOLOGIE.md` (its
    own `.claude/CLAUDE.md` names it as the place for exactly this kind of
    instance-specific fact) — if it documents a scheduler host/port, offer
    that as the default instead of asking outright.
  - Neither available → ask outright, no default.
  - If the suggested URL doesn't actually respond and this looks like it
    might be a container running on the *same* host as the scheduler, also
    try `http://host.docker.internal:<port>` as a fallback candidate before
    asking the human to type one — the same Docker host-gateway pattern
    `ttyd-onboarding.py`'s `_resolve_gitea_host()` already relies on for an
    identical problem (reaching a locally-hosted service by name from inside
    a container).
- **Git remote** (`--remote`) — auto-suggest, don't ask blind:
  ```bash
  git remote get-url origin
  ```
  Show the result as the default; this repo is already cloned, so the value
  is almost always right as-is, but let the user confirm or override.
- **Node name** (`--node-name`) — optional, suggest rather than skip. Left
  empty, the daemon falls back to `socket.gethostname()` — inside a
  container that's a short, opaque string, not a useful label in the team's
  Nodes screen. Offer a descriptive suggestion instead (e.g. derived from
  the onboarded person's account name, or the machine's hostname on a native
  install).
- **Claude binary** (`--claude-bin`) — usually skip silently (default
  `claude`, resolved via `PATH`, which is enough for the foreground/`tmux`
  path this skill's container case uses). Only ask if `command -v claude`
  fails to resolve at all.
- **Not asked, fixed for this plan's client-only scope:** `--role
  synchronizer,controller` — hardcoded, not prompted (see the scope note
  above).
- **Not asked, left at the engine default:** `--public-host` (only matters
  for a node dispatching app jobs — a pure client never does),
  `--status-poll-interval`/`--job-status-poll-interval` (already-tuned UI
  defaults, no reason to burden a first setup with them).

## 4. Apply

```bash
<bibi-ctrl> init --non-interactive \
  --scheduler-url "<answer>" \
  --role synchronizer,controller \
  --remote "<answer>" \
  [--node-name "<answer>"] \
  [--claude-bin "<answer>"]
```

Only pass flags for values the interview actually collected — an omitted
flag keeps the existing value (re-init on an already-configured node) or
falls back to the engine default, exactly like an empty Enter in the old
interactive prompts.

## 5. Daemon start — environment-aware

```bash
<bibi-ctrl> daemon status
```

Already running → skip straight to step 6. Otherwise:

```bash
if command -v systemctl >/dev/null 2>&1 || command -v launchctl >/dev/null 2>&1; then
    <bibi-ctrl> daemon install --connect
else
    # foreground, blocks -- see the tmux note below before running this
    <bibi-ctrl> daemon run --connect --host 0.0.0.0
fi
```

- No `--role` needed on either branch — both read `BIBI_ROLE` from the
  config file step 4 just wrote.
- `daemon install` picks the right bind host itself (`0.0.0.0` for
  systemd/Linux, `127.0.0.1` for launchd/Mac) — nothing to decide here.
- The foreground branch (no init system found — most commonly a container)
  needs `--host 0.0.0.0` explicitly, **always**, not just when a port
  happens to be published: the flag's own default is `127.0.0.1`, which
  stays unreachable through a published container port regardless (Docker
  forwards to the container's own address, not to its loopback interface).
- The foreground branch **blocks** — don't run it as a plain Bash-tool call
  and wait on it, that leaves the skill stuck mid-conversation. Inside a
  `tmux` session (the ttyd-onboarding case), start it in its own window
  (`tmux new-window -n daemon '<bibi-ctrl> daemon run --connect --host
  0.0.0.0'`) so it keeps running independent of this one; outside `tmux`,
  use the Bash tool's own background-execution option instead of shell-level
  `&` (a `&`'d child isn't guaranteed to outlive the tool call that spawned
  it). Report what you did either way — don't leave the human wondering
  whether it's running in the background or about to disappear when this
  pane/call ends.
- If `daemon install` reports `install FAILED: …` (missing `systemctl`
  despite the check above, a permission error, …) — don't silently fall
  back to the foreground path; show the human the exact failure and ask how
  to proceed.

## 6. Open the dashboard

```bash
<bibi-ctrl> daemon status
```

Confirms the daemon actually bound its port (`BIBI_DAEMON_PORT` env >
`BIBI_SCHEDULER_URL`-derived > default `8769`, unless a custom `--port` was
used anywhere above — none was in step 5). Then:

```bash
if command -v open >/dev/null 2>&1; then
    open "$URL" || echo "couldn't open a browser — link: $URL"
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" || echo "couldn't open a browser — link: $URL"
else
    echo "no browser tool found — link: $URL"
fi
```

No container-detection flag needed — `open`/`xdg-open` are simply absent
inside a typical container image (or fail/do nothing visible), the
`else`/`||` branch catches both cases the same way.

`$URL`: `http://localhost:<port>/` on a native host reached directly. For a
node reached from outside (a ttyd-onboarding container's own published
port, e.g.) — show the actual externally-reachable URL instead, the same
one the human used to reach this terminal in the first place, not
`localhost`.

## 7. Summary

```
✓ node configured:
  scheduler:  <url>
  role:       synchronizer,controller,connect
  daemon:     <install|foreground, port>
  dashboard:  <URL, or "shown above" if a browser opened directly>
```

## Refuse

- Nothing outright refused — every step here is meant to be safely
  re-runnable. If a step fails, show the actual error and ask how to
  proceed rather than guessing a workaround.
- Worker/scheduler roles: out of scope for this skill's first wave — say so
  if asked, don't quietly add them.
