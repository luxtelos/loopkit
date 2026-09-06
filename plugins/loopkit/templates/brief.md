# Subagent brief — <one-line objective>

Every brief carries four things, or the subagent will spend its context
guessing at them (anthropic.com/engineering/multi-agent-research-system: an
objective, an output format, guidance on tools and sources, and clear task
boundaries). Fill every section; delete nothing.

## Objective

One sentence. What must be TRUE when this agent is done — not what it should
do. ("Every `it(` in tests/billing/ that references `period_end` is listed with
its file and line." not "look at the billing tests".)

## Output schema

The exact shape of the return, and its size cap. Subagents return summaries,
never transcripts — the supervisor's context is the scarce resource.

```
RESULT: <one line verdict>
FINDINGS (≤ 10):
- <file>:<line> — <fact, ≤ 20 words>
UNVERIFIED: <what could not be checked, and why>
```

Cap: 200 tokens. Anything longer goes to a file under `state/` and the return
carries the path.

## Tools and sources

Which tools to use, which to avoid, and which sources are authoritative.
("Read + Grep only. Do not run tests. `specs/billing.md` is the acceptance
contract; the PR description is not.")

## Boundaries

What is out of scope, what must not be changed, and when to stop. ("Do not
edit any file. Stop after 15 tool calls and return what you have with
UNVERIFIED filled in.")
