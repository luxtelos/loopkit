# Conformance fixtures — the cross-SDK contract

**These files are the contract. Any implementation of the LoopKit runtime, in
any language, MUST produce the expected output for every fixture here.** A
fixture is not a test of the Python implementation; it is the definition of
correct behaviour that the Python implementation happens to be the first to
satisfy. When Python and JS disagree, the fixture is right and both are wrong
until one of them changes.

The specification these fixtures pin is `specs/loopkit-runtime.md`. Where a
fixture and the spec disagree, that is a bug in one of them — file it, do not
pick a winner in code.

**The bar every fixture must clear:** an implementer holding only
`specs/loopkit-runtime.md` and this README, with no access to the Python code,
must be able to compute `expected` from `input`. A fixture whose answer cannot
be derived that way pins nothing across SDKs — it only records what one
implementation happened to print. `tests/pins/fixture-derivable.py` enforces
this: it recomputes the derivable keys from the spec's stated rules and fails
on any disagreement.

## Shape

Each file is one JSON object:

```
{
  "name":        "<matches the filename without .json>",
  "description": "<what this case exists to catch, in prose>",
  "input":  { "run": {...}, "queue": [...], "policy": { "concepts": [...] }, "journal": [...] },
  "expected": { "stage": ..., "next": ..., "events": [...], "messages": [...], ... }
}
```

- `input.run` — the Run's identity and wiring, which is what the `run_start`
  Event carries: `run_id`, `store_uri`, `provider`, `policy_path`, and `lane`
  (the lane this Run is scoped to; `""` when unscoped). These are inputs rather
  than derived values, because criterion 1 requires them in the journal and
  nothing else in the fixture supplies them.
- `input.queue` — Queue rows. Exactly the five columns `triage_state.py` writes:
  `finding`, `source`, `priority`, `spec`, `status`. Identity is `source`.
  `status` is one of `triage_state.VALID_STATUSES`.
- `input.policy.concepts` — OKF Concepts: `path` (bundle-absolute, leading `/`,
  trailing `.md`), `frontmatter`, `body` — the three fields of
  `okf_bundle.Concept`. Lane membership lives in `frontmatter.tags`; the
  matching rule is in `specs/loopkit-runtime.md` §Policy scoping.
- `input.journal` — Event lines already on disk, in the shape `ticks.py` appends
  to `state/ticks.jsonl`: `at`, `event`, then arbitrary flat fields. An empty
  journal is a fresh Run; a journal carrying a `run_start` is a resume.
- `expected` — the runtime's output. `stage` and `next` are always present.
  `counts`, `targets`, `provider_calls`, `enforceable` and `trust_tiers` appear
  only in the fixtures that exercise them; a key absent from `expected` is not
  asserted.

## Comparison rules

1. **`at` is never compared, and neither is a Message's `enqueued_at`.** Both
   are wall-clock timestamps; asserting their values would make every fixture
   fail one second later. Their **presence** IS asserted — a Message without
   `enqueued_at` fails `validate_message` and is dead-lettered, so omitting it
   would change the expected behaviour rather than just the expected bytes.
   Compare every other field of an expected event exactly.
2. **`expected.events` is the APPENDED DELTA, not the final journal** —
   the Events this run appends to `input.journal`, in order, and those only.
   The final journal is `input.journal ++ expected.events`. Comparing a delta
   is what isolates this run's behaviour from the setup; comparing a full
   journal would restate the input in every fixture and hide which line the run
   was responsible for.
3. **Events are compared in order**, as a prefix-free exact list: the runtime
   must append these Events, these only, in this sequence. A fresh Run's delta
   therefore begins with `run_start` (criterion 1) and a resumed Run's does not
   (criterion 2) — see fixtures 01/02/03/05 against 04.
4. **`messages` is compared field by field except `msg_id`**, which is a fresh
   UUID per enqueue, and `enqueued_at` per rule 1. `idempotency_key` IS
   compared — it is derived, not random, and a changed derivation is a
   behaviour change that must be caught.
5. **An empty `expected.messages` means no message may be enqueued**, not that
   messages are unchecked.
6. **TWO keys are not derivable from `input` alone: `messages` and `targets`.**
   This rule said "the one key" until 2026-09-07 and was false when it said it.

   **`messages` content.** What an agent proposes depends on the Provider, and
   the stub Provider's script is not carried in the fixture. So
   `tests/pins/fixture-derivable.py` asserts of `messages` only what the spec
   does determine: that every expected Message is one
   `knowledge_actor.validate_message` ACCEPTS.

   **`targets`.** `specs/loopkit-runtime.md` asserts this key in fixtures 01,
   04 and 05 and defines it nowhere — no shape, no membership rule, no
   ordering, no cap. Four things are open, and the shipped code answers two of
   them two different ways:
   - *Membership.* `loop_next_pick.py` emits a `target:<stage>` line for every
     one of the four work stages; `loop-next.sh` awk-filters to the selected
     stage alone. On fixture 01's queue the picker emits four targets and the
     fixture asserts one.
   - *Ordering.* File order unscoped, `PRIORITY_RANK` order inside a lane.
   - *The cap.* `loop_next_pick.MAX_FANOUT` is 8; no fixture reaches it.
   - *Lane filtering.* The picker scopes rows by substring against terms from
     `<project>/.loopkit/scopes.json` — a file no fixture carries, so a scoped
     target set cannot be computed from `input` at all.

   So the pin asserts of `targets` only the properties the spec states —
   criterion 5 (no `pr-open` row), criterion 6 (no `blocked` row), criterion 9
   (no two targets share a `source`), §Edge cases (Stage `discover` serves
   none), and that a target names a real Queue row with that row's `finding`.
   It does NOT compute the key. It used to, from a hand-written reading of the
   undefined noun, which made it read as covering derivability when it did not.

   Stating both limits is the point — an unstated gap is how fixture 05 shipped
   asserting a message the runtime was required to refuse. The normative
   wording that would close `targets` is proposed in `inbox/needs-human.md`;
   once it lands in the spec, the pin should compute `targets` from it and this
   rule should shrink back to naming `messages` alone.

## Running them

```
python3 -m loopkit_core.conformance --fixtures spec/fixtures
```

Lands with M2 (`loopkit-core`), together with the stub Provider these run
against. Until M2 the fixtures are checked for well-formedness AND for
derivability:

```
python3 -c "import json,glob,sys; [json.load(open(f)) for f in glob.glob('spec/fixtures/*.json')]"
python3 tests/pins/fixture-derivable.py
```

The second is the one that matters. It is a reference implementation of exactly
the rules `specs/loopkit-runtime.md` states — stage precedence, the Next
three-way split, counts, the journal delta, provider replay, lane scoping and
the draft gate — and it recomputes those keys for every fixture from `input`.
`targets` is deliberately absent from that list: the spec states no rule to
implement, so the pin checks the properties it does state instead of inventing
one (rule 6). It
imports `okf_bundle.trust_tier` and `knowledge_actor.validate_message` rather
than reimplementing them, so a fixture that disagrees with the shipped code
fails here rather than in a reviewer's head.

The stub Provider is the control case: it is deterministic, so a fixture that
passes against it and fails against a real provider is a provider bug, and a
fixture that passes against everything is a fixture that asserts nothing.

## Adding a fixture

A fixture added in one SDK's PR without the other SDK's run of it is a CI
failure by design (pre-mortem row 8). Add the case, run both, or do not add it.
And add it to `tests/pins/fixture-derivable.py`'s reach: if the new fixture
asserts a key the pin cannot compute, either state the rule in the spec so it
can, or say in the fixture's description which key is not derivable and why.
