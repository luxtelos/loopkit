---
name: loop-tick
description: Advance the LoopKit pipeline by exactly one stage — a lookup over state/triage.md, never a judgement. Use for "work the backlog", "advance the pipeline", any /loop tick.
---

# loop-tick

**Invoked with no arguments, this is not a document to read — it is a job to do.
Start at step 1 now and stop after step 4.** Everything below the tick is
background for when something goes wrong.

Scripts live in the plugin. Every path below is `${CLAUDE_PLUGIN_ROOT}/scripts/…`;
if that variable is not expanded in your shell, use the absolute scripts path the
session-start line printed.

## Do this now

Run the commands. Do not ask which stage to work on, do not summarise this file
back, and do not do more than one stage.

## The tick

**1. Ask what stage is due.** Do not decide this yourself — it is a lookup over
`state/triage.md`, and a model choosing freely once spent ~40 consecutive ticks
re-checking a single PR while 40 findings sat unclassified.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-next.sh"
```

It prints the backlog counts, an optional `POLL:` line, a `STAGE:`, a `TARGET:`
and an `ACTION:`.

**2. If there is a `POLL:` line, run the cheap check first.**

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-watch.sh" --pr <number>
```

A few hundred bytes on a quiet tick against ~9,000 for a full read. `VERDICT:
ACT` or `ESCALATE` preempts and becomes the tick. `QUIET` means ignore it and do
the work stage — a PR in flight must never starve the backlog.

**3. Advance exactly that one stage — for every row at it.**

`loop-next.sh` prints one `TARGET:` (the first row) and then a `TARGETS:` line
per row at that stage (capped at 8). A tick advances the STAGE, not one row of
it: dispatch one implementer (or reviewer, or assessor) per `TARGETS:` row, each
in its own worktree and branch, in parallel — that is what "one worktree per
finding" is for. The one-stage rule exists so transitions stay attributable;
reading it as one ROW per tick leaves a backlog of independent stories moving one
an hour. Rows that share a file or a table are the exception — say so in the row
and run those serially.

| Stage        | What to run                                                   | Then set the row to                                            |
| ------------ | ------------------------------------------------------------- | -------------------------------------------------------------- |
| `new`        | `loopkit:loop-assess` Mode A — baseline, classify, route      | `spec-draft` if `code`; otherwise `inbox`                      |
| `spec-draft` | `loopkit:spec-writer` — EARS criteria, carrying the control case | `spec-ready`                                                |
| `spec-ready` | implementer agent, in its own worktree and branch             | `fixing`                                                       |
| `fixing`     | reviewer agent + the stop gate                                | `pr-open` on PASS; stay `fixing` on FAIL, citing the criterion |
| `pr-open`    | handled by the poll in step 2                                 | `done` on merge                                                |
| `blocked`    | nothing — it waits on a human ruling in `inbox/needs-human.md` | whatever the ruling says                                      |
| `discover`   | `loopkit:morning-triage`                                      | rows appear as `new`                                           |

On every transition into `pr-open`, if the project has a labelling rule, apply
it from a lookup (a script over the diff and the `Part of #NNNN` line), never
by hand. A scope label is how the owner finds the PR at all.

**4. Record the transition and stop.**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triage_state.py" update --state state/triage.md --source "<row source>" --status <new-status>
```

`update` keys on `--source`, not `--finding`. Upsert keys on source too, so two
findings with the same source overwrite each other — give each row a distinct
source (`state/<doc>.md §S14`), never the bare doc path.

Then commit the tick THROUGH THE LOCK, never with a bare `git commit`:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-commit.sh" \
  -m "loop(tick): <row> -> <status>" -- state/triage.md state/<your notes>.md
```

Parallel agents share one git index per worktree, so an unlocked
`git add … && git commit` publishes whatever anyone else has staged — that is
how a review verdict ended up inside commit `6989d63` on 2026-09-07. The
wrapper holds `.loopkit/driver.lock` across the add and the commit;
`triage_state.py` takes the same lock for its own write. A bare `git commit` is
refused by the plugin's `require_commit_lock.py`.

```bash
# lane-scoped tick (lanes come from <project>/.loopkit/scopes.json):
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-next.sh" --scope <lane>
```

**The ORDERING is the discipline; the volume never was.** An earlier version of
this rule read "one stage per tick… stops a tick from turning into an unbounded
work session", and that last clause was a throughput cap doing damage. It was
written to cure a _starvation_ problem — an agent burning ~40 ticks re-checking
one PR — but the cure for starvation is the ordering rule in step 2, not a limit
on how much work a turn may contain. An instruction that is routinely ignored is
worse than no instruction: it still costs context on every prompt and quietly
teaches that the file is optional.

So: pick the stage by lookup, advance it for **every** row at it, record each
transition keyed on `--source`, and stop when the stage is done — not when some
quota is met. What must stay bounded is the _stage_, so each transition is
attributable and the loop is resumable after a crash or a compaction.

## Why `new` is where the value is

Forty unclassified rows is not a backlog of code — it is forty findings nobody
has established the truth of. `loop-assess` exists because a planner goes from
"extract the problem" straight to "outline the fix", so anything that enters
becomes code whether or not code was the answer. Classifying a `new` row is
usually the highest-value thing a tick can do, and it is cheap: most rows
resolve to `inbox` or a non-code layer without any implementation at all.

## Cadence and stopping

**Never sleep on work.** At the end of a tick ask one question: _what am I
waiting ON?_ If the answer is an external process — CI, a human ruling, a PR
review, a deploy, a background agent — arm a wakeup or a Monitor sized to that
process. If the answer is "nothing, the next stage is mine", run the next tick
NOW, in the same turn. A pipeline that naps for 30 minutes between a spec and
its implementer is not pacing itself, it is idling (owner correction,
2026-09-06: "don't sleep unless waiting on some process").

- A wakeup's delay is the process's time, not a clock: a CI run gets its
  duration, a human gets 20–30 minutes, an agent gets a long fallback because
  its completion is the real signal.
- Stop the loop, rather than sleep it, when every row in the lane is `blocked`,
  `inbox`, or `pr-open` under a quiet watch — then say so.
- `noop: false` only when the tick advanced a row or produced an artifact.
  Unrelated work in the same turn does not make the tick non-noop.
- `loop-watch.sh` escalates after 24 unchanged ticks. A watch that quiet has
  stopped producing information — chase the human or stop the loop.

The question "what am I waiting on?" is answered for you: `loop-next.sh` ends
every tick with `NEXT: CONTINUE` (rows still actionable — next tick now, no
wakeup), `NEXT: WAIT` (only a PR review, CI or a human ruling can move things
— the one case for a wakeup) or `NEXT: IDLE` (run morning-triage now). Read
the line; do not re-derive it.

## Invoking it

**From the skill picker: choose `loopkit:loop-tick`. No arguments, nothing to
type.** That is the intended path, and it is why the top of this file is
imperative.

Everything reaches a tick on its own:

- The hooks fire on tool matchers, so merge, approve and stage-everything stay
  blocked whatever the prompt said.
- `CLAUDE.md` auto-loads, so the standing rules are already in context.
- The plugin's `loop_doctrine.py` (UserPromptSubmit) injects the discipline the
  moment a prompt looks like a tick — including a bare `/loop`.

So a repeating loop needs no procedure pasted into it either. This is enough:

```
/loop work the loopkit backlog
```

If you are ever writing out the steps by hand to make a tick behave, that is a
bug in this file or in the doctrine hook, not something to work around.

## If the tick is a status question

"What is in flight", "what needs review", "what can be merged" is not a stage to
advance. Use `loopkit:loop-scan`, or directly:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/loop-scan.py"
```

`READY TO MERGE` is the only line to act on without opening anything.

## Gotchas

- `triage_state.py update` keys on `--source`, not `--finding`. Two rows with
  the same source overwrite each other; give every row a distinct source.
- Never pipe `loop-next.sh` (or any gate) through `grep -q`, `tail` or `head`
  under `pipefail`: the reader exits early, the script takes SIGPIPE, and the
  pipeline reports failure — or, piped through `tail`, reports `tail`'s
  success. Capture to a variable, then read it.
- `TARGETS:` is capped at 8 rows per stage; the `BACKLOG:` counts are not. A
  stage with 20 rows takes three ticks, not one.
- A `blocked` row is never served and never polled. If a row is waiting on a
  human, set it to `blocked`, not `pr-open`, or every tick spends a poll on it.
- A scoped tick that prints `STAGE: discover` may still have work outside the
  lane — read the `unscoped new=N` note before calling the loop idle.
- A `NEXT: CONTINUE` followed by a ScheduleWakeup is the bug this rule exists
  for. The wakeup is for waiting, and CONTINUE means nobody is waiting.
