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
  hooks/stop_gate.sh               precheck → regression diff → lint (+fallback) → typecheck → build; writes state/progress.md
  hooks/protect_tests.py           a test file's test count may not drop; rm/mv of test paths refused; features.json only flips passes
  hooks/require_recall.py          a storage-invariant write is blocked until memory was consulted this session (rules: .loopkit/recall-triggers.txt)
  hooks/precompact.sh              five-section snapshot to .loopkit/session/ before every compaction; SessionStart resume|compact reads it back
  hooks/count_approvals.py         counts permission prompts, never decides one; the doctrine names approval fatigue past 30
  hooks/offload_nudge.py           a non-blocking line when a Bash result exceeds 8 KB, pointing at run-capped.sh
  hooks/offload_rewrite.py         PreToolUse updatedInput: a Bash command matching .loopkit/offload-patterns.txt runs through run-capped.sh; passive without the file
  loopkit_memory/                  the memory registry (.loopkit/memory.json) and adapters: graph (codebase-memory CLI → grep),
                                   memory (mempalace CLI → notes on disk), knowledge (OKF, 0.2.0-c)
  scripts/loop-next.sh             which ONE stage is due (+ loop_next_pick.py: lanes, priority, blocked rows, fan-out)
  scripts/loop-scan.py             two API calls: PRs with real blockers, loop-owned issues, local backlog
  scripts/loop-watch.sh            cheap per-PR poll with a per-PR baseline
  scripts/triage_state.py          the queue, escaped pipes and all; never hand-edit the table
  scripts/inbox_to_triage.py       prose findings → rows, create-only, dry run by default
  scripts/test-regressions.sh      one run diffed against state/known-test-failures.txt
  scripts/check-citations.py       file:line citations that still point where they claim
  scripts/morning-triage.sh        headless discovery run, audit-logged
  scripts/loopkit-init.sh          lay the files into a project, idempotently; --profile commerce
  scripts/fanout.sh                one claude -p per brief, own worktree, scoped tools/turns, JSON results, nothing merged
  scripts/judge.py                 pairwise judge: claude -p twice per criterion with positions swapped, disagreement → TIE 0.5, judge ≠ generator
  scripts/check-snapshot.py        snapshot evals: shape, end-state-only, no live keys
  scripts/rulings-extract.py       rulings hiding in the inbox, ADRs, specs → concept messages (dry run by default)
  scripts/rulings-compile.py       every Invariant/Gate names an artefact that exists; --strict
  scripts/loop-metrics.py          verified success, gate pass rate, re-asks, blocked rows — each with n=
  scripts/ticks.py                 the append-only event ledger those metrics read
  scripts/memory.py                status | recall | remember | invalidate | wakeup | graph …; --format concise by default
  scripts/progress.py              Files Modified from git; the append-only progress log; the compaction snapshot
  scripts/run-capped.sh            run a command (argv, or one shell string under pipefail), full output to .loopkit/scratch/, head+tail in context
  scripts/check-claude-md.py       the CLAUDE.md budget: standing instructions, one emphasised line, duplicates, derivable lines
  scripts/check-skills.py          frontmatter, Gotchas, run-vs-read verbs on every SKILL.md
  scripts/check-tools.py           no row no server, absolute paths, secret-looking literals, server count
  scripts/check-duplicate-hooks.py project hooks that duplicate plugin hooks (both fire)
  skills/                          loop-tick, loop-scan, loop-assess (+references), morning-triage, memory (+protocol),
                                   spec-writer, acceptance-review (+judge), pr-review, run-state-model (+driver, installer, fixtures)
  agents/                          planner, implementer, reviewer
  commands/                        init, tick, scan, watch, doctor
  templates/                       what init copies: constitution, contracts, mutation policy, queue, baseline, inbox, CLAUDE.md block, .loopkit/
tests/selftest.sh                  every unit test + an end-to-end pass in a scratch repo
```

Every hook is fail-open except the six meant to block (contracts, dangerous commands, governance, tests, recall, the stop gate). Every script exits 0
and prints a `VERDICT:`/`STAGE:` line you read, except the gates, whose exit
code is the point. A crash in a hook is an allow, so nothing here raises.

## Memory without RAG

`.loopkit/memory.json` names three adapters and every gate asks the registry,
never a server:

| Kind | Primary | Fallback | What it answers |
| --- | --- | --- | --- |
| graph | `codebase-memory-mcp cli` (structural index) | `grep`, same output shape | callers, callees, snippets, impact of a diff |
| memory | `mempalace` CLI (recall, wake-up); note-then-`mine` for writes | notes under `state/memory/` | "has this bitten us before"; `remember`; `invalidate` (never delete) |
| knowledge | OKF bundle — typed, drift-checked concepts, a deterministic mailbox actor | — | rulings, traps, invariants, gates; `enforced_by` proves a ruling has a gate |

`memory.py` prints `ADAPTER: <kind>=<name> [available|DEGRADED: <reason>]` on
every call; DEGRADED says how to fix it. `--format concise` (default) caps
output at 30 lines and files the rest under `.loopkit/scratch/`.

The gate that makes it matter: `require_recall.py` blocks a migration, a
`UNIQUE`/`PRIMARY KEY`/`CHECK` in code, or a shell write into a migrations
directory until memory was consulted this session — because a fact recorded
in June cannot defend itself in July if nothing reads it.

## Anthropic's practices, as checks

Everything below is a published Anthropic practice turned into a hook, a
script or a template — a check that can fail, not a sentence in a prompt.

| Practice (source) | What LoopKit does |
| --- | --- |
| "It is unacceptable to remove or edit tests" — [effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | `protect_tests.py`: a test file's test count may not drop; `rm`/`mv` of a test path is refused; `state/features.json` may only have `passes` flipped. `TEST_EDIT_OK=1` is the visible door. |
| Session bootstrap: git log + progress file; compaction must keep the file list — [best practices](https://code.claude.com/docs/en/best-practices), same harness post | `progress.py` (Files Modified from git, append-only `state/progress.md` written by the stop gate), `precompact.sh` (five-section snapshot before every compaction), SessionStart on `resume\|compact` prints them back. |
| CLAUDE.md: short, one emphasised line, nothing derivable from code — [best practices](https://code.claude.com/docs/en/best-practices); guidance over rules — [new rules of context engineering](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models) | `check-claude-md.py`: standing-instruction count against the ~150–200 ceiling, emphasis count, duplicates across the contracts, `npm run` lines that restate `package.json`. |
| Skills: frontmatter for selection, Gotchas as the highest-signal section, explicit run-vs-read — [agent skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills), [how we use skills](https://claude.com/blog/lessons-from-building-claude-code-how-we-use-skills) | `check-skills.py`; every LoopKit skill carries `## Gotchas`. |
| Tools: a high bar per tool, no overlap, absolute paths, nothing inline — [writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [seeing like an agent](https://claude.com/blog/seeing-like-an-agent) | `check-tools.py`: no row no server, relative commands, secret-looking literals (reported by key, never value), server count. `check-duplicate-hooks.py` for hooks a project wires twice. |
| Reviewer sees the diff and the criteria, flags only correctness gaps — [best practices](https://code.claude.com/docs/en/best-practices); "oversight degrades the overseer", judge before the recommendation — Mitchell, Ghosh & Passi 2026, [arXiv 2608.23642](https://arxiv.org/abs/2608.23642) | Reviewer, pr-review and the Stop agent form the per-criterion verdict from the diff BEFORE reading the implementer's summary. |
| Approval fatigue makes the human symbolic — same paper | `count_approvals.py` counts permission prompts (never decides one); past 30, the tick doctrine says so and routes decisions to the inbox. |
| Judges: pairwise twice with swapped positions, justification before score, judge ≠ generator — [multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | `skills/acceptance-review/references/judge.md`. |
| Every subagent brief: objective, output format, tool guidance, boundaries — same post | `templates/brief.md`. |

`/loopkit:doctor` runs the four checks. None of them fails the doctor; they
print what they found, and `--strict` fails a CI job.

## Profiles

A profile is templates and checks for a kind of project — never code that
runs in it. `loopkit-init.sh --profile commerce` appends, marker-guarded,
the constraints Anthropic published for commerce agents
([anatomy of effective commerce agents](https://claude.com/blog/the-anatomy-of-effective-commerce-agents)):
named block patterns that refuse live keys, live-mode flags and money-moving
verbs from an agent's shell (each block names its ruling), protected pricing
and policy paths, recall triggers on checkout and refund code, EARS lines in
`constitution.md`, a snapshot-eval template graded on end state with
`check-snapshot.py`, and the `commerce-review` skill. It claims nothing about
payment protocols Anthropic has not published on; those sit in
[docs/research/watchlist.md](docs/research/watchlist.md).

## Fan-out

`scripts/fanout.sh --briefs DIR` runs one headless `claude -p` per brief
(`templates/brief.md`: objective, output schema, tool guidance, boundaries),
each in its own worktree with tools and turns scoped, and leaves one JSON
result per brief under `state/fanout/`. It merges nothing. Use it when the
rows at a stage are independent and the cost — about fifteen times a single
session — is worth it.

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
