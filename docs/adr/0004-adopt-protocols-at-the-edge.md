# ADR-0004 — Adopt protocols at the edge, never in core

- Status: accepted
- Date: 2026-09-07
- Deciders: repo owner (plan approved 2026-09-07)
- Context: `docs/research/runtime-plan.md` §2 and pre-mortem row 7

## Context

Three protocols now cover the boundaries this runtime has:

- **MCP** — how an agent reaches a tool. The most widely implemented of the three.
- **A2A (1.0.1)** — how one agent describes and delegates to another. An *agent
  card* is the published description of what a supervisor accepts.
- **AG-UI** — how an agent streams events to a user interface.

All three sit under the Linux Foundation's Agentic AI Foundation. The plan's
reading is that these are settled and should be adopted, never re-invented — and
the repo's own non-goals say "no new protocol".

Adoption still has a wrong way to do it. Protocol types leak: an `AgentCard`
becomes the shape of a Run, an MCP `Tool` becomes the shape of a tool, an AG-UI
event becomes the shape of an Event. Then a minor version bumps and the change
lands in the middle of the runtime. Three young protocols moving independently
make that pre-mortem row 7: *"A2A / AG-UI versions churn under us."*

## Decision

**Every protocol is an adapter at the edge. The core speaks only its own spec.**

1. **Core is protocol-free.** Nothing under `loopkit_core/` imports a protocol
   library or names a protocol type. Its vocabulary is exactly the nouns in
   `specs/loopkit-runtime.md`: Run, Queue, Stage, Event, Policy, Message,
   Projection, Provider, Store.
2. **MCP for tools, at the Provider boundary.** A tool reaches core as the
   Provider contract's `tools` argument and comes back as `tool_calls` entries of
   `{name, arguments}`. An MCP adapter translates a server's tool list into that
   shape and back. Core never learns what MCP is.
3. **A2A for the supervisor, as a generated artifact.** `agent-card.json` is
   *emitted from* the spec, not authored beside it, so the card cannot drift from
   what the supervisor accepts. It is validated against the A2A schema in CI.
   The card is output; it is never an input to a Run.
4. **AG-UI for interfaces, behind a flag.** An emitter subscribes to Events and
   translates them into AG-UI events. Events are defined by this spec's Event
   noun and the `ticks.py` line shape; the emitter maps, and the mapping is the
   only place either vocabulary appears.
5. **Every adapter pins an exact protocol version**, and the version appears in
   the adapter's module name or a module-level constant, so an upgrade is a
   visible diff rather than a transitive resolution.
6. **All three land in M4**, after core exists. None is on the M2 critical path.

## Consequences

**Good.**

- A protocol version bump touches one adapter. Core, the fixtures and both SDKs
  are unaffected — which is the whole property being bought.
- The conformance fixtures stay protocol-free, so an SDK in another language can
  pass them without implementing MCP, A2A or AG-UI at all.
- We interoperate without owning a standard, satisfying the non-goal directly.
- A generated agent card cannot describe a supervisor we do not have.

**Bad, and accepted.**

- Translation costs code and a hop, and the adapter is a place bugs hide. An
  adapter that is subtly lossy looks exactly like a core bug from the outside.
- We forgo protocol features with no core equivalent — A2A's richer task
  lifecycle, AG-UI's UI-specific event kinds — until core grows its own reason to
  have them. Someone will read that as LoopKit "not really supporting" the
  protocol, and in the strict sense they will be right.
- Three adapters is three things to keep current, and an unexercised adapter rots
  silently.

**Neutral.**

- Nothing here constrains the Provider implementations; an Anthropic or
  OpenAI-compatible client is a Provider, not a protocol adapter, and is covered
  by the spec's Provider criteria.

## Alternatives considered

**Make A2A the native model — Run *is* a Task, the card *is* the config.** Real
upside: zero translation, and instant interoperability with A2A tooling.
Rejected because it makes a 1.0.1 protocol the type system of the whole runtime.
Every future protocol revision becomes a core migration, and the JS SDK inherits
the same coupling — precisely pre-mortem row 7. It would also make A2A a
dependency of the conformance fixtures, so a language with no A2A library could
not implement the spec.

**Make MCP the tool abstraction inside core.** MCP is the most stable of the
three and this is defensible. Rejected because it forces an MCP client into
core, breaking the stdlib-only constraint, and it makes a stub provider in a
conformance run need a protocol implementation to call a fake tool.

**Emit AG-UI events directly from the Runner instead of from an emitter.** One
less indirection. Rejected because the journal would then be written in a UI
protocol's vocabulary — and the journal is the durability substrate, which must
outlive whatever UI protocol is current.

**Support none of the three; publish only our own JSON.** Cheapest, and honest.
Rejected because the plan's research is unambiguous that these boundaries are
standardised, and refusing them buys isolation with no compensating benefit.

**Adopt all three in M2, alongside core.** Rejected on sequencing: adapters
written against a core that does not exist yet get designed around guesses, and
the guesses become the core's shape. Core first, adapters after — which is also
why every item here is M4.
