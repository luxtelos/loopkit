# LoopKit runtime — a spec-first, model-agnostic supervisor loop

- Plan: `docs/research/runtime-plan.md` §2 (owner-approved 2026-09-07) · Milestone: M1
- Outcome: an application can call LoopKit as a runtime — one supervisor Run over
  a Queue, driven by typed Policy rather than prose, journalling every step so a
  crash resumes instead of restarting, and handing the caller a Projection —
  with the behaviour defined here rather than in any one implementation.

This file is the source of truth. `plugins/loopkit/loopkit_core/` (M2) and
`packages/js` (M5) are its build output, and `spec/fixtures/*.json` is the
executable form of the criteria below.

## Nouns

| Noun | What it is | Where it exists today |
| --- | --- | --- |
| **Run** | One supervisor invocation: a brief, a budget, a Store, a Provider, a Policy bundle. Identified by `run_id`. | new in M2 |
| **Queue** | The ordered set of work rows. Columns are exactly `finding, source, priority, spec, status` (`triage_state.COLUMNS`); identity is `source`; `status` is one of `triage_state.VALID_STATUSES` — `new, spec-draft, spec-ready, fixing, pr-open, blocked, inbox, done`. | `triage_state.py` over `state/triage.md` |
| **Stage** | The single stage a tick advances, a pure lookup over Counts. One of `fixing, spec-ready, spec-draft, new, discover`. Paired with a Next of `CONTINUE, WAIT, IDLE`. | precedence in `loop-next.sh`, counts in `loop_next_pick.py` |
| **Counts** | The per-status tally the Stage lookup reads: a mapping from each of `pr-open, fixing, spec-ready, spec-draft, new, blocked` to the number of Queue rows at that status, after deduplication by `source`. All six keys are ALWAYS present, `0` when no row holds one. This is the runtime's structured form; `loop_next_pick.py`'s tab-separated stdout is the same tally rendered for bash, where a zero `blocked` line is omitted — a rendering detail of that transport, not a difference in the tally. | `loop_next_pick.WANT` plus its `blocked` line |
| **Event** | One append-only journal line: `at` (RFC3339 UTC, second precision), `event` (the kind), then flat key/value fields. The kinds this runtime appends, and the fields each carries, are fixed by §Journal semantics: `run_start`, `stage`, `intent`, `result`. | `ticks.py` over `state/ticks.jsonl` |
| **Policy** | An OKF bundle of Concepts. A Concept is `path` (bundle-absolute, leading `/`, trailing `.md`), `frontmatter`, `body`; `status` ∈ `draft, stable, deprecated`; trust tier is derived from `verified` as `unverified → machine-confirmed → human-reviewed`. Lane membership is `frontmatter.tags`, matched by the rule in §Policy scoping — there is no `scopes` field on a Concept and there never was. | `okf_bundle.py` (`Concept`, `VALID_STATUS`, `trust_tier`, `CANONICAL_KEY_ORDER`, `ACTOR_RE`) |
| **Message** | The only way knowledge is written back: an OKF mailbox message. REQUIRED, exactly the set `validate_message` enforces: `type: okf.message`, `schema: okf-mailbox/1`, `msg_id`, `op`, `enqueued_at`, `enqueued_by`, `idempotency_key`, `reason`, plus `target` for the ops that take one. OPTIONAL: `priority`, `payload`. A Message missing any required field is refused and dead-lettered (criterion 18), so `enqueued_at` is load-bearing rather than decorative — this row listed it beside the optional `priority` until 2026-09-07, and fixture 05 duly omitted it. Ops are `upsert, deprecate, verify, link, stale, delete`. | `knowledge_actor.py` (`Message`, `VALID_OPS`, `validate_message`) |
| **Projection** | A named JSON Schema over Concepts, plus the JSON the runtime emits for it. | new in M4 |
| **Provider** | Anything implementing `complete(messages, tools, response_schema) → {text, tool_calls, usage}`. | new in M2 |
| **Store** | Anything implementing `put_if_absent / get / append / list`, with a conditional write. | new in M2 |

## Scope

**In:** the Run lifecycle; Queue identity and the Stage/Next lookup; the
journal semantics; the journal-before-act contract and replay; the Policy
scoping rule and the draft
trust gate; the Message write-back path; the Provider and Store contracts; the
Projection emit rule; the budget; and `spec/fixtures/*.json` as the executable
form of all of it.

**Out:** the wire format of any specific provider API; the hook shells and
anything Claude Code specific (M3 makes those adapters); MCP, A2A and AG-UI
(M4, edge adapters only — ADR-0004); the JS SDK (M5); the prompt-driven vs
policy-driven bench (M6); Postgres and any multi-runner-on-one-journal design
(refused, owner ruling 2026-09-07); a hosted service, a UI beyond an event
stream, and a vector store (non-goals in the plan).

## Constraints

- **Stdlib only** in the Python core, as everything vendored under
  `loopkit_memory/vendor/` already is. No PyYAML, no third-party client.
- **The core never calls a model.** A Provider is injected; the Runner's own
  decisions — Stage, Next, replay, scoping, dedup — are deterministic Python,
  reproducible from `(queue, policy, journal)` alone. This is the same
  determinism boundary `knowledge_actor.py` draws for `drain`.
- **Existing behaviour is not redefined.** Where a noun above exists today, this
  spec describes it generalised; a criterion that would change the current
  output of `triage_state.py`, `ticks.py`, `loop_next_pick.py`, `okf_bundle.py`
  or `knowledge_actor.py` is a bug in this spec, not a requirement.
- **A cited check must be able to fail.** Every `[today]` check below names a
  command that runs now AND asserts the thing it names. Two did not until
  2026-09-07: criterion 12 cited a model assertion that survived deletion of
  the guard it was said to protect, and criterion 27 described a counterexample
  that did not occur. `bash tests/pins/model-invariants-live.sh` now mutates
  every assertion and requires the checker to FAIL, and
  `python3 tests/pins/fixture-derivable.py` recomputes every fixture from the
  rules stated here. A criterion may cite an assertion only while that pin
  proves it live.
- **Symbols, not line numbers.** This spec cites code by symbol name. It sits
  outside the `scanned` list in `.loopkit/citations.json`, so
  `check-citations.py` would not catch a rotted `file:line` here — an
  unenforceable citation is worse than none.
- **Precondition for `spec-ready`:** criteria 20 and 21 below are marked
  UNRESOLVED and go to the owner. This spec is not implementable until both are
  ruled on; a Run's identity and its budget behaviour cannot be inferred.

## Journal semantics

The journal is append-only. These are the Events the runtime appends and the
fields each carries; nothing else is journaled, and a field named here is
required.

| Event | When | Fields beyond `at` and `event` |
| --- | --- | --- |
| `run_start` | once per Run, as its first appended Event | `run_id`, `store_uri`, `provider`, `policy_path` |
| `stage` | once per tick, after `run_start` | `stage`, `scope` |
| `intent` | before a side effect | `run_id`, `step`, `idempotency_key` |
| `result` | after a side effect | `run_id`, `step`, `idempotency_key`, `status` |

A result Event's `status` is one of exactly four values. No code produces a
result Event yet — the Runner is M2 — so this vocabulary is defined HERE and the
implementation follows it, rather than the other way round. That direction is
stated because the rest of this document derives its nouns from existing code,
and a reader is entitled to know which way each definition points.

| `status` | Meaning | Replay behaviour |
| --- | --- | --- |
| `ok` | the side effect completed and its result is journaled | never re-attempted |
| `failed` | the side effect ran and returned an error the runtime understands | never re-attempted; the Run surfaces the error |
| `refused` | a gate or policy blocked the side effect before it ran | never re-attempted; no side effect occurred |
| `abandoned` | the Run stopped between `intent` and `result` | re-attempted on resume, under the same `idempotency_key` |

`abandoned` is never written by the step itself. It is what a resume infers from
an `intent` with no matching `result`, which is why it is the only value that
permits a retry.

`scope` on a `stage` Event is the Run's lane, and the empty string when the Run
is unscoped. It is not derived from the Queue or the Policy; it is an input,
carried in a fixture's `input.run.lane`.

Four rules decide what a fixture's `expected.events` contains. They are stated
here because without them fixture 04 admitted three different answers, and an
implementer in another language could not compute any of them from prose.

**J1 — delta, not journal.** `expected.events` is what the run APPENDS to
`input.journal`, in order, and nothing else. The final journal is
`input.journal ++ expected.events`. A delta isolates the run's own behaviour;
a full journal would restate the input in every fixture and hide which line the
run was responsible for.

**J2 — `run_start` exactly once per Run.** A Run whose journal carries no
`run_start` is fresh and appends one as its first Event. A Run whose journal
carries one is a resume and appends none. It is the PRESENCE of `run_start`,
not the emptiness of the journal, that makes a Run a resume.

**J3 — a re-executed step does NOT re-journal its `intent`.** Criterion 10
requires an `intent` on disk before the effect. A step being re-executed on
resume already has one, for the same `(run_id, step, idempotency_key)`, so the
requirement is already met and a second `intent` would record an act that never
went unrecorded. The model states the same thing as a guard: `JournalIntent`
fires only when no intent is journaled.

**J4 — a resume is not a no-op.** It appends a `stage` Event, and the `result`
of every step it completes. So a Run's journal is NOT byte-identical across two
runs, and this spec does not claim it is. That claim stood in criterion 2 until
2026-09-07 and contradicted fixture 04's own expected output; the invariant
that does hold is J2.

## Policy scoping

Lane membership lives in a Concept's `frontmatter.tags`. There is no `scopes`
key on a Concept. The plan said `scopes`, criterion 14 said `scope`, and
fixture 05 encoded bare tags matching neither — three names for a field the
code does not have, and a fixture that matched no lane under the one matcher
the repo owns. The name is `tags`, because that is what
`okf_bundle.CANONICAL_KEY_ORDER` carries and what `okf.py` matches.

**S1 — in lane.** A Concept is IN LANE `<lane>` when any of its tags,
lowercased, is `lane/<lane>`, `scope/<lane>` or `domain/<lane>`, or ends with
`/<lane>`. A bare tag does NOT match: `reviewer` is not in the `reviewer` lane,
`lane/reviewer` is. This is exactly the rule `okf.py`'s `search` already
implements, restated here so an implementer holding only this spec can apply
it.

**S2 — unscoped.** A Run whose lane is `""` treats every Concept as in lane.
S3 still applies.

**S3 — enforceable.** A Concept is ENFORCEABLE for a lane when it is in lane
AND its `status` is `stable`. `draft` is excluded by criteria 15 and 16
whatever its tags; `deprecated` is not enforceable either. "Enforceable" and
"in lane" are different questions, and fixture 05 tags its draft Concept into
the lane on purpose so that the draft gate, not the lane filter, is the reason
it is excluded.

## Acceptance (EARS)

Each line names the command that checks it. `[today]` marks a command that runs
in this repo now; `[M2]` marks one that lands with `loopkit-core` and does not
exist yet. A criterion whose only check is `[M2]` is specified, not yet pinned —
that is stated rather than hidden.

### Run lifecycle and identity

1. WHEN a Run is started, the runtime SHALL record a `run_start` Event carrying
   `run_id`, `store_uri`, `provider` and `policy_path` (§Journal semantics), as
   the first Event it appends and before any Provider call. Check `[today]`:
   `python3 tests/pins/fixture-derivable.py` — fixtures 01, 02, 03 and 05 are
   fresh Runs and each expects `run_start` first, recomputed from rule J2.
   Check `[M2]`: `python3 -m loopkit_core.conformance --fixtures spec/fixtures`.
2. WHEN a Run is started with a `run_id` whose journal already carries a
   `run_start` Event, the runtime SHALL resume that Run and SHALL NOT append a
   second `run_start`; the journal SHALL carry exactly one for the Run's life.
   A resume is NOT a no-op and the journal is NOT byte-identical across two
   runs — see rule J4, which replaced that claim on 2026-09-07 because it
   contradicted fixture 04's own expected output. Check `[today]`:
   `python3 tests/pins/fixture-derivable.py` — fixture 04's input journal
   carries a `run_start` and its expected delta has none, while every
   fresh-Run fixture's delta begins with one.
3. WHEN two Runs are started concurrently against one Store with the same
   `run_id`, exactly one SHALL proceed and the other SHALL fail with a distinct
   "already claimed" error rather than interleaving. Check `[M2]`:
   `bash tests/pins/store-concurrent-runners.sh` (two runners, MinIO on the
   remote docker host, and a second SQLite opener).

### Stage and Next (the lookup lifted out of `loop-next.sh`)

4. WHEN the Queue holds rows at more than one work status, `decide()` SHALL
   return the Stage highest in the precedence `fixing > spec-ready > spec-draft
   > new`, and `discover` when none of those four is present. Check `[today]`:
   `python3 plugins/loopkit/scripts/test_loop_next_pick.py`, and
   `python3 tests/pins/fixture-derivable.py`, which recomputes the Stage of
   every fixture from the precedence alone. Check `[M2]`:
   `spec/fixtures/01-stage-precedence.json`.
5. WHILE a row is at `pr-open`, the runtime SHALL count and report it as a poll
   and SHALL NOT serve it as a Stage. Check `[today]`:
   `python3 tests/pins/fixture-derivable.py`. Check `[M2]`:
   `spec/fixtures/01-stage-precedence.json` — `pr-open` is 1 in `counts`, absent
   from `targets`, and the Stage is `fixing`.
6. WHILE a row is at `blocked`, the runtime SHALL count it, SHALL NOT poll it
   and SHALL NOT serve it as a Stage. Check `[today]`:
   `python3 tests/pins/fixture-derivable.py`. Check `[M2]`:
   `spec/fixtures/02-blocked-only.json` — Stage `discover` with `blocked: 2`.
7. WHEN the Stage is not `discover`, Next SHALL be `CONTINUE`. IF the Stage is
   `discover` AND any row is at `pr-open` or `blocked`, THEN Next SHALL be
   `WAIT`. Otherwise Next SHALL be `IDLE`. Check `[today]`:
   `python3 tests/pins/fixture-derivable.py` derives all three from Counts.
   Check `[M2]`: fixtures 01 (CONTINUE),
   02 (WAIT), 03 (IDLE) — the three-way split is the whole point, so all three
   are required.
8. `decide(counts) → (Stage, Next)` SHALL be a pure function: the same counts
   SHALL yield the same pair with no filesystem, clock or network read. Check
   `[M2]`: call it twice with a frozen clock and a read-only filesystem; outputs
   compare equal.
9. WHEN a Queue row's `source` appears twice, the runtime SHALL treat the two as
   one row and SHALL NOT emit two targets for it — identity is `source`, as
   `triage_state.find_row` already assumes. Check `[today]`:
   `python3 plugins/loopkit/scripts/triage_state.py upsert` twice with one
   `--source` prints `created` then `updated` and leaves one row.

### Targets

`targets` is the dispatch list for the tick. Until 2026-09-07 it was asserted by
three fixtures and defined nowhere, and the derivability pin supplied a
definition of its own — which made the pin read as covering derivability while
it covered its author's guess.

Two readings existed in the code and they disagree. `loop_next_pick.pick()`
emits a `target:<stage>` line for **every** work stage; `loop-next.sh` then
filters that stream to the served Stage alone. The filtered form is what a tick
consumes and what every fixture asserts, so the filtered form is the contract.
The picker's fuller stream is an internal transport, not the noun.

Definition:

- A **target** is a triple `(stage, finding, source)` where `stage` is the
  served Stage.
- `targets` contains one triple for each Queue row whose `status` equals the
  served Stage, and nothing else. A row at any other status contributes
  nothing, including a row at another WORK stage.
- WHEN the Stage is `discover`, `targets` is empty: `discover` serves no work.
- Ordering: for an unscoped Run, Queue order. For a scoped Run, by priority
  (`critical, high, medium, low`, then anything unrecognised), stable within a
  priority, which preserves Queue order among equals.
- At most `MAX_FANOUT` (8) triples. The cap is a dispatch limit, not a filter:
  `counts` still reports the full tally, so a stage of twenty rows shows `20` in
  `counts` and eight in `targets`.
- Identity is `source`, so a duplicated `source` contributes one triple
  (criterion 9).
- A scoped Run's lane membership is a substring match over terms from the
  project's lane configuration. That file is NOT part of a fixture's `input`, so
  a scoped `targets` is derivable only when the Run carries its lane terms
  explicitly. Every fixture today is unscoped.

10. `targets` SHALL be exactly the list above, in exactly that order. Check
    `[today]`: `python3 tests/pins/fixture-derivable.py` derives `targets` from
    this rule and compares it to each fixture. Check `[M2]`:
    `spec/fixtures/01-stage-precedence.json`, whose Queue holds a row at every
    status, so both the inclusions and the exclusions are exercised.

### Journal before act, and replay

10. WHEN an agent proposes a side effect, the runtime SHALL append an `intent`
    Event carrying `run_id`, `step` and `idempotency_key` BEFORE executing it,
    and a `result` Event after it. An `intent` ALREADY journaled for that
    `(run_id, step, idempotency_key)` satisfies this: a step re-executed on
    resume does not append a second one (rule J3). Check `[today]`:
    `bash tests/pins/model-invariants-live.sh` — assertion
    `NoEffectWithoutJournaledIntent`, proved live by mutation MUT-INTENT
    (drop the guard from BOTH `Claim` and `CallProvider` and the checker
    FAILS). Running the driver alone reports PASSED without telling you the
    assertion could ever fail, which is why the pin, not the driver, is the
    check named here.
11. WHEN a Run is resumed, the runtime SHALL replay the journal and SHALL NOT
    call the Provider again for any step whose `result` Event is journaled.
    Check `[today]`: `bash tests/pins/model-invariants-live.sh` — assertion
    `NoProviderCallAfterJournaledResult`, proved live by mutation MUT-REPLAY
    (make `Resume` increment `provider_calls`) — and
    `python3 tests/pins/fixture-derivable.py`, which derives fixture 04's
    `provider_calls == {"1": 0, "2": 1}` from the journal. Check `[M2]`:
    `spec/fixtures/04-replay-journaled-step.json` under the conformance run.
12. IF a step's `intent` is journaled but its `result` is not, THEN a resume
    SHALL re-execute that step, and the side effect SHALL be applied at most
    once — across both attempts, AND across an at-least-once redelivery of the
    already-committed step. Check `[today]`:
    `bash tests/pins/model-invariants-live.sh` — assertion `EffectAtMostOnce`,
    proved live by mutation MUT-EFFECT (delete the convergence guard from
    `ApplyEffect`; the checker FAILS with
    `Invariant: EffectAtMostOnce`). Until 2026-09-07 that assertion was
    VACUOUS: the model had no redelivery, `Commit` could not fire twice, so
    deleting the guard changed nothing and this criterion cited a check that
    could not fail. The `Redeliver` action, the single `ApplyEffect` site and
    the mutation pin all exist because of that.
13. WHEN the runner is killed between an `intent` Event and its `result` Event
    and then resumed, the Provider call count and the final journal (excluding
    `at`) SHALL equal those of an uninterrupted run of the same Run. Check
    `[M2]`: `bash tests/pins/kill-resume.sh`.

### Policy: scoping and the draft trust gate

14. WHEN a Run starts, the runtime SHALL pass each agent only the Concepts that
    are ENFORCEABLE for that agent's lane by rules S1–S3 (§Policy scoping), and
    SHALL NOT pass the whole bundle. Lane membership is `frontmatter.tags`; a
    bare tag matches no lane. Check `[today]`:
    `python3 tests/pins/fixture-derivable.py` — fixture 05 asserts the exact
    `enforceable` list for the `reviewer` lane, and the pin recomputes it from
    S1–S3 rather than trusting the fixture. Check `[M2]`:
    `python3 -m loopkit_core.conformance --fixtures spec/fixtures`.
15. IF a Concept's `status` is `draft`, THEN the runtime SHALL NOT pass it as
    enforceable to any agent, whatever its tags. Check `[today]`:
    `python3 tests/pins/fixture-derivable.py` — fixture 05 tags
    `/loop/agent-guess.md` INTO the `reviewer` lane on purpose, so the only
    reason it is absent from `enforceable` is its `draft` status. A fixture
    where the draft Concept is also out of lane would pass for the wrong
    reason. Check `[M2]`: the same fixture under the conformance run.
16. A Concept whose `generated.by` is a `process:` actor SHALL be written at
    `status: draft`, and SHALL NOT be eligible for `enforced_by` until its
    `verified` carries a `human:` actor — that is, until `trust_tier` is
    `human-reviewed`. Check `[M2]`: fixture 05 asserts
    `trust_tiers == {"/loop/never-merge.md": "human-reviewed", "/loop/agent-guess.md": "unverified"}`.
    Check `[today]`: `python3 tests/pins/fixture-derivable.py`, which computes
    the fixture's `trust_tiers` by calling `okf_bundle.trust_tier` itself
    rather than reimplementing it.
17. IF a Concept carries `enforced_by` while its `status` is `draft`, THEN the
    conformance run SHALL FAIL. Check `[M2]`: a negative fixture; the failure is
    the assertion, so a run that reports PASS on it is itself the bug.
18. WHEN an agent writes knowledge back, it SHALL do so only as a Message, and a
    Message failing `validate_message` SHALL be dead-lettered rather than
    retried. Check `[today]`: `knowledge_actor.py drain` exits 6 when it
    dead-letters and 3 on a validation failure; both codes are already asserted
    in `tests/selftest.sh`. Also `python3 tests/pins/fixture-derivable.py`,
    which passes every fixture's expected Message through the real
    `validate_message`: a fixture asserting a Message the runtime must refuse
    asserts the opposite of this criterion, which is what fixture 05 did until
    2026-09-07 by omitting `enqueued_at`.
19. WHEN the same `idempotency_key` is delivered twice, the second delivery
    SHALL apply nothing and SHALL add no log entry. Check `[today]`: the
    `keys.txt` short-circuit plus the byte-compare in `okf_bundle.write_concept`,
    exercised by `bash tests/selftest.sh`.

### Budget

20. **UNRESOLVED — owner ruling required (see Open questions, Q1).** WHEN a
    Run's budget is exhausted mid-step, the runtime SHALL journal a
    `budget_exhausted` Event and stop. Whether the Run is then *resumable* (stop
    cleanly, resume on a raised budget) or *terminal* (mark failed, require a
    new `run_id`) is not decided, and the two produce different journals for the
    same input. Check `[M2]`: a fixture cannot be written until this is ruled.
21. **UNRESOLVED — owner ruling required (see Open questions, Q2).** The budget
    SHALL be checked before each Provider call, never after. Whether the unit is
    Provider calls, tokens reported in `usage`, or wall-clock seconds is not
    decided. Check `[M2]`: `spec/fixtures/06-budget-exhausted.json`, once the
    unit exists.

### Provider contract

22. The runtime SHALL accept any Provider implementing
    `complete(messages, tools, response_schema) → {text, tool_calls, usage}`.
    Check `[M2]`: the stub, OpenAI-compatible and Anthropic providers are all
    driven through one `conformance` run.
23. A Provider's return value SHALL carry all three keys. `text` SHALL be a
    string, possibly empty. `tool_calls` SHALL be a list, possibly empty, each
    entry `{name, arguments}`. `usage` SHALL be a mapping carrying at least
    `input_tokens` and `output_tokens` as integers, and SHALL use `0` — never
    `null`, never a missing key — when the upstream API reports no count. Check
    `[M2]`: `python3 -m loopkit_core.conformance --check-provider <name>` runs
    one fixture and asserts the shape.
24. IF a Provider raises or returns a value failing criterion 23, THEN the
    runtime SHALL journal a `provider_error` Event and SHALL NOT journal a
    `result` for that step — so a resume retries it rather than treating a
    malformed answer as done. Check `[M2]`: a stub provider that returns `{}`.
25. A Provider that has not been exercised in CI SHALL be listed as `unverified`
    in the README by a script, not by hand. Check `[M2]`:
    `python3 -m loopkit_core.provider_status --check` fails when the README and
    the CI matrix disagree. (Pre-mortem row 2.)

### Store contract

26. The runtime SHALL accept any Store implementing `put_if_absent / get /
    append / list`. Check `[M2]`: the filesystem, SQLite and S3-compatible
    stores are driven through one `conformance` run.
27. `put_if_absent(key, value)` SHALL be atomic and SHALL return whether it
    wrote. WHEN two writers call it concurrently for one key, exactly one SHALL
    receive "wrote" and the other "already present"; neither SHALL observe a
    partial value. Check `[today]`, for the design:
    `bash tests/pins/model-invariants-live.sh` — the `Claim` action is this
    contract and assertion `LeaseIsExclusive` is what it buys. Mutation
    MUT-LEASE deletes `owner == ""` from `Claim`; a second writer then steals
    the lease mid-flight, both writers reach the Provider, and the checker
    FAILS with `Invariant: LeaseIsExclusive`. Until 2026-09-07 this criterion
    claimed that counterexample against a model that carried NO such assertion
    and passed the mutation cleanly. Two changes made the claim true: the
    assertion itself, and modelling `called` PER WRITER — as one shared flag it
    silently handed one writer's memory to the other, which is what made the
    lease guard unfalsifiable. Check `[M2]`, for the implementations:
    `bash tests/pins/store-conditional-write.sh` with two concurrent appenders
    against MinIO (`If-None-Match`), against SQLite (`BEGIN IMMEDIATE`), and
    against the filesystem (`O_EXCL`).
28. IF a second runner opens a SQLite Store already held by a live runner, THEN
    the open SHALL be refused with a distinct error and SHALL NOT interleave
    writes. Check `[M2]`: `bash tests/pins/store-concurrent-runners.sh`.
    (Pre-mortem row 4; SQLite over a network mount is unsafe for concurrent
    writers, so the contract refuses rather than degrades.)
29. `append(key, line)` SHALL be atomic per line: a concurrent reader SHALL
    never observe a partial line, and a crash mid-append SHALL leave the journal
    parseable. Check `[M2]`: `bash tests/pins/store-conditional-write.sh` reads
    while two writers append; every line parses as JSON. Check `[today]`:
    `okf_bundle.write_text_atomic` (tmp + `os.replace` + `fsync`) is the
    filesystem case and is already exercised by `bash tests/selftest.sh`.
30. `list(prefix)` SHALL return every key under the prefix in lexicographic
    order, and SHALL NOT silently truncate. Check `[M2]`:
    `bash tests/pins/store-conditional-write.sh` lists 2500 keys against MinIO
    and compares to the written set. (S3 paginates at 1000; a store that stops
    at the first page is the bug this pins — see Open questions Q3 for whether
    the contract should instead be an explicit iterator.)

### Projection

31. WHEN a caller requests a named Projection, the runtime SHALL emit JSON that
    validates against that Projection's schema, and SHALL omit every Concept not
    named by it. Check `[M2]`: `python3 -m loopkit_core.conformance --projection
    <name>` validates the emitted JSON and asserts the omitted set.
32. IF a requested Projection name is unknown, THEN the runtime SHALL fail with
    a distinct error and SHALL NOT emit a partial or empty document — an empty
    document is indistinguishable from a projection that legitimately matched
    nothing. Check `[M2]`: unknown-name case in the same run.

### The prompt ceiling

33. The single-shot supervisor brief SHALL be the only free-text instruction a
    Run carries; every other instruction SHALL reach an agent as a Concept.
    Check `[today, proxy]`: `bash tests/selftest.sh` asserts prompt bytes per
    tick against a ceiling. **This check is a proxy, not the criterion.** A
    byte ceiling cannot tell prose from a serialised Concept, so it catches
    growth and misses a smuggled instruction that stays under the cap. No
    command in this repo checks the criterion as written; the honest statement
    is that criterion 33 is pinned against regression and unpinned against
    circumvention. (Pre-mortem row 6.)

## Edge cases

- **Boundary.** Empty Queue → Stage `discover`, Next `IDLE` (fixture 03). One
  row → that row is both first target and the whole Stage. Empty Policy bundle →
  zero enforceable Concepts, which is legal and must not be read as "unscoped".
  Empty journal → a fresh Run, never a resume. A Concept body of zero bytes is
  legal; a Concept `path` not starting `/` or not ending `.md` is refused by
  `okf_bundle.resolve_target`. Budget of zero: undecided, folded into Q1.
- **Error.** Unparseable Queue file → the runtime SHALL refuse and say so, never
  report an empty Queue; the 2026-08-31 header-matching bug proved these two are
  indistinguishable to a caller and that the wrong one is silent. Unreachable
  Store → the Run does not start and nothing is journaled. Provider error →
  criterion 24. Malformed Message → dead-letter, not retry (criterion 18).
  Unknown Projection → criterion 32. A `status` outside `VALID_STATUSES` →
  `triage_state.validate_status` raises; the runtime SHALL propagate rather than
  coerce.
- **Concurrency.** Two writers on one journal → criterion 27, modelled in
  `specs/loopkit-runtime.model.fizz` and pinned against MinIO and SQLite. Two
  Runs with one `run_id` → criterion 3. Crash between intent and result →
  criteria 12 and 13. **Retry after partial success:** applies here and is the
  hard case — at-least-once delivery with effectively-once application, exactly
  as `knowledge_actor.py` states it. Exactly-once across a crash is NOT claimed
  by this spec, and any implementation claiming it is wrong. The two idempotency
  layers are the `idempotency_key` short-circuit and the convergent byte-compare
  apply; both must be present.
- **Not applicable.** Ordering between independent Runs: none is defined and
  none is required — Runs share no state but the Store, and the Store's contract
  is per-key. Time zones: every `at` is UTC with an explicit offset, as
  `ticks.py` writes it.

## Control case

The control case is the thing that proves each gate can fail, because a gate
that cannot fail is not a gate:

1. **The model.** `specs/loopkit-runtime.model.fizz` PASSES, and every one of
   its four assertions is proved able to FAIL by
   `bash tests/pins/model-invariants-live.sh`, which applies the mutation
   recorded beside each assertion and requires driver exit 1:
   MUT-EFFECT → `EffectAtMostOnce`, MUT-INTENT → `NoEffectWithoutJournaledIntent`,
   MUT-REPLAY → `NoProviderCallAfterJournaledResult`, MUT-LEASE →
   `LeaseIsExclusive`. The pin also refuses a mutation whose target string is
   no longer in the file, so a model that drifts away from its own pin fails
   rather than passing quietly. Two near misses are recorded so nobody
   re-derives them: removing `intent == 1` from `CallProvider` ALONE changes
   nothing, because `Claim` already implies it; and `EffectAtMostOnce` held
   vacuously until a `Redeliver` action existed to exercise the guard.
   A driver run on its own is NOT this control case — `PASSED` says nothing
   about whether the checker would ever say FAILED.
2. **The fixtures.** `python3 tests/pins/fixture-derivable.py` recomputes
   `stage`, `next`, `counts`, `targets`, `events`, `provider_calls`,
   `enforceable` and `trust_tiers` for every fixture from the rules in this
   document, and validates every expected Message through the real
   `validate_message`. It fails on each of the three defects found in the
   2026-09-07 review when they are reintroduced: bare tags → `enforceable` is
   `[]`; a missing `run_start` → the delta disagrees; a missing `enqueued_at` →
   the Message is refused. `messages` CONTENT is the one key it cannot derive,
   because that depends on the Provider — README rule 6 says so rather than
   leaving a reader to find out. The stub Provider is deterministic, so a
   fixture that passes against the stub and fails against a real provider
   isolates the provider.
3. **The repo.** `bash tests/selftest.sh` ends ALL PASS,
   `python3 plugins/loopkit/scripts/check-skills.py --plugin plugins/loopkit --strict`
   passes, `python3 plugins/loopkit/scripts/check-citations.py --root plugins/loopkit`
   passes, and `claude plugin validate .` passes — all four before and after this
   spec lands. M1 adds documents and two pins only, so any change in those four
   is caused by this PR and nothing else. Both pins run inside
   `tests/selftest.sh`, so neither can rot unnoticed by being a command nobody
   remembers to type.

## Open questions routed to the owner

These are in `inbox/needs-human.md` and in the PR body. None has a
recommendation attached, because each is a cost trade rather than a lookup.

- **Q1 — budget exhaustion (criterion 20).** Resumable or terminal?
- **Q2 — budget unit (criterion 21).** Provider calls, tokens, or seconds?
- **Q3 — `list` contract (criterion 30).** Total ordered list, or an iterator?
- **Q4 — dead-letter destination (criterion 18).** The mailbox's own
  `dead-letter/` directory, or `inbox/needs-human.md`?
