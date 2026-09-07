# Assessment — the suite fails on a stock macOS temp directory (2026-09-07)

## Observed

On the merge base `2d86daa`, with `TMPDIR` set to the macOS default from
`getconf DARWIN_USER_TEMP_DIR` (a path under `/var/folders/...`),
`tests/selftest.sh` reports three failures: `graph status`, `graph find` and
`graph callers`. With a non-symlinked `TMPDIR` the same commit is `ALL PASS`.

## Evidence

Both arms run on the merge base, in a detached worktree, changing nothing but
`TMPDIR`:

- symlinked → `3 FAILURE(S)`
- symlinked with a trailing slash → `3 FAILURE(S)`
- non-symlinked control → `ALL PASS`

**The control arm is not deterministic, and this document should have said so.**
`tests/selftest.sh:738` bounds a wall-clock measurement at `< 200 ms`, and what
that measurement is mostly made of is a Python interpreter starting up on a
shared CPU. Re-running the control on this tree, changing nothing at all, I
measured 31 ms to 154 ms across 70 invocations. The reviewer of #28 saw a fourth
failure, `FAIL timing: 280 hit`, on a run that was otherwise `ALL PASS`, and a
re-run passed. So `ALL PASS` above is a claim about the three graph checks and
is reproducible as such; it is not a promise that every run of the suite is
green, because a load-sensitive pin can add a red line to any arm. That pin is
its own finding and is already queued on PR #29 as
`flake 2026-09-07 §offload-timing-budget` — referenced here rather than
duplicated as a new row.

`/var` is a symlink to `private/var` on this machine, so `Path(tmp).resolve()`
differs from the raw string. The failing messages quote a `/private/var/...`
path back, which is the tell.

My FIRST attempt at this proved nothing and I nearly recorded it as a
refutation: my own `TMPDIR` is already non-symlinked, so both arms ran the same
case and both passed. The control case is what caught that — if the two arms
cannot differ, the comparison is not a comparison.

## Control case

A machine whose `TMPDIR` is already resolved must stay `ALL PASS`, and the graph
adapter must still report DEGRADED honestly when a project genuinely is not
indexed. A fix that makes the check pass by weakening it would be worse than the
bug.

## Classification

`code`. `loopkit_memory/graph.py` `available()` compares
`str(self.root.resolve())` against the raw `root_path` the indexer reported.
One side is resolved and the other is not, so the comparison is between a path
and a different spelling of the same path.

The reflex to resist is "set `TMPDIR` in the suite". That hides it: the same
mismatch would still fire for any project living under a symlinked path, which
on macOS includes anything under `/tmp`. Compare resolved to resolved on both
sides instead.

## Route

`spec-writer`, then implementer. It deserves acceptance criteria because the
honest-DEGRADED behaviour must be pinned alongside the fix — the failure mode of
a careless fix is a graph adapter that claims to be available when it is not.

## Why this matters more than three red lines

It is the first thing a new adopter sees. Clone the repository on a Mac, run the
suite as the getting-started guide instructs, and three checks fail for reasons
having nothing to do with the repository. A suite that fails on a clean machine
teaches people to skim its output, which is how a real failure gets ignored.
