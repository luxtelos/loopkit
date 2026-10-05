# Assessment — loop-commit.sh cannot finish a merge (2026-10-04)

## Observed

A merge stopped on a conflict. The conflict was resolved and staged. Then:

    $ loop-commit.sh -m "merge side" -- CHANGELOG.md
    fatal: cannot do a partial commit during a merge.        (exit 128)

A bare `git commit` is refused by `hooks/require_commit_lock.py`. So no allowed
path completes the merge. The report came from a landing run with two branches
stuck on a one-file conflict.

## Evidence

Reproduced in a throwaway repo under `$TMPDIR`, never in a real worktree: two
branches that both change `CHANGELOG.md`, `git merge side`, resolve the file,
call the wrapper from `origin/main` (`f544466`). Output as above, exit 128,
`HEAD` unmoved, `.git/MERGE_HEAD` still present.

Could the check have failed? Yes. The same fixture with no merge in progress
commits the named path with exit 0 (the control below), so the 128 is caused by
the merge state and not by the fixture.

The cause is in the wrapper, not in the caller: it always runs
`git commit … -- <paths>`, and git refuses the pathspec form whenever
`MERGE_HEAD` exists, whatever the paths are.

Neighbouring states, measured in the same fixture on `f544466`:

| State in progress | What the wrapper does today |
|---|---|
| merge (`MERGE_HEAD`) | exit 128, `cannot do a partial commit during a merge` |
| cherry-pick (`CHERRY_PICK_HEAD`) | exit 128, `cannot do a partial commit during a cherry-pick` |
| revert (`REVERT_HEAD`) | exit 0, an ordinary commit of only the named path |
| rebase stopped on a conflict | exit 0, an ordinary commit of only the named path; the rest of the picked commit stays staged |

## Control case

No merge in progress, one path named, a second file staged by "someone else":
the commit holds exactly the named path and the other file stays staged. This
is the property the wrapper exists for and the fix must leave it as it is.

## Classification

`tool`. The wrapper is the only allowed way to commit and it has no branch for
a state git reaches in ordinary work. Not `process` ("avoid conflicts") — a
conflict is not a mistake. Not `spec` — no spec in `specs/` covers the wrapper,
and the rule it serves ("name the files; never commit what you did not name")
is already written down and is not in question.

## Route

Fix in the wrapper, on `fix/loop-commit-finishes-a-merge`, with pins first.

The fix has to commit the whole index, because the paths git merged by itself
are staged and nobody named them. That is the hole the pathspec form closes, so
the fix is only acceptable with a check that detects a foreign staged file:

- Ask git what the merge alone produces: `git merge-tree --write-tree HEAD
  MERGE_HEAD` (reads objects only; touches neither index nor working tree).
- Every path where the index differs from that tree was changed by hand after
  the merge stopped. Each must be covered by a path the caller named.
- Anything else differing is refused by name; nothing is committed.

Pre-mortem, one pin each (`tests/pins/loop-commit-finishes-a-merge.sh`):

| # | Risk | Pinned answer |
|---|---|---|
| 1 | a foreign staged file is present mid-merge | refused, exit 3, path named, merge still in progress |
| 2 | an unresolved path remains | refused before any commit is attempted |
| 3 | no paths named / the wrong paths named | exit 2 (usage) / refused, the unnamed resolution listed |
| 4 | no merge in progress | unchanged: only the named path is published |
| 5 | cherry-pick, revert, rebase | cherry-pick refused with a reason; revert and rebase unchanged |

Fails closed where the proof is not available: an octopus merge, unrelated
histories, or a git older than 2.38 are refused rather than committed unchecked.

## Not a code problem

- `require_commit_lock.py` refusing a bare `git commit` mid-merge is correct
  and is not changed here. Loosening the hook for merges would put the merge
  commit outside the lock and outside the foreign-file check.
- The rebase row above is a separate finding, not fixed here: at a rebase stop
  the wrapper makes a commit of only the named path, which splits the commit
  being replayed. It publishes nothing foreign, so it is not this bug. It needs
  its own decision (refuse, or leave) — a candidate triage row.
- Finishing a cherry-pick through the wrapper is also a separate finding. The
  same kind of proof exists (`merge-tree --merge-base`, git 2.40+) but nobody
  has asked for it; until then it is refused with a reason instead of git's
  one-line error.

## Review round 1 (FAIL at 6154190) and what changed

The first fix checked the shared index against `merge-tree` and then committed
the shared index. Review showed the gap: for a whole-index commit git drops
`index.lock` before the pre-commit hook and reads the index again after it, so
a lockless `git add` during the hook rode into the merge commit with exit 0. A
pathspec commit keeps the lock through the hook, which is why the ordinary path
never had this. `require_commit_lock.py` lets a bare `git add` through on
purpose, so the driver lock cannot be the defence.

Now the shared index is never committed. The commit is built in a private index
(merge-tree result + named files from the working tree) and made with a real
`GIT_INDEX_FILE=<private> git commit`, so hooks and signing still run (no
`commit-tree`). Afterwards only the named paths are refreshed in the shared
index; a foreign staged change stays staged. Pinned by `RC` in the pin: a
foreign add fired from inside a slow pre-commit hook — red on 6154190, green
now.

Also closed from the same review: conflict markers in a named conflicted file,
a directory or `.` named on a merge, `-X theirs` merges (complete once named),
a path with a space split in the refusal text.

Still out of scope, its own finding: `require_commit_lock.py` refuses
`git commit` but lets `git merge --continue` through, which is an unguarded
whole-index finish. Present before this change.

## Review round 2 (FAIL at a5a1be1) and what changed

The private index removed the sweep, but its EXIT trap deleted the index while
`git commit` could still read it. git re-reads the index after the pre-commit
hook, so a SIGTERM to the helper alone (`pkill -f loop-commit` matches it, not
`git commit`) produced a two-parent merge with git's empty tree, exit 143.

Property now pinned (SG cases in the pin): a signal to the helper alone, at any
point, leaves either the right merge commit or nothing committed with the merge
and the shared index as they were. Mechanism: `git commit` is a waited-on
child; INT/HUP/TERM are passed on as TERM; the private index is removed only
after the child exits; the outcome is read from HEAD, not from exit codes; a
landed commit's tree is checked for added/removed paths against the built tree
(content changes by hooks are allowed); the refresh finishes before the helper
stops. Red on a5a1be1 (empty tree, 0 files), green now.

Also: path lists read with -z, so non-ASCII conflicted names finish.

Residual, noted not fixed: if loop-commit.sh and driver_lock.py are killed too,
the lock is released while the helper is still waiting for its dying
`git commit`; a second caller could start in that short window. It cannot
commit (git commit is being terminated, and MERGE_HEAD stays until a commit
lands), but it would see the merge still in progress.
