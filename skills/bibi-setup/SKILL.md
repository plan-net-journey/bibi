---
name: bibi-setup
description: Interview-guided node onboarding for all four node kinds (Client, Worker, Scheduler, Scheduler+Worker) — asks which one this is, installs bibi if needed, configures it non-interactively, brings up the right kind of daemon (session daemon for a client, supervisor for a server) and opens the web UI. Wraps `bibi-ctrl init --non-interactive` + `bibi-ctrl daemon install/run`.
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
known values prefilled, the right daemon running by the end.

**It asks what this node should be and does that** — it does not assume, and
it does not quietly do half of it.

**Scope: all four node kinds.** There are exactly four (m.rau/bibi#174) —
**Client**, **Worker**, **Scheduler**, **Scheduler+Worker** — and the first
question of the interview is which one this is. The role list is the
*answer*, never the question: `synchronizer` is on every node
(m.rau/bibi#163), `connect` follows from whether a scheduler exists rather
than from taste, and `scheduler` rules out `connect` outright
(`bibi/daemon/roles.py`, `validate()`: *"der Scheduler ist das
Verbindungsziel, er verbindet sich nicht zu sich selbst"*). Of the five bits
the engine knows, only two are a real decision — does this node hold the job
database, and does it serve a UI.

| This node is… | `--profile` | roles it derives | daemon |
|---|---|---|---|
| **Client** — a workstation | `client` | `synchronizer,controller` | **session daemon, no supervisor** |
| **Worker** — execution only | `worker` | `synchronizer,worker` | supervisor |
| **Scheduler** — the server | `scheduler` | `synchronizer,scheduler` | supervisor |
| **Scheduler+Worker** | `scheduler+worker` | `synchronizer,scheduler,worker` | supervisor |

**Since `v0.7.2` the engine speaks this vocabulary itself** — `bibi-ctrl init
--profile <name>` derives the roles, so this skill passes the answer through
instead of translating it (m.rau/bibi#174). `--with-ui` adds `controller` to a
scheduler; `--role` still takes a raw list for anyone who wants one. A
scheduler carries no UI by default, and `init` refuses a `worker` with no
scheduler URL rather than configuring one.

**A client comes in two shapes, and the scheduler question decides which
(m.rau/bibi#179).** With a scheduler it also gets `--connect` and joins the
federation. Without one it is a complete, working node on its own — the case
zyklus, `bibi-ctrl run`, its own dashboard and `doctor` all work from day
one. What it gives up is exactly two things: jobs firing on a schedule, and
jobs distributed across machines.

**A worker has no second shape.** Without a scheduler it has nobody to take
orders from — that is not a standalone node, it is a misconfiguration. If the
answer to the scheduler question is "no", the node kind was wrong, not the
answer.

**A client never gets a supervisor, and that is a decision, not an omission
(m.rau/bibi#180).** A workstation daemon exists while someone works and ends
with the session; a service that outlives the person who ordered it is one
nobody ordered. Scheduler and worker are the opposite case — they have to be
there when nobody is watching, so they get systemd/launchd. **The dividing
line is the node kind above, never the operating system** — "there is a
`launchctl`, so install a unit" is exactly the inference that produced the
live incident below.

Until 2026-08-06 this skill knew one shape only: a client, with a scheduler,
under a supervisor. All three assumptions broke in the same week. `bibi-lhg`
is the first instance without a scheduler, and a daemon pointed at one that
does not exist fails in a way a newcomer cannot diagnose (#179). The launchd
service this skill installed on a workstation — `com.bibi.35cea3f6`, plist
written, running — contradicted a decision taken on 2026-08-01 and had to be
removed by hand (#180).

Every step below is idempotent — re-running this skill on an already
configured node is safe, it just confirms the current state instead of
redoing work.

## 0. The repo you were handed

### 0a. Per-user git prerequisites

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

### 0b. The inherited `LICENSE` (m.rau/bibi#178)

A team repo created from the `bibi-team` template carries the blueprint's
MIT `LICENSE` along with everything else. The blueprint needs it — a public
repo without one is "all rights reserved" and unusable as a template. **An
instance almost never does.** The normal case is `--private`: a client
project, an internal vault, working material. An MIT file there states that
anyone may use, change and redistribute the contents, which is a false
statement about the rights situation — and it sits there unread, because
nobody reads a file that appeared by itself.

```bash
test -f LICENSE && head -3 LICENSE && git log -1 --format='%h %s' -- LICENSE
```

If a `LICENSE` is present and this repo is private, **ask** — don't delete
it silently, and don't leave it silently either:

- *private repo* → suggest removing it, with the reason above.
- *public team repo* → it needs a licence decision of its own, not an
  inherited one. Say so and leave the file alone.

This is the one place the question arises by itself instead of having to be
looked up. Live-Fund `bibi-lhg`, 2026-08-06: created from the template, the
MIT file came with it — with both copyright lines — and was removed by hand
once it was noticed.

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

**Second instance on this machine? Settle `BIBI_CONFIG_PATH` here, before
`init` runs — not in a footnote afterwards (m.rau/bibi#173).** `bibi-ctrl
init` rewrites `~/.config/bibi/env` from scratch. Run on a machine that
already carries a node, it takes the first one's configuration with it:
`BIBI_NODE_ID` (the node loses its identity and its `approved` status at the
scheduler), `BIBI_PUBLIC_HOST`, and every `BIBI_JOB_ENV_*` line the file
carries.

Since v0.7.2 that is cushioned in **one** direction only: when the existing
file belongs to a *different* team repo (`BIBI_REMOTE` differs), `init`
copies it to `env.bak-<timestamp>`, says so, and gives this instance its own
`BIBI_NODE_ID` instead of letting it inherit the first one's.

```bash
test -f ~/.config/bibi/env && grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' ~/.config/bibi/env | tr -d '='
```

That prints the variable names without their values — enough to see whether
a node lives there already. Two cases follow, and **both** need a decision:

- **It belongs to a different checkout** → give this instance its own file
  (below). The first node keeps its identity and its credentials, and the
  backup above is the safety net if you get it wrong.
- **It belongs to *this* checkout** → the backup does **not** apply, and
  `write_env()` keeps only the keys in `config.KEYS`. Every
  `BIBI_JOB_ENV_*` line the listing just showed you is gone after `init`,
  silently (m.rau/bibi#183). Copy the file aside by hand first — on a
  scheduler host those lines *are* the credentials it distributes to every
  client.

For the different-checkout case, use the own file for every `bibi-ctrl` call
below:

```bash
export BIBI_CONFIG_PATH="$PWD/data/bibi-env"
```

`config.py` resolves it ahead of `XDG_CONFIG_HOME`, and the sarasate host and
its client have run exactly this way since 2026-07-11 — the capability is
proven, only its mention was missing.

Two follow-ups that decide whether it actually holds:

- **The variable belongs in the unit too**, if step 5 installs one
  (`Environment=BIBI_CONFIG_PATH=…`). `daemon install` does not carry it
  over by itself, so without this the daemon reads the shared file again on
  its next start — the same trap has already sprung in another instance.
- **`BIBI_WORKER_NAME` goes with it.** Without it the second instance
  registers under `socket.gethostname()` and collides with the first in the
  team registry — same dict key, one entry.

Keep the `export` to the shell doing this setup. A shell that also touches
the *other* instance would carry the wrong config file into it.

## 3. Interview

One question at a time via `AskUserQuestion`, not all at once.

- **First, and it decides everything else: what kind of node is this?** Offer
  the four kinds in plain words, not in role names — the role list is what
  this answer produces, not what it asks for:
  - **Client** — *"a workstation. You work here, you see the dashboard, jobs
    run elsewhere."*
  - **Worker** — *"executes jobs it is handed. No UI, no clock of its own."*
  - **Scheduler** — *"the always-on server. It owns the job database and the
    wall clock, and hands work out."*
  - **Scheduler+Worker** — *"the server, and it runs the jobs itself."* The
    usual shape for a team's first server — sarasate is exactly this
    (`vault/TOPOLOGIE.md`).

  Look for evidence to offer as a default before asking blind: `Rollen` from
  step 2 on a re-run, then `vault/TOPOLOGIE.md`, which the team repo's own
  `.claude/CLAUDE.md` names as the place for this kind of fact. Take the
  answer to the table in the scope note above — it gives roles, `--connect`
  and daemon kind in one row, and nothing below needs to re-derive them.

- **Is there a scheduler?** Only for **Client** and **Worker** — the other
  two *are* the scheduler, and asking would be nonsense. Ask plainly, and
  don't infer it from a reachable URL; a scheduler that happens to be down
  would flip the answer. Evidence for a default, in this order: a
  `BIBI_SCHEDULER_URL` already set (step 2), then `vault/TOPOLOGIE.md` — a
  repo that documents "no scheduler" should not be asked twice.
  - **Client, no scheduler** → skip the URL question entirely, no
    `--connect` anywhere below. Say once what that means: scheduled and
    distributed jobs are out, everything else works. Then continue with the
    git remote.
  - **Worker, no scheduler** → stop and go back one question. A worker
    without a scheduler has nobody to take orders from; the node kind was
    wrong, not the answer. Don't quietly configure a standalone worker —
    it starts, reports healthy and never receives anything.
  - **Scheduler** → ask for the URL as described next.

- **Does this node serve a UI?** Only for **Scheduler** and
  **Scheduler+Worker** — a client always has `controller`, a worker never
  does. `roles.controller` gates exactly one thing, `add_controller_routes()`:
  with it the node serves its own `/-/` dashboard, without it `/-/` is a
  `404` and the node is backend only.
  - Derive the default instead of asking cold: if this is the team's **first**
    node, suggest **yes** — otherwise nobody has anything to look at until a
    client exists. If the team already has a client, suggest **no**, which is
    what sarasate did on 2026-08-04 (m.rau: *"der Scheduler alleine soll
    eigentlich nur Backend sein"*).
  - Say which way you are leaning and why, then let the answer stand.
    Whether a scheduler carries `controller` by default is deliberately
    still open on the engine side (m.rau/bibi#174) — this skill asks, it
    does not pre-empt that decision.
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
- **Not asked, because it is derived:** `--role`. The node kind *is* the
  answer, and since `v0.7.2` the engine derives the roles from it itself
  (m.rau/bibi#174). Asking for the list would be asking the same question a
  second time, in the engine's vocabulary instead of the human's.
- **Not asked, left at the engine default:** `--public-host` (only matters
  for a node dispatching app jobs — a pure client never does),
  `--status-poll-interval`/`--job-status-poll-interval` (already-tuned UI
  defaults, no reason to burden a first setup with them).

## 4. Apply

```bash
<bibi-ctrl> init --non-interactive \
  --profile "<client|worker|scheduler|scheduler+worker>" \
  [--with-ui] \
  [--scheduler-url "<answer>"] \
  --remote "<answer>" \
  [--node-name "<answer>"] \
  [--claude-bin "<answer>"]
```

**Pass the profile, not a role list.** The engine owns the mapping since
`v0.7.2`; translating it here would mean two places that must agree, and one
of them would drift. `--profile` and `--role` together are refused outright —
they answer the same question.

**`--with-ui` only for a scheduler that is the team's first node.** On a
client it is a no-op, not an error, so passing it out of habit costs nothing.

**Read the roles back, never assume them.** `bibi-ctrl daemon run` resolves
roles from the config file (`BIBI_CONFIG_PATH` > `XDG_CONFIG_HOME` >
`~/.config/bibi/env`), **not** from any `Environment=BIBI_ROLE=…` a unit
might carry — `daemon install --role` writes that line and nothing reads it.
So the file this step just wrote is the single source of truth for step 5,
and `<bibi-ctrl> status` is how you check what it says.

**`--scheduler-url` only when there is a scheduler.** Without one, leave the
flag off — the engine keeps the field empty, which is the correct state, not
a gap. `bibi-ctrl init` skips the question for the same reason
(`init_cmd.py`, m.rau/bibi#61).

Only pass flags for values the interview actually collected — an omitted
flag keeps the existing value (re-init on an already-configured node) or
falls back to the engine default, exactly like an empty Enter in the old
interactive prompts.

## 5. Daemon start — node kind first, environment second

**Which daemon this node gets was decided in step 3, not here.** Client →
session daemon, no supervisor. Worker, Scheduler, Scheduler+Worker →
supervisor. **Never derive it from which init system happens to exist**
(m.rau/bibi#180) — `command -v launchctl` answers "can I install a unit
here", which is a different question from "should this node have one".

```bash
<bibi-ctrl> daemon status
```

Already running → skip straight to step 6, whichever kind this is.

### 5a. Client — the session brings its own daemon (m.rau/bibi#180)

A client daemon is not installed, it is started by the session that needs
it. `bibi` — the launcher, `bibi/session.py` — starts

```
bibi-ctrl daemon run --host 127.0.0.1 --port auto --session \
    --synchronizer --controller [--connect]
```

as a child of the session and registers that session under `data/sessions/`.
`--session` means the daemon ends when the last session does
(m.rau/bibi#46); `--port auto` means two repos on one machine never have to
agree on a port (m.rau/bibi#45) — read the actual port from `<bibi-ctrl>
status`, never hardcode it.

So on a client there is usually **nothing to start here**:

- **Inside a `bibi` session** (step 2 showed `Herkunft: Sitzung (PID …)`) →
  the daemon is already up. Confirm it and move on.
- **Not in a session** → don't hand-start one. Tell the human to start
  `bibi` in this repo; the daemon comes with it. A `daemon run --session`
  fired from a plain shell registers no session, so the sweeper sees
  `session_registry.count() == 0` on its next tick and shuts the daemon down
  again (`bibi/daemon/sweeper.py`, `_check_sessions`). It looks exactly like
  a start that silently failed.

**If a client already carries a unit, offer to remove it:**

```bash
<bibi-ctrl> daemon uninstall
```

Show what is there and ask — don't remove it silently either. This is not
hypothetical: on 2026-08-06 this skill installed `com.bibi.35cea3f6` on a
workstation, plist in `~/Library/LaunchAgents/`, running, and it had to be
taken out by hand. `vault/TOPOLOGIE.md` had carried the decision for five
days, with a line that reads like a forecast of that exact morning:
*"worth knowing before anyone reinstalls it on the assumption it vanished by
accident."*

**A container is the one exception, and it is about reachability, not
lifetime.** A client in a container reached through a published port cannot
bind `127.0.0.1` (Docker forwards to the container's own address, not to its
loopback), and nothing there registers a bibi session. Start it detached,
without `--session`, bound to `0.0.0.0`:

```bash
mkdir -p data
setsid <bibi-ctrl> daemon run [--connect] --host 0.0.0.0 --port auto \
    > data/daemon.out.log 2>&1 < /dev/null &
disown
```

It still gets no supervisor. Its lifetime is the container's, which is the
same promise the session daemon makes on a workstation.

### 5b. Worker, Scheduler, Scheduler+Worker — supervisor

These have to be there while nobody is watching, so here a unit is the point
rather than the accident.

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
if command -v systemctl >/dev/null 2>&1 || command -v launchctl >/dev/null 2>&1; then
    <bibi-ctrl> daemon install [--connect]
else
    mkdir -p data
    setsid <bibi-ctrl> daemon run [--connect] --host 0.0.0.0 \
        > data/daemon.out.log 2>&1 < /dev/null &
    disown
fi
```

- **`--connect` on a worker, never on a scheduler.** A worker without it has
  no one to take orders from; a scheduler with it is rejected outright by
  `roles.validate()` — it is the connection target, it does not connect to
  itself.
- **The `else` branch is a fallback, and for a server it is a weak one.**
  With no init system present the daemon runs detached but unsupervised: it
  does not come back after a reboot or a crash. Say that plainly instead of
  reporting a clean success — a scheduler nobody restarts is a team whose
  jobs stop overnight without a message.
- No `--role` needed on either branch — both read `BIBI_ROLE` from the
  config file step 4 just wrote.
- **`BIBI_CONFIG_PATH` belongs in the unit** if step 2 introduced one
  (`Environment=BIBI_CONFIG_PATH=…`, alongside `BIBI_WORKER_NAME`).
  `daemon install` does not carry it over, so a unit written without it
  sends the daemon back to the shared config file on its next start
  (m.rau/bibi#173).
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

**Only if this node has `controller`** — every client does, a worker never
does, a scheduler only if step 3 said so. On a node without it, `/-/` is a
`404` by design, not a fault: say where the team's dashboard actually lives
(a client, or the scheduler if it carries a UI) and skip to step 7.

```bash
<bibi-ctrl> daemon status
```

Confirms the daemon actually bound its port. On a client that port is
dynamic (`--port auto`), so read it from the output instead of assuming
`8769` — on a supervised node it is `BIBI_DAEMON_PORT` env >
`BIBI_SCHEDULER_URL`-derived > default `8769`. Then:

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
  kind:       <Client | Worker | Scheduler | Scheduler+Worker>
  scheduler:  <url — or "none (standalone client)" / "this node">
  role:       <the resolved list, as `bibi-ctrl status` reports it>
  daemon:     <session (port) | unit <name> (port) | foreground (port)>
  dashboard:  <URL, "shown above", or "none — this node has no controller">
```

**Report the daemon kind in the words above, not as "started".** A client
that says `session (61874)` and a scheduler that says `unit
bibi-notes-daemon.service (8780)` are making two different promises about
what happens when the person walks away, and that is the distinction this
whole step exists for.

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
- **A worker without a scheduler**: refuse and go back to the node-kind
  question (step 3). It would start, report healthy and never receive
  anything — a failure mode nobody diagnoses from the outside.
- **A supervisor on a client**: refuse, and say why rather than just
  declining. If the human insists after the reason, that is their call —
  but it has to be a decision taken, not one that happened because an init
  system was present (m.rau/bibi#180).
- **Deleting an inherited `LICENSE` unasked** (step 0b): show it, propose,
  let them answer. It is a statement about rights, and this skill is not the
  one who gets to make it.
