# Findings are concepts; the queue stays a table

- ADR: `docs/adr/0007-routing-findings-through-the-knowledge-layer.md` — accepted
  2026-09-07, **Option C**. Trigger: ADR 0006.
- Outcome: a dated finding carries its own anchor, so an agent that acts on it
  either proves it is still true or escalates. `state/triage.md` is untouched.

## Citations in this spec

Every `file:line` below is written **`path:line as of <sha>`**, the convention
`specs/blocked-waits-on-what.md` adopted after nine of its citations rotted at
once. The sha is **`039b4c5`**, the merge of #45 into this branch, at which every
number below was re-measured. Two moved since the first draft measured them at
`3ea14d9`: `check-citations.py`'s `CITATION` pattern, which #45 pushed from line
120 to 174, and line 36 of `okf.py`, whose *number* holds while this PR changes its
*content* by one token (`Finding` joins `TYPES`) — the pin on it is what proves
the number. Round 3 replaced `okf.py`'s refusal list with a closed set, so every
citation of that file below the `TYPES` line is written **as of `c3da4a3`**, the
commit that made the change, and was measured there. To re-verify one: `git diff 039b4c5..HEAD -- <path>`; an empty diff
means the number still holds. `check-citations.py` matches `path:line` and ignores the
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
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:687 as of 039b4c5`).

Two things this spec found by measuring rather than by reading the ADR, both bad
news, both stated before anything else:

1. **The verification Option C was chosen for does not hold on the path the loop
   actually uses.** ADR 0007's case against Option A is that it "leaves
   verification forgeable, which makes the trust tiers decorative", and its case
   for the mailbox is that `apply_verify` refuses a self-authenticated human
   claim. `apply_verify` does refuse one — measured below — but the refusal is
   wired to the `verify` op alone
   (`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1130 as of 039b4c5`),
   and `apply_upsert` copies whatever frontmatter the message carries straight
   onto the concept
   (`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:572 as of 039b4c5`).
   So a `process:` actor writes `verified: [{by: human:akul}]` in an ordinary
   upsert and `trust_tier` returns `human-reviewed`
   (`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:535 as of 039b4c5`).
   Measured, not argued — the transcript is in criterion 4.
2. **Even the guarded door is only guarded for a committed message.**
   `_assert_human_claim_is_credible` cross-checks git authorship and allows
   unknown authorship, which its own docstring records as deliberate
   (`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1139 as of 039b4c5`).
   A tick that enqueues and drains in one breath commits nothing in between, so
   the check never fires. Measured in criterion 4 as well.

Neither is a reason to reject Option C. Both are the work Option C was ruled to
buy, and this spec is where it gets bought. A spec that repeated the ADR's
sentence about `apply_verify` without these two paragraphs would be claiming a
protection the write path does not have — which is the defect this repo hunts
hardest, one layer up from code.

The knowledge adapter is also **off** in this repo today —
`.loopkit/memory.json:17 as of 039b4c5` — and there is no `knowledge/` bundle.
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
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1244 as of 039b4c5`)
and stays a thing an operator runs, never a timer, hook or tick step. ADR 0006's
fourth clause is the reason and criterion 10 is the check.

## The boundary

This section is normative. ADR 0007 holds Option C loosely on exactly one point:
the boundary "must be stated so plainly that no agent has to think about it, or
facts will land in the wrong store". `FILES.md` opens by warning that a fact
filed in the wrong layer is a fact lost. So the rule is one question with four
answers and no judgement in any of them. The first draft had three; the fourth
was found by a reviewer applying the rule to this spec's own worked table, and
the hole it fills is described below it.

### The rule

> **Name the file whose bytes, if they changed, would oblige you to re-read this
> fact.**
>
> 1. You can name a repo file **at HEAD** — its bytes can change — and it is
>    **not** `state/triage.md`, `state/ticks.jsonl`, `state/progress.md` or
>    anything under `inbox/` → it is a **finding**. Write a concept. That file
>    is its `sources` entry.
> 2. The only file you can name **is** one of those four → it is **loop state**.
>    It belongs in a triage row's cells. Do not write a concept.
> 3. You can name **no file at all** → it is neither. It is a judgement waiting
>    on a person → `inbox/needs-human.md`, with the whole context.
> 4. The file you name is **immutable** — it is a file *in a commit*
>    (`git show <sha>:<path>`), and no later commit can change those bytes → it
>    is a **finding**, and its anchor is the commit id: never a HEAD path, and
>    never a *name* for a commit. Its `sources` entry is
>    `commit://<40-hex sha>/<path>`. A tag, a release or a version number is a
>    label for a commit, and a label can be moved or deleted. You may hand the
>    write path `tag://<tag>/<path>`; it resolves the tag to its commit and
>    records the commit, keeping the tag name beside it as a note. The actor
>    digests nothing for such a source; validation resolves `<sha>:<path>` in
>    git instead.

The rule is not asked in the abstract, and it is not asked twice. It is asked
about the one sentence you were about to write down, and the file you name
becomes the `sources` entry — so answering it and doing the work are the same
act. A fact whose answer you cannot produce has no anchor, cannot be validated
under ADR 0006, and must not be written as a concept at all.

### Why there is a fourth answer

Take the worked table's third row, *"v0.2.2's `loop-next.sh` reports IDLE on a
six-column queue"*, and apply the three-answer rule without judgement. The bytes
that make it true are `plugins/loopkit/scripts/loop-next.sh` as it stood in the
commit v0.2.2 names, `ef336b4` — a file in a commit, which cannot change. So no file whose bytes *can* change obliges
a re-read → case 3 → `inbox/`. Wrong: it is plainly a finding and needs no
human. The escape is to name `plugins/loopkit/scripts/loop-next.sh` at HEAD →
case 1 → a concept anchored to a file the fact is **not about**: the next edit
to `loop-next.sh` marks it stale while the fact about v0.2.2 stays true forever.
Both answers the three-answer rule gives are wrong, and choosing between them
is judgement — the one thing the rule exists to remove. The class is *facts
about a file in a commit*. A tag, a release and a version are how people *name*
such a commit; none of them is the immutable thing. Round 2 of this spec got
that wrong, and "A tag is a label, not an anchor" below is the correction.

The write path already has the slot; the rule was not pointing at it.
`_capture_digests` records nothing for a resource that carries a scheme
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:706 as of 039b4c5`),
and `scan_drift` walks only `repo-file` entries
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1270 as of 039b4c5`),
so a scheme-carrying source is never digested and never drifts in the actor's
scan — which is right for bytes that cannot move, and is also why the actor
cannot be what validates it. LoopKit's own adapter does that:
`artifact_drift`, at
`plugins/loopkit/loopkit_memory/okf.py:328 as of c3da4a3`, resolves
`<sha>:<path>` for every such source and `knowledge scan-drift` lists what it
finds beside the actor's reports.

### A tag is a label, not an anchor

Round 2 of this spec recorded `tag://v0.2.2/<path>` as the source and called it
immutable. A reviewer then ran `git tag -f v0.2.2 HEAD`: the bytes under the tag
differed, and `scan-drift` and `verify` both said nothing. Then `git tag -d
v0.2.2`: still nothing. The concept had no digest by construction and validated
on `verified {by, at}`, which records who and when and never *which bytes* — so
a fact about "v0.2.2's `loop-next.sh`" stayed validated after v0.2.2 meant
something else, and after it meant nothing. The same reviewer showed what does
work: a `commit://<sha>/<path>` source was accepted, never drifted, and read
back. A commit id is the immutable thing; a tag is a movable name for one.

So the write path anchors on the object. `tag://<tag>/<path>` is accepted as
INPUT and never recorded: the tag is resolved through `refs/tags/` alone — a
branch cannot wear the scheme — peeled to its commit, and the concept's source
becomes `commit://<sha>/<path>`, with the blob id of the cited bytes (`x_blob`)
and the tag name as a note (`x_label`). A tag name may contain slashes; git
refuses `refs/tags/a` beside `refs/tags/a/b`, so at most one split of the input
names a tag and there is no choice to make.

Measured on this branch in a standalone clone, adapter enabled, through
`memory.py knowledge enqueue` → `drain`. C is the tag form, D the reviewer's
commit form, CTL a HEAD file:

```
enqueue C    tag://v0.2.2/plugins/loopkit/scripts/loop-next.sh                 rc=0
enqueue D    commit://ef336b4d60e1…e17a/plugins/loopkit/scripts/loop-next.sh   rc=0
enqueue CTL  docs/GETTING-STARTED.md                                           rc=0
drain        applied 4, dead-lettered 0
on disk C    resource commit://ef336b4d60e1…e17a/…/loop-next.sh  x_blob ae2c0c41a01a  x_label tag:v0.2.2  digests 0
on disk D    resource commit://ef336b4d60e1…e17a/…/loop-next.sh  x_blob ae2c0c41a01a  (no label)         digests 0
on disk CTL  resource docs/GETTING-STARTED.md                                                           digests 1

scan-drift   tag untouched, nothing edited              none listed               <-- CONTROL
scan-drift   one-line edit to CTL's source              /findings/CTL.md  source-changed   <-- CONTROL: the scan fires at all
git tag -f v0.2.2 HEAD                                  bytes under the tag differ: YES
             recorded source ef336b4:…/loop-next.sh     still resolves to ae2c0c41a01a
scan-drift                                              /findings/C.md  label-moved    (D not listed)
git tag -d v0.2.2
scan-drift                                              /findings/C.md  label-gone     (D not listed)
git tag v0.2.2 ef336b4 (put back)
scan-drift                                              none listed               <-- CONTROL
```

Two different things are being said there and they must not be confused. **The
bytes are anchored on the commit and never depended on the tag**: after both
attacks the recorded source resolves to the recorded blob, and D, which never
named the tag, is not listed at any point. **The label is watched separately**:
C's words say "v0.2.2", so the day v0.2.2 stops naming that commit the concept
is reported, by kind, and a reader about to act on it treats it as failed (see
"Reading to act"). Before this round both attacks were silent.

**When the recorded commit no longer resolves** — history was rewritten and the
object pruned, or the clone is shallow and never had it — `artifact_drift`
reports `artifact-unresolvable`, `knowledge scan-drift` lists the concept, and
a reader SHALL treat it as failed validation: `stale`, escalate, do not act.
Pinned in `tests/pins/finding-sources-closed-set.py` by pruning a real commit
out of a scratch repository; the sha that still resolves is that pin's control.
It is reported by `scan-drift` and not by `verify`, on purpose: whether an
object is present depends on the clone, and `verify` is conformance, which must
give one answer for one tree. The cost is stated rather than hidden: in a
shallow CI checkout every case-4 concept reports unresolvable. That is the
correct answer there — nothing in that clone can prove the bytes — and it is
loud, which the old behaviour was not.

### Why case 2 is a refusal rather than advice

The write path accepts a **closed set** of source shapes and refuses everything
else, at the single function every source passes through,
`plugins/loopkit/loopkit_memory/okf.py:136 as of c3da4a3`:

| Shape | Accepted when | Recorded as |
|---|---|---|
| repo file | every `/`-separated segment is, spelled exactly, an entry of the directory before it; every segment but the last is a real directory and the last a real regular file (`lstat`, so a symlink is neither) | itself |
| artifact | `commit://<40 lowercase hex>/<path>`; the id is a **commit** object in this repository and `<path>` is a regular-file blob in its tree | itself, plus `x_blob` |
| tag (input only) | `tag://<tag>/<path>`; `<tag>` resolves under `refs/tags/` and the artifact rule then holds for its commit | the **artifact** shape, plus `x_label` |

and, for whichever shape it was, the path is not under `inbox/` and does not
begin `state/triage`, `state/ticks` or `state/progress` — case 2.

**This used to be a refusal list, and that was the defect.** Until round 3 the
code was `res.startswith(("inbox/", "state/triage", …))` on the raw string: it
named the known-bad and permitted the unknown. This spec then said "loop state
cannot be dressed up as a finding", and that sentence was false — a reviewer
wrote `x://state/triage.md` and `tag://main/state/triage.md` (a *branch* under
the tag scheme) straight through it, along with a tag that did not exist.
Adding those three to the list would have fixed three instances and kept the
class; this repository has paid for that mistake more than once. The rule is
now stated the other way round: a source is accepted only when it **is** one of
the shapes above. The corrected sentence is narrower and is what the code
enforces: **a source is refused unless it is one of the three shapes, and a
path that is one of the loop's four queues is refused in every shape.** The
four-queue list is still an enumeration, deliberately — it *is* case 2, four
named files — and it is safe now where it was not before because it runs on a
path already proven canonical: `..`, `./`, `//`, an absolute path, a scheme, a
case-dressed name and a symlink are none of them a directory entry, so there is
no spelling left for a queue to hide in.

It also closes a door no reviewer had tried. `apply_upsert` copies
`payload.frontmatter` onto the concept wholesale, `sources` included, and the
old check read only `payload.sources` — so the queue passed as
`payload.frontmatter.sources` was accepted. Both are resolved now.

Measured on this branch through the real write path, and pinned both ways in
`tests/pins/finding-sources-closed-set.py`:

```
ACCEPTED  plugins/loopkit/loopkit_core/loop_next_pick.py      a mechanism file          <-- CONTROL
ACCEPTED  state/2026-09-07-blocked-conflates-two-waits.md     a dated note              <-- CONTROL
ACCEPTED  commit://<sha>/<path>                               an artifact (case 4)
ACCEPTED  tag://v1/<path>, tag://rel/1.0/<path>               recorded as commit://…, lightweight and annotated
REFUSED   state/triage.md · state/ticks.jsonl · state/progress.md · inbox/needs-human.md · plugins/ · plugins
REFUSED   tag://v9.9.9-never/…        a tag that does not exist            (reviewer's F)
REFUSED   tag://main/state/triage.md  a branch under the tag scheme        (reviewer's G)
REFUSED   x://state/triage.md         an arbitrary scheme                  (reviewer's H)
REFUSED   tag://main/<good path> · x://<good path>          G and H again, isolated from the queue
REFUSED   tag://v1/state/triage.md · commit://<sha>/state/triage.md · commit://<sha>/inbox/…
REFUSED   abbreviated sha · uppercase sha · sha of nothing · 40 hex that is a BLOB · a ref name in the sha slot
REFUSED   commit path not in the commit · a tree · a symlink in the commit
REFUSED   COMMIT:// · https:// · file:// · v1:<path> (git revision syntax)
REFUSED   absolute path · ../outside · src/../state/triage.md · ./state/triage.md · ./<good path> · src//mech.py
REFUSED   State/Triage.md (case-dressed) · a symlink to the queue · a file that does not exist
REFUSED   trailing space · trailing newline · empty · no resource key · non-string · a list · a bare dict naming the queue
REFUSED   one good source beside one queue · the queue, and a scheme, via payload.frontmatter.sources
```

Every refused row returns rc=3 and queues nothing, and no refused probe ever
became a concept. Most refused rows cite a path that is accepted on its own, so
each isolates the one thing wrong with it: the reviewer's G is wrong twice (a
branch *and* a queue) and on its own cannot say which check caught it. The pin
was run red first against the unchanged adapter — 57 FAIL, the accepted rows
and controls passing — and each part of the fix was then broken on a copy, one
at a time, and the pin named it: the side door (3 rows), `refs/tags/` dropped
(1), the segment walk replaced by `is_file()` (7), the label check (4), commit
resolution (5), the queue rule inside a commit (3), the unresolvable report (2).

The accepted rows are the control. A closed set that accepts nothing is not a
boundary either, and a check fed only the bad cases could not tell.

### Worked examples

Ordinary cases first, then the two that look like the wrong side.

| The fact | Names which file? | Verdict |
|---|---|---|
| `loop_next_pick.WANT` omits `blocked`, so a blocked row is counted and never served | `loop_next_pick.py` at HEAD, mutable | finding (case 1) |
| The offload timing pin budgets 200 ms and fails under load, not under regression | the pin script at HEAD, mutable | finding (case 1) |
| v0.2.2's `loop-next.sh` reports IDLE on a six-column queue | `plugins/loopkit/scripts/loop-next.sh` in the commit v0.2.2 names — immutable; given as `tag://v0.2.2/…`, recorded as `commit://ef336b4d60e1…e17a/plugins/loopkit/scripts/loop-next.sh` | finding (case 4) |
| Row `plan §0.3 judge-runner` is at `new` | `state/triage.md` only | loop state (case 2) |
| The backlog is 39 rows, 12 blocked | `state/triage.md` only | loop state (case 2) |
| Whether agents may self-authorize the `specs/` override | no file — nobody has ruled | `inbox/` (case 3) |

The table was re-run against the four-answer rule row by row. Five rows answer
as they did under three; the third row is the one that had no honest answer and
now has one.

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
of an audit, and audits have been dated notes since `FILES.md:27 as of 039b4c5`
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

**Look-alike C — reads as a finding, is a finding, and the three-answer rule
still filed it wrong.** *"v0.2.2's `loop-next.sh` reports IDLE on a six-column
queue."* Nothing about it looks like loop state and nothing about it needs a
person, yet under three answers it lands in `inbox/` (no mutable file) or on a
HEAD path it is not about (false drift on the next edit). Under four answers the
question is answered the same way and the answer has a slot: the file is
immutable → case 4 → finding. The author may write
`tag://v0.2.2/plugins/loopkit/scripts/loop-next.sh`; what is recorded is
`commit://ef336b4d60e1…e17a/plugins/loopkit/scripts/loop-next.sh`, no digest,
validated by resolving it. A and B were not the whole test; C is what a rule
that only routes mutable files cannot see.

**Look-alike D — the reviewer's: the same fact, named by its commit.** *"At
`ef336b4`, `loop-next.sh` reports IDLE on a six-column queue."* Nobody wrote a
tag, so there is no label to watch: case 4, source `commit://ef336b4…/…`,
recorded as given. C and D end with the same anchor and the same `x_blob`; the
only difference on disk is C's `x_label`.

**All four, re-run on the round-3 write path.** A still splits: *the row says
`blocked`* names only `state/triage.md` → case 2; *no tick will ever serve it*
names `loop_next_pick.py` at HEAD, mutable → case 1. B still names only
`state/triage.md` → case 2, and the write path still refuses the concept. C and
D → case 4. Measured, in the same run as the transcript under "A tag is a label":
A's state half rc=3, A's mechanism half rc=0 with a digest, B rc=3, C rc=0, D
rc=0, and no concept exists for either refused half. None of the four needed a
judgement: each was routed by the one question, and the closed set changed none
of their answers. Case 4 changes nothing for A and B, because neither names a
file in a commit; it is a fourth answer, not a new tie-breaker.

### The shape of the mistake this rule is guarding against

A and B fail the same way if you answer from the *topic* instead of the rule:
both are about the queue, so a topic-matcher files them identically and gets
one wrong whichever way it leans. C fails differently — the question was
answered correctly and the rule had no slot for the answer. The rule never asks
what a fact is about. It asks what could falsify it, and now also admits that
for some facts the honest answer is *nothing can*.

## The write path

A finding-concept is written by enqueuing a message and draining it. It is never
hand-edited; `protect_governance` guards the bundle when the project lists it,
and the actor's idempotency layer assumes it owns the bytes.

| Field | Required? | Who may set it | Enforced by |
|---|---|---|---|
| `type` | required, exactly `Finding` | agent | this spec, criterion 2 |
| `title` | required | agent | this spec, criterion 2 |
| `status` | required; `draft` on write | agent may write `draft` only | this spec, criterion 5 |
| `sources` | required, ≥1, each one of the closed set: a repo file, or `commit://<sha>/<path>` (case 4; `tag://` is input only) | agent; `x_blob` and `x_label` inside an entry are **the adapter's, never the agent's** — typed values are dropped | `plugins/loopkit/loopkit_memory/okf.py:136 as of c3da4a3`; criteria 1, 2 |
| `generated {by, at}` | always present | **the actor, never the agent** | the code, criterion 3 |
| `verified {by, at}` | optional | `verify` op only; `human:` only by a human actor | criterion 4 — **not enforced today** |
| `stale_after` | optional | `stale` op, or the reader on a failed validation | the code, criterion 7 |
| `superseded_by` | optional | `deprecate` op | the code |
| `x_source_digests` | one record per repo-file source; absent on disk when every source is an artifact (measured, case 4) — such a source carries `x_blob` instead | the actor, never the agent | the code, criterion 9 |

Four notes on that table, each measured on this branch.

**`type: Finding` is legal and is the tenth house type.** OKF conformance
requires only a non-empty `type`; the house types at
`plugins/loopkit/loopkit_memory/okf.py:36 as of 039b4c5` are a search filter,
not a closed set the checker enforces. `Finding` joins that list in this PR —
`okf.py` is LoopKit's own adapter, not a vendored file; the vendored pair is
under `vendor/` — so the tenth filter value is discoverable beside the nine.
The pin on that line went red before the edit and green after it. One value
is specified rather than
"`Trap` for a hazard, `Post-mortem` for an incident", because a two-way choice
is judgement and this spec has spent its judgement budget on the boundary. It
also makes `memory.py knowledge search --type Finding` return exactly the
findings, which is what the read path needs.

**`generated` cannot be forged.** `apply_upsert` overwrites it from the
message's own actor and enqueued time at
`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:580 as of 039b4c5`.
An agent that types `generated: {by: human:akul, at: 1999-01-01T00:00:00Z}` into
the payload gets `by: process:probe/1` and the real timestamp on disk. Measured;
criterion 3 is the check.

**`verified` can be forged, today, on the ordinary path.** This is the write
path's one real defect and it is the reason criterion 4 exists. See criterion 4
for the transcript of all three doors.

**`status: stable` is a SHALL this spec adds, not a refusal the code makes.**
`VALID_STATUS` is `("draft", "stable", "deprecated")` at
`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:96 as of 039b4c5` and
nothing checks who wrote which. Conformance run with `require_sources` only asks
that a `stable` concept name its evidence
(`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:707 as of 039b4c5`).
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
  `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1244 as of 039b4c5`,
  and the reader SHALL reuse that comparison rather than write a second one. Two
  implementations of one rule diverge, and the day they diverge is the day
  somebody trusts the wrong one.
- **Where the target is a file in a commit (case 4)** — every source shaped
  `commit://<sha>/<path>`: resolve `<sha>:<path>` in git and compare the blob
  id with the recorded `x_blob`. Equal → validated. If it does not resolve
  (`artifact-unresolvable`), resolves to other bytes (`artifact-changed`), or
  the entry carries an `x_label` whose tag no longer names `<sha>`
  (`label-moved`, `label-gone`) → failed. A failed label with sound bytes is
  still failed: the concept's words name the label, and a reader cannot act on
  "v0.2.2's …" once v0.2.2 is something else. The report says which half
  broke. A source on disk in any other scheme (`source-shape-unknown`) → failed,
  and `verify` exits 4 on it. This is the comparison `artifact_drift` performs
  and the reader SHALL reuse it, for the reason given above. It does **not**
  fall back to `verified {by, at}`: round 2 sent it there, to the one slot
  criterion 4 measures as forgeable, when a byte check was free.
- **Where the target is not version-controlled** — a released runtime's
  behaviour, an external service, a decision taken in a meeting — there is no
  digest and no object to resolve, so validation resolves `verified {by, at}`
  instead. No source shape exists for such a target today — the closed set has
  none — so a concept about one cites the repo file that records it. The
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
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:651 as of 039b4c5`)
and **deliberately does not touch `status`**, which the code says in its own
words at
`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:659 as of 039b4c5`.
Clobbering a human's `stable` would destroy the ratification the system exists
to preserve. Nothing in this spec may change that.

Where a superseding concept is known, `deprecate` carries `superseded_by` and
`link` writes the navigable trail — that is ADR 0007's "this moved, where did it
go". `link` requires both concepts to exist and raises otherwise
(`plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:664 as of 039b4c5`),
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

`FILES.md:34 as of 039b4c5` already routes code structure to *derived: query the
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
   NOT write a concept. The refusal SHALL apply to the path part of every
   accepted shape, and to `payload.frontmatter.sources` as to
   `payload.sources`. More generally the write path SHALL accept a source only
   when it is one of the closed set in "Why case 2 is a refusal", and SHALL
   refuse every other shape, named or not.

   Check `[today]`: `tests/pins/finding-sources-closed-set.py`, wired into
   `tests/selftest.sh`. It asserts both halves in one run — six accepted rows
   that must not be refused, among them a dated note and a mechanism file, and
   forty-five refused rows that must each return rc=3 and queue nothing. A check
   fed only the refusals passes against an `enqueue` that refuses everything.
   Run red before the fix (57 FAIL) and against seven single mutations of it.

2. A finding-concept SHALL carry `type: Finding`, a non-empty `title`, a
   `status`, and at least one `sources` entry naming an existing regular file
   or a file in a commit, `commit://<40-hex>/<path>` (case 4). The commit and
   the path in it SHALL resolve at write time; a `tag://` input SHALL resolve
   under `refs/tags/` and SHALL be recorded as its commit, never as the tag.
   `Finding` SHALL be listed in `okf.TYPES`.

   Check `[today]`: a validator over a fixture bundle, with a control per field.
   `type: Finding` must be accepted by `okf_bundle.conformance_errors` — measured
   on this branch, it is, and it round-trips through a drain unchanged — and a
   concept with an empty `type` must be rejected by the same call. `Finding in
   okf.TYPES` is pinned in `.loopkit/citations.json` and the pin was run red
   first. A concept with
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
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1151 as of 039b4c5`,
   which tests the git author of the message file. The second is allowed on
   purpose — the docstring at
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1139 as of 039b4c5`
   says unknown authorship is permitted — and it is the door the loop walks
   through every time, because a tick enqueues and drains without committing in
   between. The third bypasses the guard entirely, because
   `_assert_human_claim_is_credible` is called only on the `verify` branch at
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:1130 as of 039b4c5`
   while `apply_upsert` copies payload frontmatter wholesale at
   `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py:572 as of 039b4c5`.

   **The fix this criterion requires does not depend on git.** Refuse a `human:`
   claim whose message actor is not `human:`, and strip `verified` from upsert
   payload frontmatter. Git authorship stays as a second line for the committed
   case; it cannot be the first, because the ordinary case has no commit to read.

   Check `[today]`: the three-row table above, run as three fixtures, plus two
   controls — a `verify` op from a `human:` actor must still be APPLIED, and a
   `process:` actor writing `verified: [{by: "process:x/1"}]` through the
   permitted op must still be APPLIED. A guard that refuses every `verified`
   write would pass the three-row table and destroy the feature.

   **Dependency, escalated.** This criterion is in the spec because the write
   path table and every trust-tier claim in the read path rest on it. But the
   fix lands in `plugins/loopkit/loopkit_memory/vendor/knowledge_actor.py`,
   whose header says *do not edit here: fix upstream, re-vendor*. Whether to
   carry a local patch or wait on upstream is an owner's decision, filed in
   `inbox/needs-human.md` under *Criterion 4 of specs/findings-are-concepts.md*
   with both options costed and a `blocked` row keyed by the bridge. The same
   entry names the sentence in ADR 0007 that this measurement falsifies. The
   criterion stays RED until the ruling lands and the fix with it.

5. WHILE an agent is the author, a finding-concept's `status` SHALL be `draft`.
   Only a `verify` from a `human:` actor promotes it, and promotion to `stable`
   SHALL be accompanied by non-empty `sources`.

   Check `[today]`: a detector over the bundle reporting every `Finding` whose
   `status` is not `draft` and whose `verified` carries no `human:` entry. Its
   control is a concept that *is* human-verified and `stable`, which must not be
   reported. Note plainly what this is: `VALID_STATUS` at
   `plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:96 as of 039b4c5` admits
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
   - `[today]`: the case-4 half. A concept written from a `tag://` input
     reports nothing while the tag is untouched (the control), `label-moved`
     after `git tag -f`, `label-gone` after `git tag -d`, and nothing again once
     the tag is put back; a concept written from a `commit://` input is listed
     at no point; a concept whose commit has been pruned reports
     `artifact-unresolvable` while a sibling whose commit resolves does not.
     Same pin as criterion 1, and the transcript under "A tag is a label".
   - `[M2c]`: the non-version-controlled half — a concept with no `repo-file`
     and no `commit://` source validates on `verified` alone, and the reader
     prints the actor and date verbatim. `[M2c]` also owns the reader calling
     `artifact_drift` before it acts; today an operator's `scan-drift` does.

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
    `plugins/loopkit/scripts/check-citations.py:174 as of 039b4c5` — imports it,
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
    `{blocked: 12, done: 19, fixing: 1, inbox: 1, new: 5, spec-ready: 1}` — and,
    at `0848e3a`, the merge of #48 into this branch, 70 rows over 70 distinct sources —
    `{blocked: 14, done: 22, fixing: 1, inbox: 1, new: 31, spec-ready: 1}` —
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
- **Error.** A concept with a repo-file-shaped source (no scheme) and no
  matching `x_source_digests` record (hand-written, or upserted with
  `record_digests: false`): failed validation, never passed. A concept whose
  every source is `commit://` has no digest record by construction (case 4,
  measured) and validates by resolving it; a `commit://` entry with no `x_blob`
  (hand-written) still has to resolve, and one that does not is failed.
  Absence of an anchor is not evidence of freshness, in either shape.
- **Supersession.** A finding replaced by a later one: `deprecate` with
  `superseded_by`, then `link`. A `link` naming a concept that does not exist
  raises rather than writing a dangling trail, which is correct and must not be
  softened.
- **Trust.** A concept carrying only `process:` verification is
  `machine-confirmed`, never `human-reviewed`
  (`plugins/loopkit/loopkit_memory/vendor/okf_bundle.py:548 as of 039b4c5`). The
  read path SHALL print the tier, so a reader can weigh it. It SHALL NOT gate on
  the tier — that is a separate ruling nobody has made.
- **Concurrency.** Two agents draining one mailbox: the actor's lock and
  idempotency keys already cover it. Not re-specified here.
- **A finding about the queue itself.** Legal, and Look-alike A is one. Its
  `sources` name the queue's *code*, never the queue's *file*. The closed set at
  `plugins/loopkit/loopkit_memory/okf.py:136 as of c3da4a3` refuses the
  wrong version of it in every spelling the pin tries, which is a measured claim
  about forty-five shapes and not a proof about all of them — the reason it is
  an allow-list is that the unmeasured ones are refused by default.

## Control case

**Primary: the loop that exists must not change.** `state/triage.md` parses
identically, `loop-next.sh` prints identically, and
`tests/pins/loop-next-output-parity.py` passes unmodified. If this change makes
the tick behave differently by one byte, it has failed regardless of what else it
buys — Option C's entire cost story is that the queue does not move. Criterion 12.

**Secondary: a consumer who never enables the adapter must not notice.** LoopKit
ships as a marketplace plugin and `knowledge.enabled` is false by default.
Criterion 13, with its "before" captured on this branch and quoted above.

Both baselines exist now. `tests/selftest.sh` on this branch was **300 ok and
zero FAIL lines** at `3ea14d9`, and is **338 ok and zero FAIL lines** with #45
merged forward and this round's changes (macOS, read by FAIL lines) (one `ok` line contains the word FAIL in its
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
  `plugins/loopkit/hooks/protect_governance.py:79 as of 039b4c5`, and whether an
  agent may self-authorize it is an open question already sitting in
  `inbox/needs-human.md`. This spec used the override to write itself and says so
  in its PR. It does not claim that was permitted.
- **Whether the read path should gate on trust tier.** The edge-case section
  says it prints the tier and does not gate. Gating is a product decision nobody
  has made, and under this repo's routing rule a change that would encode an
  unmade decision goes to `inbox/`, not into a criterion.
- **Criterion 4's fix path** — patch the vendored actor and carry the patch,
  or fix upstream and re-vendor. Escalated with both options costed; see
  criterion 4. Until it is ruled the criterion is RED and criterion 5's
  detector is satisfiable by a forgery.
- **The ADR 0007 sentence this spec falsifies.** "`apply_verify` refuses a
  self-authenticated human claim" is true of the `verify` op and false of the
  path the loop uses. Correcting an accepted ADR is an owner edit; the wording
  is in the same inbox entry.
