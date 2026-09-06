# The knowledge bundle — OKF v0.2 through a mailbox actor

`knowledge/` is a directory of markdown concepts with typed YAML frontmatter,
an Open Knowledge Format v0.2 bundle. It is the layer that turns a project's
history — rulings, traps, invariants, gates — into something a tick reads
before it acts, and something a check can prove is enforced.

## Shape

```
knowledge/
  index.md              generated — never messaged
  log.md                generated — every applied change, newest first
  loop/                 the loop's own doctrine (seeded by `memory.py knowledge init`)
  rulings/              what humans decided (rulings-extract.py --apply, then ratify)
  <domain>/…            whatever the project adds
state/knowledge-mailbox/
  inbox/ processing/ processed/ dead-letter/ pending-delete/
  keys.txt ledger.md source-index.json
```

A concept:

```
---
type: Invariant                     # Doctrine | Invariant | Decision | Trap | Topology | Contract | Gate | Playbook | Post-mortem
title: The loop never merges and never approves
description: >
  Merge and approve are human acts; every agent is refused them at the hook layer.
tags: [domain/loop, origin/constitution, lane/billing]
status: draft                       # draft | stable | deprecated  (stable = a human ratified it)
sources:
  - id: constitution
    resource: constitution.md       # a FILE in the repo, never a directory, never a queue
enforced_by:                        # Invariant and Gate only — what makes it real
  - hook: block_dangerous.py
generated: {by: process:loopkit/seed, at: "2026-09-06T…"}
x_source_digests: […]               # captured at apply; scan-drift compares
---

## The claim
…
## Why
…
## Enforced by
…
```

## The one rule

**Never hand-edit the bundle.** Every change is a message: `memory.py
knowledge enqueue …` then `memory.py knowledge drain`. The actor is
deterministic — no model inside it — so replay converges and `log.md` is an
audit trail. `protect_governance.py` refuses hand edits once
`knowledge.enabled` is true; `KNOWLEDGE_EDIT_OK=1` is the visible door for a
human repair.

## Verbs (`memory.py knowledge …`)

| verb | what it does | exit |
| --- | --- | --- |
| `init` | enable, seed ten loop concepts, drain, reindex, add to `.prettierignore` | 0 |
| `status` | concepts by type, queue depth, dead letters | 0 |
| `search "<q>" [--type T] [--lane L]` | deterministic text match; a lane matches `lane/<L>` tags | 0 |
| `get /path.md` | the concept text | 0 |
| `enqueue --target /dir/x.md --reason … [--type --title --tags --sources a.md,b.md --enforced-by hook:x.py --body-file f]` | one message | 0 / 3 invalid |
| `drain` | apply the queue | 0 / 6 dead-lettered (escalated to `inbox/needs-human.md`) |
| `verify` | OKF conformance + house rules | 0 / 4 |
| `reindex [--check]` | regenerate `index.md`; `--check` exits 5 if bytes would change | 0 / 5 |
| `scan-drift` | which concepts cite sources whose bytes moved | 0 |

## Rulings, and proving they are enforced

- `rulings-extract.py` (dry run by default) finds RESOLVED inbox sections,
  ADR decisions and "owner ruled" lines and enqueues one Decision/Trap/
  Invariant each under `/rulings/`. A human drains, reads, and ratifies with
  `status: stable`.
- `rulings-compile.py` lists every Invariant and Gate concept and whether its
  `enforced_by` names an artefact that exists: a `#name`d line in
  `.loopkit/block-patterns.txt`, a `protected.txt` regex, a test file, a
  model-checker property, or a hook. `--strict` fails when any ruling is
  prose only.
- `block_dangerous.py` prints `ruling: <name>` when a named extra pattern
  fires, so a block is attributable to the ruling that asked for it.

## Traps into the tick

Concepts of type Trap tagged `lane/<lane>` are injected by `loop_doctrine.py`
into a tick scoped to that lane (`--scope <lane>`, `LOOPKIT_LANE`, or the lane
name in the prompt) — at most five lines, never into CLAUDE.md. Recording a
trap with the right lane tag is how the next tick does not repeat it.

## Gotchas

- Sources are files, cited repo-relative. A directory has no digest and
  drifts forever; a queue (`inbox/`, `state/triage.md`) drifts by design.
  Both are refused at author time.
- `reason` is one line, ≤200 chars, and lands verbatim in `log.md`.
- The actor's `by` must be `human:<id>`, `process:<id>` or `<producer>/<version>`.
- A drain that dead-letters exits 6 and appends to `inbox/needs-human.md`;
  the message is in `state/knowledge-mailbox/dead-letter/` with the reason.
- `verify` runs the vendored actor; its `--require-sources` (a `stable`
  concept must cite evidence) is on the actor CLI, not yet on `memory.py`.
