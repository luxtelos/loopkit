# ADR-0003 — Journal before act, over a small Store contract

- Status: accepted
- Date: 2026-09-07
- Deciders: repo owner (plan approved 2026-09-07)
- Context: `docs/research/runtime-plan.md` §2 and pre-mortem rows 3, 4 and 10

## Context

A Run performs side effects. It must survive being killed and resume without
doing them twice, and without asking the model again for an answer it already
has — a replayed model call is not merely wasteful, it can return a *different*
answer, which silently forks the Run's history (pre-mortem row 3).

**This problem is solved, commercially, by at least four vendors.** Temporal,
DBOS, Restate and Inngest all provide durable execution: journal every step,
replay on crash, exactly-once effects, across five or so languages, backed by
Postgres or a managed runtime. The plan's own quadrant says so plainly —
durability is a commodity. Writing our own is, on its face, the textbook mistake,
and pre-mortem row 10 is the note-to-self that says so: *"the durability half is
rebuilt when Temporal/DBOS already do it."*

So this ADR has to justify not adopting one, and has to say what would change
that.

## Decision

**Journal before act, over a Store contract of four methods.**

1. Before a side effect, append an `intent` Event carrying `run_id`, `step` and
   `idempotency_key`. After it, append a `result` Event.
2. On resume, replay the journal. A step with a journaled `result` is answered
   from the journal; **the Provider is not called again for it.** A step with an
   `intent` and no `result` is re-executed.
3. The side effect is applied through a conditional write keyed by the
   `idempotency_key`, so a re-execution converges instead of doubling.
4. A Store implements `put_if_absent / get / append / list`. `put_if_absent`
   must be atomic — `If-None-Match` on S3, `BEGIN IMMEDIATE` on SQLite, `O_EXCL`
   on the filesystem. That single method is what makes two writers safe.
5. Reference stores: filesystem, SQLite (one file, single runner) and
   S3-compatible. **No Postgres** — owner ruling 2026-09-07; nothing in scope
   needs several runners sharing one journal, and a schema in M2 is cost with no
   buyer.
6. **Exactly-once is not claimed.** At-least-once delivery with effectively-once
   application, in the same words `knowledge_actor.py` already uses. A crash
   between the effect and the result journal is real, and the conditional write
   is what makes it harmless. Any implementation claiming exactly-once across a
   crash is wrong.
7. The design is model-checked: `specs/loopkit-runtime.model.fizz` asserts
   `EffectAtMostOnce`, `NoEffectWithoutJournaledIntent` and
   `NoProviderCallAfterJournaledResult` over two writers and a bounded crash.

## Why not adopt Temporal, DBOS, Restate or Inngest now

Four honest reasons, none of which is "we can do better":

1. **Deployment shape.** LoopKit runs as a plugin on a laptop and in CI, with no
   service to talk to. Temporal wants a cluster or Temporal Cloud; DBOS and
   Restate want Postgres or their runtime; Inngest wants a hosted event bus.
   Every one of them turns "clone the repo and run the selftest" into "stand up
   infrastructure first". That is the single largest cost, and it is paid by
   every user, on every machine, forever.
2. **The stdlib constraint.** The core is stdlib-only Python, as everything
   vendored here already is. All four vendors mean an SDK, a dependency tree and
   a version treadmill inside the one component that must never be the reason a
   gate cannot run.
3. **We do not need the hard part.** Their value concentrates in distributed
   durability: many workers, one workflow, partial failures across a network.
   The owner's ruling removes that requirement — no Postgres, no several runners
   on one journal. Buying a distributed execution engine to run a single-runner
   loop is paying for the expensive 90% we refuse to use.
4. **The contract is four methods.** `put_if_absent / get / append / list` is
   small enough to specify in a page, model-check in one `.fizz` file, and
   implement three times. A `TemporalStore` — or a DBOS one — is then a later
   *adapter* behind the same four methods, not a rewrite (pre-mortem row 10 says
   exactly this).

**What we give up by not adopting one, stated plainly:** their retry policies,
timers, signals, versioning of in-flight workflows, and the operational
tooling — a UI that shows a stuck run, and years of production hardening against
crash interleavings we have not thought of. Our substitute for that last item is
a model checker, which enumerates interleavings but only in the model we wrote.

## What would make us adopt one later

Written down now so the reversal is a decision and not a drift. Any one of these
is sufficient:

- **Multiple runners must share one journal.** The moment the no-Postgres ruling
  is reversed, we are building a distributed execution engine, and we should buy
  one instead.
- **A Run outlives the process by more than a restart** — human-in-the-loop waits
  measured in days, needing timers, signals and in-flight versioning. Rebuilding
  durable timers is where home-grown durability reliably becomes a project.
- **The Store contract grows past about six methods**, or `put_if_absent` needs
  transactions across keys. That is the signal that the small-contract bet has
  lost.
- **A conformance or MinIO pin catches a crash interleaving the model missed.**
  One is a bug; a pattern of them says the model is not the right instrument and
  hardened production code is.

Until then, the Store contract stays four methods and the adapter door stays
open.

## Consequences

**Good.** No infrastructure to run the loop. Stdlib-only core. The durability
property is model-checked rather than asserted. Swapping the Store is a class,
not a migration.

**Bad, and accepted.** We own the correctness of replay, and a subtle bug there
is expensive and quiet. We have no operational UI for a stuck Run. `list` over
S3 paginates and a naive implementation truncates at 1000 keys — pinned by spec
criterion 30, and still a foot-gun. SQLite refuses a second runner rather than
coordinating one, which is a real limitation presented as a feature; it is a
feature only because the ruling says we do not need the alternative.

**Neutral.** The journal is the same `state/ticks.jsonl` line shape `ticks.py`
already writes, so existing tooling reads it unchanged.

## Alternatives considered

**Adopt Temporal / DBOS / Restate / Inngest now.** Covered above: mature,
commodity, and the wrong deployment shape for a plugin that must run with no
service. Reconsidered under the triggers listed above.

**LangGraph checkpointers.** Closer to our shape and genuinely good, but it
brings the whole graph runtime with it and puts an agent framework inside core —
which contradicts ADR-0004's rule that adapters live at the edge.

**No journal; make everything idempotent and just retry.** Tempting, and wrong:
without a journal, resume cannot tell "not started" from "finished", so it
re-asks the model and can get a different answer. That is pre-mortem row 3
happening by design.

**Journal after the act instead of before.** Cheaper — one write on the happy
path. Rejected, and the model checker shows why: with the `intent` guard removed
the checker returns a counterexample at `{"effects": 1, "intent": 0}` — an effect
that happened with no record that it might have. This is not a hypothetical; it
is the buggy twin in the spec's Control case.
