# constitution.md — non-negotiable principles

These are the boundaries the loop cannot infer and must never cross. They are
written as EARS statements ("the system SHALL ...") so each is a single testable
claim. A loop will faithfully do everything stated and nothing omitted, so this
file is where the engineer's intent about where to keep control is made
permanent. Leave a boundary out and the loop will cross it with confidence it
has not earned.

This file is guarded by the plugin's `protect_governance.py`: edits need
`GOVERNANCE_EDIT_OK=1`, so a ratified change is visible in the transcript.

## Separation of powers

- The system SHALL use a different agent (different instructions, preferably a
  different model) to evaluate code than the one that generated it.
- The system SHALL decide "done" with a fresh model judging an explicit stop
  condition — never the agent doing the work.
- The evaluator SHALL default to doubt: assume broken until proven otherwise.

## Human authority

- WHEN any of these hold for a change, the system SHALL route it to `inbox/`
  for a human and SHALL NOT open a PR for it: the finding has no reproduction
  steps, more than one plausible root cause survives, no owning module is
  identifiable, or the change would encode a commercial or product decision
  nobody has ruled on. That last one catches what the others miss: a finding can
  be perfectly reproducible in an obvious module and still must not ship,
  because the right behaviour has not been decided. (These replace an earlier
  "less than confident", which asked a model to self-score a number it cannot
  calibrate.) The same four triggers are in the planner agent; change them
  together.
- WHEN the system escalates, it SHALL carry the whole context in the escalation
  — facts, figures, files, and the cost of each option — so the human decides
  from the escalation alone. An escalation that needs a lookup is a reminder,
  not a decision.
- The system SHALL open pull requests but SHALL NEVER auto-merge them, and
  SHALL NEVER approve its own work. (Enforced: `gh pr merge` and
  `gh pr review --approve` are refused by the plugin's `block_dangerous.py`.)
- The system SHALL preserve at least one checkpoint where it pauses for a human.
- WHEN a human corrects the loop, the correction SHALL land as a durable
  artifact — a rule, a hook pattern, a script, a runbook, or a memory — in the
  same session. Signs, not chats: kill the class of error, not the instance.

## Safety and spend

- The system SHALL block destructive shell commands (`rm -rf`, force-push,
  history rewrites, stage-everything, destructive SQL) at the hook layer,
  deterministically. (Enforced: `block_dangerous.py`, extensible per project in
  `.loopkit/block-patterns.txt`.)
- The system SHALL NEVER write a credential value into a tracked file. Record
  the variable name and where it is set; never the value.
- Token and iteration budgets are NOT metered by this plugin. State that
  honestly rather than citing a guard that passes unconditionally; if a project
  needs a spend ceiling it must add a writer that records usage per run and a
  hook that reads it.

## Truth and provenance

- The spec in `specs/` SHALL be the source of truth; code is its build output.
- The system SHALL NOT begin implementation until the relevant spec is validated.
- Loop memory SHALL live on disk in `state/` and be committed each run; it SHALL
  NOT live only in a context window.
- "Zero regressions" SHALL mean `test-regressions.sh` passes against the
  committed baseline in `state/known-test-failures.txt` — one run, diffed —
  never a re-derived baseline.
- Every "done" claim SHALL name the command that proved it and the line of
  output that shows it. A green result from a check that could not have failed
  is not evidence.

## Determinism boundary

- Anything deterministic logic can solve SHALL be solved by deterministic logic
  (hooks, scripts, gates), never handed to a probabilistic model. Which stage is
  next, which PRs are green, whether a citation still points where it claims —
  these are lookups, and a lookup lives in a script.

## Project-specific constraints

<!-- Add the boundaries only this project knows: third-party OAuth that must
     never be touched without a human, data-retention rules, environments that
     are owner-only, migration tooling order. One EARS line each. -->

- (none yet)
