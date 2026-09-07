# 0007 — Findings become concepts; the queue stays a table

Status: **accepted** (owner ruling, 2026-09-07) — **Option C**.

## Context

ADR 0006 decided *when* a note is validated: when an agent acts on it. It
deliberately did not decide *how* a note carries the things that make validation
possible — an anchor, a verification stamp, a supersession trail.

This repository already vendors all of that and does not use it for notes.
`knowledge_actor.py` ships `upsert | deprecate | verify | link | stale | delete`.
An OKF concept's frontmatter already has `verified: {by, at}`, `generated:
{by, at}`, `stale_after`, `superseded_by`, `sources` and `status`, with trust
tiers `unverified → machine-confirmed → human-reviewed`. `apply_verify` refuses a
self-authenticated human claim: a file cannot vouch for its own author.
`palace.py`'s `invalidate()` never deletes — it appends `valid_until` and
`supersedes: <id>` so history survives temporal questions.

Meanwhile `state/*.md` and `state/triage.md` are plain markdown with none of it.
Four notes rotted on 2026-09-07 and nothing noticed until an unrelated gate
tripped.

Scale, measured rather than assumed: **3 dated finding notes, 37 triage rows.**
Small enough that any of the options below is affordable, which means this is a
question about the right shape, not about migration cost.

`FILES.md` already draws the distinction the options turn on: the triage table
is *loop state* — what is queued, in flight, done — while a dated note is a
*finding*. A queue is not knowledge.

## Options

### Option A — frontmatter only

`state/` stays markdown; notes gain OKF frontmatter (`verified`, `stale_after`,
`superseded_by`, `sources`). The reader parses it at act-time per ADR 0006.

- **Cost:** low. A frontmatter block and a reader. No new store, no change to
  the triage parser or its pins.
- **Buys:** notes become self-checkable, which is ADR 0006's precondition.
- **Loses:** no `link` graph, so no navigation from a note to what superseded
  it. No actor-authenticated `verify` — the frontmatter is whatever the writer
  typed, so a machine can claim human verification, which `apply_verify`
  explicitly refuses. Two systems that look alike and are not.

### Option B — route everything through `knowledge_actor`

Findings *and* triage rows become OKF concepts, written through the mailbox.

- **Cost:** high. Every writer changes. `state/triage.md` stops being a table,
  so `triage_state.py`, `loop_next_pick.py`, the goldens and several pins all
  move with it. That parser is load-bearing and has been the subject of five
  review rounds this week.
- **Buys:** one memory system. Verification that cannot be forged, a link graph,
  supersession trails, dedup, trust tiers.
- **Loses:** the queue's simplicity. A markdown table a human can read and edit
  is a real asset for a loop whose whole premise is that state is inspectable.

### Option C — findings as concepts, the queue stays a table

Dated findings (`state/<date>-<topic>.md`) become OKF concepts. `state/triage.md`
remains exactly as it is.

- **Cost:** medium. Three notes migrate. The write path for findings changes;
  the queue is untouched, so no parser, golden or pin moves.
- **Buys:** lifecycle exactly where things rot — findings are what cite code and
  what agents act on later. Verification is actor-authenticated. `link` gives
  the navigation for "this moved, where did it go".
- **Loses:** two stores rather than one, and a boundary someone must understand.
  The boundary is already in `FILES.md`, but it becomes load-bearing.

## The ruling, and the reasoning behind it

**Option C, ruled by the owner 2026-09-07.** The rot is in findings, not in the queue: a triage row says
`status=fixing`, which cannot go stale the way a finding naming a code location can. Option B pays
its largest cost — rebuilding a parser five review rounds deep — to fix a class
of problem the queue does not have. Option A leaves verification forgeable,
which makes the trust tiers decorative.

I hold this loosely on one point: Option C's boundary must be stated so plainly
that no agent has to think about it, or facts will land in the wrong store — and
`FILES.md` opens by warning that a fact filed in the wrong layer is a fact lost.

## Consequences

- A finding is written through the mailbox, not by appending markdown.
- `FILES.md` gains a row and the boundary becomes explicit.
- The `link` verb carries "superseded by" trails, so a moved fact is navigable
  rather than merely wrong.
- ADR 0006's validation reads the concept's own `verified` / `stale_after`,
  which is checkable rather than typed prose.
- Spec follows this ADR; implementation follows the spec.
