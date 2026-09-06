# ADR-0002 — Policy is an OKF bundle, not a prompt

- Status: accepted
- Date: 2026-09-07
- Deciders: repo owner (plan approved 2026-09-07)
- Context: `docs/research/runtime-plan.md` §2 and the quadrant in the same file

## Context

Every agent framework in the plan's quadrant answers "who owns the behaviour?"
with *prompts*. Rules live in an instruction string; the agent obeys them as
well as attention allows; nothing records which rule applied to which action, and
nothing stops the agent from rewriting the rule it dislikes. The governance
research the plan cites names the gap precisely: prompting *reduces* violations,
access control *blocks unconditionally*, and neither can enforce a constraint
over a *sequence* of actions.

LoopKit already holds the other half. `okf_bundle.py` gives typed concepts with
`status` (`draft | stable | deprecated`), an actor convention
(`human:<id>`, `process:<id>`, `<producer>/<version>`), and a derived
`trust_tier` of `unverified → machine-confirmed → human-reviewed`.
`knowledge_actor.py` gives the write path: a mailbox, six ops, idempotency keys,
a ledger, dead-letters, and a hard determinism boundary — `drain` never calls a
model. The pieces for policy-as-data are on disk and were built for a different
purpose.

The pressure this ADR resists is real and constant: it is always faster to add
one more sentence to a prompt than to write a concept, get it ratified, and scope
it to a lane.

## Decision

**A Run's rules are Concepts in an OKF bundle. The only free text a Run carries
is the single-shot supervisor brief.**

1. **Concepts are the instruction channel.** Doctrine, Trap, Invariant and Gate
   concepts carry `enforced_by` naming the mechanism that actually holds them.
   Everything an agent must obey arrives as a Concept.
2. **Scoping, not broadcast.** Each agent receives only the Concepts whose scope
   matches its lane — today's `traps_for`, generalised. An agent that receives
   the whole bundle is receiving a prompt with extra steps.
3. **Write-back is a Message, never an edit.** An agent proposes knowledge by
   enqueuing an `okf.message` (`schema: okf-mailbox/1`, ops `upsert | deprecate
   | verify | link | stale | delete`) with an `idempotency_key` and a `reason`.
   The actor applies it deterministically or dead-letters it.
4. **Agents cannot ratify.** A Concept whose `generated.by` is a `process:`
   actor lands at `status: draft`. It is not eligible for `enforced_by` until
   `verified` carries a `human:` actor — until `trust_tier` is `human-reviewed`.
   A `draft` Concept carrying `enforced_by` is a conformance FAIL.
5. **The core never calls a model** to decide any of this. Scoping, dedup and
   the trust gate are deterministic functions of the bundle.

## Consequences

**Good.**

- Rules become auditable data: what applied, to whom, and who ratified it. Wired
  to the journal, this is enforcement over a *path*, which is the thing the
  governance papers say frameworks cannot do.
- The self-ratification loop is closed by construction rather than by good
  behaviour (pre-mortem row 5).
- Rules survive a model swap. They are not tuned to one provider's instruction
  following, which is what makes "model-agnostic" mean more than "we can change
  the base URL".
- The knowledge half already exists, tested, stdlib-only. This ADR spends
  design, not new code.

**Bad, and accepted.**

- Ratification is a human bottleneck. An agent that discovers a real rule cannot
  act on it until a person verifies it. That is the intended cost, and it will
  feel wrong on the day it blocks something obviously correct.
- Writing a Concept is slower than adding a sentence to a prompt. Nothing in the
  design prevents someone from smuggling instructions into the brief instead —
  see the honest limits below.
- The bundle is a second store to keep conformant. `okf_bundle`'s parser refuses
  rather than guesses, so a malformed concept is a hard stop, not a warning.
  That is deliberate and it will bite.

**Honest limit.** The prompt-bytes-per-tick ceiling in the selftest catches
*growth* in free text. It cannot tell prose from a serialised Concept, so it does
not catch a smuggled instruction that stays under the cap. Spec criterion 33
says so in place. Pre-mortem row 6 is a live risk, not a closed one.

## Alternatives considered

**Rules in the system prompt (every framework in the quadrant).** Cheapest, and
the agent needs no new machinery. Rejected because nothing records which rule
applied, nothing stops the agent from ignoring or rewriting one, and there is no
trust tier — a rule an agent invented reads identically to a rule a human
ratified. That last point is the whole reason for the draft gate.

**A policy engine — OPA/Rego, or a deontic policy language from the cited
research.** Genuinely stronger at expressing constraints, and a real option.
Rejected for now on two grounds: it adds a non-stdlib dependency and a second
language to the one thing everyone in the loop must be able to read, and the
rules would then live somewhere other than the knowledge bundle — splitting
"what is true" from "what is enforced", which is the split this repo spent its
knowledge layer closing. Revisit if constraints over sequences outgrow what
`enforced_by` plus the journal can express.

**JSON Schema or plain YAML rule files.** Simpler than OKF. Rejected because
they carry no provenance, no trust tier and no ratification state — precisely
the three fields the draft gate is built on. We would rebuild OKF badly.

**Let agents edit the bundle directly, with review at PR time.** Rejected: it
loses the idempotency key, the ledger and the dead-letter, and it puts a model
inside the determinism boundary that `knowledge_actor.py` exists to draw.
