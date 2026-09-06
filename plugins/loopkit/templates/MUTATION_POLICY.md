# MUTATION_POLICY.md — what the loop may change on its own

> An owner touch should be a decision, never a lookup, a reminder, or a
> verification the loop can run. This file says which mutations the loop makes
> without asking, which it proposes, and which stay human forever — so the
> question is answered once, here, and not re-asked in every session.

## Tiers

| Tier   | Class                                                                                                   | Who acts                                  |
| ------ | ------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| **T0** | Reads. Logs, dashboards, sandbox data, test output                                                      | Loop, freely                              |
| **T1** | Reversible non-prod writes: test data, worktrees, branches, PR comments, `state/`, `inbox/`             | Loop, freely; recorded in `state/`        |
| **T2** | Named, pre-ratified classes (listed below): each with its runbook, its rollback, and its test pin       | Loop, without asking; runbook cited       |
| **T3** | Production anything, money, credentials, `specs/`, `constitution.md`, merges, approvals                 | Human only, always                        |

The rule of the tiers: a plan, the exact command, and the rollback are written
down BEFORE any shared-data write, in `state/`, and the write cites them.

## T2 classes ratified for this project

<!-- One entry per class. A class without all four parts is not T2 — it is T3
     until the parts exist. Example:

1. **Rotate the read-only BI token into non-prod configs.** Runbook:
   `docs/runbooks/rotate-bi-token.md`. Rollback: previous value kept in the
   secrets manager's history. Pin: `tests/...`. Ratified YYYY-MM-DD by <owner>. -->

- (none yet)

## Enforcement — what is a gate and what is a rule

- `block_dangerous.py` refuses history rewrites, stage-everything, merge and
  approve for every agent. That is a gate.
- Project extras in `.loopkit/block-patterns.txt` (e.g. a secrets-manager write
  aimed at the prod config, a management-API call carrying a prod project id)
  are gates once written there.
- Everything else in this file is a rule the loop is told, not a wall that
  stops it. Read a violation as possible rather than impossible, and when one
  happens, turn it into a pattern.
