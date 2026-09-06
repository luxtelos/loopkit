# Failure table — symptom to layer

Read this at step A2, every time. The table is the reason the skill exists: the
middle column is the reflex that feels right and produces the wrong artifact.

Each row below is drawn from a failure a real loop actually had.

## The table

| Symptom you can observe                                               | The reflex to resist                                           | Layer          | The fix that holds                                                                                                                                                                                                       |
| --------------------------------------------------------------------- | -------------------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| CI is green but the behaviour is still wrong                          | Add more tests                                                 | `measurement`  | Confirm the suite executed at all. Gated suites, quoted globs, and pipelines through `tail` all report success while running nothing. See `measurement.md`.                                                              |
| A number, date or money value is wrong                                | "Handle the timezone carefully", "double-check the arithmetic" | `tool`         | Move the derivation into a deterministic function and assert the value. Arithmetic inside an instruction is never reliable — it gets closer, never correct.                                                              |
| Behaviour is right on dev and wrong elsewhere                         | "It works on my env"                                           | `measurement`  | Environment identity gap. Resolve the schema from the running service's own boot config, then prove it with an exact `count(*)`. Never a planner estimate — one measured on the wrong table nearly bought an index.       |
| A test asserts something must NOT happen, and passes                  | Leave it, it is green                                          | `spec`         | Negative pins encode world-state with no expiry. A passing negative pin may only prove the old world is still enforced. Re-justify with a dated `era-ok(YYYY-MM-DD)` or delete it.                                       |
| A guard is written as a blocklist of statuses                         | Add the missing status                                         | `code`         | Allowlist the states that may proceed. A blocklist misses every state added after it was written; five instances of the same bug surfaced in one day this way.                                                           |
| A constraint is violated in a fraction of runs                        | Longer prompt, more guard clauses                              | `architecture` | One pass cannot self-verify. Generate → Evaluate → Repair, where the evaluator returns violations, not a rewrite and not a score.                                                                                        |
| PR merged, CI green, nothing changed downstream                       | Merge it again                                                 | `process`      | Stacked-base no-op. GitHub only retargets a child PR when the base branch is deleted. Make "delete branch on merge" the default; prove landing with `git merge-base --is-ancestor`.                                       |
| Migration ordering is wrong                                           | `repair` from wherever you are                                 | `process`      | Never repair from a branch with a partial migration set. Pin the schema explicitly, snapshot and rollback written first.                                                                                                 |
| The owner is asked the same question a second time                    | Ask again, more clearly                                        | `process`      | The first ruling never became an artifact. Land it as a rule, hook, script, runbook or memory in the same session.                                                                                                       |
| The owner is asked something they already ruled on                    | Escalate to be safe                                            | `process`      | Check `inbox/`, the thread and `state/` first. Re-asking a settled question teaches the owner that escalations can be ignored.                                                                                            |
| A gate "passed" but you did not read its verdict line                 | Trust the exit code                                            | `measurement`  | Read the verdict. A guard that exits 0 with "skipping" when it cannot resolve its input looks identical to a pass.                                                                                                       |
| Coverage drops after a refactor that added tests                      | Write more tests                                               | `process`      | Coverage tools count executed lines. A module split makes moved lines "new code" needing direct coverage. Extract into pure modules so tests actually execute them.                                                       |
| An instruction file keeps growing and nobody can say what a line does | Append the new rule                                            | `prompt`       | Audit pass, Mode B. Delete every instruction whose triggering scenario is gone; they compete for the same compliance budget.                                                                                             |
| Two reasonable options and the loop picked one quietly                | Proceed with the sensible default                              | `decision`     | Route to `inbox/needs-human.md` with the cost of both sides and the whole context. Quietly choosing is how a scope cut becomes a silent regression.                                                                       |
| A success response, but the effect never landed                       | Trust the 200 / the success link                               | `measurement`  | An API accepting a payload is not proof it rendered or persisted. Read the result back. A provider returning `true` while the local row stays stale is this exact shape.                                                  |
| Behaviour varies run to run on hard inputs                            | Fiddle with temperature                                        | `architecture` | Let reasoning scale with difficulty, or add the GER loop. Variance on hard inputs is a shape problem, not a wording problem.                                                                                             |
| Wrong because the model was never told a domain fact                  | Tell it to try harder                                          | `prompt`       | Context gap. Supply the fact. This is the one row where adding words is the correct fix.                                                                                                                                |
| A review or audit returns about the same number of findings every run | Work the list                                                  | `measurement`  | The instrument is reporting its quota. Feed it something you know is clean; if it still finds things, its counts carry no information. Fix: no quota, zero legal, every finding must quote text checked against the file. |
| A "green in isolation, red together" suite                            | Bisect the tests                                               | `measurement`  | It is usually the environment, not pollution: one variable present under the runner and absent bare. Diff the env of the two runs before touching a test.                                                               |

## When two rows fit

Take the earlier layer. `measurement` before everything: if you are not certain
what is true, a code fix is a guess dressed as work. `decision` before `code`: if
the right behaviour has not been ruled, code encodes a guess into the product.

## The four things a strong instruction set carries

When nothing in the table fits cleanly, check whether one of these is simply
missing. Most "this prompt needs tightening" cases are actually a missing intent
or specification, and no amount of rewording supplies them.

- **Craft** — structure, separation of policy from data, clear ordering.
- **Context** — the domain facts the model cannot infer.
- **Intent** — the outcome the output must achieve, not merely the task.
  "A PR body a first-year student can follow" beats "write a PR body".
- **Specification** — acceptance stated as checkable conditions.

Intent and specification are the two most often missing, and they are exactly
what EARS criteria in `specs/` are for. A finding whose real defect is a missing
specification should produce a spec amendment, not new code.
