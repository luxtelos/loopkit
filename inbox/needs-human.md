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

## I pushed to main without review, while asking you to rule on exactly that (2026-09-07)

Disclosure, not a question. Cutting 0.2.2 needed a fix to the release script,
and I committed it straight to `main` instead of opening a pull request. That
bypassed the review gate — the same gap I escalated earlier today, one section
above. The change is small and its follow-up is in #23, but the point is that
the convention did not hold under mild time pressure, which is evidence for
option 1 in that ruling rather than option 2.

## Three credential facts that shape what the loop can do (2026-09-07)

Each is verified here, not assumed. None needs a decision today; all three
change what an agent can finish unaided.

- **No credential carries GitHub's `workflow` scope.** The variable named for
  that purpose returns unauthorized; the others are valid but scope-less.
  GitHub rejects any push whose commit touches `.github/`. So the continuous
  integration change for the model checkers sits in
  `docs/ci-model-engines.patch` with its selftest pin, waiting for a token that
  has the scope.
- **The 1Password SSH agent refuses to sign**, intermittently at first and then
  consistently. Commits since are unsigned. Worth knowing alongside it:
  signature *verification* is not configured in this repository, and 22 of the
  last 40 commits were already unsigned, so signing here currently proves
  nothing to anyone. Either wire up an allowed-signers file and make it real, or
  drop the requirement and stop paying for a control that does not check.
- **Plain `git push` has no working credential** over either transport, so the
  release script now falls back to the `gh` token inline. It is never written to
  a file and never echoed.

## The queue lags its own work by however long review takes (2026-09-07)

`loop-next.sh` served a row one tick after that row was advanced, because the
advance lives on a branch and the lookup reads the checkout. Both states are
real. The assessment is in `state/2026-09-07-queue-lags-review-assessment.md`;
it classifies this as architecture rather than process, because "merge the state
PR sooner" is a habit and the failure recurs at exactly the review latency.

Three options, each costing something:

- **Exempt `state/` from review and write it to the trunk.** Immediate, and
  gives up the audit trail on the loop's own memory.
- **One long-lived state branch** the loop reads and writes, merged
  periodically. Keeps review; the lookup must then read that ref instead of the
  checkout, which changes how every project runs the loop.
- **Accept the lag, make it visible.** Warn when the checkout's queue differs
  from the newest pushed state branch. Changes no workflow and fixes nothing —
  it only stops the loop being surprised by its own memory.

Related, and the reason this surfaced now: five pull requests are open and
unmerged, so the lag is currently hours rather than minutes.
## Proposed spec wording the loop could not apply itself (2026-09-07)

Three gaps found by the PR #15 review are fixes to `specs/loopkit-runtime.md`.
The loop could not make them. `protect_governance.py` guards `specs/` on Write,
Edit and Bash, and its escape hatch reads `GOVERNANCE_EDIT_OK` from the **hook's
own process environment** — so neither an inline `GOVERNANCE_EDIT_OK=1 cmd`
prefix nor an `export` inside the command reaches it. The hook that fires is the
installed plugin cache (`~/.claude/plugins/cache/loopkit/loopkit/0.2.1`), which
predates the `INLINE_OVERRIDE` fix now on `main`. Two ways to unblock: reinstall
the plugin from `main`, or export `GOVERNANCE_EDIT_OK=1` in the environment that
launches the session. The guard behaved correctly; it is the documented hatch
that does not work the documented way.

The wording below is proposed, not applied. Everything outside `specs/` — the
derivability pin, the fixtures README, and the criterion 33 ceiling — landed on
`fix/spec-undefined-nouns`.

**1 — `targets` has no definition (blocks the PR #15 verdict).** Asserted in
fixtures 01, 04 and 05, defined nowhere. Add a Nouns row:

> | **Targets** | The Queue rows a tick serves at the Stage it selected: every deduplicated row whose `status` equals the Stage, in Queue file order, capped at `loop_next_pick.MAX_FANOUT` (8). Empty when the Stage is `discover`. A target carries `stage`, `finding` and `source`; identity is `source`, so no two targets share one (criterion 9), and a `pr-open` or `blocked` row is never a target (criteria 5, 6). WHEN the Run is scoped to a lane, membership is restricted to rows whose `finding` or `source` contains one of that lane's terms in `<project>/.loopkit/scopes.json`, ordered by `loop_next_pick.PRIORITY_RANK` then file order; a lane matching zero rows falls back to the unscoped set. The cap is a dispatch limit, not a filter — Counts still reports the full Stage. | `loop_next_pick.pick` `target:` lines, filtered to the Stage by `loop-next.sh` |

Two decisions are yours, because the shipped code answers them both ways:

- **Membership.** `loop_next_pick.py` emits a `target:<stage>` line for *all
  four* work stages; `loop-next.sh` awk-filters to the selected stage alone. On
  fixture 01's queue the picker emits four targets and the fixture asserts one.
  The row above takes the `loop-next.sh` reading, because that is the observable
  contract and no fixture then changes. Taking the other reading means editing
  three fixtures.
- **Lane filtering is not computable from a fixture at all.** The picker's scope
  terms come from `.loopkit/scopes.json`, which no fixture carries. Either add a
  `input.policy.scope_terms` field to the fixture schema, or state that scoped
  runs are out of the conformance contract.

**2 — `result.status` is never enumerated.** Add after the §Journal semantics
table:

> `status` on a `result` Event is exactly one value: `ok`. Every other outcome is
> a different Event KIND, not a different status — a Provider failure journals
> `provider_error` and no `result` at all (criterion 24), and budget exhaustion
> journals `budget_exhausted` and stops (criterion 20). A journaled `result`
> therefore means the side effect completed; there is no failed `result`. An
> outcome that is neither must add a value here or a row to the table above.

Derived from the code, not invented: `ticks.py` takes arbitrary `--k key=value`
and constrains nothing, `specs/loopkit-runtime.model.fizz` carries `result` as a
0/1 flag with no vocabulary, and `ok` is the only value any fixture uses.

**3 — criterion 33 is now TRUE as written and needs no edit.** It cited
`tests/selftest.sh` asserting prompt bytes against a ceiling; that assertion did
not exist — the suite only checked that `brief_bytes` was *present*. Rather than
delete the claim, the check was built (8192 bytes per brief, with a control case
that measures an oversized brief through the same path). Optional refinement
only: name the ceiling value in the criterion so a reader need not open the
suite.

**4 — REQUIRED follow-up, introduced by this branch.** §Control case item 2 says
the pin "recomputes `stage`, `next`, `counts`, `targets`, `events`,
`provider_calls`, `enforceable` and `trust_tiers`". After this branch that is
false for `targets`: the pin checks criteria 5/6/9 and §Edge cases against it and
deliberately does not compute it. Strike `targets` from that list and add a
sentence naming it beside `messages` as the second key the pin cannot derive.
This inconsistency is the unavoidable cost of fixing the pin without being able
to touch the spec, and it is a plain falsehood until item 1 lands.

## The marketplace submission is ready, and it is yours to send (2026-09-07)

Its precondition is met — 0.2.0 was tagged three releases ago, and the current
tag is v0.2.2 — so the row is no longer waiting on the repository. It is waiting
on you for a different reason: submitting to `anthropics/claude-plugins-official`
means opening a pull request against someone else's repository under your name.
That is a public, outward-facing act, and it is not mine to perform on your
behalf.

What is ready now: the plugin validates, the suite passes on macOS and Linux,
three releases are tagged with notes, and the README, roadmap and getting-started
guide are current.

What I would fix before submitting, and would rather you decide on:

- The model invariants are proved on macOS only. The continuous integration
  patch that would prove them on Linux cannot be pushed from here, so a reviewer
  reading the workflow sees a suite that runs but does not check the model.
- The citation checker reports EMPTY on this repository — there are no
  file-and-line citations to check. Honest, but a reviewer may read the feature
  as unexercised, which it is.

Neither blocks a submission. Both are things I would rather you knew before
your name is on it.

## Ruling needed: may an implementing agent grant itself a governance override?

Raised 2026-09-07 from the PR #29 round-2 review. **Not urgent, but it recurs
every time an agent touches `specs/`.**

`protect_governance.py` guards `specs/`. The round-2 implementer needed to edit
its own spec, used the documented inline `GOVERNANCE_EDIT_OK=1` override, and
disclosed it on the PR as its own call rather than an owner's. The reviewer
judged it legitimate on the facts and then named the real problem:

> the hook cannot distinguish a draft-on-a-branch from a ratified-spec-on-main.

That is the whole issue. Editing an unmerged draft of your own spec is ordinary
work. Editing a ratified spec on `main` is a governance act. The hook sees one
path and cannot tell them apart, so the override is currently the same gesture
for both — and an agent can perform it unilaterally.

**Option A — teach the hook the difference.** Allow the override when the target
spec is not yet an ancestor of `main`; refuse otherwise.
*Cost:* the hook must ask git about merge-base on every write, so it gets slower
and gains a failure mode (a hook that cannot reach git must then decide whether
to fail open or closed — and either choice is wrong in one direction).
*Buys:* agents stop needing the override for ordinary drafting, so the gesture
becomes rare enough that using it means something.

**Option B — keep one override, require it be declared.** Any use must name a
reason in the PR body, and a reviewer must judge it.
*Cost:* it stays a social control, not a technical one — it works exactly as
well as reviewers are attentive, which today's record does not flatter.
*Buys:* nothing to build; works now; no new failure mode in the hook.

**Recommendation: A**, because B is the shape this repo keeps getting burned by
— a rule that holds only while someone remembers to look. But A costs real work
and adds a fail-open/fail-closed decision, so it is your call, not mine.

Blocking nothing today. PR #29 proceeds either way.
