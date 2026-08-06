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

**Scope: client role only.** Sets up `synchronizer,controller`. Worker and
scheduler roles bring their own complexity (job dispatch, wall-clock
ownership) this skill doesn't cover yet — say so if asked, don't quietly add
them.

**A client comes in two shapes, and the first question decides which
(m.rau/bibi#179).** With a scheduler it also gets `--connect` and joins the
federation. Without one it is a complete, working node on its own — the case
zyklus, `bibi-ctrl run`, its own dashboard and `doctor` all work from day
one. What it gives up is exactly two things: jobs firing on a schedule, and
jobs distributed across machines.

Until 2026-08-06 this skill assumed every team had a scheduler and hardcoded
`--connect`. That held while it was true; `bibi-lhg` is the first instance
without one, and a daemon pointed at a scheduler that does not exist fails in
a way a newcomer cannot diagnose.

Every step below is idempotent — re-running this skill on an already
configured node is safe, it just confirms the current state instead of
redoing work.

## 0. Per-user git prerequisites

A freshly created OS user has no `~/.gitconfig`, and therefore no LFS filters
— even when the `git-lfs` binary is installed system-wide. The repo is
already cloned by the time this skill runs, so every LFS-tracked file
(screenshots and other binaries in the vault) has landed as a pointer text
file instead of its content. Check and fix before anything else:

```bash
git config --get filter.lfs.clean >/dev/null 2>&1 || git lfs install
```

Then confirm the working copy is actually intact rather than assuming the
filter fixed it retroactively — `git lfs install` only arms future
checkouts:

```bash
git lfs pull 2>/dev/null || true
```

If `git-lfs` isn't installed at all, say so and stop rather than working
around it: the team's `CLAUDE.md` requires it on every node, and a checkout
full of pointer files is worse than a failed setup because it looks fine.

(Live-Fund PLAN-37, 2026-07-27: this step was missing from every onboarding
path — neither `INSTALL.md` nor this skill mentioned it — and had to be run
by hand on the `mmu` test node.)

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

**Then put `bibi-ctrl` on `PATH`, even though every step below uses the
resolved path.** Claude Code's own hooks and statusline from this repo's
`.claude/settings.json` call it blank, and they run outside this
conversation — they fail with `bibi-ctrl: not found` regardless of how
carefully the steps below qualify the command:

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/bibi-ctrl" ~/.local/bin/bibi-ctrl
```

Skip only if `command -v bibi-ctrl` already resolved in the check above.
The ttyd-container onboarding has always done this (PLAN-33 stages
33.0–33.2); the native-host path lost it in translation, which is why a
successful setup still ended every Claude session with two
`Stop hook error: /bin/sh: 1: bibi-ctrl: not found` lines (Live-Fund
PLAN-37, 2026-07-27).

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

- **First, and it decides the rest: is there a scheduler?** Ask plainly —
  *"Does your team run a scheduler (an always-on server), or is this a
  standalone client?"* Don't infer it from a reachable URL; a scheduler that
  happens to be down would flip the answer. Look for evidence to offer as a
  default, in this order: a `BIBI_SCHEDULER_URL` already set (step 2), then
  `vault/TOPOLOGIE.md` — the team repo's own `.claude/CLAUDE.md` names it as
  the place for exactly this fact, and a repo that documents "no scheduler"
  should not be asked twice.
  - **No scheduler** → skip the URL question entirely, no `--connect`
    anywhere below. Say once what that means: scheduled and distributed jobs
    are out, everything else works. Then continue with the git remote.
  - **Scheduler** → ask for the URL as described next.
- **Scheduler URL** (`--scheduler-url`, only if there is one) — no safe
  generic default (team-private). Suggest one, don't guess silently:
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
  [--scheduler-url "<answer>"] \
  --role synchronizer,controller \
  --remote "<answer>" \
  [--node-name "<answer>"] \
  [--claude-bin "<answer>"]
```

**`--scheduler-url` only when there is a scheduler.** Without one, leave the
flag off — the engine keeps the field empty, which is the correct state, not
a gap. `bibi-ctrl init` skips the question for the same reason
(`init_cmd.py`, m.rau/bibi#61).

Only pass flags for values the interview actually collected — an omitted
flag keeps the existing value (re-init on an already-configured node) or
falls back to the engine default, exactly like an empty Enter in the old
interactive prompts.

## 5. Daemon start — environment-aware

**First: does this node share a machine with another bibi instance?** If the
scheduler (or another client) runs on this same host, this node needs its
own listen port — otherwise `config.daemon_port()` derives it from
`BIBI_SCHEDULER_URL` and collides with the instance already there. There is
no `--port` flag on `daemon install`; it reads `config.daemon_port()`, so
the only way to bake a different port into the unit is the environment
variable. **Set it per command, never `export` it:**

```bash
BIBI_DAEMON_PORT=<free port> <bibi-ctrl> daemon install [--connect]
```

The written unit then carries the port for good (`Environment=BIBI_DAEMON_PORT=…`
plus `--port` in `ExecStart`, see `bibi/daemon/install.py`), so the daemon is
correct from then on without anything left in the shell.

**Why never `export`:** `BIBI_DAEMON_PORT` outranks `BIBI_SCHEDULER_URL` in
`config.scheduler_base_url()` — deliberately, it means "talk to MY OWN
daemon" (PLAN-13). Left standing in the shell, it silently redirects every
later `bibi-ctrl job`/`at` away from the scheduler to this node's own
daemon. The failure is silent and plausible: `/job list` returns a
believable list — the local node's — with no error at all. That cost real
debugging time on the `mmu` test node (Live-Fund PLAN-37, 2026-07-27).

```bash
<bibi-ctrl> daemon status
```

Already running → skip straight to step 6. Otherwise:

```bash
if command -v systemctl >/dev/null 2>&1 || command -v launchctl >/dev/null 2>&1; then
    <bibi-ctrl> daemon install [--connect]
else
    mkdir -p data
    setsid <bibi-ctrl> daemon run [--connect] --host 0.0.0.0 \
        > data/daemon.out.log 2>&1 < /dev/null &
    disown
fi
```

- **`--connect` only if the interview found a scheduler.** Without one it
  would point the daemon at nothing — and the failure looks like a broken
  setup rather than a missing server.
- No `--role` needed on either branch — both read `BIBI_ROLE` from the
  config file step 4 just wrote.
- `daemon install` picks the right bind host itself (`0.0.0.0` for
  systemd/Linux, `127.0.0.1` for launchd/Mac) — nothing to decide here.
- The foreground branch (no init system found — most commonly a container)
  needs `--host 0.0.0.0` explicitly, **always**, not just when a port
  happens to be published: the flag's own default is `127.0.0.1`, which
  stays unreachable through a published container port regardless (Docker
  forwards to the container's own address, not to its loopback interface).
- The foreground branch must run fully **detached**, not merely
  backgrounded in the current shell — a `tmux` session here is the human's
  interactive `claude` UI, nothing else should show up in it or be
  reachable by switching windows. `setsid` (the same detachment `bibi`'s
  own job wrapper already relies on, `bibi/daemon/worker.py`'s
  `start_new_session=True`) starts it in its own session, immune to this
  shell's own lifecycle; `disown` additionally drops it from this shell's
  job table so it won't print a stray "Done" later. Don't use a `tmux`
  window for this — that's still one keystroke away from the human
  stumbling into raw daemon output; don't run it as a plain foregrounded
  Bash-tool call either, that blocks the conversation. stdout/stderr go to
  `data/daemon.out.log`, not the terminal — the daemon already keeps its
  own structured activity log too (`<bibi-ctrl> daemon logs -f`), point the
  human there if they ever want to look, but by default they shouldn't
  need to.
- Confirm it actually started (`<bibi-ctrl> daemon status`, same as step 6
  below) before reporting success — `setsid …&` returns immediately
  regardless of whether the daemon itself then failed to bind.
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
  scheduler:  <url — or "none (standalone client)">
  role:       synchronizer,controller[,connect]
  daemon:     <install|foreground, port>
  dashboard:  <URL, or "shown above" if a browser opened directly>
```

Before reporting success, check the shell you're leaving behind:

```bash
echo "${BIBI_DAEMON_PORT:-(unset)}"
```

If it is set, tell the human plainly that it must go — `unset
BIBI_DAEMON_PORT`, and remove it from any profile file it was added to,
then start a fresh session (a `claude` session inherits the environment it
was launched from, so an already-running one keeps the stale value). Until
then every `/job` and `/at` from this shell silently addresses the local
daemon instead of the scheduler. Don't skip this because the setup itself
succeeded — that is exactly the situation in which it bites, since the
daemon is fine and only the human's later commands go wrong.

## Refuse

- Nothing outright refused — every step here is meant to be safely
  re-runnable. If a step fails, show the actual error and ask how to
  proceed rather than guessing a workaround.
- Worker/scheduler roles: out of scope for this skill's first wave — say so
  if asked, don't quietly add them.
