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
| **Stage** | The single stage a tick advances, a pure lookup over Queue counts. One of `fixing, spec-ready, spec-draft, new, discover`. Paired with a Next of `CONTINUE, WAIT, IDLE`. | precedence in `loop-next.sh`, counts in `loop_next_pick.py` |
| **Event** | One append-only journal line: `at` (RFC3339 UTC, second precision), `event` (the kind), then flat key/value fields. | `ticks.py` over `state/ticks.jsonl` |
| **Policy** | An OKF bundle of Concepts. A Concept is `path` (bundle-absolute, leading `/`, trailing `.md`), `frontmatter`, `body`; `status` ∈ `draft, stable, deprecated`; trust tier is derived from `verified` as `unverified → machine-confirmed → human-reviewed`. | `okf_bundle.py` (`Concept`, `VALID_STATUS`, `trust_tier`, `CANONICAL_KEY_ORDER`, `ACTOR_RE`) |
| **Message** | The only way knowledge is written back: an OKF mailbox message with `type: okf.message`, `schema: okf-mailbox/1`, `msg_id`, `op`, `enqueued_at`, `enqueued_by`, `idempotency_key`, `reason`, `priority`, and `target` or `payload`. Ops are `upsert, deprecate, verify, link, stale, delete`. | `knowledge_actor.py` (`Message`, `VALID_OPS`, `validate_message`) |
| **Projection** | A named JSON Schema over Concepts, plus the JSON the runtime emits for it. | new in M4 |
| **Provider** | Anything implementing `complete(messages, tools, response_schema) → {text, tool_calls, usage}`. | new in M2 |
| **Store** | Anything implementing `put_if_absent / get / append / list`, with a conditional write. | new in M2 |

## Scope

**In:** the Run lifecycle; Queue identity and the Stage/Next lookup; the
journal-before-act contract and replay; the Policy scoping rule and the draft
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
- **Symbols, not line numbers.** This spec cites code by symbol name. It sits
  outside the `scanned` list in `.loopkit/citations.json`, so
  `check-citations.py` would not catch a rotted `file:line` here — an
  unenforceable citation is worse than none.
- **Precondition for `spec-ready`:** criteria 20 and 21 below are marked
  UNRESOLVED and go to the owner. This spec is not implementable until both are
  ruled on; a Run's identity and its budget behaviour cannot be inferred.

## Acceptance (EARS)

Each line names the command that checks it. `[today]` marks a command that runs
in this repo now; `[M2]` marks one that lands with `loopkit-core` and does not
exist yet. A criterion whose only check is `[M2]` is specified, not yet pinned —
that is stated rather than hidden.

### Run lifecycle and identity

1. WHEN a Run is started, the runtime SHALL record a `run_start` Event carrying
   `run_id`, the Store URI, the Provider name and the Policy bundle path, before
   any Provider call. Check `[M2]`: `python3 -m loopkit_core.conformance --fixtures spec/fixtures`
   asserts the first journal line of a fresh Run.
2. WHEN a Run is started with a `run_id` that already has a `run_start` Event in
   the journal, the runtime SHALL resume that Run and SHALL NOT append a second
   `run_start`. Check `[M2]`: `spec/fixtures/04-replay-journaled-step.json` run
   twice; the journal is byte-identical after the second run.
3. WHEN two Runs are started concurrently against one Store with the same
   `run_id`, exactly one SHALL proceed and the other SHALL fail with a distinct
   "already claimed" error rather than interleaving. Check `[M2]`:
   `bash tests/pins/store-concurrent-runners.sh` (two runners, MinIO on the
   remote docker host, and a second SQLite opener).

### Stage and Next (the lookup lifted out of `loop-next.sh`)

4. WHEN the Queue holds rows at more than one work status, `decide()` SHALL
   return the Stage highest in the precedence `fixing > spec-ready > spec-draft
   > new`, and `discover` when none of those four is present. Check `[today]`:
   `python3 plugins/loopkit/scripts/test_loop_next_pick.py`. Check `[M2]`:
   `spec/fixtures/01-stage-precedence.json`.
5. WHILE a row is at `pr-open`, the runtime SHALL count and report it as a poll
   and SHALL NOT serve it as a Stage. Check `[M2]`:
   `spec/fixtures/01-stage-precedence.json` — `pr-open` is 1 in `counts`, absent
   from `targets`, and the Stage is `fixing`.
6. WHILE a row is at `blocked`, the runtime SHALL count it, SHALL NOT poll it
   and SHALL NOT serve it as a Stage. Check `[M2]`:
   `spec/fixtures/02-blocked-only.json` — Stage `discover` with `blocked: 2`.
7. WHEN the Stage is not `discover`, Next SHALL be `CONTINUE`. IF the Stage is
   `discover` AND any row is at `pr-open` or `blocked`, THEN Next SHALL be
   `WAIT`. Otherwise Next SHALL be `IDLE`. Check `[M2]`: fixtures 01 (CONTINUE),
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

### Journal before act, and replay

10. WHEN an agent proposes a side effect, the runtime SHALL append an `intent`
    Event carrying `run_id`, `step` and `idempotency_key` BEFORE executing it,
    and a `result` Event after it. Check `[today]`:
    `node plugins/loopkit/skills/run-state-model/driver.mjs check specs/loopkit-runtime.model.fizz`
    — assertion `NoEffectWithoutJournaledIntent`.
11. WHEN a Run is resumed, the runtime SHALL replay the journal and SHALL NOT
    call the Provider again for any step whose `result` Event is journaled.
    Check `[today]`: the same driver run — assertion
    `NoProviderCallAfterJournaledResult`. Check `[M2]`:
    `spec/fixtures/04-replay-journaled-step.json` asserts
    `provider_calls == {"1": 0, "2": 1}`.
12. IF a step's `intent` is journaled but its `result` is not, THEN a resume
    SHALL re-execute that step, and the side effect SHALL be applied at most
    once across both attempts. Check `[today]`: driver assertion
    `EffectAtMostOnce`, with `Crash` bounded to one occurrence.
13. WHEN the runner is killed between an `intent` Event and its `result` Event
    and then resumed, the Provider call count and the final journal (excluding
    `at`) SHALL equal those of an uninterrupted run of the same Run. Check
    `[M2]`: `bash tests/pins/kill-resume.sh`.

### Policy: scoping and the draft trust gate

14. WHEN a Run starts, the runtime SHALL pass each agent only the Concepts whose
    scope matches that agent's lane, and SHALL NOT pass the whole bundle. Check
    `[M2]`: `spec/fixtures/05-draft-concept-not-enforceable.json` asserts the
    exact `enforceable` list for one lane.
15. IF a Concept's `status` is `draft`, THEN the runtime SHALL NOT pass it as
    enforceable to any agent, whatever its scope. Check `[M2]`: fixture 05 —
    `/loop/agent-guess.md` is `draft` and is absent from `enforceable`.
16. A Concept whose `generated.by` is a `process:` actor SHALL be written at
    `status: draft`, and SHALL NOT be eligible for `enforced_by` until its
    `verified` carries a `human:` actor — that is, until `trust_tier` is
    `human-reviewed`. Check `[M2]`: fixture 05 asserts
    `trust_tiers == {"/loop/never-merge.md": "human-reviewed", "/loop/agent-guess.md": "unverified"}`.
    Check `[today]`: `okf_bundle.trust_tier` already derives exactly this.
17. IF a Concept carries `enforced_by` while its `status` is `draft`, THEN the
    conformance run SHALL FAIL. Check `[M2]`: a negative fixture; the failure is
    the assertion, so a run that reports PASS on it is itself the bug.
18. WHEN an agent writes knowledge back, it SHALL do so only as a Message, and a
    Message failing `validate_message` SHALL be dead-lettered rather than
    retried. Check `[today]`: `knowledge_actor.py drain` exits 6 when it
    dead-letters and 3 on a validation failure; both codes are already asserted
    in `tests/selftest.sh`.
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
    `node plugins/loopkit/skills/run-state-model/driver.mjs check specs/loopkit-runtime.model.fizz`
    — the `Claim` action is this contract, and removing its guard produces a
    counterexample. Check `[M2]`, for the implementations:
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

1. **The model.** `specs/loopkit-runtime.model.fizz` PASSES. Its buggy twin —
   the same file with the `intent == 1` guard removed from BOTH `Claim` and
   `CallProvider` — yields a counterexample at `{"effects": 1, "intent": 0}` and
   the driver exits 1. Removing the guard from `CallProvider` alone changes
   nothing, because `Claim` already implies it; that near miss is recorded here
   so nobody re-derives it and mistakes a redundant guard for a checked one.
2. **The fixtures.** The stub Provider is deterministic, so a fixture that
   passes against the stub and fails against a real provider isolates the
   provider. A fixture asserting no `expected` key beyond `stage` asserts
   almost nothing; the README's comparison rules say which keys are live.
3. **The repo.** `bash tests/selftest.sh` ends ALL PASS,
   `python3 plugins/loopkit/scripts/check-skills.py --plugin plugins/loopkit --strict`
   passes, `python3 plugins/loopkit/scripts/check-citations.py --root plugins/loopkit`
   passes, and `claude plugin validate .` passes — all four before and after this
   spec lands. M1 adds documents only, so any change in those four is caused by
   this PR and nothing else.

## Open questions routed to the owner

These are in `inbox/needs-human.md` and in the PR body. None has a
recommendation attached, because each is a cost trade rather than a lookup.

- **Q1 — budget exhaustion (criterion 20).** Resumable or terminal?
- **Q2 — budget unit (criterion 21).** Provider calls, tokens, or seconds?
- **Q3 — `list` contract (criterion 30).** Total ordered list, or an iterator?
- **Q4 — dead-letter destination (criterion 18).** The mailbox's own
  `dead-letter/` directory, or `inbox/needs-human.md`?
