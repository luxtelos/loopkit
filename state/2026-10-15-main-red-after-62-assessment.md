# Assessment — main is red after #62 (2026-10-15)

Mode A (classify a finding). Scope: `origin/main` at `77203a7`, the merge of
#62 "fix(stop_gate): gate the session worktree, not CLAUDE_PROJECT_DIR". #62
merged with no reviewer verdict.

## Observed

Reported by another agent: on pristine `77203a7`, `tests/selftest.sh` reports
10 failures.

- 9 from the stop gate's new skip, `SKIP: gate root (…) != session cwd (…)`,
  in the suite's nested stop-gate cases.
- 1 from the suite's project-token guard:
  `FAIL project tokens in: tests/pins/stop-gate-worktree-root.sh`.

## Evidence

Reproduced, not believed: the full suite on a standalone clone of `77203a7`
(Linux, the LAN box) and on macOS. Exact lines are in the PR body; the count is
the suite's own `FAILED: N`.

**Which cases, and why.** The suite exports `CLAUDE_PROJECT_DIR="$T"` (a
scratch repo) and then runs `bash "$P/hooks/stop_gate.sh"` from wherever the
suite was launched — the loopkit checkout, never `$T`. Before #62 the gate did
`cd "$CLAUDE_PROJECT_DIR"` and the launch dir did not matter. Since #62 the
gate takes the SESSION's tree from the Stop payload's `cwd`, else its own
`pwd`, and when that tree is a different repo from `CLAUDE_PROJECT_DIR` it
prints `SKIP: gate root ($T) != session cwd (<loopkit checkout>)` and exits 0.

So every nested case that expects a FAIL (rc 1, the seventh-block warning, the
override tick, the PASS that resets the counter, the `{report}` placeholder,
the counter restarting at 1) sees a SKIP and fails. Two more cases, "stop gate
short-circuits with no code change" and "stop gate passes with green
commands", expect rc 0 and still PASSED on `77203a7` — vacuously, because a
SKIP also exits 0. They measured nothing after #62.

**Could the check have failed?** Yes, both ways:

- Run the same nested cases with the hook's cwd in `$T` (what Claude Code
  does: it runs a Stop hook in the session's cwd and also sends that cwd on the
  payload). The gate resolves `$T`, gates it, and the expected FAILs appear.
  So the SKIP is caused by the fixture's cwd, not by the gate's logic.
- `tests/pins/stop-gate-worktree-root.sh` case 2 asserts the SKIP on purpose:
  cwd in one repo, `CLAUDE_PROJECT_DIR` naming an unrelated repo → SKIP, exit
  0. The suite's nested calls are exactly that input. The gate gave the answer
  its own pin requires.

**The leak.** `tests/pins/stop-gate-worktree-root.sh:6` names the downstream
project #62 was written for. This repo is public; the owner rule of 2026-09-06
says the source project is never named here, and the suite's guard
(`tests/selftest.sh`, the `leaks=` grep) enforces it.

## Control case

#62's intent must survive: the gate gates the SESSION's worktree, not
`CLAUDE_PROJECT_DIR`. Concretely, all three cases of
`tests/pins/stop-gate-worktree-root.sh` stay green and stay able to fail:

1. linked worktree + `CLAUDE_PROJECT_DIR` = main checkout → the worktree's
   suite runs, not main's;
2. `CLAUDE_PROJECT_DIR` names an unrelated repo → SKIP, exit 0 (and
   `LOOP_FORCE_GATE=1` overrides it);
3. matching roots → gates as before.

A fix that removed or weakened the skip to make the suite green would break (2)
and re-open the 800-block false-FAIL storm #62 was written for.

## Classification

`measurement` — the suite's fixtures, not the gate.

The 9 are a fixture defect: the nested calls state a session (the suite's
launch dir) that is not the repo they mean to gate, and they were written under
the pre-#62 contract "CLAUDE_PROJECT_DIR is the root". After #62 the contract
is "the session's cwd is the root, and a mismatched project dir is refused",
which is the behaviour #62 set out to add and pins. Calling it a gate defect
would mean the gate should gate a project dir the session is not in — the exact
behaviour #62 removed.

Not `code` in the gate. Not `spec`: no spec in `specs/` covers the gate's root
resolution; #62's pin is the executable statement and it is not in question.
The leak is `process` (a downstream name in a public file), fixed in place.

## Route

Fix on `fix/main-red-after-stop-gate-worktree`, tests only:

- `tests/selftest.sh`: one helper, `gate_in_T`, runs the gate from `$T` (a
  subshell `cd`, as the hook runner does). Every nested stop-gate call in the
  suite goes through it. No `LOOP_FORCE_GATE` — that would also override the
  "no code changed" short-circuit the first case asserts.
- `tests/pins/stop-gate-worktree-root.sh`: the downstream name replaced with a
  generic description.

The env-scrub helper `tests/lib/scrub-gate-env.sh` named in the brief is not on
`main`; it lives on open PR #49. It scrubs inherited `LOOP_*` /
`CLAUDE_PROJECT_DIR` at suite entry, and the suite then sets
`CLAUDE_PROJECT_DIR="$T"` itself, so it would not change these 9. Pulling the
file in here would duplicate #49 and conflict with it. Not used.

No version bump: nothing under `plugins/loopkit/**` changes. #62 itself changed
`plugins/loopkit/hooks/stop_gate.sh` without a bump, so its gate change reaches
consumers only with the next bump (0.2.5, already claimed by open PRs). Noted
under `## Unreleased` in `CHANGELOG.md`.

### Second finding, exposed by the first fix

With the nested cases gating for real, the first Linux run of the fixed suite
failed "stop gate passes with green commands" (rc=1, want 0), and with it the
two downstream checks that read its PASS ("gate PASS not recorded", "no gate
events"). Debug output on the Linux host: the scratch repo has a baseline, so
the gate runs `LOOP_TEST_JSON_CMD`; unset, that is `npx vitest … --reporter=json`,
and on that host's node vitest dies at startup (`node:util` has no
`styleText`), so `FAIL: no JSON report produced`. On macOS npx happened to
start vitest, so the case passed there. The same output shows "fails on red
lint" was failing for the wrong reason on Linux: tests run before lint, and
the broken vitest failed first.

Same layer (`measurement`), same file. The fixture now passes a script that
writes an empty green report as `LOOP_TEST_JSON_CMD` for the green and the
red-lint cases, the same fixture the override section already uses. Verified
on the Linux host: all four cases pass, and the scratch report dir is removed.

## Not a code problem

The gate's skip is correct and stays. What was wrong is that the suite had not
been taught the new contract, and that #62 merged with no reviewer run of the
full suite — a run would have shown the 10. The two vacuous passes are the
sharper lesson: a SKIP that exits 0 turns an "expects pass" assertion into one
that cannot fail. Both now gate for real.

Separate known items, reported and not edited here: older downstream tokens
outside the guard's list sit in vendored files under
`plugins/loopkit/loopkit_memory/vendor/` and in `docs/research/runtime-plan.md`
(listed in the PR body).
