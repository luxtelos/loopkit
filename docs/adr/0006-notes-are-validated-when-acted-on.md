# 0006 — A note is validated when an agent acts on it, not on a schedule

Status: accepted (owner ruling, 2026-09-07)

## Context

`FILES.md` says where a fact goes. It says nothing about when a fact is
re-checked, when it expires, or who notices that it went stale. A note is
written once and lives forever, and nothing ever asks whether it is still true.

On 2026-09-07 four notes rotted in a single day. Each cited live code by line
number; `main` moved under them; the line numbers became wrong. One of them cost
a confidently-argued and completely incorrect diagnosis of a CI failure — the
reasoning was coherent, specific, and built on a note that was no longer true.

The machinery to prevent this already exists in this repository and is unused
for notes. `knowledge_actor.py` ships `upsert | deprecate | verify | link |
stale | delete`; concepts carry `verified: {by, at}`, `superseded_by` and trust
tiers; `palace.py`'s `invalidate()` never deletes, appending `valid_until` and
`supersedes: <id>` so history survives. None of this reaches `state/*.md` or
`state/triage.md`, which are plain markdown.

Four triggers were considered for re-validation: on the subject PR merging,
whenever `main` moves, on a time expiry, or lazily when an agent acts.

## Decision

**A note is validated at the moment an agent acts on it. Never on a schedule,
never on a sweep.**

1. Reading a note **to act on it** validates it first. Validation resolves the
   note's anchor: a commit SHA where the target is version-controlled,
   `verified: {by, at}` where it is not.
2. A note that fails validation is marked **`stale`** — the verb already
   exists — and the agent **escalates instead of acting**. It is never silently
   deleted, and never silently acted upon.
3. Reading a note **to display it** (a status summary, a scan) does not
   validate, and therefore MUST mark it as unvalidated. A count is not a claim.
4. Nothing runs in the background. There is no expiry timer, no re-validation
   sweep, no hook on `main` moving.

## Consequences

- **Cost is paid only where being wrong is expensive.** A note nobody acts on
  can be wrong indefinitely and harms nobody. This is the whole argument for
  lazy validation, and it is why the three eager alternatives were rejected.
- **The read path carries the check, not the write path.** Writing stays cheap,
  so nothing discourages recording a finding.
- **A note must be self-checkable.** It has to carry what it claims plus an
  anchor. A note asserting "the bug is at line 633" with no anchor cannot be
  validated and must not be written — see `FILES.md`, whose routing table
  already says code structure is *derived: query the code, never author it*.
  Naming the symbol survives movement; naming the coordinate does not.
- **Display and action diverge, deliberately.** A scan may show a stale note; it
  may not present it as true. Conflating the two is how a wrong note gets
  quoted into a decision.
- **Accepted risk:** a note can sit wrong for a long time before anyone touches
  it. Accepted, because the alternative — sweeps and timers — spends real work
  re-checking notes nobody wants, and this loop has already proved it will
  happily burn a day on bookkeeping.

## What this does NOT decide

Routing `state/` findings through the knowledge layer so they inherit
`verify`/`stale`/`supersedes`/`link` is a separate and larger piece of work. This
ADR fixes the trigger. The plumbing needs its own spec.

## Alternatives rejected

- **Re-validate when the subject PR merges.** Misses every note whose target is
  changed by an unrelated PR — which is exactly what happened four times.
- **Re-validate whenever `main` moves.** Correct but expensive, and it re-checks
  the whole corpus to protect the handful of notes anyone will read.
- **Time expiry into "unverified".** Time is not the variable. A note about
  untouched code is as true after a year; a note about a hot file is wrong in a
  day.
