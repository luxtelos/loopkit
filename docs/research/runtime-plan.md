# LoopKit Runtime — a spec-first, model-agnostic supervisor loop driven by OKF

> Supersedes the "LoopKit 0.2 — knowledge layer" plan on this file. That plan is
> executed up to batch d: PRs #2, #3, #4 (stacked) and #5 (dogfood + releases) are
> open on `luxtelos/loopkit`; `v0.1.0` and `v0.2.0-alpha.1` are released; the
> judge runner and the `updatedInput` offload are built on disk in two worktrees
> and wait only on commit/push/PR (step 0 below).

## Context

The owner's ask (2026-09-06): imagine LoopKit as a framework that is model-,
provider- and tool-agnostic, used not only for coding but as a runtime that
application code calls: a hub-and-spoke supervisor starts N agents from one
single-shot brief; each agent acts on rules held as OKF and writes its result
back as OKF; everything lives in a store (blob / S3-compatible / Postgres); a
crash resumes from the last journaled step, the way a desktop client resumes a
session; an API caller receives the result and projects it — OKF markdown →
schemaless JSON → a typed schema → a UI that renders only the views it needs.
Before any SDK (JS or otherwise) there is a specification. The requested order:
plan → specify → evaluate → pre-mortem → execute, with the plan shown first and
a competitor quadrant from research.

What already exists (verified by a read-only inventory of branch
`feat/0.3-profiles-fanout` today): about 80% of the loop's logic is already
portable Python with no Claude Code dependency — `triage_state.py`
(queue), `ticks.py` + `loop-metrics.py` (journal + metrics), `loop_next_pick.py`
(stage lookup), `inbox_to_triage.py`, `rulings-*.py`, the vendored
`okf_bundle.py` + `knowledge_actor.py` (typed knowledge with a mailbox, atomic
writes, exit codes 0/2/3/4/5/6), the memory adapters, and the pure decision
halves of every hook (`is_dangerous`, `test_count`, `looks_like_tick`,
`traps_for`…). Only the hook shells, `fanout.sh`, `morning-triage.sh` and
`stop_gate.sh` are bound to Claude Code. The only loop logic still living in
bash is the stage precedence and the `NEXT: CONTINUE|WAIT|IDLE` decision in
`loop-next.sh` — the first extraction target.

## Research — the quadrant that exists, and the one that does not

No analyst quadrant covers "agent runtime SDK". Gartner's 2026 Magic
Quadrants nearest to this space are *Enterprise AI Coding Agents* (GitHub,
OpenAI Codex, Cursor as Leaders) and *AI Application Development Platforms*
(Google/Gemini Enterprise); open-source frameworks are not rated. So the
quadrant below is ours, on two axes that the owner's vision actually turns on:

- **x — who owns the behaviour:** prompt-driven (instructions written per
  agent) → policy-driven (rules are typed data the runtime enforces and the
  agent writes back).
- **y — what survives a crash:** ephemeral (context window) → durable
  (journal-before-act, replay to resume, exactly-once side effects).

| Player (2026) | x: behaviour owner | y: durability | Model-agnostic | Note |
| --- | --- | --- | --- | --- |
| Temporal / DBOS / Restate / Inngest AgentKit | code (policy = your code) | **high** — journal every step, replay on crash, exactly-once | yes (they never call the model) | Durable execution is a commodity: four vendors, five languages, Postgres or a managed runtime |
| LangGraph | graph + prompts | high — checkpointer, `interrupt()`, pause/resume; pairs with the four above for long runs | yes, "one line to swap" | Purpose-built agent runtime; observability and memory primitives bundled |
| Google ADK 1.0 | prompts + graph workflows | high — persistent checkpoints, restore, rewind | adapters; Gemini-optimised | Native A2A agent cards; Python/Java/Go |
| Microsoft Agent Framework 1.0 (AutoGen + SK, GA 2026-04-03) | prompts | medium | yes | .NET + Python; enterprise orchestration |
| OpenAI Agents SDK | prompts + handoffs | low–medium — sessions as key-value history | 100+ LLMs | Lightest; handoff is the primitive |
| Claude Agent SDK | prompts + subagents (hub-and-spoke) | low–medium — compaction, budget caps | Bedrock / Vertex / Azure only | Deepest MCP; "give the agent a computer" |
| Mastra / CrewAI | prompts + roles | medium — suspend/resume, Postgres backend | yes | TypeScript-first (Mastra); A2A delegation (CrewAI) |
| DSPy / BAML | **programs, not prompts** (compiled) | low | yes | The "promptless" corner exists, but for single calls, not runtimes |
| Policy engines (research: "Policies on Paths", deontic policies) | **policy as data**, path-dependent | n/a | n/a | Names the gap: frameworks cannot enforce constraints over *sequences* of actions; prompting reduces, access control blocks unconditionally |
| Protocol layer: MCP, A2A 1.0.1, AG-UI (Linux Foundation AAIF, 190 members) | — | — | — | Tools, agent-to-agent, agent-to-UI are standardised; adopt, never re-invent |
| **LoopKit today** | policy as data (OKF, gates, ledger) | medium — append-only files, one machine, Claude Code bound | no | Top-right is empty. That is the position. |

Reading: durability, model-agnosticism and protocols are commoditised. What
nobody ships is a runtime whose rules are typed, drift-checked, human-ratified
knowledge that agents both obey and write back — with enforcement wired to a
ledger of what actually happened (path-dependent, exactly the gap the
governance papers name). LoopKit already has the knowledge half; it lacks the
runtime half. The plan builds the runtime half without giving up the first.

## The five steps

### 1. Plan (this document) — what is built, in what order, and what is refused

Order of delivery, one PR per milestone, each with its own gate:

- **M0 — finish what is in flight** (today). Fix the leak grep in
  `tests/selftest.sh` (`--exclude-dir=.git` skips only directories; a worktree's
  `.git` is a *file* holding the volume path → add `--exclude=.git`). Commit,
  push and open the two implementer PRs (`feat/0.3-judge-runner`,
  `feat/0.3-offload-rewrite`, base `feat/0.3-profiles-fanout`), run the
  reviewer + stop gate on each, set the rows `pr-open`. Release
  `v0.2.0-alpha.2/3/4` → `v0.2.0` as the owner merges #2–#4.
- **M1 — the specification** (paper trail before code, the owner's epic rule).
- **M2 — `loopkit-core`** (Python, pure): extract, add Provider + Store + Runner.
- **M3 — the plugin becomes an adapter** over core; nothing user-visible changes.
- **M4 — projections and protocols:** OKF → JSON → schema → AG-UI; A2A card.
- **M5 — `loopkit-js`** from the spec's conformance fixtures.
- **M6 — the bench:** prompt-driven vs policy-driven on one task set.

### 2. Specify — before any SDK

Files (all in `luxtelos/loopkit`):

- `specs/loopkit-runtime.md` — EARS. The nouns: **Run** (one supervisor
  invocation: brief, budget, store, provider), **Queue** (today's
  `state/triage.md` rows; identity = `source`), **Stage** (pure lookup, the
  precedence lifted from `loop-next.sh` into `loop_next_pick.py` — first task),
  **Event** (today's `state/ticks.jsonl` line; the journal), **Policy** (an
  OKF bundle: Doctrine / Trap / Invariant / Gate concepts with `enforced_by`),
  **Message** (an OKF mailbox message: `upsert | deprecate | verify | link |
  stale | delete`, idempotency key, dead-letter → inbox), **Projection** (a
  named JSON Schema over concepts).
  Load-bearing criteria (verbatim intent):
  - WHEN a Run starts, the runtime SHALL read the policy bundle and pass each
    agent only the concepts whose `tags` put them in its lane, never the whole
    bundle (today's `traps_for`, generalised). The field is `tags` and the
    matching rule is `specs/loopkit-runtime.md` §Policy scoping; this line
    said `scopes` until 2026-09-07, a field no Concept has ever carried.
  - WHEN an agent proposes a side effect, the runtime SHALL journal the
    intent before executing it and journal the result after; WHEN a Run is
    resumed, the runtime SHALL replay the journal and SHALL NOT call the
    provider again for any step whose result is journaled.
  - WHEN an agent writes knowledge back, it SHALL do so only as a mailbox
    Message; a concept written by `process:*` SHALL land as `status: draft`
    and SHALL NOT be eligible for `enforced_by` until `verified.by` carries a
    `human:` actor (trust tier, already in `okf_bundle.py`).
  - The runtime SHALL accept any provider implementing
    `complete(messages, tools, response_schema) → {text, tool_calls, usage}`;
    the reference implementations are a **stub** (deterministic, for
    conformance), an **OpenAI-compatible** client and an **Anthropic** client.
  - The runtime SHALL accept any store implementing
    `put_if_absent / get / append / list` with a conditional write; reference
    implementations are **filesystem**, **SQLite** (one file, transactional
    appends, the metrics query surface; single-runner only — SQLite over a
    network mount is unsafe for concurrent writers) and **S3-compatible**
    (the shared-blob case; MinIO on the remote docker host in CI-on-docker).
    No Postgres: nothing in scope needs several runners on one journal.
  - The single-shot supervisor brief SHALL be the only free-text instruction a
    Run carries; every other instruction reaches an agent as a concept. Pin:
    prompt bytes per tick, asserted in the selftest.
  - WHEN a caller requests a projection, the runtime SHALL emit JSON that
    validates against that projection's schema and SHALL omit concepts not
    in the projection (the "limited graphs per division" requirement).
- `docs/adr/0001-spec-first-runtime.md`, `0002-policy-is-an-okf-bundle.md`,
  `0003-journal-before-act.md`, `0004-adopt-protocols-at-the-edge.md`
  (MCP for tools, A2A agent card for the supervisor, AG-UI events for UIs —
  adapters, never in core).
- `docs/DDD-ERD.md` — Run / Queue row / Event / Concept / Message /
  Projection with cardinalities (the owner's ERD-before-code rule).
- `spec/fixtures/*.json` — conformance cases: (queue, policy, journal) in →
  (stage, NEXT, events, messages) out. Both SDKs must pass the same fixtures;
  this is what makes "any language" a claim you can check.
- `specs/loopkit-runtime.model.fizz` — the journal/replay state machine
  (two writers, a crash between intent and result, a retry) checked with the
  existing `run-state-model` driver; the trace is cited in the criteria.

### 3. Evaluate — how we will know it worked

- **Conformance:** the fixtures above run against the stub provider in the
  selftest (macOS bash 3.2) and in CI (ubuntu). Same in/out or the PR fails.
- **Durability:** kill the runner between "intent journaled" and "result
  journaled", resume, assert provider-call count and the final journal are
  identical to the uninterrupted run.
- **Portability:** a real OpenAI-compatible endpoint (an open model served on
  the remote docker host) runs one fixture end to end; the stub is the control
  case that proves the harness can fail.
- **The thesis metrics** (already defined as K2): verified-success per tick and
  re-ask rate of settled rulings, prompt-driven vs policy-driven, same tasks.
  If neither moves, the runtime half is still worth having but the "policy as
  data" claim is dropped, not defended.

### 4. Pre-mortem — how this fails, and the pin that catches each one

| # | Failure a year from now | Pin, red first |
| --- | --- | --- |
| 1 | The spec is written and nothing consumes it | M3: the plugin's own hooks import `loopkit-core`; the selftest fails if a hook re-implements a core function |
| 2 | "Model-agnostic" was only ever run on Claude | CI matrix: stub + OpenAI-compatible endpoint on the docker host; a provider that is never exercised is listed as `unverified` in the README by a script, not by hand |
| 3 | Replay re-calls the model and gets a different answer | Journal stores the provider result; replay asserts zero provider calls for journaled steps |
| 4 | S3 has no atomic rename; two runners clobber the journal | Store contract requires a conditional write (ETag / `If-None-Match`); MinIO pin with two concurrent appenders. SQLite pin: a second runner opening the same file is refused (`BEGIN IMMEDIATE` + a runner lock row), never silently interleaved |
| 5 | Agents ratify their own rules through write-back | `process:*` writes are `draft`; `rulings-compile.py` counts only human-verified concepts toward coverage; pin: a draft with `enforced_by` is a FAIL |
| 6 | "Promptless" quietly becomes prompt-ful | Prompt-bytes-per-tick asserted in the selftest with a ceiling; concepts are the only channel |
| 7 | A2A / AG-UI versions churn under us | Edge adapters only, version-pinned; core speaks its own spec |
| 8 | Python and JS SDKs drift apart | One fixture set, both must pass it; a fixture added in one PR without the other SDK's run fails CI |
| 9 | Scope balloons into "an agent platform" | Non-goals below are a selftest grep on the README; one PR per milestone; always-on tokens ≤ +150 per batch |
| 10 | The durability half is rebuilt when Temporal/DBOS already do it | ADR-0003 names the alternative; the Store contract is small enough that a `TemporalStore` is a later adapter, not a rewrite |

### 5. Execute — milestones, files, gates

- **M2 `loopkit-core`** (`plugins/loopkit/loopkit_core/` first, published as a
  package later): move bucket-A modules unchanged (imports only), lift stage
  precedence + `NEXT` into `loop_next_pick.py` (`decide(counts) → Stage, Next`),
  add `provider.py` (`Provider` protocol, `StubProvider`, `OpenAICompatProvider`
  via `urllib`, `AnthropicProvider`), `store.py` (`FsStore`, `SqliteStore` on
  the stdlib `sqlite3` module, `S3Store` with conditional put), `runner.py` (journal-before-act supervisor over
  `Queue` + `Policy`), `projection.py` (`okf → dict`, schema filter using the
  existing `parse_frontmatter`). Gate: fixtures + kill/resume pin + MinIO pin on
  the docker host.
- **M3 adapter:** hooks call core functions; `loop-next.sh` becomes a
  three-line shell around `decide`; `fanout.sh` becomes `Runner` with the
  Claude provider. Gate: existing selftest unchanged and green; doctor reports
  no duplicated logic.
- **M4 projections/protocols:** `memory.py knowledge project --schema <name>`;
  `agent-card.json` for the supervisor (A2A); AG-UI event emitter behind a
  flag. Gate: schema validation pin; card validates against the A2A schema.
- **M5 `loopkit-js`:** a separate package in the same repo (`packages/js`),
  generated types from the fixtures, the same conformance run in CI.
- **M6 bench:** `bench/` with the task set, both modes, the ledger metrics.

Non-goals: no hosted service, no UI beyond an AG-UI event stream, no vector
store, no new protocol, no claim about any model we have not run in CI.

## Decisions for the owner (recommendation first)

1. **Core language first:** Python now (extraction, ~80% exists), JS in M5
   from fixtures — recommended. Alternative: JS first, which means a rewrite
   before a spec exists.
2. **Stores:** filesystem + SQLite (stdlib, one file, single runner) +
   S3-compatible (shared blobs; MinIO on the docker host). Owner ruling
   2026-09-07: no Postgres — nothing in scope needs several runners sharing
   one journal, and a schema in M2 is cost with no buyer.
3. **Providers in M2:** stub + OpenAI-compatible + Anthropic — recommended.
4. **Protocols:** A2A card and AG-UI events land in M4 as edge adapters, not
   in the spec's core — recommended.

## Verification (end to end)

`bash tests/selftest.sh` (macOS) and the ubuntu CI job stay ALL PASS at every
milestone; the fixtures run under the stub provider in both; the kill/resume
and MinIO pins run on the remote docker host via `ssh -i ~/.ssh/id_ed25519
info@192.168.1.12`; `claude plugin validate .`; `check-citations.py` green;
the leak grep green (no origin-project names anywhere in the repo).

## Sources

LangGraph vs Temporal (langchain.com/resources/langgraph-vs-temporal); Durable
AI agents 2026 — Temporal, Inngest, DBOS, Restate
(reactify-solutions.com/articles/durable-ai-agents-2026); Claude Agent SDK vs
OpenAI Agents SDK vs Google ADK (composio.dev/content/claude-agents-sdk-vs-openai-agents-sdk-vs-google-adk);
AI agent frameworks compared 2026 (agentmelt.com/blog/ai-agent-frameworks-compared-2026);
A2A / MCP / AG-UI stack and the Agentic AI Foundation (dev.to/pockit_tools/mcp-vs-a2a-…, medium.com/@visrow/…);
Runtime Governance for AI Agents: Policies on Paths (arxiv.org/html/2603.16586v1);
Deontic Policies for Runtime Governance (arxiv.org/pdf/2606.19464);
Gartner 2026 MQ Enterprise AI Coding Agents (github.com/resources/whitepapers/…, openai.com/index/gartner-2026-agentic-coding-leader, cursor.com/blog/cursor-leads-gartner-mq-2026);
Gartner MQ AI Application Development Platforms (gartner.com/en/documents/7188230).
