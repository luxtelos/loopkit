---
name: loop-assess
description: Classify a finding before anyone writes a spec — establish the baseline, name the layer (measurement, code, tool, spec, process, architecture, prompt, decision), route it. Mode B audits an instruction file. Use on any `new` triage row, alert, complaint, or "tighten this CLAUDE.md".
---

# loop-assess

The LoopKit pipeline runs `morning-triage → planner → spec-writer → implementer →
reviewer → stop gate → PR`. Look closely at the planner's moves: step 2 extracts
the problem, step 3 outlines the fix. Nothing in between asks _what kind of
problem this is_. The pipeline has exactly one output shape — a spec, then code,
then a PR — so every finding that enters it comes out as code, whether or not
code was the answer.

That is the gap this skill fills. It runs **before** the planner, and it does two
jobs:

- **Mode A — assess a finding.** Establish what is actually true today, classify
  the failure, and route it to the right layer. Only findings classified as
  code-layer continue to `spec-writer`.
- **Mode B — audit an instruction file.** CLAUDE.md, a skill, an agent file are
  prompts. They decay the same way prompts decay, and nothing in the loop
  otherwise measures them.

Pick the mode from what arrived. A GitHub issue, an alert, an owner complaint or
a triage row is Mode A. A request to tighten, clean up or review an instruction
file is Mode B.

**Before either mode: confirm nobody has already ruled.** Check
`inbox/needs-human.md`, the ticket thread and recent `state/` records for a
decision on this exact question. Escalating something the owner settled last
week is the most expensive kind of noise, because it teaches the owner that
escalations can be ignored.

---

## Mode A — assess a finding

### A1. Establish the baseline before you touch anything

The single most repeated failure in a loop is not a wrong fix. It is a
**measurement that could not have failed**, treated as evidence. Real instances,
all of which passed a gate:

- Four integration suites had never once executed — an env gate was never set,
  so the suite reported success by running nothing.
- A quoted test glob was silently read as a filename filter, so most of the
  suite never ran and CI stayed green.
- A gate script piped through `tail` returned `tail`'s exit code, so a FAIL rode
  into a PR labelled PASS.
- A chat post returned a success link while silently dropping the table that
  carried the entire message.
- An audit tool asked its judge for "3-6 findings" and got exactly 6 on eight of
  nine instruction files. Handed a twelve-line file with nothing wrong with it,
  it still produced 4. Every count it had ever reported was its own output rate
  times the number of files.

That last one is the one to remember, because it was the audit tool. Nothing
protects the instrument you are measuring with from the failure you are using it
to find. The control case is what catches it: feed the thing something you know
is clean, and see whether it can say so.

So before classifying, answer three questions in writing, in `state/`:

1. **What is observably true right now?** One sentence of behaviour, not theory.
   "Checkout returns 500 for any account with no subscription row" is usable.
   "Subscriptions are broken" is not.
2. **What did I run to know that, and could it have failed?** Name the command
   and the signal. If the answer is "CI was green", that is not yet evidence —
   confirm the relevant suite actually executed.
3. **What is the control case?** The ordinary path you are most likely to break
   and least likely to test. If you cannot name it, you cannot tell a fix from a
   regression later.

If you cannot establish a baseline, that is itself the finding. Say so and stop;
do not proceed to a spec. `references/measurement.md` has the recipes — which
gates lie and how to make them honest.

### A2. Classify before proposing a fix

Match the observed behaviour to `references/failure-table.md`. It maps common
symptoms to a layer, and names the reflex to resist for each one. The layers:

| Layer          | Meaning                                         | Where the fix lands                                   |
| -------------- | ----------------------------------------------- | ----------------------------------------------------- |
| `measurement`  | We do not actually know what is true            | A gate or an assertion, before anything else          |
| `code`         | Behaviour is wrong and the logic owns it        | `specs/` → implementer, the normal path               |
| `tool`         | A derived value the prompt should never compute | A deterministic function or script                    |
| `spec`         | Acceptance was wrong, absent, or expired        | Amend `specs/`; re-date era-pins                      |
| `process`      | Git, CI, migration or release mechanics         | A hook, a script, or a runbook                        |
| `architecture` | One pass cannot satisfy the constraint          | Generate → Evaluate → Repair, or a new loop stage     |
| `prompt`       | An instruction file caused it                   | Mode B                                                |
| `decision`     | Nobody has ruled; the loop is guessing          | `inbox/needs-human.md`, with the cost of both options |

Only `code` continues to `spec-writer`. Everything else is misfiled if it
becomes a spec, and misfiled work is how a repo grows guard clauses that nobody
can later explain.

### A3. Write the assessment down

Findings that stay in the chat window are lost — the state file is the memory.
Write to `state/<date>-<slug>-assessment.md`:

```markdown
## Observed

<one sentence of behaviour>

## Evidence

<command run, signal read, and why it could have failed>

## Control case

<the path a fix must not break>

## Classification

<layer> — <the reflex resisted, and why this layer instead>

## Route

<spec-writer | script | hook | inbox | GER loop | instruction audit>

## Not a code problem

<anything another layer owns, with the recommendation>
```

The last section is required even when it is empty. It is what stops the next
run from re-adding the guard clause this one removed.

### A4. Hand off

- `code` → `loopkit:spec-writer`, carrying the control case into the EARS
  criteria so the regression alarm is part of acceptance, not an afterthought.
- `decision` → `inbox/needs-human.md`. State the cost of **both** options, and
  give the whole context in the escalation itself — the facts, the figures, the
  files — so the owner can decide from the escalation alone without a lookup. A
  bare question with one recommended answer reads as a rubber stamp.
- Everything else → the artifact named in Route. An owner ruling that does not
  become a rule, hook, script or runbook in the same session will be re-asked;
  that is the "signs, not chats" rule and it exists because it kept happening.

---

## Mode B — audit an instruction file

CLAUDE.md, `constitution.md`, skills and agent files are prompts. They are never
measured, they only ever grow, and CLAUDE.md itself carries a compliance ceiling
— frontier models reliably follow on the order of 150–200 standing instructions
before compliance degrades — that nothing checks.

Check it — but count the right thing. Only `CLAUDE.md` files load automatically.
`constitution.md` and the contracts are pointer-only: an agent reads them if it
goes looking. Counting all of them together overstates the always-on load.

In a git worktree, **two** `CLAUDE.md` files can load: the parent checkout's and
the worktree's own. If they have drifted the agent is holding two versions of
the same rule. Measure both, then diff them:

```bash
grep -cE '^\s*[-*] ' CLAUDE.md "<parent checkout>/CLAUDE.md"
diff CLAUDE.md "<parent checkout>/CLAUDE.md"
```

Report the auto-loaded count against the ceiling, note how much of it is the
same directive counted twice, and treat any diff hunk as a finding in its own
right — a rule that says two things is worse than a rule nobody reads. If the
count is climbing, deletion is the work, not better wording.

Then run the audit in `references/instruction-audit.md`. Its discipline in short:

- **One change per version.** A batched rewrite gives you a result you cannot
  attribute. Version the file v0, v1, v2, each targeting one named failure.
- **Delete before you add.** Every instruction whose triggering scenario no
  longer exists is now noise competing for the same compliance budget.
- **Trade-offs beat walls.** For judgement calls, a bare "NEVER do X" gets
  broken under pressure or obeyed too rigidly. State the cost of both sides.
- **Order along the dependency chain.** If a later rule depends on an earlier
  decision, it must come after it.

An instruction file change is a change to how every future run behaves, so it
carries the same evidence bar as code: name the failure it fixes, and name the
control case — a behaviour that was correct before and must stay correct.

---

## What this skill will not do

It does not write the fix, the spec or the code. Its output is a classification
and a route, and it is finished when those are on disk. Handing a well-classified
finding to the right agent is the whole job; doing that agent's work as well
collapses the separation of powers the constitution exists to protect.

It also never approves its own downstream work. Whatever it routes, a different
agent implements and a third judges.

## Reference files

- `references/failure-table.md` — symptom → layer, with real cases. Read it
  during A2, every time; the table is the point.
- `references/measurement.md` — the gates that lie and how to get an honest
  signal. Read it during A1 whenever the evidence is a passing check.
- `references/instruction-audit.md` — the Mode B loop in full, with the
  anti-patterns worth deleting on sight.
