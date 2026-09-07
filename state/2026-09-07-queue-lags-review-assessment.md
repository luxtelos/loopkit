# Assessment — the queue lags its own work by the review latency (2026-09-07)

## Observed

`loop-next.sh` served `process 2026-09-07 §merge-outran-review` as a `new` row,
one tick after that row was set to `blocked`. Both are true: the row is `blocked`
on branch `chore/tick-0.2.2-released` and `new` on `main`, and the lookup reads
the checkout.

## Evidence

`git show main:state/triage.md` and the branch's copy disagree on that row's
status. Could the check have failed? Yes — if the two files agreed, the lookup
would have served the next row instead and nothing would have surfaced.

## Control case

A single-branch project, where the queue file is written and read on the same
ref, must keep working exactly as now. Whatever fixes this must not require a
project to change how it commits.

## Classification

`architecture`, not `process`. The reflex to resist is "remember to merge the
state PR sooner": that is a habit, and the failure recurs whenever review is
slower than the loop. The queue is memory, and this memory is only durable at
merge time — so a loop that outruns its reviewer necessarily reads a stale copy
of its own mind. Rate of recurrence is exactly the review latency.

Two consequences already visible today: rows re-served after advancing, and
eleven `blocked` rows that exist on `main` only because earlier state PRs did
merge. Had they not, the loop would be polling them.

## Route

`inbox/needs-human.md` — it needs a ruling, because every fix costs something:

- **Write state straight to the trunk**, exempting `state/` from review. Cheap
  and immediate; gives up the audit trail on the loop's own memory, and the
  trunk gains commits nobody reviewed.
- **Keep one long-lived state branch** that the loop reads and writes, merged
  periodically. Preserves review; the lookup must then read that ref rather than
  the checkout, which is a change to how every project runs the loop.
- **Accept the lag and make it visible.** The lookup warns when the checkout's
  queue differs from the newest pushed state branch. Cheapest, changes no
  workflow, and fixes nothing — it only stops the loop from being surprised.

## Not a code problem

The re-serving itself is correct behaviour given a stale file. Nothing in
`loop_next_pick.py` is wrong, and changing it would encode a workaround for a
storage decision that has not been made.
