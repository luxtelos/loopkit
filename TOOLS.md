# TOOLS.md — the tool diet

> One job, one tool. When two tools can answer the same question, this file
> says which one is the source of truth, so two runs do not answer differently.

## Job → tool

| Job                                        | Tool                                                                 |
| ------------------------------------------ | -------------------------------------------------------------------- |
| Which pipeline stage is next               | `loop-next.sh` (a lookup over `state/triage.md`; never the model)    |
| What is open / green / mergeable           | `loop-scan.py` — `READY TO MERGE` is the only line to act on unread  |
| Did a watched PR move                      | `loop-watch.sh --pr N` (cheap) before any full read                  |
| Zero regressions                           | `test-regressions.sh` against `state/known-test-failures.txt`        |
| Is a `file:line` citation still true       | `check-citations.py`                                                 |
| Two writers, a retry, a guard-then-write   | `run-state-model` driver (FizzBee first); trace cited in the spec    |
| Triage rows                                | `triage_state.py` — never hand-edit the table                        |
| Findings in prose → rows                   | `inbox_to_triage.py` (dry run by default)                            |
| GitHub reads and PR comments               | `gh` CLI; never `gh pr merge`, never `--approve`                     |
| Secrets a command genuinely needs          | `LOOP_ENV_WRAPPER` (e.g. a secrets-manager `run --`), value-blind    |

## Reading vs writing

Reads are free. Writes are tiered in `docs/MUTATION_POLICY.md`. Before
prefixing any command with a secrets wrapper, ask what secret it actually
consumes — lint and mocked unit suites consume none, and a wrapper that is
always on is a wrapper nobody questions.

## Search order for code

1. A structural code-graph tool, if the project has one (callers, callees,
   chains).
2. `Grep` / `Glob` for literal strings and file names.
3. Reading the file.

Never guess at a library's idiom when its documentation is one tool call away.

## Project tools

<!-- List the MCP servers and CLIs this project sanctions, one row each:
     what it is for, which environment it points at, and which config is the
     ROOT one (branch configs belonging to other workstreams are a trap: their
     credentials still work, so the wrong one connects you to the wrong data
     with no error). -->

| Tool | For | Environment / config |
| ---- | --- | -------------------- |
|      |     |                      |
