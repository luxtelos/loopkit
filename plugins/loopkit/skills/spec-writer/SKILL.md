---
name: spec-writer
description: Turn a classified finding into a validated EARS spec in specs/ — outcome, scope, testable acceptance lines, edge-case families, and an inbox route for anything that needs a human number. Never overwrites an existing spec.
---

# Skill: Spec Writer

## Job

Turn a finding (issue, PR, incident) into a validated EARS spec.
Prevents intent drift by making acceptance testable from day one.

## Algorithm

1. Read the finding: title, description, any hints about acceptance — and the
   `loop-assess` assessment in `state/` if one exists; its control case must
   appear in the criteria.
2. Ask: What is the user outcome? (one sentence)
3. Ask: What is in scope? What is out of scope?
4. Ask: What are the constraints (perf, security, stack)?
5. Draft EARS acceptance criteria (each line is testable in <5 min).
   - `WHEN <trigger>, the system SHALL <response>`
   - `IF <condition>, THEN the system SHALL <action>`
   - `WHILE <state>, the system SHALL <action>`
6. Validate: can each EARS line be verified without human judgment? Do this as a
   **separate pass with the draft in front of you**, not while writing — read
   each line back and name the command that would check it. If you cannot name
   one, the line is prose wearing an EARS costume.

   The same agent drafting and judging is a weak check, so treat this as a first
   filter rather than the verification. The reviewer re-checks with
   `acceptance-review`, and that separation is the real guarantee.

7. Output: path to `specs/<slug>.md`, where the contract is:
   - **Slug** — lowercase, hyphenated, derived from the outcome not the ticket:
     `firm-wide-client-deletion`, not `fix-1453`. Ticket numbers move between
     issues; outcomes do not, and a reader should know what a spec is for
     without opening it.
   - **Never overwrite an existing spec.** If the path exists, that is a finding:
     either this work belongs in the existing spec as an amendment, or the slug
     is wrong. Silently replacing a spec destroys the acceptance criteria some
     merged PR was reviewed against.
   - **Amend by appending a dated section**, not by editing earlier lines. The
     spec is the source of truth; its history is how you tell what was agreed
     when.
   - `specs/` is guarded by `protect_governance.py`. A ratified write sets
     `GOVERNANCE_EDIT_OK=1` for that invocation so the override is visible.

## EARS Format (Examples)

- "WHEN a user submits valid credentials, the system SHALL return a session token."
- "IF credentials are invalid, THEN the system SHALL return 401."
- "WHILE a session is expired, the system SHALL reject protected requests with 401."

Each line = one test case.

## Do NOT

- Write implementation details in the spec.
- Make acceptance criteria vague ("should be fast" → "SHALL complete in <5s").
  If a hint cannot be turned into a number, an enumeration or a boolean, that is
  the finding — say so and route it to `inbox/`. Inventing a threshold to make
  the line look testable is worse than an open question, because it becomes a
  number nobody agreed to and everyone later builds against.

  **Routing to `inbox/` means appending a section to `inbox/needs-human.md`** —
  one file, newest at the top, never a new file per finding. The section needs
  four things, and a missing one makes it unanswerable rather than merely terse:
  1. A dated heading: `## <what is undecided> (YYYY-MM-DD)`.
  2. The spec slug it blocks, so the reader can see what is stalled.
  3. The question, stated so it can be answered yes/no or by picking an option,
     with the whole context in the section — the reader must not need a lookup.
  4. **The cost of each option**, not a single recommendation. A question with
     one proposed answer reads as a rubber stamp and the owner cannot weigh it.

  The owner picks it up; nothing polls it, so a finding parked here is stopped
  until a human reads it. That is the intended trade — a stalled spec is
  cheaper than an invented threshold.

- Leave out edge cases. Before finishing, walk three families explicitly and
  write one line per applicable case: boundary (empty, one, maximum), error
  (dependency down, malformed input, permission denied), and concurrency (two
  callers at once, retry after partial success). Naming a family as
  "not applicable here" counts as covering it — silence does not.

- Skip the model when the design has two writers, a retry, or a guard followed
  by a write. That shape is what `loopkit:run-state-model` exists for; the trace
  it produces is evidence you hand this skill, and the criteria cite it.
