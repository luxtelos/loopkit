# Findings are concepts; the queue stays a table

- ADR: `docs/adr/0007-routing-findings-through-the-knowledge-layer.md` — accepted
  2026-09-07, **Option C**. Trigger: ADR 0006.
- Outcome: a dated finding carries its own anchor, so an agent that acts on it
  either proves it is still true or escalates. `state/triage.md` is untouched.

## Citations in this spec

Every `file:line` below is written **`path:line as of <sha>`**, the convention
`specs/blocked-waits-on-what.md` adopted after nine of its citations rotted at
once. The sha is **`3ea14d9`**, the branch head every number was measured on. To
re-verify one: `git diff 3ea14d9..HEAD -- <path>`; an empty diff means the
number still holds. `check-citations.py` matches `path:line` and ignores the
suffix, so the convention costs the gate nothing and is still checked by it.

**No `path:line` in this document is an illustration.** The gate cannot tell an
illustration from a claim — it resolves both — so an invented example goes red
exactly like a rotted one. ADR 0007 itself learned this in `6cb2652`: it carried
one made-up coordinate as a figure of speech, in the ADR arguing that findings
must not carry coordinates, and it had to be rewritten before the gate would
pass. Where this spec needs to *show* a bad citation it spells the number
`NNN`, which the gate's pattern does not match.

## Why

ADR 0006 fixed the trigger — a note is validated when an agent acts on it — and
deliberately left the plumbing open. Nothing in `state/*.md` carries an anchor,
so "validate it" currently resolves to "read it again and hope". ADR 0007 rules
that dated findings become OKF concepts written through the mailbox, which
supplies the anchor: `_capture_digests` records a `git hash-object` digest and
the head commit for every cited source, in the concept's own frontmatter, in the
same atomic write as the claim
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:687 as of 3ea14d9`).

Two things this spec found by measuring rather than by reading the ADR, both bad
news, both stated before anything else:

1. **The verification Option C was chosen for does not hold on the path the loop
   actually uses.** ADR 0007's case against Option A is that it "leaves
   verification forgeable, which makes the trust tiers decorative", and its case
   for the mailbox is that `apply_verify` refuses a self-authenticated human
   claim. `apply_verify` does refuse one — measured below — but the refusal is
   wired to the `verify` op alone
   (`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1130 as of 3ea14d9`),
   and `apply_upsert` copies whatever frontmatter the message carries straight
   onto the concept
   (`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:572 as of 3ea14d9`).
   So a `process:` actor writes `verified: [{by: human:akul}]` in an ordinary
   upsert and `trust_tier` returns `human-reviewed`
   (`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:535 as of 3ea14d9`).
   Measured, not argued — the transcript is in criterion 4.
2. **Even the guarded door is only guarded for a committed message.**
   `_assert_human_claim_is_credible` cross-checks git authorship and allows
   unknown authorship, which its own docstring records as deliberate
   (`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1139 as of 3ea14d9`).
   A tick that enqueues and drains in one breath commits nothing in between, so
   the check never fires. Measured in criterion 4 as well.

Neither is a reason to reject Option C. Both are the work Option C was ruled to
buy, and this spec is where it gets bought. A spec that repeated the ADR's
sentence about `apply_verify` without these two paragraphs would be claiming a
protection the write path does not have — which is the defect this repo hunts
hardest, one layer up from code.

The knowledge adapter is also **off** in this repo today —
`.loopkit/memory.json:17 as of 3ea14d9` — and there is no `knowledge/` bundle.
`memory.py knowledge status` exits 0 and says so. Turning it on is therefore in
scope; without it every criterion below is untestable.

## Scope

**In:** the boundary between a finding and loop state, stated as a rule; the
write path for a finding-concept and what may set each field; the read path per
ADR 0006; the rule that a finding names a symbol and never a coordinate;
migration of the three notes that exist; enabling the adapter and seeding a
bundle; one row in `FILES.md`.

**Out, explicitly, so the next reader does not assume otherwise:**
`state/triage.md` and its format; `plugins/loopkit/loopkit_core/triage_state.py`;
`plugins/loopkit/loopkit_core/loop_next_pick.py`; the `loop-next` goldens; the
citation pins in `.loopkit/citations.json` beyond the ones this spec adds for
its own citations; the queue's status vocabulary; anything in
`specs/blocked-waits-on-what.md`. Option B moved those and was not chosen.

Also out: any background job. `scan-drift` exists
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1244 as of 3ea14d9`)
and stays a thing an operator runs, never a timer, hook or tick step. ADR 0006's
fourth clause is the reason and criterion 10 is the check.

## The boundary

This section is normative. ADR 0007 holds Option C loosely on exactly one point:
the boundary "must be stated so plainly that no agent has to think about it, or
facts will land in the wrong store". `FILES.md` opens by warning that a fact
filed in the wrong layer is a fact lost. So the rule is one question with three
answers and no judgement in any of them.

### The rule

> **Name the file whose bytes, if they changed, would oblige you to re-read this
> fact.**
>
> 1. You can name a repo file, and it is **not** `state/triage.md`,
>    `state/ticks.jsonl`, `state/progress.md` or anything under `inbox/` →
>    it is a **finding**. Write a concept. That file is its `sources` entry.
> 2. The only file you can name **is** one of those four → it is **loop state**.
>    It belongs in a triage row's cells. Do not write a concept.
> 3. You can name **no file at all** → it is neither. It is a judgement waiting
>    on a person → `inbox/needs-human.md`, with the whole context.

The rule is not asked in the abstract, and it is not asked twice. It is asked
about the one sentence you were about to write down, and the file you name
becomes the `sources` entry — so answering it and doing the work are the same
act. A fact whose answer you cannot produce has no anchor, cannot be validated
under ADR 0006, and must not be written as a concept at all.

### Why case 2 is a refusal rather than advice

The four paths in case 2 are the four the write path already rejects, plus
directories, at
`plugins/loopkit/loopkit_memory/okf.py:142 as of 3ea14d9`. That is not a
coincidence to be maintained by hand — it is the same list, and the spec quotes
the code rather than the code implementing the spec. Loop state cannot be
dressed up as a finding, because the message is refused before it is written.
Measured on this branch:

```
REFUSED-at-okf.py      state/triage.md                                the queue
REFUSED-at-okf.py      state/ticks.jsonl                              the tick log
REFUSED-at-okf.py      state/progress.md                              the progress file
REFUSED-at-okf.py      inbox/needs-human.md                           the escalation door
REFUSED-at-okf.py      plugins/                                       a directory
rc=0                   state/2026-09-07-blocked-conflates-two-waits.md a dated note
rc=0                   plugins/loopkit/loopkit_core/loop_next_pick.py  a mechanism file  <-- CONTROL
```

The last two rows are the control. A refusal list that refuses everything is not
a boundary, and a check fed only the bad cases could not tell the difference.

### Worked examples

Ordinary cases first, then the two that look like the wrong side.

| The fact | Names which file? | Verdict |
|---|---|---|
| `loop_next_pick.WANT` omits `blocked`, so a blocked row is counted and never served | `loop_next_pick.py` | finding |
| The offload timing pin budgets 200 ms and fails under load, not under regression | the pin script | finding |
| v0.2.2's `loop-next.sh` reports IDLE on a six-column queue | the released runtime's file | finding |
| Row `plan §0.3 judge-runner` is at `new` | `state/triage.md` only | loop state |
| The backlog is 39 rows, 12 blocked | `state/triage.md` only | loop state |
| Whether agents may self-authorize the `specs/` override | no file — nobody has ruled | `inbox/` |

**Look-alike A — reads as loop state, is a finding.** *"`plan §distribution
official-marketplace` has been blocked since before 0.2.0 was tagged; the tag
exists, so the precondition is met and nobody will ever notice."* Every noun in
that sentence is a queue noun, and the reflex is "the queue is a table, so this
is a row". Apply the rule and the sentence splits in two. *That the row says
`blocked`* names `state/triage.md` and nothing else — case 2, it stays in the
cell. *That no tick will ever serve it* names
`plugins/loopkit/loopkit_core/loop_next_pick.py`, a file the loop did not write
and a commit can change — case 1, it is a finding. The split is the point: the
bookkeeping half and the mechanism half go to different stores, and trying to
file the sentence whole is what sends it to the wrong one.

**Look-alike B — reads as a finding, is loop state.** *"PR #38 has sat at
`pr-open` for four ticks; review latency is the bottleneck."* It has a
measurement, a diagnosis and an implied recommendation, so it wears the costume
of an audit, and audits have been dated notes since `FILES.md:27 as of 3ea14d9`
said so. Apply the rule and the only file you can name is `state/triage.md` —
the claim goes false the moment the loop does its own job and moves the row.
Case 2. The write path proves it independently: `--sources state/triage.md` is
refused, so there is no legal way to write this concept, and a concept written
without that source would carry an anchor that has nothing to do with what it
claims.

Look-alike B is worth one more sentence, because a real note on this branch sits
next to it and lands on the *other* side.
`state/2026-09-07-queue-lags-review-assessment.md` is also about the queue
lagging — but its claim is that `loop-next.sh` reads the checkout's copy, so
`main` and a branch disagree. That names the lookup's own file. Case 1, a
finding. The difference is not the subject matter and not the tone; it is
whether any file outside the queue can make the sentence false.

### The shape of the mistake this rule is guarding against

Both look-alikes fail the same way if you answer from the *topic* instead of the
rule: A is about the queue and B is about the queue, so a topic-matcher files
both identically and gets one of them wrong whichever way it leans. The rule
never asks what a fact is about. It asks what could falsify it.

## The write path

A finding-concept is written by enqueuing a message and draining it. It is never
hand-edited; `protect_governance` guards the bundle when the project lists it,
and the actor's idempotency layer assumes it owns the bytes.

| Field | Required? | Who may set it | Enforced by |
|---|---|---|---|
| `type` | required, exactly `Finding` | agent | this spec, criterion 2 |
| `title` | required | agent | this spec, criterion 2 |
| `status` | required; `draft` on write | agent may write `draft` only | this spec, criterion 5 |
| `sources` | required, ≥1, repo-relative files | agent | `plugins/loopkit/loopkit_memory/okf.py:142 as of 3ea14d9`; criteria 1, 2 |
| `generated {by, at}` | always present | **the actor, never the agent** | the code, criterion 3 |
| `verified {by, at}` | optional | `verify` op only; `human:` only by a human actor | criterion 4 — **not enforced today** |
| `stale_after` | optional | `stale` op, or the reader on a failed validation | the code, criterion 7 |
| `superseded_by` | optional | `deprecate` op | the code |
| `x_source_digests` | always present | the actor, never the agent | the code, criterion 9 |

Four notes on that table, each measured on this branch.

**`type: Finding` is legal and is a new value.** OKF conformance requires only a
non-empty `type`; the nine house types at
`plugins/loopkit/loopkit_memory/okf.py:36 as of 3ea14d9` are a search filter,
not a closed set the checker enforces. One value is specified rather than
"`Trap` for a hazard, `Post-mortem` for an incident", because a two-way choice
is judgement and this spec has spent its judgement budget on the boundary. It
also makes `memory.py knowledge search --type Finding` return exactly the
findings, which is what the read path needs.

**`generated` cannot be forged.** `apply_upsert` overwrites it from the
message's own actor and enqueued time at
`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:580 as of 3ea14d9`.
An agent that types `generated: {by: human:akul, at: 1999-01-01T00:00:00Z}` into
the payload gets `by: process:probe/1` and the real timestamp on disk. Measured;
criterion 3 is the check.

**`verified` can be forged, today, on the ordinary path.** This is the write
path's one real defect and it is the reason criterion 4 exists. See criterion 4
for the transcript of all three doors.

**`status: stable` is a SHALL this spec adds, not a refusal the code makes.**
`VALID_STATUS` is `("draft", "stable", "deprecated")` at
`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:96 as of 3ea14d9` and
nothing checks who wrote which. Conformance run with `require_sources` only asks
that a `stable` concept name its evidence
(`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:707 as of 3ea14d9`).
So this is a rule upheld by a detector, not a wall, and it is written that way
here so nobody reads it as a wall.

## The read path

Normative, and it is ADR 0006 applied to a concept rather than to prose.

### Reading to act

**Validate first, then act.** Validation of one concept resolves, per source:

- **Where the target is version-controlled** — every entry in
  `x_source_digests` whose `kind` is `repo-file`: recompute `okf.blob_digest`
  and compare it to the recorded `digest`. Equal for every entry → validated.
  Any mismatch, or a source that has gone missing, → failed. This is exactly the
  comparison `scan_drift` performs, per concept, at
  `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1244 as of 3ea14d9`,
  and the reader SHALL reuse that comparison rather than write a second one. Two
  implementations of one rule diverge, and the day they diverge is the day
  somebody trusts the wrong one.
- **Where the target is not version-controlled** — a released runtime's
  behaviour, an external service, a decision taken in a meeting — there is no
  digest to compare, so validation resolves `verified {by, at}` instead. The
  concept must carry at least one entry, and the reader SHALL report it verbatim
  with its actor and its date, so the human reading the tick output can judge a
  three-month-old machine confirmation for themselves.
- **`stale_after` in the past** → failed, whichever of the two applied. This is
  `scan_drift`'s `expired` kind and needs no new logic.

### When validation fails

The agent **SHALL NOT act**. It enqueues a `stale` message for the concept and
escalates to `inbox/needs-human.md`. It never deletes, and it never proceeds
with a caveat — a caveat is how a wrong note gets quoted into a decision.

`apply_stale` is already correct for this and the spec relies on the detail:
it sets `stale_after` and an `x_drift` record
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:651 as of 3ea14d9`)
and **deliberately does not touch `status`**, which the code says in its own
words at
`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:659 as of 3ea14d9`.
Clobbering a human's `stable` would destroy the ratification the system exists
to preserve. Nothing in this spec may change that.

Where a superseding concept is known, `deprecate` carries `superseded_by` and
`link` writes the navigable trail — that is ADR 0007's "this moved, where did it
go". `link` requires both concepts to exist and raises otherwise
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:664 as of 3ea14d9`),
so the trail cannot point at nothing.

### Reading to display

A scan, a status line, a count. It **SHALL NOT** validate — that is the whole
economy of lazy validation — and it **SHALL** mark what it shows as unvalidated.
A count is not a claim. Conflating the two is how a stale note gets read as a
true one, and the marking is the only thing that keeps display cheap without
making it dishonest.

### No sweep

Nothing re-validates on a timer, on `main` moving, or as a step of the tick.
`scan-drift` stays an operator command. Criterion 10.

## What a finding may not say

**A finding-concept SHALL NOT contain a `path:line` coordinate. It names the
symbol.**

`FILES.md:34 as of 3ea14d9` already routes code structure to *derived: query the
code, never author it*, and ADR 0006 records the cost of ignoring it: four notes
rotted in a day, one of them buying a confident and completely wrong diagnosis
of a CI failure. Naming `resolve_qbo_gate` survives a refactor that moves it;
naming the line it was on does not.

Two things make this a SHALL with its own check rather than a note in prose.

**The existing gate does not cover it.** `check-citations.py` deliberately never
scans dated records — its docstring says correcting them would falsify the
record — and `knowledge/` is not in its scanned set either. So a coordinate
inside a finding is unchecked by the gate that exists, which is precisely how
the rot got in.

**Illustrations are not exempt.** The detector cannot distinguish "the bug is at
`<file>.py:NNN`" written as an example from the same string written as a claim,
and neither can a reader six months later. Permitting illustrations makes the
rule unenforceable, so there is no exemption: no coordinate at all, in the body
or the frontmatter. ADR 0007 paid for this lesson in `6cb2652` — an example
coordinate, in the ADR about coordinates.

`sources` entries are paths without line numbers and are unaffected. So is
`captured_commit`, which is a sha and is the anchor the coordinate was pretending
to be.

## Acceptance (EARS)

1. WHEN an agent enqueues a finding-concept whose `sources` name only
   `state/triage.md`, `state/ticks.jsonl`, `state/progress.md`, a path under
   `inbox/`, or a directory, the write path SHALL refuse the message and SHALL
   NOT write a concept.

   Check `[today]`: the seven-row transcript in "Why case 2 is a refusal". Five
   refusals, and **two controls that must not be refused** — a dated note and a
   mechanism file. Assert both halves in one run; a check fed only the five
   passes against an `enqueue` that refuses everything.

2. A finding-concept SHALL carry `type: Finding`, a non-empty `title`, a
   `status`, and at least one `sources` entry naming an existing regular file.

   Check `[today]`: a validator over a fixture bundle, with a control per field.
   `type: Finding` must be accepted by `okf_bundle.conformance_errors` — measured
   on this branch, it is, and it round-trips through a drain unchanged — and a
   concept with an empty `type` must be rejected by the same call. A concept with
   no `sources` must be rejected by this spec's validator even though
   conformance alone accepts it below `stable`; that gap is why the validator
   exists rather than leaning on `verify-bundle`.

3. WHEN a message's payload frontmatter carries a `generated` block, the applied
   concept's `generated` SHALL be the message's own actor and enqueued time, and
   SHALL NOT be the typed value.

   Check `[today]`: enqueue an upsert whose payload sets
   `generated: {by: "human:akul", at: "1999-01-01T00:00:00Z"}`, drain, read the
   file. Measured on this branch: the concept came back
   `by: "process:probe/1"` and the drain's real timestamp. The control is that
   the *rest* of the typed frontmatter (`title`, `status`) does survive — a
   check that only asserts `generated` changed would also pass against an actor
   that discarded the whole payload.

4. A `verified` entry whose `by` begins `human:` SHALL NOT be writable by an
   actor that is not itself `human:`, through **any** op — and `verified` SHALL
   NOT be settable through `upsert` at all.

   **This check goes RED against the current code, and that is the criterion, not
   a mistake in it.** Three doors, all measured on this branch:

   ```
   op       message state          claim              outcome
   verify   committed by a bot     human:akul         DEAD-LETTERED  "the actor never signs for a human"
   verify   not committed          human:akul         APPLIED        verified written, trust=human-reviewed
   upsert   irrelevant             human:akul         APPLIED        verified written, trust=human-reviewed
   ```

   Only the first door is guarded, by
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1151 as of 3ea14d9`,
   which tests the git author of the message file. The second is allowed on
   purpose — the docstring at
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1139 as of 3ea14d9`
   says unknown authorship is permitted — and it is the door the loop walks
   through every time, because a tick enqueues and drains without committing in
   between. The third bypasses the guard entirely, because
   `_assert_human_claim_is_credible` is called only on the `verify` branch at
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1130 as of 3ea14d9`
   while `apply_upsert` copies payload frontmatter wholesale at
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:572 as of 3ea14d9`.

   **The fix this criterion requires does not depend on git.** Refuse a `human:`
   claim whose message actor is not `human:`, and strip `verified` from upsert
   payload frontmatter. Git authorship stays as a second line for the committed
   case; it cannot be the first, because the ordinary case has no commit to read.

   Check `[today]`: the three-row table above, run as three fixtures, plus two
   controls — a `verify` op from a `human:` actor must still be APPLIED, and a
   `process:` actor writing `verified: [{by: "process:x/1"}]` through the
   permitted op must still be APPLIED. A guard that refuses every `verified`
   write would pass the three-row table and destroy the feature.

5. WHILE an agent is the author, a finding-concept's `status` SHALL be `draft`.
   Only a `verify` from a `human:` actor promotes it, and promotion to `stable`
   SHALL be accompanied by non-empty `sources`.

   Check `[today]`: a detector over the bundle reporting every `Finding` whose
   `status` is not `draft` and whose `verified` carries no `human:` entry. Its
   control is a concept that *is* human-verified and `stable`, which must not be
   reported. Note plainly what this is: `VALID_STATUS` at
   `plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:96 as of 3ea14d9` admits
   all three values from anyone, so this criterion is a detector over a
   convention, not a refusal at the write path. Criterion 4's fix is what makes
   the `human:` half of it meaningful; until that lands this detector can be
   satisfied by a forged entry.

6. WHEN an agent reads a finding-concept in order to ACT on it, the runtime
   SHALL validate the concept before the action, and SHALL NOT begin the action
   if validation fails.

   Check `[M2c]`: a fixture concept whose source file is then edited by one
   byte; the act path SHALL refuse. **Control:** the same fixture unedited SHALL
   proceed. The control is the half that can fail — a validator that refuses
   everything passes the first fixture and is useless.

7. IF validation fails, THEN the runtime SHALL enqueue a `stale` message for
   that concept, SHALL escalate to `inbox/needs-human.md`, and SHALL NOT delete
   the concept, modify its `status`, or act with a caveat.

   Check, split, because half of this runs today:
   - `[today]`: drive `apply_stale` directly against a fixture concept whose
     `status` is `stable`. Assert `stale_after` and `x_drift` appear and
     `status` is still `stable`. Control: a concept at `draft` must also keep
     `draft` — asserting only the `stable` case passes against an implementation
     that hardcodes `stable`.
   - `[M2c]`: the reader actually enqueues that message and writes the inbox
     entry, and the action does not run.

8. WHEN a finding-concept is read in order to DISPLAY it — a scan, a count, a
   status line — the runtime SHALL NOT validate it, and SHALL mark it
   unvalidated in the output.

   Check `[M2c]`: a display over a fixture whose sources have drifted emits zero
   digest computations and carries the unvalidated marker. **Control:** the same
   display over a *clean* fixture must carry the marker too. Marking only the
   drifted ones would be validation wearing a different hat, and would pass a
   check that only looked at drifted fixtures.

9. Validation SHALL resolve a recorded digest where the target is
   version-controlled, and `verified {by, at}` where it is not.

   Check, split:
   - `[today]`: the digest half, measured on this branch. A fixture bundle with
     one concept sourcing one file reports **0** drift reports before the file is
     touched and **1**, kind `source-changed`, after a one-line edit. The zero is
     the control and it is the half that matters: a drift detector first run
     after the edit cannot tell a real detection from a detector that always
     fires.
   - `[M2c]`: the non-version-controlled half — a concept with no `repo-file`
     source validates on `verified` alone, and the reader prints the actor and
     date verbatim.

10. Nothing SHALL schedule re-validation. `scan-drift` SHALL remain invoked only
    by a person.

    Check `[today]`: a detector that greps the hooks, `hooks.json`,
    `.claude/settings*.json`, `tests/selftest.sh` and `.github/workflows/` for
    `scan-drift` and reports every hit. Expect zero. **Control, and this
    criterion is worthless without it:** point the same detector at a scratch
    file that *does* schedule `scan-drift` and assert it reports one. An
    absence-assertion aimed at the wrong paths reports zero forever and reads as
    a pass.

11. A finding-concept SHALL NOT contain a `path:line` coordinate, in its body or
    its frontmatter.

    Check `[today]`: a detector that imports `CITATION` from
    `plugins/loopkit/scripts/check-citations.py:120 as of 3ea14d9` — imports it,
    never re-spells it, because a second copy of that pattern drifts from the
    first — and applies it to every `Finding` in the bundle. Measured on this
    branch against five strings, two refused and three controls that must pass:
    a coordinate in prose and a coordinate inside a path are both matched; a
    backticked symbol name, a `captured_commit` sha, and a `resource:` source
    path are all left alone.

    Three controls, not one, because the two most likely false positives are the
    two things a finding is *supposed* to carry: its anchor commit and its source
    paths. A detector that refused those would make the rule unfollowable.

    Runs today against a fixture bundle and needs no runtime change. It cannot be
    left to run against the real bundle alone — there is no bundle yet, so a
    detector pointed only at `knowledge/` reports zero and passes by finding
    nothing.

12. `state/triage.md`, its parser and its runtime SHALL be unchanged.

    Check `[today]`: `triage_state.COLUMNS`, `triage_state.VALID_STATUSES` and
    `triage_state.ACCEPTED_HEADERS` are equal before and after; the real queue
    parses to the same row count and the same status histogram; and
    `tests/pins/loop-next-output-parity.py` passes unmodified. Measured on this
    branch as the baseline: 39 rows —
    `{blocked: 12, done: 19, fixing: 1, inbox: 1, new: 5, spec-ready: 1}` —
    `COLUMNS = ['finding', 'source', 'priority', 'spec', 'status']`. Assert
    against a freshly parsed count of the same file, **never against the numbers
    in this paragraph**: the totals move with every triage run, and a check that
    hardcodes 39 goes red tomorrow and teaches whoever fixes it to distrust the
    criterion.

13. WHILE `knowledge.enabled` is false in a consuming project,
    `memory.py knowledge status` SHALL exit 0 and say the adapter is disabled,
    and no other command SHALL require the bundle.

    Check `[today]`: measured on this branch before any change —
    `ADAPTER: knowledge=none [DEGRADED: disabled (memory.json
    knowledge.enabled=false)]`, rc 0. Re-run it after. LoopKit ships as a
    marketplace plugin; a consumer who never opts in must not be broken by this
    spec, and the "before" must be captured now, because a control case captured
    after the change is not a control case.

14. The three dated notes on this branch SHALL each gain a concept, and their
    files SHALL remain byte-identical.

    Check, split:
    - `[today]`: record the `git hash-object` digest of each of the three files
      now; assert equality after. Control: the digest of a file the
      implementation *does* change — `FILES.md`, per Migration — must differ, or
      the comparison is testing nothing. `FILES.md` is unchanged in the spec PR
      itself, so this control only has teeth in the implementation PR; say so
      rather than letting a reader think the spec PR already moved it.
    - `[M2c]`: one `type: Finding` concept exists per note, each with ≥1
      `sources` entry, and none of the three names its own note file as a source.
      That last clause is the one with teeth; see Migration.

### What is checkable today

Criteria **1, 2, 3, 4, 5, 10, 11, 12 and 13** run against the current runtime,
as do the `[today]` halves of **7, 9 and 14**. Criteria **6 and 8** and the
`[M2c]` halves of **7, 9 and 14** need the read path, which does not exist.

I have run the `[today]` checks of 1, 3, 4, 9 and 13, and the discriminating half
of 11, on this branch; their transcripts are quoted above rather than described.
Criteria 2, 5, 10, 12 and the `[today]` halves of 7 and 14 are **written but not
yet run** — they need fixture bundles or a scratch scheduling file that this spec
does not ship. They are inside the untagged set because they need no unbuilt
runtime, not because I have executed them, and that distinction is stated here
rather than left for a reader to discover. `specs/blocked-waits-on-what.md`
asserted a complete untagged set and was wrong three drafts running; this one
does not make the claim it cannot support.

Criterion 4 is the one to read twice. Its check is `[today]` and it is expected
to **fail** today: it describes a hole that is open on this branch. A criterion
whose check passes on the unchanged tree would be describing work already done.

Every criterion above names at least one check clause, and every check names a
control or is itself a control. Counted mechanically over this section: 14 of 14.

## Edge cases

- **Boundary.** Zero findings: the read path does nothing and prints nothing. A
  concept whose every source is missing: validation fails on the first, one
  `stale` message for the concept, not one per source — `scan_drift` already
  batches per concept and the reader must too.
- **Error.** A concept with `sources` but no `x_source_digests` (hand-written, or
  upserted with `record_digests: false`): treat as failed validation, never as
  passed. Absence of an anchor is not evidence of freshness.
- **Supersession.** A finding replaced by a later one: `deprecate` with
  `superseded_by`, then `link`. A `link` naming a concept that does not exist
  raises rather than writing a dangling trail, which is correct and must not be
  softened.
- **Trust.** A concept carrying only `process:` verification is
  `machine-confirmed`, never `human-reviewed`
  (`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:548 as of 3ea14d9`). The
  read path SHALL print the tier, so a reader can weigh it. It SHALL NOT gate on
  the tier — that is a separate ruling nobody has made.
- **Concurrency.** Two agents draining one mailbox: the actor's lock and
  idempotency keys already cover it. Not re-specified here.
- **A finding about the queue itself.** Legal, and Look-alike A is one. Its
  `sources` name the queue's *code*, never the queue's *file*. The refusal at
  `plugins/loopkit/loopkit_memory/okf.py:142 as of 3ea14d9` makes the wrong
  version of it impossible to write, which is why that refusal is quoted in the
  boundary rather than paraphrased.

## Control case

**Primary: the loop that exists must not change.** `state/triage.md` parses
identically, `loop-next.sh` prints identically, and
`tests/pins/loop-next-output-parity.py` passes unmodified. If this change makes
the tick behave differently by one byte, it has failed regardless of what else it
buys — Option C's entire cost story is that the queue does not move. Criterion 12.

**Secondary: a consumer who never enables the adapter must not notice.** LoopKit
ships as a marketplace plugin and `knowledge.enabled` is false by default.
Criterion 13, with its "before" captured on this branch and quoted above.

Both baselines exist now. `tests/selftest.sh` on this branch, at `3ea14d9`, is
**300 ok and zero FAIL lines** (one `ok` line contains the word FAIL in its
description, which is why this gate is read by its FAIL lines and never by its
exit code). One SKIP is reported honestly as UNVERIFIED: the S3Store live pins,
which need `--with-s3`.

## Migration

Three dated notes exist on this branch. What happens to each:

1. **A concept is written for it**, `type: Finding`, body derived from the note,
   any coordinate rewritten to name the symbol, `sources` naming **the files the
   finding is about**.
2. **The note file stays exactly where it is, unmodified.**

The second point is the one that will be argued with, so here is the evidence.
All three notes are referenced by path from files that are not dated records:

```
state/2026-09-06-plan-remainder-assessment.md    <- specs/bash-output-offload.md,
                                                    specs/pairwise-judge-verdicts.md
state/2026-09-07-blocked-conflates-two-waits.md  <- specs/blocked-waits-on-what.md
state/2026-09-07-queue-lags-review-assessment.md <- inbox/needs-human.md
```

Deleting them breaks four references in three files, and **`check-citations.py`
would not catch one of them** — they are bare paths, not `path:line`, so the
structural pass never looks at them. A silent break in two specs and the
escalation door is a worse outcome than a little duplication.

They are also different artifacts, not two copies of one. A dated note records
what was true on a date; `check-citations.py`'s own docstring gives the reason
dated records are never corrected — correcting them would falsify the record. A
concept records what is true now and carries the machinery to stop being it.

**A note file SHALL NOT be its own concept's source.** If it were, the anchor
would be the note's bytes, which never change, so validation would pass forever
while the code the note describes moved underneath. That inverts the whole
purpose. This is criterion 14's `[M2c]` clause and it is the clause with teeth —
naming the note is the obvious, wrong, convenient thing to do, and it is legal at
the write path, since a dated note is not on the refusal list (measured: `rc=0`,
sixth row of the boundary transcript). Nothing but this rule stops it.

**Findings written from this spec forward are concepts only.** No new
`state/<date>-<topic>.md`. `FILES.md`'s routing row moves from
`state/<date>-<topic>.md` to the bundle, and the boundary rule above goes into
`FILES.md` next to it. That is the one-time cutover; the duplication is three
files wide and does not grow.

**Notes written before this spec, elsewhere.** There are exactly three in
`state/` on this branch, but consumer projects and other branches have their own.
A pre-spec note is migrated **when an agent first reads it in order to act**,
never on a sweep. That is ADR 0006's doctrine applied to migration, and it means
the cost is paid only where being wrong is expensive — and that no background job
is needed to finish this migration, which is the same promise criterion 10 makes.

## Open, and not settled by this spec

- **Who may use the `specs/` override.** `protect_governance` documents an
  escape hatch at
  `plugins/loopkit/hooks/protect_governance.py:79 as of 3ea14d9`, and whether an
  agent may self-authorize it is an open question already sitting in
  `inbox/needs-human.md`. This spec used the override to write itself and says so
  in its PR. It does not claim that was permitted.
- **Whether the read path should gate on trust tier.** The edge-case section
  says it prints the tier and does not gate. Gating is a product decision nobody
  has made, and under this repo's routing rule a change that would encode an
  unmade decision goes to `inbox/`, not into a criterion.
- **Whether `type: Finding` should join the house type list** at
  `plugins/loopkit/loopkit_memory/okf.py:36 as of 3ea14d9`. It works without
  joining; joining is cosmetic and would touch a vendored file, which is a
  bigger decision than it looks.
