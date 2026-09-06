---
name: pr-review
description: One-shot review and verification of a single GitHub PR in an isolated worktree — tests, lint, acting on the changed behaviour, PASS/REJECT with evidence. Never merges, never approves.
---

# Skill: PR Review

## Job

One-shot review + verify of a single GitHub PR. Input: a PR number.
Output: PASS/REJECT verdict with evidence, findings filed per FILES.md.

## Stance

Assume the code is BROKEN until proven otherwise. "Reads fine" is not
evidence; only execution is. Never merge, never approve — a human does both,
and the plugin's `block_dangerous.py` refuses `gh pr merge` and
`gh pr review --approve` in any case.

## Algorithm

1. `gh pr view <n> --json number,headRefName,files` and `gh pr diff <n>` — the
   diff and the linked spec in `specs/` first. Form your provisional verdict
   per criterion BEFORE reading the PR body or any summary the author wrote;
   then read them. The spec is the acceptance contract; the description is a
   claim. Flag only gaps that affect correctness.
2. Check out into an isolated worktree, never the main tree:
   `git worktree add .worktrees/pr-<n> && cd .worktrees/pr-<n> && gh pr checkout <n>`
3. Run tests AND lint. Record actual output, not a summary of it. Prefer the
   regression diff so a known-bad baseline cannot mask a new failure:
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/test-regressions.sh"`.
4. If the diff touches UI or user flows, drive the running app and observe the
   changed behavior directly.
5. Review the diff with the reviewer agent's instructions: correctness,
   idempotency of every transition, authorisation checked in BOTH middleware
   and handler when auth-adjacent, pagination and tenant-scoping invariants,
   data-retention rules the project states.
6. File what does not belong to this PR: out-of-scope defects →
   `state/<date>-pr<n>-findings.md`; anything needing a human decision →
   `inbox/needs-human.md` (this pings the configured webhook).
7. Verdict in the final message: PASS (every criterion held, with evidence)
   or REJECT (reasons, file:line). If asked to comment, use
   `gh pr comment` — never approve or merge.
8. Clean up: `git worktree remove .worktrees/pr-<n>` unless the human wants
   the checkout kept.

## Environment

- `gh` authenticated.

## Do NOT

- Merge, approve, or push to the PR branch uninvited.
- Review in the main working tree.
- Claim PASS without running tests + lint in the PR's worktree.

## Gotchas

- `gh pr merge` and `gh pr review --approve` are refused by the plugin's
  `block_dangerous.py` for every agent; do not look for another spelling.
- A green CI rollup means nothing REPORTED failure. Steps inside a job fail
  fast, so later steps may never have run. Read the job.
- Reading the PR body first anchors you on the author's framing. Diff and
  criteria first, body second — the order is the review.
- The worktree resolves `node_modules` from the parent checkout; a dependency
  added on the PR branch is missing until you install inside the worktree.

