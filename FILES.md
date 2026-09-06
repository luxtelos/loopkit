# FILES.md — layout + memory routing

> The repo IS the memory. This file says which memory lives where, so
> retrieval knows where to look. A fact filed in the wrong layer is a fact
> lost.

## Layout

| Path                            | What it is                                                                                          | Committed?                 |
| ------------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------- |
| `specs/`                        | Source of truth (EARS criteria). Written via the spec-writer skill; guarded by `protect_governance` | yes                        |
| `docs/adr/`                     | Architecture decision records, numbered. One load-bearing decision each; an epic ships its ADRs before code | yes                        |
| `spec/fixtures/`                | Cross-SDK conformance fixtures: `(queue, policy, journal)` in, `(stage, next, events, messages)` out        | yes                        |
| `state/`                        | The loop's working memory: `triage.md`, `known-test-failures.txt`, dated run records and audits     | yes, every run             |
| `inbox/needs-human.md`          | Escalation door — work that needs a human waits here. Edits ping the configured webhook             | yes                        |
| `constitution.md`               | Non-negotiables, one EARS line each. Guarded                                                        | yes                        |
| `FILES.md` `TOOLS.md` `COMMANDS.md` | The three contracts. A gate blocks work tools until they are read                               | yes                        |
| `.loopkit/`                     | Per-project plugin config: `scopes.json`, `block-patterns.txt`, `citations.json`, `config.env`      | yes, except `config.env`   |
| `docs/MUTATION_POLICY.md`       | Which mutations the loop may make on its own, tiered                                                | yes                        |
| `.claude/`                      | Project-level agents, skills, hooks and settings, if any — the plugin supplies the loop's           | yes                        |

## Memory routing — where does a fact go?

| Kind of fact                                | Home                                                  |
| ------------------------------------------- | ----------------------------------------------------- |
| Loop state: what's triaged, in flight, done | `state/triage.md`                                     |
| Run findings, audits, assessments           | `state/<date>-<topic>.md`                             |
| Anything needing a human decision           | `inbox/needs-human.md` (whole context, both options)  |
| Requirements and acceptance criteria        | `specs/<feature>.md`                                  |
| Why a design decision went that way         | `docs/adr/NNNN-<slug>.md`                             |
| Behaviour every SDK must reproduce          | `spec/fixtures/*.json` (the contract, not a test)     |
| Known-failing tests                         | `state/known-test-failures.txt`                       |
| User prefs, feedback, cross-session gotchas | Claude auto-memory                                    |
| Code structure (callers, callees, chains)   | derived — query the code, never author it             |

Rule of thumb: if the loop needs it next run → `state/`. If a human needs to
decide → `inbox/`. If it's about how the user works → auto-memory. If it's
derivable from code → nowhere, query the code.

## Verify, never guess

Before answering about any past decision, ruling or event: look it up in
`state/`, `inbox/` and the ticket thread first. A recalled fact carries the date
it was true; a file carries the date it was written. Prefer the file.

## Project additions

<!-- Add the memory systems this project uses beyond the plugin's: a vector
     store, a knowledge graph, a wiki. One row each: what it holds, who writes
     it, whether it is derived (never authored) or authored. -->
