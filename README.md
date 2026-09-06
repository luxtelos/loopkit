# LoopKit

A finishing harness for Claude Code. It does not make Claude smarter; it makes
Claude **finish** — by taking the decisions a model gets wrong under pressure
out of the model's hands and putting them in files, scripts and hooks that
cannot drift.

Extracted from a production repo where an agent loop shipped a payments epic,
then stripped of everything that named that repo. What is left is the part
that transfers.

## What it actually does

| Problem in a long agent session                      | What LoopKit does about it                                                                                                                                  |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The chat window is the memory, and it compacts       | `state/triage.md` is the queue, `specs/` is the truth, `inbox/needs-human.md` is the door. Findings go to disk, not to the conversation.                   |
| The model picks what to work on, and drifts          | `loop-next.sh` — the next stage is a **lookup** over the state file. One stage per tick, every row at it, each transition recorded.                        |
| A PR in flight starves the backlog                   | `loop-watch.sh` — a cheap poll (a few hundred bytes) before any full read; QUIET means do the real work.                                                   |
| Rules in CLAUDE.md are forgotten under pressure      | Hooks that **block**: no `rm -rf`, no history rewrite, no `git add -A`, no `gh pr merge`, no self-approve, no edits to `constitution.md`/`specs/`.        |
| "Done" is claimed, not proven                        | A Stop hook that runs precheck → regression diff → lint → typecheck → build, then an acceptance review against the spec. Red blocks the stop.               |
| The suite has known failures so the gate never passes| `test-regressions.sh` diffs one run against a committed baseline. Zero NEW failures is the bar; the baseline is a debt with a name.                          |
| The agent that wrote it approves it                  | Separate implementer and reviewer agents; the reviewer's default stance is *broken until proven otherwise*; merge and approve are refused for everyone.    |
| Things that need a human get guessed                 | `inbox/needs-human.md` with a required shape: whole context, both options costed. Edits ping a webhook. Rows can sit `blocked`, counted but never polled. |
| Findings become code whether or not code was the fix | `loop-assess` classifies first — measurement, code, tool, spec, process, architecture, prompt, decision — and only `code` reaches the spec-writer.        |
| A design has two writers or a guard-then-write       | `run-state-model` — FizzBee, Quint/Apalache and TLA+ behind one driver with one honest exit-code contract (PASS / VIOLATION / ERROR / UNPROVEN).           |
| `file:line` citations rot                            | `check-citations.py` — structural check on every citation, regex pins on the load-bearing ones.                                                            |

## Install

```
/plugin marketplace add luxtelos/loopkit
/plugin install loopkit@loopkit
```

First time? **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)** walks
through install (GitHub or a local folder), init, configuring the gate,
seeding the test baseline, finding work and running the first tick — with
what each step should print.

Then, inside the project you want the loop to run in:

```
/loopkit:init
```

That lays down `state/`, `inbox/`, `specs/`, the three contracts (`FILES.md`,
`TOOLS.md`, `COMMANDS.md`), `constitution.md`, `docs/MUTATION_POLICY.md`,
`.loopkit/` config, and a marker-guarded "LoopKit standing rules" block in
`CLAUDE.md`. It never overwrites; run it twice and it says `KEPT` for
everything.

Until `init` runs, the hooks are passive: the contracts gate only requires
contracts that exist, and the session-start line tells you to run `init`.

Requirements: `python3`, `bash`, `git`, `gh` (for scan/watch), `node` (only
for the model-checking driver).

## Quick start

```
/loopkit:init                       # once per project
/loopkit:morning-triage             # find work → state/triage.md at status new
/loop work the loopkit backlog      # tick; the doctrine hook injects the discipline
/loopkit:scan                       # status: READY TO MERGE is the only actionable line
/loopkit:doctor                     # is the install healthy
```

Configure the gate in `.loopkit/config.env` (copied from the example by `init`):

```
LOOP_TEST_CMD=npm test
LOOP_LINT_CMD=npm run lint
LOOP_TYPECHECK_CMD=npx tsc --noEmit
LOOP_BUILD_CMD=npm run build
LOOP_TEST_JSON_CMD=npx vitest run --reporter=json --outputFile={report}
# LOOP_ENV_WRAPPER=doppler run --project p --config c --     # any secrets manager, value-blind
```

Set a `*_CMD` to empty to skip that step. Non-JS projects: any command that
writes a jest-shaped JSON report works for `LOOP_TEST_JSON_CMD`, or feed any
runner through `test-regressions.sh --from-list FILE` (one `<file> :: <test>`
per line). Seed the baseline once with `--update-baseline`.

## The pieces

```
plugins/loopkit/
  .claude-plugin/plugin.json
  hooks/hooks.json                 the wiring (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop)
  hooks/block_dangerous.py         destructive floor + merge/approve/stage-all refusal; project extras and disables
  hooks/protect_governance.py      constitution.md and specs/ by ABSOLUTE path (worktree-proof); GOVERNANCE_EDIT_OK=1 is the door
  hooks/require_contracts.py       read FILES/TOOLS/COMMANDS before any work tool; passive until init
  hooks/loop_doctrine.py           injects the tick discipline when a prompt looks like a tick; never blocks
  hooks/notify_needs_human.py      webhook on inbox edits (Slack/Discord/generic); fail-open
  hooks/stop_gate.sh               precheck → regression diff → lint (+fallback) → typecheck → build
  scripts/loop-next.sh             which ONE stage is due (+ loop_next_pick.py: lanes, priority, blocked rows, fan-out)
  scripts/loop-scan.py             two API calls: PRs with real blockers, loop-owned issues, local backlog
  scripts/loop-watch.sh            cheap per-PR poll with a per-PR baseline
  scripts/triage_state.py          the queue, escaped pipes and all; never hand-edit the table
  scripts/inbox_to_triage.py       prose findings → rows, create-only, dry run by default
  scripts/test-regressions.sh      one run diffed against state/known-test-failures.txt
  scripts/check-citations.py       file:line citations that still point where they claim
  scripts/morning-triage.sh        headless discovery run, audit-logged
  scripts/loopkit-init.sh          lay the files into a project, idempotently
  skills/                          loop-tick, loop-scan, loop-assess (+references), morning-triage,
                                   spec-writer, acceptance-review, pr-review, run-state-model (+driver, installer, fixtures)
  agents/                          planner, implementer, reviewer
  commands/                        init, tick, scan, watch, doctor
  templates/                       what init copies: constitution, contracts, mutation policy, queue, baseline, inbox, CLAUDE.md block, .loopkit/
tests/selftest.sh                  every unit test + an end-to-end pass in a scratch repo
```

Every hook is fail-open except the four meant to block. Every script exits 0
and prints a `VERDICT:`/`STAGE:` line you read, except the gates, whose exit
code is the point. A crash in a hook is an allow, so nothing here raises.

## Adding your own skills, tools and agents

Yes — the plugin is designed so a project brings its own. The loop's interface
is **files with a fixed shape**, not code you have to call:

- **The queue** — `state/triage.md`, columns `finding | source | priority | spec
  | status`, statuses `new spec-draft spec-ready fixing pr-open blocked inbox
  done`. Anything that writes rows through `triage_state.py upsert` (a skill, a
  cron, an MCP tool, a script that reads your alerting system) is in the loop.
  `morning-triage` is one discovery source; add others the same way.
- **The door** — `inbox/needs-human.md`, `## heading (date)` sections; the
  bridge script files open headings as rows and skips `RESOLVED` ones.
- **The truth** — `specs/*.md` with EARS lines; the reviewer and the Stop hook
  grade against whatever is there.
- **The gate** — any shell commands in `.loopkit/config.env`, any secrets
  manager as `LOOP_ENV_WRAPPER`, any runner via `--from-list`.
- **The guards** — extend, don't fork: `.loopkit/block-patterns.txt` (extra
  regexes), `block-disabled.txt` (switch a built-in off, by name),
  `protected.txt` (more guarded paths), `scopes.json` (your lanes),
  `citations.json` (your pins).

Your own `.claude/skills`, `.claude/agents` and `.claude/hooks` (or another
plugin's) run alongside; Claude Code merges hook lists, so a project hook and a
LoopKit hook on the same event both fire. The loop-tick table names
`loopkit:*` skills for each stage, but a project skill invoked from a stage
(say a Playwright walkthrough inside `acceptance-review`) needs no
registration — it is just a tool the reviewer picks up.

What is **not** pluggable in 0.1.0, stated so nobody discovers it the hard way:
the five stage names in `loop-next.sh`; the skill names the doctrine hook
prints; and the JSON report shape (`testResults[].assertionResults[]`) — use
`--from-list` for anything else.

## Testing the plugin itself

```bash
bash tests/selftest.sh
```

Hook tests, script tests, the model-checker driver's selftest (stub engines,
no install needed), then an end-to-end pass in a scratch git repo: init twice,
rows in, `loop-next` serves the right stage, a finding with a `|` survives the
round trip, the regression diff passes and fails when it should, the stop gate
short-circuits and blocks when it should, citations past EOF fail, and a grep
proves no project-specific token leaked into the plugin. Ends with
`claude plugin validate` when the CLI is on PATH.

## On ECC and other plugins

[everything-claude-code](https://github.com/affaan-m/everything-claude-code)
and similar collections give you *capabilities* — agents, skills, commands,
rules. LoopKit gives you the *loop* around them: memory on disk, stage as a
lookup, blocking hooks, a stop gate that runs the tests, separated powers, and
an escalation door. Install both; they do not overlap.

## Licence

MIT.
