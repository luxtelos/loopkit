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

## Runtime spec M1 — four contract decisions the spec cannot infer (2026-09-07)

Blocks: `specs/loopkit-runtime.md` criteria 20, 21, 30 and 18; milestone M2
(`loopkit-core`). The spec is drafted and its gates are green, but these four
change observable behaviour for the same input, so an implementer would have to
guess. Each is a cost trade, not a lookup — no recommendation is attached.

**Q1 — budget exhaustion: resumable or terminal? (criterion 20)**
When a Run's budget runs out mid-step, the runtime journals `budget_exhausted`
and stops. What happens next is undecided.
- *Resumable* (stop cleanly; raising the budget and re-running the same `run_id`
  continues where it stopped). Cost: a Run can sit half-finished indefinitely
  holding its lease; "the Run failed" and "the Run is paused" look identical to
  a caller who does not read the journal; a runaway Run is resurrected by anyone
  who raises the budget, so the budget stops being a hard stop.
- *Terminal* (mark failed; a new `run_id` is required). Cost: all work already
  journaled is discarded on a budget that was merely set too low — the common
  case — and the caller pays for every completed step again. Also awkward with
  Q2: a token budget is only knowable after the call that exceeds it.

**Q2 — budget unit: calls, tokens, or seconds? (criterion 21)**
- *Provider calls.* Cheap, exact, checkable before the call. Cost: a call is not
  a unit of cost — one 200k-token call and one 200-token call count the same, so
  the budget does not bound spend, which is what a budget is usually for.
- *Tokens from `usage`.* Bounds actual spend. Cost: only knowable AFTER the call
  returns, so "check before each call" becomes "check against the previous
  call's total" and a single large call can overshoot by any amount. Also
  requires every Provider to report `usage` honestly; the spec already has to
  say `0` rather than `null` for providers that report nothing, and a provider
  reporting 0 has an unlimited budget.
- *Wall-clock seconds.* Simple, provider-independent, bounds the thing an
  operator actually waits on. Cost: not reproducible — the same Run costs a
  different budget on a slow network, so fixtures cannot pin it and CI is flaky
  by construction.

**Q3 — the Store `list` contract: total list or iterator? (criterion 30)**
- *Total ordered list.* Simplest contract; the fixtures can compare one value.
  Cost: an S3 bucket paginates at 1000 keys, so the implementation must loop
  internally, and a journal of 10^6 events is materialised in memory before the
  caller sees the first key. A store that quietly truncates at the first page is
  the bug pre-mortem row 4 warns about, and it looks exactly like "no more work".
- *Explicit iterator / cursor.* Bounded memory, honest about pagination. Cost: a
  second concept in a contract whose whole justification (ADR-0003) is that it is
  four small methods; every SDK must implement lazy iteration identically, and
  the fixtures get harder to express because the expected value is a stream.

**Q4 — dead-letter destination for a malformed Message (criterion 18)**
- *The mailbox's own `dead-letter/` directory* (what `knowledge_actor.py` does
  today, with its own escalation path). Cost: two escalation surfaces exist and a
  human watching `inbox/needs-human.md` never sees it; a Run can dead-letter
  every message it writes and still report success.
- *`inbox/needs-human.md`* (one door for everything needing a person). Cost:
  changes existing actor behaviour that is already tested and already exits 6 to
  signal it; mixes machine-generated envelope failures into a file written for
  humans to read, and a noisy Run could bury a real ruling request.
## Two of the four M1 spec questions are already answered (2026-09-07)

A review of the merged runtime specification found that only two of its four
escalations need a human:

- **Still yours:** whether budget exhaustion is resumable or terminal, and what
  unit a budget counts (calls, tokens or seconds). Both are product choices with
  no evidence in the repo either way.
- **Answerable without you, and now closed:** whether `Store.list` returns a
  total or an iterator is already settled normatively by the specification's own
  criterion 30; the dead-letter destination is settled by its constraint that
  existing behaviour is not redefined, and the existing behaviour escalates to
  this file.

An escalation that could have been answered from the repo is noise in your
queue, so the two answerable ones were withdrawn rather than left standing.

## Merges are outrunning their reviews (2026-09-07)

Twice today a pull request merged before its reviewer's verdict posted: #8 and
#15. Both verdicts were FAIL and both found real defects — the first found the
governance hook was not guarding the shell at all, the second found an undefined
noun that the derivability pin quietly papered over. Nothing was lost, because
the findings became rows and then fixes, but the sequence is backwards: the
review is what the merge is supposed to wait for.

This needs your ruling because both options cost something.

- **Require a review before merge** (a branch protection rule, or a convention
  the runner honours). Cost: every fix waits on a reviewer agent, roughly seven
  to ten minutes each, and a stalled agent blocks the queue.
- **Keep merging fast and treat review as post-hoc.** Cost: defects reach the
  trunk and, twice today, a tagged release. `v0.2.0` shipped a specification
  with an undefined noun and a fabricated citation.

There is a middle option: require the review only for changes under `specs/`,
`hooks/` and `.github/`, and let everything else merge on the gate alone. That
matches where today's defects actually were.
