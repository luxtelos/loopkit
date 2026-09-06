# Roadmap

Where LoopKit is going, why, and what would prove it wrong. The full reasoning,
the competitor scan and the pre-mortem live in
[docs/research/runtime-plan.md](docs/research/runtime-plan.md); this page is the
short form. Milestones ship one pull request at a time, each with its own gate.

## The position

Three things are commoditised in 2026: durable execution (Temporal, DBOS,
Restate, Inngest all sell journal-and-replay), model-agnosticism (every agent
SDK swaps providers), and the protocol layer (MCP for tools, A2A between
agents, AG-UI to a front end, all under one foundation). Adopt those; do not
rebuild them.

What is not commoditised is a project's own decision record — the rulings,
traps and invariants a human produced over months — turned into checks that
block. LoopKit has that half: typed knowledge, gates, a ledger. It lacks the
runtime half. The roadmap builds the runtime half without giving up the first.

**The claim, stated so it can fail:** converting a project's history into
enforcement raises verified success per tick and lowers the rate at which
settled rulings get re-asked. Falsified if neither metric moves after ninety
days against the pre-ledger baseline, or if a provider ships typed,
drift-checked, hook-wired memory. If it fails, the finishing floor stays and
the claim is dropped rather than defended.

## Where we are on the loop ladder

The four rungs are turn-based (you hand off the check), goal-based (the stop
condition), time-based (the trigger) and proactive (the prompt). They nest:
a proactive routine is a schedule around a goal around a check.

| Rung | Status |
| --- | --- |
| Turn-based, the check | **Built.** The stop gate runs the project's own tests, lint and build and diffs against a failure baseline; a reviewer agent with different instructions judges; a hook refuses a diff that deletes tests |
| Goal-based, the stop condition | **Structural.** The Stop event carries the deterministic gate plus an evaluator agent; acceptance is the EARS lines in the spec. The condition is repo-fixed, not per run — M1 closes that |
| Time-based, the trigger | **Deliberately absent.** A timer over a loop with no journal repeats work after a crash. M2 first |
| Proactive, the prompt | **Not yet.** Needs the rung above plus a run that survives its own machine |

Details and the artefact behind each claim: [docs/loop-ladder.md](docs/loop-ladder.md).

## Milestones

| # | What ships | Done when |
| --- | --- | --- |
| **M1 — the specification** | `specs/loopkit-runtime.md` in EARS, four ADRs, a domain model as an ERD, cross-language conformance fixtures, and a model-checked journal/replay state machine | The fixtures exist and the model checks; no runtime code |
| **M2 — `loopkit-core`** | The portable modules extracted, the stage precedence and the continue/wait/idle decision lifted out of shell, plus `Provider` (stub, OpenAI-compatible, Anthropic), `Store` (filesystem, SQLite, S3-compatible) and a journal-before-act `Runner` | Fixtures pass under the stub; a killed run resumes without re-calling the model; two runners cannot corrupt one journal |
| **M3 — the plugin becomes an adapter** | Hooks and scripts call core; nothing user-visible changes | The existing suite passes unchanged, and no hook re-implements a core function |
| **M4 — projections and protocols** | Typed knowledge to JSON to a named schema; an A2A agent card; AG-UI events behind a flag | A projection validates against its schema; the card validates against the A2A schema |
| **M5 — `loopkit-js`** | A second SDK generated from the same fixtures | Both SDKs pass the identical fixture set in CI |
| **M6 — the bench** | Prompt-driven against policy-driven on one task set | The two ledger metrics are reported with their sample sizes |

## Design rules that hold across every milestone

- **Stores:** filesystem, SQLite and S3-compatible. No Postgres — nothing in
  scope needs several runners sharing one journal, and a schema is cost with no
  buyer.
- **Providers:** anything that can complete messages with tools and a response
  schema. A provider never exercised in CI is listed as unverified, by a script
  rather than by hand.
- **One free-text instruction per run.** The supervisor's brief. Everything
  else reaches an agent as a typed concept, and prompt bytes per tick is
  asserted in the suite so "promptless" cannot quietly decay.
- **Agents propose, humans ratify.** Knowledge written by a process lands as a
  draft and cannot enforce anything until a human verifies it.
- **Protocols at the edge**, version-pinned, never in the core.

## Not on the roadmap

No hosted service. No user interface beyond an event stream. No vector store,
no retrieval-augmented generation of our own, no new protocol. No claim about
any model that has not run in continuous integration.
