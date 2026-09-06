# Measurement — the gates that lie

Read this at step A1 whenever your evidence is "a check passed". In the repo
this was written for, a passing check repeatedly meant "nothing ran", and every
one of those cases cost a day or more.

The question that catches all of them: **what would this signal look like if the
thing I am testing were completely broken?** If the answer is "the same", it is
not evidence yet.

## Known liars, and how to get an honest signal

**A suite behind an env gate.** Four integration suites had never executed once,
because the env var that enables them was never set. The runner reported success
by skipping everything. Before trusting a suite, confirm it reported a non-zero
test count, not just a zero exit code.

**A quoted test glob.** `vitest "tests/unit/foo-*"` is a filename _filter_, not
a glob. Most of the suite silently does not run and the result is green. Pass a
directory instead, and check the count of files that ran.

**A pipeline through `tail` or `head`.** The pipeline's exit status is the last
command's, so a failing gate piped to `tail` exits 0. A real FAIL once rode into
a PR labelled PASS this way. Run gate scripts bare, or set `pipefail`, and read
the verdict line rather than the exit code.

**A guard that skips when it cannot resolve.** A guard that exits 0 with a
"skipping" message when no base ref resolves looks exactly like a pass on a
local run. Pass the base ref explicitly and read the verdict.

**A green tick on a conflicting PR.** A PR in conflict skips the `pull_request`
job entirely, so the tick means "did not run", not "passed". A secret-scanning
push job that scans a single commit is not the same as scanning the branch.

**A merge that changed nothing.** GitHub only retargets a child PR when its base
branch is deleted. A stacked merge can be green and be a no-op downstream. Prove
the change landed with `git merge-base --is-ancestor`, not with the PR page.

**A 200, a `true`, or a success link.** An accepted payload is not a persisted or
rendered effect. A payment provider returning success while the local
subscription row stays stale is this shape. So is a chat API call that returns
a message link while dropping the table inside the message. Read the result
back from the far side.

**An estimate instead of a count.** A planner estimate (`reltuples`) is an
estimate, and neighbouring environments have similar row counts, so a
wrong-schema query returns a confident, well-formed, wrong answer. Use an exact
`count(*)` and quote the schema name beside any number you report.

**A local run with the wrong dependencies.** A worktree can resolve
`node_modules` from its parent checkout and so test against a different
manifest. Thirty "failures" were once suites that could not load a package; the
fix was an install inside the worktree, not thirty bug fixes.

## Establishing a baseline you can defend

1. Name the command and paste its verdict line, not your summary of it.
2. Say which environment, proved from the running service's own boot config —
   never inferred from a branch name, a secrets-manager config name, or the last
   session's note.
3. Name the control case: the ordinary path that currently works and must keep
   working. Carry it into the EARS criteria so it becomes acceptance, not
   folklore.
4. If a scenario is borderline, run it several times and record a rate. A single
   run of a flaky case tells you almost nothing, and "it passed once" has started
   more bad fixes than any other sentence.

## When you cannot measure

Say so plainly and stop. "I could not establish what is true, and here is what
access or fixture I would need" is a legitimate, useful finding — it routes to
`inbox/needs-human.md` and it is honest. Proceeding to a spec on an unverified
baseline produces a fix nobody can evaluate, which is strictly worse than
producing nothing.

The live example when this was written: a money path sat "unverified" for days
because no single person held both the payment-provider dashboard and the
database. The correct output was never a code change — it was naming the access
gap.
