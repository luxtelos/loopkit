# needs-human.md — the open door

Work the loop is not confident enough to ship waits here for a human. One file,
newest section at the top, never a file per finding. Every edit to this file
pings the webhook in `LOOPKIT_NEEDS_HUMAN_WEBHOOK` (if set).

A section is answerable only if it carries all of:

1. A dated heading: `## <what is undecided> (YYYY-MM-DD)`.
2. What it blocks (spec slug, ticket, triage row source).
3. The question, stated so it can be answered yes/no or by picking an option —
   with the WHOLE context in the section: the facts, the figures, the files.
   The reader must not need a lookup.
4. The cost of EACH option, not one recommendation.

When the human rules, do not delete the section: prefix the heading with
`RESOLVED <date> —` (uppercase, so `inbox_to_triage.py` stops filing it) and
record the ruling under it. Then land the ruling as a durable artifact — a rule,
a hook pattern, a runbook, a test pin — in the same session.

---

## Post-compact probe harness — no way to compact on demand (2026-09-06)

The plan's 0.3 item "post-compact probe harness" cannot be built honestly today:
`claude -p` has no compaction trigger and `/compact` is interactive-only, so a
probe would be prompts nothing can run. The deterministic `SessionStart(compact)`
output is pinned by the selftest and is the whole measurement for now.
Re-check on 2026-10-01 whether `claude plugin eval` can drive a session through
compaction. Cost of waiting: none measurable. Cost of building now: a harness
that reports success by running nothing. Row set to `inbox`.

## Runtime plan milestones M2–M6 are sequenced, not blocked on a ruling (2026-09-07)

Rows M2–M6 in state/triage.md sit at `blocked` only because each depends on the
previous milestone's PR merging (M1 spec first — the owner's paper-trail rule).
No decision is pending: the plan was approved 2026-09-07 with one ruling folded
in (SQLite, no Postgres). Flip each row to `new` when its predecessor merges.
