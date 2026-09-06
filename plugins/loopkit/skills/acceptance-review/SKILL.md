---
name: acceptance-review
description: Grade a change against its EARS acceptance criteria by ACTING — reproduce the trigger, observe the response, attach the evidence. PASS / FAIL / BLOCKED, never a rounded-up pass. Run by the Stop hook and by the reviewer.
---

# Skill: Acceptance Review

## Job

How the reviewer grades code against EARS criteria by acting.

## For Each EARS Criterion

1. Read the criterion: "WHEN X, the system SHALL Y."
2. Act: reproduce X in the code/test/sandbox.
3. Verify: does Y happen?
4. If no, reject and cite the criterion that failed.
5. If yes, record it as passed **with the evidence attached** — the command you
   ran and the line of output that proves it. A bare "passed" is the failure
   loops keep repeating: a gated suite, a quoted glob and a pipe through `tail`
   have each produced a green result while running nothing.
6. If you cannot verify a criterion, report it as **unverifiable** and name what
   is missing — the credential, the environment, the fixture. That is a useful
   result. Guessing to avoid an awkward gap is not.

## Rolling the criteria up into one verdict

Per-criterion outcomes do not add themselves up, and leaving the arithmetic
implicit is how a review with one unverified criterion gets reported as a pass.

- **PASS** only when every criterion passed, each with its evidence attached.
- **FAIL** when any criterion failed. One is enough; do not weigh it against the
  others.
- **BLOCKED** when nothing failed but at least one criterion is `unverifiable`.
  This is deliberately not a pass. The honest statement is "I could not
  establish this", and rounding that up to PASS is the whole failure class this
  skill exists to prevent.

State the verdict, then the counts — `PASS 7/7`, `BLOCKED 6 passed, 1
unverifiable (needs sandbox credentials)` — so a reader can see the shape
without re-reading every line.

## Common Patterns

### Authentication

- EARS: "WHEN a user submits valid credentials, the system SHALL return a session token."
- Action: submit valid credentials (in test or sandbox), verify token in response.

### Error Handling

- EARS: "IF an API call fails, the system SHALL retry up to 3 times."
- Action: inject a network error, watch the retry loop, count retries.

### Performance

- EARS: "The system SHALL process 10k transactions in <5s."
- Action: load 10k transactions, measure time, assert <5s.
- **State the environment beside the number, always.** A timing with no stated
  environment is not a finding. Name the machine or CI runner, and for anything
  touching a database name the schema and prove it with an exact `count(*)` —
  never a planner estimate, which passes on the wrong schema. A number measured
  against the wrong table once nearly bought an index nobody needed. A number
  from an unnamed environment is worse than no number, because it gets acted on.

### Third-party integration

- EARS: "WHEN the loop refreshes vendor data, the system SHALL call the vendor's info endpoint."
- Action: trigger the refresh in the vendor's sandbox, verify the call in its logs.

## Comparing two candidates

When the verdict is A-versus-B rather than held/not-held, read
`references/judge.md`: justification before score, pairwise run twice with
positions swapped (TIE on disagreement), the judge never the generator.

## Gotchas

- BLOCKED is not PASS. One `unverifiable` criterion with nothing failed is
  BLOCKED, and rounding it up is the whole failure class this skill exists
  for.
- A number without its environment is not a finding. Name the machine, the
  schema, and prove counts with `count(*)`, never an estimate.
- Form the per-criterion verdict from the diff and the criteria BEFORE reading
  the implementer's summary; the order is the review.
- The Stop hook runs this skill on THIS TURN's changes only. Orphaned work from
  other sessions is an inbox note, never a reason to block.

## Never

- Approve something that "should work"; prove it works.
- Read the code and guess. The one exception is a criterion you genuinely cannot
  execute — and the answer there is step 6, `unverifiable` with the missing
  piece named, never a quiet pass. A blanket ban with no escape hatch just gets
  broken silently under pressure.
