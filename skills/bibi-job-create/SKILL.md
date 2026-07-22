---
name: job-create
description: Interactive wizard to create a new bibi4 schedule. Walks through location, trigger, type, soul, prompt/command; writes the MD; triggers a rescan via `bibi-ctrl rescan`.
argument-hint:
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# /job create — schedule wizard

Interactive multi-step wizard that walks the user through a schedule spec
and drops a schedule MD into the vault for the scheduler to pick up.

Ported from bibi3 (PLAN-13 Stufe 13.2) — adapted to bibi4's Unified Job Model
(single `job:` key, `claude:` prefix instead of a separate key) and its
case-centric vault layout. See `vault/CONVENTIONS.md` (this team repo) for the
authoritative frontmatter schema — check it before writing, it may have
evolved since this skill was written.

## Flow

One question at a time via `AskUserQuestion`. Don't ask everything in one
shot — the user should see the next question only after answering the
previous one.

### 1. Trigger type

Question: "When should the schedule run?"

Options:
- **`cron`** — recurring on a cron pattern (e.g. "every 10 minutes",
  "daily at 09:00", "Mondays at 18:00")
- **`at`** — one-shot at a single point in time

### 2. Cron pattern OR at-timestamp

For **cron**: ask for the cron pattern. Offer useful presets:
- `*/10 * * * *` — every 10 minutes
- `0 * * * *` — hourly (top of hour)
- `0 9 * * *` — daily 09:00
- `0 9 * * 1-5` — weekdays 09:00
- "Other" for a custom pattern

For **at**: ask for the timestamp. Accept:
- ISO `2026-06-01T18:00:00`
- Relative `+5min`, `+2h`, `+1d`
- "tomorrow 09:00", "in 5 minutes", or German equivalents

Convert to ISO before continuing. On ambiguity: ask again, don't guess. (For
a one-shot, `bibi-ctrl at "<when>" "<prompt>"` already does steps 2-9 in one
call for the common case — mention it as a shortcut, but continue the wizard
if the user wants the fuller flow, e.g. to set retry parameters or a soul.)

### 3. Location

Run `bibi-ctrl status` first — if `path:` shows an active/parked case, ask
"add this schedule to the active case (`<path>`), or open/create a different
one?" Default: the active case, if any.

If no case is active, or the user wants a different one: ask for a short
topic and run `bibi-ctrl open "<topic>"` (substring-matches an existing case
or creates a new one — the same command `/open` uses). Read its `cd:` output
line for the resulting case path; the wizard's own cwd does not change
(`bibi-ctrl open` only reports the path, actually parking a shell is a
separate, session-level step this skill does not need).

Then ask for a short, descriptive filename for the schedule MD itself
(CamelCase, no extension needed from the user — e.g. "DailyDigest" →
`DailyDigest.md`). Never write into the case's `README.md` — that belongs to
the case lifecycle skills (`/open`/`/save`/`/close`), a schedule MD is always
a separate flat file next to it.

### 4. Execution mode

Question: "How should this schedule run?"

Options:
- **AI job** (default) — a `claude -p` worker driven by a soul + prompt.
  For reasoning, writing, summarising, research.
- **Script / shell job** — a plain command, no LLM. For deterministic,
  idempotent work (RSS polling, a push, a data fetch).

If **AI job** → continue with steps 5 (Soul) + 6 (Prompt).
If **Script job** → skip soul + prompt; instead do step 6b (Command).

Then ask: "Run on the host directly, or in a container (`exec_mode`)?"
Default **host** — container only if the job needs isolation or its own
dependencies baked into an image (`bibi-ctrl doctor`/`DESIGN.md` §7 has more
on when container mode is worth it). Leave `exec_mode` out of the frontmatter
entirely for the default (host) case — only write it when the user picks
container.

If the job is a long-running app the user (or another browser) should reach
(not a one-shot batch run): ask for `app_port` (and optional `app_prefix`).
Most jobs are not apps — skip this unless the user says so.

### 5. Soul   *(AI job only)*

Run `bibi-ctrl soul` (no argument) to see if one is already active — offer it
as the default. List the actually available personas by reading
`.claude/souls/*.SOUL.md` in this team repo (do **not** hardcode a name
list — different teams carry different souls, and this repo's set may not
match bibi3's original six). If `.claude/souls/` is empty or missing, skip
this step entirely — leave `soul:` unset in the frontmatter and say so.

### 6. Prompt   *(AI job only)*

Open question: "What should the schedule do? (prompt for the worker)"

Tip for the user: relative paths in the prompt are resolved by the worker
against the **schedule MD's own folder** (the case dir), not the repo root.
"Read file xy" finds xy relative to that folder.

### 6b. Command   *(script job only)*

Open question: "Which command should run? (shell command, e.g.
`uv run --script collect.py`)"

Tips for the user:
- The command runs with **cwd = the case folder** the schedule MD lives in.
  Reference files relative to that folder.
- The command should be **idempotent** — it may run many times.
- **Status = exit code**: `0` → complete, anything else → failed.
- A scheduled job runs in a **fresh git worktree checked out from `trunk` on
  every fire** — anything gitignored inside it (including `vault/case/*/data/`)
  is wiped before the job starts. State that must survive across fires
  (a watermark, a growing NDJSON, cached API results) needs a path outside
  any worktree — see `vault/CONVENTIONS.md` § "External job data & secrets"
  for the established XDG-style convention, don't invent a new one.
- If the script needs its own dependencies, use `uv run --script` with an
  inline PEP 723 block (`vault/CONVENTIONS.md` § "Job scripts") — never
  `uv run python` directly.

### 7. Optional: retry parameters & explicit slug

Ask explicitly: "Use defaults or customise?"

- **Defaults** (most common, matches the parser's own bibi4 defaults —
  don't hardcode a different set): `attempts: 1, backoff: fixed`, no
  `wall_time` override (unset means no wall-clock limit at all — apps and
  long-running jobs rely on the zombie check instead, not wall_time; only
  set it for a job that should hard-fail past a fixed duration),
  `silence_timeout` left unset (auto-picks 1h for AI jobs, 2h for plain
  jobs, 48h for apps — PLAN-31 Befund 4; only override if the user has a
  specific reason).
- **Customise** → ask only for the fields the user wants to change (most
  commonly `wall_time` for a job that legitimately runs long; `attempts` for
  "no retry, one shot only" — explicit `attempts: 1` already is that).

Explicit `slug:` — ask only if the user wants to decouple the slug from the
filename (e.g. so the MD can be renamed later without losing the schedule's
identity). Default: no explicit slug (derived from the filename).

### 8. Preview + confirm

Before writing the file: show the rendered MD in a code block and ask
"create as shown, or adjust?". On "adjust": ask which field to change.

### 9. Write MD

```yaml
---
schedule: "<cron-pattern>"      # OR:  at: <ISO>
job: <command>                  # OR:  job: "claude: <prompt>"
# include the following only when set / overridden:
# soul: <Soul>
# exec_mode: container
# app_port: <port>
# app_prefix: <prefix>
# slug: <explicit-slug>
# attempts: <n>
# wall_time: <sec>
# silence_timeout: <sec>
# backoff: <linear|fixed|exponential>
---

# <topic headline>

<body text, possibly multiline — optional>
```

Exactly one `job:` key, always — never a separate `claude:` key
(`vault/CONVENTIONS.md`, corrected 2026-07-17: an earlier version of that
file showed `claude:` as its own key, which the parser has never accepted).

Path: `vault/case/<case-folder>/<Name>.md`, next to (not instead of) that
case's `README.md`.

### 10. Rescan + result

```bash
bibi-ctrl rescan
```

This talks to whichever scheduler is actually configured for this node
(`BIBI_SCHEDULER_URL`, possibly remote) — never assume it is local.

Inspect the output:
- Schedule appears in `inserted=` → success
- Errors are printed on stderr → show the error, ask whether to fix or
  delete the MD
- A collision is printed on stderr → warn: same slug already exists, pick a
  different filename or set an explicit `slug:`

On success: compact confirmation:

```
✓ schedule created:
  slug:  <slug>
  path:  vault/case/<...>/<Name>.md
  next:  <ETA, from the rescan output or a quick `bibi-ctrl job list`>
  soul:  <Soul, if set>

Observe: /job list    or the scheduler's web UI (`/-/ui/schedules`)
```

## Refuse

- Scheduler unreachable (`bibi-ctrl rescan` reports it): write the MD anyway;
  tell the user "scheduler unreachable, this will be picked up at the next
  rescan or scheduler restart."
- User wants to abort the wizard: accept gracefully, don't write a
  half-finished MD.
