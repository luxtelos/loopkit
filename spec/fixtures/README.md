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

## Shape

Each file is one JSON object:

```
{
  "name":        "<matches the filename without .json>",
  "description": "<what this case exists to catch, in prose>",
  "input":  { "queue": [...], "policy": { "concepts": [...] }, "journal": [...] },
  "expected": { "stage": ..., "next": ..., "events": [...], "messages": [...], ... }
}
```

- `input.queue` — Queue rows. Exactly the five columns `triage_state.py` writes:
  `finding`, `source`, `priority`, `spec`, `status`. Identity is `source`.
  `status` is one of `triage_state.VALID_STATUSES`.
- `input.policy.concepts` — OKF Concepts: `path` (bundle-absolute, leading `/`,
  trailing `.md`), `frontmatter`, `body` — the three fields of
  `okf_bundle.Concept`.
- `input.journal` — Event lines, in the shape `ticks.py` appends to
  `state/ticks.jsonl`: `at`, `event`, then arbitrary flat fields.
- `expected` — the runtime's output. `stage` and `next` are always present.
  `counts`, `targets`, `provider_calls`, `enforceable` and `trust_tiers` appear
  only in the fixtures that exercise them; a key absent from `expected` is not
  asserted.

## Comparison rules

1. **`at` is never compared.** It is a wall-clock timestamp; asserting it would
   make every fixture fail one second later. Compare every other field of an
   expected event exactly.
2. **Events are compared in order**, as a prefix-free exact list: the runtime
   must emit these events, these only, in this sequence.
3. **`messages` is compared field by field except `msg_id`**, which is a fresh
   UUID per enqueue. `idempotency_key` IS compared — it is derived, not random,
   and a changed derivation is a behaviour change that must be caught.
4. **An empty `expected.messages` means no message may be enqueued**, not that
   messages are unchecked.

## Running them

```
python3 -m loopkit_core.conformance --fixtures spec/fixtures
```

Lands with M2 (`loopkit-core`), together with the stub Provider these run
against. Until M2 the fixtures are checked for well-formedness only:

```
python3 -c "import json,glob,sys; [json.load(open(f)) for f in glob.glob('spec/fixtures/*.json')]"
```

The stub Provider is the control case: it is deterministic, so a fixture that
passes against it and fails against a real provider is a provider bug, and a
fixture that passes against everything is a fixture that asserts nothing.

## Adding a fixture

A fixture added in one SDK's PR without the other SDK's run of it is a CI
failure by design (pre-mortem row 8). Add the case, run both, or do not add it.
