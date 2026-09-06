# Instruction audit — Mode B

Read this when the thing under review is an instruction file: `CLAUDE.md`,
`constitution.md`, the three contracts, a skill, or an agent file. These are
prompts. They decay like prompts, and unlike code nothing in the loop measures
them.

## Why this is not cosmetic

Every standing instruction competes for the same finite compliance budget —
roughly 150 to 200 standing instructions before adherence degrades — which
means an instruction added today can be paid for by an unrelated rule silently
being dropped tomorrow. You will not see that failure at the point of the edit.
You will see it weeks later as "the agent ignored the rule about X", and the
reflex will be to add another rule about X.

So the default move in an audit is **deletion**, and additions carry a burden of
proof.

## Step 1 — Measure the load

```bash
cat CLAUDE.md constitution.md | grep -cE '^\s*[-*] |SHALL'
```

Report the number against the ceiling. Treat it as a floor: skills and agent
files load on top of it, as does the session's own system prompt. If the count
is climbing, that is the finding — no rewording of individual lines addresses a
budget problem.

## Step 2 — Name one failure

Not "it feels bloated". A behaviour: an instruction that was not followed, two
instructions that conflict, or a rule whose triggering scenario no longer exists.
An audit with no named failure has no way to know when it is finished.

## Step 3 — Classify the failure before rewriting

| Symptom                                                           | Reflex to resist                      | The fix                                                                                    |
| ----------------------------------------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------ |
| A rule is broken under pressure, or followed so rigidly it annoys | Harder wording, capitals, "NEVER"     | Trade-off gap. State the cost of both sides. Judgement calls need trade-offs, not walls.   |
| Two sections bleed together; policy is confused with data         | Bold text, "IMPORTANT"                | Structure gap. Separate role, policy, guidelines, examples and untrusted input.            |
| The agent is oddly timid or withholds useful work                 | Add reassurance                       | Legacy patch written for a weaker model. Delete it. These turn actively harmful over time. |
| A later rule contradicts an earlier one                           | Repeat the rule in both places        | Ordering gap. Reorder along the dependency chain; give the rule one home.                  |
| The same rule appears in three files, worded differently          | Sync all three                        | Pick one home. Three copies drift, and the drift is invisible until they disagree.         |
| Nobody can say what a line is for                                 | Leave it, it is probably load-bearing | Find its triggering scenario. If it no longer exists, delete it.                           |
| A rule is routinely ignored                                       | Say it louder                         | It is probably a cap doing damage. Find the failure it was written for; keep only the part that cures it. |

## Step 4 — One change per version

Version the file: v0 is what exists, v1 fixes exactly one named failure. This is
not ceremony. If you batch five edits and behaviour improves, you have learned
nothing you can carry to the next file, and if it degrades you cannot tell which
edit did it.

Keep a short changelog next to the work: version, failure targeted, edit made,
what you observed afterwards.

## Step 5 — Name the control case

An instruction file governs every future run, so the risk is not that your fix
fails — it is that it quietly breaks an unrelated behaviour that was working. Name
one behaviour that is currently correct and must stay correct, and check it after
the edit. Regressions on the control case are the ordinary outcome of instruction
edits, which is exactly why this step is not optional.

## Anti-patterns worth deleting on sight

- Politeness padding and motivational framing that carries no constraint.
- Instructions describing capability the model already has.
- A blanket negative constraint sitting on a judgement call.
- Any rule whose original triggering model version is no longer in use.
- Restatements of a rule that already has a home elsewhere.
- Everything crammed into one file when the content has independent audiences —
  durable rules belong in `CLAUDE.md`, procedures in a skill, and mechanism in a
  hook or script. Respect that split rather than growing the root file.

## What good looks like when you finish

- The load count, before and after.
- The one failure you targeted, and what you observed after the change.
- The control case, and confirmation it still holds.
- Anything you found that another layer owns, listed explicitly so the next run
  does not re-add what you removed.
