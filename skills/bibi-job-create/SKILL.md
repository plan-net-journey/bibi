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
- **`never`** — registered but never fires by itself; run it by hand
  (`/run` locally, or START/RESET in the web UI). The right answer for a
  test schedule, a draft, or anything whose timing isn't settled yet —
  `never` is a supported special value (`bibi/schedule/parser.py`,
  `SPECIAL_SCHEDULES`), not a trick: the job registers normally and simply
  gets no `next_fire_at`.

Offer `never` actively when the user's own words suggest a trial ("test",
"try out", "probe", "erstmal schauen"). It is the safe default for
experiments, and picking a cron pattern instead is the single most likely
way for this wizard to cause damage — see the warning in step 10.

### 2. Cron pattern OR at-timestamp

For **never**: nothing to ask — write `schedule: never` and continue with
step 3.

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
  don't hardcode a different set): `attempts: 0, backoff: fixed`, no
  `wall_time` override (unset means no wall-clock limit at all — apps and
  long-running jobs rely on the zombie check instead, not wall_time; only
  set it for a job that should hard-fail past a fixed duration),
  `silence_timeout` left unset (auto-picks 1h for AI jobs, 2h for plain
  jobs, 48h for apps — PLAN-31 Befund 4; only override if the user has a
  specific reason).
- **Customise** → ask only for the fields the user wants to change (most
  commonly `wall_time` for a job that legitimately runs long; `attempts` for
  retries). **`attempts` counts retries *in addition to* the first run, and
  its default is `0`** — so `attempts: 0` is "one shot, no retry" and
  `attempts: 1` already means two runs. The field name suggests otherwise;
  the parser is the authority (`bibi/schedule/parser.py`), and a job written
  from the wrong reading runs twice where the user asked for once.

Explicit `slug:` — ask only if the user wants to decouple the slug from the
filename (e.g. so the MD can be renamed later without losing the schedule's
identity). Default: no explicit slug (derived from the filename).

### 8. Preview + confirm

Before writing the file: show the rendered MD in a code block and ask
"create as shown, or adjust?". On "adjust": ask which field to change.

### 9. Write MD

```yaml
---
schedule: "<cron-pattern>"      # OR:  schedule: never   OR:  at: <ISO>
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

**Say this out loud before finishing, it is the wizard's sharpest edge:**
an uncommitted schedule MD is harmless — only this node sees it, and only a
local `/run` executes it. **The commit is what arms it.** Once the MD
reaches `trunk`, the scheduler node's synchronizer pulls it and fires it on
its pattern, on someone else's machine, without anyone re-confirming. A
`*/10 * * * *` test schedule that felt local while it was being tried out
therefore starts producing a job run plus a merge commit every ten minutes
on the production host the moment it is pushed.

That is not hypothetical: it happened on 2026-07-28 (PLAN-37 Befund 6) with
exactly this wizard's default preset — 14 fires plus 14 merge commits in
`trunk` in one day, and the resulting output file went on to cause a sync
divergence that took hours to unwind.

So, when the trigger is `cron` or `at`:
- Tell the user in one sentence that committing arms the schedule on the
  scheduler node, and ask whether that is intended **now** or whether
  `schedule: never` (step 1) plus a manual `/run` is the better fit for the
  moment.
- If they keep the cron pattern, remind them how to disarm it later:
  change `schedule:` to `never` and commit, or delete the MD and commit —
  removing it only locally changes nothing on the host until pushed.

If the schedule writes an output file, add `.gitignore` coverage **before**
the first run, not after: once a run has committed the file, it is tracked,
and a later ignore rule no longer applies to it (that is precisely how the
`probe.log` mess above started — `git rm --cached` is then needed).

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
  armed: <no — uncommitted | no — schedule: never | YES — committed cron/at>

Observe: /job list    or the scheduler's web UI (`/-/ui/schedules`)
```

Fill `armed:` from what is actually true, not from what was intended: a
`never` job shows `next: --` and never fires; an uncommitted MD is invisible
to the scheduler node no matter what its pattern says. If the MD is
committed **and** carries a cron/`at` trigger, say `YES` and name the node
it will fire on.

## Refuse

- Scheduler unreachable (`bibi-ctrl rescan` reports it): write the MD anyway;
  tell the user "scheduler unreachable, this will be picked up at the next
  rescan or scheduler restart."
- User wants to abort the wizard: accept gracefully, don't write a
  half-finished MD.
