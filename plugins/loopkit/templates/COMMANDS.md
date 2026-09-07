# COMMANDS.md — how work fires

> Files hold the memory; commands move it. This is the inventory of what can
> run, what gates it, and how it runs without a human typing.

The loop's machinery is supplied by the `loopkit` plugin. Scripts live at
`${CLAUDE_PLUGIN_ROOT}/scripts/` (the session-start line prints the absolute
path); the plugin's `hooks/hooks.json` wires the gates.

## Agents (plugin)

| Agent         | Role                                                                                      |
| ------------- | ----------------------------------------------------------------------------------------- |
| `planner`     | Decomposes a classified finding into a DRAFT spec                                         |
| `implementer` | Generator — drafts code in its own worktree, tests first                                  |
| `reviewer`    | Evaluator — different instructions, judges via tools; assumes code is BROKEN until proven |

Constitution: the agent that wrote code never approves it.

## Skills (plugin)

| Skill                       | Fires when                                                                 |
| --------------------------- | -------------------------------------------------------------------------- |
| `loopkit:loop-tick`         | Any `/loop` tick or "work the backlog" — advances exactly one stage        |
| `loopkit:loop-scan`         | A status question — what is open, green, mergeable, unclassified           |
| `loopkit:loop-assess`       | A `new` row, an alert, a complaint — baseline, classify, route             |
| `loopkit:morning-triage`    | Start of a work cycle — finds and ranks work into `state/triage.md`        |
| `loopkit:spec-writer`       | A finding classified `code` needs EARS acceptance criteria in `specs/`     |
| `loopkit:acceptance-review` | A change claims done — verify by acting, against the spec                  |
| `loopkit:pr-review`         | One PR needs review+verify — worktree, tests, verdict                       |
| `loopkit:run-state-model`   | A design has two writers, a retry or a guard-then-write — model check it   |

## Deterministic gates (plugin `hooks/hooks.json`)

| Event                        | Gate                                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------------- |
| SessionStart                 | one orientation line (or "run /loopkit:init")                                               |
| UserPromptSubmit             | `loop_doctrine.py` — injects the tick discipline when a prompt looks like a tick            |
| PreToolUse (\*)              | `require_contracts.py` — blocks work tools until FILES/TOOLS/COMMANDS are read              |
| PreToolUse (Bash)            | `block_dangerous.py` — destructive-command floor; merge/approve/stage-all refused           |
| PreToolUse (Bash)            | `require_commit_lock.py` — a bare `git commit` is refused; commit via `loop-commit.sh`       |
| PreToolUse (Write/Edit)      | `protect_governance.py` — `constitution.md` and `specs/` need `GOVERNANCE_EDIT_OK=1`        |
| PostToolUse (Write/Edit/Bash)| `notify_needs_human.py` — edits to `inbox/needs-human.md` ping a webhook (fail-open)        |
| Stop                         | `stop_gate.sh` (precheck, tests, lint, typecheck, build) + `acceptance-review` against `specs/` |

## Running the loop (human-pulled, not timer-pushed)

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/morning-triage.sh"   # batch: find + rank work → state/triage.md
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-next.sh"        # which ONE stage is due
```

Or, inside an interactive session:

```
/loop work the loopkit backlog
```

`/loop` needs no special prompt. It is a generic timer that knows nothing about
this repo, but three layers reach it anyway: hooks fire on tool matchers
regardless of what was typed, `CLAUDE.md` auto-loads every session, and skills
auto-trigger on their description. The fourth layer is a _constraint_ rather
than a capability — advance exactly one stage, then stop — and no skill
description can carry it, so `loop_doctrine.py` injects it whenever a prompt
looks like a tick. If you find yourself pasting a paragraph of procedure into
`/loop`, that is the bug.

If a timer is ever wanted, put `morning-triage.sh` in cron — never a bare
`claude -p`, or the audit line never lands in `state/cron-triage.log`.

## The driver lock — the guard rail that is now a mechanism

This row used to read *"never run two loop drivers concurrently against
`state/triage.md`; the state file is the lock."* Nothing enforced it, and on
2026-09-07 it failed: a reviewer staged one file by name, a concurrent driver
ran `git add … && git commit` in the same worktree, and the driver's commit
published the reviewer's file under its own message (`6989d63`) — the
reviewer's own commit then found nothing staged.

The state file was never the lock. The shared thing is the **git index**: one
file per worktree, shared by every process in it. So the lock is per worktree,
`<worktree>/.loopkit/driver.lock`, held with `flock`.

| What                    | Mechanism                                                                          |
| ----------------------- | ----------------------------------------------------------------------------------- |
| Committing              | `loop-commit.sh -m "…" -- <paths>` — stages AND commits inside one critical section |
| A bare `git commit`     | refused by `require_commit_lock.py` (PreToolUse, Bash)                             |
| Writing `state/triage.md` | `triage_state.py` upsert/update/ensure-schema take the same lock                  |
| Who holds it right now  | `driver_lock.py status`                                                            |
| Running anything else under it | `driver_lock.py run --label … -- <cmd>`                                     |

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/loop-commit.sh" -m "msg" -- state/x.md
```

The kernel releases the lock when the holder exits — including on a crash or a
`SIGKILL` — so there is no stale lock to reap and no liveness rule to get
wrong. Two drivers in two DIFFERENT worktrees never contend, because they never
shared an index. `commit-without-lock` in `.loopkit/block-disabled.txt` turns
the gate off for a project that genuinely has one writer.

## Project commands

<!-- The project's own launchers and gates, one row each. -->
