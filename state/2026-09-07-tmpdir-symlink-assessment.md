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

**The control arm was not deterministic when these arms ran, and this document
should have said so.** `tests/selftest.sh:738 as of 72b8c71` — the tree these
arms were gated on — bounded a wall-clock measurement at `< 200 ms`, and what
that measurement was mostly made of is a Python interpreter starting up on a
shared CPU. Re-running the control on that tree, changing nothing at all, I
measured 31 ms to 154 ms across 70 invocations. The reviewer of #28 saw a fourth
failure, `FAIL timing: 280 hit`, on a run that was otherwise `ALL PASS`, and a
re-run passed. So `ALL PASS` above is a claim about the three graph checks and
is reproducible as such; on that tree it was not a promise that every run of the
suite is green.

**That bound no longer exists, and this paragraph went on naming it after it had
been replaced.** `main` moved to `65d005a` and was merged forward into this
branch. On `65d005a`, `tests/selftest.sh:738` is `out="$(rw 'seq 1 5')"`, and
`grep -nE 'lt 200|< 200'` over the file returns nothing. What pins the cost now
is `tests/selftest.sh:759-768 as of 65d005a`, and the rationale comment at
`:742-758` is the refutation of the paragraph above it: a hard 200 ms budget
failed for LOAD at 215 ms, 205 ms and 307 ms with nothing regressed, so the pin
was rewritten to import `decide()` and measure nanoseconds per pattern
**in-process** at 20 patterns and at 2000 — the interpreter-startup constant is
not in the measurement at all. So the flake this document warned about cannot
fire on this tree, and the mechanism it blamed is exactly the one the
replacement was built to remove.

Nothing above about the symlink finding changes; only the caveat was stale. It
went stale without anyone editing it, which is the point: a bare `path:line` in
a dated record silently means "as of today", and `main` moved several times
today. That is now its own owner ruling — `inbox/needs-human.md`, section
"Owner ruling needed: how a dated record should cite live code", counted by
`inbox 2026-09-07 §dated-record-citation-anchor`.

**Knock-on, flagged not fixed.** The row this deferred to rather than duplicate,
`flake 2026-09-07 §offload-timing-budget` on PR #29 at `ee81f03`, still describes
the 200 ms budget as live. That pin is gone from `main`, so the row should be
re-issued or closed on #29. It is not this branch's to change, and is recorded
here so it is not lost.

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

`code`. `plugins/loopkit/loopkit_memory/graph.py:57,63 as of 65d005a` —
`available()` sets `root = str(self.root.resolve())` at `:57` and compares it at
`:63` against the raw `root_path` the indexer reported. One side is resolved and
the other is not, so the comparison is between a path and a different spelling
of the same path. (Re-checked on the current head, not assumed: unlike the
timing citation above, this one had not rotted.)

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
