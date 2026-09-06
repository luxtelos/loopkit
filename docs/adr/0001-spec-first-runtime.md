# ADR-0001 — The runtime is specified before it is built

- Status: accepted
- Date: 2026-09-07
- Deciders: repo owner (plan approved 2026-09-07)
- Context: `docs/research/runtime-plan.md` §2, milestone M1

## Context

LoopKit is about to grow a second half. The first half — typed, drift-checked,
human-ratified knowledge — exists and works. The second half is a runtime: a
supervisor that starts agents from one brief, journals what they do, survives a
crash, and hands a caller a projection of the result.

The plan commits to two SDKs, Python in M2 and JavaScript in M5. That is the
decision that forces this one. Two implementations of an unspecified behaviour
do not converge; they diverge politely, each correct against its own tests,
until some third party discovers they disagree about what `blocked` means.

The repo already carries the rule — code is the build output, `specs/` is the
source of truth — and the owner's epic gate already requires a spec, an ERD and
ADRs before any implementation PR. This ADR records why that rule is load-bearing
here specifically, rather than restating it.

There is also a cheaper failure available: write the spec, build the Python, and
let the spec quietly become documentation of what Python happens to do. Pre-mortem
row 1 names it — "the spec is written and nothing consumes it".

## Decision

**The specification is written first, and it is executable.**

1. `specs/loopkit-runtime.md` defines the nouns and the EARS criteria. It is the
   source of truth. When an implementation and the spec disagree, the
   implementation is wrong until the spec is changed by a spec PR.
2. `spec/fixtures/*.json` is the executable form of the criteria: `(queue,
   policy, journal)` in, `(stage, next, events, messages)` out. **Every
   implementation in every language must produce the expected output.** This is
   what turns "model-agnostic, language-agnostic" from a claim into a check.
3. `specs/loopkit-runtime.model.fizz` model-checks the part that prose is worst
   at — the interleavings of a crash, a retry and two writers.
4. A criterion that no command can check is marked as such in the spec, in
   place, rather than being quietly written as though it were checked.

## Consequences

**Good.**

- Two SDKs have one definition of correct, and a disagreement between them is a
  fixture failure rather than an argument.
- The fixtures are the anti-drift mechanism: adding one in a Python PR without
  running it in JS fails CI by design (pre-mortem row 8).
- The model checker enumerates the crash interleavings nobody writes tests for.
  The buggy twin in the spec's Control case is the proof that it can fail.

**Bad, and accepted.**

- M1 ships no working code. The plan pays a milestone for a document set. The
  bet is that a wrong noun found in M1 costs a paragraph and the same noun found
  in M5 costs two SDKs.
- A spec is a second thing to keep true. It rots exactly as fast as nobody runs
  it, which is why criterion-by-criterion command naming and the fixtures matter
  more than the prose does.
- Some criteria are specified before they are pinnable — every `[M2]` check in
  the spec. These are honestly marked; the risk is that "specified" is read as
  "verified" by somebody skimming.

**Neutral.**

- The spec is guarded by `protect_governance.py`, so writing it needs
  `GOVERNANCE_EDIT_OK=1` per invocation. Note the guard is wired on
  `Write|Edit|MultiEdit|NotebookEdit` and **not** on `Bash`, unlike its sibling
  `protect_tests.py` — a shell redirect into `specs/` walks past it. Treat the
  guard as a convention that makes the intent visible, not as a wall.

## Alternatives considered

**Build the Python first, extract the spec from it.** Cheaper to start and it is
what most projects do. Rejected because the extracted spec is a description, and
a description cannot arbitrate between two implementations — it can only report
what the first one did. It also inverts the repo's own rule, and a rule that is
suspended when it is inconvenient is not a rule.

**Specify in prose only, no fixtures.** Half the cost. Rejected because prose
criteria are graded by whoever reads them, and the whole point of the second SDK
is that nobody will be reading both.

**Adopt an existing agent-framework spec.** There is not one. The plan's quadrant
found MCP, A2A and AG-UI standardised at the protocol edges and nothing
standardised at the runtime core. ADR-0004 covers the edges.

**Skip the model checker, rely on tests.** Rejected on evidence: the worked
example shipped with the `run-state-model` skill is a real bug whose test suite
mocked both guards to no-ops. The interleaving this runtime depends on — a crash
between intent and result, with a second writer — is precisely the shape unit
tests do not reach.
